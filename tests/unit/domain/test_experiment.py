"""Experiment tests — production/networked modes require approval."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain.experiment import Experiment, ExperimentExecutionMode


def test_dry_run_ok_without_approval():
    e = Experiment(
        hypothesis_id="h1",
        protocol="simulate counterfactual",
        execution_mode=ExperimentExecutionMode.DRY_RUN,
    )
    assert e.execution_mode == "dry_run"


def test_production_requires_approval():
    with pytest.raises(ValidationError):
        Experiment(
            hypothesis_id="h1",
            protocol="…",
            execution_mode=ExperimentExecutionMode.PRODUCTION,
            safety_limits={},
        )


def test_production_with_approval_ok():
    e = Experiment(
        hypothesis_id="h1",
        protocol="…",
        execution_mode=ExperimentExecutionMode.PRODUCTION,
        safety_limits={"approved_by": "operator-1"},
    )
    assert e.safety_limits["approved_by"] == "operator-1"


def test_networked_requires_cost_approval():
    with pytest.raises(ValidationError):
        Experiment(
            hypothesis_id="h1",
            protocol="…",
            execution_mode=ExperimentExecutionMode.NETWORKED,
            cost_limits={},
        )


def test_networked_with_cost_approval_ok():
    e = Experiment(
        hypothesis_id="h1",
        protocol="…",
        execution_mode=ExperimentExecutionMode.NETWORKED,
        cost_limits={"approved_by": "operator-2"},
    )
    assert e.execution_mode == "networked"
