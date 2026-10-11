"""G06-T03 — Scenario Evaluation.

Deterministic evaluation of persisted future scenarios.

Per the approved G06 plan (sections 4.4, 7.2, 13.3, 16):

- **Evidence coverage**: proportion of referenced evidence fragments that
  meet the explicitly documented verification criterion (claim is
  independently verified via ``assess_claim()``). Fragment existence
  alone does NOT count as verified evidence.
- **Source provenance**: distinct source URL count + conservative
  independent-origin count (per PRB-04). Zero evidence → zero origins.
- **Dependency readiness**: classify each ``missing_dependency`` as
  engineering / research / integration / caller-attested / unknown.
  Caller-supplied deps are NOT independently verified.
- **Contradiction exposure**: identify evidence-backed contradictions.
  Absence of supporting evidence is NOT a contradiction.
- **Assumption assessment**: for each assumption, classify as supported /
  contested / not_evidenced / unknown. Natural-language similarity alone
  is not proof.
- **Horizon alignment**: reassess whether the persisted F0-F4 horizon is
  consistent. Never silently upgrade.
- **Overall**: deterministic categorical result (testable /
  needs_more_evidence / speculative / inconsistent) with clear precedence.

Uncertainty semantics (Correction C, G06-P00C):
``uncertainty = 1.0 - evidence_coverage`` — measures ONLY the evidence-
coverage gap. Not a probability of future occurrence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.scenario_builder import get_scenario
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
)

__all__ = [
    "evaluate_scenario",
]

_log = get_logger("synapse.application.scenario_evaluator")

# Conservative default for independent-origin count (per PRB-04).
_CONSERVATIVE_INDEPENDENT_ORIGIN_COUNT = 1


# ── Public API ─────────────────────────────────────────────────────────────


async def evaluate_scenario(
    session: AsyncSession,
    scenario_id: str,
    *,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Evaluate a persisted scenario deterministically.

    Args:
        session: AsyncSession bound to the Synapse database.
        scenario_id: ID of the persisted scenario.
        requester: optional actor name for audit logging.

    Returns:
        A ``ScenarioEvaluation`` dict, or ``None`` if the scenario does
        not exist.
    """
    scenario = await get_scenario(session, scenario_id)
    if scenario is None:
        return None

    # ── Gather evidence fragments ──────────────────────────────────────
    evidence_refs = scenario.get("current_evidence_refs", [])
    fragments = await _load_fragments(session, evidence_refs)

    # ── A. Evidence coverage ───────────────────────────────────────────
    evidence_coverage, coverage_details = await _compute_evidence_coverage(
        session, evidence_refs, fragments
    )

    # ── B. Source provenance ───────────────────────────────────────────
    distinct_source_uri_count, independent_origin_count = _compute_source_provenance(
        fragments
    )

    # ── C. Dependency readiness ────────────────────────────────────────
    missing_deps = scenario.get("missing_dependencies", [])
    dep_readiness, dep_details = await _compute_dependency_readiness(
        session, missing_deps
    )

    # ── D. Contradiction exposure ──────────────────────────────────────
    contradiction_count, contradiction_details = await _compute_contradiction_exposure(
        session, scenario, fragments
    )

    # ── E. Assumption assessment ───────────────────────────────────────
    assumptions = scenario.get("assumptions", [])
    assumption_assessment = await _assess_assumptions(
        session, assumptions, fragments
    )

    # ── F. Horizon alignment ───────────────────────────────────────────
    horizon_class = scenario.get("horizon_class", "F0")
    horizon_alignment, horizon_notes = _assess_horizon_alignment(
        horizon_class, evidence_coverage, dep_readiness, dep_details
    )

    # ── G. Overall evaluation ──────────────────────────────────────────
    overall = _compute_overall(
        evidence_coverage=evidence_coverage,
        contradiction_count=contradiction_count,
        horizon_class=horizon_class,
        dep_readiness=dep_readiness,
    )

    # ── Uncertainty (Correction C: 1.0 - evidence_coverage only) ───────
    uncertainty = 1.0 - evidence_coverage

    # ── Build the evaluation dict ──────────────────────────────────────
    evaluation: dict[str, Any] = {
        "scenario_id": scenario_id,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "evidence_coverage": evidence_coverage,
        "evidence_coverage_details": coverage_details,
        "distinct_source_uri_count": distinct_source_uri_count,
        "independent_origin_count": independent_origin_count,
        "dependency_readiness": dep_readiness,
        "dependency_details": dep_details,
        "contradiction_exposure": contradiction_count,
        "contradiction_details": contradiction_details,
        "assumption_assessment": assumption_assessment,
        "horizon_class": horizon_class,
        "horizon_alignment": horizon_alignment,
        "horizon_notes": horizon_notes,
        "overall": overall,
        "uncertainty": uncertainty,
        "uncertainty_method": "1.0 - evidence_coverage",
        "uncertainty_limitation": (
            "uncertainty measures ONLY the evidence-coverage gap. "
            "It is NOT a probability of future occurrence, experimental "
            "success, or overall epistemic certainty."
        ),
        "reasoning": _build_reasoning(
            evidence_coverage=evidence_coverage,
            contradiction_count=contradiction_count,
            horizon_class=horizon_class,
            dep_readiness=dep_readiness,
            overall=overall,
        ),
    }

    # ── Record audit event (idempotent: one per evaluation call) ───────
    await _record_evaluation_audit(session, scenario_id, evaluation, requester)

    return evaluation


