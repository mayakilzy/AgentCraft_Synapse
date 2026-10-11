"""G06-T02 — Future Intelligence API.

Activates the three existing 501 placeholders for future scenarios:
  - POST   /api/v1/future/scenarios
  - GET    /api/v1/future/scenarios/{scenario_id}
  - POST   /api/v1/future/scenarios/{scenario_id}/prototype-plan

Also exposes the G06-T01 signal service:
  - POST   /api/v1/future/signals

Per the approved G06 plan (section 9):

- Scenario creation and retrieval endpoints become functional in T02.
- The future-signal endpoint exposes the completed G06-T01 service
  without duplicating its logic.
- The prototype-plan endpoint is **deferred to G06-T03**. It retains
  an explicit, documented not-yet-implemented response (501) rather
  than pretending it is complete. The route remains registered for
  contract compatibility.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError, UnsupportedFeatureError, ValidationError
from synapse.api.responses import Envelope
from synapse.application.future_signal import identify_future_signals
from synapse.application.scenario_builder import build_scenario, get_scenario

router = APIRouter(prefix="/future", tags=["future"])


# ── Request / response models ──────────────────────────────────────────────


class SignalRequest(BaseModel):
    """Request body for ``POST /api/v1/future/signals``."""

    focus_capability_id: str | None = Field(
        default=None,
        description="If provided, restrict signals to this capability.",
    )
    focus_entity_id: str | None = Field(
        default=None,
        description="If provided, restrict signals to capabilities linked to this entity.",
    )
    combination_problem_domain: str | None = Field(
        default=None,
        description="Optional problem domain for combination density computation.",
    )
    combination_context: str | None = Field(
        default=None,
        description="Optional context for combination density and gap analysis.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Max number of capabilities to scan.",
    )


class ScenarioRequest(BaseModel):
    """Request body for ``POST /api/v1/future/scenarios``."""

    capability_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Capability entity IDs the scenario involves. Every ID must resolve.",
    )
    assumptions: list[str] = Field(
        ...,
        min_length=1,
        description="Caller-supplied assumptions (non-empty strings).",
    )
    signal_id: str | None = Field(
        default=None,
        description="Optional G06-T01 signal ID for grounding.",
    )
    signal_content_version: str | None = Field(
        default=None,
        description="Optional content-version hash for signal drift detection.",
    )
    proposed_combination: str | None = Field(
        default=None,
        description="Optional human-readable description of the proposed integration.",
    )
    evidence_refs: list[str] | None = Field(
        default=None,
        description="Optional evidence fragment IDs. Every ID must resolve.",
    )
    missing_dependencies: list[str] | None = Field(
        default=None,
        description="Optional dependency entity IDs. If omitted, derived from analyze_gap.",
    )
    horizon_class_override: str | None = Field(
        default=None,
        description="Optional horizon class (F0-F4). May only downgrade.",
    )
    critique: str | None = Field(
        default=None,
        description="Mandatory for F4; optional otherwise.",
    )
    prototype_plan: str | None = Field(
        default=None,
        description="Optional human-readable prototype plan.",
    )
    linked_innovation_concept_id: str | None = Field(
        default=None,
        description="Optional G05 innovation concept entity ID.",
    )
    run_id: str | None = Field(
        default=None,
        description="Optional retry identity for idempotent creation.",
    )


# ── POST /api/v1/future/signals ────────────────────────────────────────────


@router.post("/signals", status_code=status.HTTP_200_OK)
async def create_signals(
    body: SignalRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Identify current knowledge-base signals (Layer 1 snapshots only).

    Exposes the G06-T01 ``identify_future_signals()`` service. Does NOT
    duplicate the signal logic — delegates directly.
    """
    result = await identify_future_signals(
        session,
        focus_capability_id=body.focus_capability_id,
        focus_entity_id=body.focus_entity_id,
        combination_problem_domain=body.combination_problem_domain,
        combination_context=body.combination_context,
        limit=body.limit,
        requester=str(principal),
    )
    return Envelope.success(
        data=result,
        request_id=request_id,
    )


# ── POST /api/v1/future/scenarios ──────────────────────────────────────────


@router.post("/scenarios", status_code=status.HTTP_200_OK)
async def create_scenario(
    body: ScenarioRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Construct, validate, and persist a future-intelligence scenario.

    The horizon class (F0-F4) is derived deterministically from the KB
    state. The caller may only downgrade (make more speculative).

    Quality gates:
    - Every capability_id resolves to a persisted EntityRow(kind=capability).
    - Every evidence_ref resolves to a persisted EvidenceFragmentRow.
    - Every missing_dependency resolves to a persisted entity.
    - F4 requires a critique.
    - F1 requires at least one honest integration dependency; fail-closed.
    - F0 requires evidenced integration + empty missing_dependencies.
    - No fabricated evidence, dependencies, or trends.
    - No auto-VERIFIED promotion (impossible by construction).
    - Idempotent retry via run_id; conflicting retry rejected.
    """
    try:
        result = await build_scenario(
            session,
            capability_ids=body.capability_ids,
            assumptions=body.assumptions,
            signal_id=body.signal_id,
            signal_content_version=body.signal_content_version,
            proposed_combination=body.proposed_combination,
            evidence_refs=body.evidence_refs,
            missing_dependencies=body.missing_dependencies,
            horizon_class_override=body.horizon_class_override,
            critique=body.critique,
            prototype_plan=body.prototype_plan,
            linked_innovation_concept_id=body.linked_innovation_concept_id,
            run_id=body.run_id,
            requester=str(principal),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    return Envelope.success(
        data=result,
        request_id=request_id,
    )


# ── GET /api/v1/future/scenarios/{scenario_id} ─────────────────────────────


@router.get("/scenarios/{scenario_id}", status_code=status.HTTP_200_OK)
async def retrieve_scenario(
    scenario_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Retrieve a persisted scenario by ID."""
    result = await get_scenario(session, scenario_id)
    if result is None:
        raise NotFoundError(f"scenario {scenario_id!r} not found")
    return Envelope.success(
        data=result,
        request_id=request_id,
    )


# ── POST /api/v1/future/scenarios/{scenario_id}/prototype-plan ─────────────
#
# This endpoint is DEFERRED to G06-T03. It retains an explicit,
# documented not-yet-implemented response (501) rather than pretending
# it is complete. The route remains registered for contract compatibility.
#
# Per the G06 plan section 9.3 and the T02 mission section 6:
# "If prototype planning is deferred to G06-T03, retain an explicit,
# documented not-yet-implemented response rather than pretending it is
# complete."


@router.post(
    "/scenarios/{scenario_id}/prototype-plan",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def prototype_plan(
    scenario_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> None:
    """Prototype-plan endpoint — deferred to G06-T03.

    This endpoint will be implemented in G06-T03 (Scenario Evaluation +
    Prototype Plan). It will delegate to ``plan_experiment()`` only in
    Mode A (linked innovation + hypothesis) with caller-supplied
    metrics; in Mode B (no linked innovation) it will return a proposal.

    For now, it returns 501 Not Implemented to preserve contract
    compatibility without prematurely implementing T03 logic.
    """
    raise UnsupportedFeatureError(
        "POST /future/scenarios/{id}/prototype-plan is not implemented in "
        "G06-T02. It will land in G06-T03 (Scenario Evaluation + Prototype "
        "Plan)."
    )
