"""Scenario — a future-intelligence hypothesis with horizon class.

Per Master Spec §13: Future Intelligence reuses capability networks and
relationship services; produces F0..F4 scenarios. Each scenario includes
evidence, assumptions, constraints, missing capabilities, risks,
uncertainty, time-horizon class and minimum testable prototype.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, HorizonClass, StrEnum


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    UNDER_CRITIQUE = "under_critique"
    TESTABLE = "testable"
    PROTOTYPED = "prototyped"
    RETIRED = "retired"


class Scenario(DomainRecord):
    current_evidence_refs: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_dependencies: list[str] = Field(default_factory=list)
    horizon_class: HorizonClass = HorizonClass.F0
    critique: str | None = None
    prototype_plan: str | None = None
    status: ScenarioStatus = ScenarioStatus.DRAFT
    risks: dict[str, Any] = Field(default_factory=dict)
    uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _f4_requires_critique(self) -> Scenario:
        """Speculative scenarios (F4) must carry a critique — they cannot
        be silent speculation."""
        if self.horizon_class == HorizonClass.F4 and not self.critique:
            raise ValueError("Scenario with horizon_class=F4 requires a critique")
        return self

    @model_validator(mode="after")
    def _non_f0_must_list_missing_deps(self) -> Scenario:
        """Non-existing scenarios must explicitly list what's missing —
        prevents hand-waving futures."""
        if self.horizon_class != HorizonClass.F0 and not self.missing_dependencies:
            raise ValueError(
                f"Scenario with horizon_class={self.horizon_class} requires "
                "at least one missing_dependency"
            )
        return self
