"""Hypothesis tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain.hypothesis import (
    HYPOTHESIS_TRANSITIONS,
    Hypothesis,
    HypothesisStatus,
)


def test_hypothesis_with_assumptions_ok():
    h = Hypothesis(
        description="Tool X enables capability Y",
        assumptions=["Y is reachable from current stack"],
    )
    assert h.status == HypothesisStatus.PROPOSED


def test_hypothesis_with_premise_ok():
    h = Hypothesis(
        description="…",
        premise_relationship_ids=["rel-1"],
    )
    assert h.premise_relationship_ids == ["rel-1"]


def test_hypothesis_without_premise_or_assumption_rejected():
    with pytest.raises(ValidationError):
        Hypothesis(description="just a sentence")


def test_hypothesis_transitions_complete():
    """Every status must have an entry in the transitions table."""
    for status in HypothesisStatus:
        assert status in HYPOTHESIS_TRANSITIONS


def test_hypothesis_superseded_is_terminal():
    assert HYPOTHESIS_TRANSITIONS[HypothesisStatus.SUPERSEDED] == set()
