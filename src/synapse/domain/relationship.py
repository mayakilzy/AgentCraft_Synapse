"""Relationship — a first-class record linking two Entities.

Relationship is THE central object of Synapse (per Master Spec §6). It is
never auto-promoted: derived/hypothesized relationships retain their
derivation trace and stay unverified until reviewed or tested (invariant §2
in `DOMAIN_AND_API_CONTRACTS.md`).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import (
    DomainRecord,
    Origin,
    RelationshipDirection,
    VerificationState,
)

# Initial predicate vocabulary per Master Spec §6.
# Each predicate has a typed signature — `from_kind` and `to_kind` are
# EntityType values that constrain which entity pairs the predicate applies to.
# `*` means any kind.
PREDICATES: dict[str, dict[str, Any]] = {
    "PROVIDES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "REQUIRES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "ENABLES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "IMPROVES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "LIMITS": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "INTEGRATES_WITH": {"direction": "undirected", "from_kind": "*", "to_kind": "*"},
    "REPLACES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "DEPENDS_ON": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "SUPPORTS": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "CONTRADICTS": {"direction": "undirected", "from_kind": "*", "to_kind": "*"},
    "VALIDATES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "INVALIDATES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "INSPIRED_BY": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "USEFUL_FOR": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "TESTS": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
    "PRODUCES": {"direction": "directed", "from_kind": "*", "to_kind": "*"},
}


class Relationship(DomainRecord):
    from_entity_id: str = Field(..., min_length=1)
    to_entity_id: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    direction: RelationshipDirection = RelationshipDirection.DIRECTED
    origin: Origin = Origin.EXPLICIT
    verification_state: VerificationState = VerificationState.UNVERIFIED
    evidence_refs: list[str] = Field(default_factory=list)
    confidence_value: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_method: str | None = None
    conditions: list[str] = Field(default_factory=list)
    valid_from: str | None = None  # ISO8601
    valid_to: str | None = None
    superseded_by: str | None = None
    derivation_chain: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _predicate_must_be_known(self) -> Relationship:
        if self.predicate not in PREDICATES:
            raise ValueError(f"Unknown predicate {self.predicate!r}. Allowed: {sorted(PREDICATES)}")
        return self

    @model_validator(mode="after")
    def _no_self_loop(self) -> Relationship:
        """A self-loop (from == to) on a directed predicate is suspicious."""
        if self.from_entity_id == self.to_entity_id:
            raise ValueError("Relationship.from_entity_id must differ from to_entity_id")
        return self

    @model_validator(mode="after")
    def _derived_hypothesized_never_verified(self) -> Relationship:
        """Invariant §2: derived/hypothesized relationships are never
        auto-promoted to verified."""
        if self.origin in (Origin.DERIVED, Origin.HYPOTHESIZED):
            if self.verification_state == VerificationState.VERIFIED:
                raise ValueError(
                    "Relationship with origin in {derived, hypothesized} "
                    "cannot have verification_state=verified "
                    "(requires explicit review)"
                )
        return self

    @model_validator(mode="after")
    def _explicit_verified_requires_evidence(self) -> Relationship:
        """Invariant §1: every verified explicit relationship has at least
        one evidence fragment."""
        if (
            self.origin == Origin.EXPLICIT
            and self.verification_state == VerificationState.VERIFIED
            and not self.evidence_refs
        ):
            raise ValueError("Verified explicit relationship requires at least one evidence_ref")
        return self

    @model_validator(mode="after")
    def _derived_requires_derivation_chain(self) -> Relationship:
        """Derived relationships must record how they were derived."""
        if self.origin == Origin.DERIVED and not self.derivation_chain:
            raise ValueError("Derived relationship requires a non-empty derivation_chain")
        return self

    @model_validator(mode="after")
    def _confidence_requires_method(self) -> Relationship:
        if self.confidence_value is not None and not self.confidence_method:
            raise ValueError(
                "confidence_value requires confidence_method (no decorative probabilities)"
            )
        return self

    @model_validator(mode="after")
    def _validity_window(self) -> Relationship:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must be <= valid_to")
        return self
