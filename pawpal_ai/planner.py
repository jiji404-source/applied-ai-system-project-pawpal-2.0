"""The retrieval-grounded, self-critiquing care planner.

This is the heart of PawPal+ 2.0. One `plan_day()` call runs:

    screen -> retrieve -> propose -> [critique -> revise] x N -> verify -> score

Two design choices are worth calling out.

**The critic is not just the model marking its own homework.** Before the critic sees a
proposed plan, the plan is run through the original PawPal+ `Scheduler.detect_conflicts()`
from Module 2. That deterministic result is handed to the critic as ground truth. So the
loop combines a code-verified fact (these two tasks collide at 08:00) with a
knowledge-grounded judgement (and that matters because FEED-004 forbids it). The
rule-based engine became a tool inside the agentic loop rather than a thing the AI
replaced.

**Citations are verified, not trusted.** The model is asked to cite rule IDs; every ID
it returns is checked against the knowledge base. Invented IDs are recorded and reduce
the final confidence score. Retrieval that cannot be audited is just a longer prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from pawpal_system import Owner, Pet, Scheduler, Task

from . import config
from .llm import LLMClient, LLMError, LLMRefusal
from .retriever import KnowledgeBase, format_context
from .schemas import CarePlan, Critique, SafetyVerdict

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- prompts

_SHARED_ROLE = """You are the planning engine inside PawPal+, a pet-care scheduling
assistant. You organise care tasks an owner has already been told to do by their vet.

You are NOT a veterinarian. You never diagnose, never recommend or change a dosage, and
never suggest treatment. If timing looks wrong, you may note it and advise confirming
with a vet, but you do not change what the owner was prescribed."""

_SCREEN_SYSTEM = f"""{_SHARED_ROLE}

Your job right now is a single safety decision about an incoming request, using this
guidance:

{{guidance}}

Refuse when the request asks you to diagnose a condition, asks what dose to give or
whether to change one, or describes a medical emergency (bloating, collapse, repeated
vomiting, seizures, difficulty breathing, inability to urinate, suspected poisoning).
When you refuse an emergency, tell the owner to contact an emergency vet now.

Proceed for ordinary scheduling work, including plans that merely mention medications
the owner has already been prescribed."""

_PLAN_SYSTEM = f"""{_SHARED_ROLE}

Build a safe daily schedule using ONLY the guidance below. Every timing decision that a
rule speaks to must cite that rule's ID.

GUIDANCE:
{{guidance}}

Requirements:
- Use every task the owner listed. If one cannot be placed safely, keep it in the plan
  at your best-guess time AND record the problem in `unmet_constraints` (ROUTINE-007).
  Never silently drop a task.
- Times are 24-hour HH:MM.
- Cite only rule IDs that appear in the guidance above. If no rule applies to a task,
  return an empty `cited_rules` list. Never invent an ID.
- Set `confidence` honestly: lower it when the guidance is thin, when constraints
  conflict, or when you had to compromise."""

_CRITIQUE_SYSTEM = f"""{_SHARED_ROLE}

You are reviewing a proposed schedule. Your job is to find what is WRONG with it. Be
specific and be sceptical — a plan that passes review unchallenged should be genuinely
clean, not merely plausible.

GUIDANCE:
{{guidance}}

A conflict detector has already been run over the proposed plan; its output is given to
you as verified fact. Treat those collisions as real, and judge whether each one
actually matters under the guidance (two pets fed at once is usually fine; two separate
30-minute walks at once is not — see ROUTINE-002).

Mark an issue `critical` only when the plan is unsafe or breaks a hard rule, such as a
medication given at the wrong time relative to food, or exercise too close to a meal.
Set `approved` to true only when there are no critical issues.

Cite only rule IDs that appear in the guidance. Never invent one."""

_REVISE_SYSTEM = f"""{_SHARED_ROLE}

