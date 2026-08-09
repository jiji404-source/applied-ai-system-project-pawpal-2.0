"""Claude API wrapper: structured calls, guardrails, logging, and error handling.

The planner talks to this module rather than to the SDK directly. That buys three
things the project needs:

1. **Testability.** `LLMClient` is a Protocol, so the test suite runs the full agentic
   loop against a scripted stand-in with no network and no API key.
2. **Observability.** Every call appends a JSON line to `logs/planner_trace.jsonl` —
   which stage ran, how long it took, tokens used, and whether it failed. That file is
   the evidence behind the testing summary in the README.
3. **Error containment.** API failures are converted into one exception type the
   planner knows how to degrade on, instead of leaking six different SDK exceptions
   into the UI.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from . import config

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Any failure that stopped us getting a usable structured response."""


class LLMRefusal(LLMError):
    """The model's safety system declined the request.

    Distinct from `LLMError` because it is a content outcome, not a fault — the right
    response is to show the user a safety message, not to retry.
    """


class LLMClient(Protocol):
    """The interface the planner depends on."""

    def structured(
        self,
        *,
        system: str,
        user: str,
        output_format: type[T],
        stage: str,
    ) -> T:
        """Return a validated instance of *output_format*."""
        ...


def _log_trace(record: dict, path: Path | None = None) -> None:
    """Append one JSON line to the trace log. Never raises.

    Logging is diagnostic; a broken log must not take down a working plan.
    """
    try:
        config.ensure_log_dir()
        target = path or config.TRACE_LOG
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:  # pragma: no cover - disk problems are environmental
        logger.warning("Could not write trace log: %s", exc)


class ClaudeClient:
    """Live Claude API client.

    Constructed lazily so importing this module never requires an API key — the
    Streamlit app and the test suite both import it unconditionally.
    """

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model or config.MODEL
        self.effort = effort or config.EFFORT
        self.max_tokens = max_tokens or config.MAX_TOKENS
        self._api_key = api_key
        self._client = None

    @property
    def client(self):
        """The underlying SDK client, created on first use."""
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise LLMError(
                    "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
                ) from exc

            try:
                # With no explicit key the SDK resolves ANTHROPIC_API_KEY or an
                # `ant auth login` profile, which is the normal local-dev path.
                self._client = (
                    anthropic.Anthropic(api_key=self._api_key)
                    if self._api_key
                    else anthropic.Anthropic()
                )
            except Exception as exc:
                raise LLMError(f"Could not create the Anthropic client: {exc}") from exc
        return self._client

    def structured(
        self,
        *,
        system: str,
        user: str,
        output_format: type[T],
        stage: str,
    ) -> T:
        """Make one structured call and return the validated result.

        Raises:
            LLMRefusal: the model declined on safety grounds.
            LLMError: any other failure (auth, rate limit, network, bad response).
        """
        import anthropic

        started = time.monotonic()
        record: dict = {
            "stage": stage,
            "model": self.model,
            "effort": self.effort,
            "schema": output_format.__name__,
        }

        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
                # Adaptive thinking lets Claude decide how much to reason per request;
                # `effort` bounds the overall spend. Both are current-model settings —
                # a fixed thinking budget is rejected on this model.
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
            )

            # A refusal arrives as a normal 200 with stop_reason set, so this has to be
            # checked before touching the parsed content.
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                record.update(ok=False, error="refusal", category=category)
                raise LLMRefusal(
                    "Claude declined this request on safety grounds"
                    + (f" (category: {category})." if category else ".")
                )

            parsed = response.parsed_output
            if parsed is None:
                record.update(ok=False, error="empty_parse", stop_reason=response.stop_reason)
                raise LLMError(
                    f"Model returned no structured output (stop_reason={response.stop_reason}). "
                    "If this says 'max_tokens', raise PAWPAL_MAX_TOKENS."
                )

            usage = response.usage
            record.update(
                ok=True,
                stop_reason=response.stop_reason,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
            )
            return parsed

        except LLMError:
            raise  # already shaped; re-raise unchanged
        except anthropic.AuthenticationError as exc:
            record.update(ok=False, error="auth")
            raise LLMError(
                "Authentication failed. Set ANTHROPIC_API_KEY or run 'ant auth login'."
            ) from exc
        except anthropic.RateLimitError as exc:
            record.update(ok=False, error="rate_limit")
            raise LLMError("Rate limited by the API. Wait a moment and try again.") from exc
        except anthropic.APIConnectionError as exc:
            record.update(ok=False, error="connection")
            raise LLMError("Could not reach the API. Check your network connection.") from exc
        except anthropic.APIStatusError as exc:
            record.update(ok=False, error=f"http_{exc.status_code}")
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except Exception as exc:  # validation errors, unexpected SDK faults
            record.update(ok=False, error=type(exc).__name__)
            raise LLMError(f"Unexpected failure during '{stage}': {exc}") from exc
        finally:
            record["elapsed_s"] = round(time.monotonic() - started, 3)
            _log_trace(record)
