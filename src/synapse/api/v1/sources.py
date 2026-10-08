"""``/api/v1/sources`` — discovery + ingest endpoints.

Replaces the G01 501 placeholders for:
  - POST /sources/discover
  - POST /sources/ingest
  - GET  /sources/{source_id}
  - GET  /sources/{source_id}/acquisitions

Per the user's G02 authorization: implements the minimal end-to-end
slice (arxiv_search + httpx + trafilatura + content_delta_hash) behind
the existing G01 API contract. Reuses PrincipalDep, DbSessionDep,
RequestIDDep, and the existing error envelope.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import DomainError, NotFoundError
from synapse.api.responses import Envelope
from synapse.application.acquisition import acquire_and_extract, discover_sources
from synapse.storage.models import AuditEventRow, EvidenceFragmentRow, SourceRow

router = APIRouter(prefix="/sources", tags=["sources"])


# ── Request / response models ─────────────────────────────────────────────────


class DiscoverRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)
    max_results: int = Field(default=5, ge=1, le=20)
    author: str | None = Field(default=None, max_length=256)
    category: str | None = Field(default=None, max_length=64)


class IngestRequest(BaseModel):
    canonical_uri: str = Field(..., min_length=1, max_length=2048)
    source_type: str = Field(default="paper", max_length=32)
    provider: str = Field(default="httpx", max_length=64)
    # Optional metadata from the discovery step (arxiv_id, doi, title, etc.)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── POST /sources/discover ──────────────────────────────────────────────────


@router.post("/discover", status_code=status.HTTP_200_OK)
async def discover(
    body: DiscoverRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Discover candidate Sources via the arxiv_search provider.

    Live network call to https://export.arxiv.org/api/query — bounded
    by ``max_results`` (1-20). Rate-limited by the in-process rate
    limiter (G01 SimpleRateLimitMiddleware).
    """
    r = await discover_sources(
        query=body.query,
        max_results=body.max_results,
        author=body.author,
        category=body.category,
    )
    if "error" in r:
        return Envelope.failure(
            error=__import__("synapse.api.errors", fromlist=["DomainError"]).DomainError(
                r["error"]
            ),
            request_id=request_id,
        )
    return Envelope.success(data=r, request_id=request_id)


# ── POST /sources/ingest ────────────────────────────────────────────────────


@router.post("/ingest", status_code=status.HTTP_200_OK)
async def ingest(
    body: IngestRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Acquire + extract + persist a single Source end-to-end.

    Synchronous (not 202+job) because the minimal slice is small enough
    to complete within one HTTP request. A future group can convert
    this to the 202+job pattern per Master Spec §15.
    """
    r = await acquire_and_extract(
        session,
        canonical_uri=body.canonical_uri,
        source_type=body.source_type,
        provider_name=body.provider,
        source_metadata=body.metadata,
        requester=principal.subject,
    )
    if not r.get("ok"):
        return Envelope.failure(
            error=DomainError(r.get("error", "ingest failed")),
            request_id=request_id,
        )
    return Envelope.success(data=r, request_id=request_id)


# ── GET /sources ─────────────────────────────────────────────────────────────


@router.get("", status_code=status.HTTP_200_OK)
async def list_sources(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """List all Sources (most recent first)."""
    stmt = select(SourceRow).order_by(SourceRow.created_at.desc()).limit(50)
    rows = (await session.execute(stmt)).scalars().all()
    return Envelope.success(
        data={
            "items": [
                {
                    "id": r.id,
                    "canonical_uri": r.canonical_uri,
                    "source_type": r.source_type,
                    "publisher": r.publisher,
                    "author": r.author,
                    "license": r.license,
                    "status": r.status,
                    "version": r.version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "count": len(rows),
        },
        request_id=request_id,
    )


# ── GET /sources/{source_id} ─────────────────────────────────────────────────


@router.get("/{source_id}", status_code=status.HTTP_200_OK)
async def get_source(
    source_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Get a single Source by ID."""
    stmt = select(SourceRow).where(SourceRow.id == source_id)
    r = (await session.execute(stmt)).scalar_one_or_none()
    if r is None:
        raise NotFoundError(f"source {source_id!r} not found")
    return Envelope.success(
        data={
            "id": r.id,
            "canonical_uri": r.canonical_uri,
            "source_type": r.source_type,
            "publisher": r.publisher,
            "author": r.author,
            "license": r.license,
            "status": r.status,
            "version": r.version,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        },
        request_id=request_id,
    )


# ── GET /sources/{source_id}/acquisitions ─────────────────────────────────────


@router.get("/{source_id}/acquisitions", status_code=status.HTTP_200_OK)
async def list_acquisitions(
    source_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """List Acquisition events for a Source (read from audit_events).

    Per the G02 minimal slice: acquisitions are stored as
    ``event_type='acquisition.*'`` rows in ``audit_events`` (the G01
    schema does not have an ``acquisitions`` table; this is the
    least-invasive reuse of existing contracts per ADR-0009 §4).
    """
    stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.target_id == source_id,
            AuditEventRow.target_type == "source",
            AuditEventRow.event_type.like("acquisition.%"),
        )
        .order_by(AuditEventRow.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    # Also fetch any evidence_fragments for this source
    ev_stmt = (
        select(EvidenceFragmentRow)
        .where(EvidenceFragmentRow.source_id == source_id)
        .order_by(EvidenceFragmentRow.created_at.desc())
    )
    ev_rows = (await session.execute(ev_stmt)).scalars().all()

    if not rows and not ev_rows:
        raise NotFoundError(f"no acquisitions or evidence found for source {source_id!r}")

    return Envelope.success(
        data={
            "source_id": source_id,
            "acquisitions": [r.payload for r in rows],
            "evidence_fragments": [
                {
                    "id": e.id,
                    "acquisition_id": e.acquisition_id,
                    "title": e.title,
                    "author": e.author,
                    "published_at": e.published_at,
                    "retrieved_at": e.retrieved_at,
                    "extraction_method": e.extraction_method,
                    "content_fingerprint": e.content_fingerprint,
                    "excerpt_hash": e.excerpt_hash,
                    "toolkit_commit_sha": e.toolkit_commit_sha,
                    "citation_ids": e.citation_ids,
                    "exact_excerpt": (e.exact_excerpt or "")[:500],
                }
                for e in ev_rows
            ],
        },
        request_id=request_id,
    )
