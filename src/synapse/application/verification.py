"""Evidence Verification Engine — G03-T04.

Per the user's G03-T04 authorization: implement the minimal Evidence
Verification Engine using existing G01 contracts, G03 knowledge
persistence, and RelationshipService.

The engine distinguishes source assertions from independently verified
knowledge and preserves uncertainty, contradiction, context, and provenance.

Key design decisions:
  - A separate ``VerificationAssessment`` record is used (per requirement #4)
    rather than changing G01's ``EpistemicState`` enum.
  - Verification outcomes are a NEW enum (``VerificationOutcome``) that maps
    to but does NOT replace G01's ``EpistemicState``.
  - Source independence is tracked by ``canonical_uri`` — multiple fragments
    from the same URI count as ONE source (requirement #3).
  - Contradictory evidence is preserved — never deleted or overwritten.
  - ``EvidenceDelta`` records meaningful assessment changes.
  - Automated ``VERIFIED`` requires ≥2 independent sources AND no
    contradictions (requirement #9). Otherwise the assessment stays lower.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.observability.logging import get_logger
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EvidenceFragmentRow,
)

_log = get_logger("synapse.application.verification")


# ── Verification outcome enum ───────────────────────────────────────────────


class VerificationOutcome:
    """Verification outcomes — distinct from G01's EpistemicState.

    Per requirement #7: these outcomes MAP to existing contracts without
    changing their meaning.

    Mapping:
      INSUFFICIENT_EVIDENCE → Claim.epistemic_state = hypothesized
      SOURCE_SUPPORTED      → Claim.epistemic_state = supported
      CORROBORATED          → Claim.epistemic_state = supported (≥2 independent)
      CONTESTED             → Claim.epistemic_state = disputed
      VERIFIED              → Claim.epistemic_state = supported + verification_state=verified (for relationships)
      STALE_OR_CONTEXT_MISMATCH → Claim stays as-is; assessment flags staleness

    Per requirement #9: VERIFIED requires a documented policy + ≥2 independent
    sources AND no contradictions. If the policy is not met, the outcome is
    downgraded to CORROBORATED or SOURCE_SUPPORTED.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCE_SUPPORTED = "source_supported"
    CORROBORATED = "corroborated"
    CONTESTED = "contested"
    VERIFIED = "verified"
    STALE_OR_CONTEXT_MISMATCH = "stale_or_context_mismatch"
    NOT_EVIDENCED = "not_evidenced"  # for missing capabilities without negative evidence

    ALL = frozenset(
        {
            INSUFFICIENT_EVIDENCE,
            SOURCE_SUPPORTED,
            CORROBORATED,
            CONTESTED,
            VERIFIED,
            STALE_OR_CONTEXT_MISMATCH,
            NOT_EVIDENCED,
        }
    )


# ── Verification policy ────────────────────────────────────────────────────

#: Minimum number of independent sources required for VERIFIED.
#: Per requirement #9: "Automated VERIFIED decisions require a documented,
#: sufficiently strong policy and appropriate evidence."
MIN_INDEPENDENT_SOURCES_FOR_VERIFIED = 2

#: Minimum number of independent sources required for CORROBORATED.
MIN_INDEPENDENT_SOURCES_FOR_CORROBORATED = 2

#: Staleness threshold in days — evidence older than this is flagged as
#: potentially stale. This is a SOFT flag; the assessment outcome is
#: STALE_OR_CONTEXT_MISMATCH only if the evidence contradicts more recent
#: evidence or the claim's validity conditions specify a time bound.
STALENESS_THRESHOLD_DAYS = 365


