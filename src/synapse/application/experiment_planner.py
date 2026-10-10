"""G05-T05 -- Evidence-Grounded Experiment Planning.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§G05-T05):

  "Define small, measurable experiments for a concept/hypothesis. Activate
   ``POST /api/v1/experiments`` + ``GET /api/v1/experiments/{id}``."

  "Planning is not execution. A proposed experiment is not evidence."

Architecture
------------

``plan_experiment()`` is the single entry point. It:

1. **Resolves the hypothesis** by loading the persisted ``ClaimRow`` with
   ``epistemic_state='hypothesized'`` and ``subject_ref`` pointing at the
   parent innovation concept entity. A missing hypothesis returns ``None``.
2. **Loads the innovation context** by reusing G05-T04's
   ``_load_persisted_concept()`` and ``critique_innovation()`` -- this gives
   the planner the concept's uncertainties, missing evidence, failure modes,
   applicable contradictions, dependency completeness, and feasibility score
   without re-implementing them.
3. **Synthesizes the plan** -- objectives, measurable success/failure
   criteria, required inputs (with explicit ``available: bool`` flags --
   unavailable resources are *reported*, never invented), interpretation
   rules, estimated effort, and execution mode.
4. **Validates invariants** by constructing a frozen ``Experiment`` domain
   record -- this enforces ``production`` mode requiring
   ``safety_limits.approved_by`` and ``networked`` mode requiring
   ``cost_limits.approved_by``.
5. **Persists idempotently** as an ``EntityRow(kind='experiment')`` with
   attributes JSON. A deterministic fingerprint (SHA-256 of hypothesis_id +
   normalized protocol + sorted metric keys + execution_mode) is stored so
   repeated equivalent planning requests reuse the same experiment identity.
6. **Returns** the persisted plan dict. No ``ObservationRecord`` is created,
   no ``EvidenceDelta`` is emitted, no hypothesis state or epistemic state
   changes -- planning is observation-free.

Epistemic safety
----------------

- The hypothesis ``ClaimRow`` is **read-only**. Its ``epistemic_state`` and
  ``confidence_value`` are never modified by planning.
- No ``AuditEventRow`` of ``event_type='evidence.delta'`` is created.
- No ``ObservationRecord`` is created (the ``Experiment.result`` field is
  always ``None`` for a plan).
- ``ExperimentResult`` is never constructed during planning -- it is the
  exclusive output of G05-T06 execution.
- Missing inputs/resources are reported via ``required_inputs[*].available =
  false`` rather than fabricated.
- Untestable assumptions are reported via ``untestable_assumptions[]`` rather
  than coerced into a metric.
- No automatic ``VERIFIED`` promotion (impossible by construction:
  ``EpistemicState`` has no ``VERIFIED`` value).

Determinism
-----------

Same input + same DB state -> same output and same persisted experiment
identity. Ties in any list are broken by stable lexical ordering.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.innovation import (
    _collect_existing_ids,
    _load_persisted_concept,
    critique_innovation,
)
from synapse.domain.experiment import (
    Experiment,
    ExperimentExecutionMode,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import ClaimRow, EntityRow

_log = get_logger("synapse.application.experiment_planner")


# ── Hard limits ────────────────────────────────────────────────────────────

MAX_PROTOCOL_CHARS = 4096
MAX_BASELINE_CHARS = 2048
MAX_OBJECTIVES = 8
MAX_CRITERIA_PER_KIND = 8  # success / failure
MAX_REQUIRED_INPUTS = 12
MAX_INTERPRETATION_RULES = 12
MAX_EVIDENCE_REFS = 30


# ── Result data classes ────────────────────────────────────────────────────


class ExperimentPlan(dict):
    """Dict subclass for the persisted plan returned by ``plan_experiment``."""


class ExperimentPlanResult(dict):
    """Top-level result envelope for ``plan_experiment``."""


# ── Helpers ────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _normalize_protocol(protocol: str) -> str:
    """Normalize a protocol string for fingerprinting.

    Strips whitespace, collapses repeated spaces, lowercases. This is a
    fingerprinting canonicalization -- the original protocol text is
    preserved verbatim in the persisted attributes.
    """
    return " ".join(protocol.lower().split())


def _compute_experiment_fingerprint(
    *,
    hypothesis_id: str,
    protocol: str,
    metric_keys: list[str],
    execution_mode: str,
) -> str:
    """SHA-256 fingerprint of the planning request.

    The fingerprint is the identity of the *plan*, not the identity of the
    hypothesis. Two different plans for the same hypothesis produce two
    different fingerprints -- this is what enables multiple experiments per
    hypothesis.
    """
    payload = "\n".join(
        [
            hypothesis_id,
            _normalize_protocol(protocol),
            "\n".join(sorted(metric_keys)),
            execution_mode,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _find_existing_experiment(
    session: AsyncSession,
    fingerprint: str,
) -> EntityRow | None:
    """Find an existing persisted experiment by its concept_fingerprint.

    Experiments are stored as ``EntityRow(kind='experiment')`` with the
    fingerprint stored in the ``attributes`` JSON under
    ``experiment_fingerprint``. We scan experiment entities and match in
    Python -- this is bounded by the number of experiments per hypothesis
    (typically 1-3) and avoids a schema change.
    """
    stmt = select(EntityRow).where(EntityRow.kind == "experiment")
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if not row.attributes:
            continue
        with _suppress_json():
            parsed = json.loads(row.attributes)
            if isinstance(parsed, dict) and parsed.get("experiment_fingerprint") == fingerprint:
                return row
    return None


class _suppress_json:
    """Suppress JSONDecodeError/TypeError around best-effort parses."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type in (json.JSONDecodeError, TypeError, ValueError)


