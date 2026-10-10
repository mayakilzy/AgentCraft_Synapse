"""G05-T06 -- Experiment Execution + Evidence Feedback API.

Endpoints:
- ``POST /api/v1/executions/{execution_id}/observations`` — record observation
- ``POST /api/v1/executions/{execution_id}/finalize`` — finalize + assess
- ``GET /api/v1/executions/{execution_id}`` — retrieve execution

The ``POST /api/v1/experiments/{experiment_id}/execute`` endpoint is
registered in ``experiments.py`` (it lives on the experiments router).

Follows existing FastAPI conventions: auth, standard envelope, validation
errors, OpenAPI compatibility.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError, ValidationError
from synapse.api.responses import Envelope
from synapse.application.execution_record import (
    ExecutionStatus,
    finalize_execution,
    get_execution,
    record_observation,
)

router = APIRouter(prefix="/executions", tags=["executions"])


# ── Request / response models ────────────────────────────────────────────────


class ObservationRequest(BaseModel):
    """Request body for ``POST /api/v1/executions/{execution_id}/observations``."""

    metric: str = Field(..., min_length=1, description="The metric identifier.")
    observed_value: float | str | bool | None = Field(
        ..., description="The observed value."
    )
    expected_value: float | str | bool | None = Field(
        default=None, description="Optional expected/baseline value."
    )
    unit: str | None = Field(default=None, description="Optional unit.")
    measurement_method: str = Field(
        default="manual", description="How the measurement was taken."
    )
    source_ref: str | None = Field(
        default=None, description="Source reference (experimenter, tool, etc.)."
    )
    uncertainty: str | None = Field(
        default=None, description="Optional uncertainty or limitation note."
    )


class FinalizeRequest(BaseModel):
    """Request body for ``POST /api/v1/executions/{execution_id}/finalize``."""

    status: str = Field(
        default=ExecutionStatus.COMPLETED,
        description=(
            "Terminal status: completed, failed, or cancelled. "
            "Default: completed."
        ),
    )
    error_details: dict[str, str] | None = Field(
        default=None, description="Optional error/failure details (for failed)."
    )


# ── POST /api/v1/executions/{execution_id}/observations ──────────────────


@router.post("/{execution_id}/observations", status_code=status.HTTP_200_OK)
async def add_observation(
    execution_id: str,
    body: ObservationRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Record an observation against an execution.

    The metric must be declared in the experiment plan's metrics schema.
    Observations are append-only — they are never overwritten or removed.

    The execution transitions from CREATED to RUNNING on the first
    observation.
    """
    try:
        result = await record_observation(
            session,
            execution_id,
            metric=body.metric,
            observed_value=body.observed_value,
            expected_value=body.expected_value,
            unit=body.unit,
            measurement_method=body.measurement_method,
            source_ref=body.source_ref,
            uncertainty=body.uncertainty,
            requester=str(principal),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if result is None:
        raise NotFoundError(f"execution {execution_id!r} not found")

    return Envelope.success(data=result, request_id=request_id)


# ── POST /api/v1/executions/{execution_id}/finalize ──────────────────────


@router.post("/{execution_id}/finalize", status_code=status.HTTP_200_OK)
async def finalize_execution_endpoint(
    execution_id: str,
    body: FinalizeRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Finalize an execution: set terminal status, assess evidence, emit
    EvidenceDelta if the assessment changes the hypothesis state.

    If status=completed, the evidence assessment runs automatically and
    an EvidenceDelta may be emitted (HYPOTHESIZED → SUPPORTED or
    DISPUTED). If status=failed or cancelled, no assessment runs.

    The hypothesis is NEVER promoted to VERIFIED.
    """
    try:
        result = await finalize_execution(
            session,
            execution_id,
            status=body.status,
            error_details=body.error_details,
            requester=str(principal),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    if result is None:
        raise NotFoundError(f"execution {execution_id!r} not found")

    return Envelope.success(data=result, request_id=request_id)


# ── GET /api/v1/executions/{execution_id} ────────────────────────────────


@router.get("/{execution_id}", status_code=status.HTTP_200_OK)
async def get_execution_endpoint(
    execution_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Retrieve an execution record by ID.

    Returns the full execution dict including status, observations,
    assessment (if finalized), and evidence_delta_id (if emitted).
    """
    result = await get_execution(session, execution_id)
    if result is None:
        raise NotFoundError(f"execution {execution_id!r} not found")

    return Envelope.success(data=result, request_id=request_id)
