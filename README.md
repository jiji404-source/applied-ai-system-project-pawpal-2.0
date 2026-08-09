# PawPal+ 2.0 — AI Care Planner

**Base project:** This extends **PawPal+ (Module 2 Project)**, a Streamlit app that let
a pet owner track care tasks (walks, feeding, meds) for one or more pets. The original
version's `Scheduler` class sorted tasks chronologically, filtered by pet/status,
detected same-time conflicts, and auto-scheduled the next occurrence of a recurring
task once one was marked complete — all through a rule-based, no-AI backend.

## Title and Summary

PawPal+ 2.0 adds an AI Care Planner on top of that scheduler: given an owner's pets and
a free-text note, Claude drafts a full day's care schedule grounded in a knowledge base
of vet-care guidance, checks its own draft against a deterministic conflict detector,
critiques and revises the plan until it's clean (or gives up and says so), and shows the
owner exactly what it checked. It matters because a generic LLM asked to "make me a pet
schedule" will happily invent plausible-sounding advice with no way to tell whether it's
grounded in anything — this system makes that groundedness checkable: every timing
decision cites a real rule ID, every citation is verified against the knowledge base, and
every schedule conflict is caught by code, not by asking the model to notice it.

## Architecture Overview

See [`diagrams/architecture.mmd`](diagrams/architecture.mmd) for the full flowchart.
In short:

```
Owner input -> retrieve guidance (BM25) -> screen (refuse emergencies/dosage Qs)
   -> propose plan -> Scheduler.detect_conflicts() [Module 2 code, reused as a tool]
   -> critique -> revise (loop, up to 3 rounds) -> verify citations + re-check conflicts
   -> score confidence -> render plan + full audit trail
```

The one design choice this diagram is built around: **the critic doesn't grade its own
homework.** Before Claude reviews a proposed plan, the plan is converted back into real
`Task`/`Pet`/`Owner` objects and run through the *original, untouched* Module 2
`Scheduler.detect_conflicts()`. That deterministic result is handed to the critic as
verified fact, so the review loop combines a code-checked fact ("these two tasks collide
at 17:00") with a knowledge-grounded judgment ("and that matters because of
ROUTINE-002"). The same principle applies to citations: every rule ID Claude cites is
checked against the knowledge base after the fact, and invented ones are recorded and
penalized rather than trusted.

Human oversight sits at two points: the Streamlit UI always shows a "How this plan was
checked" audit trail (retrieved rules, citation validity, residual conflicts, every
critique round verbatim), and any plan scoring below 60% confidence is flagged with an
explicit warning to read it critically and confirm anything medication-related with a
vet.

## Setup Instructions

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# The AI Care Plan tab needs a Claude API key:
export ANTHROPIC_API_KEY=sk-ant-...

streamlit run app.py
```

Open the app, add a pet and some tasks in the sidebar/Add Task tab as usual, then go to
the **🤖 AI Care Plan** tab, optionally add a note, and click **Generate plan**.

Run the test suite (no API key required — it runs against a scripted stand-in client):

```bash
python3 -m pytest -q
```

## Sample Interactions

These are real captured runs against the live Claude API (`claude-opus-5`, effort
`medium`) — not mocked. Full request/response metadata (tokens, latency, stage) for
these runs is in [`logs/planner_trace.jsonl`](logs/planner_trace.jsonl).

### 1. Ordinary daily schedule with a medication

**Input:**
```
Mochi (dog, Golden Retriever, 4y) meds: allergy pill, once daily | allergies: chicken
  - Morning walk requested 07:00, daily
  - Breakfast requested 07:30, daily
  - Allergy pill requested 07:35, daily
  - Evening walk requested 18:00, daily
  - Dinner requested 18:30, daily
Notes: I leave for work at 08:30 and get home at 18:00.
```

**Output:**
```
Confidence: 62%  |  Review rounds: 2  |  Approved: True
Retrieved rules: FEED-001, MED-002, MED-001, ROUTINE-002, EX-001, MED-007, ROUTINE-006, MED-005

Mochi walks at 06:45, eats breakfast at 07:45, and gets her allergy pill at 08:00 — but
only once she has actually eaten, since it should not go on an empty stomach. The evening
walk moves to 17:15 with dinner at 19:00, keeping meals about 11 hours apart and putting
a real gap between exercise and food. Two open items for your vet: the exercise-to-meal
buffer, and whether 50-60 minutes of daily activity is enough for Mochi's breed and
energy level.

06:45  Mochi: Morning walk (about 25-30 minutes)  [EX-001]
       Moved 15 minutes earlier so the walk ends around 07:15, leaving a 30-minute
       settling buffer before breakfast while still finishing well before the 08:30
       work departure (EX-001).
07:45  Mochi: Breakfast (chicken-free food)  [FEED-001]
       Breakfast sits in the typical 07:00-09:00 window and now starts at least 30
       minutes after the walk ends, and about 11.25 hours before dinner (FEED-001).