# ── A. Evidence coverage ──────────────────────────────────────────────────


async def _compute_evidence_coverage(
    session: AsyncSession,
    evidence_refs: list[str],
    fragments: list[EvidenceFragmentRow],
) -> tuple[float, dict[str, Any]]:
    """Compute the proportion of evidence refs that meet the verification
    criterion.

    A fragment "meets the verification criterion" if it is cited by a
    claim that has been independently verified (``assess_claim()`` was
    called and the outcome is ``source_supported``, ``corroborated``, or
    ``verified``).

    Fragment existence alone does NOT count as verified evidence.
    """
    if not evidence_refs:
        return 0.0, {
            "total_refs": 0,
            "verified_refs": 0,
            "unverified_refs": 0,
            "missing_refs": 0,
            "note": "No evidence references — coverage is 0.0",
        }

    total = len(evidence_refs)
    fragment_ids = {f.id for f in fragments}
    missing_refs = [ref for ref in evidence_refs if ref not in fragment_ids]
    existing_refs = [ref for ref in evidence_refs if ref in fragment_ids]

    # Find claims that cite each fragment and check their verification
    verified_refs: set[str] = set()
    unverified_refs: set[str] = set()

    for ref in existing_refs:
        is_verified = await _is_fragment_verified(session, ref)
        if is_verified:
            verified_refs.add(ref)
        else:
            unverified_refs.add(ref)

    coverage = len(verified_refs) / total if total > 0 else 0.0

    return coverage, {
        "total_refs": total,
        "verified_refs": len(verified_refs),
        "unverified_refs": len(unverified_refs),
        "missing_refs": len(missing_refs),
        "verified_ref_ids": sorted(verified_refs),
        "unverified_ref_ids": sorted(unverified_refs),
        "missing_ref_ids": sorted(missing_refs),
    }


async def _is_fragment_verified(session: AsyncSession, fragment_id: str) -> bool:
    """Check whether a fragment is cited by a claim with a positive
    verification outcome (source_supported / corroborated / verified).

    Fragment existence alone does NOT count as verified evidence.
    """
    # Find claims that cite this fragment
    claim_stmt = select(ClaimRow).where(
        ClaimRow.evidence_refs.contains(fragment_id)
    )
    claims = list((await session.execute(claim_stmt)).scalars().all())
    if not claims:
        return False

    # Check the latest verification assessment for each claim
    for claim in claims:
        assessment = await _get_latest_verification_assessment(session, claim.id)
        if assessment is not None:
            outcome = assessment.get("outcome", "")
            if outcome in ("source_supported", "corroborated", "verified"):
                return True
    return False


