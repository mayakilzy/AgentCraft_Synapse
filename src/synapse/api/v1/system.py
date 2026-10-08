"""``/api/v1/system`` — system state (activity mode) & ``/api/v1/jobs`` read.

G01 ships read-only access to the system activity mode and a stub job
listing endpoint. Mutation endpoints (PUT /system/activity-mode,
POST /jobs/{id}/cancel) require privileged scopes and return 501 in G01
since their full implementations belong to later groups.
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, status

from synapse.api.deps import PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError, UnsupportedFeatureError
from synapse.api.responses import Envelope

router = APIRouter(prefix="/system", tags=["system"])


class ActivityMode(str, Enum):
    PASSIVE = "passive"  # only serving reads, no autonomous work
    ASSISTED = "assisted"  # serving reads + queued writes
    AUTONOMOUS = "autonomous"  # fully autonomous — requires operator approval


# In-process activity mode — a real implementation persists this to DB
# in a later group. For G01 we keep it in memory (acceptable — process restart
# resets to PASSIVE which is the safe default).
_current_mode: ActivityMode = ActivityMode.PASSIVE


@router.get("/activity-mode")
async def get_activity_mode(
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Return the current activity mode (PASSIVE / ASSISTED / AUTONOMOUS)."""
    return Envelope.success(
        data={
            "mode": _current_mode.value,
            "transitionable_to": [m.value for m in ActivityMode],
        },
        request_id=request_id,
    )


@router.put("/activity-mode", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def put_activity_mode(
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Not implemented in G01 — belongs to a later group (per START_HERE
    §10 "Unsupported future routes must return a documented 501/feature-disabled
    response rather than fabricated success").
    """
    raise UnsupportedFeatureError(
        "PUT /system/activity-mode is not implemented in G01. "
        "It will land in a later group with operator-scope enforcement "
        "and an explicit policy decision."
    )


# ── Stub jobs endpoints ────────────────────────────────────────────────────────

jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])


@jobs_router.get("/{job_id}")
async def get_job(
    job_id: str,
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Stub — return a 404 for any unknown job_id in G01. Real job
    persistence begins in G02."""
    raise NotFoundError(
        f"Job {job_id!r} not found. (G01 ships only the contract; no jobs are persisted yet.)"
    )


@jobs_router.post("/{job_id}/cancel", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def cancel_job(
    job_id: str,
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Not implemented in G01 — full job lifecycle begins in G02."""
    raise UnsupportedFeatureError(
        "Job cancellation is not implemented in G01. "
        "It will land with the first real mutation endpoint (G02 Acquisition)."
    )
