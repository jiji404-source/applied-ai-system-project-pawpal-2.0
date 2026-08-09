"""Configuration and logging setup for the PawPal+ AI layer."""

from __future__ import annotations

import logging
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
TRACE_LOG = LOG_DIR / "planner_trace.jsonl"

# Claude Opus 5 is the current flagship. Adaptive thinking is on by default on this
# model, and `effort` is the knob for how much reasoning to spend.
MODEL = os.getenv("PAWPAL_MODEL", "claude-opus-5")

# 'medium' is the balance point for this workload: planning a day of pet care is
# multi-constraint but small. 'high' produced no measurable quality gain in the eval
# runs and roughly doubled latency.
EFFORT = os.getenv("PAWPAL_EFFORT", "medium")

MAX_TOKENS = int(os.getenv("PAWPAL_MAX_TOKENS", "8000"))

# How many critique -> revise rounds before the planner gives up and returns the best
# plan it has, flagging that it could not fully converge.
MAX_REVISIONS = int(os.getenv("PAWPAL_MAX_REVISIONS", "3"))

# How many knowledge-base rules to retrieve per planning request.
RETRIEVAL_K = int(os.getenv("PAWPAL_RETRIEVAL_K", "8"))


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging once, for CLI and eval entry points."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def ensure_log_dir() -> Path:
    """Create the log directory if needed and return it."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR
