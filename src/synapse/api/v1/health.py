"""Health endpoints — ``/health/live`` and ``/health/ready``.

Live = process is up. Ready = process is up AND can serve real requests
(database reachable, critical providers available).
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import text

from synapse.api.deps import DbSessionDep
from synapse.api.responses import Envelope
from synapse.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", status_code=status.HTTP_200_OK)
async def live() -> Envelope[dict]:
    """Liveness — always 200 if the process is alive."""
    return Envelope.success(
        data={
            "status": "ok",
            "service": get_settings().app_name,
            "env": get_settings().env,
        }
    )


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready(
    session: DbSessionDep,
) -> Envelope[dict]:
    """Readiness — checks DB connectivity."""
    try:
        result = await session.execute(text("SELECT 1"))
        one = result.scalar_one()
        if one != 1:
            raise RuntimeError(f"unexpected db result: {one!r}")
        db_ok = True
    except Exception:
        db_ok = False

    return Envelope.success(
        data={
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "unreachable",
        }
    )
