"""G06-T04 — Scenario Revision.

Explicit reassessment of persisted scenarios with append-only revision
history via ``AuditEventRow``.

Per the approved G06 plan (sections 4.5, 5.3, 9.4, 13.4):

- **Explicit reassessment only.** No background scheduler, no polling, no
  queue. Reassessment occurs only when a caller invokes the
  ``/api/v1/future/scenarios/{scenario_id}/reassess`` endpoint.
- **Material-change fingerprint.** A revision is warranted only when a
  material evaluation field differs between the prior authoritative
  evaluation snapshot and the freshly recomputed evaluation. A later
  timestamp alone does NOT create a revision. The volatile metadata
  (``evaluated_at``, request IDs, audit timestamps, deterministic prose
  derivable from the axes) is excluded from the comparison.
- **Append-only history.** Prior revision snapshots are NEVER overwritten.
  Each revision is a new ``AuditEventRow(event_type="scenario.revision")``
  row. The history is queryable via
  ``GET /api/v1/future/scenarios/{scenario_id}/revisions`` (newest first).
- **Version monotonicity.** The scenario's ``version`` (stored as a
  field in its attributes JSON) starts at 1 on creation. Each material
  revision increments it by exactly 1. No-change reassessment leaves the
  version untouched.
- **Duplicate revision prevention.** If the same evidence state is
  reassessed twice without intervening KB changes, the second reassessment
  produces no new audit row and ``revised=false``.
- **Concurrent reassessment safety.** The scenario ``EntityRow`` is
  loaded with ``SELECT ... FOR UPDATE`` on PostgreSQL (transaction-scoped
  row lock) before bumping the version, so two concurrent reassessments
  cannot both publish the same next version.
- **No new ORM tables, migrations, or domain contracts.** Reuses
  ``AuditEventRow``, ``EntityRow``, the G06-T03 evaluator, and the
  existing attributes-JSON persistence convention.

Material evaluation fields
--------------------------
The following fields determine a material revision. Any difference in any
of these fields between the prior and new evaluation snapshots constitutes
a material change (and triggers a revision). All other evaluation fields
are either volatile (timestamps) or deterministic derivable from these
axes (the reasoning prose, the uncertainty_method/limitation constants,
the drill-down details dicts whose headline field is already in the
material set).

    1. ``evidence_coverage`` (float) — primary axis; drives uncertainty.
    2. ``distinct_source_uri_count`` (int) — provenance breadth.
    3. ``independent_origin_count`` (int) — conservative independence.
    4. ``dependency_readiness`` (str) — precedence label of worst dep.
    5. ``contradiction_exposure`` (int) — count of evidence-backed
       contradictions.
    6. ``assumption_assessment`` (dict) — per-assumption classification
       (supported / contested / not_evidenced / unknown).
    7. ``horizon_alignment`` (str) — aligned / insufficient_evidence /
       misaligned / unknown.
    8. ``overall`` (str) — categorical: testable / needs_more_evidence /
       speculative / inconsistent.

Excluded (volatile or derivative): ``evaluated_at``, ``reasoning``,
``uncertainty_method``, ``uncertainty_limitation``, ``scenario_id``,
``evidence_coverage_details``, ``dependency_details``,
``contradiction_details``, ``horizon_notes``, ``uncertainty`` (because
``uncertainty = 1.0 - evidence_coverage`` per Correction C — it is a
pure function of ``evidence_coverage``, so any change in coverage
already triggers a revision).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.scenario_builder import get_scenario
from synapse.application.scenario_evaluator import evaluate_scenario
from synapse.observability.logging import get_logger
from synapse.storage.models import AuditEventRow, EntityRow

__all__ = [
    "MATERIAL_FIELDS",
    "VALID_TRIGGERS",
    "get_scenario_revisions",
    "persist_evaluation_snapshot",
    "reassess_scenario",
]

_log = get_logger("synapse.application.scenario_revision")


# ── Public constants ───────────────────────────────────────────────────────


#: Material evaluation fields — whose change warrants a revision.
MATERIAL_FIELDS: tuple[str, ...] = (
    "evidence_coverage",
    "distinct_source_uri_count",
    "independent_origin_count",
    "dependency_readiness",
    "contradiction_exposure",
    "assumption_assessment",
    "horizon_alignment",
    "overall",
)

#: Valid trigger values for the reassess endpoint.
VALID_TRIGGERS: frozenset[str] = frozenset(
    {"explicit_reassess", "new_evidence", "new_experiment_result"}
)


# ── Public API ────────────────────────────────────────────────────────────


async def reassess_scenario(
    session: AsyncSession,
    scenario_id: str,
    *,
    trigger: str,
    trigger_ref: str | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Reassess a persisted scenario.

    The reassessment flow (per G06 plan §4.5):

    1. Load the current scenario.
    2. Resolve its relevant evidence and dependencies (the evaluator does
       this internally — it reads ``current_evidence_refs`` from the
       scenario attributes and consults the current KB).
    3. Recompute the scenario evaluation using the existing G06-T03
       evaluator (``evaluate_scenario``).
    4. Load the prior authoritative evaluation snapshot.
    5. Compare the new evaluation with the prior snapshot on material
       fields only.
    6. Determine whether a material revision is warranted.
    7. If warranted: preserve the prior snapshot (append-only audit row),
       bump the scenario's version, persist the new snapshot as
       ``last_evaluation`` in the scenario attributes JSON, and emit a
       ``scenario.revision`` audit event.
    8. Return a clear ``revised`` boolean and a structured explanation.

    Args:
        session: AsyncSession bound to the Synapse database.
        scenario_id: ID of the persisted scenario.
        trigger: One of ``VALID_TRIGGERS``.
        trigger_ref: Optional reference to the triggering artifact
            (e.g., an execution_id, fragment_id, or claim_id). May be
            None for ``explicit_reassess``.
        requester: Optional actor name for audit logging.

    Returns:
        A dict with the reassessment result (see §9.4 of the plan for the
        response shape), or ``None`` if the scenario does not exist.

    Raises:
        ValueError: if ``trigger`` is not in ``VALID_TRIGGERS``.
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(
            f"Invalid trigger {trigger!r}; expected one of "
            f"{sorted(VALID_TRIGGERS)}"
        )

    # ── 1. Load the current scenario ──────────────────────────────────
    scenario = await get_scenario(session, scenario_id)
    if scenario is None:
        return None

    # ── 2-3. Recompute the evaluation (re-runs G06-T03 evaluator) ─────
    # The evaluator reads current_evidence_refs from the scenario's
    # persisted attributes and consults the current KB state for each
    # fragment's verification status. It re-runs analyze_gap-equivalent
    # logic for dependency readiness. It is read-only on the scenario
    # (it does emit a `scenario.evaluated` audit row but does not modify
    # scenario attributes).
    new_evaluation = await evaluate_scenario(
        session, scenario_id, requester=requester
    )
    # new_evaluation is non-None here because we already verified the
    # scenario exists above.
    assert new_evaluation is not None

    # ── 4. Load the prior authoritative evaluation snapshot ──────────
    prior_evaluation = await _load_prior_evaluation(session, scenario_id)

    # ── 5. Compute material changes ──────────────────────────────────
    if prior_evaluation is None:
        # First reassessment with no prior authoritative snapshot.
        # The new evaluation establishes the baseline — this is a
        # material revision by definition (we are recording the first
        # authoritative evaluation snapshot in the revision history).
        changes: list[str] = [
            "initial_revision: prior_evaluation absent, baseline established"
        ]
        revised = True
    else:
        changes = _diff_evaluations(prior_evaluation, new_evaluation)
        revised = len(changes) > 0

    # ── 6. If not revised, return without writing an audit row ───────
    if not revised:
        return {
            "scenario_id": scenario_id,
            "revised": False,
            "prior_evaluation": prior_evaluation,
            "new_evaluation": new_evaluation,
            "changes": [],
            "revision_id": None,
            "trigger": trigger,
            "trigger_ref": trigger_ref,
            "reassessed_at": datetime.now(UTC).isoformat(),
            "reassessed_by": requester or "unknown",
        }

    # ── 7. Material revision: bump version, persist snapshot, audit ──
    # Load the EntityRow with a row-level lock to serialize concurrent
    # reassessments on PostgreSQL.
    entity = await _load_scenario_entity_for_update(session, scenario_id)
    if entity is None:
        # Race: scenario was deleted between get_scenario and now.
        return None

    attrs: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attrs = parsed

    prior_version = attrs.get("version", 1)
    new_version = int(prior_version) + 1
    attrs["version"] = new_version
    attrs["last_evaluation"] = new_evaluation
    attrs["last_revised_at"] = datetime.now(UTC).isoformat()
    entity.attributes = json.dumps(attrs, default=str)
    entity.version = new_version
    entity.updated_at = datetime.now(UTC)
    await session.flush()

    # Write the scenario.revision audit row (append-only).
    revision_id = f"rev-{uuid4().hex[:16]}"
    reassessed_at = datetime.now(UTC).isoformat()
    audit_payload = {
        "scenario_id": scenario_id,
        "prior_version": prior_version,
        "new_version": new_version,
        "prior_evaluation": prior_evaluation,
        "new_evaluation": new_evaluation,
        "changes": changes,
        "trigger": trigger,
        "trigger_ref": trigger_ref,
        "reassessed_at": reassessed_at,
        "reassessed_by": requester or "unknown",
    }
    audit = AuditEventRow(
        id=revision_id,
        event_type="scenario.revision",
        actor=requester or "unknown",
        target_id=scenario_id,
        target_type="scenario",
        payload=json.dumps(audit_payload, default=str),
    )
    session.add(audit)
    await session.flush()

    _log.info(
        "reassess_scenario scenario=%s revised=True prior_v=%s new_v=%s "
        "trigger=%s trigger_ref=%s changes=%d",
        scenario_id,
        prior_version,
        new_version,
        trigger,
        trigger_ref,
        len(changes),
    )

    return {
        "scenario_id": scenario_id,
        "revised": True,
        "prior_evaluation": prior_evaluation,
        "new_evaluation": new_evaluation,
        "changes": changes,
        "revision_id": revision_id,
        "prior_version": prior_version,
        "new_version": new_version,
        "trigger": trigger,
        "trigger_ref": trigger_ref,
        "reassessed_at": reassessed_at,
        "reassessed_by": requester or "unknown",
    }


async def get_scenario_revisions(
    session: AsyncSession,
    scenario_id: str,
) -> list[dict[str, Any]]:
    """Return the scenario's revision history, newest first.

    Append-only: prior revisions are never overwritten. Each revision's
    payload contains the full ``prior_evaluation`` and ``new_evaluation``
    snapshots, the change summary, the trigger, and the provenance.

    Returns an empty list if the scenario has never been revised.
    """
    stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.event_type == "scenario.revision",
            AuditEventRow.target_id == scenario_id,
        )
        .order_by(AuditEventRow.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    revisions: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload
        if not isinstance(payload, dict):
            payload = _safe_json_loads(payload)
        revisions.append(
            {
                "revision_id": row.id,
                "scenario_id": scenario_id,
                "prior_version": payload.get("prior_version"),
                "new_version": payload.get("new_version"),
                "prior_evaluation": payload.get("prior_evaluation"),
                "new_evaluation": payload.get("new_evaluation"),
                "changes": payload.get("changes", []),
                "trigger": payload.get("trigger"),
                "trigger_ref": payload.get("trigger_ref"),
                "reassessed_at": payload.get("reassessed_at"),
                "reassessed_by": payload.get("reassessed_by"),
                "actor": row.actor,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return revisions


async def persist_evaluation_snapshot(
    session: AsyncSession,
    scenario_id: str,
    evaluation: dict[str, Any],
) -> None:
    """Persist the full evaluation snapshot in the scenario's attributes JSON.

    This is called by the ``/evaluate`` API endpoint after
    ``evaluate_scenario()`` returns, so that subsequent reassessments can
    compare against a full prior snapshot (not just the summary fields in
    the ``scenario.evaluated`` audit row).

    This is purely additive: the scenario's other fields are not modified.
    The snapshot is stored under the ``last_evaluation`` key in the
    attributes JSON. The scenario's ``EntityRow.version`` is NOT bumped
    (an evaluation is not a revision).
    """
    entity_stmt = select(EntityRow).where(
        EntityRow.id == scenario_id,
        EntityRow.kind == "scenario",
    )
    entity = (await session.execute(entity_stmt)).scalar_one_or_none()
    if entity is None:
        # Scenario was deleted between the evaluate call and now. Nothing
        # to persist. The /evaluate endpoint will still return the
        # evaluation result to the caller; only the snapshot persistence
        # is skipped.
        return

    attrs: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attrs = parsed
    attrs["last_evaluation"] = evaluation
    attrs["last_evaluated_at"] = datetime.now(UTC).isoformat()
    entity.attributes = json.dumps(attrs, default=str)
    await session.flush()


# ── Helpers ───────────────────────────────────────────────────────────────


async def _load_prior_evaluation(
    session: AsyncSession,
    scenario_id: str,
) -> dict[str, Any] | None:
    """Load the prior authoritative evaluation snapshot.

    The authoritative prior snapshot is the scenario's persisted
    ``last_evaluation`` attribute, set either by the ``/evaluate`` endpoint
    (via ``persist_evaluation_snapshot``) or by a prior ``/reassess`` call
    (which always updates ``last_evaluation`` to the new snapshot when a
    revision is warranted).

    The ``scenario.evaluated`` audit row payload contains only summary
    fields (overall, evidence_coverage, uncertainty, horizon_alignment),
    so it CANNOT serve as a full prior snapshot — it would miss changes
    in distinct_source_uri_count, independent_origin_count,
    dependency_readiness, contradiction_exposure, assumption_assessment.
    The /evaluate endpoint therefore persists the full snapshot via
    ``persist_evaluation_snapshot``.

    Returns None if no prior authoritative snapshot exists (i.e., the
    scenario has never been evaluated or reassessed).
    """
    entity_stmt = select(EntityRow).where(
        EntityRow.id == scenario_id,
        EntityRow.kind == "scenario",
    )
    entity = (await session.execute(entity_stmt)).scalar_one_or_none()
    if entity is None:
        return None
    attrs = _safe_json_loads(entity.attributes)
    if not isinstance(attrs, dict):
        return None
    return attrs.get("last_evaluation")


async def _load_scenario_entity_for_update(
    session: AsyncSession,
    scenario_id: str,
) -> EntityRow | None:
    """Load a scenario EntityRow with a row-level lock for revision.

    On PostgreSQL: ``SELECT ... FOR UPDATE`` acquires a transaction-scoped
    row lock, serializing concurrent reassessments of the same scenario.
    On SQLite: writes are serialized at the DB level (single-writer), so
    no explicit lock is needed.
    """
    stmt = select(EntityRow).where(
        EntityRow.id == scenario_id,
        EntityRow.kind == "scenario",
    )
    dialect = session.bind.dialect.name if session.bind else "unknown"
    if dialect == "postgresql":
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def _diff_evaluations(
    prior: dict[str, Any] | None,
    new: dict[str, Any],
) -> list[str]:
    """Compare material evaluation fields between prior and new.

    Returns a list of human-readable change descriptions. An empty list
    means no material change. If ``prior`` is None, returns an empty list
    (the caller handles the first-revision case separately).
    """
    if prior is None:
        return []

    changes: list[str] = []
    for field in MATERIAL_FIELDS:
        old_val = prior.get(field)
        new_val = new.get(field)
        if _values_differ(old_val, new_val):
            changes.append(
                f"{field}: {_short_repr(old_val)} -> {_short_repr(new_val)}"
            )
    return changes


def _values_differ(a: Any, b: Any) -> bool:
    """Compare two values for material difference.

    Floats are compared with a small tolerance to avoid spurious revisions
    from floating-point rounding. Dicts and lists use deep equality.
    """
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) > 1e-9
    if isinstance(a, dict) and isinstance(b, dict):
        return a != b
    return a != b


def _short_repr(val: Any) -> str:
    """Short representation for change descriptions."""
    if val is None:
        return "None"
    if isinstance(val, dict):
        # For dicts (e.g., assumption_assessment), summarize keys to keep
        # the change description readable. The full dict is in the
        # prior_evaluation / new_evaluation snapshots in the audit payload.
        return f"dict(keys={sorted(val.keys())})"
    if isinstance(val, list):
        return f"list(len={len(val)})"
    if isinstance(val, str) and len(val) > 60:
        return repr(val[:57] + "...")
    return repr(val)


def _safe_json_loads(raw: Any) -> Any:
    """Safely parse JSON, handling both string and already-parsed values.

    SQLAlchemy may auto-deserialize JSON text into dicts/lists on read.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict | list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
