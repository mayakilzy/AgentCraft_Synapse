"""``POST /api/v1/innovations/generate`` + ``POST /api/v1/innovations/{id}/critique``
-- G05-T03 + G05-T04 Innovation API.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§5.1): these endpoints activate the G04 501 placeholders for innovation
generation and critique.

G07-PRE-02 (read-only amendments): two new GET endpoints were added
at the bottom of this router:

- ``GET /api/v1/innovations`` — paginated list of persisted innovation
  concepts (filtered to genuine concepts via the
  ``EntityFingerprintRow`` discriminator on kind='project').
- ``GET /api/v1/innovations/{innovation_id}`` — read a persisted
  concept detail by stable ID.

These GETs are read-only: they emit no ``AuditEventRow``, mutate no
attributes, and never call ``critique_innovation`` or
``generate_innovations``. Route ordering is safe: FastAPI matches by
HTTP method, so the new GETs do not conflict with the existing POSTs.
"""

from __future__ import annotations

import contextlib
import json

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.innovation import (
    MAX_CONCEPTS,
    MAX_QUERY_CHARS,
    _load_persisted_concept,
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


# ── G07-PRE-02 — read-only list + detail ───────────────────────────────────
#
# These GETs were added by the G07-PRE-02 mission. They are read-only
# (no AuditEventRow, no attribute mutation, no critique/generation
# call). They reuse the existing ``_load_persisted_concept`` helper and
# the new ``_list_pagination`` module.


@router.get("", status_code=status.HTTP_200_OK)
async def list_innovations(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
    cursor: str | None = None,
    limit: int = 20,
) -> Envelope[dict]:
    """List persisted innovation concepts, newest first.

    Returns a paginated list of innovation concepts filtered to genuine
    persisted concepts via the ``EntityFingerprintRow`` discriminator
    (kind='project'). Each item carries the lightweight entity row plus
    the persisted attributes (which include ``problem_domain``,
    ``context``, ``integration_mechanism``, ``potential_benefit``,
    ``uncertainties``, ``concept_fingerprint``, ``template_name``).

    This endpoint does NOT call ``critique_innovation`` or
    ``generate_innovations``. It does NOT emit any audit event.

    Pagination:
    - ``limit``: 1..100 (default 20).
    - ``cursor``: opaque base64url token; the previous page's
      ``meta.pagination.next_cursor`` value, or ``None`` for the first
      page.
    - ``next_cursor``: populated iff more results exist after this
      page; ``None`` if this is the last page.
    - Cursor malformed/stale/mismatched → 422 ``validation_error``
      (for malformed) or empty page (for stale — the rows referenced
      by the cursor no longer exist).

    For full payload (hypothesis_id, components, evidence_refs,
    integration_mechanism, potential_benefit per concept), use
    ``GET /api/v1/innovations/{innovation_id}``.
    """
    from synapse.api.v1._list_pagination import (
        DEFAULT_LIMIT,
        list_innovations_page,
    )

    # ``limit`` is intentionally clamped server-side to [1, 100] to
    # avoid silent truncation; out-of-range values raise 422.
    effective_limit = limit if limit is not None else DEFAULT_LIMIT

    page = await list_innovations_page(
        session,
        cursor=cursor,
        limit=effective_limit,
    )

    items = []
    for row in page.items:
        attributes: dict = {}
        if row.attributes:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(row.attributes)
                if isinstance(parsed, dict):
                    attributes = parsed
        items.append(
            {
                "id": row.id,
                "kind": row.kind,
                "canonical_name": row.canonical_name,
                "description": row.description or "",
                "attributes": attributes,
                "version": row.version,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )

    return Envelope.success(
        data={
            "items": items,
            "count": len(items),
        },
        request_id=request_id,
        pagination=page.pagination,
    )


@router.get("/{innovation_id}", status_code=status.HTTP_200_OK)
async def get_innovation(
    innovation_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Read a persisted innovation concept by stable ID.

    Returns the full concept payload (per the G05 §5.3 stability
    contract): ``concept_id``, ``canonical_name``, ``description``,
    ``attributes`` (which includes ``problem_domain``, ``context``,
    ``integration_mechanism``, ``potential_benefit``, ``uncertainties``,
    ``concept_fingerprint``, ``template_name``), ``hypothesis_id``,
    ``hypothesis_proposition``, ``hypothesis_evidence_refs``, and
    ``component_links`` (with each component's ``entity_id``,
    ``canonical_name``, ``kind``, ``role``, ``relationship_id``,
    ``evidence_refs``, ``origin``).

    This endpoint is read-only — it does NOT call
    ``critique_innovation`` (which would emit an
    ``innovation.critiqued`` audit row). It reuses the existing
    ``_load_persisted_concept`` helper from
    ``synapse.application.innovation``.

    Returns 404 ``NotFoundError`` if:
    - No ``EntityRow`` exists with the given ID, OR
    - The ``EntityRow`` exists but its ``kind`` is not ``"project"``
      (i.e., the ID is valid but is not an innovation concept), OR
    - The ``EntityRow`` is kind='project' but lacks a hypothesis
      ``ClaimRow`` and INTEGRATES_WITH edges — in which case the
      helper returns ``None`` and we treat it as "not an innovation".
    """
    concept = await _load_persisted_concept(session, innovation_id)
    if concept is None:
        raise NotFoundError(f"innovation {innovation_id!r} not found")
    return Envelope.success(
        data=concept,
        request_id=request_id,
    )