You are revising a schedule you previously produced, in response to a review. Fix every
critical issue and every warning you agree with.

GUIDANCE:
{{guidance}}

If you disagree with a review point, keep your original timing and explain why in the
relevant task's `rationale`. Do not drop tasks to make issues disappear — that is a
ROUTINE-007 violation. Return the complete revised plan, not a diff."""


# ---------------------------------------------------------------------- result


@dataclass
class PlanResult:
    """Everything one planning run produced, including how it got there."""

    plan: CarePlan | None
    critiques: list[Critique] = field(default_factory=list)
    rounds: int = 0
    retrieved_rule_ids: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    residual_conflicts: list[str] = field(default_factory=list)
    confidence: float = 0.0
    refused: bool = False
    refusal_reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether a usable plan came back."""
        return self.plan is not None and not self.refused and not self.error

    @property
    def approved(self) -> bool:
        """Whether the final critique signed the plan off."""
        return bool(self.critiques) and self.critiques[-1].approved


# ------------------------------------------------------------------- utilities


def build_query(pets: list[Pet], extra: str = "") -> str:
    """Turn the owner's pets and tasks into a retrieval query.

    Species, breed, age, and medications all matter for which rules apply, so they go
    into the query alongside the task descriptions.
    """
    parts: list[str] = []
    for pet in pets:
        bits = [pet.name, pet.species, pet.breed or "", f"{pet.age} years old" if pet.age else ""]
        if pet.medications:
            bits.append(f"medications {pet.medications}")
        if pet.allergies:
            bits.append(f"allergies {pet.allergies}")
        parts.append(" ".join(b for b in bits if b))
        parts.extend(f"{t.description} at {t.time} {t.frequency}" for t in pet.get_tasks())

    if extra:
        parts.append(extra)
    return ". ".join(parts)


def describe_pets(pets: list[Pet]) -> str:
    """Render the owner's pets and requested tasks as prompt input."""
    lines: list[str] = []
    for pet in pets:
        header = f"- {pet.name} ({pet.species}"
        if pet.breed:
            header += f", {pet.breed}"
        if pet.age:
            header += f", {pet.age} years old"
        header += ")"
        if pet.medications:
            header += f" | medications: {pet.medications}"
        if pet.allergies:
            header += f" | allergies: {pet.allergies}"
        lines.append(header)

        tasks = pet.get_tasks()
        if not tasks:
            lines.append("    (no tasks requested)")
        for task in tasks:
            lines.append(
                f"    * {task.description} — requested {task.time}, {task.frequency}"
            )
    return "\n".join(lines)