def _attributes_to_plan(
    entity: EntityRow,
    *,
    include_audit: bool = True,
) -> dict[str, Any]:
    """Reconstruct the plan dict from a persisted ``EntityRow``.

    Used both for ``GET /api/v1/experiments/{id}`` and for returning a
    reused plan from ``plan_experiment``.
    """
    attributes: dict[str, Any] = {}
    if entity.attributes:
        with _suppress_json():
            parsed = json.loads(entity.attributes)
            if isinstance(parsed, dict):
                attributes = parsed
    plan = dict(attributes)
    plan["experiment_id"] = entity.id
    plan["canonical_name"] = entity.canonical_name
    if include_audit:
        plan["retrieved_at"] = _utcnow_iso()
    return plan


# ── Plan synthesis helpers ─────────────────────────────────────────────────


def _synthesize_objectives(
    *,
    problem_domain: str,
    uncertainties: list[str],
    missing_evidence: list[str],
    failure_modes: list[dict[str, Any]],
) -> list[str]:
    """Derive measurable objectives from the concept's evidence gaps.

    Each objective targets one uncertainty or missing-evidence item.
    Objectives are stated as measurable verification questions, NOT as
    benefit claims.
    """
    objectives: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = text.strip()
        if text and text not in seen and len(objectives) < MAX_OBJECTIVES:
            seen.add(text)
            objectives.append(text)

    # Primary objective: verify the hypothesis addresses the problem domain
    _add(
        f"Verify whether the proposed combination materially addresses "
        f"'{problem_domain[:160]}' on at least one measurable axis."
    )

    # Objectives from uncertainties
    for u in uncertainties:
        if not u:
            continue
        _add(f"Resolve uncertainty: {u[:200]}")

    # Objectives from missing evidence
    for me in missing_evidence:
        if not me:
            continue
        _add(f"Acquire missing evidence for: {me[:200]}")

    # Objectives from failure modes (the things that could break the concept)
    for fm in failure_modes:
        mode = (fm.get("mode") or "").strip()
        desc = (fm.get("description") or "").strip()
        if not mode and not desc:
            continue
        _add(f"Stress-test failure mode '{mode or 'unknown'}': {desc[:200]}")

    return objectives