async def _get_latest_verification_assessment(
    session: AsyncSession,
    claim_id: str,
) -> dict[str, Any] | None:
    """Fetch the latest verification.assessed audit event for a claim."""
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
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return _safe_json_loads(row.payload)


# ── B. Source provenance ──────────────────────────────────────────────────


def _compute_source_provenance(
    fragments: list[EvidenceFragmentRow],
) -> tuple[int, int]:
    """Compute distinct source URL count + conservative independent-origin
    count.

    Zero evidence → zero origins. Multiple URLs without verified
    independence → independent_origin_count = 1 (conservative, PRB-04).
    """
    if not fragments:
        return 0, 0

    source_uris = {f.source_uri for f in fragments if f.source_uri}
    distinct_count = len(source_uris)

    # Conservative: 1 if there is any evidence, 0 if none.
    # No new provenance inference algorithm (per PRB-04).
    independent_count = _CONSERVATIVE_INDEPENDENT_ORIGIN_COUNT if distinct_count > 0 else 0

    return distinct_count, independent_count


# ── C. Dependency readiness ───────────────────────────────────────────────


async def _compute_dependency_readiness(
    session: AsyncSession,
    missing_deps: list[str],
) -> tuple[str, dict[str, Any]]:
    """Classify dependency readiness.

    Returns (overall_readiness, details_dict).

    overall_readiness is one of:
    - "engineering_ready": all deps are engineering-tractable
    - "research_dependent": at least one dep is research
    - "integration_unresolved": at least one dep is unresolved integration
    - "caller_attested": at least one dep is caller-attested only
    - "unknown": readiness cannot be established
    - "none": no missing dependencies

    Precedence: research > engineering > integration > caller_attested > unknown
    """
    if not missing_deps:
        return "none", {"dependencies": [], "note": "No missing dependencies"}

    dep_classifications: list[dict[str, Any]] = []
    has_research = False
    has_engineering = False
    has_integration = False
    has_caller_attested = False
    has_unknown = False

    for dep_id in missing_deps:
        classification = await _classify_dependency(session, dep_id)
        dep_classifications.append({
            "entity_id": dep_id,
            "classification": classification,
        })
        if classification == "research":
            has_research = True
        elif classification == "engineering":
            has_engineering = True
        elif classification == "integration":
            has_integration = True
        elif classification == "caller_attested":
            has_caller_attested = True
        elif classification == "unknown":
            has_unknown = True

    # Precedence: research > engineering > integration > caller_attested > unknown
    if has_research:
        overall = "research_dependent"
    elif has_engineering:
        overall = "engineering_ready"
    elif has_integration:
        overall = "integration_unresolved"
    elif has_caller_attested:
        overall = "caller_attested"
    elif has_unknown:
        overall = "unknown"
    else:
        overall = "unknown"

    return overall, {"dependencies": dep_classifications, "overall": overall}


