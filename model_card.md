# Model Card — PawPal+ 2.0 AI Care Planner

This card documents the AI layer added on top of the Module 2 PawPal+ scheduler
(`pawpal_ai/`): a retrieval-grounded, self-critiquing daily care planner built with
Claude Opus 5. It answers the graded reflection prompts on AI collaboration, bias, and
testing that the README's own reflection section does not cover.

## What the system does

`CarePlanner.plan_day()` takes an owner's pets and free-text notes and runs:

```
screen -> retrieve -> propose -> [critique -> revise] x up to 3 -> verify -> score
```

It is not a chatbot wrapper. The model's output is a structured `CarePlan` (Pydantic
schema), every timing decision it makes has to cite a real rule ID from the knowledge
base, and the plan it proposes is run through the original Module 2
`Scheduler.detect_conflicts()` before a second Claude call critiques it. See
`diagrams/architecture.mmd` for the full flow.

## How I collaborated with AI on this extension

I built this extension in conversation with Claude Code, in a similar phased style to
Module 2 — retrieval and the knowledge base first, then the LLM client and schemas,
then the critique/revise loop, then the Streamlit tab. Because this project is graded on
*my* understanding of the system, not just working code, I paired every major file with
a "why," not just a "what": for example the retriever's docstring explains why BM25 over
the standard library was chosen over an embedding model (deterministic, reproducible in
CI, no model download), and `config.py` documents why `effort="medium"` was picked over
`"high"` for this workload.

### A suggestion that helped

The most important design decision in this project was **not trusting the critic to spot
scheduling conflicts on its own**. My first instinct (and the AI's first draft) was to
just ask a second Claude call to "review this plan for conflicts." I pushed back because
that's still just one model's read of a schedule, checked by another model's read of the
same schedule — the loop could hallucinate a conflict, or miss a real one, and neither
mistake would be detectable. The AI's revised suggestion — reuse the *deterministic*
`Scheduler.detect_conflicts()` from Module 2 as a tool inside the loop, and hand its
output to the critic as verified fact — is what actually shipped. It means the loop
combines a code-verified fact ("these two tasks collide at 08:00") with a
knowledge-grounded judgment ("and that matters because ROUTINE-002 says so"), instead of
asking the model to be right about something a five-line function can just check.

### A suggestion I had to correct

The first version of `CarePlan.confidence` in `schemas.py` used Pydantic's `Field(ge=0.0,
le=1.0)` to bound the value — the obvious way to say "this must be between 0 and 1." That
suggestion was wrong for this setup: Claude's structured-output feature does not support
`minimum`/`maximum` constraints, so the bound gets silently stripped from the schema Claude
actually sees, while Pydantic still enforces it client-side. In testing, a response of
`"confidence": 85` (Claude reading "85%" as the value, not 0.85) or a value like `1.02`
would pass the model's own check but then raise a hard `ValidationError` in Python and
crash the whole planning run over a cosmetic slip. I had the AI remove the schema
constraint entirely and instead added `CarePlan.clamped_confidence`, which detects the
percentage case and clamps the range in code instead of failing on it. The lesson that
generalizes: a validation rule that's correct in the abstract can still be the wrong
place to enforce it, depending on where in the pipeline the value is actually produced.

## Known limitations

- **The live API path has now been exercised end-to-end, but only lightly.**
  `logs/planner_trace.jsonl` records three real runs against `claude-opus-5` — an
  ordinary schedule, a multi-pet conflict, and a refusal — with the full loop converging
  in each case (see the README's Sample Interactions). That's real evidence the
  screen/propose/critique/revise sequence, citation verification, conflict re-checking,
  and confidence scoring work against actual model output, not just the scripted
  `LLMClient` stand-in the 41-test suite uses (see `tests/test_planner.py`). It is still
  only three scenarios, hand-picked to exercise the interesting paths — it is not
  evidence the system is robust across the long tail of real inputs an owner might type.
- **The safety screen is a prompt, not a guarantee.** `SafetyVerdict` refuses on
  emergency symptoms and dosage questions, but it is one Claude call reasoning over a
  system prompt — it can still be worded around, and it has not been adversarially
  tested with edge-case phrasing.
- **No persistence.** Like the base PawPal+ app, all state lives in
  `st.session_state` and resets when the browser closes; a generated plan is not saved.
- **Citation verification only checks existence, not correctness.** `verify_citations()`
  confirms a cited rule ID is real, not that the model applied it correctly to the task
  it attached it to.

## Bias and coverage gaps

- **Breed coverage in the knowledge base is uneven.** `knowledge/exercise_needs.md` only
  names a handful of breeds explicitly (Golden Retrievers, Labradors and "similar
  sporting breeds," brachycephalic breeds). A pet outside those named categories still
  gets a plan, but it's grounded in the generic adult-dog rule (`EX-001`) rather than
  anything breed-specific — the system's advice quality is not evenly distributed across
  breeds, and an owner of an uncommon breed would not know that from the confidence score
  alone.
- **BM25 retrieval is vocabulary-sensitive.** Because scoring is keyword-based, not
  semantic, a query that describes a situation in different words than the knowledge base
  author used can under-retrieve relevant rules. The system's groundedness depends on
  overlap between how an owner phrases their notes and how `knowledge/*.md` was written.
- **The knowledge base itself is self-authored, generic guidance**, not sourced from a
  veterinary professional or a specific pet's records. It's written to be broadly
  reasonable, not to be a substitute for a vet's actual instructions for a specific
  animal — which is exactly why `SAFE-001`–`SAFE-003` exist and why the planner refuses
  diagnosis and dosage questions rather than answering them.

## Testing summary

46 automated tests pass (`python3 -m pytest -q`, ~0.11s), split across:

- `tests/test_pawpal.py` (9) — the original 5 Module 2 scheduler behaviors, plus 4 new
  ones added in response to a code review of that base project: `Task` now validates
  `time` and `frequency` at construction (`Task.__post_init__`) instead of letting a
  malformed value crash `sort_by_time()` deep inside the scheduler with an unhelpful
  error. This matters more here than it did in Module 2 — the AI planner reconstructs
  `Task` objects from model-generated plan data before checking it for conflicts, so a
  malformed value is a real possibility, not a hypothetical one.
- `tests/test_retriever.py` — BM25 ranking, rule-ID parsing, citation lookup.
- `tests/test_planner.py` — the full `screen -> propose -> critique -> revise -> verify
  -> score` loop against a scripted `LLMClient`, including: a critical issue forcing a
  revision round, a plan that never converges within `max_revisions`, a refusal short-
  circuiting before any planning work happens, an invented rule citation reducing
  confidence, a residual conflict surviving to the final plan, and a malformed
  AI-generated time being skipped (logged, not crashed) by the new validation.

On top of the mocked suite, three real runs against the live API
(`logs/planner_trace.jsonl`, reproduced in the README) confirmed the loop converges on
actual model output: two ordinary scheduling scenarios each needed one revision round
before the critic approved (5 issues including 2 critical -> 0 critical), and a
medical-emergency-plus-dosage scenario was refused by the safety screen before any
planning work ran, exactly as designed.

What this still does **not** cover, and what I'd test next with more time: a broader
sample of real Claude responses (three hand-picked scenarios is not statistical
coverage), and adversarial safety-screen prompts designed to slip past the
emergency/dosage refusal without tripping it.
