"""Structured output schemas for the planner.

These are Pydantic models passed to the Claude API as `output_format`, so the model
is constrained to return exactly this shape. That removes a whole class of failure —
we never parse free text or hand-roll JSON extraction.

Numeric ranges are deliberately *not* expressed as schema constraints. Structured
outputs do not support `minimum`/`maximum`, so a bound there would be stripped and
enforced client-side as a hard validation error, turning a slightly-out-of-range
confidence into a crash. We take the value and clamp it instead (see
`CarePlan.clamped_confidence`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "warning", "suggestion"]


class PlannedTask(BaseModel):
    """One scheduled care task in a generated plan."""

    time: str = Field(description="24-hour time in HH:MM format, e.g. '08:00'.")
    pet_name: str = Field(description="Which pet this task is for.")
    description: str = Field(description="What the owner should do, in a short phrase.")
    rationale: str = Field(
        description="One sentence on why this task sits at this time, referencing the guidance."
    )
    cited_rules: list[str] = Field(
        description=(
            "IDs of knowledge-base rules that justify this timing, e.g. ['MED-002']. "
            "Use only IDs present in the provided guidance. Empty list if no rule applies."
        )
    )


class CarePlan(BaseModel):
    """A full day's care plan produced by the planner."""

    tasks: list[PlannedTask] = Field(description="Every task, ordered earliest to latest.")
    conflicts_resolved: list[str] = Field(
        description="Scheduling conflicts found and how each was resolved."
    )
    unmet_constraints: list[str] = Field(
        description=(
            "Constraints that could NOT be satisfied, and why. Required by ROUTINE-007 — "
            "never silently drop a task the owner asked for."
        )
    )
    confidence: float = Field(
        description="How confident the plan is correct and complete, from 0.0 to 1.0."
    )
    summary: str = Field(description="Two or three sentences an owner can read at a glance.")

    @property
    def clamped_confidence(self) -> float:
        """Confidence forced into [0.0, 1.0].

        The model very occasionally returns a value slightly outside the range (or a
        percentage). Clamping keeps a cosmetic slip from failing an otherwise good plan.
        """
        value = self.confidence
        if value > 1.0:
            # A model that answered "85" instead of "0.85" is still telling us 85%.
            value = value / 100.0 if value <= 100.0 else 1.0
        return max(0.0, min(1.0, value))


class CritiqueIssue(BaseModel):
    """One problem the critic found in a proposed plan."""

    severity: Severity = Field(
        description=(
            "'critical' if the plan is unsafe or violates a hard rule; "
            "'warning' if it is suboptimal; 'suggestion' for a minor improvement."
        )
    )
    task_reference: str = Field(
        description="Which task the issue concerns, e.g. \"08:00 Mochi allergy pill\"."
    )
    problem: str = Field(description="What is wrong, in one sentence.")
    suggested_fix: str = Field(description="The concrete change that would resolve it.")
    cited_rules: list[str] = Field(
        description="Rule IDs the issue is based on. Use only IDs from the provided guidance."
    )


class Critique(BaseModel):
    """The critic's verdict on a proposed plan."""

    approved: bool = Field(
        description="True only if there are no critical issues and the plan is safe to follow."
    )
    issues: list[CritiqueIssue] = Field(description="Every issue found. Empty if the plan is clean.")
    assessment: str = Field(description="One or two sentences summarising the plan's quality.")

    @property
    def critical_issues(self) -> list[CritiqueIssue]:
        return [i for i in self.issues if i.severity == "critical"]

    @property
    def has_critical(self) -> bool:
        return bool(self.critical_issues)


class SafetyVerdict(BaseModel):
    """Input-guardrail decision, made before any planning work happens."""

    decision: Literal["proceed", "refuse"] = Field(
        description=(
            "'refuse' if the request asks for diagnosis, dosage changes, or describes a "
            "medical emergency (SAFE-001 to SAFE-003). 'proceed' for ordinary scheduling."
        )
    )
    reason: str = Field(
        description=(
            "If refusing, what the owner should do instead (e.g. contact an emergency vet). "
            "If proceeding, a brief note on why the request is in scope."
        )
    )
