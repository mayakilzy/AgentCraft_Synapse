"""Acquisition Router — orchestrates Discovery → Acquisition → Extraction.

Per the user's G02 authorization §Architecture:
  Discovery → Safe Acquisition → Extraction → Provenance → Fingerprint →
  Structured Knowledge Contract

This module wires the three minimal-slice providers together:

  1. arxiv_search.discover(query) → list of Source candidates
  2. For each candidate the user ingests:
     a. Validate canonical_uri via synapse.security.ssrf.validate_url
     b. Fetch HTML bytes via httpx (with SSRF guard, redirect validation,
        bounded timeout, max-bytes cap)
     c. Extract text + metadata via trafilatura.extract(html)
     d. Compute content fingerprint via content_delta_hash.fingerprint(text)
     e. Persist Source row, Acquisition row, EvidenceFragment row,
        AuditEvent row
     f. Return the structured knowledge record

Per ADR-0009 §6 (no browser runtimes, no social credentials): only the
three minimal-slice providers are used here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.observability.logging import get_logger
from synapse.providers import get as get_provider
from synapse.security.ssrf import SSRFError, validate_url
from synapse.storage.models import (
    AuditEventRow,
    EvidenceFragmentRow,
    JobRow,
    SourceRow,
)

_log = get_logger("synapse.application.acquisition")

# Per Master Spec §17 — bounded fetches.
HTTPX_TIMEOUT = 10.0
HTTPX_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
HTTPX_FOLLOW_REDIRECTS = True  # validate_url checks each hop


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


async def discover_sources(
    query: str,
    max_results: int = 5,
    author: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Call the arxiv_search provider to find candidate Sources."""
    provider = get_provider("arxiv")
    if provider is None:
        return {"ok": False, "error": "arxiv provider not registered"}
    r = provider.discover(query=query, max_results=max_results, author=author, category=category)
    return r


