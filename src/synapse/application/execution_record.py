"""G05-T06 -- Experiment Execution Records + Evidence Feedback.

Per the G05-T06 mission: implement a minimal, reliable workflow that
transforms an existing experiment plan into a traceable execution record
and evaluates submitted observations against the plan.

The system distinguishes:
- Experiment planned (G05-T05)
- Experiment execution recorded (T06-A)
- Observations received (T06-B)
- Evidence assessed (T06-C)
- Hypothesis supported, contradicted, or inconclusive (T06-D)

**An experiment plan is not evidence. An observation is not automatically
verified evidence.**

Architecture
------------

Execution records reuse the existing ``EntityRow(kind="execution")``
pattern (same as experiments use ``kind="experiment"``). No new tables,
no migrations. The execution's attributes JSON carries:

- execution_id, experiment_id, hypothesis_id
- execution_mode, status (CREATED/RUNNING/COMPLETED/FAILED/CANCELLED)
- started_at, completed_at
- protocol_snapshot (verbatim copy of the experiment's protocol at execution time)
- observations[] (append-only list)
- assessment (result + reasoning, populated on finalize)
- error_details (populated on failure)
- evidence_delta_id (link to AuditEventRow if emitted)

Evidence feedback uses the existing ``AuditEventRow(event_type=
"evidence.delta")`` table. The hypothesis ``ClaimRow``'s
``epistemic_state`` and ``confidence_value`` are updated ONLY when the
assessment produces a state change (HYPOTHESIZED → SUPPORTED/DISPUTED).

Idempotency reuses the PRB-03 ``claim_fingerprint`` infrastructure:
- Execution fingerprint: SHA-256(experiment_id + execution_mode +
  protocol_snapshot)
- Same fingerprint → same execution_id (idempotent reuse)
- Different fingerprint → different execution_id (no false reuse)

Epistemic safety
----------------

- An experiment plan is NOT evidence.
- An observation is NOT verified evidence — it is a recorded measurement.
- The assessment is a deterministic comparison of observations against
  the plan's success/failure criteria.
- No observation promotes the hypothesis to VERIFIED (impossible —
  ``EpistemicState`` has no VERIFIED value).
- Repeated observations from the same origin are NOT independent
  corroboration (the assessment treats them as a single data point).
- Evidence history is append-only — prior observations are never
  overwritten or erased.
- Contradictory evidence is preserved, not hidden.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.experiment_planner import get_experiment
from synapse.domain._base import EpistemicState
from synapse.observability.logging import get_logger
from synapse.storage.fingerprint import claim_fingerprint
from synapse.storage.models import AuditEventRow, ClaimRow, EntityRow

_log = get_logger("synapse.application.execution_record")


# ── Execution lifecycle ────────────────────────────────────────────────────


class ExecutionStatus:
    """Execution lifecycle states (string constants, not an enum, to keep
    them JSON-serializable without importing StrEnum)."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed transitions
EXECUTION_TRANSITIONS: dict[str, set[str]] = {
    ExecutionStatus.CREATED: {ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED, ExecutionStatus.FAILED},
    ExecutionStatus.RUNNING: {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED},
    ExecutionStatus.COMPLETED: set(),  # terminal
    ExecutionStatus.FAILED: set(),  # terminal
    ExecutionStatus.CANCELLED: set(),  # terminal
}


# ── Assessment results ─────────────────────────────────────────────────────


class AssessmentResult:
    """Evidence assessment outcomes."""

    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT_DATA = "insufficient_data"


# ── Helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _compute_execution_fingerprint(
    *,
    experiment_id: str,
    execution_mode: str,
    protocol_snapshot: str,
) -> str:
    """SHA-256 fingerprint for idempotent execution creation.

    The fingerprint includes the experiment_id, execution_mode, and a
    normalized protocol snapshot. Same inputs → same execution_id.
    Different inputs → different execution_id (no false reuse).
    """
    normalized_protocol = " ".join(protocol_snapshot.lower().split())
    payload = "\n".join([experiment_id, execution_mode, normalized_protocol])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _attributes_to_execution(entity: EntityRow) -> dict[str, Any]:
    """Reconstruct the execution dict from a persisted ``EntityRow``."""
    attributes: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attributes = parsed
    execution = dict(attributes)
    execution["execution_id"] = entity.id
    execution["canonical_name"] = entity.canonical_name
    execution["retrieved_at"] = _utcnow_iso()
    return execution


# ── T06-A: Execution record creation ───────────────────────────────────────


async def create_execution(
    session: AsyncSession,
    experiment_id: str,
    *,
    execution_mode: str | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Create an execution record for an existing experiment plan.

    Args:
        session: AsyncSession bound to the Synapse database.
        experiment_id: ID of the persisted experiment plan (EntityRow
            kind="experiment").
        execution_mode: optional override for the execution mode. If
            omitted, the experiment plan's execution_mode is used.
        requester: optional actor name for audit logging.

    Returns:
        The execution dict, or ``None`` if the experiment_id does not
        resolve to a persisted experiment plan.

    Epistemic safety:
        - Does NOT create an EvidenceDelta.
        - Does NOT modify the hypothesis.
        - The execution starts in CREATED status — no observations, no
          assessment.
    """
    request_id = uuid4().hex

    # ── Load the experiment plan ───────────────────────────────────────
    plan = await get_experiment(session, experiment_id)
    if plan is None:
        _log.info(
            "create_execution experiment_id=%s not found — returning None",
            experiment_id,
        )
        return None

    hypothesis_id = plan.get("hypothesis_id", "")
    plan_mode = plan.get("execution_mode", "dry_run")
    mode = execution_mode or plan_mode
    protocol_snapshot = plan.get("protocol", "")

    # ── Compute fingerprint + atomically claim it (PRB-03 reuse) ──────
    fingerprint = _compute_execution_fingerprint(
        experiment_id=experiment_id,
        execution_mode=mode,
        protocol_snapshot=protocol_snapshot,
    )
    execution_id = f"exec-{fingerprint[:16]}"
    claim_id = f"efp-{execution_id}"

    winning_entity_id, we_won = await claim_fingerprint(
        session,
        kind="execution",
        fingerprint=fingerprint,
        entity_id=execution_id,
        claim_id=claim_id,
    )

    if not we_won:
        # Idempotent reuse — load the existing execution
        _log.info(
            "create_execution reused execution_id=%s fingerprint=%s",
            winning_entity_id,
            fingerprint[:16],
        )
        existing = (
            await session.execute(
                select(EntityRow).where(
                    EntityRow.id == winning_entity_id,
                    EntityRow.kind == "execution",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            reused = _attributes_to_execution(existing)
            reused["was_reused"] = True
            return reused

    # ── Persist the new execution record ──────────────────────────────
    execution_payload: dict[str, Any] = {
        "execution_id": execution_id,
        "experiment_id": experiment_id,
        "hypothesis_id": hypothesis_id,
        "execution_mode": mode,
        "status": ExecutionStatus.CREATED,
        "started_at": _utcnow_iso(),
        "completed_at": None,
        "protocol_snapshot": protocol_snapshot,
        "metrics_schema": plan.get("metrics", {}),
        "success_criteria": plan.get("success_criteria", []),
        "failure_criteria": plan.get("failure_criteria", []),
        "observations": [],
        "assessment": None,
        "error_details": None,
        "evidence_delta_id": None,
        "provenance": {
            "created_by": requester or "unknown",
            "created_at": _utcnow_iso(),
            "request_id": request_id,
        },
    }

    # Store the execution as EntityRow(kind="execution")
    attributes = dict(execution_payload)
    attributes.pop("execution_id", None)
    attributes.pop("retrieved_at", None)
    attributes.pop("was_reused", None)

    entity = EntityRow(
        id=execution_id,
        kind="execution",
        canonical_name=f"Execution of {experiment_id[:24]} ({mode})"[:512],
        aliases="[]",
        attributes=json.dumps(attributes, default=str),
        description=f"Execution record for experiment {experiment_id}"[:2048],
        version=1,
    )
    session.add(entity)
    await session.flush()

    execution_payload["was_reused"] = False
    _log.info(
        "create_execution persisted execution_id=%s experiment_id=%s mode=%s",
        execution_id,
        experiment_id,
        mode,
    )
    return execution_payload


async def get_execution(
    session: AsyncSession,
    execution_id: str,
) -> dict[str, Any] | None:
    """Retrieve a persisted execution record by ID."""
    stmt = select(EntityRow).where(
        EntityRow.id == execution_id,
        EntityRow.kind == "execution",
    )
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        return None
    return _attributes_to_execution(entity)


def _update_execution_attributes(
    entity: EntityRow,
    updates: dict[str, Any],
) -> None:
    """Merge updates into the execution's attributes JSON."""
    attributes: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attributes = parsed
    attributes.update(updates)
    entity.attributes = json.dumps(attributes, default=str)


# ── T06-B: Observation capture ─────────────────────────────────────────────


async def record_observation(
    session: AsyncSession,
    execution_id: str,
    *,
    metric: str,
    observed_value: Any,
    expected_value: Any | None = None,
    unit: str | None = None,
    measurement_method: str = "manual",
    source_ref: str | None = None,
    uncertainty: str | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Record an observation against an execution.

    Validates that:
    - The execution exists and is not in a terminal state.
    - The metric is declared in the experiment plan's metrics schema.

    Observations are append-only — they are never overwritten or removed.

    Returns the observation dict, or ``None`` if the execution does not
    exist. Raises ``ValueError`` if the metric is invalid or the
    execution is terminal.
    """
    # ── Load the execution ────────────────────────────────────────────
    stmt = select(EntityRow).where(
        EntityRow.id == execution_id,
        EntityRow.kind == "execution",
    )
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        return None

    attributes: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attributes = parsed

    status = attributes.get("status", ExecutionStatus.CREATED)
    if status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED):
        raise ValueError(
            f"Cannot record observation on terminal execution (status={status})"
        )

    # ── Validate the metric ───────────────────────────────────────────
    metrics_schema = attributes.get("metrics_schema", {})
    if metric not in metrics_schema:
        raise ValueError(
            f"Metric {metric!r} is not declared in the experiment's metrics "
            f"schema. Declared metrics: {list(metrics_schema.keys())}"
        )

    # ── Build the observation ─────────────────────────────────────────
    observation_id = f"obs-{uuid4().hex[:16]}"
    observation = {
        "observation_id": observation_id,
        "metric": metric,
        "observed_value": observed_value,
        "expected_value": expected_value,
        "unit": unit or metrics_schema.get(metric, ""),
        "measurement_method": measurement_method,
        "source_ref": source_ref or requester or "unknown",
        "timestamp": _utcnow_iso(),
        "evidence_origin": "experiment_execution",
        "uncertainty": uncertainty,
    }

    # ── Append to the observations list (append-only) ─────────────────
    observations = attributes.get("observations", [])
    observations.append(observation)
    attributes["observations"] = observations

    # Transition to RUNNING if still CREATED
    if status == ExecutionStatus.CREATED:
        attributes["status"] = ExecutionStatus.RUNNING

    _update_execution_attributes(entity, attributes)
    await session.flush()

    _log.info(
        "record_observation execution_id=%s metric=%s observation_id=%s",
        execution_id,
        metric,
        observation_id,
    )
    return observation


# ── T06-C: Evidence assessment ─────────────────────────────────────────────


def _compare_values(
    observed: Any,
    threshold: Any,
    comparator: str,
) -> bool | None:
    """Compare observed value against threshold using the comparator.

    Returns True/False if the comparison is definitive, or None if the
    values cannot be compared (e.g. type mismatch).
    """
    try:
        if comparator == ">=":
            return float(observed) >= float(threshold)
        if comparator == "<=":
            return float(observed) <= float(threshold)
        if comparator == ">":
            return float(observed) > float(threshold)
        if comparator == "<":
            return float(observed) < float(threshold)
        if comparator == "==":
            return observed == threshold
        if comparator == "!=":
            return observed != threshold
    except (TypeError, ValueError):
        return None
    return None


def assess_evidence(
    execution: dict[str, Any],
) -> dict[str, Any]:
    """Deterministically assess recorded observations against the plan.

    Assessment rules:
    1. If no observations → INSUFFICIENT_DATA.
    2. For each success criterion with calibration_status="operational"
       and non-null threshold:
       - If an observation exists for the metric, compare using the
         comparator. If the comparison passes, the criterion is "met".
       - If the comparison fails, the criterion is "not_met".
       - If no observation exists, the criterion is "no_data".
    3. For each failure criterion (same operational/threshold rule):
       - If triggered (comparison passes), the assessment is CONTRADICTING.
    4. If any failure criterion is triggered → CONTRADICTING.
    5. If all operational success criteria are met → SUPPORTING.
    6. If some criteria have no_data or require_calibration → INCONCLUSIVE.
    7. Criteria with calibration_status="requires_calibration" or
       "untestable" are reported but do NOT contribute to the pass/fail
       decision.

    Returns the assessment dict with:
    - result: supporting/contradicting/inconclusive/insufficient_data
    - reasoning: human-readable explanation
    - criteria_evaluated: list of per-criterion results
    - assessed_at: ISO8601 timestamp
    """
    observations = execution.get("observations", [])
    success_criteria = execution.get("success_criteria", [])
    failure_criteria = execution.get("failure_criteria", [])

    # Build a metric → latest observation map
    # (repeated observations from the same origin are NOT independent —
    # we use the latest value per metric)
    metric_observations: dict[str, Any] = {}
    metric_observation_count: dict[str, int] = {}
    for obs in observations:
        m = obs.get("metric", "")
        metric_observations[m] = obs.get("observed_value")
        metric_observation_count[m] = metric_observation_count.get(m, 0) + 1

    # ── No observations → insufficient data ────────────────────────────
    if not observations:
        return {
            "result": AssessmentResult.INSUFFICIENT_DATA,
            "reasoning": (
                "No observations have been recorded for this execution. "
                "Cannot assess evidence without measurements."
            ),
            "criteria_evaluated": [],
            "assessed_at": _utcnow_iso(),
            "unique_metric_count": 0,
            "total_observation_count": 0,
        }

    # ── Evaluate failure criteria first (contradicting takes priority) ──
    criteria_evaluated: list[dict[str, Any]] = []
    any_failure_triggered = False

    for fc in failure_criteria:
        cal_status = fc.get("calibration_status", "operational")
        threshold = fc.get("threshold")
        metric = fc.get("metric", "")
        comparator = fc.get("comparator", "")

        entry: dict[str, Any] = {
            "criterion_type": "failure",
            "metric": metric,
            "comparator": comparator,
            "threshold": threshold,
            "calibration_status": cal_status,
            "observed_value": metric_observations.get(metric),
            "result": "not_evaluated",
        }

        if cal_status != "operational" or threshold is None:
            entry["result"] = "requires_calibration"
        elif metric not in metric_observations:
            entry["result"] = "no_data"
        else:
            triggered = _compare_values(
                metric_observations[metric], threshold, comparator
            )
            if triggered is None:
                entry["result"] = "comparison_error"
            elif triggered:
                entry["result"] = "triggered"
                any_failure_triggered = True
            else:
                entry["result"] = "not_triggered"

        criteria_evaluated.append(entry)

    if any_failure_triggered:
        triggered_metrics = [
            e["metric"] for e in criteria_evaluated if e["result"] == "triggered"
        ]
        return {
            "result": AssessmentResult.CONTRADICTING,
            "reasoning": (
                f"One or more failure criteria were triggered: {triggered_metrics}. "
                f"The observations contradict the experiment's hypothesis."
            ),
            "criteria_evaluated": criteria_evaluated,
            "assessed_at": _utcnow_iso(),
            "unique_metric_count": len(metric_observations),
            "total_observation_count": len(observations),
        }

    # ── Evaluate success criteria ──────────────────────────────────────
    all_met = True
    any_no_data = False
    any_requires_calibration = False

    for sc in success_criteria:
        cal_status = sc.get("calibration_status", "operational")
        threshold = sc.get("threshold")
        metric = sc.get("metric", "")
        comparator = sc.get("comparator", "")

        entry = {
            "criterion_type": "success",
            "metric": metric,
            "comparator": comparator,
            "threshold": threshold,
            "calibration_status": cal_status,
            "observed_value": metric_observations.get(metric),
            "result": "not_evaluated",
        }

        if cal_status != "operational" or threshold is None:
            entry["result"] = "requires_calibration"
            any_requires_calibration = True
        elif metric not in metric_observations:
            entry["result"] = "no_data"
            any_no_data = True
            all_met = False
        else:
            met = _compare_values(
                metric_observations[metric], threshold, comparator
            )
            if met is None:
                entry["result"] = "comparison_error"
                all_met = False
            elif met:
                entry["result"] = "met"
            else:
                entry["result"] = "not_met"
                all_met = False

        criteria_evaluated.append(entry)

    # ── Determine the final result ─────────────────────────────────────
    if all_met and not any_no_data:
        return {
            "result": AssessmentResult.SUPPORTING,
            "reasoning": (
                "All operational success criteria were met, and no failure "
                "criteria were triggered. The observations support the "
                "experiment's hypothesis. Note: this is NOT independent "
                "verification — the hypothesis is not promoted to VERIFIED."
            ),
            "criteria_evaluated": criteria_evaluated,
            "assessed_at": _utcnow_iso(),
            "unique_metric_count": len(metric_observations),
            "total_observation_count": len(observations),
        }

    if any_no_data or any_requires_calibration:
        return {
            "result": AssessmentResult.INCONCLUSIVE,
            "reasoning": (
                "Some success criteria could not be evaluated (no data or "
                "requires calibration). The evidence is inconclusive."
            ),
            "criteria_evaluated": criteria_evaluated,
            "assessed_at": _utcnow_iso(),
            "unique_metric_count": len(metric_observations),
            "total_observation_count": len(observations),
        }

    # All operational criteria evaluated but not all met
    return {
        "result": AssessmentResult.INCONCLUSIVE,
        "reasoning": (
            "Not all operational success criteria were met, but no failure "
            "criteria were triggered. The evidence is inconclusive."
        ),
        "criteria_evaluated": criteria_evaluated,
        "assessed_at": _utcnow_iso(),
        "unique_metric_count": len(metric_observations),
        "total_observation_count": len(observations),
    }


# ── T06-D: Evidence feedback (finalize + emit EvidenceDelta) ──────────────


# Epistemic state transitions based on assessment result
# (only HYPOTHESIZED → SUPPORTED/DISPUTED — never to VERIFIED)
ASSESSMENT_TO_EPISTEMIC_DELTA: dict[str, EpistemicState | None] = {
    AssessmentResult.SUPPORTING: EpistemicState.SUPPORTED,
    AssessmentResult.CONTRADICTING: EpistemicState.DISPUTED,
    AssessmentResult.INCONCLUSIVE: None,  # no state change
    AssessmentResult.INSUFFICIENT_DATA: None,  # no state change
}


async def finalize_execution(
    session: AsyncSession,
    execution_id: str,
    *,
    status: str = ExecutionStatus.COMPLETED,
    error_details: dict[str, Any] | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Finalize an execution: set terminal status, assess evidence, emit
    EvidenceDelta if the assessment changes the hypothesis state.

    Args:
        session: AsyncSession bound to the Synapse database.
        execution_id: ID of the execution to finalize.
        status: terminal status (COMPLETED, FAILED, or CANCELLED).
        error_details: optional error/failure details (for FAILED status).
        requester: optional actor name for audit logging.

    Returns:
        The finalized execution dict with assessment populated, or
        ``None`` if the execution does not exist.

    Epistemic safety:
        - Only emits an EvidenceDelta if the assessment produces a state
          change (HYPOTHESIZED → SUPPORTED or DISPUTED).
        - Never promotes to VERIFIED (impossible — EpistemicState has no
          VERIFIED value).
        - The EvidenceDelta is stored as an AuditEventRow
          (event_type="evidence.delta") — append-only audit log.
        - The hypothesis ClaimRow's epistemic_state and confidence_value
          are updated ONLY when a delta is emitted.
        - Prior observations are preserved (append-only).
    """
    # ── Load the execution ────────────────────────────────────────────
    stmt = select(EntityRow).where(
        EntityRow.id == execution_id,
        EntityRow.kind == "execution",
    )
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        return None

    attributes: dict[str, Any] = {}
    if entity.attributes:
        parsed = _safe_json_loads(entity.attributes)
        if isinstance(parsed, dict):
            attributes = parsed

    current_status = attributes.get("status", ExecutionStatus.CREATED)

    # Validate transition
    if current_status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED):
        raise ValueError(
            f"Execution is already terminal (status={current_status})"
        )

    if status not in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED):
        raise ValueError(
            f"Invalid finalize status={status!r}; expected one of "
            f"COMPLETED, FAILED, CANCELLED"
        )

    # ── Update execution status ───────────────────────────────────────
    attributes["status"] = status
    attributes["completed_at"] = _utcnow_iso()
    if error_details:
        attributes["error_details"] = error_details

    # ── Assess evidence (only if COMPLETED; FAILED/CANCELLED → no assessment) ──
    assessment: dict[str, Any] | None = None
    evidence_delta_id: str | None = None

    if status == ExecutionStatus.COMPLETED:
        assessment = assess_evidence(attributes)

        # ── Emit EvidenceDelta if the assessment changes the hypothesis state ──
        hypothesis_id = attributes.get("hypothesis_id", "")
        experiment_id = attributes.get("experiment_id", "")

        if hypothesis_id and assessment:
            target_state = ASSESSMENT_TO_EPISTEMIC_DELTA.get(
                assessment.get("result")
            )

            if target_state is not None:
                # Load the hypothesis ClaimRow
                hyp_stmt = select(ClaimRow).where(ClaimRow.id == hypothesis_id)
                hypothesis = (await session.execute(hyp_stmt)).scalar_one_or_none()

                if hypothesis is not None:
                    prior_state_str = hypothesis.epistemic_state
                    try:
                        prior_state = EpistemicState(prior_state_str)
                    except ValueError:
                        prior_state = EpistemicState.HYPOTHESIZED

                    # Only emit a delta if the state actually changes
                    if prior_state != target_state:
                        # Build the observation summary
                        observations = attributes.get("observations", [])
                        obs_summary = (
                            f"Execution {execution_id} of experiment {experiment_id} "
                            f"produced {len(observations)} observation(s). "
                            f"Assessment: {assessment['result']}. "
                            f"Reasoning: {assessment['reasoning']}"
                        )

                        # Evidence refs from observations
                        evidence_refs: list[str] = []
                        for obs in observations:
                            if obs.get("source_ref"):
                                evidence_refs.append(obs["source_ref"])

                        # For REJECTED, we need evidence_refs (domain invariant)
                        # — but we only go to DISPUTED for contradicting, not REJECTED,
                        # so this is safe.
                        delta_id = f"edelta-{uuid4().hex[:16]}"
                        delta_payload = {
                            "hypothesis_id": hypothesis_id,
                            "experiment_id": experiment_id,
                            "execution_id": execution_id,
                            "prior_state": prior_state.value,
                            "updated_state": target_state.value,
                            "observation": obs_summary,
                            "update_method": "deterministic_assessment",
                            "evidence_refs": evidence_refs[:20],
                            "assessment_result": assessment["result"],
                            "assessment_reasoning": assessment["reasoning"],
                            "criteria_evaluated": assessment.get(
                                "criteria_evaluated", []
                            ),
                            "reviewer": requester or "system",
                            "timestamp": _utcnow_iso(),
                        }

                        audit_event = AuditEventRow(
                            id=delta_id,
                            event_type="evidence.delta",
                            actor=requester or "system",
                            target_id=hypothesis_id,
                            target_type="claim",
                            payload=delta_payload,
                            request_id=uuid4().hex,
                        )
                        session.add(audit_event)
                        await session.flush()

                        # Update the hypothesis ClaimRow
                        hypothesis.epistemic_state = target_state.value
                        # Adjust confidence: supporting -> +0.2, contradicting -> -0.2
                        # (bounded to [0.0, 1.0])
                        current_conf = hypothesis.confidence_value or 0.0
                        if target_state == EpistemicState.SUPPORTED:
                            new_conf = min(1.0, current_conf + 0.2)
                        elif target_state == EpistemicState.DISPUTED:
                            new_conf = max(0.0, current_conf - 0.2)
                        else:
                            new_conf = current_conf
                        hypothesis.confidence_value = new_conf
                        # Bump version + updated_at (ClaimRow doesn't have
                        # touch(), so we update fields directly)
                        hypothesis.version = (hypothesis.version or 1) + 1
                        hypothesis.updated_at = datetime.now(UTC)

                        evidence_delta_id = delta_id
                        _log.info(
                            "finalize_execution emitted evidence_delta=%s "
                            "hypothesis=%s %s→%s conf=%.2f→%.2f",
                            delta_id,
                            hypothesis_id,
                            prior_state.value,
                            target_state.value,
                            current_conf,
                            new_conf,
                        )

    attributes["assessment"] = assessment
    attributes["evidence_delta_id"] = evidence_delta_id

    _update_execution_attributes(entity, attributes)
    await session.flush()

    result = _attributes_to_execution(entity)
    _log.info(
        "finalize_execution execution_id=%s status=%s assessment=%s delta=%s",
        execution_id,
        status,
        assessment.get("result") if assessment else "none",
        evidence_delta_id or "none",
    )
    return result


