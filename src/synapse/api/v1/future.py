"""G06-T02 + G06-T03 + G06-T04 — Future Intelligence API.

Activates the future-intelligence endpoints:
  - POST   /api/v1/future/signals                              (G06-T01)
  - POST   /api/v1/future/scenarios                            (G06-T02)
  - GET    /api/v1/future/scenarios/{scenario_id}              (G06-T02)
  - POST   /api/v1/future/scenarios/{scenario_id}/evaluate             (G06-T03)
  - POST   /api/v1/future/scenarios/{scenario_id}/prototype-plan       (G06-T03)
  - POST   /api/v1/future/scenarios/{scenario_id}/reassess             (G06-T04)
  - GET    /api/v1/future/scenarios/{scenario_id}/revisions            (G06-T04)

Per the approved G06 plan (section 9):

- Scenario creation and retrieval endpoints are functional (T02).
- The future-signal endpoint exposes the G06-T01 service (T02).
- The evaluate endpoint is functional (T03). After ``evaluate_scenario()``
  returns, the full evaluation snapshot is persisted in the scenario's
  attributes JSON (via ``persist_evaluation_snapshot``) so that the
  next ``reassess`` call can compare against a full prior snapshot.
- The prototype-plan endpoint is functional (T03) with two-mode design:
  Mode A (linked innovation) delegates to plan_experiment(); Mode B
  (no linked innovation) returns a proposal.
- The reassess endpoint (T04) is explicit-only — no background
  scheduler. A revision is warranted only when material evaluation
  fields differ from the prior authoritative snapshot.
- The revisions endpoint (T04) returns the append-only revision history
  (newest first) — prior snapshots are never overwritten.
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
from synapse.application.scenario_revision import (
    VALID_TRIGGERS,
    get_scenario_revisions,
    persist_evaluation_snapshot,
    reassess_scenario,
)

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

    The evaluation is read-only — it does NOT modify the scenario's
    persisted fields. The full evaluation snapshot is persisted under
    the scenario's ``last_evaluation`` attribute key (additive only) so
    that the next ``/reassess`` call can compare against a full prior
    snapshot, not just the summary fields recorded in the
    ``scenario.evaluated`` audit row.
    """
    result = await evaluate_scenario(
        session,
        scenario_id,
        requester=str(principal),
    )
    if result is None:
        raise NotFoundError(f"scenario {scenario_id!r} not found")
    # Persist the full evaluation snapshot for future reassessment
    # comparison. This is purely additive — the scenario's other
    # fields are not modified.
    await persist_evaluation_snapshot(session, scenario_id, result)
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
    hypothesis_id: str | None = Field(
        default=None,
        description=(
            "Optional explicit hypothesis selection for Mode A. "
            "If provided, must belong to the linked innovation concept. "
            "If omitted and exactly one hypothesis exists, auto-selected. "
            "If omitted and multiple hypotheses exist, returns 422."
        ),
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
            hypothesis_id=body.hypothesis_id,
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


# ── POST /api/v1/future/scenarios/{scenario_id}/reassess (G06-T04) ──────────


class ReassessRequest(BaseModel):
    """Request body for ``POST /api/v1/future/scenarios/{id}/reassess``."""

    trigger: str = Field(
        ...,
        description=(
            "Trigger of the reassessment. Must be one of: "
            f"{sorted(VALID_TRIGGERS)}. "
            "``explicit_reassess`` for caller-initiated re-evaluation; "
            "``new_evidence`` when new evidence fragments or claims have "
            "been added to the KB; ``new_experiment_result`` when a "
            "linked experiment has finalized and the hypothesis state "
            "may have changed."
        ),
    )
    trigger_ref: str | None = Field(
        default=None,
        description=(
            "Optional reference to the triggering artifact. For "
            "``new_experiment_result``, this is typically the execution_id "
            "or experiment_id. For ``new_evidence``, the fragment_id or "
            "claim_id. May be None for ``explicit_reassess``."
        ),
    )


@router.post("/scenarios/{scenario_id}/reassess", status_code=status.HTTP_200_OK)
async def reassess_scenario_endpoint(
    scenario_id: str,
    body: ReassessRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Reassess a persisted scenario (G06-T04).

    Explicit reassessment only — no background scheduler. The service
    recomputes the scenario evaluation against the current KB state and
    compares against the prior authoritative evaluation snapshot on
    material fields. If any material field differs (or no prior snapshot
    exists), a revision is warranted: the scenario's version is bumped,
    the new snapshot is persisted, and a ``scenario.revision`` audit
    event is emitted (append-only). Otherwise, ``revised=false`` and no
    audit row is written.

    Material fields: ``evidence_coverage``, ``distinct_source_uri_count``,
    ``independent_origin_count``, ``dependency_readiness``,
    ``contradiction_exposure``, ``assumption_assessment``,
    ``horizon_alignment``, ``overall``. Volatile metadata
    (``evaluated_at``, request IDs) is excluded from the comparison.
    """
    try:
        result = await reassess_scenario(
            session,
            scenario_id,
            trigger=body.trigger,
            trigger_ref=body.trigger_ref,
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


# ── GET /api/v1/future/scenarios/{scenario_id}/revisions (G06-T04) ─────────


@router.get("/scenarios/{scenario_id}/revisions", status_code=status.HTTP_200_OK)
async def list_revisions_endpoint(
    scenario_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[list]:
    """Return the scenario's revision history, newest first (G06-T04).

    Append-only: prior revision snapshots are never overwritten. Each
    revision carries the full prior and new evaluation snapshots, the
    change summary, the trigger, and the provenance. Returns an empty
    list if the scenario has never been revised.
    """
    # Verify the scenario exists (404 if not).
    scenario = await get_scenario(session, scenario_id)
    if scenario is None:
        raise NotFoundError(f"scenario {scenario_id!r} not found")

    revisions = await get_scenario_revisions(session, scenario_id)
    return Envelope.success(
        data=revisions,
        request_id=request_id,
    )
