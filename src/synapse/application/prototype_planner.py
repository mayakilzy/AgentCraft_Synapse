"""G06-T03 — Prototype Planning (two-mode design).

Per the approved G06 plan (sections 4.6, 9.3, 16):

- **Mode A — Linked G05 Innovation**: when the scenario is linked to a
  valid persisted G05 innovation concept with a compatible hypothesis,
  delegate to ``plan_experiment()`` with caller-supplied metrics. Never
  fabricate metrics from missing dependencies.
- **Mode B — No Linked Innovation**: when no valid linked innovation
  exists, return a structured prototype proposal. Do NOT call
  ``plan_experiment()``. Do NOT create an Experiment entity.

Both modes record an audit event. Mode A records
``scenario.prototype_linked``; Mode B records
``scenario.prototype_proposed``.

No new relationship predicates are invented. The scenario-to-experiment
linkage in Mode A uses an audit-event reference (not a new predicate).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.experiment_planner import plan_experiment
from synapse.application.scenario_builder import get_scenario
from synapse.observability.logging import get_logger
from synapse.storage.models import AuditEventRow, ClaimRow, EntityRow

__all__ = [
    "plan_prototype",
]

_log = get_logger("synapse.application.prototype_planner")


# ── Public API ─────────────────────────────────────────────────────────────


async def plan_prototype(
    session: AsyncSession,
    scenario_id: str,
    *,
    protocol_override: str | None = None,
    metrics: dict[str, str] | None = None,
    baseline: str | None = None,
    execution_mode: str | None = None,
    safety_limits: dict[str, Any] | None = None,
    cost_limits: dict[str, Any] | None = None,
    run_id: str | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Plan a prototype for a persisted scenario.

    Mode A: if the scenario has a ``linked_innovation_concept_id`` that
    resolves to a persisted G05 innovation concept with a hypothesis,
    delegate to ``plan_experiment()`` with the caller-supplied metrics.

    Mode B: if no linked innovation exists, return a structured proposal.
    Do NOT call ``plan_experiment()``. Do NOT create an Experiment entity.

    Args:
        session: AsyncSession bound to the Synapse database.
        scenario_id: ID of the persisted scenario.
        protocol_override: optional human-readable protocol. If omitted,
            the scenario's ``prototype_plan`` is used.
        metrics: caller-supplied metric schema (``{name: dtype}``).
            REQUIRED for Mode A; ignored in Mode B.
        baseline: optional baseline description (Mode A only).
        execution_mode: optional execution mode (Mode A only).
        safety_limits: optional safety limits (Mode A only).
        cost_limits: optional cost limits (Mode A only).
        run_id: optional retry identity for idempotent creation.
        requester: optional actor name for audit logging.

    Returns:
        A dict with ``mode`` = "linked_experiment" or "prototype_proposal".

    Raises:
        ValueError: if the scenario is F4 without critique, if Mode A is
            requested but metrics are missing, or if the linked innovation
            or hypothesis does not resolve.
    """
    scenario = await get_scenario(session, scenario_id)
    if scenario is None:
        return None

    # F4 scenarios require a critique (frozen validator already enforced
    # at construction, but double-check here).
    if scenario.get("horizon_class") == "F4" and not scenario.get("critique"):
        raise ValueError(
            "F4 scenario requires a critique before prototype planning"
        )

    linked_concept_id = scenario.get("linked_innovation_concept_id")

    # ── Try Mode A: linked innovation ──────────────────────────────────
    if linked_concept_id:
        hypothesis_id = await _find_hypothesis_for_concept(session, linked_concept_id)
        if hypothesis_id is not None:
            return await _mode_a_linked_experiment(
                session,
                scenario=scenario,
                hypothesis_id=hypothesis_id,
                protocol_override=protocol_override,
                metrics=metrics,
                baseline=baseline,
                execution_mode=execution_mode,
                safety_limits=safety_limits,
                cost_limits=cost_limits,
                run_id=run_id,
                requester=requester,
            )

    # ── Mode B: no linked innovation (or no hypothesis) ────────────────
    return await _mode_b_proposal(
        session,
        scenario=scenario,
        protocol_override=protocol_override,
        requester=requester,
    )


# ── Mode A ─────────────────────────────────────────────────────────────────