# ── T06-D: Evidence delta retrieval ────────────────────────────────────────


async def get_evidence_deltas(
    session: AsyncSession,
    hypothesis_id: str,
) -> list[dict[str, Any]]:
    """Retrieve the ordered evidence delta audit trail for a hypothesis.

    Returns a list of delta dicts (newest first), each containing:
    - delta_id, hypothesis_id, experiment_id, execution_id
    - prior_state, updated_state
    - observation, update_method
    - evidence_refs
    - assessment_result, assessment_reasoning
    - reviewer, timestamp
    - created_at
    """
    stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.event_type == "evidence.delta",
            AuditEventRow.target_id == hypothesis_id,
        )
        .order_by(AuditEventRow.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    deltas: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        deltas.append(
            {
                "delta_id": row.id,
                "hypothesis_id": payload.get("hypothesis_id", hypothesis_id),
                "experiment_id": payload.get("experiment_id"),
                "execution_id": payload.get("execution_id"),
                "prior_state": payload.get("prior_state"),
                "updated_state": payload.get("updated_state"),
                "observation": payload.get("observation", ""),
                "update_method": payload.get("update_method", ""),
                "evidence_refs": payload.get("evidence_refs", []),
                "assessment_result": payload.get("assessment_result"),
                "assessment_reasoning": payload.get("assessment_reasoning"),
                "criteria_evaluated": payload.get("criteria_evaluated", []),
                "reviewer": payload.get("reviewer"),
                "timestamp": payload.get("timestamp"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return deltas
