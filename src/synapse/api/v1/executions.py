"""G05-T06 -- Experiment Execution + Evidence Feedback API.

Endpoints:
- ``POST /api/v1/executions/{execution_id}/observations`` — record observation
- ``POST /api/v1/executions/{execution_id}/finalize`` — finalize + assess
- ``GET /api/v1/executions/{execution_id}`` — retrieve execution

The ``POST /api/v1/experiments/{experiment_id}/execute`` endpoint is
registered in ``experiments.py`` (it lives on the experiments router).

G07-PRE-02 (read-only amendments): a new ``GET /api/v1/executions`` list
endpoint was added. It is read-only (no audit event, no mutation) and
paginated via the new ``_list_pagination`` module. Optional
``experiment_id`` filter is supported via a substring match on the
execution's persisted ``attributes`` JSON column (portable across
SQLite and PostgreSQL).

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


# ── G07-PRE-02 — read-only list (paginated, optional experiment_id) ────────
#
# This GET was added by the G07-PRE-02 mission. It is read-only (no
# audit event, no mutation) and paginated via the new
# ``_list_pagination`` module. Route ordering is safe: FastAPI matches
# by HTTP method, and the empty-string path "" only matches the exact
# prefix ``/api/v1/executions`` (not ``/api/v1/executions/{id}``).


@router.get("", status_code=status.HTTP_200_OK)
async def list_executions(
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
    experiment_id: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> Envelope[dict]:
    """List persisted execution records, newest first.

    Optional ``experiment_id`` filter: returns only executions whose
    ``attributes.experiment_id`` matches EXACTLY. The filter uses a
    portable JSON-path extraction (``json_extract`` on SQLite, ``->>``
    on PostgreSQL) — not a SQL LIKE substring match. The exact match
    treats ``%``, ``_``, quotes, and other special characters as
    ordinary data. Only the top-level ``experiment_id`` key is
    compared; the value will not match if the same ID appears only in
    a different field (e.g. a ``trigger_ref`` that embeds an
    experiment_id).

    Per G07-PRE-02A Task A: this replaces the previous SQL LIKE
    approach which could match substrings and wildcard characters.

    Returns a paginated list of execution records filtered to genuine
    persisted executions via the ``EntityFingerprintRow`` discriminator
    (kind='execution'). Each item carries the full execution payload
    (reconstructed by the existing ``_attributes_to_execution`` helper,
    reused via ``get_execution``).

    This endpoint is read-only — it emits no audit event, mutates no
    attributes, and never calls ``create_execution``,
    ``record_observation``, or ``finalize_execution``.

    Pagination:
    - ``limit``: 1..100 (default 20).
    - ``cursor``: opaque base64url token; the previous page's
      ``meta.pagination.next_cursor`` value, or ``None`` for the first
      page.
    - ``next_cursor``: populated iff more results exist after this
      page; ``None`` if this is the last page.
    - Cursor malformed → 422 ``validation_error``; stale cursor →
      empty page (200, ``items=[]``, ``next_cursor=null``).
    """
    from synapse.api.v1._list_pagination import (
        DEFAULT_LIMIT,
        list_executions_page,
    )

    effective_limit = limit if limit is not None else DEFAULT_LIMIT

    page = await list_executions_page(
        session,
        cursor=cursor,
        limit=effective_limit,
        experiment_id=experiment_id,
    )

    # Reconstruct each execution via the existing read-only
    # ``get_execution`` helper.
    items = []
    for row in page.items:
        execution = await get_execution(session, row.id)
        if execution is None:
            continue
        items.append(execution)

    return Envelope.success(
        data={
            "items": items,
            "count": len(items),
        },
        request_id=request_id,
        pagination=page.pagination,
    )
