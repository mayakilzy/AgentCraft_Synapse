"""Relationship tests — invariant §2: derived/hypothesized never auto-promoted."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import (
    Origin,
    VerificationState,
)
from synapse.domain.relationship import PREDICATES, Relationship


def test_explicit_verified_requires_evidence():
    """Invariant §1: verified explicit relationship needs evidence."""
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.EXPLICIT,
            verification_state=VerificationState.VERIFIED,
            evidence_refs=[],
        )


def test_explicit_verified_with_evidence_ok():
    r = Relationship(
        from_entity_id="e1",
        to_entity_id="e2",
        predicate="PROVIDES",
        origin=Origin.EXPLICIT,
        verification_state=VerificationState.VERIFIED,
        evidence_refs=["ev1"],
    )
    assert r.verification_state == "verified"


def test_derived_cannot_be_verified():
    """Invariant §2: derived/hypothesized never auto-promoted to verified."""
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.DERIVED,
            verification_state=VerificationState.VERIFIED,
            derivation_chain=["rule:counterpart"],
            evidence_refs=["ev1"],
        )


def test_hypothesized_cannot_be_verified():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.HYPOTHESIZED,
            verification_state=VerificationState.VERIFIED,
            evidence_refs=["ev1"],
        )


def test_derived_requires_derivation_chain():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.DERIVED,
            verification_state=VerificationState.UNVERIFIED,
            derivation_chain=[],
        )


def test_unknown_predicate_rejected():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="LIKES_PIZZA",
        )


def test_self_loop_rejected():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e1",
            predicate="PROVIDES",
        )


def test_confidence_requires_method():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.EXPLICIT,
            verification_state=VerificationState.UNVERIFIED,
            confidence_value=0.8,
            confidence_method=None,
        )


def test_validity_window_inverted_rejected():
    with pytest.raises(ValidationError):
        Relationship(
            from_entity_id="e1",
            to_entity_id="e2",
            predicate="PROVIDES",
            origin=Origin.EXPLICIT,
            verification_state=VerificationState.UNVERIFIED,
            valid_from="2026-12-01T00:00:00Z",
            valid_to="2026-01-01T00:00:00Z",
        )


def test_all_predicates_in_vocabulary():
    """Smoke test — verify the 16 predicates from Master Spec §6 exist."""
    expected = {
        "PROVIDES",
        "REQUIRES",
        "ENABLES",
        "IMPROVES",
        "LIMITS",
        "INTEGRATES_WITH",
        "REPLACES",
        "DEPENDS_ON",
        "SUPPORTS",
        "CONTRADICTS",
        "VALIDATES",
        "INVALIDATES",
        "INSPIRED_BY",
        "USEFUL_FOR",
        "TESTS",
        "PRODUCES",
    }
    assert expected.issubset(set(PREDICATES.keys()))
