"""Experiment — a controlled test of a Hypothesis.

Per `DOMAIN_AND_API_CONTRACTS.md`: ``Experiment`` carries hypothesis_id,
protocol, baseline, metrics, safety/cost limits, execution_mode, result,
artifact_refs. External actions and costly runs require policy approval
(Master Spec §12).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, StrEnum


class ExperimentExecutionMode(StrEnum):
    DRY_RUN = "dry_run"  # no real execution, just simulation
    SANDBOX = "sandbox"  # isolated environment
    LOCAL = "local"  # local machine, no network
    NETWORKED = "networked"  # requires explicit approval
    PRODUCTION = "production"  # requires explicit operator approval


class ExperimentResult(DomainRecord):
    """The outcome of running an experiment — immutable once recorded."""

    outcome: str  # "supporting" | "contradicting" | "inconclusive"
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    notes: str | None = None


class Experiment(DomainRecord):
    hypothesis_id: str = Field(..., min_length=1)
    protocol: str = Field(..., min_length=1)  # description of steps
    baseline: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    safety_limits: dict[str, Any] = Field(default_factory=dict)
    cost_limits: dict[str, Any] = Field(default_factory=dict)
    execution_mode: ExperimentExecutionMode = ExperimentExecutionMode.DRY_RUN
    result: ExperimentResult | None = None
    artifact_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _production_mode_requires_explicit_approval_field(self) -> Experiment:
        """Production experiments require an explicit approval marker."""
        if self.execution_mode == ExperimentExecutionMode.PRODUCTION:
            if "approved_by" not in self.safety_limits:
                raise ValueError("Production-mode experiment requires safety_limits.approved_by")
            if not self.safety_limits.get("approved_by"):
                raise ValueError("safety_limits.approved_by must not be empty")
        if self.execution_mode == ExperimentExecutionMode.NETWORKED:
            if "approved_by" not in self.cost_limits:
                raise ValueError("Networked experiment requires cost_limits.approved_by")
        return self
