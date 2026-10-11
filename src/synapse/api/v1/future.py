"""G06-T02 + G06-T03 — Future Intelligence API.

Activates the future-intelligence endpoints:
  - POST   /api/v1/future/signals                     (G06-T01)
  - POST   /api/v1/future/scenarios                   (G06-T02)
  - GET    /api/v1/future/scenarios/{scenario_id}     (G06-T02)
  - POST   /api/v1/future/scenarios/{scenario_id}/evaluate       (G06-T03)
  - POST   /api/v1/future/scenarios/{scenario_id}/prototype-plan (G06-T03)

Per the approved G06 plan (section 9):

- Scenario creation and retrieval endpoints are functional (T02).
- The future-signal endpoint exposes the G06-T01 service (T02).
- The evaluate endpoint is functional (T03).
- The prototype-plan endpoint is functional (T03) with two-mode design:
  Mode A (linked innovation) delegates to plan_experiment(); Mode B
  (no linked innovation) returns a proposal.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError, ValidationError
from synapse.api.responses import Envelope
from synapse.application.future_signal import identify_future_signals
from synapse.application.prototype_planner import plan_prototype
from synapse.application.scenario_builder import build_scenario, get_scenario
from synapse.application.scenario_evaluator import evaluate_scenario

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


# ── POST /api/v1/future/scenarios/{scenario_id}/evaluate ──────────────────


class EvaluateRequest(BaseModel):
    """Request body for ``POST /api/v1/future/scenarios/{id}/evaluate``."""

    pass  # No body fields — evaluation is deterministic from the scenario


@router.post("/scenarios/{scenario_id}/evaluate", status_code=status.HTTP_200_OK)
async def evaluate_scenario_endpoint(
    scenario_id: str,
    body: EvaluateRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Evaluate a persisted scenario deterministically.

    Returns a structured evaluation with evidence coverage, source
    provenance, dependency readiness, contradiction exposure, assumption
    assessment, horizon alignment, and an overall categorical result.

    The evaluation is read-only — it does NOT modify the scenario.
    """
    result = await evaluate_scenario(
        session,
        scenario_id,
        requester=str(principal),
    )
    if result is None:
        raise NotFoundError(f"scenario {scenario_id!r} not found")
    return Envelope.success(
        data=result,
        request_id=request_id,
    )


# ── POST /api/v1/future/scenarios/{scenario_id}/prototype-plan ─────────────


class PrototypePlanRequest(BaseModel):
    """Request body for ``POST /api/v1/future/scenarios/{id}/prototype-plan``."""

    protocol_override: str | None = Field(
        default=None,
        description="Optional protocol override. If omitted, the scenario's prototype_plan is used.",
    )
    metrics: dict[str, str] | None = Field(
        default=None,
        description=(
            "Caller-supplied metric schema ({name: dtype}). "
            "REQUIRED for Mode A (linked innovation). Ignored in Mode B."
        ),
    )
    baseline: str | None = Field(default=None, description="Optional baseline (Mode A only).")
    execution_mode: str | None = Field(
        default=None, description="Optional execution mode (Mode A only)."
    )
    safety_limits: dict[str, str | None] | None = Field(
        default=None, description="Optional safety limits (Mode A only)."
    )
    cost_limits: dict[str, str | None] | None = Field(
        default=None, description="Optional cost limits (Mode A only)."
    )
    run_id: str | None = Field(
        default=None, description="Optional retry identity for idempotent creation."
    )


@router.post("/scenarios/{scenario_id}/prototype-plan", status_code=status.HTTP_200_OK)
async def prototype_plan_endpoint(
    scenario_id: str,
    body: PrototypePlanRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Plan a prototype for a persisted scenario (two-mode design).

    **Mode A — Linked Innovation**: if the scenario has a
    ``linked_innovation_concept_id`` that resolves to a persisted G05
    innovation concept with a hypothesis, delegate to
    ``plan_experiment()`` with caller-supplied metrics.

    **Mode B — No Linked Innovation**: return a structured prototype
    proposal. Do NOT call ``plan_experiment()``. Do NOT create an
    Experiment entity.

    Quality gates:
    - F4 scenarios require a critique (frozen validator).
    - Mode A requires caller-supplied metrics — never fabricated.
    - Mode A never bypasses hypothesis resolution.
    - No new relationship predicates.
    - G05 experiment-planning behavior is unchanged.
    """
    try:
        result = await plan_prototype(
            session,
            scenario_id,
            protocol_override=body.protocol_override,
            metrics=body.metrics,
            baseline=body.baseline,
            execution_mode=body.execution_mode,
            safety_limits=body.safety_limits,
            cost_limits=body.cost_limits,
            run_id=body.run_id,
            requester=str(principal),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if result is None:
        raise NotFoundError(f"scenario {scenario_id!r} not found")
    return Envelope.success(
        data=result,
        request_id=request_id,
    )
