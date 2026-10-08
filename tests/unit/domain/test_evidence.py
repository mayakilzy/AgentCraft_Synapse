"""EvidenceFragment tests — invariant: never lose original source linkage."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain.evidence import EvidenceFragment, Locator


def test_evidence_with_excerpt_ok():
    e = EvidenceFragment(
        acquisition_id="a1",
        exact_excerpt="…",
        extraction_method="trafilatura",
        source_id="src1",
    )
    assert e.exact_excerpt
    assert e.source_id == "src1"


def test_evidence_with_only_hash_ok():
    e = EvidenceFragment(
        acquisition_id="a1",
        excerpt_hash="abcdef0123456789",
        extraction_method="hash-only",
        source_uri="https://example.com/page",
    )
    assert e.excerpt_hash


def test_evidence_requires_excerpt_or_hash():
    with pytest.raises(ValidationError):
        EvidenceFragment(
            acquisition_id="a1",
            exact_excerpt=None,
            excerpt_hash=None,
            extraction_method="x",
            source_id="s1",
        )


def test_evidence_requires_source_linkage():
    """Per invariant §1: source linkage is mandatory."""
    with pytest.raises(ValidationError):
        EvidenceFragment(
            acquisition_id="a1",
            exact_excerpt="text",
            extraction_method="x",
            source_id=None,
            source_uri=None,
        )


def test_locator_span_valid():
    loc = Locator(section="2.1", page=4, span=(10, 20))
    assert loc.span == (10, 20)


def test_locator_span_inverted_rejected():
    with pytest.raises(ValidationError):
        Locator(span=(20, 10))
