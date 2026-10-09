"""``/api/v1/knowledge`` — entity, claim, relationship, verification endpoints.

Replaces the G01 501 placeholders for:
  - GET  /api/v1/entities
  - GET  /api/v1/entities/{entity_id}
  - GET  /api/v1/relationships
  - GET  /api/v1/relationships/{relationship_id}
  - GET  /api/v1/claims/{claim_id}/evidence
  - POST /api/v1/knowledge/search

All endpoints are read-oriented (GET/POST-search). No mutation endpoints
in G03-T05 — creation happens via the extraction/canonicalization pipeline.

Per the user's G03-T05 authorization: reuse existing G01 API placeholders,
do not expand API surface unnecessarily.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.relationship_service import (
    find_related_entities,
    find_relationships,
    get_relationship_evidence,
)
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
    SourceSpanRow,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

entities_router = APIRouter(prefix="/entities", tags=["entities"])
relationships_router = APIRouter(prefix="/relationships", tags=["relationships"])
claims_router = APIRouter(prefix="/claims", tags=["claims"])


# ── Request models ───────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=256)
    kind: str | None = Field(default=None, max_length=32)


# ── GET /api/v1/entities ──────────────────────────────────────────────────


@entities_router.get("", status_code=status.HTTP_200_OK)
async def list_entities(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
    kind: str | None = None,
    limit: int = 50,
) -> Envelope[dict]:
    """List all entities (paginated, filterable by kind)."""
    stmt = select(EntityRow).order_by(EntityRow.created_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(EntityRow.kind == kind)

    rows = (await session.execute(stmt)).scalars().all()
    return Envelope.success(
        data={
            "items": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "canonical_name": r.canonical_name,
                    "canonical_uri": r.canonical_uri,
                    "aliases": r.aliases,
                    "version": r.version,
                }
                for r in rows
            ],
            "count": len(rows),
        },
        request_id=request_id,
        pagination=PaginationMeta(limit=limit, total=len(rows)),
    )


# ── GET /api/v1/entities/{entity_id} ──────────────────────────────────────


@entities_router.get("/{entity_id}", status_code=status.HTTP_200_OK)
async def get_entity(
    entity_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Get a single entity with its claims, relationships, and evidence spans."""
    stmt = select(EntityRow).where(EntityRow.id == entity_id)
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        raise NotFoundError(f"entity {entity_id!r} not found")

    # Get claims for this entity
    claim_stmt = select(ClaimRow).where(ClaimRow.subject_ref == entity_id)
    claims = (await session.execute(claim_stmt)).scalars().all()

    # Get relationships
    rels_outgoing = await find_related_entities(session, entity_id, direction="outgoing", limit=20)
    rels_incoming = await find_related_entities(session, entity_id, direction="incoming", limit=20)

    # Get source spans for the entity's claims
    claim_ids = [c.id for c in claims]
    span_stmt = (
        select(SourceSpanRow).where(SourceSpanRow.claim_id.in_(claim_ids))
        if claim_ids
        else select(SourceSpanRow).where(False)
    )
    spans = (await session.execute(span_stmt)).scalars().all()

    return Envelope.success(
        data={
            "entity": {
                "id": entity.id,
                "kind": entity.kind,
                "canonical_name": entity.canonical_name,
                "canonical_uri": entity.canonical_uri,
                "aliases": entity.aliases,
                "attributes": entity.attributes,
                "description": entity.description,
                "version": entity.version,
                "created_at": entity.created_at.isoformat() if entity.created_at else None,
            },
            "claims": [
                {
                    "id": c.id,
                    "proposition": c.proposition,
                    "epistemic_state": c.epistemic_state,
                    "evidence_refs": c.evidence_refs,
                    "extraction_method": c.extraction_method,
                }
                for c in claims
            ],
            "relationships": {
                "outgoing": rels_outgoing[:10],
                "incoming": rels_incoming[:10],
            },
            "source_spans": [
                {
                    "id": s.id,
                    "claim_id": s.claim_id,
                    "evidence_fragment_id": s.evidence_fragment_id,
                    "start_offset": s.start_offset,
                    "end_offset": s.end_offset,
                    "excerpt": s.excerpt[:200],
                }
                for s in spans
            ],
        },
        request_id=request_id,
    )


# ── GET /api/v1/relationships ──────────────────────────────────────────────


@relationships_router.get("", status_code=status.HTTP_200_OK)
async def list_relationships(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
    entity_id: str | None = None,
    predicate: str | None = None,
    origin: str | None = None,
    verification_state: str | None = None,
    limit: int = 50,
) -> Envelope[dict]:
    """List relationships (filterable)."""
    rels = await find_relationships(
        session,
        entity_id=entity_id,
        predicate=predicate,
        origin=origin,
        verification_state=verification_state,
        limit=limit,
    )
    return Envelope.success(
        data={"items": rels, "count": len(rels)},
        request_id=request_id,
        pagination=PaginationMeta(limit=limit, total=len(rels)),
    )


