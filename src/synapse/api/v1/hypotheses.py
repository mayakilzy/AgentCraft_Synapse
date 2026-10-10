"""``GET /api/v1/hypotheses/{hypothesis_id}/evidence-deltas``
-- G05-T06 Evidence Feedback API.

Returns the ordered evidence delta audit trail for a hypothesis.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.errors import NotFoundError
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.execution_record import get_evidence_deltas
from synapse.storage.models import ClaimRow

router = APIRouter(prefix="/hypotheses", tags=["hypotheses"])


@router.get(
    "/{hypothesis_id}/evidence-deltas",
    status_code=status.HTTP_200_OK,
)
async def get_hypothesis_evidence_deltas(
    hypothesis_id: str,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Retrieve the ordered evidence delta audit trail for a hypothesis.

    Returns the full belief-change history: each EvidenceDelta records
    a prior_state → updated_state transition, the observation that
    triggered it, and the assessment reasoning.

    The deltas are ordered newest-first. If the hypothesis has no
    evidence deltas, an empty list is returned.
    """
    # Verify the hypothesis exists
    stmt = select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    hypothesis = (await session.execute(stmt)).scalar_one_or_none()
    if hypothesis is None:
        raise NotFoundError(f"hypothesis {hypothesis_id!r} not found")

    deltas = await get_evidence_deltas(session, hypothesis_id)

    return Envelope.success(
        data={
            "hypothesis_id": hypothesis_id,
            "deltas": deltas,
            "total": len(deltas),
        },
        request_id=request_id,
        pagination=PaginationMeta(
            limit=len(deltas),
            total=len(deltas),
        ),
    )