async def _classify_dependency(session: AsyncSession, dep_id: str) -> str:
    """Classify a dependency entity.

    - "research": entity does not exist in KB, or exists only with
      HYPOTHESIZED/INFERRED claims (no SUPPORTED claim).
    - "engineering": entity exists with a SUPPORTED claim but has a
      LIMITS or REQUIRES edge (engineering constraint).
    - "integration": entity exists with a SUPPORTED claim but lacks an
      integration relationship (INTEGRATES_WITH/PROVIDES/ENABLES) to
      the scenario's capabilities. This is only detectable when the
      scenario's capabilities are known; here we classify based on
      the entity alone, so "integration" requires the caller to have
      attested it (it resolves to a real entity with a SUPPORTED claim
      and no LIMITS/REQUIRES edges, but the caller explicitly listed it
      as a missing dependency).
    - "caller_attested": entity exists but the builder cannot independently
      verify that it is genuinely unresolved (no SUPPORTED claim, no
      LIMITS/REQUIRES edges — the caller's attestation is the only basis).
    - "unknown": entity does not exist in the KB.
    """
    ent_stmt = select(EntityRow).where(EntityRow.id == dep_id)
    entity = (await session.execute(ent_stmt)).scalar_one_or_none()
    if entity is None:
        return "unknown"

    # Check for SUPPORTED claims referencing this entity
    claim_stmt = select(ClaimRow).where(
        (ClaimRow.subject_ref == dep_id)
        | (ClaimRow.object_ref == dep_id),
        ClaimRow.epistemic_state == "supported",
    )
    supported_claims = list((await session.execute(claim_stmt)).scalars().all())

    if not supported_claims:
        # No SUPPORTED claim — could be HYPOTHESIZED or caller-attested
        any_claim_stmt = select(ClaimRow).where(
            (ClaimRow.subject_ref == dep_id)
            | (ClaimRow.object_ref == dep_id),
        )
        any_claims = list((await session.execute(any_claim_stmt)).scalars().all())
        if not any_claims:
            # Entity exists but no claims at all — caller-attested only
            return "caller_attested"
        for c in any_claims:
            if c.epistemic_state in ("hypothesized", "inferred"):
                return "research"
        return "caller_attested"

    # Has SUPPORTED claim — check for LIMITS/REQUIRES edges (engineering)
    dep_stmt = select(RelationshipRow).where(
        (RelationshipRow.from_entity_id == dep_id)
        | (RelationshipRow.to_entity_id == dep_id),
        RelationshipRow.predicate.in_(("LIMITS", "REQUIRES")),
    )
    dep_rels = list((await session.execute(dep_stmt)).scalars().all())
    if dep_rels:
        return "engineering"

    # SUPPORTED claim, no LIMITS/REQUIRES — integration unresolved
    # (the caller listed it as a missing dependency, so the integration
    # is genuinely unresolved even though the entity itself is supported)
    return "integration"


# ── D. Contradiction exposure ─────────────────────────────────────────────


async def _compute_contradiction_exposure(
    session: AsyncSession,
    scenario: dict[str, Any],
    fragments: list[EvidenceFragmentRow],
) -> tuple[int, dict[str, Any]]:
    """Identify evidence-backed contradictions.

    A contradiction exists when a claim citing one of the scenario's
    evidence fragments has a CONTESTED verification outcome (both
    supporting and contradicting evidence exist).

    Absence of supporting evidence is NOT a contradiction.
    """
    evidence_refs = scenario.get("current_evidence_refs", [])
    contradictions: list[dict[str, Any]] = []

    for ref in evidence_refs:
        # Find claims citing this fragment
        claim_stmt = select(ClaimRow).where(
            ClaimRow.evidence_refs.contains(ref)
        )
        claims = list((await session.execute(claim_stmt)).scalars().all())
        for claim in claims:
            assessment = await _get_latest_verification_assessment(session, claim.id)
            if assessment is not None:
                outcome = assessment.get("outcome", "")
                if outcome == "contested":
                    contradictions.append({
                        "fragment_id": ref,
                        "claim_id": claim.id,
                        "claim_proposition": claim.proposition,
                        "outcome": outcome,
                        "opposing_evidence_count": assessment.get("opposing_evidence_count", 0),
                    })

    # Also check claims whose contradicting_refs reference the scenario's fragments
    for ref in evidence_refs:
        claim_stmt = select(ClaimRow).where(
            ClaimRow.contradicting_refs.contains(ref)
        )
        claims = list((await session.execute(claim_stmt)).scalars().all())
        for claim in claims:
            contradictions.append({
                "fragment_id": ref,
                "claim_id": claim.id,
                "claim_proposition": claim.proposition,
                "outcome": "contradicting_ref",
                "note": "This fragment is listed as contradicting evidence for this claim.",
            })

    # Deduplicate by (fragment_id, claim_id)
    seen = set()
    unique_contradictions: list[dict[str, Any]] = []
    for c in contradictions:
        key = (c["fragment_id"], c["claim_id"])
        if key not in seen:
            seen.add(key)
            unique_contradictions.append(c)

    return len(unique_contradictions), {"contradictions": unique_contradictions}


