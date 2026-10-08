"""EvidenceDelta — the immutable audit record of a belief change.

Per Master Spec §12: "A failed run changes only what the observations
justify; maintain experiment lineage and reproducibility." Per invariant §5:
"Experiment result never mutates prior belief in place without a
corresponding immutable EvidenceDelta audit record."

This is the *append-only* audit log of the system's epistemic state.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, EpistemicState


class EvidenceDelta(DomainRecord):
    hypothesis_id: str = Field(..., min_length=1)
    experiment_id: str | None = None  # may be absent if delta is from review
    prior_state: EpistemicState = Field(...)
    observation: str = Field(..., min_length=1)
    update_method: str = Field(..., min_length=1)  # e.g. "bayesian", "manual"
    updated_state: EpistemicState = Field(...)
    applicable_conditions: list[str] = Field(default_factory=list)
    timestamp: str | None = None  # ISO8601 of the original observation
    reviewer: str | None = None  # who made the decision
    evidence_refs: list[str] = Field(default_factory=list)  # supporting fragments
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def _must_change_state(self) -> EvidenceDelta:
        """A delta with no state change is a no-op audit record — forbidden."""
        if self.prior_state == self.updated_state:
            raise ValueError(
                "EvidenceDelta must change state — "
                f"prior_state == updated_state == {self.prior_state}"
            )
        return self

    @model_validator(mode="after")
    def _rejected_requires_contradicting_evidence(self) -> EvidenceDelta:
        """A move to REJECTED must cite contradicting evidence."""
        if self.updated_state == EpistemicState.REJECTED and not self.evidence_refs:
            raise ValueError(
                "EvidenceDelta moving to epistemic_state=rejected requires "
                "at least one evidence_ref"
            )
        return self