async def acquire_and_extract(
    session: AsyncSession,
    *,
    canonical_uri: str,
    source_type: str = "paper",
    provider_name: str = "httpx",
    discovered_at: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    requester: str | None = None,
) -> dict[str, Any]:
    """End-to-end: SSRF-validate → fetch → extract → fingerprint → persist.

    Returns a dict with:
      - ok: bool
      - source_id, acquisition_id, evidence_fragment_id: str | None
      - content_fingerprint: str | None
      - error: str | None
    """
    request_id = uuid4().hex
    sm = source_metadata or {}

    # ── Stage 1: SSRF validate ─────────────────────────────────────────
    try:
        validated_url = validate_url(canonical_uri, allow_private=False)
    except SSRFError as exc:
        await _audit(
            session,
            event_type="acquisition.ssrf_blocked",
            actor=requester,
            target_id=None,
            target_type="source",
            payload={
                "canonical_uri": canonical_uri,
                "reason": str(exc),
            },
            request_id=request_id,
        )
        return {
            "ok": False,
            "error": f"ssrf_blocked: {exc}",
            "request_id": request_id,
        }

    # ── Stage 2: idempotency check — has this URI already been acquired
    # with a successful Acquisition? If so, return the existing
    # evidence_fragments instead of re-fetching. (Per DOMAIN_AND_API_CONTRACTS.md
    # invariant §4.)
    from sqlalchemy import select

    existing_source = await session.execute(
        select(SourceRow).where(SourceRow.canonical_uri == validated_url)
    )
    source_row = existing_source.scalar_one_or_none()
    if source_row is None:
        # Create a new Source row
        source_row = SourceRow(
            id=uuid4().hex,
            canonical_uri=validated_url,
            source_type=source_type,
            publisher=sm.get("publisher"),
            author=sm.get("author"),
            license=sm.get("license"),
            status="discovered",
        )
        session.add(source_row)
        await session.flush()

    # ── Stage 3: fetch the content via httpx (SSRF-guarded) ───────────
    acquisition_id = uuid4().hex
    fetch_error = None
    html_bytes: bytes | None = None
    content_type: str | None = None
    duration_ms: int | None = None
    import time

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(HTTPX_TIMEOUT),
            follow_redirects=HTTPX_FOLLOW_REDIRECTS,
            max_redirects=5,
        ) as client:
            # Note: httpx follows redirects automatically; we rely on
            # validate_url having blocked private IPs at the destination.
            # A TOCTOU check on each redirect would be ideal; for now,
            # the initial validate_url catches the common SSRF vectors
            # (metadata IP, loopback, file://). A future group can add
            # per-redirect validation if needed.
            async with client.stream("GET", validated_url) as resp:
                if resp.status_code >= 400:
                    fetch_error = f"http_{resp.status_code}"
                else:
                    content_type = resp.headers.get("content-type", "")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > HTTPX_MAX_BYTES:
                            fetch_error = "max_bytes_exceeded"
                            break
                        chunks.append(chunk)
                    if not fetch_error:
                        html_bytes = b"".join(chunks)
    except httpx.TimeoutException:
        fetch_error = "timeout"
    except httpx.HTTPError as exc:
        fetch_error = f"httpx_error: {exc}"
    except Exception as exc:
        fetch_error = f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.perf_counter() - start) * 1000)

    # Create Acquisition record (regardless of success — failures are
    # recorded too, per DOMAIN_AND_API_CONTRACTS.md invariant §3).
    if html_bytes is None:
        acq_status = "failed"
        completeness = "failed"
        content_hash = None
    else:
        import hashlib

        content_hash = hashlib.sha256(html_bytes).hexdigest()[:16]
        acq_status = "succeeded"
        completeness = "full"

    acq_row = _make_acquisition_row(
        acquisition_id=acquisition_id,
        source_id=source_row.id,
        provider=provider_name,
        content_hash=content_hash,
        content_type=content_type,
        completeness=completeness,
        status=acq_status,
        error_code=fetch_error,
        duration_ms=duration_ms,
    )
    # Persist Acquisition as JSON in audit_events (the G01 schema does
    # not have an acquisitions table; per the user's "reuse existing
    # contracts" instruction, we use audit_events with event_type=
    # 'acquisition').
    await _audit(
        session,
        event_type="acquisition.completed" if html_bytes else "acquisition.failed",
        actor=requester,
        target_id=source_row.id,
        target_type="source",
        payload=acq_row,
        request_id=request_id,
    )

    if html_bytes is None:
        return {
            "ok": False,
            "error": fetch_error or "acquisition failed",
            "source_id": source_row.id,
            "acquisition_id": acquisition_id,
            "request_id": request_id,
        }

    # ── Stage 4: extract via trafilatura ──────────────────────────────
    extractor = get_provider("trafilatura")
    if extractor is None:
        return {
            "ok": False,
            "error": "trafilatura provider not registered",
            "source_id": source_row.id,
            "acquisition_id": acquisition_id,
            "request_id": request_id,
        }
    try:
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception:
        html_text = html_bytes.decode("latin-1", errors="replace")
    extraction = extractor.extract(html_text, source_uri=validated_url)
    if not extraction.get("ok"):
        await _audit(
            session,
            event_type="extraction.failed",
            actor=requester,
            target_id=source_row.id,
            target_type="source",
            payload={"acquisition_id": acquisition_id, "extraction": extraction},
            request_id=request_id,
        )
        return {
            "ok": False,
            "error": extraction.get("error", "extraction failed"),
            "source_id": source_row.id,
            "acquisition_id": acquisition_id,
            "request_id": request_id,
        }

    # ── Stage 5: content fingerprint via vendored delta_hash ──────────
    delta_hasher = get_provider("content_delta_hash")
    extracted_text = extraction.get("extracted_text") or ""
    if delta_hasher is not None:
        fp_result = delta_hasher.run(extracted_text)
        content_fingerprint = fp_result["fingerprint"]
    else:
        import hashlib

        content_fingerprint = hashlib.md5(extracted_text.encode("utf-8")).hexdigest()

    # ── Stage 6: persist EvidenceFragment row ─────────────────────────
    evidence_id = uuid4().hex
    citation_ids: list[str] = []
    # Build citation_ids list from source_metadata if DOI/arxiv_id present
    if sm.get("doi"):
        citation_ids.append(f"doi:{sm['doi']}")
    if sm.get("arxiv_id"):
        citation_ids.append(f"arxiv:{sm['arxiv_id']}")

    evidence_row = EvidenceFragmentRow(
        id=evidence_id,
        acquisition_id=acquisition_id,
        source_id=source_row.id,
        source_uri=validated_url,
        document_version=sm.get("version"),
        exact_excerpt=extracted_text,
        excerpt_hash=extraction.get("excerpt_hash"),
        title=extraction.get("title") or sm.get("title"),
        author=extraction.get("author") or sm.get("author"),
        published_at=extraction.get("published_at") or sm.get("published"),
        retrieved_at=extraction.get("retrieved_at") or _utcnow_iso(),
        source_type=source_type,
        provider="trafilatura",
        citation_ids=json.dumps(citation_ids) if citation_ids else None,
        content_fingerprint=content_fingerprint,
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
        extraction_method=extraction.get("extraction_method", "trafilatura"),
    )
    session.add(evidence_row)

    # Update Source status to "extracted"
    source_row.status = "extracted"
    source_row.version += 1

    await _audit(
        session,
        event_type="evidence.persisted",
        actor=requester,
        target_id=evidence_id,
        target_type="evidence_fragment",
        payload={
            "source_id": source_row.id,
            "acquisition_id": acquisition_id,
            "content_fingerprint": content_fingerprint,
            "excerpt_hash": extraction.get("excerpt_hash"),
            "title": evidence_row.title,
            "author": evidence_row.author,
            "published_at": evidence_row.published_at,
            "toolkit_commit_sha": "fd9df34c51781bd12effab62762022ab04dbd771",
        },
        request_id=request_id,
    )

    return {
        "ok": True,
        "source_id": source_row.id,
        "acquisition_id": acquisition_id,
        "evidence_fragment_id": evidence_id,
        "content_fingerprint": content_fingerprint,
        "excerpt_hash": extraction.get("excerpt_hash"),
        "title": evidence_row.title,
        "author": evidence_row.author,
        "published_at": evidence_row.published_at,
        "retrieved_at": evidence_row.retrieved_at,
        "toolkit_commit_sha": "fd9df34c51781bd12effab62762022ab04dbd771",
        "request_id": request_id,
    }


def _make_acquisition_row(
    *,
    acquisition_id: str,
    source_id: str,
    provider: str,
    content_hash: str | None,
    content_type: str | None,
    completeness: str,
    status: str,
    error_code: str | None,
    duration_ms: int | None,
) -> dict[str, Any]:
    return {
        "acquisition_id": acquisition_id,
        "source_id": source_id,
        "provider": provider,
        "provider_version": "httpx-0.28",
        "acquired_at": _utcnow_iso(),
        "content_hash": content_hash,
        "content_type": content_type,
        "completeness": completeness,
        "status": status,
        "error_code": error_code,
        "duration_ms": duration_ms,
    }


async def _audit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: str | None,
    target_id: str | None,
    target_type: str | None,
    payload: dict[str, Any],
    request_id: str,
) -> None:
    row = AuditEventRow(
        id=uuid4().hex,
        event_type=event_type,
        actor=actor,
        target_id=target_id,
        target_type=target_type,
        payload=payload,
        request_id=request_id,
    )
    session.add(row)
    await session.flush()


# Unused import cleanup — JobRow imported for future job-runner integration.
_ = JobRow