# ── GET /api/v1/relationships/{relationship_id} ───────────────────────────


@relationships_router.get("/{relationship_id}", status_code=status.HTTP_200_OK)
async def get_relationship(
    relationship_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Get a single relationship with its evidence."""
    stmt = select(RelationshipRow).where(RelationshipRow.id == relationship_id)
    rel = (await session.execute(stmt)).scalar_one_or_none()
    if rel is None:
        raise NotFoundError(f"relationship {relationship_id!r} not found")

    evidence = await get_relationship_evidence(session, relationship_id)

    return Envelope.success(
        data={
            "id": rel.id,
            "from_entity_id": rel.from_entity_id,
            "to_entity_id": rel.to_entity_id,
            "predicate": rel.predicate,
            "direction": rel.direction,
            "origin": rel.origin,
            "verification_state": rel.verification_state,
            "evidence_refs": rel.evidence_refs,
            "conditions": rel.conditions,
            "version": rel.version,
            "evidence": evidence,
        },
        request_id=request_id,
    )


# ── GET /api/v1/claims/{claim_id}/evidence ────────────────────────────────


@claims_router.get("/{claim_id}/evidence", status_code=status.HTTP_200_OK)
async def get_claim_evidence(
    claim_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Get evidence fragments and source spans for a claim."""
    claim_stmt = select(ClaimRow).where(ClaimRow.id == claim_id)
    claim = (await session.execute(claim_stmt)).scalar_one_or_none()
    if claim is None:
        raise NotFoundError(f"claim {claim_id!r} not found")

    import contextlib
    import json

    evidence_refs: list[str] = []
    if claim.evidence_refs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            evidence_refs = json.loads(claim.evidence_refs)

    # Fetch evidence fragments
    ef_stmt = (
        select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(evidence_refs))
        if evidence_refs
        else select(EvidenceFragmentRow).where(False)
    )
    fragments = (await session.execute(ef_stmt)).scalars().all()

    # Fetch source spans for this claim
    span_stmt = select(SourceSpanRow).where(SourceSpanRow.claim_id == claim_id)
    spans = (await session.execute(span_stmt)).scalars().all()

    # Get latest verification assessment
    assess_stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.target_id == claim_id,
            AuditEventRow.target_type == "claim",
            AuditEventRow.event_type == "verification.assessed",
        )
        .order_by(AuditEventRow.created_at.desc())
        .limit(1)
    )
    assessment_row = (await session.execute(assess_stmt)).scalar_one_or_none()
    assessment = assessment_row.payload if assessment_row else None

    return Envelope.success(
        data={
            "claim": {
                "id": claim.id,
                "proposition": claim.proposition,
                "epistemic_state": claim.epistemic_state,
                "extraction_method": claim.extraction_method,
            },
            "evidence_fragments": [
                {
                    "id": ef.id,
                    "source_uri": ef.source_uri,
                    "exact_excerpt": (ef.exact_excerpt or "")[:500],
                    "extraction_method": ef.extraction_method,
                    "retrieved_at": ef.retrieved_at,
                    "content_fingerprint": ef.content_fingerprint,
                }
                for ef in fragments
            ],
            "source_spans": [
                {
                    "id": s.id,
                    "start_offset": s.start_offset,
                    "end_offset": s.end_offset,
                    "excerpt": s.excerpt[:200],
                    "context_before": s.context_before,
                    "context_after": s.context_after,
                }
                for s in spans
            ],
            "verification_assessment": assessment,
        },
        request_id=request_id,
    )


# ── POST /api/v1/knowledge/search ─────────────────────────────────────────


@router.post("/search", status_code=status.HTTP_200_OK)
async def search_knowledge(
    body: SearchRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Search entities by name or alias."""
    import contextlib
    import json

    stmt = select(EntityRow).where(EntityRow.canonical_name.ilike(f"%{body.query}%"))
    if body.kind:
        stmt = stmt.where(EntityRow.kind == body.kind)
    stmt = stmt.limit(50)

    rows = (await session.execute(stmt)).scalars().all()

    # Also search aliases (stored as JSON array in the aliases column)
    alias_matches: list[EntityRow] = []
    all_stmt = select(EntityRow)
    if body.kind:
        all_stmt = all_stmt.where(EntityRow.kind == body.kind)
    all_rows = (await session.execute(all_stmt)).scalars().all()
    for r in all_rows:
        if r.aliases:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                aliases = json.loads(r.aliases)
                if any(body.query.lower() in a.lower() for a in aliases):
                    if r not in rows:
                        alias_matches.append(r)

    results = list(rows) + alias_matches

    return Envelope.success(
        data={
            "items": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "canonical_name": r.canonical_name,
                    "canonical_uri": r.canonical_uri,
                }
                for r in results
            ],
            "count": len(results),
            "query": body.query,
        },
        request_id=request_id,
        pagination=PaginationMeta(limit=50, total=len(results)),
    )