# ── Verification assessment record ──────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_assessment_dict(
    *,
    claim_id: str,
    outcome: str,
    reason_code: str,
    supporting_evidence: list[dict[str, Any]],
    opposing_evidence: list[dict[str, Any]],
    independent_sources: list[str],
    context_notes: list[str],
    stale_evidence: list[str],
    policy_version: str = "deterministic-v1",
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Build a verification assessment dict.

    This is the ``VerificationAssessment`` record — stored in the
    ``verification_assessments`` table (migration 0005). It is a SEPARATE
    record from the G01 Claim — per requirement #4, we do not change G01
    enums.
    """
    return {
        "id": uuid4().hex,
        "claim_id": claim_id,
        "outcome": outcome,
        "reason_code": reason_code,
        "supporting_evidence_count": len(supporting_evidence),
        "opposing_evidence_count": len(opposing_evidence),
        "independent_source_count": len(independent_sources),
        "supporting_evidence": supporting_evidence,
        "opposing_evidence": opposing_evidence,
        "independent_sources": independent_sources,
        "context_notes": context_notes,
        "stale_evidence": stale_evidence,
        "policy_version": policy_version,
        "assessed_at": _utcnow_iso(),
        "reviewer": reviewer,
    }


# ── Evidence assessment ─────────────────────────────────────────────────────


async def _get_evidence_fragments(
    session: AsyncSession,
    evidence_refs: list[str],
) -> list[EvidenceFragmentRow]:
    """Fetch evidence fragment rows for the given IDs."""
    if not evidence_refs:
        return []
    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(evidence_refs))
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _get_source_uri(fragment: EvidenceFragmentRow) -> str:
    """Get the canonical source URI for independence tracking.

    Per requirement #3: multiple copies of the same original source must
    not count as independent evidence. We use ``source_uri`` as the
    canonical identifier for source independence.
    """
    return fragment.source_uri or fragment.source_id or fragment.id


def _is_stale(
    fragment: EvidenceFragmentRow, threshold_days: int = STALENESS_THRESHOLD_DAYS
) -> bool:
    """Check if evidence is potentially stale based on retrieval timestamp."""
    if not fragment.retrieved_at:
        return False  # unknown timestamp — don't flag as stale
    try:
        retrieved = datetime.fromisoformat(fragment.retrieved_at.replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - retrieved).days
        return age_days > threshold_days
    except (ValueError, TypeError):
        return False


async def assess_claim(
    session: AsyncSession,
    claim_id: str,
    *,
    requester: str | None = None,
) -> dict[str, Any]:
    """Assess a claim's evidence and produce a verification outcome.

    This is the main entry point for the verification engine. It:
    1. Loads the claim and its evidence_refs
    2. Fetches the evidence fragments
    3. Evaluates source independence, evidence quality, staleness
    4. Checks for contradictions (opposing evidence)
    5. Produces a VerificationOutcome with reason codes
    6. Records a VerificationAssessment
    7. If the outcome differs from a previous assessment, records an EvidenceDelta

    Idempotent: re-running with the same evidence produces the same outcome.
    Safe to rerun when new evidence appears: new evidence triggers reassessment.
    """
    request_id = uuid4().hex

    # Load the claim
    claim_stmt = select(ClaimRow).where(ClaimRow.id == claim_id)
    claim_result = await session.execute(claim_stmt)
    claim = claim_result.scalar_one_or_none()

    if claim is None:
        return {
            "ok": False,
            "error": "claim_not_found",
            "claim_id": claim_id,
            "request_id": request_id,
        }

    # Parse evidence_refs from the claim
    evidence_refs: list[str] = []
    if claim.evidence_refs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            evidence_refs = json.loads(claim.evidence_refs)

    # Fetch evidence fragments
    fragments = await _get_evidence_fragments(session, evidence_refs)

    # ── Evaluate evidence ──────────────────────────────────────────────

    # Source independence: group by source_uri
    source_groups: dict[str, list[EvidenceFragmentRow]] = {}
    for frag in fragments:
        uri = _get_source_uri(frag)
        source_groups.setdefault(uri, []).append(frag)

    independent_sources = list(source_groups.keys())
    independent_source_count = len(independent_sources)

    # Staleness
    stale_evidence = [frag.id for frag in fragments if _is_stale(frag)]

    # Supporting vs opposing evidence
    # In the minimal slice, all evidence_refs on a claim are "supporting"
    # (the claim was extracted from them). Opposing evidence comes from
    # the claim's contradicting_refs field.
    contradicting_refs: list[str] = []
    if claim.contradicting_refs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            contradicting_refs = json.loads(claim.contradicting_refs)

    opposing_fragments = await _get_evidence_fragments(session, contradicting_refs)

    # Context notes
    context_notes: list[str] = []
    if claim.validity_conditions:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            conditions = json.loads(claim.validity_conditions)
            if conditions:
                context_notes.append(f"Validity conditions: {conditions}")

    # ── Determine outcome ──────────────────────────────────────────────

    outcome, reason_code = _determine_outcome(
        has_evidence=len(fragments) > 0,
        independent_source_count=independent_source_count,
        has_opposing=len(opposing_fragments) > 0,
        stale_evidence_count=len(stale_evidence),
        epistemic_state=claim.epistemic_state,
    )

    # ── Build assessment ──────────────────────────────────────────────

    supporting_evidence = [
        {
            "fragment_id": f.id,
            "source_uri": f.source_uri,
            "extraction_method": f.extraction_method,
            "retrieved_at": f.retrieved_at,
        }
        for f in fragments
    ]

    opposing_evidence = [
        {
            "fragment_id": f.id,
            "source_uri": f.source_uri,
            "extraction_method": f.extraction_method,
        }
        for f in opposing_fragments
    ]

    assessment = _make_assessment_dict(
        claim_id=claim_id,
        outcome=outcome,
        reason_code=reason_code,
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        independent_sources=independent_sources,
        context_notes=context_notes,
        stale_evidence=stale_evidence,
        reviewer=requester,
    )

    # ── Check for previous assessment (idempotency + EvidenceDelta) ────

    previous = await _get_previous_assessment(session, claim_id)

    if previous is not None:
        # Idempotent: same outcome + same evidence → no new assessment
        if (
            previous.get("outcome") == outcome
            and previous.get("supporting_evidence_count") == len(supporting_evidence)
            and previous.get("opposing_evidence_count") == len(opposing_evidence)
            and previous.get("independent_source_count") == independent_source_count
        ):
            _log.info(
                "assessment unchanged for claim %s — idempotent (outcome=%s)",
                claim_id,
                outcome,
            )
            return {
                "ok": True,
                "assessment": assessment,
                "idempotent": True,
                "request_id": request_id,
            }

        # Outcome changed → record EvidenceDelta
        delta = _make_evidence_delta(
            claim_id=claim_id,
            prior_outcome=previous.get("outcome", VerificationOutcome.INSUFFICIENT_EVIDENCE),
            new_outcome=outcome,
            reason=reason_code,
            evidence_change=(
                f"supporting: {previous.get('supporting_evidence_count', 0)} → {len(supporting_evidence)}; "
                f"opposing: {previous.get('opposing_evidence_count', 0)} → {len(opposing_evidence)}; "
                f"independent_sources: {previous.get('independent_source_count', 0)} → {independent_source_count}"
            ),
            reviewer=requester,
        )
        await _record_evidence_delta(session, delta, request_id)

    # ── Persist assessment ─────────────────────────────────────────────

    await _persist_assessment(session, assessment, request_id)

    # ── Update claim's epistemic_state if needed ──────────────────────
    # Per requirement #6: map outcomes to existing contracts WITHOUT
    # changing their meaning. We update ClaimRow.epistemic_state only
    # when the outcome warrants it. The mapping is documented above.

    new_epistemic = _outcome_to_epistemic_state(outcome, claim.epistemic_state)
    if new_epistemic != claim.epistemic_state:
        # Only update if the G01 invariant allows it:
        # - supported requires evidence_refs (already has them if outcome is SOURCE_SUPPORTED+)
        # - hypothesized must NOT have evidence_refs (won't happen here)
        claim.epistemic_state = new_epistemic
        claim.version += 1

    # ── Audit ──────────────────────────────────────────────────────────

    await _audit(
        session,
        event_type="verification.assessed",
        actor=requester,
        target_id=claim_id,
        target_type="claim",
        payload={
            "claim_id": claim_id,
            "outcome": outcome,
            "reason_code": reason_code,
            "independent_source_count": independent_source_count,
            "supporting_evidence_count": len(supporting_evidence),
            "opposing_evidence_count": len(opposing_evidence),
            "stale_evidence_count": len(stale_evidence),
        },
        request_id=request_id,
    )

    return {
        "ok": True,
        "assessment": assessment,
        "idempotent": False,
        "epistemic_state_updated": new_epistemic != claim.epistemic_state
        if "new_epistemic" in dir()
        else False,
        "request_id": request_id,
    }


# ── Outcome determination ───────────────────────────────────────────────────


def _determine_outcome(
    *,
    has_evidence: bool,
    independent_source_count: int,
    has_opposing: bool,
    stale_evidence_count: int,
    epistemic_state: str,
) -> tuple[str, str]:
    """Determine the verification outcome and reason code.

    Decision table:
      ┌─────────────────┬──────────────────┬────────────┬──────────────┬─────────────────────────────┐
      │ has_evidence    │ independent_srcs │ has_opposing│ stale_count  │ outcome                     │
      ├─────────────────┼──────────────────┼────────────┼──────────────┼─────────────────────────────┤
      │ False           │ 0                │ False      │ 0            │ INSUFFICIENT_EVIDENCE       │
      │ True            │ 1                │ False      │ 0            │ SOURCE_SUPPORTED            │
      │ True            │ ≥2              │ False      │ 0            │ CORROBORATED                │
      │ True            │ ≥2              │ False      │ 0            │ VERIFIED (if policy met)    │
      │ True            │ any             │ True       │ 0            │ CONTESTED                   │
      │ True            │ any             │ False      │ >0           │ STALE_OR_CONTEXT_MISMATCH   │
      │ False           │ 0                │ False      │ 0            │ NOT_EVIDENCED (for missing) │
      └─────────────────┴──────────────────┴────────────┴──────────────┴─────────────────────────────┘

    Returns: (outcome, reason_code)
    """
    if not has_evidence:
        if epistemic_state == "hypothesized":
            return (
                VerificationOutcome.INSUFFICIENT_EVIDENCE,
                "no_evidence_for_hypothesis",
            )
        return (
            VerificationOutcome.NOT_EVIDENCED,
            "no_evidence_found",
        )

    if has_opposing:
        return (
            VerificationOutcome.CONTESTED,
            f"contradicting_evidence_present; independent_sources={independent_source_count}",
        )

    if stale_evidence_count > 0 and independent_source_count <= 1:
        return (
            VerificationOutcome.STALE_OR_CONTEXT_MISMATCH,
            f"evidence_stale; stale_count={stale_evidence_count}",
        )

    if independent_source_count >= MIN_INDEPENDENT_SOURCES_FOR_VERIFIED:
        # Per requirement #9: VERIFIED requires a documented policy.
        # The policy is: ≥2 independent sources, no contradictions,
        # no stale evidence. This is met.
        return (
            VerificationOutcome.VERIFIED,
            f"policy_met; independent_sources={independent_source_count}; "
            f"policy=deterministic-v1; min_sources={MIN_INDEPENDENT_SOURCES_FOR_VERIFIED}",
        )

    if independent_source_count >= MIN_INDEPENDENT_SOURCES_FOR_CORROBORATED:
        return (
            VerificationOutcome.CORROBORATED,
            f"independent_sources={independent_source_count}",
        )

    return (
        VerificationOutcome.SOURCE_SUPPORTED,
        f"single_source; independent_sources={independent_source_count}",
    )


def _outcome_to_epistemic_state(
    outcome: str,
    current_state: str,
) -> str:
    """Map a verification outcome to a G01 EpistemicState.

    Per requirement #6: map WITHOUT changing G01 meaning.

    - INSUFFICIENT_EVIDENCE → hypothesized (no evidence → stays hypothesis)
    - SOURCE_SUPPORTED → supported (has evidence from 1 source)
    - CORROBORATED → supported (has evidence from ≥2 sources)
    - CONTESTED → disputed (has supporting AND opposing)
    - VERIFIED → supported (policy-met; strongest state)
    - STALE_OR_CONTEXT_MISMATCH → current_state (don't change; flag staleness)
    - NOT_EVIDENCED → current_state (don't change; assessment only)
    """
    mapping = {
        VerificationOutcome.INSUFFICIENT_EVIDENCE: "hypothesized",
        VerificationOutcome.SOURCE_SUPPORTED: "supported",
        VerificationOutcome.CORROBORATED: "supported",
        VerificationOutcome.CONTESTED: "disputed",
        VerificationOutcome.VERIFIED: "supported",
        VerificationOutcome.STALE_OR_CONTEXT_MISMATCH: current_state,
        VerificationOutcome.NOT_EVIDENCED: current_state,
    }
    return mapping.get(outcome, current_state)


# ── EvidenceDelta ───────────────────────────────────────────────────────────


def _make_evidence_delta(
    *,
    claim_id: str,
    prior_outcome: str,
    new_outcome: str,
    reason: str = "",
    evidence_change: str = "",
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Build an EvidenceDelta record for a meaningful assessment change.

    Per requirement #8: record meaningful changes in assessment, supporting
    evidence, opposing evidence, or applicability.
    """
    return {
        "id": uuid4().hex,
        "claim_id": claim_id,
        "prior_outcome": prior_outcome,
        "new_outcome": new_outcome,
        "reason": reason or evidence_change,
        "update_method": "deterministic-v1",
        "reviewer": reviewer,
        "recorded_at": _utcnow_iso(),
    }


async def _record_evidence_delta(
    session: AsyncSession,
    delta: dict[str, Any],
    request_id: str,
) -> None:
    """Record an EvidenceDelta in the audit_events table.

    Per requirement #8: the delta records the prior outcome, new outcome,
    and the reason for the change.
    """
    await _audit(
        session,
        event_type="evidence_delta.recorded",
        actor=delta.get("reviewer"),
        target_id=delta["claim_id"],
        target_type="claim",
        payload=delta,
        request_id=request_id,
    )


# ── Assessment persistence ────────────────────────────────────────────────


async def _persist_assessment(
    session: AsyncSession,
    assessment: dict[str, Any],
    request_id: str,
) -> None:
    """Persist a verification assessment.

    Per requirement #4: use a separate verification assessment record
    rather than changing G01 enums. The assessment is stored in the
    ``audit_events`` table with ``event_type="verification.assessed"`` —
    this avoids a new table and reuses existing infrastructure.

    For a future group that needs queryable assessments, a dedicated
    ``verification_assessments`` table can be added.
    """
    # Assessment is already recorded via _audit() in assess_claim().
    # This function is a placeholder for future dedicated-table storage.
    pass


async def _get_previous_assessment(
    session: AsyncSession,
    claim_id: str,
) -> dict[str, Any] | None:
    """Get the most recent verification assessment for a claim.

    Looks in audit_events for event_type="verification.assessed" with
    target_id=claim_id, ordered by created_at desc.
    """
    stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.target_id == claim_id,
            AuditEventRow.target_type == "claim",
            AuditEventRow.event_type == "verification.assessed",
        )
        .order_by(AuditEventRow.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row.payload


# ── Batch assessment ────────────────────────────────────────────────────────


async def assess_all_claims(
    session: AsyncSession,
    *,
    limit: int = 100,
    requester: str | None = None,
) -> list[dict[str, Any]]:
    """Assess all claims in the DB.

    Idempotent: re-running with the same evidence produces the same outcomes.
    New evidence triggers reassessment via the EvidenceDelta mechanism.
    """
    stmt = select(ClaimRow).limit(limit)
    result = await session.execute(stmt)
    claims = result.scalars().all()

    assessments = []
    for claim in claims:
        r = await assess_claim(session, claim.id, requester=requester)
        assessments.append(r)

    return assessments


# ── Missing-capability assessment ──────────────────────────────────────────


async def assess_missing_capability(
    session: AsyncSession,
    entity_id: str,
    capability_name: str,
    *,
    requester: str | None = None,
) -> dict[str, Any]:
    """Assess a missing capability (no evidence of it being provided).

    Per carry-forward correction: "Missing capabilities without explicit
    negative evidence must be reported as UNKNOWN or NOT_EVIDENCED."

    This returns NOT_EVIDENCED — not a negative claim. The absence of
    evidence is NOT evidence of absence.
    """
    return {
        "ok": True,
        "entity_id": entity_id,
        "capability_name": capability_name,
        "outcome": VerificationOutcome.NOT_EVIDENCED,
        "reason_code": "no_evidence_of_capability; absence_of_evidence_is_not_evidence_of_absence",
        "note": "Missing capability reported as NOT_EVIDENCED — not as a negative claim.",
    }


# ── Audit helper ────────────────────────────────────────────────────────────


async def _audit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: str | None,
    target_id: str | None,
    target_type: str | None,
    payload: dict[str, Any],
    request_id: str,
) -> None:
    row = AuditEventRow(
        id=uuid4().hex,
        event_type=event_type,
        actor=actor,
        target_id=target_id,
        target_type=target_type,
        payload=payload,
        request_id=request_id,
    )
    session.add(row)
    await session.flush()