def _synthesize_metrics(
    user_metrics: dict[str, Any] | None,
    *,
    uncertainties: list[str],
) -> dict[str, Any]:
    """Resolve the metric schema.

    If the caller supplied metrics, they are used verbatim (after light
    validation). Otherwise a minimal default metric is synthesized from
    the most pressing uncertainty -- but only if a measurable proxy is
    available. If no measurable proxy can be inferred, an empty dict is
    returned and the planner records the assumption as untestable.
    """
    if user_metrics:
        # Light normalization: drop empty keys, preserve user intent.
        cleaned: dict[str, Any] = {}
        for k, v in user_metrics.items():
            if not k:
                continue
            cleaned[k] = v
        return cleaned

    # Default: a single precision-style metric if any uncertainty mentions
    # a quantifiable word. This is a heuristic, not a fabrication -- the
    # planner never invents a *number*, only a metric *name and type*.
    quantifiable_hints = (
        "precision",
        "recall",
        "latency",
        "throughput",
        "accuracy",
        "coverage",
        "rate",
        "time",
        "cost",
        "f1",
    )
    for u in uncertainties:
        low = u.lower()
        for hint in quantifiable_hints:
            if hint in low:
                return {hint: "float"}
    return {}


def _synthesize_success_criteria(
    *,
    metrics: dict[str, Any],
    objectives: list[str],
) -> list[dict[str, Any]]:
    """Produce ≥1 explicit success criterion.

    If metrics are available, each metric becomes a success criterion with
    a placeholder threshold (the threshold is the experimenter's to fill in
    before execution -- we record the structure, not the value). If no
    metrics are available, we synthesize one qualitative criterion tied to
    the primary objective.
    """
    criteria: list[dict[str, Any]] = []
    if metrics:
        for name, dtype in metrics.items():
            if len(criteria) >= MAX_CRITERIA_PER_KIND:
                break
            criteria.append(
                {
                    "metric": name,
                    "dtype": dtype if isinstance(dtype, str) else str(dtype),
                    "comparator": ">=",
                    "threshold": None,  # to be set by the experimenter
                    "rationale": (
                        f"Metric '{name}' is declared in the experiment's "
                        f"metric schema; threshold value must be set before "
                        f"execution."
                    ),
                }
            )
    if not criteria and objectives:
        criteria.append(
            {
                "metric": "primary_objective_met",
                "dtype": "boolean",
                "comparator": "==",
                "threshold": True,
                "rationale": (
                    "No quantitative metric was inferable from the concept's "
                    "evidence; the experiment is judged by whether the "
                    "primary objective is met (qualitative assessment by "
                    "the experimenter)."
                ),
            }
        )
    if not criteria:
        # Last resort -- a plan with no success criterion is invalid per
        # quality gate 1. We add a placeholder that the experimenter MUST
        # revise before execution.
        criteria.append(
            {
                "metric": "experimenter_to_define",
                "dtype": "unspecified",
                "comparator": "unspecified",
                "threshold": None,
                "rationale": (
                    "No metric could be inferred from the available "
                    "evidence; the experimenter must define a measurable "
                    "success criterion before execution."
                ),
            }
        )
    return criteria


