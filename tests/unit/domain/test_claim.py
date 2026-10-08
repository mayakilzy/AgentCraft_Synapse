"""Claim tests — the central epistemic-state invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import EpistemicState
from synapse.domain.claim import Claim


def test_hypothesized_ok_without_evidence():
    c = Claim(proposition="X causes Y", epistemic_state=EpistemicState.HYPOTHESIZED)
    assert c.epistemic_state == "hypothesized"


def test_supported_requires_evidence():
    """Invariant §1: supported claim must have evidence."""
    with pytest.raises(ValidationError):
        Claim(
            proposition="X causes Y",
            epistemic_state=EpistemicState.SUPPORTED,
            evidence_refs=[],
        )


def test_supported_with_evidence_ok():
    c = Claim(
        proposition="X causes Y",
        epistemic_state=EpistemicState.SUPPORTED,
        evidence_refs=["ev1"],
    )
    assert c.epistemic_state == "supported"


def test_hypothesized_with_evidence_rejected():
    """Per Master Spec §9 — hypothesized means 'no evidence yet'."""
    with pytest.raises(ValidationError):
        Claim(
            proposition="X causes Y",
            epistemic_state=EpistemicState.HYPOTHESIZED,
            evidence_refs=["ev1"],
        )


def test_confidence_requires_method():
    """Per Master Spec §9: confidence must not be decorative."""
    with pytest.raises(ValidationError):
        Claim(
            proposition="X",
            epistemic_state=EpistemicState.SUPPORTED,
            evidence_refs=["ev1"],
            confidence_value=0.95,
            confidence_method=None,
        )


def test_confidence_with_method_ok():
    c = Claim(
        proposition="X",
        epistemic_state=EpistemicState.SUPPORTED,
        evidence_refs=["ev1"],
        confidence_value=0.95,
        confidence_method="bayesian-v1",
    )
    assert c.confidence_value == 0.95


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Claim(
            proposition="X",
            epistemic_state=EpistemicState.SUPPORTED,
            evidence_refs=["ev1"],
            confidence_value=1.5,
            confidence_method="bayesian-v1",
        )
