"""End-to-end integration tests for the G02 minimal slice.

Per the user's G02 authorization §Required Tests:
  3. Complete end-to-end pipeline test.
  5. Duplicate-content behavior test.
  6. SSRF, redirect and unsafe destination negative tests.
  7. Provider failure and timeout tests.

Network-dependent tests are marked @live and skipped by default.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# Fixture HTML for deterministic extraction tests.
FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>Transformer Architecture Explained</title>
<meta name="author" content="Jane Researcher">
<meta name="description" content="A primer on the Transformer architecture.">
</head><body><article>
<h1>Transformer Architecture Explained</h1>
<p>The Transformer architecture, introduced in 2017, replaces recurrence with
self-attention. <a href="https://arxiv.org/abs/1706.03762">See the original paper</a>.</p>
<p>Multi-head attention allows the model to attend to information from different
representation subspaces at different positions.</p>
</article></body></html>"""


# ── Required Test #3: Complete end-to-end pipeline test ──────────────────────


@pytest.mark.asyncio
async def test_end_to_end_pipeline_with_fixture_html(app, db_session):
    """End-to-end: ingest a fixture HTML, extract, fingerprint, persist.

    Uses a local fixture HTML string passed via canonical_uri='fixture://'
    so no real network is needed. The acquire_and_extract() function
    will try to fetch the URL — we patch httpx to return our fixture.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from synapse.application.acquisition import acquire_and_extract

    # Patch httpx.AsyncClient to return our fixture HTML without a real
    # network call.
    fixture_uri = "https://example.com/fixture-paper"

    # Build a fake httpx response that yields our fixture HTML bytes.
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/html"}

    async def _fake_aiter_bytes():
        yield FIXTURE_HTML.encode("utf-8")

    fake_response.aiter_bytes = _fake_aiter_bytes
    fake_response.__aenter__ = AsyncMock(return_value=fake_response)
    fake_response.__aexit__ = AsyncMock(return_value=None)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.stream = MagicMock(return_value=fake_response)
    fake_client.timeout = MagicMock()

    with patch("synapse.application.acquisition.httpx.AsyncClient", return_value=fake_client):
        r = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            provider_name="httpx",
            source_metadata={
                "title": "Transformer Architecture Explained",
                "author": "Jane Researcher",
                "arxiv_id": "2401.00001",
            },
            requester="test-reader",
        )

    assert r["ok"] is True, f"expected ok, got: {r}"
    assert r["source_id"] is not None
    assert r["acquisition_id"] is not None
    assert r["evidence_fragment_id"] is not None
    assert r["content_fingerprint"] is not None
    assert len(r["content_fingerprint"]) == 32
    assert r["toolkit_commit_sha"] == "fd9df34c51781bd12effab62762022ab04dbd771"
    assert r["request_id"] is not None

    await db_session.commit()


# ── Required Test #5: Duplicate-content behavior test ──────────────────────


@pytest.mark.asyncio
async def test_duplicate_ingest_same_uri_creates_one_source(app, db_session):
    """Per DOMAIN_AND_API_CONTRACTS.md invariant §4: repeated ingest of
    same canonical URI does not duplicate the Source row."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from synapse.application.acquisition import acquire_and_extract

    fixture_uri = "https://example.com/duplicate-paper"

    def _make_fake_client():
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.headers = {"content-type": "text/html"}

        async def _aiter():
            yield FIXTURE_HTML.encode("utf-8")

        fake_response.aiter_bytes = _aiter
        fake_response.__aenter__ = AsyncMock(return_value=fake_response)
        fake_response.__aexit__ = AsyncMock(return_value=None)

        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)
        # stream() must return a context-manager-compatible object directly
        fake_client.stream = MagicMock(return_value=fake_response)
        return fake_client

    with patch(
        "synapse.application.acquisition.httpx.AsyncClient", return_value=_make_fake_client()
    ):
        r1 = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            requester="test-reader",
        )
        await db_session.commit()

        r2 = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            requester="test-reader",
        )
        await db_session.commit()

    # Both should succeed.
    assert r1["ok"] is True, f"first ingest failed: {r1}"
    assert r2["ok"] is True, f"second ingest failed: {r2}"
    # Same source_id (idempotency on the Source row).
    assert r1["source_id"] == r2["source_id"]
    # Different acquisition_id and evidence_fragment_id (new fetch, new extract).
    assert r1["acquisition_id"] != r2["acquisition_id"]
    assert r1["evidence_fragment_id"] != r2["evidence_fragment_id"]
    # Same content_fingerprint (same content).
    assert r1["content_fingerprint"] == r2["content_fingerprint"]


# ── Required Test #6: SSRF, redirect, and unsafe destination negative tests ─


@pytest.mark.asyncio
async def test_ssrf_meta_ip_blocked(app, db_session):
    """SSRF guard blocks the cloud metadata IP 169.254.169.254."""
    from synapse.application.acquisition import acquire_and_extract

    r = await acquire_and_extract(
        db_session,
        canonical_uri="http://169.254.169.254/latest/meta-data/",
        requester="test-reader",
    )
    assert r["ok"] is False
    assert "ssrf_blocked" in r["error"]
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ssrf_loopback_blocked(app, db_session):
    """SSRF guard blocks loopback addresses."""
    from synapse.application.acquisition import acquire_and_extract

    r = await acquire_and_extract(
        db_session,
        canonical_uri="http://127.0.0.1:8080/internal",
        requester="test-reader",
    )
    assert r["ok"] is False
    assert "ssrf_blocked" in r["error"]
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ssrf_file_scheme_blocked(app, db_session):
    """SSRF guard blocks file:// scheme."""
    from synapse.application.acquisition import acquire_and_extract

    r = await acquire_and_extract(
        db_session,
        canonical_uri="file:///etc/passwd",
        requester="test-reader",
    )
    assert r["ok"] is False
    assert "ssrf" in r["error"].lower()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_ssrf_rfc1918_blocked(app, db_session):
    """SSRF guard blocks RFC 1918 private addresses."""
    from synapse.application.acquisition import acquire_and_extract

    r = await acquire_and_extract(
        db_session,
        canonical_uri="http://10.0.0.5/internal",
        requester="test-reader",
    )
    assert r["ok"] is False
    assert "ssrf_blocked" in r["error"]
    await db_session.rollback()


