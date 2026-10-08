"""Acquisition domain model tests — focuses on invariant §3."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import AcquisitionCompleteness, AcquisitionStatus
from synapse.domain.acquisition import Acquisition


def test_full_requires_succeeded_and_hash():
    a = Acquisition(
        source_id="s1",
        provider="http-fetch",
        completeness=AcquisitionCompleteness.FULL,
        status=AcquisitionStatus.SUCCEEDED,
        content_hash="abcdef0123456789",
    )
    assert a.completeness == "full"


def test_full_with_error_rejected():
    """Per invariant §3: partial fetch never represented as full."""
    with pytest.raises(ValidationError):
        Acquisition(
            source_id="s1",
            provider="http-fetch",
            completeness=AcquisitionCompleteness.FULL,
            status=AcquisitionStatus.SUCCEEDED,
            content_hash="abcdef0123456789",
            error_code="partial_bytes",
        )


def test_full_without_hash_rejected():
    with pytest.raises(ValidationError):
        Acquisition(
            source_id="s1",
            provider="http-fetch",
            completeness=AcquisitionCompleteness.FULL,
            status=AcquisitionStatus.SUCCEEDED,
            content_hash=None,
        )


def test_full_with_wrong_status_rejected():
    with pytest.raises(ValidationError):
        Acquisition(
            source_id="s1",
            provider="http-fetch",
            completeness=AcquisitionCompleteness.FULL,
            status=AcquisitionStatus.RUNNING,
            content_hash="abcdef0123456789",
        )


def test_partial_default_status_queued_ok():
    a = Acquisition(source_id="s1", provider="http-fetch")
    assert a.completeness == "partial"
    assert a.status == "queued"


def test_failed_completeness_requires_terminal_status():
    with pytest.raises(ValidationError):
        Acquisition(
            source_id="s1",
            provider="http-fetch",
            completeness=AcquisitionCompleteness.FAILED,
            status=AcquisitionStatus.RUNNING,
        )


def test_failed_completeness_with_failed_status_ok():
    a = Acquisition(
        source_id="s1",
        provider="http-fetch",
        completeness=AcquisitionCompleteness.FAILED,
        status=AcquisitionStatus.FAILED,
        error_code="http_500",
    )
    assert a.status == "failed"
