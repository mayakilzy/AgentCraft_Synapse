"""``POST /api/v1/innovations/generate`` -- G05-T03 Innovation Generation API.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§5.1): this endpoint activates the G04 501 placeholder for
`POST /api/v1/innovations/generate` with a real implementation that
generates evidence-grounded innovation concepts.

The endpoint is READ-ONLY with respect to established knowledge: it
creates innovation entities and hypothesized relationships (origin=
"hypothesized") which are epistemically isolated from ordinary retrieval.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.innovation import (
    MAX_CONCEPTS,
    MAX_QUERY_CHARS,
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
        description="The problem or need to address. The system generates "
        "innovation concepts that combine existing tools, capabilities, "
        "and techniques to address this problem.",
    )
    context: str | None = Field(
        default=None,
        max_length=512,
        description="Optional technical context (e.g., 'AI agent systems in "
        "Python'). Used for applicability matching against claim "
        "validity_conditions.",
    )
    candidate_entity_ids: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Optional list of entity IDs to scope the innovation "
        "generation to. If None, all entities are candidates.",
    )
    max_concepts: int = Field(
        default=MAX_CONCEPTS,
        ge=1,
        le=MAX_CONCEPTS,
        description="Max number of concepts to return (capped at 10).",
    )


# ── POST /api/v1/innovations/generate ──────────────────────────────────────


@router.post("/generate", status_code=status.HTTP_200_OK)
async def generate_innovation_concepts(
    body: InnovationRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Generate evidence-grounded innovation concepts.

    Converts T01 combinations and T02 opportunities into distinct,
    technically plausible application concepts. Each concept has:
    - purpose, target users, components with roles
    - integration_mechanism (how components work together)
    - potential_benefit (hypothesis, not verified claim)
    - uncertainties (>=1 required)
    - evidence_refs (validated against existing fragments)
    - hypothesis_id (points to a ClaimRow with epistemic_state="hypothesized")

    Six deterministic templates are used as baselines (composition,
    substitution, constraint-relaxation, gap-filling, recombination,
    analogy). The optional model-assisted extension point is available
    but not mandatory — without an LLM adapter, the deterministic path runs.

    Quality gates enforced:
    - Zero fabricated component IDs or evidence refs
    - Every concept has >=1 uncertainty
    - Every concept has integration_mechanism + potential_benefit
    - No unsupported numerical business claims
    - No market novelty claims
    - No automatic VERIFIED promotion
    - Hypothesized relationships are epistemically isolated
    """
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
