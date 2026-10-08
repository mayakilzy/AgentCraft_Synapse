"""Job tests — state machine + progress consistency."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import JobStatus
from synapse.domain.job import Job, JobKind


def test_queued_default_ok():
    j = Job()
    assert j.status == JobStatus.QUEUED
    assert j.progress == 0.0


def test_succeeded_must_have_progress_1():
    with pytest.raises(ValidationError):
        Job(status=JobStatus.SUCCEEDED, progress=0.5)


def test_queued_must_have_progress_0():
    with pytest.raises(ValidationError):
        Job(status=JobStatus.QUEUED, progress=0.5)


def test_failed_requires_error_code():
    with pytest.raises(ValidationError):
        Job(status=JobStatus.FAILED)


def test_failed_with_error_code_ok():
    j = Job(status=JobStatus.FAILED, error_code="fetch_timeout")
    assert j.error_code == "fetch_timeout"


def test_transition_queued_to_running_ok():
    j = Job()
    j2 = j.transition_to(JobStatus.RUNNING)
    assert j2.status == "running"
    assert j2.previous_status == "queued"


def test_transition_terminal_forbidden():
    """Cannot revive a terminal job."""
    j = Job(status=JobStatus.FAILED, error_code="x")
    with pytest.raises(ValueError):
        j.transition_to(JobStatus.RUNNING)


def test_transition_skip_running_forbidden():
    j = Job()
    with pytest.raises(ValueError):
        j.transition_to(JobStatus.SUCCEEDED)


def test_job_kind_default():
    j = Job()
    assert j.kind == JobKind.CUSTOM
