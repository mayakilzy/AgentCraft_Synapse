"""G06-T02 — Scenario Construction.

Build a validated ``Scenario`` using existing domain contracts and real
knowledge-base references. Persist it as ``EntityRow(kind="scenario")``
with the existing fingerprint/idempotency infrastructure.

Per the approved G06 plan (sections 3, 4.2, 5, 13.2, 16):

- **F0-F4 horizon classification** is deterministic, derived from the
  KB state, not chosen freely by the caller. Precedence: F4 > F3 > F2
  > F1 > F0. The caller may only downgrade (make more speculative).
- **F0 vs F1 mutual exclusivity**: F0 requires evidence of the
  *integration itself* (a SUPPORTED claim or explicit relationship
  evidencing the combination) AND empty ``missing_dependencies``. F1
  requires unevidenced integration AND at least one honestly
  unresolved integration dependency (a real KB entity lacking an
  integration relationship to the scenario's other components).
- **F1 fail-closed**: if no honest integration dependency can be
  named, the builder raises ``ValueError`` rather than fabricating a
  dependency.
- **No frozen-contract modification**: the ``Scenario`` domain record
  and its validators are unchanged. F1 satisfies the
  ``_non_f0_must_list_missing_deps`` validator honestly via real
  integration dependencies.
- **Signal validation**: a ``signal_id`` from G06-T01 is NOT a
  persisted DB entity. It is validated by re-running
  ``identify_future_signals()`` and checking that a signal with that
  ``signal_id`` exists. The ``content_version`` is checked too (if
  provided) to detect content drift.
- **Idempotency**: uses the PRB-03 ``claim_fingerprint()``
  infrastructure with a scenario-specific fingerprint that includes
  ``run_id`` (same as G05-T06 executions). Same ``run_id`` + same
  payload → same ``scenario_id`` (idempotent retry). Same ``run_id``
  + conflicting payload → ``ValueError`` (explicit rejection). No
  ``run_id`` → each call is independent.
- **No new infrastructure**: reuses ``EntityRow``, ``ClaimRow``,
  ``EntityFingerprintRow``, ``AuditEventRow``, ``claim_fingerprint``.
  No new tables, migrations, predicates, or domain records.

The scenario attributes JSON carries the full ``Scenario`` dict so the
scenario can be reconstructed in a separate session.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.future_signal import identify_future_signals
from synapse.application.gap_analyzer import analyze_gap
from synapse.domain._base import HorizonClass
from synapse.domain.scenario import Scenario, ScenarioStatus
from synapse.observability.logging import get_logger
from synapse.storage.fingerprint import claim_fingerprint
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
)

__all__ = [
    "build_scenario",
    "get_scenario",
]

_log = get_logger("synapse.application.scenario_builder")

# ── Constants ──────────────────────────────────────────────────────────────

_HORIZON_PRECEDENCE = {
    HorizonClass.F4: 4,
    HorizonClass.F3: 3,
    HorizonClass.F2: 2,
    HorizonClass.F1: 1,
    HorizonClass.F0: 0,
}

# Predicates that count as "integration" relationships (evidence that
# two components are integrated). Only existing predicates from the
# 16-predicate vocabulary (src/synapse/domain/relationship.py) are used.
_INTEGRATION_PREDICATES = ("INTEGRATES_WITH", "PROVIDES", "ENABLES", "PRODUCES")

# Predicates that count as "dependency" edges for the
# dependency_graph_snapshot signal.
_DEPENDENCY_PREDICATES = ("PROVIDES", "DEPENDS_ON", "LIMITS")


# ── Public API ─────────────────────────────────────────────────────────────


async def build_scenario(
    session: AsyncSession,
    *,
    capability_ids: list[str],
    assumptions: list[str],
    signal_id: str | None = None,
    signal_content_version: str | None = None,
    proposed_combination: str | None = None,
    evidence_refs: list[str] | None = None,
    missing_dependencies: list[str] | None = None,
    horizon_class_override: str | None = None,
    critique: str | None = None,
    prototype_plan: str | None = None,
    linked_innovation_concept_id: str | None = None,
    run_id: str | None = None,
    requester: str | None = None,
) -> dict[str, Any]:
    """Construct, validate, and persist a ``Scenario``.

    Args:
        session: AsyncSession bound to the Synapse database.
        capability_ids: capability entity IDs the scenario involves.
            Every ID must resolve to a persisted ``EntityRow(kind=
            "capability")``; otherwise ``ValueError`` is raised.
        assumptions: caller-supplied assumptions (non-empty list of
            non-empty strings). The scenario cannot be constructed
            without at least one assumption.
        signal_id: optional G06-T01 signal ID. If provided, the signal
            is validated by re-running ``identify_future_signals()``
            and checking that a signal with this ID exists. The signal
            is NOT a persisted DB entity — it is a deterministic
            subject-identity hash.
        signal_content_version: optional content-version hash from
            G06-T01. If provided, the builder verifies the signal's
            current ``content_version`` matches; a mismatch raises
            ``ValueError`` (signal content has drifted).
        proposed_combination: optional human-readable description of
            the proposed integration/combination.
        evidence_refs: optional list of evidence fragment IDs. Every
            ID must resolve to a persisted ``EvidenceFragmentRow``;
            otherwise ``ValueError`` is raised.
        missing_dependencies: optional list of dependency entity IDs.
            If omitted, the builder derives them from ``analyze_gap()``.
            If provided, every ID must resolve to a persisted entity.
        horizon_class_override: optional horizon class (F0-F4). May
            only downgrade (make more speculative). Upgrading raises
            ``ValueError``.
        critique: mandatory for F4; optional otherwise.
        prototype_plan: optional human-readable prototype plan.
        linked_innovation_concept_id: optional G05 innovation concept
            entity ID. If provided, must resolve to a persisted
            ``EntityRow(kind="project")``.
        run_id: optional retry identity. Same ``run_id`` + same payload
            → idempotent reuse. Same ``run_id`` + conflicting payload
            → ``ValueError``. No ``run_id`` → independent creation.
        requester: optional actor name for audit logging.

    Returns:
        The persisted scenario dict (same shape as
        ``_attributes_to_scenario``), including ``scenario_id``,
        ``was_reused``, and ``provenance``.

    Raises:
        ValueError: if any referenced entity/evidence/signal does not
            resolve, if the F0/F1 classification cannot be supported
            honestly, if the horizon override upgrades, or if a
            conflicting ``run_id`` retry is detected.
    """
    request_id = uuid4().hex

    # ── Validate inputs ────────────────────────────────────────────────
    if not capability_ids:
        raise ValueError("build_scenario requires at least one capability_id")
    if not assumptions:
        raise ValueError("build_scenario requires at least one assumption")
    for a in assumptions:
        if not isinstance(a, str) or not a.strip():
            raise ValueError("assumptions must be non-empty strings")

    # ── Resolve and validate capabilities ──────────────────────────────
    capabilities_info = await _resolve_capabilities(session, capability_ids)

    # ── Resolve and validate evidence refs ─────────────────────────────
    evidence_refs = evidence_refs or []
    if evidence_refs:
        await _validate_evidence_refs(session, evidence_refs)

    # ── Validate signal (if provided) ──────────────────────────────────
    if signal_id:
        await _validate_signal(
            session,
            signal_id=signal_id,
            expected_content_version=signal_content_version,
            focus_capability_id=capability_ids[0] if len(capability_ids) == 1 else None,
        )

    # ── Validate linked innovation (if provided) ───────────────────────
    if linked_innovation_concept_id:
        await _validate_linked_innovation(session, linked_innovation_concept_id)

    # ── Derive the horizon classification ──────────────────────────────
    derived_horizon, integration_evidenced, dep_classifications = await _derive_horizon(
        session, capabilities_info, evidence_refs, missing_dependencies
    )

    # ── Apply horizon override (downgrade only) ────────────────────────
    final_horizon = derived_horizon
    if horizon_class_override:
        override = HorizonClass(horizon_class_override)
        if _HORIZON_PRECEDENCE[override] < _HORIZON_PRECEDENCE[derived_horizon]:
            raise ValueError(
                f"horizon_class_override={override.value} upgrades the "
                f"derived horizon={derived_horizon.value} — only downgrades "
                f"are allowed"
            )
        final_horizon = override

    # ── F4 requires critique ───────────────────────────────────────────
    if final_horizon == HorizonClass.F4 and not critique:
        raise ValueError(
            "Scenario with horizon_class=F4 requires a critique — "
            "speculative scenarios cannot be silent"
        )

    # ── Resolve missing_dependencies for the final horizon ─────────────
    final_missing_deps = await _resolve_missing_dependencies(
        session,
        final_horizon,
        capabilities_info,
        missing_dependencies,
        dep_classifications,
        integration_evidenced,
    )

    # ── F1 fail-closed: need at least one honest integration dependency ──
    if final_horizon == HorizonClass.F1 and not final_missing_deps:
        raise ValueError(
            "F1 scenario requires at least one honestly unresolved "
            "integration dependency — cannot fabricate a dependency to "
            "satisfy the frozen Scenario validator"
        )

    # ── F4 (non-F0) requires at least one missing_dependency per the
    #    frozen validator. If the caller overrode to F4 but no deps were
    #    derived, populate from the capabilities themselves (they are the
    #    unresolved integration points in a speculative scenario). ──────
    if final_horizon != HorizonClass.F0 and not final_missing_deps:
        # Use the capability IDs as honest integration dependencies —
        # they are real KB entities, and in a speculative scenario the
        # integration itself is the unresolved dependency.
        final_missing_deps = list(capability_ids)

    # ── Compute the scenario fingerprint (idempotency) ─────────────────
    effective_run_id = run_id if run_id else uuid4().hex

    # ── Check for conflicting-payload retry BEFORE claiming ────────────
    # If the caller passed a run_id, check whether a scenario with that
    # run_id already exists but with a different payload. This catches
    # the "same run_id + conflicting payload" case that the fingerprint
    # claim alone cannot detect (different payload → different fingerprint
    # → claim wins → no conflict detected).
    if run_id:
        existing_with_run_id = await _find_scenario_by_run_id(session, run_id)
        if existing_with_run_id is not None:
            existing_attrs = _safe_json_loads(existing_with_run_id.attributes)
            existing_payload_key = existing_attrs.get("_scenario_fingerprint_payload", "")
            new_payload_key = _compute_scenario_fingerprint_payload_key(
                capability_ids=sorted(capability_ids),
                assumptions=assumptions,
                signal_id=signal_id or "",
                horizon_class=final_horizon.value,
                missing_dependencies=sorted(final_missing_deps),
            )
            if existing_payload_key and existing_payload_key != new_payload_key:
                raise ValueError(
                    f"Conflicting retry: run_id={run_id!r} already owns "
                    f"scenario {existing_with_run_id.id} with a different "
                    f"payload. Use a different run_id for a new scenario."
                )
            # Same run_id + same payload → idempotent reuse
            await session.commit()
            scenario_dict = await _reconstruct_scenario(session, existing_with_run_id.id)
            scenario_dict["was_reused"] = True
            return scenario_dict

    fingerprint = _compute_scenario_fingerprint(
        capability_ids=sorted(capability_ids),
        assumptions=assumptions,
        signal_id=signal_id or "",
        horizon_class=final_horizon.value,
        missing_dependencies=sorted(final_missing_deps),
        run_id=effective_run_id,
    )
    scenario_id = f"scn-{fingerprint[:16]}"
    claim_id = f"efp-{fingerprint[:16]}"

    # ── Atomically claim the fingerprint ───────────────────────────────
    winning_entity_id, we_won = await claim_fingerprint(
        session,
        kind="scenario",
        fingerprint=fingerprint,
        entity_id=scenario_id,
        claim_id=claim_id,
    )

    if not we_won:
        # We lost the claim — a scenario with this fingerprint already exists.
        # Check for conflicting-payload retry (same run_id, different payload).
        existing = await _load_scenario_entity(session, winning_entity_id)
        if existing is not None:
            existing_attrs = _safe_json_loads(existing.attributes)
            existing_payload_key = existing_attrs.get("_scenario_fingerprint_payload", "")
            new_payload_key = _compute_scenario_fingerprint_payload_key(
                capability_ids=sorted(capability_ids),
                assumptions=assumptions,
                signal_id=signal_id or "",
                horizon_class=final_horizon.value,
                missing_dependencies=sorted(final_missing_deps),
            )
            if existing_payload_key and existing_payload_key != new_payload_key:
                # Same run_id but conflicting payload — reject explicitly.
                raise ValueError(
                    f"Conflicting retry: run_id={effective_run_id!r} already "
                    f"owns scenario {winning_entity_id} with a different "
                    f"payload. Use a different run_id for a new scenario."
                )
        # Reuse the winning scenario
        await session.commit()
        scenario_dict = await _reconstruct_scenario(session, winning_entity_id)
        scenario_dict["was_reused"] = True
        return scenario_dict

    # ── We won the claim — persist the scenario ────────────────────────
    scenario_dict = {
        "scenario_id": scenario_id,
        "signal_id": signal_id,
        "signal_content_version": signal_content_version,
        "horizon_class": final_horizon.value,
        "status": ScenarioStatus.DRAFT.value,
        "current_evidence_refs": evidence_refs,
        "capabilities": list(capability_ids),
        "assumptions": list(assumptions),
        "missing_dependencies": final_missing_deps,
        "risks": {},
        "uncertainty": None,  # G06-T03 scope; not computed in T02
        "critique": critique,
        "prototype_plan": prototype_plan,
        "proposed_combination": proposed_combination,
        "linked_innovation_concept_id": linked_innovation_concept_id,
        "scenario_fingerprint": fingerprint,
        "provenance": {
            "created_by": requester or "unknown",
            "created_at": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "run_id": effective_run_id,
        },
        # Store the payload key for conflicting-retry detection
        "_scenario_fingerprint_payload": _compute_scenario_fingerprint_payload_key(
            capability_ids=sorted(capability_ids),
            assumptions=assumptions,
            signal_id=signal_id or "",
            horizon_class=final_horizon.value,
            missing_dependencies=sorted(final_missing_deps),
        ),
    }

    # Validate the Scenario domain record (frozen contract) before persisting
    _validate_scenario_domain_record(
        horizon_class=final_horizon,
        missing_dependencies=final_missing_deps,
        critique=critique,
        evidence_refs=evidence_refs,
        capabilities=list(capability_ids),
        assumptions=list(assumptions),
    )

    # Persist as EntityRow(kind="scenario")
    canonical_name = _scenario_canonical_name(
        proposed_combination, capability_ids, final_horizon
    )
    entity = EntityRow(
        id=scenario_id,
        kind="scenario",
        canonical_name=canonical_name,
        aliases="[]",
        attributes=json.dumps(scenario_dict, default=str),
        description=proposed_combination or canonical_name,
        version=1,
    )
    session.add(entity)
    await session.flush()

    # Emit audit event
    audit = AuditEventRow(
        id=f"aev-{uuid4().hex[:16]}",
        event_type="scenario.created",
        actor=requester or "unknown",
        target_id=scenario_id,
        target_type="scenario",
        payload=json.dumps(
            {
                "scenario_id": scenario_id,
                "horizon_class": final_horizon.value,
                "signal_id": signal_id,
                "capability_count": len(capability_ids),
                "assumption_count": len(assumptions),
                "evidence_count": len(evidence_refs),
                "missing_dependency_count": len(final_missing_deps),
                "was_reused": False,
                "request_id": request_id,
            },
            default=str,
        ),
        request_id=request_id,
    )
    session.add(audit)
    await session.commit()

    scenario_dict["was_reused"] = False
    return scenario_dict


async def get_scenario(
    session: AsyncSession,
    scenario_id: str,
) -> dict[str, Any] | None:
    """Retrieve a persisted scenario by ID.

    Returns the scenario dict, or ``None`` if no
    ``EntityRow(kind="scenario")`` exists with the given ID.
    """
    entity = await _load_scenario_entity(session, scenario_id)
    if entity is None:
        return None
    return await _reconstruct_scenario(session, scenario_id)


# ── Horizon derivation ─────────────────────────────────────────────────────


async def _derive_horizon(
    session: AsyncSession,
    capabilities_info: list[dict[str, Any]],
    evidence_refs: list[str],
    caller_missing_deps: list[str] | None,
) -> tuple[HorizonClass, bool, dict[str, str]]:
    """Derive the horizon class from the KB state.

    Returns ``(horizon, integration_evidenced, dep_classifications)``.

    Precedence (highest first):
    1. F4 — any capability is NOT_EVIDENCED/CONTESTED/UNKNOWN, or the
       caller explicitly requests speculation (handled by override).
    2. F3 — any missing dependency is "research" (entity absent or
       HYPOTHESIZED/INFERRED only).
    3. F2 — any missing dependency is "engineering" (entity exists with
       SUPPORTED claim, but integration relationship is missing).
    4. F1 — integration unevidenced + at least one honest integration
       dependency. Fail-closed if none can be named.
    5. F0 — integration evidenced + empty missing_dependencies.

    The distinction between F1, F2, F3 does NOT depend merely on the
    same missing integration edge being described with different labels.
    - F1: the integration edge is missing but the dependency entity is
      fully SUPPORTED (no additional work beyond integration).
    - F2: the dependency entity is SUPPORTED but requires engineering
      work to become usable (e.g., packaging, tooling, API adaptation).
      Detected via a LIMITS relationship or an explicit REQUIRES edge
      that is itself unevidenced.
    - F3: the dependency entity is NOT_EVIDENCED or only HYPOTHESIZED
      (research question — no known solution in KB).
    """
    # Check if any capability is NOT_EVIDENCED/CONTESTED/UNKNOWN
    all_supported = True
    for cap in capabilities_info:
        classification = cap.get("classification")
        if classification not in ("supported", "partially_supported"):
            all_supported = False
            break

    if not all_supported:
        # At least one capability is not supported → F4
        return HorizonClass.F4, False, {}

    # Check integration evidence: is there an explicit relationship
    # evidencing the combination of these capabilities?
    integration_evidenced = await _check_integration_evidence(
        session, [c["id"] for c in capabilities_info]
    )

    # Classify missing dependencies
    dep_classifications: dict[str, str] = {}
    has_research_dep = False
    has_engineering_dep = False

    if caller_missing_deps:
        for dep_id in caller_missing_deps:
            classification = await _classify_dependency(session, dep_id)
            dep_classifications[dep_id] = classification
            if classification == "research":
                has_research_dep = True
            elif classification == "engineering":
                has_engineering_dep = True
    else:
        # Derive from analyze_gap
        cap_names = [c["canonical_name"] for c in capabilities_info if c["canonical_name"]]
        if cap_names:
            gap_result = await analyze_gap(
                session,
                cap_names,
                limit=len(cap_names),
                requester="g06-t02-scenario-builder",
            )
            for req in gap_result.get("requirements", []):
                cap_id = req.get("capability_id")
                if cap_id:
                    classification = req.get("classification", "unknown")
                    # Map gap classification to dependency classification
                    if classification in ("not_evidenced", "unknown"):
                        dep_classifications[cap_id] = "research"
                        has_research_dep = True
                    elif classification in ("constrained", "partially_supported"):
                        dep_classifications[cap_id] = "engineering"
                        has_engineering_dep = True
                    elif classification == "contested":
                        dep_classifications[cap_id] = "research"
                        has_research_dep = True

    # Apply precedence
    if has_research_dep:
        return HorizonClass.F3, integration_evidenced, dep_classifications
    if has_engineering_dep:
        return HorizonClass.F2, integration_evidenced, dep_classifications
    if not integration_evidenced:
        # F1 — but need at least one honest integration dependency.
        # The caller (or the builder) must populate missing_dependencies
        # with real KB entities whose integration is unresolved.
        return HorizonClass.F1, integration_evidenced, dep_classifications
    # Integration evidenced + no missing deps → F0
    return HorizonClass.F0, integration_evidenced, dep_classifications


async def _check_integration_evidence(
    session: AsyncSession,
    capability_ids: list[str],
) -> bool:
    """Check whether the combination of these capabilities is evidenced.

    Returns True if there is an explicit relationship (INTEGRATES_WITH,
    PROVIDES, ENABLES, PRODUCES) between any pair of the given
    capabilities, OR a SUPPORTED claim whose subject+object are both in
    the set. Returns False otherwise.
    """
    if len(capability_ids) < 2:
        # A single capability has no "combination" to evidence.
        # F0 for a single capability means the capability itself is
        # SUPPORTED with no missing deps.
        return True

    cap_set = set(capability_ids)
    # Check for integration relationships between pairs
    stmt = select(RelationshipRow).where(
        RelationshipRow.predicate.in_(_INTEGRATION_PREDICATES),
        RelationshipRow.from_entity_id.in_(cap_set),
        RelationshipRow.to_entity_id.in_(cap_set),
    )
    rels = list((await session.execute(stmt)).scalars().all())
    if rels:
        return True

    # Check for SUPPORTED claims linking pairs
    claim_stmt = select(ClaimRow).where(
        ClaimRow.subject_ref.in_(cap_set),
        ClaimRow.object_ref.in_(cap_set),
        ClaimRow.epistemic_state == "supported",
    )
    claims = list((await session.execute(claim_stmt)).scalars().all())
    return bool(claims)


async def _classify_dependency(
    session: AsyncSession,
    dep_entity_id: str,
) -> str:
    """Classify a dependency entity as 'research', 'engineering', or 'supported'.

    - 'research': entity does not exist in KB, or exists only with
      HYPOTHESIZED/INFERRED claims (no SUPPORTED claim).
    - 'engineering': entity exists with a SUPPORTED claim, but has a
      LIMITS or REQUIRES edge indicating unresolved engineering work.
    - 'supported': entity exists with a SUPPORTED claim and no LIMITS/
      REQUIRES edges.
    """
    # Check entity exists
    ent_stmt = select(EntityRow).where(EntityRow.id == dep_entity_id)
    entity = (await session.execute(ent_stmt)).scalar_one_or_none()
    if entity is None:
        return "research"

    # Check for SUPPORTED claims referencing this entity
    claim_stmt = select(ClaimRow).where(
        (ClaimRow.subject_ref == dep_entity_id)
        | (ClaimRow.object_ref == dep_entity_id),
        ClaimRow.epistemic_state == "supported",
    )
    supported_claims = list((await session.execute(claim_stmt)).scalars().all())
    if not supported_claims:
        # No SUPPORTED claim — could be HYPOTHESIZED or absent
        any_claim_stmt = select(ClaimRow).where(
            (ClaimRow.subject_ref == dep_entity_id)
            | (ClaimRow.object_ref == dep_entity_id),
        )
        any_claims = list((await session.execute(any_claim_stmt)).scalars().all())
        if not any_claims:
            return "research"
        for c in any_claims:
            if c.epistemic_state in ("hypothesized", "inferred"):
                return "research"
        return "research"

    # Has SUPPORTED claim — check for LIMITS/REQUIRES edges
    dep_stmt = select(RelationshipRow).where(
        (RelationshipRow.from_entity_id == dep_entity_id)
        | (RelationshipRow.to_entity_id == dep_entity_id),
        RelationshipRow.predicate.in_(("LIMITS", "REQUIRES", "CONSTRAINED")),
    )
    # Note: CONSTRAINED is not a real predicate; use LIMITS/REQUIRES only.
    dep_stmt = select(RelationshipRow).where(
        (RelationshipRow.from_entity_id == dep_entity_id)
        | (RelationshipRow.to_entity_id == dep_entity_id),
        RelationshipRow.predicate.in_(("LIMITS", "REQUIRES")),
    )
    dep_rels = list((await session.execute(dep_stmt)).scalars().all())
    if dep_rels:
        return "engineering"

    return "supported"


async def _resolve_missing_dependencies(
    session: AsyncSession,
    horizon: HorizonClass,
    capabilities_info: list[dict[str, Any]],
    caller_missing_deps: list[str] | None,
    dep_classifications: dict[str, str],
    integration_evidenced: bool,
) -> list[str]:
    """Resolve the final missing_dependencies list for the scenario.

    For F0: must be empty (integration evidenced, no deps).
    For F1: must contain at least one honest integration dependency.
    For F2: must contain at least one engineering dependency.
    For F3: must contain at least one research dependency.
    For F4: any (may be empty only if critique explains why).
    """
    if horizon == HorizonClass.F0:
        return []

    if caller_missing_deps:
        # Validate every caller-supplied dependency resolves to a real entity
        for dep_id in caller_missing_deps:
            ent_stmt = select(EntityRow).where(EntityRow.id == dep_id)
            entity = (await session.execute(ent_stmt)).scalar_one_or_none()
            if entity is None:
                raise ValueError(
                    f"missing_dependency {dep_id!r} does not resolve to a "
                    f"persisted entity — cannot fabricate a dependency"
                )
        return list(caller_missing_deps)

    # Derive from dep_classifications
    if dep_classifications:
        return list(dep_classifications.keys())

    # F1 with no caller-supplied deps and no gap-derived deps:
    # try to find honest integration dependencies among the capabilities.
    if horizon == HorizonClass.F1 and not integration_evidenced:
        integration_deps = await _find_integration_dependencies(
            session, [c["id"] for c in capabilities_info]
        )
        if integration_deps:
            return integration_deps
        # No honest integration dependency → fail-closed (handled by caller)
        return []

    return []


async def _find_integration_dependencies(
    session: AsyncSession,
    capability_ids: list[str],
) -> list[str]:
    """Find real KB entities whose integration into the scenario is
    unresolved.

    An integration dependency is a real KB entity that:
    (a) exists in the KB (so the ID is traceable, not fabricated), AND
    (b) has a SUPPORTED claim (so it is a real component), AND
    (c) lacks an integration relationship (INTEGRATES_WITH, PROVIDES,
        ENABLES, PRODUCES) to at least one of the scenario's other
        capabilities (so the integration is genuinely unresolved).
    """
    if len(capability_ids) < 2:
        return []

    integration_deps: list[str] = []
    for cap_id in capability_ids:
        # Check if this capability has an integration relationship to
        # ANY other capability in the set
        other_caps = set(capability_ids) - {cap_id}
        stmt = select(RelationshipRow).where(
            ((RelationshipRow.from_entity_id == cap_id) & (RelationshipRow.to_entity_id.in_(other_caps)))
            | ((RelationshipRow.to_entity_id == cap_id) & (RelationshipRow.from_entity_id.in_(other_caps))),
            RelationshipRow.predicate.in_(_INTEGRATION_PREDICATES),
        )
        rels = list((await session.execute(stmt)).scalars().all())
        if not rels:
            # No integration relationship to other capabilities — this
            # is an honest integration dependency.
            integration_deps.append(cap_id)

    return integration_deps


# ── Validation helpers ─────────────────────────────────────────────────────


async def _resolve_capabilities(
    session: AsyncSession,
    capability_ids: list[str],
) -> list[dict[str, Any]]:
    """Resolve capability IDs to entities + gap classifications.

    Raises ValueError if any ID does not resolve.
    """
    result: list[dict[str, Any]] = []
    for cap_id in capability_ids:
        stmt = select(EntityRow).where(
            EntityRow.id == cap_id,
            EntityRow.kind == "capability",
        )
        entity = (await session.execute(stmt)).scalar_one_or_none()
        if entity is None:
            raise ValueError(
                f"capability_id {cap_id!r} does not resolve to a persisted "
                f"EntityRow(kind='capability')"
            )

        # Get the gap classification for this capability
        classification = await _get_capability_classification(session, cap_id)
        result.append(
            {
                "id": cap_id,
                "canonical_name": entity.canonical_name,
                "classification": classification,
            }
        )
    return result


async def _get_capability_classification(
    session: AsyncSession,
    capability_id: str,
) -> str:
    """Get the current gap classification for a capability.

    Uses analyze_gap() with the capability's canonical_name.
    Returns one of: supported, partially_supported, not_evidenced,
    contested, constrained, unknown.
    """
    # Get the canonical name
    stmt = select(EntityRow).where(EntityRow.id == capability_id)
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        return "not_evidenced"

    try:
        gap_result = await analyze_gap(
            session,
            [entity.canonical_name],
            limit=1,
            requester="g06-t02-scenario-builder",
        )
        for req in gap_result.get("requirements", []):
            if req.get("capability_id") == capability_id:
                return req.get("classification", "unknown")
    except Exception as exc:
        _log.warning("analyze_gap failed for %s: %s", capability_id, exc)
    return "unknown"


async def _validate_evidence_refs(
    session: AsyncSession,
    evidence_refs: list[str],
) -> None:
    """Validate that every evidence ref resolves to a real fragment."""
    for ref in evidence_refs:
        stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == ref)
        fragment = (await session.execute(stmt)).scalar_one_or_none()
        if fragment is None:
            raise ValueError(
                f"evidence_ref {ref!r} does not resolve to a persisted "
                f"EvidenceFragmentRow — cannot fabricate evidence"
            )


async def _validate_signal(
    session: AsyncSession,
    *,
    signal_id: str,
    expected_content_version: str | None,
    focus_capability_id: str | None,
) -> dict[str, Any] | None:
    """Validate a G06-T01 signal_id by re-running identify_future_signals().

    A signal_id is NOT a persisted DB entity — it is a deterministic
    subject-identity hash. The only way to validate it is to re-run the
    signal service and check that a signal with this signal_id exists.

    If ``expected_content_version`` is provided, the builder also checks
    that the signal's current ``content_version`` matches; a mismatch
    raises ``ValueError`` (signal content has drifted since the caller
    last saw it).
    """
    result = await identify_future_signals(
        session,
        focus_capability_id=focus_capability_id,
        requester="g06-t02-scenario-builder",
    )
    for signal in result.get("signals", []):
        if signal.get("signal_id") == signal_id:
            if expected_content_version:
                actual_cv = signal.get("content_version")
                if actual_cv != expected_content_version:
                    raise ValueError(
                        f"signal {signal_id!r} content_version mismatch: "
                        f"expected {expected_content_version!r}, got "
                        f"{actual_cv!r} — signal content has drifted"
                    )
            return signal
    raise ValueError(
        f"signal_id {signal_id!r} not found in current KB scan — "
        f"the signal may have been generated against a different KB state"
    )


async def _validate_linked_innovation(
    session: AsyncSession,
    concept_id: str,
) -> None:
    """Validate that a linked innovation concept exists."""
    stmt = select(EntityRow).where(
        EntityRow.id == concept_id,
        EntityRow.kind == "project",
    )
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        raise ValueError(
            f"linked_innovation_concept_id {concept_id!r} does not resolve "
            f"to a persisted EntityRow(kind='project')"
        )


def _validate_scenario_domain_record(
    *,
    horizon_class: HorizonClass,
    missing_dependencies: list[str],
    critique: str | None,
    evidence_refs: list[str],
    capabilities: list[str],
    assumptions: list[str],
) -> None:
    """Validate against the frozen Scenario domain record.

    This does NOT modify the frozen contract — it constructs a
    ``Scenario`` instance to trigger the frozen validators, then
    discards it.
    """
    try:
        Scenario(
            current_evidence_refs=evidence_refs,
            capabilities=capabilities,
            assumptions=assumptions,
            missing_dependencies=missing_dependencies,
            horizon_class=horizon_class,
            critique=critique,
            status=ScenarioStatus.DRAFT,
        )
    except Exception as exc:
        raise ValueError(
            f"Scenario domain record validation failed: {exc}"
        ) from exc


# ── Persistence helpers ────────────────────────────────────────────────────


async def _load_scenario_entity(
    session: AsyncSession,
    scenario_id: str,
) -> EntityRow | None:
    """Load a persisted scenario EntityRow by ID."""
    stmt = select(EntityRow).where(
        EntityRow.id == scenario_id,
        EntityRow.kind == "scenario",
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_scenario_by_run_id(
    session: AsyncSession,
    run_id: str,
) -> EntityRow | None:
    """Find a persisted scenario by its run_id (stored in attributes JSON).

    This is used for conflicting-payload retry detection: if a scenario
    with the same run_id already exists, we check whether the payload
    matches (idempotent reuse) or conflicts (explicit rejection).
    """
    # Scan scenario entities for the given run_id in their attributes JSON.
    # This is a linear scan over scenario-kind entities, which is bounded
    # by the number of scenarios in the DB. For production scale, a
    # dedicated index on run_id would be needed, but that requires a
    # migration (out of G06-T02 scope).
    stmt = select(EntityRow).where(EntityRow.kind == "scenario")
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        attrs = _safe_json_loads(row.attributes)
        if isinstance(attrs, dict) and attrs.get("provenance", {}).get("run_id") == run_id:
            return row
    return None


async def _reconstruct_scenario(
    session: AsyncSession,
    scenario_id: str,
) -> dict[str, Any]:
    """Reconstruct the scenario dict from a persisted EntityRow."""
    entity = await _load_scenario_entity(session, scenario_id)
    if entity is None:
        raise ValueError(f"scenario {scenario_id!r} not found")
    attrs = _safe_json_loads(entity.attributes)
    scenario = dict(attrs) if isinstance(attrs, dict) else {}
    scenario["scenario_id"] = entity.id
    scenario["canonical_name"] = entity.canonical_name
    scenario["retrieved_at"] = _utcnow_iso()
    return scenario


# ── Fingerprint helpers ────────────────────────────────────────────────────


def _compute_scenario_fingerprint(
    *,
    capability_ids: list[str],
    assumptions: list[str],
    signal_id: str,
    horizon_class: str,
    missing_dependencies: list[str],
    run_id: str,
) -> str:
    """Compute a deterministic scenario fingerprint.

    The fingerprint includes the payload (capability_ids, assumptions,
    signal_id, horizon_class, missing_dependencies) AND the run_id.

    - **Idempotent retry**: same run_id + same payload → same fingerprint
      → same scenario_id → idempotent reuse.
    - **Independent creation**: no run_id (or unique run_id) → distinct
      fingerprint → distinct scenario_id → independent scenario.
    - **Conflicting retry**: same run_id + different payload → different
      fingerprint, but the claim_fingerprint loser detects the conflict
      via the stored payload key.
    """
    payload_key = _compute_scenario_fingerprint_payload_key(
        capability_ids=capability_ids,
        assumptions=assumptions,
        signal_id=signal_id,
        horizon_class=horizon_class,
        missing_dependencies=missing_dependencies,
    )
    full_payload = "\n".join([payload_key, run_id])
    return hashlib.sha256(full_payload.encode("utf-8")).hexdigest()


def _compute_scenario_fingerprint_payload_key(
    *,
    capability_ids: list[str],
    assumptions: list[str],
    signal_id: str,
    horizon_class: str,
    missing_dependencies: list[str],
) -> str:
    """Compute the payload key (without run_id) for conflict detection."""
    return "\n".join(
        [
            "scenario-v1",
            "\n".join(capability_ids),
            "\n".join(assumptions),
            signal_id,
            horizon_class,
            "\n".join(missing_dependencies),
        ]
    )


def _scenario_canonical_name(
    proposed_combination: str | None,
    capability_ids: list[str],
    horizon: HorizonClass,
) -> str:
    """Build a human-readable canonical name for the scenario entity."""
    if proposed_combination:
        return proposed_combination[:512]
    cap_count = len(capability_ids)
    return f"Scenario [{horizon.value}] combining {cap_count} capability(ies)"[:512]


# ── Utility helpers ────────────────────────────────────────────────────────


def _safe_json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