async def _mode_a_linked_experiment(
    session: AsyncSession,
    *,
    scenario: dict[str, Any],
    hypothesis_id: str,
    protocol_override: str | None,
    metrics: dict[str, str] | None,
    baseline: str | None,
    execution_mode: str | None,
    safety_limits: dict[str, Any] | None,
    cost_limits: dict[str, Any] | None,
    run_id: str | None,
    requester: str | None,
) -> dict[str, Any]:
    """Mode A: delegate to plan_experiment() with caller-supplied metrics.

    Never fabricate metrics from missing_dependencies.
    """
    if not metrics:
        raise ValueError(
            "Mode A (linked innovation) requires caller-supplied metrics. "
            "G06-T03 does NOT fabricate metrics from missing_dependencies."
        )

    protocol = protocol_override or scenario.get("prototype_plan") or ""

    try:
        plan_result = await plan_experiment(
            session,
            hypothesis_id,
            protocol=protocol if protocol else None,
            baseline=baseline,
            metrics=metrics,
            execution_mode=execution_mode,
            safety_limits=safety_limits,
            cost_limits=cost_limits,
            requester=requester or "g06-t03-prototype-planner",
        )
    except ValueError as exc:
        raise ValueError(f"plan_experiment failed: {exc}") from exc

    if plan_result is None:
        raise ValueError(
            f"plan_experiment returned None for hypothesis {hypothesis_id!r} — "
            f"the hypothesis may not resolve to a valid innovation concept"
        )

    experiment = plan_result.get("experiment", plan_result)
    experiment_id = experiment.get("experiment_id", "")

    # Record audit event
    audit = AuditEventRow(
        id=f"aev-{uuid4().hex[:16]}",
        event_type="scenario.prototype_linked",
        actor=requester or "unknown",
        target_id=scenario["scenario_id"],
        target_type="scenario",
        payload=json.dumps(
            {
                "scenario_id": scenario["scenario_id"],
                "experiment_id": experiment_id,
                "hypothesis_id": hypothesis_id,
                "innovation_concept_id": scenario.get("linked_innovation_concept_id"),
                "mode": "linked_experiment",
                "metrics": metrics,
                "run_id": run_id,
            },
            default=str,
        ),
    )
    session.add(audit)
    await session.commit()

    return {
        "mode": "linked_experiment",
        "experiment": experiment,
        "experiment_id": experiment_id,
        "innovation_concept_id": scenario.get("linked_innovation_concept_id"),
        "hypothesis_id": hypothesis_id,
        "audit_event_id": audit.id,
    }


# ── Mode B ─────────────────────────────────────────────────────────────────


async def _mode_b_proposal(
    session: AsyncSession,
    *,
    scenario: dict[str, Any],
    protocol_override: str | None,
    requester: str | None,
) -> dict[str, Any]:
    """Mode B: return a structured prototype proposal.

    Do NOT call plan_experiment(). Do NOT create an Experiment entity.
    """
    missing_deps = scenario.get("missing_dependencies", [])
    assumptions = scenario.get("assumptions", [])
    prototype_plan = protocol_override or scenario.get("prototype_plan") or ""

    # Build investigation targets from missing dependencies
    investigation_targets: list[dict[str, Any]] = []
    for dep_id in missing_deps:
        ent_stmt = select(EntityRow).where(EntityRow.id == dep_id)
        entity = (await session.execute(ent_stmt)).scalar_one_or_none()
        investigation_targets.append({
            "entity_id": dep_id,
            "canonical_name": entity.canonical_name if entity else "(unknown)",
            "kind": entity.kind if entity else "unknown",
            "note": "Genuine unresolved dependency — investigate before prototyping.",
        })

    proposal = {
        "mode": "prototype_proposal",
        "scenario_id": scenario["scenario_id"],
        "prototype_plan": prototype_plan,
        "missing_dependencies": missing_deps,
        "investigation_targets": investigation_targets,
        "assumptions_to_test": assumptions,
        "note": (
            "No linked G05 innovation. Create an innovation + hypothesis "
            "to persist an experiment via Mode A."
        ),
        "prerequisites_missing": [
            "A G05 innovation concept (EntityRow kind='project') linked to this scenario",
            "A hypothesis (ClaimRow with subject_ref=concept_id, epistemic_state='hypothesized')",
        ],
    }

    # Record audit event
    audit = AuditEventRow(
        id=f"aev-{uuid4().hex[:16]}",
        event_type="scenario.prototype_proposed",
        actor=requester or "unknown",
        target_id=scenario["scenario_id"],
        target_type="scenario",
        payload=json.dumps(
            {
                "scenario_id": scenario["scenario_id"],
                "mode": "prototype_proposal",
                "investigation_target_count": len(investigation_targets),
            },
            default=str,
        ),
    )
    session.add(audit)
    await session.commit()

    proposal["audit_event_id"] = audit.id
    return proposal


# ── Helpers ────────────────────────────────────────────────────────────────


async def _find_hypothesis_for_concept(
    session: AsyncSession,
    concept_id: str,
) -> str | None:
    """Find a hypothesis (ClaimRow) linked to a G05 innovation concept.

    The hypothesis must have ``subject_ref = concept_id`` and
    ``epistemic_state = 'hypothesized'`` (or any non-terminal state).
    """
    stmt = select(ClaimRow).where(
        ClaimRow.subject_ref == concept_id,
    ).order_by(ClaimRow.created_at.desc()).limit(1)
    claim = (await session.execute(stmt)).scalar_one_or_none()
    if claim is None:
        return None
    # Verify the concept entity exists and is kind="project"
    ent_stmt = select(EntityRow).where(
        EntityRow.id == concept_id,
        EntityRow.kind == "project",
    )
    entity = (await session.execute(ent_stmt)).scalar_one_or_none()
    if entity is None:
        return None
    return claim.id
