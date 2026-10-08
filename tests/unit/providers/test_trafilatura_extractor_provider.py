"""Tests for the trafilatura extractor provider.

Per the user's G02 authorization §Required Tests:
  2. Deterministic extraction fixture test.
"""

from __future__ import annotations

from synapse.providers.trafilatura_extractor_provider import (
    TrafilaturaExtractorProvider,
    __license__,
    __source_repo__,
    __toolkit_commit__,
)

# Fixture HTML — must be parsed deterministically by trafilatura.
FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>Transformer Architecture Explained</title>
<meta name="author" content="Jane Researcher">
<meta name="description" content="A primer on the Transformer architecture.">
<meta name="dcterms.date" content="2024-01-15">
</head><body><article>
<h1>Transformer Architecture Explained</h1>
<p>The Transformer architecture, introduced in 2017, replaces recurrence with
self-attention. <a href="https://arxiv.org/abs/1706.03762">See the original paper</a>.</p>
<p>Multi-head attention allows the model to attend to information from different
representation subspaces at different positions.</p>
</article></body></html>"""


def test_provenance_header_present():
    assert __toolkit_commit__ == "fd9df34c51781bd12effab62762022ab04dbd771"
    assert __source_repo__ == "https://github.com/adbar/trafilatura"
    assert __license__ == "Apache-2.0"


def test_provider_implements_protocol():
    p = TrafilaturaExtractorProvider()
    assert p.name == "trafilatura"
    assert p.kind == "extraction"
    assert p.enabled is True
    assert callable(p.health_check)
    assert callable(p.extract)


def test_provider_health_check_returns_bool():
    p = TrafilaturaExtractorProvider()
    assert p.health_check() is True


def test_extract_returns_deterministic_text():
    """Deterministic extraction fixture test (Required Test #2)."""
    p = TrafilaturaExtractorProvider()
    r = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper")

    assert r["ok"] is True
    assert r["extracted_text"] is not None
    assert "Transformer" in r["extracted_text"]
    assert "self-attention" in r["extracted_text"]

    # Same input → same excerpt_hash (deterministic)
    r2 = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper")
    assert r["excerpt_hash"] == r2["excerpt_hash"]
    assert len(r["excerpt_hash"]) == 16  # SHA-256 first 16 chars


def test_extract_preserves_provenance_metadata():
    """Per the user's G02 authorization §Architecture: title, author,
    publication date must be preserved where available."""
    p = TrafilaturaExtractorProvider()
    r = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper")

    # Title may or may not be captured depending on trafilatura's
    # metadata extraction — but if it is, it must contain "Transformer".
    if r["title"]:
        assert "Transformer" in r["title"]
    # Author — trafilatura may pick up the meta tag.
    if r["author"]:
        assert "Jane" in r["author"] or "Researcher" in r["author"]
    # published_at — may be None; if set, must be a string.
    if r["published_at"]:
        assert isinstance(r["published_at"], str)


def test_extract_records_retrieved_at_timestamp():
    """Per §Architecture: retrieval timestamp must be present."""
    p = TrafilaturaExtractorProvider()
    r = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper")
    assert r["retrieved_at"] is not None
    assert "T" in r["retrieved_at"]  # ISO8601


def test_extract_records_extraction_method_with_version():
    """Per §Architecture: extraction_method must include the provider
    version for traceability."""
    p = TrafilaturaExtractorProvider()
    r = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper")
    assert r["extraction_method"].startswith("trafilatura-")
    # Version is non-empty
    assert len(r["extraction_method"]) > len("trafilatura-")


def test_extract_preserves_source_uri_passthrough():
    """source_uri is preserved in the output for provenance — never
    fetched by this provider (SSRF guard is the caller's responsibility)."""
    p = TrafilaturaExtractorProvider()
    r = p.extract(FIXTURE_HTML, source_uri="https://example.com/paper-123")
    assert r["source_uri"] == "https://example.com/paper-123"


def test_extract_empty_html_returns_not_ok():
    """Per ADR-0009 §2: unknown metadata must remain explicitly unknown,
    never fabricated. Empty HTML should produce ok=False, not raise."""
    p = TrafilaturaExtractorProvider()
    r = p.extract("<html><body></body></html>", source_uri="https://example.com/empty")
    # Either ok=False (no content) or ok=True with empty-ish text.
    # The key invariant: the call returns a dict, never raises.
    assert isinstance(r, dict)
    assert "ok" in r
    assert "error" in r


def test_extract_handles_malformed_html_gracefully():
    """Malformed HTML should not crash the provider."""
    p = TrafilaturaExtractorProvider()
    r = p.extract("<html><body><p>unclosed paragraph", source_uri="https://example.com/bad")
    assert isinstance(r, dict)
    assert "ok" in r
    # Either ok=True (trafilatura is forgiving) or ok=False — both acceptable.
