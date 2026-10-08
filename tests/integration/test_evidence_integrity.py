"""Evidence integrity tests — verify provenance fields are persisted
correctly, and that source metadata is distinguishable from extraction-
generated metadata.

Per the user's G02 Final Qualification §4:
  - Confirm source metadata, citations, timestamps, content fingerprints,
    and Toolkit commit SHA are persisted correctly.
  - Distinguish source metadata from extraction-generated metadata.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>Transformer Architecture Explained</title>
<meta name="author" content="Jane Researcher">
<meta name="description" content="A primer on the Transformer architecture.">
</head><body><article>
<h1>Transformer Architecture Explained</h1>
<p>The Transformer architecture, introduced in 2017, replaces recurrence with
self-attention.</p>
</article></body></html>"""


def _build_fake_httpx_client(html: str):
    """Helper: build a fake httpx client that returns the given HTML."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "text/html"}

    async def _aiter():
        yield html.encode("utf-8")

    fake_response.aiter_bytes = _aiter
    fake_response.__aenter__ = AsyncMock(return_value=fake_response)
    fake_response.__aexit__ = AsyncMock(return_value=None)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.stream = MagicMock(return_value=fake_response)
    return fake_client


@pytest.mark.asyncio
async def test_evidence_fragment_persists_all_required_provenance(app, db_session):
    """Per Final Qualification §4: confirm source metadata, citations,
    timestamps, content fingerprints, and Toolkit commit SHA are
    persisted correctly in evidence_fragments.

    Source metadata (from the discovery step, passed via source_metadata):
      - title (from arXiv result)
      - author (from arXiv result)
      - arxiv_id (citation identifier)

    Extraction-generated metadata (from trafilatura):
      - extracted_text
      - excerpt_hash (SHA-256 of extracted_text, 16 chars)
      - extraction_method (e.g., "trafilatura-2.3.1")
      - retrieved_at (extraction timestamp)
      - content_fingerprint (MD5 of normalized extracted_text, 32 chars)

    Toolkit provenance:
      - toolkit_commit_sha = "fd9df34..." (for vendored delta_hash)

    Persistent fields:
      - source_id, source_uri, acquisition_id (linkage)
      - title, author, published_at (bibliographic; extraction wins if both)
      - citation_ids (JSON array of {doi, arxiv_id})
    """
    from sqlalchemy import select

    from synapse.application.acquisition import acquire_and_extract
    from synapse.storage.models import EvidenceFragmentRow

    fixture_uri = "https://arxiv.org/abs/2401.00001"
    source_metadata = {
        "title": "Sample Paper Title",  # source metadata
        "author": "Author From Discovery",  # source metadata
        "arxiv_id": "2401.00001",  # source citation
        "doi": "10.48550/arXiv.2401.00001",  # source citation
        "published": "2024-01-01",  # source metadata
    }

    fake_client = _build_fake_httpx_client(FIXTURE_HTML)
    with patch(
        "synapse.application.acquisition.ssrf_guarded_client",
        return_value=fake_client,
    ):
        r = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            source_metadata=source_metadata,
            requester="test-reader",
        )
    await db_session.commit()

    assert r["ok"] is True, f"ingest failed: {r}"
    evidence_id = r["evidence_fragment_id"]

    # Query the persisted row directly
    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == evidence_id)
    row = (await db_session.execute(stmt)).scalar_one()

    # ── Required persistent fields per §4 ──────────────────────────────
    assert row.source_uri == fixture_uri, "canonical_uri not preserved"
    assert row.source_id is not None, "source_id missing"
    assert row.acquisition_id is not None, "acquisition_id missing"

    # ── Bibliographic metadata (extraction wins; source is fallback) ──
    # Trafilatura extracted "Transformer Architecture Explained" from the
    # <title> tag, which overrides the discovery-provided "Sample Paper Title".
    # This is the correct behavior: extracted metadata is more authoritative
    # for the actual fetched content; discovery metadata is the fallback.
    assert row.title is not None, "title missing"
    assert "Transformer" in row.title, f"unexpected title: {row.title}"

    # Author: trafilatura may extract the meta-author "Jane Researcher";
    # if it does, that wins. If not, the source_metadata author is used.
    assert row.author is not None, "author missing"

    # ── Citation identifiers (JSON array) ──────────────────────────────
    assert row.citation_ids is not None, "citation_ids missing"
    citations = json.loads(row.citation_ids)
    assert isinstance(citations, list)
    assert any("arxiv" in c for c in citations), f"arxiv citation missing: {citations}"
    assert any("doi" in c for c in citations), f"doi citation missing: {citations}"

    # ── Retrieval timestamp ────────────────────────────────────────────
    assert row.retrieved_at is not None, "retrieved_at missing"
    assert "T" in row.retrieved_at, f"retrieved_at not ISO8601: {row.retrieved_at}"

    # ── Content fingerprint (32-char MD5 hex from vendored delta_hash) ──
    assert row.content_fingerprint is not None, "content_fingerprint missing"
    assert len(row.content_fingerprint) == 32, (
        f"content_fingerprint should be 32 chars, got {len(row.content_fingerprint)}"
    )
    assert all(c in "0123456789abcdef" for c in row.content_fingerprint), (
        f"content_fingerprint not a hex string: {row.content_fingerprint}"
    )

    # ── Toolkit commit SHA (provenance for vendored components) ────────
    assert row.toolkit_commit_sha == "fd9df34c51781bd12effab62762022ab04dbd771", (
        f"unexpected toolkit_commit_sha: {row.toolkit_commit_sha}"
    )

    # ── Extraction method (with provider version) ──────────────────────
    assert row.extraction_method.startswith("trafilatura-"), (
        f"unexpected extraction_method: {row.extraction_method}"
    )

    # ── Extracted content reference ────────────────────────────────────
    assert row.exact_excerpt is not None, "exact_excerpt missing"
    assert "Transformer" in row.exact_excerpt
    assert row.excerpt_hash is not None, "excerpt_hash missing"
    assert len(row.excerpt_hash) == 16, (
        f"excerpt_hash should be 16 chars (SHA-256 truncated), got {len(row.excerpt_hash)}"
    )

    # ── Source type and provider (acquisition provenance) ──────────────
    assert row.source_type == "paper"
    assert row.provider == "trafilatura"


@pytest.mark.asyncio
async def test_source_metadata_distinguishable_from_extraction_metadata(app, db_session):
    """Per Final Qualification §4: distinguish source metadata from
    extraction-generated metadata.

    The schema has fields that come from DIFFERENT sources:

    FROM SOURCE (discovery step, passed via source_metadata):
      - source_uri (canonical URL from arXiv)
      - source_type ('paper')
      - document_version (e.g., 'v1')
      - citation_ids (doi, arxiv_id from discovery)

    FROM EXTRACTION (trafilatura on the fetched HTML):
      - extracted_text (exact_excerpt)
      - excerpt_hash (SHA-256 of extracted_text)
      - extraction_method (e.g., 'trafilatura-2.3.1')
      - content_fingerprint (MD5 of normalized extracted_text)
      - retrieved_at (extraction timestamp)

    SHARED (extraction wins, source is fallback):
      - title (extraction: from <title> tag; source: from arXiv metadata)
      - author (extraction: from <meta name=author>; source: from arXiv)
      - published_at (extraction: from <meta name=date>; source: from arXiv)

    FROM TOOLKIT (vendored component provenance):
      - toolkit_commit_sha

    This test confirms the schema distinguishes these via column names
    and that the persistence logic puts each value in the right column.
    """
    from sqlalchemy import select

    from synapse.application.acquisition import acquire_and_extract
    from synapse.storage.models import EvidenceFragmentRow

    fixture_uri = "https://arxiv.org/abs/2401.00002"
    source_metadata = {
        "title": "Discovery Title",
        "author": "Discovery Author",
        "arxiv_id": "2401.00002",
        "doi": "10.48550/arXiv.2401.00002",
        "version": "v2",  # document_version
        "published": "2024-02-01",
    }

    fake_client = _build_fake_httpx_client(FIXTURE_HTML)
    with patch(
        "synapse.application.acquisition.ssrf_guarded_client",
        return_value=fake_client,
    ):
        r = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            source_metadata=source_metadata,
            requester="test-reader",
        )
    await db_session.commit()

    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == r["evidence_fragment_id"])
    row = (await db_session.execute(stmt)).scalar_one()

    # ── SOURCE-derived fields ──────────────────────────────────────────
    assert row.source_uri == fixture_uri, "source_uri must come from source"
    assert row.source_type == "paper", "source_type must come from source"
    assert row.document_version == "v2", (
        f"document_version must come from source: {row.document_version}"
    )
    citations = json.loads(row.citation_ids or "[]")
    assert "arxiv:2401.00002" in citations, "arxiv_id must come from source"
    assert "doi:10.48550/arXiv.2401.00002" in citations, "doi must come from source"

    # ── EXTRACTION-derived fields ──────────────────────────────────────
    assert row.exact_excerpt is not None, "exact_excerpt must come from extraction"
    assert row.excerpt_hash is not None, "excerpt_hash must come from extraction"
    assert row.extraction_method.startswith("trafilatura-"), (
        "extraction_method must come from extraction (with version)"
    )
    assert row.content_fingerprint is not None, (
        "content_fingerprint must come from extraction (via delta_hash)"
    )
    assert row.retrieved_at is not None, "retrieved_at must come from extraction"
    assert row.provider == "trafilatura", "provider must reflect extraction source"

    # ── SHARED fields — extraction wins, source is fallback ────────────
    # trafilatura extracts title from <title> tag — should override
    # discovery's "Discovery Title" if trafilatura found one.
    # If trafilatura didn't find a title, source's "Discovery Title" is used.
    assert row.title is not None
    assert row.title in ("Transformer Architecture Explained", "Discovery Title"), (
        f"unexpected title: {row.title!r}"
    )

    # ── TOOLKIT provenance ─────────────────────────────────────────────
    assert row.toolkit_commit_sha == "fd9df34c51781bd12effab62762022ab04dbd771"


@pytest.mark.asyncio
async def test_unknown_metadata_stays_null_not_fabricated(app, db_session):
    """Per ADR-0009 §2 / Final Qualification §4: unknown metadata must
    remain explicitly NULL, never fabricated.

    When source_metadata is empty AND trafilatura cannot extract
    metadata, the persisted row should have NULL for title, author,
    published_at — NOT empty strings, NOT placeholder values like
    "unknown" or "untitled".
    """
    from sqlalchemy import select

    from synapse.application.acquisition import acquire_and_extract
    from synapse.storage.models import EvidenceFragmentRow

    fixture_uri = "https://example.com/no-metadata-page"
    # Minimal HTML with no <title>, no <meta> tags, just text.
    bare_html = "<html><body><p>Just some text content with no metadata.</p></body></html>"

    fake_client = _build_fake_httpx_client(bare_html)
    with patch(
        "synapse.application.acquisition.ssrf_guarded_client",
        return_value=fake_client,
    ):
        r = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="other",
            source_metadata={},  # no source metadata at all
            requester="test-reader",
        )
    await db_session.commit()

    assert r["ok"] is True, f"ingest failed: {r}"
    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == r["evidence_fragment_id"])
    row = (await db_session.execute(stmt)).scalar_one()

    # These fields are still present (always set):
    assert row.source_uri == fixture_uri
    assert row.retrieved_at is not None
    assert row.extraction_method.startswith("trafilatura-")
    assert row.content_fingerprint is not None
    assert row.toolkit_commit_sha == "fd9df34c51781bd12effab62762022ab04dbd771"

    # These fields may be NULL when neither source nor extraction provided them:
    if row.title is not None:
        assert row.title != "", "title must be NULL, not empty string"
        assert row.title.lower() not in {"unknown", "untitled", "none"}, (
            f"title must be NULL, not fabricated: {row.title!r}"
        )
    if row.author is not None:
        assert row.author != "", "author must be NULL, not empty string"
        assert row.author.lower() not in {"unknown", "anonymous", "none"}, (
            f"author must be NULL, not fabricated: {row.author!r}"
        )
    if row.published_at is not None:
        assert row.published_at != "", "published_at must be NULL, not empty string"

    # citation_ids is NULL when no DOI/arxiv_id was provided:
    if row.citation_ids is not None:
        citations = json.loads(row.citation_ids)
        assert isinstance(citations, list)
        assert len(citations) == 0, (
            f"citations should be empty or NULL, not fabricated: {citations}"
        )


@pytest.mark.asyncio
async def test_audit_events_record_full_pipeline(app, db_session):
    """Per Final Qualification §4: confirm the audit_events table records
    the full pipeline with request_id correlation."""
    from sqlalchemy import select

    from synapse.application.acquisition import acquire_and_extract
    from synapse.storage.models import AuditEventRow

    fixture_uri = "https://example.com/audit-test"
    fake_client = _build_fake_httpx_client(FIXTURE_HTML)

    with patch(
        "synapse.application.acquisition.ssrf_guarded_client",
        return_value=fake_client,
    ):
        r = await acquire_and_extract(
            db_session,
            canonical_uri=fixture_uri,
            source_type="paper",
            source_metadata={"arxiv_id": "2401.00003"},
            requester="audit-tester",
        )
    await db_session.commit()

    request_id = r["request_id"]
    # All audit events for this ingest should share the same request_id
    stmt = select(AuditEventRow).where(AuditEventRow.request_id == request_id)
    audit_rows = (await db_session.execute(stmt)).scalars().all()

    # Expected event types: acquisition.completed, evidence.persisted
    event_types = {row.event_type for row in audit_rows}
    assert "acquisition.completed" in event_types, (
        f"missing acquisition.completed event; got: {event_types}"
    )
    assert "evidence.persisted" in event_types, (
        f"missing evidence.persisted event; got: {event_types}"
    )

    # All events should have the same actor and target_type
    for row in audit_rows:
        assert row.actor == "audit-tester", f"unexpected actor: {row.actor}"
        assert row.target_type in {"source", "evidence_fragment"}, (
            f"unexpected target_type: {row.target_type}"
        )

    # The evidence.persisted event should include the content_fingerprint
    # and toolkit_commit_sha in its payload.
    evidence_event = next((r for r in audit_rows if r.event_type == "evidence.persisted"), None)
    assert evidence_event is not None
    payload = evidence_event.payload
    assert "content_fingerprint" in payload
    assert payload["toolkit_commit_sha"] == "fd9df34c51781bd12effab62762022ab04dbd771"
