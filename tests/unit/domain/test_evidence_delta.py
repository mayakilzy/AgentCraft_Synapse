"""EvidenceDelta tests — invariant §5: must change state."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import EpistemicState
from synapse.domain.evidence_delta import EvidenceDelta


def test_delta_changes_state_ok():
    d = EvidenceDelta(
        hypothesis_id="h1",
        prior_state=EpistemicState.HYPOTHESIZED,
        observation="experiment-1 supports the hypothesis",
        update_method="manual",
        updated_state=EpistemicState.SUPPORTED,
        evidence_refs=["ev1"],
    )
    assert d.updated_state == "supported"


def test_delta_must_change_state():
    """Per Master Spec §12: a no-op delta is forbidden."""
    with pytest.raises(ValidationError):
        EvidenceDelta(
            hypothesis_id="h1",
            prior_state=EpistemicState.SUPPORTED,
            observation="nothing changed",
            update_method="manual",
            updated_state=EpistemicState.SUPPORTED,
            evidence_refs=["ev1"],
        )


def test_delta_to_rejected_requires_evidence():
    with pytest.raises(ValidationError):
        EvidenceDelta(
            hypothesis_id="h1",
            prior_state=EpistemicState.HYPOTHESIZED,
            observation="contradicting observation",
            update_method="manual",
            updated_state=EpistemicState.REJECTED,
            evidence_refs=[],
        )


def test_delta_to_rejected_with_evidence_ok():
    d = EvidenceDelta(
        hypothesis_id="h1",
        prior_state=EpistemicState.HYPOTHESIZED,
        observation="contradicting observation",
        update_method="manual",
        updated_state=EpistemicState.REJECTED,
        evidence_refs=["ev1", "ev2"],
    )
    assert d.updated_state == "rejected"
