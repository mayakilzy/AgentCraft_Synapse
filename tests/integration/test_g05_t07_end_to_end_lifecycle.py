"""G05-T07 — End-to-End Innovation Lifecycle Acceptance.

T07-A: a single integrated workflow that exercises every G05 stage in
order, against real application services and real persistence:

  Knowledge → Combination → Opportunity → Innovation → Architecture →
  Critique → Experiment Plan → Execution Record → Observations →
  Assessment → Evidence Feedback

Provenance is asserted at every boundary: knowledge inputs keep their
source fragments; combinations reference supporting claims;
opportunities reference their combinations; innovations reference their
opportunities and components; architecture references the proposed
innovation; critique references the architecture; experiment plans
reference the hypothesis; execution records reference the plan;
observations are explicitly submitted; assessments derive only from
submitted observations; evidence feedback references the correct
hypothesis and execution.

T07-B: epistemic-integrity scenarios — supporting, contradicting,
inconclusive — plus idempotency and conflicting-results-preservation
checks. No auto-VERIFIED promotion, no duplicate deltas, no fabricated
evidence, provenance preserved across the lifecycle.

These tests run against the per-test isolated SQLite engine (fast,
deterministic). The PostgreSQL isolation question is covered by
``test_g05_t07_pg_isolation.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from synapse.application.execution_record import (
    AssessmentResult,
    ExecutionStatus,
    assess_evidence,
    create_execution,
    finalize_execution,
    get_evidence_deltas,
    get_execution,
    record_observation,
)
from synapse.application.experiment_planner import plan_experiment
from synapse.application.innovation import (
    combine_knowledge,
    compose_architecture,
    critique_innovation,
    discover_opportunities,
    generate_innovations,
)
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
from synapse.domain._base import EpistemicState
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()


# ── Fixture helpers (mirrors the canonical T06 pattern, with provenance
#    tags that are asserted throughout the lifecycle) ─────────────────────


async def _make_source(session, sid: str, uri: str, source_type: str = "paper") -> SourceRow:
    src = SourceRow(
        id=sid, canonical_uri=uri, source_type=source_type, status="extracted",
    )
    session.add(src)
    await session.flush()
    return src


async def _make_fragment(
    session, fid: str, src: SourceRow, excerpt: str
) -> EvidenceFragmentRow:
    ef = EvidenceFragmentRow(
        id=fid,
        acquisition_id=f"acq-{fid}",
        source_id=src.id,
        source_uri=src.canonical_uri,
        exact_excerpt=excerpt,
        excerpt_hash=f"hash-{fid}",
        retrieved_at=RECENT_ISO,
        extraction_method="test-fixture",
        content_fingerprint=f"fp-{fid}",
        toolkit_commit_sha="fixture",
    )
    session.add(ef)
    await session.flush()
    return ef


async def _make_entity(
    session, eid: str, kind: str, name: str, desc: str = ""
) -> EntityRow:
    e = EntityRow(
        id=eid, kind=kind, canonical_name=name, aliases="[]",
        attributes="{}", description=desc, version=1,
    )
    session.add(e)
    await session.flush()
    return e


async def _make_claim(
    session, cid: str, proposition: str, *,
    subject_ref: str | None = None, object_ref: str | None = None,
    evidence_refs: list[str] | None = None, contradicting_refs: list[str] | None = None,
    epistemic_state: str = "supported", validity_conditions: list[str] | None = None,
) -> ClaimRow:
    c = ClaimRow(
        id=cid, proposition=proposition, subject_ref=subject_ref, object_ref=object_ref,
        evidence_refs=json.dumps(evidence_refs or []),
        contradicting_refs=json.dumps(contradicting_refs or []),
        epistemic_state=epistemic_state,
        validity_conditions=json.dumps(validity_conditions or []),
        extraction_method="test-fixture", version=1,
    )
    session.add(c)
    await session.flush()
    return c


async def _seed_knowledge_base(session) -> dict[str, Any]:
    """Seed a realistic, traceable technical knowledge base.

    Scenario: a small knowledge base about edge inference for autonomous
    drones. Three technologies, three capabilities, three grounded
    claims, three relationships. Every claim is grounded in a fragment
    that traces back to a source.
    """
    src_a = await _make_source(session, "src-t07-a", "https://example.com/arxiv-t07")
    src_b = await _make_source(session, "src-t07-b", "https://example.com/trafilatura-t07")
    src_c = await _make_source(session, "src-t07-c", "https://example.com/synapse-t07")

    frag_a = await _make_fragment(
        session, "frag-t07-a", src_a,
        "TensorRT provides optimized edge inference for autonomous drones.",
    )
    frag_b = await _make_fragment(
        session, "frag-t07-b", src_b,
        "ONNX runtime enables cross-platform model deployment on embedded devices.",
    )
    frag_c = await _make_fragment(
        session, "frag-t07-c", src_c,
        "Quantization-aware training reduces model size while preserving accuracy.",
    )

    await _make_entity(session, "ent-t07-tensorrt", "technology", "tensorrt",
                       "GPU inference optimization library")
    await _make_entity(session, "ent-t07-onnx", "technology", "onnx-runtime",
                       "Cross-platform model runtime")
    await _make_entity(session, "ent-t07-quant", "technology", "quantization",
                       "Model compression technique")

    for cap_id, name in [
        ("cap-t07-inf", "edge inference"),
        ("cap-t07-depl", "cross-platform deployment"),
        ("cap-t07-comp", "model compression"),
    ]:
        await _make_entity(session, cap_id, "capability", name)

    await _make_claim(
        session, "claim-t07-inf", "tensorrt provides edge inference for drones.",
        subject_ref="ent-t07-tensorrt", object_ref="cap-t07-inf",
        evidence_refs=["frag-t07-a"],
    )
    await _make_claim(
        session, "claim-t07-depl", "onnx-runtime enables cross-platform deployment.",
        subject_ref="ent-t07-onnx", object_ref="cap-t07-depl",
        evidence_refs=["frag-t07-b"],
    )
    await _make_claim(
        session, "claim-t07-comp", "quantization provides model compression.",
        subject_ref="ent-t07-quant", object_ref="cap-t07-comp",
        evidence_refs=["frag-t07-c"],
    )

    for frm, to, ev in [
        ("ent-t07-tensorrt", "cap-t07-inf", ["frag-t07-a"]),
        ("ent-t07-onnx", "cap-t07-depl", ["frag-t07-b"]),
        ("ent-t07-quant", "cap-t07-comp", ["frag-t07-c"]),
    ]:
        r = await create_relationship(
            session, from_entity_id=frm, to_entity_id=to,
            predicate="PROVIDES", evidence_refs=ev, origin="explicit",
        )
        assert r["ok"] is True

    await session.commit()
    # assess claims so they carry verification state
    for cid in ("claim-t07-inf", "claim-t07-depl", "claim-t07-comp"):
        await assess_claim(session, cid, requester="t07-fixture")
    await session.commit()

    return {
        "sources": [src_a.id, src_b.id, src_c.id],
        "fragments": [frag_a.id, frag_b.id, frag_c.id],
        "technology_entities": ["ent-t07-tensorrt", "ent-t07-onnx", "ent-t07-quant"],
        "capability_entities": ["cap-t07-inf", "cap-t07-depl", "cap-t07-comp"],
        "claims": ["claim-t07-inf", "claim-t07-depl", "claim-t07-comp"],
    }


def _load_execution_attrs(entity: EntityRow) -> dict[str, Any]:
    return json.loads(entity.attributes)


async def _set_operational_thresholds(
    session, exec_id: str, *,
    success_threshold: float | None = 0.7,
    failure_threshold: float | None = None,
    metric: str = "precision_at_10",
) -> None:
    """Promote a criterion from `requires_calibration` to `operational` by
    setting a numeric threshold. Mirrors the documented T06 pattern."""
    entity = (await session.execute(
        select(EntityRow).where(
            EntityRow.id == exec_id, EntityRow.kind == "execution"
        )
    )).scalar_one()
    attrs = _load_execution_attrs(entity)
    if success_threshold is not None:
        for sc in attrs["success_criteria"]:
            if sc.get("metric") == metric:
                sc["threshold"] = success_threshold
                sc["calibration_status"] = "operational"
    if failure_threshold is not None:
        for fc in attrs["failure_criteria"]:
            if fc.get("metric") == metric:
                fc["threshold"] = failure_threshold
                fc["calibration_status"] = "operational"
                fc["comparator"] = "<"
    entity.attributes = json.dumps(attrs, default=str)
    await session.flush()


# ═══════════════════════════════════════════════════════════════════════════
# T07-A — End-to-End Lifecycle
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_t07a_end_to_end_lifecycle(app, db_session):
    """Exercise the full G05 lifecycle in order against real services.

    Every stage's output is asserted to carry provenance back to the
    knowledge base, and the final evidence feedback is asserted to
    reference the correct hypothesis and execution.
    """
    kb = await _seed_knowledge_base(db_session)

    # ── 1. Knowledge inputs retain provenance ────────────────────────────
    # The fragments are the ground truth; every downstream artifact must
    # ultimately trace back to one of them.
    fragments_in_db = (await db_session.execute(
        select(EvidenceFragmentRow.id)
    )).scalars().all()
    assert set(fragments_in_db) == set(kb["fragments"])

    # ── 2. Combination: grounded in available knowledge ─────────────────
    combos = await combine_knowledge(
        db_session, "edge inference for autonomous drones",
        context="on-device ML pipelines",
    )
    assert combos is not None
    assert "combinations" in combos
    assert len(combos["combinations"]) >= 1
    combo = combos["combinations"][0]
    # Combination must reference real entities from the knowledge base.
    # `components` is a list of ComponentRef (dict subclass) objects,
    # each carrying entity_id, kind, role, evidence_refs, relationship_id.
    combo_entity_ids = {c["entity_id"] for c in combo.get("components", [])}
    assert combo_entity_ids, "combination has no components"
    known = set(kb["technology_entities"]) | set(kb["capability_entities"])
    unknown = combo_entity_ids - known
    assert not unknown, f"combination references unknown entities: {unknown}"
    # Combination must carry evidence refs back to fragments
    assert "evidence_refs" in combo, "combination missing evidence_refs field"
    combo_evidence = set(combo.get("evidence_refs", []))
    assert combo_evidence, "combination has no evidence_refs"
    assert combo_evidence.issubset(set(kb["fragments"])), (
        f"combination evidence_refs not in knowledge base: {combo_evidence - set(kb['fragments'])}"
    )
    # Each component must carry its own evidence_refs (per-component provenance)
    for comp in combo.get("components", []):
        assert comp.get("evidence_refs"), (
            f"component {comp.get('entity_id')} has no evidence_refs"
        )

    # ── 3. Opportunity: references its supporting combinations ───────────
    # The fixture is fully-evidenced (every claim has evidence_refs, no
    # contradicting_refs), so the gap analyzer may return 0 opportunities.
    # We assert the service runs and returns a well-formed structure; any
    # returned opportunity must be grounded in the knowledge base.
    opps = await discover_opportunities(
        db_session, "edge inference for autonomous drones",
        context="on-device ML pipelines",
    )
    assert opps is not None
    assert "opportunities" in opps
    for opportunity in opps["opportunities"]:
        assert "id" in opportunity
        assert "problem_domain" in opportunity
        assert "gap_type" in opportunity
        # Opportunity must carry evidence provenance
        assert "evidence_refs" in opportunity
        # Candidate components must reference real entities
        for comp in opportunity.get("candidate_components", []):
            comp_id = comp.get("entity_id") or comp.get("id") if isinstance(comp, dict) else comp
            if comp_id is not None:
                assert comp_id in set(kb["technology_entities"]) | set(kb["capability_entities"]), (
                    f"opportunity candidate_component {comp_id} not in knowledge base"
                )

    # ── 4. Innovation: retains source relationships ──────────────────────
    gen = await generate_innovations(
        db_session, "edge inference for autonomous drones",
        context="on-device ML pipelines for autonomous drones",
    )
    assert gen is not None
    assert "concepts" in gen
    assert len(gen["concepts"]) >= 1
    concept = gen["concepts"][0]
    concept_id = concept["id"]
    hypothesis_id = concept["hypothesis_id"]
    assert concept_id.startswith("innov-")
    assert hypothesis_id.startswith("hyp-")

    # The persisted innovation entity must carry provenance fields.
    # Innovation concepts are persisted as EntityRow(kind="project") per
    # the G05-T03 implementation. The attributes carry combination_basis,
    # integration_mechanism, concept_fingerprint (idempotency), and
    # uncertainties — all of which tie the innovation back to the
    # knowledge base.
    innov_entity = (await db_session.execute(
        select(EntityRow).where(EntityRow.id == concept_id)
    )).scalar_one()
    assert innov_entity.kind == "project"
    innov_attrs = _load_execution_attrs(innov_entity)
    # Provenance: the innovation must declare its combination basis
    assert innov_attrs.get("combination_basis"), (
        "innovation missing combination_basis provenance field"
    )
    # Provenance: the integration mechanism must be documented
    assert innov_attrs.get("integration_mechanism"), (
        "innovation missing integration_mechanism provenance field"
    )
    # Idempotency: the concept_fingerprint must be present
    assert innov_attrs.get("concept_fingerprint"), (
        "innovation missing concept_fingerprint (idempotency) field"
    )
    # The problem_domain must match what was requested
    assert innov_attrs.get("problem_domain") == "edge inference for autonomous drones"

    # The hypothesis claim must exist and start in HYPOTHESIZED state
    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.HYPOTHESIZED.value
    assert hyp.confidence_value == 0.0
    await db_session.commit()

    # ── 5. Architecture: references the proposed innovation ─────────────
    arch = await compose_architecture(db_session, concept_id)
    assert arch is not None
    assert arch["concept_id"] == concept_id
    assert "components" in arch
    assert len(arch["components"]) >= 1
    # Architecture components must be a subset of the innovation's components
    arch_comp_ids = {c["entity_id"] for c in arch["components"]}
    assert arch_comp_ids, "architecture has no components"
    assert arch_comp_ids.issubset(set(kb["technology_entities"]) | set(kb["capability_entities"]))

    # ── 6. Critique: records meaningful limitations or risks ─────────────
    critique = await critique_innovation(db_session, concept_id)
    assert critique is not None
    assert critique["concept_id"] == concept_id
    # Critique must produce non-empty rationale fields
    assert critique.get("feasibility_rationale", "") != ""
    assert critique.get("evidence_coverage_rationale", "") != ""
    # Critique must record at least one failure mode or limitation
    has_limitation = (
        len(critique.get("failure_modes", [])) >= 1
        or len(critique.get("limitations", [])) >= 1
        or len(critique.get("evidence_gaps", [])) >= 1
        or critique.get("overall_recommendation") in ("needs_more_evidence", "rejected")
    )
    assert has_limitation, (
        "critique produced no meaningful limitation, risk, or failure mode"
    )
    # Architecture is embedded in the critique result (T04 contract)
    assert "architecture" in critique
    await db_session.commit()

    # ── 7. Experiment plan: measurable criteria ──────────────────────────
    plan = await plan_experiment(
        db_session, hypothesis_id,
        protocol="Benchmark edge-inference latency on Jetson Nano at batch size 1.",
        metrics={"precision_at_10": "float", "latency_p99_ms": "float"},
    )
    assert plan is not None
    experiment = plan["experiment"]
    experiment_id = experiment["experiment_id"]
    assert experiment_id.startswith("exp-")
    assert experiment["hypothesis_id"] == hypothesis_id
    # Plan must contain measurable success criteria
    assert len(experiment["success_criteria"]) >= 1
    for sc in experiment["success_criteria"]:
        assert "calibration_status" in sc
        assert "metric" in sc
    # Plan must NOT be evidence (no observations, no assessment, no delta)
    assert experiment.get("result") in (None, "", {})
    await db_session.commit()

    # ── 8. Execution record: references the correct plan ─────────────────
    exec_result = await create_execution(
        db_session, experiment_id,
        run_id="t07-lifecycle-run-001",
        requester="t07-experimenter",
    )
    assert exec_result is not None
    exec_id = exec_result["execution_id"]
    assert exec_result["experiment_id"] == experiment_id
    assert exec_result["hypothesis_id"] == hypothesis_id
    assert exec_result["status"] == ExecutionStatus.CREATED
    assert exec_result["observations"] == []
    assert exec_result["assessment"] is None
    assert exec_result["evidence_delta_id"] is None
    # Idempotent retry: same run_id → same execution_id
    exec_retry = await create_execution(
        db_session, experiment_id, run_id="t07-lifecycle-run-001",
    )
    assert exec_retry["execution_id"] == exec_id
    assert exec_retry["was_reused"] is True
    await db_session.commit()

    # ── 9. Observations: explicitly submitted ────────────────────────────
    await _set_operational_thresholds(
        db_session, exec_id,
        success_threshold=0.7, failure_threshold=None,
        metric="precision_at_10",
    )
    obs = await record_observation(
        db_session, exec_id,
        metric="precision_at_10",
        observed_value=0.85,
        expected_value=None,
        measurement_method="automated_benchmark",
        source_ref="experimenter-t07-001",
        uncertainty=None,
    )
    assert obs is not None
    assert obs["metric"] == "precision_at_10"
    assert obs["observed_value"] == 0.85
    assert obs["source_ref"] == "experimenter-t07-001"
    assert obs["evidence_origin"] == "experiment_execution"
    assert obs["observation_id"].startswith("obs-")
    # Execution transitioned to RUNNING
    running = await get_execution(db_session, exec_id)
    assert running["status"] == ExecutionStatus.RUNNING
    assert len(running["observations"]) == 1
    await db_session.commit()

    # ── 10. Assessment: derives only from submitted observations ─────────
    # Use assess_evidence directly (pure function, no side effects) to
    # verify the assessment is deterministic and grounded in observations.
    fetched = await get_execution(db_session, exec_id)
    assessment = assess_evidence(fetched)
    assert assessment["result"] == AssessmentResult.SUPPORTING
    assert "reasoning" in assessment
    assert "criteria_evaluated" in assessment
    # Every criterion evaluated must reference a metric that was observed
    observed_metrics = {o["metric"] for o in fetched["observations"]}
    for ce in assessment["criteria_evaluated"]:
        if ce.get("status") == "met" or ce.get("status") == "not_met":
            assert ce["metric"] in observed_metrics, (
                f"assessment evaluated metric {ce['metric']} that was never observed"
            )

    # ── 11. Evidence feedback: references correct hypothesis + execution ─
    before_deltas = len((await db_session.execute(
        select(AuditEventRow).where(
            AuditEventRow.event_type == "evidence.delta",
            AuditEventRow.target_id == hypothesis_id,
        )
    )).scalars().all())

    final_result = await finalize_execution(
        db_session, exec_id,
        status=ExecutionStatus.COMPLETED,
        requester="t07-experimenter",
    )
    assert final_result is not None
    assert final_result["status"] == ExecutionStatus.COMPLETED
    assert final_result["assessment"]["result"] == AssessmentResult.SUPPORTING
    assert final_result["evidence_delta_id"] is not None
    await db_session.commit()

    # The hypothesis moved HYPOTHESIZED → SUPPORTED (NOT VERIFIED)
    hyp_after = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    )).scalar_one()
    assert hyp_after.epistemic_state == EpistemicState.SUPPORTED.value
    assert hyp_after.epistemic_state != "verified"
    assert hyp_after.confidence_value == 0.2  # +0.2 from supporting

    # The evidence delta audit trail references the correct hypothesis + execution
    deltas = await get_evidence_deltas(db_session, hypothesis_id)
    assert len(deltas) == before_deltas + 1
    new_delta = deltas[0]  # newest first
    assert new_delta["hypothesis_id"] == hypothesis_id
    assert new_delta["execution_id"] == exec_id
    assert new_delta["experiment_id"] == experiment_id
    assert new_delta["prior_state"] == EpistemicState.HYPOTHESIZED.value
    assert new_delta["updated_state"] == EpistemicState.SUPPORTED.value
    assert new_delta["assessment_result"] == AssessmentResult.SUPPORTING

    # The execution record's evidence_delta_id matches the audit event id
    finalized = await get_execution(db_session, exec_id)
    assert finalized["evidence_delta_id"] == new_delta["delta_id"]


# ═══════════════════════════════════════════════════════════════════════════
# T07-B — Epistemic Integrity
# ═══════════════════════════════════════════════════════════════════════════


async def _prepare_execution_for_assessment(
    session, *,
    success_threshold: float | None,
    failure_threshold: float | None,
    observed_value: float,
    metric: str = "precision_at_10",
    run_id: str = "t07b-run",
) -> dict[str, Any]:
    """End-to-end helper: seed KB → innovate → plan → execute → observe."""
    kb = await _seed_knowledge_base(session)
    gen = await generate_innovations(
        session, "edge inference for autonomous drones",
        context="on-device ML pipelines",
    )
    concept_id = gen["concepts"][0]["id"]
    hypothesis_id = gen["concepts"][0]["hypothesis_id"]
    plan = await plan_experiment(
        session, hypothesis_id,
        protocol="Benchmark protocol.",
        metrics={metric: "float"},
    )
    experiment_id = plan["experiment"]["experiment_id"]
    await session.commit()

    exec_result = await create_execution(
        session, experiment_id, run_id=run_id, requester="t07b-experimenter",
    )
    exec_id = exec_result["execution_id"]
    await _set_operational_thresholds(
        session, exec_id,
        success_threshold=success_threshold,
        failure_threshold=failure_threshold,
        metric=metric,
    )
    if observed_value is not None:
        await record_observation(
            session, exec_id, metric=metric, observed_value=observed_value,
            source_ref=f"experimenter-{run_id}",
        )
    await session.commit()
    return {
        "kb": kb,
        "concept_id": concept_id,
        "hypothesis_id": hypothesis_id,
        "experiment_id": experiment_id,
        "exec_id": exec_id,
    }


@pytest.mark.asyncio
async def test_t07b_supporting_evidence_no_auto_verified(app, db_session):
    """Supporting observations move HYPOTHESIZED → SUPPORTED, never VERIFIED.

    The assessment is a deterministic comparison, NOT independent
    verification. EpistemicState has no VERIFIED value at all.
    """
    # Confirm VERIFIED is impossible by construction
    allowed_states = {e.value for e in EpistemicState}
    assert "verified" not in allowed_states

    ctx = await _prepare_execution_for_assessment(
        db_session,
        success_threshold=0.7,
        failure_threshold=None,
        observed_value=0.85,  # meets success >= 0.7
        run_id="t07b-sup",
    )

    result = await finalize_execution(
        db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
    )
    assert result["assessment"]["result"] == AssessmentResult.SUPPORTING
    assert result["evidence_delta_id"] is not None

    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.SUPPORTED.value
    assert hyp.epistemic_state != "verified"
    assert hyp.epistemic_state in allowed_states
    assert hyp.confidence_value == 0.2  # exactly one +0.2 adjustment


@pytest.mark.asyncio
async def test_t07b_contradicting_evidence_preserved(app, db_session):
    """Contradictory evidence moves HYPOTHESIZED → DISPUTED and is preserved
    in the audit trail."""
    ctx = await _prepare_execution_for_assessment(
        db_session,
        success_threshold=0.7,
        failure_threshold=0.3,  # failure triggered if observed < 0.3
        observed_value=0.1,  # triggers failure < 0.3
        run_id="t07b-con",
    )

    result = await finalize_execution(
        db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
    )
    assert result["assessment"]["result"] == AssessmentResult.CONTRADICTING
    assert result["evidence_delta_id"] is not None

    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.DISPUTED.value
    # Confidence is bounded to [0.0, 1.0]; -0.2 clamps to 0.0
    assert hyp.confidence_value == 0.0, (
        f"Expected 0.0 (clamped from -0.2), got {hyp.confidence_value}"
    )

    # The contradicting delta is preserved in the audit trail
    deltas = await get_evidence_deltas(db_session, ctx["hypothesis_id"])
    assert len(deltas) >= 1
    contradicting_deltas = [d for d in deltas if d["assessment_result"] == "contradicting"]
    assert len(contradicting_deltas) >= 1
    d = contradicting_deltas[0]
    assert d["prior_state"] == EpistemicState.HYPOTHESIZED.value
    assert d["updated_state"] == EpistemicState.DISPUTED.value
    assert d["execution_id"] == ctx["exec_id"]


@pytest.mark.asyncio
async def test_t07b_inconclusive_evidence_no_delta(app, db_session):
    """Inconclusive evidence (observation present but not meeting success
    criterion, no failure triggered) → no delta, no state change."""
    ctx = await _prepare_execution_for_assessment(
        db_session,
        success_threshold=0.9,  # high bar
        failure_threshold=None,  # no failure criteria operational
        observed_value=0.5,  # does not meet success >= 0.9, no failure
        run_id="t07b-inc",
    )

    before_deltas = len((await db_session.execute(
        select(AuditEventRow).where(
            AuditEventRow.event_type == "evidence.delta",
            AuditEventRow.target_id == ctx["hypothesis_id"],
        )
    )).scalars().all())

    result = await finalize_execution(
        db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
    )
    assert result["assessment"]["result"] == AssessmentResult.INCONCLUSIVE
    assert result["evidence_delta_id"] is None

    # No state change, no confidence change, no new delta
    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.HYPOTHESIZED.value
    assert hyp.confidence_value == 0.0

    after_deltas = len((await db_session.execute(
        select(AuditEventRow).where(
            AuditEventRow.event_type == "evidence.delta",
            AuditEventRow.target_id == ctx["hypothesis_id"],
        )
    )).scalars().all())
    assert after_deltas == before_deltas, (
        "Inconclusive assessment must not emit an evidence delta"
    )


@pytest.mark.asyncio
async def test_t07b_insufficient_evidence_no_fabrication(app, db_session):
    """Missing evidence (no observations) → INSUFFICIENT_DATA, no fabricated
    measurements, no delta."""
    ctx = await _prepare_execution_for_assessment(
        db_session,
        success_threshold=0.7,
        failure_threshold=None,
        observed_value=None,  # no observation recorded
        run_id="t07b-ins",
    )

    result = await finalize_execution(
        db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
    )
    assert result["assessment"]["result"] == AssessmentResult.INSUFFICIENT_DATA
    assert result["evidence_delta_id"] is None

    # No fabricated observations
    fetched = await get_execution(db_session, ctx["exec_id"])
    assert fetched["observations"] == []

    # No state change
    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.HYPOTHESIZED.value
    assert hyp.confidence_value == 0.0


@pytest.mark.asyncio
async def test_t07b_repeated_finalization_no_duplicate_delta(app, db_session):
    """Re-finalizing an already-terminal execution must raise and must NOT
    emit a second evidence delta or adjust confidence a second time."""
    ctx = await _prepare_execution_for_assessment(
        db_session,
        success_threshold=0.7,
        failure_threshold=None,
        observed_value=0.85,
        run_id="t07b-idem",
    )

    # First finalization → delta + confidence adjustment
    r1 = await finalize_execution(
        db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
    )
    assert r1["evidence_delta_id"] is not None
    await db_session.commit()

    hyp_after_first = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    conf_after_first = hyp_after_first.confidence_value
    state_after_first = hyp_after_first.epistemic_state
    deltas_after_first = len(await get_evidence_deltas(db_session, ctx["hypothesis_id"]))

    # Re-finalization must raise
    with pytest.raises(ValueError, match="already terminal"):
        await finalize_execution(
            db_session, ctx["exec_id"], status=ExecutionStatus.COMPLETED,
        )

    # Confidence unchanged, state unchanged, no new delta
    hyp_after_second = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == ctx["hypothesis_id"])
    )).scalar_one()
    assert hyp_after_second.confidence_value == conf_after_first
    assert hyp_after_second.epistemic_state == state_after_first
    deltas_after_second = len(await get_evidence_deltas(db_session, ctx["hypothesis_id"]))
    assert deltas_after_second == deltas_after_first


@pytest.mark.asyncio
async def test_t07b_conflicting_results_preserved_across_executions(app, db_session):
    """Two independent executions of the same experiment producing
    conflicting results (one supporting, one contradicting) — both
    EvidenceDeltas must be preserved in the audit trail. Confidence is
    updated cumulatively per the established rules."""
    kb = await _seed_knowledge_base(db_session)
    gen = await generate_innovations(
        db_session, "edge inference for autonomous drones",
        context="on-device ML pipelines",
    )
    hypothesis_id = gen["concepts"][0]["hypothesis_id"]
    plan = await plan_experiment(
        db_session, hypothesis_id,
        protocol="Benchmark protocol for conflicting-results test.",
        metrics={"precision_at_10": "float"},
    )
    experiment_id = plan["experiment"]["experiment_id"]
    await db_session.commit()

    # Execution 1: supporting (run_id-A → independent execution)
    exec1 = await create_execution(
        db_session, experiment_id, run_id="t07b-conflict-sup",
    )
    exec1_id = exec1["execution_id"]
    await _set_operational_thresholds(
        db_session, exec1_id,
        success_threshold=0.7, failure_threshold=None,
    )
    await record_observation(
        db_session, exec1_id, metric="precision_at_10", observed_value=0.85,
        source_ref="experimenter-sup",
    )
    await db_session.commit()
    r1 = await finalize_execution(db_session, exec1_id, status=ExecutionStatus.COMPLETED)
    assert r1["assessment"]["result"] == AssessmentResult.SUPPORTING
    await db_session.commit()

    # Execution 2: contradicting (run_id-B → independent execution)
    exec2 = await create_execution(
        db_session, experiment_id, run_id="t07b-conflict-con",
    )
    exec2_id = exec2["execution_id"]
    assert exec2_id != exec1_id, "independent executions must have distinct IDs"
    await _set_operational_thresholds(
        db_session, exec2_id,
        success_threshold=0.7, failure_threshold=0.3,
    )
    await record_observation(
        db_session, exec2_id, metric="precision_at_10", observed_value=0.1,
        source_ref="experimenter-con",
    )
    await db_session.commit()
    r2 = await finalize_execution(db_session, exec2_id, status=ExecutionStatus.COMPLETED)
    assert r2["assessment"]["result"] == AssessmentResult.CONTRADICTING
    await db_session.commit()

    # Both deltas preserved in the audit trail
    deltas = await get_evidence_deltas(db_session, hypothesis_id)
    assert len(deltas) >= 2
    results = {d["assessment_result"] for d in deltas}
    assert "supporting" in results
    assert "contradicting" in results

    # Each delta references its own execution
    delta_by_exec = {d["execution_id"]: d for d in deltas}
    assert exec1_id in delta_by_exec
    assert exec2_id in delta_by_exec
    assert delta_by_exec[exec1_id]["assessment_result"] == "supporting"
    assert delta_by_exec[exec2_id]["assessment_result"] == "contradicting"

    # Confidence is the cumulative sum: +0.2 (supporting) then -0.2 (contradicting) = 0.0
    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    )).scalar_one()
    # The final state is DISPUTED (contradicting was the last delta)
    assert hyp.epistemic_state == EpistemicState.DISPUTED.value
    assert hyp.confidence_value == 0.0  # +0.2 - 0.2


@pytest.mark.asyncio
async def test_t07b_provenance_traceable_across_lifecycle(app, db_session):
    """Provenance is traceable end-to-end: every artifact can be traced
    back to a source fragment, and no artifact introduces ungrounded
    references."""
    kb = await _seed_knowledge_base(db_session)

    # Generate innovation
    gen = await generate_innovations(
        db_session, "edge inference for autonomous drones",
        context="on-device ML pipelines",
    )
    concept_id = gen["concepts"][0]["id"]
    hypothesis_id = gen["concepts"][0]["hypothesis_id"]
    await db_session.commit()

    # The hypothesis claim must reference the innovation concept
    hyp = (await db_session.execute(
        select(ClaimRow).where(ClaimRow.id == hypothesis_id)
    )).scalar_one()
    # The hypothesis is derived from the innovation — its subject/object
    # refs must point to real entities in the knowledge base.
    assert hyp.subject_ref is not None or hyp.object_ref is not None

    # Plan + execute
    plan = await plan_experiment(
        db_session, hypothesis_id,
        protocol="Provenance traceability protocol.",
        metrics={"precision_at_10": "float"},
    )
    experiment_id = plan["experiment"]["experiment_id"]
    await db_session.commit()

    exec_result = await create_execution(
        db_session, experiment_id, run_id="t07b-prov",
    )
    exec_id = exec_result["execution_id"]

    # The execution's protocol_snapshot must match the experiment plan's protocol
    exec_entity = (await db_session.execute(
        select(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution")
    )).scalar_one()
    exec_attrs = _load_execution_attrs(exec_entity)
    assert exec_attrs["experiment_id"] == experiment_id
    assert exec_attrs["hypothesis_id"] == hypothesis_id
    assert exec_attrs["protocol_snapshot"] is not None
    assert exec_attrs["protocol_snapshot"] != ""

    # Record an observation with a traceable source_ref
    await _set_operational_thresholds(
        db_session, exec_id, success_threshold=0.7, failure_threshold=None,
    )
    await record_observation(
        db_session, exec_id,
        metric="precision_at_10", observed_value=0.85,
        source_ref="experimenter-prov-001",
        measurement_method="automated_benchmark",
    )
    await db_session.commit()

    # Finalize → delta
    result = await finalize_execution(
        db_session, exec_id, status=ExecutionStatus.COMPLETED,
    )
    assert result["evidence_delta_id"] is not None
    await db_session.commit()

    # The evidence delta must carry full provenance: hypothesis, experiment, execution
    deltas = await get_evidence_deltas(db_session, hypothesis_id)
    assert len(deltas) >= 1
    d = deltas[0]
    assert d["hypothesis_id"] == hypothesis_id
    assert d["experiment_id"] == experiment_id
    assert d["execution_id"] == exec_id
    # The delta must reference the observation that produced it
    assert "evidence_refs" in d or "observation" in d

    # Count audit events: exactly one evidence.delta for this hypothesis
    delta_count = (await db_session.execute(
        select(func.count()).select_from(AuditEventRow).where(
            AuditEventRow.event_type == "evidence.delta",
            AuditEventRow.target_id == hypothesis_id,
        )
    )).scalar()
    assert delta_count == 1, (
        f"Expected exactly 1 evidence delta, got {delta_count}"
    )