def _synthesize_failure_criteria(
    *,
    metrics: dict[str, Any],
    failure_modes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce ≥1 explicit failure criterion.

    Failure criteria are the *opposite* boundary -- the conditions under
    which the experiment would *weaken* the hypothesis, not just fail to
    support it. We tie them to either a metric threshold or an evidenced
    failure mode.
    """
    criteria: list[dict[str, Any]] = []
    if metrics:
        for name, dtype in metrics.items():
            if len(criteria) >= MAX_CRITERIA_PER_KIND:
                break
            criteria.append(
                {
                    "metric": name,
                    "dtype": dtype if isinstance(dtype, str) else str(dtype),
                    "comparator": "<",
                    "threshold": None,
                    "rationale": (
                        f"If '{name}' falls below the experimenter-set "
                        f"threshold, the experiment is recorded as "
                        f"contradicting the hypothesis."
                    ),
                }
            )
    for fm in failure_modes:
        if len(criteria) >= MAX_CRITERIA_PER_KIND:
            break
        mode = (fm.get("mode") or "").strip()
        desc = (fm.get("description") or "").strip()
        if not mode and not desc:
            continue
        criteria.append(
            {
                "metric": f"failure_mode_{mode or 'unknown'}",
                "dtype": "boolean",
                "comparator": "==",
                "threshold": True,
                "rationale": (
                    f"Observation of failure mode '{mode or 'unknown'}' "
                    f"({desc[:160]}) is recorded as contradicting evidence."
                ),
            }
        )
    if not criteria:
        criteria.append(
            {
                "metric": "no_evidence_collected",
                "dtype": "boolean",
                "comparator": "==",
                "threshold": True,
                "rationale": (
                    "If the experiment collects no usable evidence, the "
                    "result is recorded as inconclusive -- never as "
                    "supporting."
                ),
            }
        )
    return criteria


def _synthesize_required_inputs(
    *,
    components: list[dict[str, Any]],
    missing_components: list[str],
    existing_entity_ids: set[str],
    existing_fragment_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Enumerate the inputs the experiment needs.

    Returns ``(required_inputs, untestable_assumptions)``. Inputs include
    component entities (with availability = does the EntityRow exist?) and
    evidence fragments (with availability = does the EvidenceFragmentRow
    exist?). Missing components become ``available=false`` entries -- they
    are reported, not invented.
    """
    required: list[dict[str, Any]] = []
    untestable: list[str] = []
    seen_keys: set[str] = set()

    def _add(kind: str, name: str, ref: str, available: bool, rationale: str) -> None:
        key = f"{kind}:{ref}"
        if key in seen_keys or len(required) >= MAX_REQUIRED_INPUTS:
            return
        seen_keys.add(key)
        required.append(
            {
                "kind": kind,
                "name": name,
                "ref": ref,
                "available": available,
                "rationale": rationale,
            }
        )

    for comp in components:
        eid = comp.get("entity_id", "")
        name = comp.get("canonical_name", "") or eid
        available = bool(eid) and eid in existing_entity_ids
        _add(
            "component_entity",
            name,
            eid,
            available,
            (
                "Component entity is persisted in the knowledge graph."
                if available
                else "Component entity ID does not resolve -- experiment "
                "cannot proceed without it."
            ),
        )
        for ref in comp.get("evidence_refs", []) or []:
            available_ref = bool(ref) and ref in existing_fragment_ids
            _add(
                "evidence_fragment",
                f"evidence for {name}",
                ref,
                available_ref,
                (
                    "Evidence fragment is persisted in the knowledge graph."
                    if available_ref
                    else "Evidence fragment ID does not resolve -- "
                    "experiment must acquire substitute evidence."
                ),
            )

    for mc in missing_components:
        if not mc:
            continue
        _add(
            "missing_capability",
            mc,
            mc,
            False,
            "Capability is NOT_EVIDENCED in the knowledge graph; experiment "
            "must source an external provider or substitute.",
        )
        untestable.append(
            f"Assumption that a capability for '{mc[:120]}' exists is not "
            f"evidenced; experiment must either source the capability or "
            f"narrow its scope."
        )

    return required, untestable


def _synthesize_interpretation_rules(
    *,
    success_criteria: list[dict[str, Any]],
    failure_criteria: list[dict[str, Any]],
    applicable_contradictions: list[dict[str, Any]],
) -> list[str]:
    """Produce the interpretation rules: how observations map to beliefs.

    These rules are *deterministic declarations* -- they say what the
    experimenter will conclude *if* a particular observation is recorded.
    They are NOT observations themselves.
    """
    rules: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = text.strip()
        if text and text not in seen and len(rules) < MAX_INTERPRETATION_RULES:
            seen.add(text)
            rules.append(text)

    _add(
        "An observation is recorded only after the experiment is executed "
        "(G05-T06). Planning does not produce observations."
    )
    _add(
        "A supporting observation requires the experimenter to cite the "
        "specific evidence fragments that support it -- not just the metric."
    )
    _add(
        "An inconclusive observation (e.g., no evidence collected) is "
        "recorded as inconclusive -- never as supporting."
    )

    for c in success_criteria[:3]:
        metric = c.get("metric", "")
        comparator = c.get("comparator", "")
        _add(
            f"If '{metric}' {comparator} threshold is observed and backed "
            f"by cited evidence, the experiment is recorded as supporting."
        )
    for c in failure_criteria[:3]:
        metric = c.get("metric", "")
        comparator = c.get("comparator", "")
        _add(
            f"If '{metric}' {comparator} threshold is observed, the "
            f"experiment is recorded as contradicting (with evidence_refs)."
        )

    if applicable_contradictions:
        _add(
            "Pre-existing applicable contradictions are NOT erased by a "
            "supporting observation; both supporting and contradicting "
            "refs are preserved in any EvidenceDelta."
        )

    _add(
        "No observation promotes the hypothesis to VERIFIED -- that state "
        "does not exist in the EpistemicState enum."
    )
    return rules


def _classify_testability(
    *,
    untestable_assumptions: list[str],
    missing_evidence: list[str],
    required_inputs: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Classify whether the experiment is executable with available tools.

    Returns ``(executability, unknowns)`` where ``executability`` is one of:
    - ``"executable_with_available_tools"``: all required inputs are available
    - ``"requires_external_resources"``: at least one input is unavailable
    - ``"not_executable"``: critical inputs missing AND untestable
      assumptions dominate
    """
    unknowns: list[str] = []
    unavailable = [r for r in required_inputs if not r.get("available")]
    critical_unavailable = [
        r for r in unavailable if r.get("kind") == "component_entity"
    ]
    for u in untestable_assumptions:
        unknowns.append(u)
    for me in missing_evidence:
        unknowns.append(f"missing evidence: {me[:160]}")
    for r in unavailable:
        unknowns.append(
            f"unavailable input: {r.get('kind', 'unknown')} "
            f"'{r.get('name', '')[:120]}'"
        )

    if critical_unavailable and len(untestable_assumptions) >= len(
        required_inputs
    ):
        return "not_executable", unknowns
    if unavailable:
        return "requires_external_resources", unknowns
    return "executable_with_available_tools", unknowns


def _synthesize_estimated_effort(
    *,
    execution_mode: str,
    required_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rough effort estimate (range, not point).

    Dry-run is always cheap; sandbox is moderate; local/networked/production
    scale up. The estimate is a *planning placeholder*, not a commitment.
    """
    base_minutes: dict[str, int] = {
        "dry_run": 5,
        "sandbox": 30,
        "local": 60,
        "networked": 240,
        "production": 480,
    }
    base = base_minutes.get(execution_mode, 30)
    # +5 minutes per unavailable input (acquisition overhead)
    unavailable = sum(1 for r in required_inputs if not r.get("available"))
    extra = min(120, unavailable * 10)
    return {
        "min_minutes": base,
        "max_minutes": base + extra,
        "unit": "minutes",
        "caveat": (
            "Estimate is a planning placeholder; actual execution time "
            "is recorded by G05-T06."
        ),
    }


# ── Persistence ────────────────────────────────────────────────────────────


async def _persist_experiment(
    session: AsyncSession,
    *,
    experiment_id: str,
    canonical_name: str,
    fingerprint: str,
    plan: dict[str, Any],
) -> EntityRow:
    """Persist the experiment plan as an ``EntityRow(kind='experiment')``.

    The full plan is stored in ``attributes`` JSON. The fingerprint is
    stored under ``experiment_fingerprint`` for idempotent reuse. The
    hypothesis linkage is stored under ``hypothesis_id`` for traceability
    (no RelationshipRow is created -- experiments link to claims, not
    entities, so a relationship edge would be semantically wrong).
    """
    attributes = dict(plan)
    attributes["experiment_fingerprint"] = fingerprint
    attributes["planning_method"] = "deterministic"
    # Strip fields that don't belong in the persisted JSON
    attributes.pop("experiment_id", None)
    attributes.pop("canonical_name", None)
    attributes.pop("retrieved_at", None)
    attributes.pop("was_reused", None)

    entity = EntityRow(
        id=experiment_id,
        kind="experiment",
        canonical_name=canonical_name[:512],
        aliases="[]",
        attributes=json.dumps(attributes, default=str),
        description=plan.get("protocol", "")[:2048],
        version=1,
    )
    session.add(entity)
    await session.flush()
    return entity


# ── Main entry point ───────────────────────────────────────────────────────


async def plan_experiment(
    session: AsyncSession,
    hypothesis_id: str,
    *,
    protocol: str | None = None,
    baseline: str | None = None,
    metrics: dict[str, Any] | None = None,
    execution_mode: ExperimentExecutionMode | str | None = None,
    safety_limits: dict[str, Any] | None = None,
    cost_limits: dict[str, Any] | None = None,
    requester: str | None = None,
) -> ExperimentPlanResult | None:
    """Plan a measurable, reproducible experiment for a persisted hypothesis.

    Args:
        session: AsyncSession bound to the Synapse database.
        hypothesis_id: ID of the persisted hypothesis ``ClaimRow`` (created
            by G05-T03 ``generate_innovations``).
        protocol: optional human-readable protocol. If omitted, a default
            protocol is synthesized from the concept's uncertainties.
        baseline: optional baseline description. If omitted, a default is
            synthesized.
        metrics: optional metric schema (``{name: dtype}``). If omitted,
            a default is synthesized from quantifiable uncertainties.
        execution_mode: optional ``ExperimentExecutionMode`` (default
            ``dry_run``). ``production`` requires ``safety_limits.approved_by``.
        safety_limits: optional safety limits dict.
        cost_limits: optional cost limits dict.
        requester: optional actor name for audit logging.

    Returns:
        An ``ExperimentPlanResult`` dict with the persisted plan, or ``None``
        if the hypothesis does not resolve to a persisted ClaimRow.

    Epistemic safety:
        - The hypothesis ``ClaimRow`` is read-only.
        - No ``ObservationRecord`` is created.
        - No ``EvidenceDelta`` is emitted.
        - No hypothesis state transition occurs.
        - No automatic VERIFIED promotion (impossible by construction).
    """
    request_id = uuid4().hex

    # ── Resolve the execution mode ──────────────────────────────────────
    if execution_mode is None:
        mode_value = ExperimentExecutionMode.DRY_RUN.value
    elif isinstance(execution_mode, ExperimentExecutionMode):
        mode_value = execution_mode.value
    else:
        mode_value = str(execution_mode)
    # Validate against the enum -- raises ValueError on unknown values
    try:
        mode_enum = ExperimentExecutionMode(mode_value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown execution_mode={mode_value!r}; expected one of "
            f"{[m.value for m in ExperimentExecutionMode]}"
        ) from exc

    # ── Resolve the hypothesis ──────────────────────────────────────────
    hyp_stmt = select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    hypothesis = (await session.execute(hyp_stmt)).scalar_one_or_none()
    if hypothesis is None:
        _log.info(
            "plan_experiment hypothesis_id=%s not found -- returning None",
            hypothesis_id,
        )
        return None

    # The hypothesis must be a hypothesized claim tied to a concept entity.
    # We don't strictly require epistemic_state="hypothesized" -- the
    # hypothesis may have been transitioned by G05-T06 in a future session.
    # We DO require subject_ref to point at a persisted concept entity.
    concept_id = hypothesis.subject_ref or ""
    if not concept_id:
        _log.info(
            "plan_experiment hypothesis_id=%s has no subject_ref -- "
            "cannot link to a concept",
            hypothesis_id,
        )
        return None

    concept = await _load_persisted_concept(session, concept_id)
    if concept is None:
        _log.info(
            "plan_experiment concept_id=%s (from hypothesis %s) not found",
            concept_id,
            hypothesis_id,
        )
        return None

    # ── Load critique context (reuse G05-T04) ───────────────────────────
    critique = await critique_innovation(
        session, concept_id, requester=requester or "g05-t05-experiment-planner"
    )
    if critique is None:
        critique = {}

    # ── Collect existing IDs for availability checks ───────────────────
    existing_ids = await _collect_existing_ids(session)
    existing_entity_ids = existing_ids["entities"]
    existing_fragment_ids = existing_ids["fragments"]

    # ── Extract concept/critique context for synthesis ─────────────────
    attributes = concept.get("attributes", {})
    problem_domain = attributes.get("problem_domain", "") or ""
    concept_uncertainties = list(attributes.get("uncertainties", []) or [])
    hypothesis_evidence_refs = list(concept.get("hypothesis_evidence_refs", []) or [])
    components = concept.get("component_links", []) or []
    missing_components = list(critique.get("architecture", {}).get("missing_components", []) or [])
    failure_modes = list(critique.get("failure_modes", []) or [])
    applicable_contradictions = list(critique.get("applicable_contradictions", []) or [])
    # critique.unknowns is a list of strings
    critique_unknowns = list(critique.get("unknowns", []) or [])
    # missing evidence is encoded as unknowns entries that mention "evidence"
    missing_evidence = [u for u in critique_unknowns if "evidence" in u.lower()]

    # ── Synthesize the plan fields ─────────────────────────────────────
    # Protocol
    if protocol and protocol.strip():
        protocol_text = protocol.strip()[:MAX_PROTOCOL_CHARS]
    else:
        # Default protocol derived from the primary uncertainty
        primary_uncertainty = (
            concept_uncertainties[0]
            if concept_uncertainties
            else "the proposed combination's actual behavior"
        )
        protocol_text = (
            f"Dry-run simulation of the proposed combination to test "
            f"'{primary_uncertainty[:240]}'. For each component, retrieve "
            f"its documented capabilities and constraints; for each "
            f"integration point, check whether documented dependencies "
            f"conflict. Record observations as evidence fragments (no "
            f"fabricated outcomes)."
        )

    # Baseline
    if baseline and baseline.strip():
        baseline_text = baseline.strip()[:MAX_BASELINE_CHARS]
    else:
        baseline_text = (
            "Random selection of equivalent components from the knowledge "
            "graph, holding the metric constant. The baseline is the "
            "default behavior without the proposed integration."
        )

    # Metrics
    metrics_dict = _synthesize_metrics(
        metrics, uncertainties=concept_uncertainties
    )

    # Objectives
    objectives = _synthesize_objectives(
        problem_domain=problem_domain,
        uncertainties=concept_uncertainties,
        missing_evidence=missing_evidence,
        failure_modes=failure_modes,
    )

    # Success / failure criteria
    success_criteria = _synthesize_success_criteria(
        metrics=metrics_dict, objectives=objectives
    )
    failure_criteria = _synthesize_failure_criteria(
        metrics=metrics_dict, failure_modes=failure_modes
    )

    # Required inputs + untestable assumptions
    required_inputs, untestable_assumptions = _synthesize_required_inputs(
        components=components,
        missing_components=missing_components,
        existing_entity_ids=existing_entity_ids,
        existing_fragment_ids=existing_fragment_ids,
    )

    # Interpretation rules
    interpretation_rules = _synthesize_interpretation_rules(
        success_criteria=success_criteria,
        failure_criteria=failure_criteria,
        applicable_contradictions=applicable_contradictions,
    )

    # Executability classification
    executability, unknowns = _classify_testability(
        untestable_assumptions=untestable_assumptions,
        missing_evidence=missing_evidence,
        required_inputs=required_inputs,
    )

    # Estimated effort
    estimated_effort = _synthesize_estimated_effort(
        execution_mode=mode_value, required_inputs=required_inputs
    )

    # Resource constraints (mirror safety/cost limits for visibility)
    resource_constraints: list[dict[str, Any]] = []
    if safety_limits:
        for k, v in safety_limits.items():
            resource_constraints.append(
                {"kind": "safety", "name": k, "value": v, "available": True}
            )
    if cost_limits:
        for k, v in cost_limits.items():
            resource_constraints.append(
                {"kind": "cost", "name": k, "value": v, "available": True}
            )
    # Always note the no-vector-db constraint from G04 ADRs
    resource_constraints.append(
        {
            "kind": "architectural",
            "name": "no_vector_database",
            "value": True,
            "available": True,
            "rationale": "Per ADR-0011: no vector DB -- experiments must "
            "use lexical + structured + graph retrieval only.",
        }
    )

    # Applicable contradictions (preserved for the experimenter)
    preserved_contradictions = [
        {
            "claim_id": ac.get("claim_id", ""),
            "contradicting_refs": list(ac.get("contradicting_refs", []) or []),
        }
        for ac in applicable_contradictions
    ]

    # Evidence references (preserved, deduplicated, capped)
    evidence_refs: list[str] = []
    seen_refs: set[str] = set()
    for ref in hypothesis_evidence_refs:
        if ref and ref not in seen_refs:
            seen_refs.add(ref)
            evidence_refs.append(ref)
    for comp in components:
        for ref in comp.get("evidence_refs", []) or []:
            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                evidence_refs.append(ref)
    evidence_refs = evidence_refs[:MAX_EVIDENCE_REFS]

    # Testable vs untestable assumptions (split for visibility)
    testable_assumptions = [
        a for a in (attributes.get("uncertainties", []) or [])
        if a not in untestable_assumptions
    ][:10]

    # ── Validate invariants via the frozen Experiment domain record ────
    # This enforces: production → safety_limits.approved_by, etc.
    Experiment(
        hypothesis_id=hypothesis_id,
        protocol=protocol_text,
        baseline=baseline_text,
        metrics=metrics_dict,
        safety_limits=safety_limits or {},
        cost_limits=cost_limits or {},
        execution_mode=mode_enum,
        result=None,  # planning NEVER produces a result
        artifact_refs=[],
    )

    # ── Compute fingerprint + check for an existing equivalent plan ────
    fingerprint = _compute_experiment_fingerprint(
        hypothesis_id=hypothesis_id,
        protocol=protocol_text,
        metric_keys=list(metrics_dict.keys()),
        execution_mode=mode_value,
    )

    existing_entity = await _find_existing_experiment(session, fingerprint)
    if existing_entity is not None:
        reused_plan = _attributes_to_plan(existing_entity)
        reused_plan["was_reused"] = True
        _log.info(
            "plan_experiment reused experiment_id=%s fingerprint=%s "
            "hypothesis_id=%s",
            existing_entity.id,
            fingerprint[:16],
            hypothesis_id,
        )
        return ExperimentPlanResult(
            experiment=reused_plan,
            unknowns=unknowns,
            executability=executability,
            request_id=request_id,
            planned_at=_utcnow_iso(),
        )

    # ── Persist the new experiment ─────────────────────────────────────
    experiment_id = f"exp-{fingerprint[:16]}"
    canonical_name = (
        f"Experiment for hypothesis {hypothesis_id[:24]} "
        f"({mode_value})"
    )

    plan_payload = ExperimentPlan(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        concept_id=concept_id,
        protocol=protocol_text,
        baseline=baseline_text,
        objectives=objectives,
        success_criteria=success_criteria,
        failure_criteria=failure_criteria,
        metrics=metrics_dict,
        required_inputs=required_inputs,
        dependencies=[
            r for r in required_inputs if r.get("kind") == "component_entity"
        ],
        resource_constraints=resource_constraints,
        estimated_effort=estimated_effort,
        execution_mode=mode_value,
        safety_limits=safety_limits or {},
        cost_limits=cost_limits or {},
        evidence_refs=evidence_refs,
        applicable_contradictions=preserved_contradictions,
        testable_assumptions=testable_assumptions,
        untestable_assumptions=untestable_assumptions,
        missing_evidence=missing_evidence,
        interpretation_rules=interpretation_rules,
        executability=executability,
        result=None,  # planning NEVER produces a result
        artifact_refs=[],
    )

    await _persist_experiment(
        session,
        experiment_id=experiment_id,
        canonical_name=canonical_name,
        fingerprint=fingerprint,
        plan=plan_payload,
    )

    plan_payload["was_reused"] = False

    _log.info(
        "plan_experiment persisted experiment_id=%s fingerprint=%s "
        "hypothesis_id=%s executability=%s success_criteria=%d "
        "failure_criteria=%d",
        experiment_id,
        fingerprint[:16],
        hypothesis_id,
        executability,
        len(success_criteria),
        len(failure_criteria),
    )

    return ExperimentPlanResult(
        experiment=plan_payload,
        unknowns=unknowns,
        executability=executability,
        request_id=request_id,
        planned_at=_utcnow_iso(),
    )


async def get_experiment(
    session: AsyncSession,
    experiment_id: str,
) -> dict[str, Any] | None:
    """Retrieve a persisted experiment plan by ID.

    Returns the plan dict, or ``None`` if no ``EntityRow(kind='experiment')``
    exists with the given ID.
    """
    stmt = select(EntityRow).where(
        EntityRow.id == experiment_id,
        EntityRow.kind == "experiment",
    )
    entity = (await session.execute(stmt)).scalar_one_or_none()
    if entity is None:
        return None
    return _attributes_to_plan(entity, include_audit=True)
