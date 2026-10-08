"""Source domain model tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain.source import Source, SourceType


def test_source_minimal_ok():
    s = Source(canonical_uri="https://example.com/page")
    assert s.canonical_uri.host == "example.com"
    assert s.source_type == SourceType.OTHER
    assert s.id  # auto-generated


def test_source_rejects_invalid_url():
    with pytest.raises(ValidationError):
        Source(canonical_uri="not a url")
    with pytest.raises(ValidationError):
        Source(canonical_uri="ftp://example.com/file")


def test_source_rejects_schemeless_url():
    with pytest.raises(ValidationError):
        Source(canonical_uri="example.com")


def test_source_extra_field_forbidden():
    """Per ADR-0005: extra="forbid" catches typos."""
    with pytest.raises(ValidationError):
        Source(canonical_uri="https://example.com", unknown_field="x")


def test_source_canonical_uri_must_have_host():
    with pytest.raises((ValidationError, ValueError)):
        Source(canonical_uri="https:///")