08:00  Mochi: Give the once-daily allergy pill with or right after breakfast — only if
       Mochi has actually eaten  [MED-002, MED-007]
       Given within 30 minutes after food so it is never on an empty stomach; if Mochi
       refuses or barely touches breakfast, hold the pill until she has eaten at the
       next meal and, if that means the dose is more than 6 hours late, give it when
       remembered without doubling up and confirm with your vet (MED-002, MED-007).
17:15  Mochi: Evening walk (about 25-30 minutes), plus 5-10 minutes of active play if
       Mochi is a high-energy or working breed  [EX-001]
       Walking 17:15-17:45 puts a clear gap before the 19:00 dinner and completes the
       second of two daily activity sessions (EX-001).
19:00  Mochi: Dinner (chicken-free food)  [FEED-001]
       Dinner moved to the end of the 17:00-19:00 window so it is over an hour after
       exercise ends and roughly 11.25 hours after breakfast (FEED-001).

Conflicts resolved:
  - Morning walk ran up against breakfast: walk shifted to 06:45 and breakfast to
    07:45, giving a ~30 minute buffer (EX-001, FEED-001).
  - Evening walk ran up against dinner: walk shifted to 17:15 and dinner to 19:00
    (EX-001, FEED-001).
  - Allergy pill was silently dependent on breakfast happening: now carries an
    explicit "only after she has eaten" condition (MED-002, MED-007).

Unmet constraints:
  - Exercise-to-meal buffer: the guidance provided does not specify a required gap
    between activity and feeding, so the buffers used here are a precaution, not a
    cited rule — please confirm with your vet.
  - Breed/energy level unknown: daily activity totals 50-60 minutes, the minimum
    under EX-001. If Mochi is high-energy this may be insufficient.
  - Pill dependency on food intake cannot be scheduled away: if Mochi refuses
    breakfast the dose must wait for the next meal (MED-007).
```

### 2. Two pets with a same-time conflict and a late meal

**Input:**
```
Buddy (dog, Labrador, 2y) meds: none | allergies: none
  - Walk requested 17:00, daily
  - Big dinner requested 17:15, daily
Whiskers (cat, , 8y) meds: none | allergies: none
  - Play session requested 17:00, daily
  - Litter scoop requested 09:00, daily
Notes: Buddy just had a large breakfast at 16:30.
```

**Output (abridged — full run in `logs/planner_trace.jsonl`):**
```
Confidence: 67%  |  Review rounds: 2  |  Approved: True
Retrieved rules: FEED-001, FEED-008, MED-001, EX-005, FEED-004, ROUTINE-006, ROUTINE-002, ROUTINE-003

Buddy's walk now starts at 17:45 and his dinner at 19:30, giving a true 60-minute gap on
both sides of the walk after his late 16:30 breakfast. Whiskers keeps her 09:00 scoop and
17:00 play session, with two optional short sessions added for a senior cat. Buddy's
meals are unavoidably close together today because of the late breakfast — keep portions
exactly as your vet directed.

17:00  Whiskers: 15-minute low-impact play session  [ROUTINE-002, EX-005, ROUTINE-003]
17:45  Buddy: Walk, 30-40 minutes (moved from 17:00)  [FEED-004, ROUTINE-002]
       Pushed to 17:45 so the full 60-minute buffer is measured from when Buddy
       actually finishes his 16:30 breakfast, not from when it was served — this
       matters for a deep-chested Labrador.
19:30  Buddy: Dinner — serve at least 60 minutes after the walk ends  [FEED-004, FEED-001]

Conflicts resolved:
  - Two pets needing attention at once: Whiskers' 17:00 play session and Buddy's walk
    are now 45 minutes apart, so neither overlaps (ROUTINE-002).
  - Meal-to-exercise buffer: walk and dinner both shifted so a genuine 60-minute gap
    exists on both sides (FEED-004).

Unmet constraints:
  - Buddy's two meals cannot be 10-12 hours apart today given the 16:30 breakfast
    (FEED-001) — tomorrow, shifting breakfast back to 07:00-09:00 restores spacing.
  - Buddy's age/breed size were not confirmed; if he's a senior large breed, FEED-008
    suggests three smaller meals instead of two.
```

### 3. Medical emergency + dosage question — refused

**Input:**
```
Rex (dog, Beagle, 6y) meds: thyroid medication, once daily | allergies: none
  - Thyroid pill requested 08:00, daily
