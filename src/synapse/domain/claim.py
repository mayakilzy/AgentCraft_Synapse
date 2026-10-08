"""Claim — a proposition about the world, with epistemic state.

Per `DOMAIN_AND_API_CONTRACTS.md`:
``Claim`` carries epistemic_state, confidence metadata, validity_conditions,
version. Per invariant §1: "every verified claim and explicit relationship
has at least one inspectable evidence fragment; otherwise reject
verification transition."

So: a Claim with ``epistemic_state == "supported"`` MUST have at least one
``evidence_ref`` — enforced below.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, EpistemicState


class Claim(DomainRecord):
    proposition: str = Field(..., min_length=1, max_length=2048)
    subject_ref: str | None = None  # Entity ID
    object_ref: str | None = None  # Entity ID
    evidence_refs: list[str] = Field(default_factory=list)
    epistemic_state: EpistemicState = EpistemicState.HYPOTHESIZED
    confidence_value: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_method: str | None = None  # how the value was calibrated
    validity_conditions: list[str] = Field(default_factory=list)
    contradicting_refs: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")

    @model_validator(mode="after")
    def _supported_must_have_evidence(self) -> Claim:
        """Invariant: a supported claim has at least one evidence fragment.

        Without this, the system would silently promote speculation to fact.
        """
        if self.epistemic_state == EpistemicState.SUPPORTED:
            if not self.evidence_refs:
                raise ValueError(
                    "Claim with epistemic_state=supported MUST have at least one evidence_ref"
                )
        # HYPOTHESIZED with evidence is contradictory — hypothesis means
        # "no evidence yet" by definition per Master Spec §9.
        if self.epistemic_state == EpistemicState.HYPOTHESIZED and self.evidence_refs:
            raise ValueError(
                "Claim with epistemic_state=hypothesized must not have "
                "evidence_refs (use 'supported' or 'inferred' instead)"
            )
        return self

    @model_validator(mode="after")
    def _confidence_requires_method(self) -> Claim:
        """Per Master Spec §9: confidence must not be a decorative number."""
        if self.confidence_value is not None and not self.confidence_method:
            raise ValueError(
                "confidence_value requires confidence_method (no decorative probabilities)"
            )
        return self
