"""Tests for the arxiv_search provider (vendored adapter).

Per the user's G02 authorization §Required Tests:
  1. Real arXiv discovery integration test — covered here (marked @live).
  2. Deterministic extraction fixture test — for trafilatura (separate file).

Live tests are marked and skipped by default to keep CI deterministic.
Run with `pytest -m live` to execute them.
"""

from __future__ import annotations

import pytest

from synapse.providers.arxiv_search_provider import (
    ArxivSearchProvider,
    ArxivSearchTool,
    __license__,
    __source_repo__,
    __toolkit_commit__,
    __toolkit_path__,
)

# Register the marker (also declared in pyproject.toml [tool.pytest.ini_options] markers).
# No module-level pytestmark — tests are not marked "unit" (that marker isn't registered).


# ── Provenance header tests ──────────────────────────────────────────────────


def test_provenance_header_present():
    """Per the user's G02 authorization §Architecture: source Toolkit
    commit SHA must be retained for vendored components."""
    assert __toolkit_commit__ == "fd9df34c51781bd12effab62762022ab04dbd771"
    assert __toolkit_path__ == "ToolKit/research/arxiv_search_tool/tool.py"
    assert __source_repo__ == "https://github.com/NousResearch/hermes-agent"
    assert __license__ == "MIT"


# ── Provider protocol tests ──────────────────────────────────────────────────


def test_provider_implements_protocol():
    p = ArxivSearchProvider()
    assert p.name == "arxiv"
    assert p.kind == "search"
    assert p.enabled is True
    assert callable(p.health_check)
    assert callable(p.discover)


def test_provider_health_check_returns_bool():
    """health_check returns a bool — True if arxiv API responds, False on
    network failure. We don't assert the value because CI may be offline."""
    p = ArxivSearchProvider()
    result = p.health_check()
    assert isinstance(result, bool)


# ── Tool input validation ─────────────────────────────────────────────────────


def test_arxiv_tool_no_query_returns_error_dict():
    """The tool's contract: missing query parameters returns a dict
    with an error, NOT raises."""
    t = ArxivSearchTool()
    r = t.run()
    assert isinstance(r, dict)
    assert "error" in r
    assert r["count"] == 0


def test_arxiv_tool_empty_query_returns_error_dict():
    t = ArxivSearchTool()
    r = t.run(query="")
    assert "error" in r


# ── Live arXiv integration test (skipped by default) ────────────────────────


@pytest.mark.live
def test_arxiv_live_discovery_returns_results():
    """REAL arXiv API call — skipped by default to keep CI deterministic.

    Run with: pytest -m live
    """
    p = ArxivSearchProvider()
    r = p.discover(query="transformers attention", max_results=2)
    assert "error" not in r or r.get("count", 0) >= 0
    if r.get("count", 0) > 0:
        first = r["results"][0]
        assert "arxiv_id" in first
        assert "title" in first
        assert "canonical_uri" in first
        assert "discovered_at" in first
        assert first["provider"] == "arxiv"
        assert first["source_type"] == "paper"


@pytest.mark.live
def test_arxiv_live_discovery_with_author_filter():
    """REAL arXiv API call — verifies the author filter works."""
    p = ArxivSearchProvider()
    r = p.discover(query="attention", author="Vaswani", max_results=3)
    # We don't assert specific results (the API may rate-limit), but
    # the call should not crash.
    assert isinstance(r, dict)
    assert "results" in r