Notes: Rex has been vomiting repeatedly since this morning and seems lethargic.
Should I double his thyroid dose to help?
```

**Output:**
```
REFUSED: This request describes a possible medical emergency (repeated vomiting since
this morning plus lethargy) and also asks whether to double a prescribed dose. I can't
diagnose or advise on dosage changes. Please contact an emergency vet now about Rex's
vomiting and lethargy, and give only the dose your vet prescribed unless they tell you
otherwise. Once Rex has been seen and you have vet instructions, I'm happy to build the
daily schedule around them.
```

This is the safety screen (`SafetyVerdict`, rules SAFE-001–003) working as intended: it
runs *before* any planning work happens, so an emergency never reaches the scheduling
logic at all.

## Design Decisions

- **BM25 over an embedding model for retrieval.** The knowledge base is 35 short rules —
  small enough that a dependency-free, deterministic keyword search is both sufficient
  and reproducible in tests/grading, with no network call or model download needed to
  answer "which rules are relevant." The tradeoff is that retrieval is vocabulary-
  sensitive: a note phrased very differently from the knowledge base's wording can
  under-retrieve (see `model_card.md`).
- **The rule-based Module 2 scheduler became a tool inside the AI loop, not a thing the
  AI replaced.** `Scheduler.detect_conflicts()` runs unmodified on every proposed plan,
  and its output is handed to the critic as verified fact rather than trusting Claude to
  notice its own scheduling conflicts.
- **Citations are verified, not trusted.** Every rule ID Claude cites is checked against
  the real knowledge base after the fact. An invented ID is recorded and reduces the
  reported confidence score — retrieval that can't be audited is just a longer prompt.
- **Confidence is computed, not just self-reported.** The model states its own
  confidence, but the system subtracts for unresolved critical issues, invented
  citations, and conflicts that survive to the final plan. A model that is dishonestly
  confident about a still-broken plan gets caught.
- **A bounded revision loop (`max_revisions=3`), not "critique until perfect."** If the
  critic and reviser can't converge, the loop returns its best attempt and says so in
  the UI rather than looping indefinitely or silently shipping a still-flawed plan.
- **`Task` validates `time`/`frequency` at construction, not deep inside the scheduler.**
  A code review of the Module 2 base project flagged that a malformed time string (e.g.
  `"8am"` instead of `"08:00"`) would crash `sort_by_time()` with an unhelpful error, and
  that nothing validated `frequency`. That boundary matters more now than it did in
  Module 2: the AI planner reconstructs `Task` objects from *model-generated* plan data
  in `plan_to_conflicts()` before running the deterministic conflict check, so a
  malformed AI output is a real input, not a hypothetical one. `Task.__post_init__` now
  raises a clear `ValueError` immediately, and `plan_to_conflicts()` catches it, logs the
  bad task, and keeps checking the rest of the plan instead of crashing the whole run.
- **`detect_conflicts()` flagging same-time tasks across different pets is a documented
  decision, not an oversight.** A code comment on the method now says so explicitly:
  the detector stays a simple, deterministic fact-finder, and the AI critic is what
  applies judgment on top (two pets fed at once is usually fine; two simultaneous walks
  are not).

## Testing Summary

**46 automated tests pass** (`python3 -m pytest -q`, ~0.11s): 9 for the Module 2
scheduler (including 4 new ones for the `Task` validation above), the rest for BM25
retrieval/rule parsing and the full planner loop against a scripted `LLMClient` —
including a critical issue forcing a revision, a plan that never converges within the
round limit, a refusal short-circuiting before planning starts, an invented citation
reducing confidence, a residual conflict surviving to the final plan, and a malformed
AI-generated time being skipped rather than crashing the conflict check.

Beyond the mocked suite, the three real end-to-end runs above (captured against the live
API, traced in `logs/planner_trace.jsonl`) show the loop actually converging: Scenario 1
needed one revision round (5 issues, 2 critical → 0 critical) before approval; Scenario 2
the same; Scenario 3 never reached the planning stage at all because the safety screen
caught it first. Confidence scores of 62% and 67% — not close to 100% — are the system
being honest about compromises it had to make (an exercise buffer it invented rather than
found in the guidance, an owner's requested times it had to move).

What I'd test next with more time: adversarial phrasings of the safety screen (right now
it's only been checked against one clearly-worded emergency), and retrieval recall when
an owner's notes use very different vocabulary from the knowledge base.

## Reflection

The most valuable moment in this project wasn't writing the retrieval or the prompt
templates — it was deciding *what the AI was and wasn't allowed to be the final word on*.
Scheduling conflicts and rule citations are things a program can check exactly, so I made
sure those checks happened in code, with the model's judgment layered on top rather than
substituting for them. That's the core lesson: an agentic loop is more trustworthy when
you're honest about which parts of it are verifiable facts and which parts are judgment
calls, and you build the architecture to keep those separate instead of asking one model
call to be right about everything at once.

For the graded reflection on AI collaboration, biases, and limitations, see
[`model_card.md`](model_card.md).

---

![UML Final Diagram](diagrams/UML_final_diagram.png)
![PawPal+ App Screenshot](PawPal%20Screenshot.png)
