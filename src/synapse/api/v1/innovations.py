"""``POST /api/v1/innovations/generate`` + ``POST /api/v1/innovations/{id}/critique``
-- G05-T03 + G05-T04 Innovation API.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§5.1): these endpoints activate the G04 501 placeholders for innovation
generation and critique.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.innovation import (
    MAX_CONCEPTS,
    MAX_QUERY_CHARS,
    critique_innovation,
    generate_innovations,
)

router = APIRouter(prefix="/innovations", tags=["innovations"])


# ── Request / response models ────────────────────────────────────────────────


class InnovationRequest(BaseModel):
    """Request body for ``POST /api/v1/innovations/generate``."""

    problem_domain: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="The problem or need to address.",
    )
    context: str | None = Field(default=None, max_length=512)
    candidate_entity_ids: list[str] | None = Field(default=None, max_length=20)
    max_concepts: int = Field(default=MAX_CONCEPTS, ge=1, le=MAX_CONCEPTS)


class CritiqueRequest(BaseModel):
    """Request body for ``POST /api/v1/innovations/{id}/critique``."""

    context: str | None = Field(
        default=None,
        max_length=512,
        description="Optional technical context for applicability matching.",
    )


# ── POST /api/v1/innovations/generate ──────────────────────────────────────


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_innovation_concepts(
    body: InnovationRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Generate evidence-grounded innovation concepts."""
    result = await generate_innovations(
        session,
        body.problem_domain,
        context=body.context,
        candidate_entity_ids=body.candidate_entity_ids,
        max_concepts=body.max_concepts,
        requester=str(principal),
    )
    return Envelope.success(
        data=result,
        request_id=request_id,
        pagination=PaginationMeta(
            limit=body.max_concepts,
            total=len(result.get("concepts", [])),
        ),
    )


# ── POST /api/v1/innovations/{id}/critique ────────────────────────────────


@router.post("/{innovation_id}/critique", status_code=status.HTTP_200_OK)
async def critique_innovation_concept(
    innovation_id: str,
    body: CritiqueRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Critique an existing persisted innovation concept.

    Evaluates technical feasibility, evidence coverage, dependency
    completeness, constraint conflicts, applicable contradictions,
    technical alternatives, failure modes, and structural uniqueness.

    The concept must already be persisted (via POST /innovations/generate).
    This endpoint does NOT regenerate the concept - it critiques the
    existing one by its stable ID.

    Returns a structured critique with:
    - feasibility_score + rationale
    - evidence_coverage + rationale
    - constraint_conflicts[] (LIMITS/CONTRADICTS edges)
    - failure_modes[] (CONTESTED claims, missing dependencies)
    - novelty_score + novelty_caveat (graph uniqueness != market novelty)
    - alternatives[] (REPLACES edges)
    - architecture composition (components, dependency_graph, integration_points)
    - overall_recommendation (testable / testable_with_caveats / needs_more_evidence / rejected)

    Quality gates:
    - Every architecture component references an existing entity
    - Proposed integration links remain explicitly hypothetical
    - No fabricated citations
    - Applicable contradictions remain visible
    - Missing evidence is not treated as proof of impossibility
    - Feasibility and evidence-coverage scores have explainable computation
    - Structural novelty is explicitly caveated
    - Every critique contains failure modes or a truthful statement
    - Stable innovation and hypothesis IDs are preserved
    - No automatic promotion to VERIFIED
    """
    result = await critique_innovation(
        session,
        innovation_id,
        context=body.context,
        requester=str(principal),
    )
    if result is None:
        raise NotFoundError(f"innovation {innovation_id!r} not found")
    return Envelope.success(
        data=result,
        request_id=request_id,
    )