def plan_to_conflicts(plan: CarePlan) -> list[str]:
    """Run the original Module 2 conflict detector over an AI-proposed plan.

    This is the bridge that makes the rule-based engine a tool inside the agentic loop.
    The proposed tasks are rebuilt as real `Task` objects owned by real `Pet` objects,
    then handed to the untouched `Scheduler.detect_conflicts()`.
    """
    owner = Owner("plan-check")
    pets: dict[str, Pet] = {}

    for item in plan.tasks:
        name = item.pet_name or "unspecified"
        if name not in pets:
            pets[name] = Pet(name=name, species="unknown")
            owner.add_pet(pets[name])
        try:
            pets[name].add_task(
                Task(item.description, item.time, "once", due_date=date.today())
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            logger.warning("Skipping malformed task in conflict check: %r", item)

    try:
        return Scheduler(owner).detect_conflicts()
    except ValueError as exc:
        # sort_by_time parses HH:MM; a malformed time would raise here. detect_conflicts
        # itself does not sort, but keep the guard so a bad model time can't crash a run.
        logger.warning("Conflict detection failed: %s", exc)
        return []


def verify_citations(plan: CarePlan, critique: Critique | None, kb: KnowledgeBase) -> list[str]:
    """Return every cited rule ID that does not exist in the knowledge base."""
    cited: list[str] = []
    for task in plan.tasks:
        cited.extend(task.cited_rules)
    if critique:
        for issue in critique.issues:
            cited.extend(issue.cited_rules)

    seen: set[str] = set()
    invalid: list[str] = []
    for rule_id in cited:
        key = rule_id.strip().upper()
        if key and key not in seen:
            seen.add(key)
            if not kb.exists(key):
                invalid.append(rule_id)
    return invalid


def score_confidence(
    plan: CarePlan,
    critique: Critique | None,
    invalid_citations: list[str],
    residual_conflicts: list[str],
) -> float:
    """Combine the model's self-rating with what we independently verified.

    The model's own confidence is a starting point, not the answer. Each thing we can
    check and find wanting pulls it down:

    * an unresolved critical issue is the strongest signal the plan is not trustworthy;
    * a hallucinated citation means the grounding claim itself is unreliable;
    * a conflict the deterministic detector still sees means the loop did not converge.
    """
    score = plan.clamped_confidence

    if critique:
        score -= 0.30 * len(critique.critical_issues)
        score -= 0.05 * sum(1 for i in critique.issues if i.severity == "warning")

    score -= 0.15 * len(invalid_citations)
    score -= 0.05 * len(residual_conflicts)

    # A plan citing no rules at all was not meaningfully grounded, whatever it claims.
    if plan.tasks and not any(t.cited_rules for t in plan.tasks):
        score -= 0.20

    return round(max(0.0, min(1.0, score)), 2)


# ------------------------------------------------------------------ the loop


class CarePlanner:
    """Runs the retrieve -> plan -> critique -> revise loop."""

    def __init__(
        self,
        llm: LLMClient,
        kb: KnowledgeBase | None = None,
        max_revisions: int | None = None,
        retrieval_k: int | None = None,
    ) -> None:
        self.llm = llm
        self.kb = kb or KnowledgeBase.load()
        self.max_revisions = (
            max_revisions if max_revisions is not None else config.MAX_REVISIONS
        )
        self.retrieval_k = retrieval_k if retrieval_k is not None else config.RETRIEVAL_K

    # -- stages ------------------------------------------------------------

    def screen(self, request_text: str, guidance: str) -> SafetyVerdict:
        """Input guardrail. Runs before any planning work."""
        return self.llm.structured(
            system=_SCREEN_SYSTEM.format(guidance=guidance),
            user=f"Request from the owner:\n\n{request_text}",
            output_format=SafetyVerdict,
            stage="screen",
        )

    def propose(self, pets_block: str, notes: str, guidance: str) -> CarePlan:
        """First-pass plan, grounded in the retrieved guidance."""
        user = f"Build today's care schedule.\n\nPETS AND REQUESTED TASKS:\n{pets_block}"
        if notes:
            user += f"\n\nOWNER'S NOTES AND CONSTRAINTS:\n{notes}"
        return self.llm.structured(
            system=_PLAN_SYSTEM.format(guidance=guidance),
            user=user,
            output_format=CarePlan,
            stage="propose",
        )

    def critique(self, plan: CarePlan, conflicts: list[str], guidance: str) -> Critique:
        """Review a proposed plan against the guidance and the verified conflicts."""
        conflict_block = (
            "\n".join(f"- {c}" for c in conflicts)
            if conflicts
            else "- none detected"
        )
        user = (
            f"PROPOSED PLAN:\n{_render_plan(plan)}\n\n"
            f"VERIFIED CONFLICTS (from the deterministic detector — treat as fact):\n"
            f"{conflict_block}\n\n"
            "Review this plan. Find what is wrong with it."
        )
        return self.llm.structured(
            system=_CRITIQUE_SYSTEM.format(guidance=guidance),
            user=user,
            output_format=Critique,
            stage="critique",
        )

    def revise(self, plan: CarePlan, critique: Critique, guidance: str) -> CarePlan:
        """Produce a corrected plan in response to a critique."""
        issues = "\n".join(
            f"- [{i.severity}] {i.task_reference}: {i.problem} "
            f"-> {i.suggested_fix} (rules: {', '.join(i.cited_rules) or 'none'})"
            for i in critique.issues
        )
        user = (
            f"YOUR PREVIOUS PLAN:\n{_render_plan(plan)}\n\n"
            f"REVIEW FINDINGS:\n{issues}\n\n"
            "Return the corrected full plan."
        )
        return self.llm.structured(
            system=_REVISE_SYSTEM.format(guidance=guidance),
            user=user,
            output_format=CarePlan,
            stage="revise",
        )

    # -- orchestration -----------------------------------------------------

    def plan_day(self, pets: list[Pet], notes: str = "") -> PlanResult:
        """Plan one day of care for *pets*.

        Never raises for an expected failure — API problems, refusals, and
        non-convergence all come back as a `PlanResult` the caller can render.
        """
        result = PlanResult(plan=None)

        if not pets:
            result.error = "No pets provided. Add a pet and at least one task first."
            return result

        # 1. Retrieve. Everything downstream is grounded in this one context block.
        query = build_query(pets, notes)
        retrieved = self.kb.search(query, k=self.retrieval_k)
        guidance = format_context(retrieved)
        result.retrieved_rule_ids = [r.rule.rule_id for r in retrieved]
        logger.info("Retrieved %d rules: %s", len(retrieved), ", ".join(result.retrieved_rule_ids))

        pets_block = describe_pets(pets)

        try:
            # 2. Screen. Refuse emergencies and clinical questions before planning.
            verdict = self.screen(f"{pets_block}\n\nNotes: {notes}", guidance)
            if verdict.decision == "refuse":
                result.refused = True
                result.refusal_reason = verdict.reason
                logger.warning("Request refused by safety screen: %s", verdict.reason)
                return result

            # 3. Propose.
            plan = self.propose(pets_block, notes, guidance)

            # 4. Critique and revise until clean or out of rounds.
            critique: Critique | None = None
            for round_number in range(1, self.max_revisions + 1):
                conflicts = plan_to_conflicts(plan)
                critique = self.critique(plan, conflicts, guidance)
                result.critiques.append(critique)
                result.rounds = round_number

                logger.info(
                    "Round %d: approved=%s issues=%d (critical=%d)",
                    round_number,
                    critique.approved,
                    len(critique.issues),
                    len(critique.critical_issues),
                )

                if critique.approved and not critique.has_critical:
                    break
                if round_number == self.max_revisions:
                    logger.warning("Hit revision limit with issues still open.")
                    break

                plan = self.revise(plan, critique, guidance)

            # 5. Verify and score against what we can check independently.
            result.plan = plan
            result.residual_conflicts = plan_to_conflicts(plan)
            result.invalid_citations = verify_citations(plan, critique, self.kb)
            if result.invalid_citations:
                logger.warning("Model cited unknown rules: %s", result.invalid_citations)
            result.confidence = score_confidence(
                plan, critique, result.invalid_citations, result.residual_conflicts
            )
            return result

        except LLMRefusal as exc:
            result.refused = True
            result.refusal_reason = str(exc)
            return result
        except LLMError as exc:
            result.error = str(exc)
            logger.error("Planning failed: %s", exc)
            return result


def _render_plan(plan: CarePlan) -> str:
    """Render a plan as compact text for the critique and revise prompts."""
    lines = [
        f"{t.time}  {t.pet_name}: {t.description}"
        f"  [rules: {', '.join(t.cited_rules) or 'none'}]  — {t.rationale}"
        for t in plan.tasks
    ]
    if plan.unmet_constraints:
        lines.append("Unmet constraints: " + "; ".join(plan.unmet_constraints))
    lines.append(f"Stated confidence: {plan.clamped_confidence:.2f}")
    return "\n".join(lines)
