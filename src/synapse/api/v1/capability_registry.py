"""``/api/v1/knowledge/capabilities`` -- G04-T02 Capability Registry API.

Per the user's G04-T02 mission briefing §7:

  "Provide the smallest useful read-oriented interface."

This file exposes three read-only endpoints over the existing knowledge
graph (no new database, no new migrations):

  - GET  /api/v1/knowledge/capabilities
         List documented capabilities (EntityRow kind=capability).
  - GET  /api/v1/knowledge/capabilities/{capability_id}
         Get full detail for one capability (providers, dependencies,
         limitations, claims, evidence bundles).
  - POST /api/v1/knowledge/capabilities/analyze-gap
         Analyze capability coverage against a list of required
         capabilities. Returns one of six classifications per
         requirement (SUPPORTED, PARTIALLY_SUPPORTED, NOT_EVIDENCED,
         CONTESTED, CONSTRAINED, UNKNOWN).

These endpoints are READ-ONLY. They do not create, modify, or delete
any knowledge. All mutations happen through the existing G02/G03
acquisition + extraction + canonicalization pipeline.

The endpoints are namespaced under /knowledge to avoid collision with
the G01 /api/v1/capabilities endpoint (which lists Synapse SYSTEM
capabilities from the G01 CapabilityRow table -- a different concept).
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.capability_registry import (
    DEFAULT_LIMIT as CAP_DEFAULT_LIMIT,
)
from synapse.application.capability_registry import (
    get_capability,
    list_capabilities,
)
from synapse.application.gap_analyzer import (
    DEFAULT_LIMIT as GAP_DEFAULT_LIMIT,
)
from synapse.application.gap_analyzer import (
    MAX_LIMIT as GAP_MAX_LIMIT,
)
from synapse.application.gap_analyzer import (
    MAX_REQUIRED_CAPABILITIES,
    analyze_gap,
)

router = APIRouter(prefix="/knowledge/capabilities", tags=["capability-registry"])


# ── Request / response models ────────────────────────────────────────────────


class AnalyzeGapRequest(BaseModel):
    """Request body for ``POST /api/v1/knowledge/capabilities/analyze-gap``."""

    required_capabilities: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_REQUIRED_CAPABILITIES,
        description="Required capability names to evaluate. Each name is "
        "resolved against the knowledge graph by exact match, alias match, "
        "or fuzzy ILIKE match (in that order).",
    )
    context: str | None = Field(
        default=None,
        max_length=512,
        description="Optional technical context (e.g., 'AI agent systems in Python'). "
        "Used for applicability matching against claim validity_conditions.",
    )
    candidate_entity_ids: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Optional list of entity IDs to restrict the analysis to "
        "(e.g., the candidate technology to evaluate). If None, all entities "
        "are candidates.",
    )
    limit: int = Field(
        default=GAP_DEFAULT_LIMIT,
        ge=1,
        le=GAP_MAX_LIMIT,
        description="Max number of requirements to evaluate.",
    )


# ── GET /api/v1/knowledge/capabilities ─────────────────────────────────────


@router.get("", status_code=status.HTTP_200_OK)
async def list_capabilities_endpoint(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
    name_contains: str | None = None,
    limit: int = CAP_DEFAULT_LIMIT,
) -> Envelope[dict]:
    """List documented capabilities in the knowledge graph.

    Returns ``EntityRow(kind="capability")`` records with a lightweight
    summary (provider count, limitation flag). For full detail
    (providers, dependencies, evidence), use
    ``GET /api/v1/knowledge/capabilities/{capability_id}``.
    """
    result = await list_capabilities(
        session,
        name_contains=name_contains,
        limit=limit,
    )
    return Envelope.success(
        data=result,
        request_id=request_id,
        pagination=PaginationMeta(limit=limit, total=result["count"]),
    )


# ── GET /api/v1/knowledge/capabilities/{capability_id} ──────────────────────


@router.get("/{capability_id}", status_code=status.HTTP_200_OK)
async def get_capability_endpoint(
    capability_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Get full detail for a single capability.

    Returns the capability entity plus its providers (entities with
    PROVIDES / ENABLES / PRODUCES edges), the providers' dependencies
    (REQUIRES / DEPENDS_ON), the capability's limitations (LIMITS /
    CONTRADICTS / INVALIDATES), and the assessed claims with their
    evidence bundles.
    """
    detail = await get_capability(session, capability_id)
    if detail is None:
        raise NotFoundError(f"capability {capability_id!r} not found")
    return Envelope.success(
        data=detail,
        request_id=request_id,
    )


# ── POST /api/v1/knowledge/capabilities/analyze-gap ────────────────────────


@router.post("/analyze-gap", status_code=status.HTTP_200_OK)
async def analyze_gap_endpoint(
    body: AnalyzeGapRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Analyze capability coverage against a list of required capabilities.

    For each required capability, returns one of six classifications:
    SUPPORTED, PARTIALLY_SUPPORTED, NOT_EVIDENCED, CONTESTED,
    CONSTRAINED, UNKNOWN.

    Lexical similarity alone is NOT sufficient for SUPPORTED. A capability
    name that fuzzy-matches an entity is recorded with
    ``match_quality="fuzzy"`` but is NOT promoted to SUPPORTED without
    assessed evidence.

    Contradictions are preserved (CONTESTED), not suppressed. Missing
    capabilities are reported as NOT_EVIDENCED, not as proven absent.
    """
    result = await analyze_gap(
        session,
        body.required_capabilities,
        context=body.context,
        candidate_entity_ids=body.candidate_entity_ids,
        limit=body.limit,
        requester=str(principal),
    )
    return Envelope.success(
        data=result,
        request_id=request_id,
        pagination=PaginationMeta(limit=body.limit, total=len(result.get("requirements", []))),
    )
