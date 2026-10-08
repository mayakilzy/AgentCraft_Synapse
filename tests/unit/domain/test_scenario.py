"""Scenario tests — Future Intelligence invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import HorizonClass
from synapse.domain.scenario import Scenario


def test_f0_scenario_ok_without_critique():
    s = Scenario(
        horizon_class=HorizonClass.F0,
        capabilities=["existing-cap"],
    )
    assert s.horizon_class == "F0"


def test_f4_requires_critique():
    """Speculative scenarios cannot be silent."""
    with pytest.raises(ValidationError):
        Scenario(
            horizon_class=HorizonClass.F4,
            missing_dependencies=["X"],
        )


def test_f4_with_critique_ok():
    s = Scenario(
        horizon_class=HorizonClass.F4,
        missing_dependencies=["X"],
        critique="Speculative; risks: ..., assumptions: ...",
    )
    assert s.horizon_class == "F4"


def test_non_f0_requires_missing_deps():
    with pytest.raises(ValidationError):
        Scenario(horizon_class=HorizonClass.F2, missing_dependencies=[])


def test_non_f0_with_missing_deps_ok():
    s = Scenario(
        horizon_class=HorizonClass.F2,
        missing_dependencies=["tool-Y"],
    )
    assert s.horizon_class == "F2"
