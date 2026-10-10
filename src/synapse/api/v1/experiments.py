"""``POST /api/v1/experiments`` + ``GET /api/v1/experiments/{experiment_id}``
+ ``POST /api/v1/experiments/{experiment_id}/execute``
-- G05-T05 + G05-T06 Experiment API.

Per the corrected G05 plan: these endpoints activate the G04 501
placeholders for experiment planning (T05) and execution (T06).
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError, ValidationError
from synapse.api.responses import Envelope
from synapse.application.execution_record import create_execution
from synapse.application.experiment_planner import (
    MAX_BASELINE_CHARS,
    MAX_PROTOCOL_CHARS,
    plan_experiment,
)
from synapse.domain.experiment import ExperimentExecutionMode

router = APIRouter(prefix="/experiments", tags=["experiments"])


# ── Request / response models ────────────────────────────────────────────────


class ExperimentRequest(BaseModel):
    """Request body for ``POST /api/v1/experiments``."""

    hypothesis_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="ID of the persisted hypothesis to plan an experiment for.",
    )
    protocol: str | None = Field(
        default=None,
        max_length=MAX_PROTOCOL_CHARS,
        description=(
            "Optional human-readable protocol. If omitted, a default "
            "protocol is synthesized from the concept's uncertainties."
        ),
    )
    baseline: str | None = Field(
        default=None,
        max_length=MAX_BASELINE_CHARS,
        description=(
            "Optional baseline description. If omitted, a default is "
            "synthesized."
        ),
    )
    metrics: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional metric schema (``{name: dtype}``). If omitted, a "
            "default is synthesized from quantifiable uncertainties."
        ),
    )
    execution_mode: str = Field(
        default=ExperimentExecutionMode.DRY_RUN.value,
        description=(
            "Execution mode. One of: dry_run, sandbox, local, networked, "
            "production. Default: dry_run. production requires "
            "safety_limits.approved_by."
        ),
    )
    safety_limits: dict[str, str | None] = Field(
        default_factory=dict,
        description="Safety limits. production mode requires approved_by.",
    )
    cost_limits: dict[str, str | None] = Field(
        default_factory=dict,
        description="Cost limits. networked mode requires approved_by.",
    )


# ── POST /api/v1/experiments ──────────────────────────────────────────────


@router.post("", status_code=status.HTTP_200_OK)
async def create_experiment_plan(
    body: ExperimentRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Plan a measurable, reproducible experiment for a persisted hypothesis.

    The hypothesis must already be persisted (via G05-T03
    ``POST /innovations/generate``). This endpoint does NOT execute the
    experiment -- it produces a structured plan with objectives, success/
    failure criteria, required inputs, and interpretation rules.

    Quality gates:
    - Every experiment references a valid persisted hypothesis.
    - Objectives and metrics are measurable.
    - Success and failure conditions are explicit (>=1 each).
    - Unavailable resources are reported rather than invented.
    - No ObservationRecord or EvidenceDelta is created during planning.
    - Experiment plans do not change hypothesis confidence or epistemic
      state.
    - Multiple experiments may reference the same hypothesis.
    - No fabricated experimental outcomes.
    - No automatic VERIFIED promotion.
    """
    # Validate execution_mode against the enum
    try:
        mode_enum = ExperimentExecutionMode(body.execution_mode)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown execution_mode={body.execution_mode!r}; expected one of "
            f"{[m.value for m in ExperimentExecutionMode]}"
        ) from exc

    try:
        result = await plan_experiment(
            session,
            body.hypothesis_id,
            protocol=body.protocol,
            baseline=body.baseline,
            metrics=body.metrics,
            execution_mode=mode_enum,
            safety_limits=body.safety_limits,
            cost_limits=body.cost_limits,
            requester=str(principal),
        )
    except ValueError as exc:
        # Invariant violation from the Experiment domain record (e.g.
        # production mode missing safety_limits.approved_by)
        raise ValidationError(str(exc)) from exc

    if result is None:
        raise NotFoundError(
            f"hypothesis {body.hypothesis_id!r} not found or has no "
            f"linked innovation concept"
        )

    return Envelope.success(
        data=result,
        request_id=request_id,
    )


# ── GET /api/v1/experiments/{experiment_id} ───────────────────────────────


@router.get("/{experiment_id}", status_code=status.HTTP_200_OK)
async def get_experiment_plan(
    experiment_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Retrieve a persisted experiment plan by ID.

    Returns the plan dict including objectives, success/failure criteria,
    required inputs, interpretation rules, and evidence references. The
    ``result`` field is always ``null`` for a plan -- results are recorded
    by G05-T06 execution.
    """
    from synapse.application.experiment_planner import get_experiment

    plan = await get_experiment(session, experiment_id)
    if plan is None:
        raise NotFoundError(f"experiment {experiment_id!r} not found")

    return Envelope.success(
        data=plan,
        request_id=request_id,
    )


# ── POST /api/v1/experiments/{experiment_id}/execute ──────────────────────


class ExecuteRequest(BaseModel):
    """Request body for ``POST /api/v1/experiments/{experiment_id}/execute``."""

    execution_mode: str | None = Field(
        default=None,
        description=(
            "Optional override for the execution mode. If omitted, the "
            "experiment plan's execution_mode is used."
        ),
    )
    run_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional run identifier for idempotent retry. If provided, "
            "the same run_id always produces the same execution_id "
            "(idempotent). If omitted, each call creates a distinct "
            "independent execution."
        ),
    )


@router.post("/{experiment_id}/execute", status_code=status.HTTP_200_OK)
async def execute_experiment(
    experiment_id: str,
    body: ExecuteRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Create an execution record for an existing experiment plan.

    The experiment must already be persisted (via G05-T05
    ``POST /api/v1/experiments``). This endpoint creates a traceable
    execution record with a stable ID, lifecycle status, and protocol
    snapshot. It does NOT execute arbitrary code -- it records that an
    execution is starting.

    Quality gates:
    - The experiment_id must resolve to a persisted experiment plan.
    - Idempotent: equivalent requests reuse the same execution_id.
    - No EvidenceDelta is created during execution creation.
    - No hypothesis state mutation.
    """
    try:
        result = await create_execution(
            session,
            experiment_id,
            execution_mode=body.execution_mode,
            run_id=body.run_id,
            requester=str(principal),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if result is None:
        raise NotFoundError(f"experiment {experiment_id!r} not found")

    return Envelope.success(data=result, request_id=request_id)