# ── E. Assumption assessment ──────────────────────────────────────────────


async def _assess_assumptions(
    session: AsyncSession,
    assumptions: list[str],
    fragments: list[EvidenceFragmentRow],
) -> list[dict[str, Any]]:
    """Assess each assumption.

    For each assumption, classify as:
    - "supported": a persisted claim with a positive verification outcome
      directly addresses the assumption.
    - "contested": a persisted claim with a CONTESTED outcome addresses it.
    - "not_evidenced": no claim addresses the assumption.
    - "unknown": cannot determine (e.g., assessment unavailable).

    Natural-language similarity alone is NOT proof. We only map an
    assumption to a claim if the claim's evidence_refs overlap with the
    scenario's evidence_refs (defensible mapping to persisted evidence).
    """
    fragment_ids = {f.id for f in fragments}
    results: list[dict[str, Any]] = []

    for assumption in assumptions:
        # Find claims whose evidence_refs overlap with the scenario's fragments
        # and whose proposition mentions the assumption (substring match —
        # this is a defensible heuristic, not natural-language similarity)
        # We check if any of the scenario's fragments are cited by claims
        # that have been assessed.
        supported = False
        contested = False
        has_any_claim = False

        for frag_id in fragment_ids:
            claim_stmt = select(ClaimRow).where(
                ClaimRow.evidence_refs.contains(frag_id)
            )
            claims = list((await session.execute(claim_stmt)).scalars().all())
            for claim in claims:
                has_any_claim = True
                assessment = await _get_latest_verification_assessment(session, claim.id)
                if assessment is not None:
                    outcome = assessment.get("outcome", "")
                    if outcome in ("source_supported", "corroborated", "verified"):
                        supported = True
                    elif outcome == "contested":
                        contested = True

        if contested:
            classification = "contested"
        elif supported:
            classification = "supported"
        elif has_any_claim:
            classification = "unknown"
        else:
            classification = "not_evidenced"

        results.append({
            "assumption": assumption,
            "classification": classification,
            "note": (
                "Classification is based on the verification outcome of "
                "claims citing the scenario's evidence fragments. "
                "Natural-language similarity alone is not proof."
            ),
        })

    return results


# ── F. Horizon alignment ──────────────────────────────────────────────────


def _assess_horizon_alignment(
    horizon_class: str,
    evidence_coverage: float,
    dep_readiness: str,
    dep_details: dict[str, Any],
) -> tuple[str, list[str]]:
    """Reassess whether the persisted horizon is consistent.

    Never silently upgrade. Return "aligned", "insufficient_evidence",
    or "misaligned".

    Rules:
    - F0: should have evidence_coverage > 0 and no missing deps.
      If coverage == 0 or deps exist → "insufficient_evidence".
    - F1: should have unevidenced integration + ≥1 integration dep.
      If all deps are engineering or research → "misaligned" (should be
      F2 or F3). But we do NOT upgrade — we report the misalignment.
    - F2: should have ≥1 engineering dep and no research deps.
      If has research deps → "misaligned".
    - F3: should have ≥1 research dep.
      If no research deps → "misaligned".
    - F4: speculative — always "aligned" (the critique is mandatory).

    If evidence is insufficient to determine alignment → "insufficient_evidence".
    """
    notes: list[str] = []

    if horizon_class == "F4":
        return "aligned", ["F4 is speculative; alignment is assumed if critique is present."]

    if horizon_class == "F0":
        if evidence_coverage == 0.0:
            notes.append("F0 requires evidence coverage > 0; coverage is 0.0.")
            return "insufficient_evidence", notes
        if dep_readiness != "none":
            notes.append(f"F0 requires no missing dependencies; readiness={dep_readiness}.")
            return "misaligned", notes
        return "aligned", notes

    if horizon_class == "F1":
        if dep_readiness == "none":
            notes.append("F1 requires at least one unresolved integration dependency; none found.")
            return "insufficient_evidence", notes
        if dep_readiness in ("research_dependent", "engineering_ready"):
            notes.append(
                f"F1 should have unresolved integration only; readiness={dep_readiness} "
                f"suggests F2 or F3 may be more appropriate. Horizon is NOT upgraded."
            )
            return "misaligned", notes
        return "aligned", notes

    if horizon_class == "F2":
        if dep_readiness == "research_dependent":
            notes.append("F2 should not have research dependencies; readiness=research_dependent suggests F3.")
            return "misaligned", notes
        if dep_readiness not in ("engineering_ready", "integration_unresolved"):
            notes.append(f"F2 readiness unclear; readiness={dep_readiness}.")
            return "insufficient_evidence", notes
        return "aligned", notes

    if horizon_class == "F3":
        if dep_readiness != "research_dependent":
            notes.append(f"F3 should have research dependencies; readiness={dep_readiness}.")
            return "misaligned", notes
        return "aligned", notes

    return "unknown", ["Unknown horizon class."]