# ── Required Test #7: Provider failure and timeout tests ────────────────────


@pytest.mark.asyncio
async def test_provider_failure_http_500(app, db_session):
    """HTTP 500 from the source is recorded as a failed acquisition."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from synapse.application.acquisition import acquire_and_extract

    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.headers = {}

    async def _empty():
        return
        yield  # unreachable — make this an async generator

    fake_response.aiter_bytes = _empty
    fake_response.__aenter__ = AsyncMock(return_value=fake_response)
    fake_response.__aexit__ = AsyncMock(return_value=None)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.stream = MagicMock(return_value=fake_response)

    with patch("synapse.application.acquisition.httpx.AsyncClient", return_value=fake_client):
        r = await acquire_and_extract(
            db_session,
            canonical_uri="https://example.com/failing",
            requester="test-reader",
        )

    assert r["ok"] is False
    assert "http_500" in r["error"]
    await db_session.rollback()


@pytest.mark.asyncio
async def test_provider_timeout(app, db_session):
    """Timeout from the source is recorded as a failed acquisition."""
    from unittest.mock import AsyncMock, MagicMock, patch

    import httpx

    from synapse.application.acquisition import acquire_and_extract

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    # stream() is a MagicMock that raises when entered — emulate timeout.
    fake_stream = MagicMock()
    fake_stream.__aenter__ = AsyncMock(side_effect=httpx.TimeoutException("simulated timeout"))
    fake_stream.__aexit__ = AsyncMock(return_value=None)
    fake_client.stream = MagicMock(return_value=fake_stream)

    with patch("synapse.application.acquisition.httpx.AsyncClient", return_value=fake_client):
        r = await acquire_and_extract(
            db_session,
            canonical_uri="https://example.com/slow",
            requester="test-reader",
        )

    assert r["ok"] is False
    assert "timeout" in r["error"].lower()
    await db_session.rollback()


# ── Required Test #8 (part 1): G01 regression — API contract ─────────────────


def test_openapi_includes_sources_routes(app):
    """The OpenAPI schema must include the now-implemented sources routes
    (no longer 501 placeholders)."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/sources/discover" in paths
    assert "/api/v1/sources/ingest" in paths
    assert "/api/v1/sources" in paths
    assert "/api/v1/sources/{source_id}" in paths
    assert "/api/v1/sources/{source_id}/acquisitions" in paths

    # Discover/ingest must NOT be marked 501 anymore (they have real handlers).
    discover_post = paths["/api/v1/sources/discover"]["post"]
    assert discover_post.get("deprecated") is not True
    assert "501" not in str(discover_post.get("responses", {}))


def test_openapi_still_lists_remaining_501_placeholders(app):
    """Other future-group routes (entities, relationships, etc.) remain
    as 501 placeholders — the G02 slice did not implement them."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/entities" in paths
    assert "/api/v1/relationships" in paths
    assert "/api/v1/knowledge/search" in paths
    assert "/api/v1/experiments" in paths
    assert "/api/v1/future/scenarios" in paths


# ── Required Test #8 (part 2): G01 regression — all tests still pass ─────────


def test_g01_security_tests_still_pass():
    """Run a subset of G01 security tests to confirm no regression."""
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/security/test_ssrf.py",
            "tests/unit/domain/test_claim.py",
            "tests/unit/api/test_capabilities.py",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout


# ── Live integration tests (skipped by default) ─────────────────────────────


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_arxiv_discovery_and_ingest(app, db_session):
    """REAL end-to-end: discover on arXiv, ingest the first result,
    extract via trafilatura, fingerprint, persist.

    Run with: pytest -m live tests/integration/test_g02_minimal_slice.py::test_live_arxiv_discovery_and_ingest
    """
    from synapse.application.acquisition import acquire_and_extract, discover_sources

    # Stage 1: discover
    discovery = await discover_sources(query="attention is all you need", max_results=2)
    assert "error" not in discovery, f"discovery failed: {discovery}"
    assert discovery["count"] > 0, "no results from arxiv"

    first = discovery["results"][0]
    canonical_uri = first.get("canonical_uri") or f"https://arxiv.org/abs/{first['arxiv_id']}"
    source_metadata = {
        "title": first.get("title"),
        "author": (first.get("authors") or [""])[0],
        "arxiv_id": first.get("arxiv_id"),
        "doi": first.get("doi"),
        "published": first.get("published"),
    }

    # Stage 2-6: ingest + extract + fingerprint + persist
    r = await acquire_and_extract(
        db_session,
        canonical_uri=canonical_uri,
        source_type="paper",
        source_metadata=source_metadata,
        requester="test-reader",
    )
    await db_session.commit()

    assert r["ok"] is True, f"ingest failed: {r}"
    assert r["content_fingerprint"] is not None
    assert r["toolkit_commit_sha"] == "fd9df34c51781bd12effab62762022ab04dbd771"
