"""``POST /api/v1/knowledge/retrieve`` -- minimal read-oriented retrieval API.

Per the user's G04-T01 mission briefing §7.8:
    "Provide a minimal read-oriented retrieval interface. Reuse existing
     API routes where practical. Avoid adding multiple overlapping
     endpoints."

This is a single new endpoint that exposes ``hybrid_retrieve()``.
It does NOT overlap with ``POST /api/v1/knowledge/search`` (which only
does entity-name lookup). The retrieve endpoint returns full evidence
bundles + citation chains + ranking breakdowns.

The endpoint is read-only (POST with a JSON body, but no mutation). It
returns structured results that include everything the mission briefing
requires:

  - Matched entity or claim
  - Relevant relationships
  - Evidence references
  - Source spans
  - Original source URI
  - Verification assessment
  - Ranking score / ordering explanation
  - Match explanation (per-result reranking_factors)

It does NOT generate speculative technical conclusions (that is G04-T03).
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.retrieval import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_DEPTH,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    hybrid_retrieve,
)

router = APIRouter(prefix="/knowledge", tags=["retrieval"])


class RetrieveRequest(BaseModel):
    """Request body for ``POST /api/v1/knowledge/retrieve``."""

    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    entity_kind: str | None = Field(default=None, max_length=32)
    predicate: str | None = Field(default=None, max_length=64)
    epistemic_state: str | None = Field(default=None, max_length=32)
    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1, le=5)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


@router.post("/retrieve", status_code=status.HTTP_200_OK)
async def retrieve_knowledge(
    body: RetrieveRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Hybrid retrieval over the G01-G03 knowledge graph.

    Combines lexical, structured, graph-assisted, and evidence-aware
    retrieval. Returns claims + relationships + entities, each with its
    evidence bundle and reranking breakdown. Unknowns are reported
    explicitly -- never silently fabricated.
    """
    result = await hybrid_retrieve(
        session,
        body.query,
        entity_kind=body.entity_kind,
        predicate=body.predicate,
        epistemic_state=body.epistemic_state,
        max_depth=body.max_depth,
        limit=body.limit,
        requester=str(principal),
    )
    return Envelope.success(
        data=result,
        request_id=request_id,
        pagination=PaginationMeta(limit=body.limit, total=len(result.get("claims", []))),
    )