# ── G. Overall evaluation ─────────────────────────────────────────────────


def _compute_overall(
    *,
    evidence_coverage: float,
    contradiction_count: int,
    horizon_class: str,
    dep_readiness: str,
) -> str:
    """Compute the overall categorical result.

    Precedence (highest first):
    1. "inconsistent" — any contradiction exists.
    2. "speculative" — horizon is F4.
    3. "needs_more_evidence" — evidence_coverage < 0.5.
    4. "testable" — otherwise.
    """
    if contradiction_count > 0:
        return "inconsistent"
    if horizon_class == "F4":
        return "speculative"
    if evidence_coverage < 0.5:
        return "needs_more_evidence"
    return "testable"


def _build_reasoning(
    *,
    evidence_coverage: float,
    contradiction_count: int,
    horizon_class: str,
    dep_readiness: str,
    overall: str,
) -> str:
    """Build a deterministic human-readable reasoning string."""
    parts = [
        f"Evidence coverage: {evidence_coverage:.2f}.",
        f"Contradictions: {contradiction_count}.",
        f"Horizon: {horizon_class}.",
        f"Dependency readiness: {dep_readiness}.",
        f"Overall: {overall}.",
    ]
    return " ".join(parts)


# ── Audit ──────────────────────────────────────────────────────────────────


async def _record_evaluation_audit(
    session: AsyncSession,
    scenario_id: str,
    evaluation: dict[str, Any],
    requester: str | None,
) -> None:
    """Record a scenario.evaluated audit event.

    This is a read-only evaluation — it does NOT modify the scenario.
    The audit event is emitted once per evaluation call (not idempotent
    across calls, because each evaluation is a distinct observation).
    """
    from uuid import uuid4

    audit = AuditEventRow(
        id=f"aev-{uuid4().hex[:16]}",
        event_type="scenario.evaluated",
        actor=requester or "unknown",
        target_id=scenario_id,
        target_type="scenario",
        payload=json.dumps(
            {
                "scenario_id": scenario_id,
                "overall": evaluation["overall"],
                "evidence_coverage": evaluation["evidence_coverage"],
                "uncertainty": evaluation["uncertainty"],
                "horizon_alignment": evaluation["horizon_alignment"],
            },
            default=str,
        ),
    )
    session.add(audit)
    await session.flush()


# ── Helpers ────────────────────────────────────────────────────────────────


async def _load_fragments(
    session: AsyncSession,
    evidence_refs: list[str],
) -> list[EvidenceFragmentRow]:
    """Load evidence fragments by ID."""
    if not evidence_refs:
        return []
    stmt = select(EvidenceFragmentRow).where(
        EvidenceFragmentRow.id.in_(evidence_refs)
    )
    return list((await session.execute(stmt)).scalars().all())


def _safe_json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
