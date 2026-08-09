"""Tests for the agentic planning loop.

The loop is driven by a `ScriptedClient` that returns canned structured responses in
order. That means the whole retrieve -> plan -> critique -> revise cycle is exercised
with no API key, no network, and no flakiness — the parts under test here are the
control flow, the verification, and the scoring, all of which are ours.
"""

from datetime import date

import pytest

from pawpal_ai.llm import LLMError, LLMRefusal
from pawpal_ai.planner import (
    CarePlanner,
    build_query,
    describe_pets,
    plan_to_conflicts,
    score_confidence,
    verify_citations,
)
from pawpal_ai.retriever import KnowledgeBase
from pawpal_ai.schemas import CarePlan, Critique, CritiqueIssue, PlannedTask, SafetyVerdict
from pawpal_system import Pet, Task


# ----------------------------------------------------------------- test doubles


class ScriptedClient:
    """An LLMClient that replays a fixed list of responses.

    Raising on exhaustion is deliberate: it turns "the loop ran more stages than
    expected" into a clear failure instead of a hang or a confusing type error.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def structured(self, *, system, user, output_format, stage):
        self.calls.append(stage)
        if not self.responses:
            raise AssertionError(f"ScriptedClient ran out of responses at stage '{stage}'")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def make_plan(confidence=0.9, cited=("MED-002",), times=("08:00", "18:00")):
    return CarePlan(
        tasks=[
            PlannedTask(
                time=times[0],
                pet_name="Biscuit",
                description="Morning feeding",
                rationale="Morning meal per FEED-001.",
                cited_rules=list(cited),
            ),
            PlannedTask(
                time=times[1],
                pet_name="Biscuit",
                description="Evening walk",
                rationale="Evening activity.",
                cited_rules=["EX-003"],
            ),
        ],
        conflicts_resolved=[],
        unmet_constraints=[],
        confidence=confidence,
        summary="A simple day.",
    )


def approved_critique():
    return Critique(approved=True, issues=[], assessment="Looks good.")


def critical_critique():
    return Critique(
        approved=False,
        issues=[
            CritiqueIssue(
                severity="critical",
                task_reference="18:00 Biscuit evening walk",
                problem="Walk is too close to the evening meal.",
                suggested_fix="Move the walk to 19:30.",
                cited_rules=["FEED-004"],
            )
        ],
        assessment="One critical timing problem.",
    )


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase.load()


@pytest.fixture
def pets():
    biscuit = Pet(name="Biscuit", species="dog", breed="Golden Retriever", age=3)
    biscuit.add_task(Task("Morning feeding", "08:00", "daily", due_date=date.today()))
    biscuit.add_task(Task("Evening walk", "18:00", "daily", due_date=date.today()))
    return [biscuit]


# ------------------------------------------------------------- helper functions


def test_build_query_includes_species_breed_and_medications():
    pet = Pet(name="Mochi", species="cat", breed="Siamese", age=5, medications="allergy pill")
    pet.add_task(Task("Give allergy pill", "09:00", "daily"))
    query = build_query([pet], extra="I work mornings")

    for expected in ["Mochi", "cat", "Siamese", "allergy pill", "Give allergy pill", "work mornings"]:
        assert expected in query


def test_describe_pets_lists_every_requested_task(pets):
    described = describe_pets(pets)
    assert "Biscuit" in described and "Golden Retriever" in described
    assert "Morning feeding" in described and "Evening walk" in described


def test_describe_pets_handles_a_pet_with_no_tasks():
    assert "no tasks requested" in describe_pets([Pet(name="Ghost", species="cat")])


# --------------------------------------------- rule engine used as a critic tool


def test_plan_to_conflicts_flags_two_tasks_at_the_same_time():
    # ARRANGE: an AI-proposed plan that puts two tasks in one slot
    plan = make_plan(times=("08:00", "08:00"))

    # ACT: run it through the original Module 2 conflict detector
    conflicts = plan_to_conflicts(plan)

    # ASSERT: the deterministic engine catches what the model may have missed
    assert len(conflicts) == 1
    assert "08:00" in conflicts[0]


def test_plan_to_conflicts_returns_nothing_for_a_clean_plan():
    assert plan_to_conflicts(make_plan(times=("08:00", "18:00"))) == []


def test_plan_to_conflicts_skips_a_malformed_ai_time_without_crashing():
    # ARRANGE: the model returned a non-24-hour time string ("8am" instead of "08:00").
    # PlannedTask itself doesn't validate format (see schemas.py's design note on why),
    # so this is a realistic case Task.__post_init__ has to catch.
    plan = make_plan(times=("8am", "18:00"))

    # ACT: the malformed task should be dropped and logged, not raise
    conflicts = plan_to_conflicts(plan)

    # ASSERT: the one well-formed task remains, alone, so there is nothing to conflict with
    assert conflicts == []


# ------------------------------------------------------- citation verification


def test_verify_citations_accepts_real_rule_ids(kb):
    assert verify_citations(make_plan(cited=("MED-002",)), approved_critique(), kb) == []


def test_verify_citations_catches_invented_rule_ids(kb):
    invalid = verify_citations(make_plan(cited=("MED-999",)), None, kb)
    assert invalid == ["MED-999"]


def test_verify_citations_also_checks_the_critique(kb):
    critique = Critique(
        approved=False,
        issues=[
            CritiqueIssue(
                severity="warning",
                task_reference="x",
                problem="y",
                suggested_fix="z",
                cited_rules=["FAKE-42"],
            )
        ],
        assessment="",
    )
    assert "FAKE-42" in verify_citations(make_plan(), critique, kb)


def test_verify_citations_reports_each_bad_id_once(kb):
    plan = make_plan(cited=("MED-999", "med-999", "MED-999"))
    assert verify_citations(plan, None, kb) == ["MED-999"]


# ------------------------------------------------------------ confidence scoring


def test_confidence_starts_from_the_models_own_rating():
    assert score_confidence(make_plan(confidence=0.9), approved_critique(), [], []) == 0.9


def test_confidence_drops_sharply_for_an_unresolved_critical_issue():
    scored = score_confidence(make_plan(confidence=0.9), critical_critique(), [], [])
    assert scored == pytest.approx(0.60)


def test_confidence_drops_for_hallucinated_citations():
    scored = score_confidence(make_plan(confidence=0.9), approved_critique(), ["MED-999"], [])
    assert scored == pytest.approx(0.75)


def test_confidence_drops_for_conflicts_the_loop_never_resolved():
    scored = score_confidence(
        make_plan(confidence=0.9), approved_critique(), [], ["Conflict at 08:00: 'a', 'b'"]
    )
    assert scored == pytest.approx(0.85)


def test_confidence_penalises_a_plan_that_cited_nothing():
    plan = make_plan(confidence=0.9)
    for task in plan.tasks:
        task.cited_rules = []
    assert score_confidence(plan, approved_critique(), [], []) == pytest.approx(0.70)


def test_confidence_is_clamped_to_zero_and_one():
    disastrous = Critique(
        approved=False,
        issues=[critical_critique().issues[0] for _ in range(5)],
        assessment="",
    )
    assert score_confidence(make_plan(confidence=0.9), disastrous, [], []) == 0.0

    # A model that answers "85" meaning 85% is normalised rather than clamped to 1.0.
    assert make_plan(confidence=85.0).clamped_confidence == pytest.approx(0.85)
    assert make_plan(confidence=-3.0).clamped_confidence == 0.0


# --------------------------------------------------------------- the full loop


def test_loop_stops_after_one_round_when_the_critique_approves(kb, pets):
    client = ScriptedClient(
        [
            SafetyVerdict(decision="proceed", reason="Routine scheduling."),
            make_plan(),
            approved_critique(),
        ]
    )
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert result.ok
    assert result.approved
    assert result.rounds == 1
    assert client.calls == ["screen", "propose", "critique"]
    assert result.retrieved_rule_ids, "the plan should have been grounded in retrieved rules"


def test_loop_revises_when_the_critique_finds_a_critical_issue(kb, pets):
    client = ScriptedClient(
        [
            SafetyVerdict(decision="proceed", reason="Routine scheduling."),
            make_plan(times=("08:00", "18:00")),
            critical_critique(),              # round 1: needs work
            make_plan(times=("08:00", "19:30")),  # the revision
            approved_critique(),              # round 2: clean
        ]
    )
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert result.ok and result.approved
    assert result.rounds == 2
    assert client.calls == ["screen", "propose", "critique", "revise", "critique"]
    # The revised time is what survived into the final plan.
    assert result.plan.tasks[1].time == "19:30"


def test_loop_gives_up_at_the_revision_limit_and_says_so(kb, pets):
    # Critic never approves, so the loop must terminate on the cap rather than spin.
    client = ScriptedClient(
        [
            SafetyVerdict(decision="proceed", reason="Routine scheduling."),
            make_plan(),
            critical_critique(),
            make_plan(),
            critical_critique(),
        ]
    )
    result = CarePlanner(client, kb=kb, max_revisions=2).plan_day(pets)

    assert result.rounds == 2
    assert not result.approved
    # A plan that never converged must not present itself as trustworthy.
    assert result.confidence < 0.7


def test_safety_screen_refuses_before_any_planning_happens(kb, pets):
    client = ScriptedClient(
        [SafetyVerdict(decision="refuse", reason="This is an emergency — call a vet now.")]
    )
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert result.refused
    assert not result.ok
    assert "emergency" in result.refusal_reason.lower()
    # Crucially, no planning stages ran.
    assert client.calls == ["screen"]


def test_api_refusal_is_reported_not_raised(kb, pets):
    client = ScriptedClient([LLMRefusal("Declined on safety grounds.")])
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert result.refused and not result.ok


def test_api_error_degrades_gracefully(kb, pets):
    client = ScriptedClient([LLMError("Rate limited by the API.")])
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert not result.ok
    assert "rate limited" in result.error.lower()
    assert result.plan is None


def test_planning_with_no_pets_returns_an_error_without_calling_the_model(kb):
    client = ScriptedClient([])
    result = CarePlanner(client, kb=kb).plan_day([])

    assert not result.ok
    assert "no pets" in result.error.lower()
    assert client.calls == []


def test_hallucinated_citation_lowers_the_final_confidence(kb, pets):
    client = ScriptedClient(
        [
            SafetyVerdict(decision="proceed", reason="Routine."),
            make_plan(confidence=0.95, cited=("MED-999",)),
            approved_critique(),
        ]
    )
    result = CarePlanner(client, kb=kb).plan_day(pets)

    assert result.ok
    assert result.invalid_citations == ["MED-999"]
    # The model claimed 0.95; independent verification pulled it down.
    assert result.confidence < 0.95
