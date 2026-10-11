"""G06-T04 -- Scenario Revision + End-to-End Acceptance integration tests.

Covers the 12 mandatory acceptance areas in G06 plan section 13.4 plus
additional cases required by the G06-T04 mission (evidence retraction,
irrelevant new evidence, observation without verification, finalized
experiment with no scenario association, explicit assumption association,
contradictory evidence, F0-F4 horizon semantics, deterministic revision
identity, concurrent reassessment on PostgreSQL, append-only history,
version monotonicity, duplicate revision prevention, authentication /
404 / validation behavior, newest-first ordering).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from synapse.application.execution_record import (
    AssessmentResult,
    ExecutionStatus,
    create_execution,
    finalize_execution,
    get_evidence_deltas,
    record_observation,
)
from synapse.application.experiment_planner import plan_experiment
from synapse.application.innovation import generate_innovations
from synapse.application.prototype_planner import plan_prototype
from synapse.application.relationship_service import create_relationship
from synapse.application.scenario_builder import build_scenario, get_scenario
from synapse.application.scenario_evaluator import evaluate_scenario
from synapse.application.scenario_revision import (
    MATERIAL_FIELDS,
    VALID_TRIGGERS,
    get_scenario_revisions,
    persist_evaluation_snapshot,
    reassess_scenario,
)
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


# ── Fixture helpers (mirrors test_g06_t03 conventions) ───────────────────


async def _make_source(session, sid: str, uri: str, **kwargs) -> SourceRow:
    src = SourceRow(
        id=sid, canonical_uri=uri, source_type="paper", status="extracted", **kwargs
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
        id=eid,
        kind=kind,
        canonical_name=name,
        aliases="[]",
        attributes="{}",
        description=desc,
        version=1,
    )
    session.add(e)
    await session.flush()
    return e


async def _make_claim(
    session,
    cid: str,
    proposition: str,
    *,
    subject_ref: str | None = None,
    object_ref: str | None = None,
    evidence_refs: list[str] | None = None,
    contradicting_refs: list[str] | None = None,
    epistemic_state: str = "supported",
    validity_conditions: list[str] | None = None,
) -> ClaimRow:
    c = ClaimRow(
        id=cid,
        proposition=proposition,
        subject_ref=subject_ref,
        object_ref=object_ref,
        evidence_refs=json.dumps(evidence_refs or []),
        contradicting_refs=json.dumps(contradicting_refs or []),
        epistemic_state=epistemic_state,
        validity_conditions=json.dumps(validity_conditions or []),
        extraction_method="test-fixture",
        version=1,
    )
    session.add(c)
    await session.flush()
    return c


async def _seed_evaluated_kb(session) -> dict[str, Any]:
    """Seed a KB with verified evidence (F0 scenario).

    Two capabilities + PROVIDES + INTEGRATES_WITH + SUPPORTED claims +
    assess_claim called so the fragments meet the verification criterion.
    """
    src = await _make_source(session, "src-t04-a", "https://example.com/t04-a")
    frag = await _make_fragment(
        session, "frag-t04-a", src, "tool-A integrates with tool-B."
    )
    await _make_entity(session, "ent-t04-toolA", "technology", "tool-A-t04")
    await _make_entity(session, "ent-t04-toolB", "technology", "tool-B-t04")
    await _make_entity(session, "cap-t04-A", "capability", "capability-A-t04")
    await _make_entity(session, "cap-t04-B", "capability", "capability-B-t04")
    await _make_claim(
        session,
        "claim-t04-A",
        "tool-A-t04 provides capability-A-t04.",
        subject_ref="ent-t04-toolA",
        object_ref="cap-t04-A",
        evidence_refs=["frag-t04-a"],
    )
    await _make_claim(
        session,
        "claim-t04-B",
        "tool-B-t04 provides capability-B-t04.",
        subject_ref="ent-t04-toolB",
        object_ref="cap-t04-B",
        evidence_refs=["frag-t04-a"],
    )
    r1 = await create_relationship(
        session,
        from_entity_id="ent-t04-toolA",
        to_entity_id="cap-t04-A",
        predicate="PROVIDES",
        evidence_refs=["frag-t04-a"],
        origin="explicit",
    )
    assert r1["ok"]
    r2 = await create_relationship(
        session,
        from_entity_id="ent-t04-toolB",
        to_entity_id="cap-t04-B",
        predicate="PROVIDES",
        evidence_refs=["frag-t04-a"],
        origin="explicit",
    )
    assert r2["ok"]
    r3 = await create_relationship(
        session,
        from_entity_id="cap-t04-A",
        to_entity_id="cap-t04-B",
        predicate="INTEGRATES_WITH",
        evidence_refs=["frag-t04-a"],
        origin="explicit",
    )
    assert r3["ok"]
    await session.commit()
    await assess_claim(session, "claim-t04-A", requester="t04-fixture")
    await assess_claim(session, "claim-t04-B", requester="t04-fixture")
    await session.commit()
    return {
        "capability_ids": ["cap-t04-A", "cap-t04-B"],
        "fragment_id": "frag-t04-a",
        "claim_ids": ["claim-t04-A", "claim-t04-B"],
    }


async def _seed_two_fragment_kb(session) -> dict[str, Any]:
    """Seed a KB with TWO verified fragments and TWO claims.

    Used for tests that need to selectively retract one fragment's
    verification without affecting the other.
    """
    src_a = await _make_source(session, "src-t04-2a", "https://example.com/t04-2a")
    src_b = await _make_source(session, "src-t04-2b", "https://example.com/t04-2b")
    frag_a = await _make_fragment(
        session, "frag-t04-2a", src_a, "tool-A provides capability A."
    )
    frag_b = await _make_fragment(
        session, "frag-t04-2b", src_b, "tool-B provides capability B."
    )
    await _make_entity(session, "ent-t04-2toolA", "technology", "tool-A-2")
    await _make_entity(session, "ent-t04-2toolB", "technology", "tool-B-2")
    await _make_entity(session, "cap-t04-2A", "capability", "capability-A-2")
    await _make_entity(session, "cap-t04-2B", "capability", "capability-B-2")
    await _make_claim(
        session,
        "claim-t04-2A",
        "tool-A-2 provides capability-A-2.",
        subject_ref="ent-t04-2toolA",
        object_ref="cap-t04-2A",
        evidence_refs=["frag-t04-2a"],
    )
    await _make_claim(
        session,
        "claim-t04-2B",
        "tool-B-2 provides capability-B-2.",
        subject_ref="ent-t04-2toolB",
        object_ref="cap-t04-2B",
        evidence_refs=["frag-t04-2b"],
    )
    for frm, to, ev in [
        ("ent-t04-2toolA", "cap-t04-2A", ["frag-t04-2a"]),
        ("ent-t04-2toolB", "cap-t04-2B", ["frag-t04-2b"]),
    ]:
        r = await create_relationship(
            session,
            from_entity_id=frm,
            to_entity_id=to,
            predicate="PROVIDES",
            evidence_refs=ev,
            origin="explicit",
        )
        assert r["ok"]
    r3 = await create_relationship(
        session,
        from_entity_id="cap-t04-2A",
        to_entity_id="cap-t04-2B",
        predicate="INTEGRATES_WITH",
        evidence_refs=["frag-t04-2a"],
        origin="explicit",
    )
    assert r3["ok"]
    await session.commit()
    await assess_claim(session, "claim-t04-2A", requester="t04-fixture")
    await assess_claim(session, "claim-t04-2B", requester="t04-fixture")
    await session.commit()
    return {
        "capability_ids": ["cap-t04-2A", "cap-t04-2B"],
        "fragment_ids": ["frag-t04-2a", "frag-t04-2b"],
        "claim_ids": ["claim-t04-2A", "claim-t04-2B"],
    }


async def _set_operational_thresholds(session, exec_id: str, metric: str = "precision_at_10"):
    """Mark the execution's success + failure criteria as operational."""
    entity = (
        await session.execute(
            select(EntityRow).where(
                EntityRow.id == exec_id,
                EntityRow.kind == "execution",
            )
        )
    ).scalar_one()
    attrs = json.loads(entity.attributes)
    for sc in attrs["success_criteria"]:
        if sc["metric"] == metric:
            sc["threshold"] = 0.7
            sc["calibration_status"] = "operational"
    for fc in attrs["failure_criteria"]:
        if fc["metric"] == metric:
            fc["threshold"] = 0.3
            fc["calibration_status"] = "operational"
            fc["comparator"] = "<"
    entity.attributes = json.dumps(attrs, default=str)
    await session.flush()


async def _seed_t04_e2e_fixture(session) -> dict[str, Any]:
    """Seed a complete G05+G06 fixture for the end-to-end lifecycle test.

    Structure:
    - 3 sources, 3 fragments (frag-A/B/C), 3 capabilities, 3 claims.
    - claim-A and claim-B are ASSESSED → frag-A, frag-B verified.
    - claim-C is NOT assessed → frag-C not verified.
    - generate_innovations → concept + hypothesis. The hypothesis cites
      [frag-A, frag-B, frag-C] (collected from the participating PROVIDES
      claims' evidence_refs).
    - Build scenario linked to concept with evidence_refs=[frag-A, frag-B, frag-C].
    - evaluate → evidence_coverage = 2/3 (frag-A, frag-B verified; frag-C not).
    - prototype-plan Mode A → experiment plan.
    - create_execution → execution record.
    - Set operational thresholds (success=0.7).
    - record_observation = 0.85 → supporting.
    - finalize_execution → hypothesis SUPPORTED. evidence.delta audit row.
    - assess_claim(hypothesis_id) → emits verification.assessed for hypothesis
      with outcome source_supported (or verified). Now frag-C is verified
      (because the hypothesis cites frag-C and has a positive assessment).
    - reassess → evidence_coverage = 3/3 = 1.0. Material change → revised=true.
    """
    src_a = await _make_source(session, "src-t04-e2e-a", "https://example.com/t04-e2e-a")
    src_b = await _make_source(session, "src-t04-e2e-b", "https://example.com/t04-e2e-b")
    src_c = await _make_source(session, "src-t04-e2e-c", "https://example.com/t04-e2e-c")
    frag_a = await _make_fragment(
        session, "frag-t04-e2e-a", src_a, "tool-A provides capability A."
    )
    frag_b = await _make_fragment(
        session, "frag-t04-e2e-b", src_b, "tool-B provides capability B."
    )
    frag_c = await _make_fragment(
        session, "frag-t04-e2e-c", src_c, "tool-C provides capability C."
    )
    await _make_entity(session, "ent-t04-e2e-A", "technology", "tool-A-e2e")
    await _make_entity(session, "ent-t04-e2e-B", "technology", "tool-B-e2e")
    await _make_entity(session, "ent-t04-e2e-C", "technology", "tool-C-e2e")
    await _make_entity(session, "cap-t04-e2e-A", "capability", "capability-A-e2e")
    await _make_entity(session, "cap-t04-e2e-B", "capability", "capability-B-e2e")
    await _make_entity(session, "cap-t04-e2e-C", "capability", "capability-C-e2e")
    await _make_claim(
        session,
        "claim-t04-e2e-A",
        "tool-A-e2e provides capability-A-e2e.",
        subject_ref="ent-t04-e2e-A",
        object_ref="cap-t04-e2e-A",
        evidence_refs=["frag-t04-e2e-a"],
    )
    await _make_claim(
        session,
        "claim-t04-e2e-B",
        "tool-B-e2e provides capability-B-e2e.",
        subject_ref="ent-t04-e2e-B",
        object_ref="cap-t04-e2e-B",
        evidence_refs=["frag-t04-e2e-b"],
    )
    await _make_claim(
        session,
        "claim-t04-e2e-C",
        "tool-C-e2e provides capability-C-e2e.",
        subject_ref="ent-t04-e2e-C",
        object_ref="cap-t04-e2e-C",
        evidence_refs=["frag-t04-e2e-c"],
    )
    for frm, to, ev in [
        ("ent-t04-e2e-A", "cap-t04-e2e-A", ["frag-t04-e2e-a"]),
        ("ent-t04-e2e-B", "cap-t04-e2e-B", ["frag-t04-e2e-b"]),
        ("ent-t04-e2e-C", "cap-t04-e2e-C", ["frag-t04-e2e-c"]),
    ]:
        r = await create_relationship(
            session,
            from_entity_id=frm,
            to_entity_id=to,
            predicate="PROVIDES",
            evidence_refs=ev,
            origin="explicit",
        )
        assert r["ok"]
    r_int = await create_relationship(
        session,
        from_entity_id="cap-t04-e2e-A",
        to_entity_id="cap-t04-e2e-B",
        predicate="INTEGRATES_WITH",
        evidence_refs=["frag-t04-e2e-a"],
        origin="explicit",
    )
    assert r_int["ok"]
    await session.commit()
    # Assess only A and B — C is left unverified.
    await assess_claim(session, "claim-t04-e2e-A", requester="t04-fixture")
    await assess_claim(session, "claim-t04-e2e-B", requester="t04-fixture")
    await session.commit()

    # Generate G05 innovation concept + hypothesis
    gen = await generate_innovations(
        session,
        "AI research assistant",
        context="AI agent systems",
    )
    await session.commit()

    # generate_innovations is non-deterministic in WHICH combination of
    # components it picks first. The hypothesis's evidence_refs is the
    # union of the participating components' PROVIDES claims' evidence_refs.
    # For the e2e test, we need a hypothesis that cites frag-C (the
    # unverified fragment) so that assess_claim(hypothesis) makes frag-C
    # verified. Iterate through generated concepts to find one.
    concept_id = None
    hypothesis_id = None
    for concept in gen["concepts"]:
        hid = concept["hypothesis_id"]
        hyp_row = (
            await session.execute(select(ClaimRow).where(ClaimRow.id == hid))
        ).scalar_one_or_none()
        if hyp_row is None:
            continue
        hyp_evidence_refs = json.loads(hyp_row.evidence_refs or "[]")
        if "frag-t04-e2e-c" in hyp_evidence_refs:
            concept_id = concept["id"]
            hypothesis_id = hid
            break
    if concept_id is None:
        # Fallback: use the first concept and manually add a claim citing
        # frag-C with subject_ref = the innovation concept. Then when we
        # assess this claim, frag-C becomes verified.
        concept_id = gen["concepts"][0]["id"]
        hypothesis_id = gen["concepts"][0]["hypothesis_id"]
        # Create a new claim citing frag-C with subject_ref = the innovation
        # concept entity. This claim participates in the next assess_call.
        await _make_claim(
            session,
            "claim-t04-e2e-hyp-extra",
            "the innovation concept is supported by frag-C.",
            subject_ref=concept_id,
            object_ref=None,
            evidence_refs=["frag-t04-e2e-c"],
        )
        # Re-evaluate the hypothesis with this additional evidence: update
        # the hypothesis's evidence_refs to include frag-C.
        hyp_row = (
            await session.execute(
                select(ClaimRow).where(ClaimRow.id == hypothesis_id)
            )
        ).scalar_one()
        existing_refs = json.loads(hyp_row.evidence_refs or "[]")
        if "frag-t04-e2e-c" not in existing_refs:
            existing_refs.append("frag-t04-e2e-c")
            hyp_row.evidence_refs = json.dumps(existing_refs)
            await session.flush()
        await session.commit()
        # Use the manually-added claim as the assessment target.
        # (We'll call assess_claim(hypothesis_id) which will see the
        # updated evidence_refs including frag-C.)

    return {
        "concept_id": concept_id,
        "hypothesis_id": hypothesis_id,
        "capability_ids": ["cap-t04-e2e-A", "cap-t04-e2e-B"],
        "fragment_ids": ["frag-t04-e2e-a", "frag-t04-e2e-b", "frag-t04-e2e-c"],
    }


# ═════════════════════════════════════════════════════════════════════════
# Mandatory tests (§13.4)
# ═════════════════════════════════════════════════════════════════════════


# ── Test 1: No-change reassessment → revised=false ────────────────────────


@pytest.mark.asyncio
async def test_01_no_change_reassessment_returns_revised_false(app, db_session):
    """Reassess with no KB change since the prior evaluation → revised=false."""
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()

    # Initial evaluation → establishes last_evaluation snapshot.
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    assert eval1 is not None
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Count audit rows before reassessment.
    before_count = len(
        (
            await db_session.execute(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "scenario.revision",
                    AuditEventRow.target_id == scenario["scenario_id"],
                )
            )
        ).scalars().all()
    )
    assert before_count == 0

    # Reassess with no KB change.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()

    assert result is not None
    assert result["revised"] is False
    assert result["revision_id"] is None
    assert result["changes"] == []

    # No new scenario.revision audit row.
    after_count = len(
        (
            await db_session.execute(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "scenario.revision",
                    AuditEventRow.target_id == scenario["scenario_id"],
                )
            )
        ).scalars().all()
    )
    assert after_count == 0


# ── Test 2: New evidence → revised=true ───────────────────────────────────


@pytest.mark.asyncio
async def test_02_new_evidence_triggers_revision(app, db_session):
    """Adding new verified evidence and reassessing → revised=true."""
    fixture = await _seed_two_fragment_kb(db_session)
    # Build scenario with both fragments, but DON'T assess claim-B yet
    # (claim-A is assessed in the seed). frag-B not verified initially.
    # First, retract claim-B's assessment by deleting the audit row.
    audit_rows = (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all()
    for row in audit_rows:
        await db_session.delete(row)
    await db_session.commit()

    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()

    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    assert eval1 is not None
    # frag-A verified, frag-B not → coverage = 0.5
    assert eval1["evidence_coverage"] == 0.5
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # NEW EVIDENCE: assess claim-B → frag-B now verified.
    await assess_claim(db_session, "claim-t04-2B", requester="t04-tester")
    await db_session.commit()

    # Reassess with new evidence.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-2B",
        requester="t04-tester",
    )
    await db_session.commit()

    assert result is not None
    assert result["revised"] is True
    assert result["prior_version"] == 1
    assert result["new_version"] == 2
    assert result["new_evaluation"]["evidence_coverage"] == 1.0
    assert result["prior_evaluation"]["evidence_coverage"] == 0.5
    # The change list should mention evidence_coverage.
    assert any("evidence_coverage" in c for c in result["changes"])


# ── Test 3: New experiment result trigger → revised=true ──────────────────


@pytest.mark.asyncio
async def test_03_new_experiment_result_trigger(app, db_session):
    """Reassess with trigger=new_experiment_result picks up the evidence delta."""
    fixture = await _seed_t04_e2e_fixture(db_session)

    # Build scenario linked to the innovation concept.
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        linked_innovation_concept_id=fixture["concept_id"],
        requester="t04-tester",
    )
    await db_session.commit()

    # Initial evaluation → establishes prior snapshot.
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    assert eval1 is not None
    # Coverage = 2/3 (frag-A, frag-B verified; frag-C not).
    assert eval1["evidence_coverage"] == pytest.approx(2 / 3, abs=1e-9)
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Prototype-plan Mode A → experiment plan.
    proto = await plan_prototype(
        db_session,
        scenario["scenario_id"],
        metrics={"precision_at_10": "float"},
        requester="t04-tester",
    )
    assert proto["mode"] == "linked_experiment"
    experiment_id = proto["experiment_id"]
    await db_session.commit()

    # Create execution + record observation + finalize.
    exec_result = await create_execution(
        db_session, experiment_id, requester="t04-tester"
    )
    exec_id = exec_result["execution_id"]
    await _set_operational_thresholds(db_session, exec_id)
    await record_observation(
        db_session,
        exec_id,
        metric="precision_at_10",
        observed_value=0.85,
        source_ref="experimenter-001",
    )
    finalize_result = await finalize_execution(
        db_session, exec_id, status=ExecutionStatus.COMPLETED, requester="t04-tester"
    )
    assert finalize_result["assessment"]["result"] == AssessmentResult.SUPPORTING
    assert finalize_result["evidence_delta_id"] is not None
    await db_session.commit()

    # Verify the evidence.delta audit row exists for the hypothesis.
    deltas = await get_evidence_deltas(db_session, fixture["hypothesis_id"])
    assert len(deltas) >= 1
    assert deltas[0]["updated_state"] == EpistemicState.SUPPORTED.value

    # Now assess the hypothesis claim → emits verification.assessed audit row
    # for the hypothesis, which cites frag-C. Now frag-C is verified.
    await assess_claim(db_session, fixture["hypothesis_id"], requester="t04-tester")
    await db_session.commit()

    # Reassess with trigger=new_experiment_result, trigger_ref=execution_id.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_experiment_result",
        trigger_ref=exec_id,
        requester="t04-tester",
    )
    await db_session.commit()

    assert result is not None
    assert result["revised"] is True
    assert result["trigger"] == "new_experiment_result"
    assert result["trigger_ref"] == exec_id
    # The new evaluation should reflect the experiment result:
    # evidence_coverage went from 2/3 to 3/3 = 1.0.
    assert result["new_evaluation"]["evidence_coverage"] == 1.0
    assert result["prior_evaluation"]["evidence_coverage"] == pytest.approx(
        2 / 3, abs=1e-9
    )


# ── Test 4: Prior version preserved in revision history ───────────────────


@pytest.mark.asyncio
async def test_04_prior_version_preserved(app, db_session):
    """After a revision, the prior evaluation snapshot is preserved in the audit row."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()

    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Trigger a revision by retracting claim-B's verification.
    audit_rows = (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all()
    for row in audit_rows:
        await db_session.delete(row)
    await db_session.commit()

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-2B-retraction",
        requester="t04-tester",
    )
    await db_session.commit()

    assert result["revised"] is True

    # Fetch the revision history.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev["prior_version"] == 1
    assert rev["new_version"] == 2
    # The prior evaluation snapshot is preserved in the audit payload.
    assert rev["prior_evaluation"] is not None
    assert rev["prior_evaluation"]["evidence_coverage"] == 1.0
    # The new evaluation snapshot reflects the retraction.
    assert rev["new_evaluation"]["evidence_coverage"] == 0.5


# ── Test 5: Revision history ordering (newest first) ──────────────────────


@pytest.mark.asyncio
async def test_05_revision_history_ordering_newest_first(app, db_session):
    """Multiple revisions → history is ordered newest-first."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()

    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Revision 1: retract claim-B's verification → coverage drops.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()
    await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()

    # Revision 2: re-assess claim-B → coverage goes back to 1.0.
    await assess_claim(db_session, "claim-t04-2B", requester="t04-tester")
    await db_session.commit()
    await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()

    # Revision 3: retract claim-A's verification → coverage drops to 0.0.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2A",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()
    await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()

    # Fetch history.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 3
    # Newest first → versions 4, 3, 2 (newest first means highest version first).
    versions = [r["new_version"] for r in revisions]
    assert versions == [4, 3, 2]
    # created_at descending.
    created_ats = [r["created_at"] for r in revisions if r["created_at"]]
    assert created_ats == sorted(created_ats, reverse=True)


# ── Test 6: End-to-end lifecycle ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_06_end_to_end_lifecycle(app, db_session):
    """Build → evaluate → prototype → experiment → observation → finalize
    → assess_claim(hypothesis) → reassess → new evaluation reflects the
    experiment result. Verify prior version preserved. Verify repeated
    reassessment with no further change creates no duplicate revision."""
    fixture = await _seed_t04_e2e_fixture(db_session)

    # 1. Build scenario linked to innovation.
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        linked_innovation_concept_id=fixture["concept_id"],
        requester="t04-tester",
    )
    await db_session.commit()

    # 2. Evaluate → establishes prior snapshot (coverage=2/3).
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()
    assert eval1["evidence_coverage"] == pytest.approx(2 / 3, abs=1e-9)

    # 3. Prototype-plan Mode A → experiment plan.
    proto = await plan_prototype(
        db_session,
        scenario["scenario_id"],
        metrics={"precision_at_10": "float"},
        requester="t04-tester",
    )
    assert proto["mode"] == "linked_experiment"
    experiment_id = proto["experiment_id"]
    await db_session.commit()

    # 4-6. Execute → record observation → finalize.
    exec_result = await create_execution(
        db_session, experiment_id, requester="t04-tester"
    )
    exec_id = exec_result["execution_id"]
    await _set_operational_thresholds(db_session, exec_id)
    await record_observation(
        db_session,
        exec_id,
        metric="precision_at_10",
        observed_value=0.85,
        source_ref="experimenter-001",
    )
    finalize_result = await finalize_execution(
        db_session, exec_id, status=ExecutionStatus.COMPLETED, requester="t04-tester"
    )
    assert finalize_result["assessment"]["result"] == AssessmentResult.SUPPORTING
    await db_session.commit()

    # 7. assess_claim(hypothesis) → emits verification.assessed for hypothesis
    # → frag-C now verified (hypothesis cites frag-C).
    await assess_claim(db_session, fixture["hypothesis_id"], requester="t04-tester")
    await db_session.commit()

    # 8-9. Reassess → revised=true, new evaluation reflects experiment result.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_experiment_result",
        trigger_ref=exec_id,
        requester="t04-tester",
    )
    await db_session.commit()

    assert result["revised"] is True
    assert result["new_evaluation"]["evidence_coverage"] == 1.0
    assert result["prior_evaluation"]["evidence_coverage"] == pytest.approx(
        2 / 3, abs=1e-9
    )

    # 10. Verify prior version preserved in revision history.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 1
    assert revisions[0]["prior_version"] == 1
    assert revisions[0]["new_version"] == 2
    assert revisions[0]["prior_evaluation"]["evidence_coverage"] == pytest.approx(
        2 / 3, abs=1e-9
    )
    assert revisions[0]["new_evaluation"]["evidence_coverage"] == 1.0

    # 11. Repeat reassessment with NO further KB change → revised=false.
    result2 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result2["revised"] is False
    assert result2["revision_id"] is None

    # No new scenario.revision audit row.
    revisions_after = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions_after) == 1  # still just the one from step 8


# ── Test 7: Provenance (trigger + trigger_ref) ───────────────────────────


@pytest.mark.asyncio
async def test_07_provenance_trigger_and_trigger_ref(app, db_session):
    """Every revision carries trigger and trigger_ref in the audit payload."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Retract claim-B → trigger a revision.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-2B",
        requester="t04-tester",
    )
    await db_session.commit()

    assert result["revised"] is True
    assert result["trigger"] == "new_evidence"
    assert result["trigger_ref"] == "claim-t04-2B"

    # Verify in audit row.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 1
    assert revisions[0]["trigger"] == "new_evidence"
    assert revisions[0]["trigger_ref"] == "claim-t04-2B"
    assert revisions[0]["reassessed_by"] == "t04-tester"


# ── Test 8: No background scheduler (static inspection) ───────────────────


@pytest.mark.asyncio
async def test_08_no_background_scheduler(app, db_session):
    """scenario_revision.py contains NO scheduler/polling/queue imports."""
    import inspect

    from synapse.application import scenario_revision as mod

    src = inspect.getsource(mod)
    forbidden = ["asyncio.create_task", "BackgroundTasks", "while True", "sleep("]
    for token in forbidden:
        assert token not in src, f"scenario_revision.py must not contain {token!r}"

    # Sanity: VALID_TRIGGERS is exactly the three documented triggers.
    assert frozenset(
        {"explicit_reassess", "new_evidence", "new_experiment_result"}
    ) == VALID_TRIGGERS


# ── Test 9: API authentication + 404 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_09_api_authentication_and_404(app, client, db_session, auth_headers_admin):
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    await evaluate_scenario(db_session, scenario["scenario_id"])
    await db_session.commit()

    # POST /reassess: no auth → 401.
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 401

    # POST /reassess: with auth, unknown scenario → 404.
    r = client.post(
        "/api/v1/future/scenarios/scn-does-not-exist/reassess",
        headers=auth_headers_admin,
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 404

    # POST /reassess: with auth, invalid trigger → 422.
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        headers=auth_headers_admin,
        json={"trigger": "invalid_trigger"},
    )
    assert r.status_code == 422

    # POST /reassess: with auth, valid → 200.
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        headers=auth_headers_admin,
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revised"] in (True, False)

    # GET /revisions: no auth → 401.
    r = client.get(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/revisions"
    )
    assert r.status_code == 401

    # GET /revisions: with auth, unknown scenario → 404.
    r = client.get(
        "/api/v1/future/scenarios/scn-does-not-exist/revisions",
        headers=auth_headers_admin,
    )
    assert r.status_code == 404

    # GET /revisions: with auth, valid → 200.
    r = client.get(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/revisions",
        headers=auth_headers_admin,
    )
    assert r.status_code == 200, r.text


# ── Test 10: OpenAPI includes new routes ──────────────────────────────────


@pytest.mark.asyncio
async def test_10_openapi_includes_new_routes(app):
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert (
        "/api/v1/future/scenarios/{scenario_id}/reassess" in paths
    ), "OpenAPI must include POST /scenarios/{id}/reassess"
    assert "post" in paths["/api/v1/future/scenarios/{scenario_id}/reassess"]
    assert (
        "/api/v1/future/scenarios/{scenario_id}/revisions" in paths
    ), "OpenAPI must include GET /scenarios/{id}/revisions"
    assert "get" in paths["/api/v1/future/scenarios/{scenario_id}/revisions"]


# ── Test 11: Invalid trigger returns 422 (API-level) ──────────────────────


@pytest.mark.asyncio
async def test_11_invalid_trigger_returns_422(app, client, db_session, auth_headers_admin):
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()

    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        headers=auth_headers_admin,
        json={"trigger": "completely_invalid"},
    )
    assert r.status_code == 422


# ── Test 12 (PG-only): Concurrent reassessment on PostgreSQL ──────────────


@pytest.mark.asyncio
async def test_12_concurrent_reassessment_pg_only(pg_session):
    """Two concurrent reassessments of the same scenario cannot both publish
    the same next version. Exactly one wins; the other returns revised=false
    (after seeing the winner's commit on READ COMMITTED)."""
    fixture = await _seed_two_fragment_kb(pg_session)
    scenario = await build_scenario(
        pg_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await pg_session.commit()
    eval1 = await evaluate_scenario(pg_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(pg_session, scenario["scenario_id"], eval1)
    await pg_session.commit()

    # Retract claim-B's verification → both concurrent reassessments should
    # see the same KB state and try to publish version 2. Only one wins.
    for row in (
        await pg_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await pg_session.delete(row)
    await pg_session.commit()

    # Spawn two sessions bound to the SAME isolated PG engine.
    factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)

    async def _reassess_in_separate_session():
        async with factory() as s:
            result = await reassess_scenario(
                s,
                scenario["scenario_id"],
                trigger="new_evidence",
                trigger_ref="claim-t04-2B-retraction",
                requester="concurrent-worker",
            )
            await s.commit()
            return result

    # Issue both concurrently.
    results = await asyncio.gather(
        _reassess_in_separate_session(),
        _reassess_in_separate_session(),
        return_exceptions=True,
    )

    # Inspect outcomes.
    revised_flags = []
    for r in results:
        assert not isinstance(r, Exception), f"reassess raised: {r!r}"
        revised_flags.append(r["revised"])

    # Exactly one should have revised=true.
    assert revised_flags.count(True) == 1, (
        f"Exactly one concurrent reassessment should revise; got {revised_flags}"
    )
    assert revised_flags.count(False) == 1

    # Verify only one scenario.revision audit row exists for this scenario.
    revisions = await get_scenario_revisions(pg_session, scenario["scenario_id"])
    assert len(revisions) == 1, (
        f"Concurrent reassessment must not produce duplicate revisions; "
        f"got {len(revisions)}"
    )
    assert revisions[0]["new_version"] == 2


# ═════════════════════════════════════════════════════════════════════════
# Additional mandatory acceptance areas (§13.4 + user task brief §8)
# ═════════════════════════════════════════════════════════════════════════


# ── Test Corr-A: No-change reassessment → no duplicate revision ────────────


@pytest.mark.asyncio
async def test_corr_a_no_change_reassessment_no_duplicate(app, db_session):
    """After a revision, reassess with no further change → no new revision."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # First revision: retract claim-B → coverage 1.0 → 0.5.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()
    r1 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r1["revised"] is True

    # Second reassessment, no change → revised=false.
    r2 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r2["revised"] is False
    assert r2["revision_id"] is None

    # Still only one revision in history.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 1


# ── Test Corr-B: Evidence retraction ────────────────────────────────────────


@pytest.mark.asyncio
async def test_corr_b_evidence_retraction(app, db_session):
    """Retracting a previously-verified fragment → coverage drops, revised=true."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    assert eval1["evidence_coverage"] == 1.0
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Retract claim-A's verification → frag-A no longer verified.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2A",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-2A-retraction",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is True
    assert result["new_evaluation"]["evidence_coverage"] == 0.5
    assert result["prior_evaluation"]["evidence_coverage"] == 1.0


# ── Test Corr-C: Irrelevant new evidence ──────────────────────────────────


@pytest.mark.asyncio
async def test_corr_c_irrelevant_new_evidence(app, db_session):
    """New evidence NOT cited by the scenario's current_evidence_refs does not
    affect the scenario's evaluation → revised=false."""
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Add an irrelevant fragment + claim that is NOT in the scenario's evidence_refs.
    src_z = await _make_source(session=db_session, sid="src-t04-irrel", uri="https://example.com/t04-irrel")
    frag_z = await _make_fragment(db_session, "frag-t04-irrel", src_z, "irrelevant evidence.")
    await _make_claim(
        db_session,
        "claim-t04-irrel",
        "irrelevant claim.",
        subject_ref="ent-t04-toolA",
        evidence_refs=["frag-t04-irrel"],
    )
    await assess_claim(db_session, "claim-t04-irrel", requester="t04-tester")
    await db_session.commit()

    # Reassess — the new evidence is irrelevant to the scenario.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="frag-t04-irrel",
        requester="t04-tester",
    )
    await db_session.commit()

    assert result["revised"] is False
    # Coverage unchanged.
    assert result["new_evaluation"]["evidence_coverage"] == eval1["evidence_coverage"]


# ── Test Corr-D: Observation without independent verification ─────────────


@pytest.mark.asyncio
async def test_corr_d_observation_without_verification(app, db_session):
    """An experiment observation alone (without assess_claim on the hypothesis)
    does not change evidence_coverage — observation is not automatically a
    VERIFIED claim."""
    fixture = await _seed_t04_e2e_fixture(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        linked_innovation_concept_id=fixture["concept_id"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Prototype-plan Mode A → experiment plan.
    proto = await plan_prototype(
        db_session,
        scenario["scenario_id"],
        metrics={"precision_at_10": "float"},
        requester="t04-tester",
    )
    experiment_id = proto["experiment_id"]
    await db_session.commit()

    # Execute + record observation + finalize (BUT do NOT call assess_claim
    # on the hypothesis).
    exec_result = await create_execution(
        db_session, experiment_id, requester="t04-tester"
    )
    exec_id = exec_result["execution_id"]
    await _set_operational_thresholds(db_session, exec_id)
    await record_observation(
        db_session,
        exec_id,
        metric="precision_at_10",
        observed_value=0.85,
    )
    finalize_result = await finalize_execution(
        db_session, exec_id, status=ExecutionStatus.COMPLETED
    )
    assert finalize_result["assessment"]["result"] == AssessmentResult.SUPPORTING
    await db_session.commit()

    # Reassess WITHOUT assess_claim(hypothesis) → frag-C still not verified
    # → coverage unchanged → revised=false.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_experiment_result",
        trigger_ref=exec_id,
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is False, (
        "An observation alone is NOT automatically verified evidence. "
        "assess_claim must be called on the hypothesis to update the "
        "verification.assessed audit trail."
    )


# ── Test Corr-E: Finalized experiment with no scenario association ─────────


@pytest.mark.asyncio
async def test_corr_e_finalized_experiment_no_scenario_association(app, db_session):
    """A finalized experiment for a hypothesis NOT linked to the scenario
    does not affect the scenario's evidence_coverage."""
    # Build scenario A (no linked innovation).
    fixture_a = await _seed_evaluated_kb(db_session)
    scenario_a = await build_scenario(
        db_session,
        capability_ids=fixture_a["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture_a["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    eval_a = await evaluate_scenario(db_session, scenario_a["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario_a["scenario_id"], eval_a)
    await db_session.commit()

    # Separately, set up an innovation + experiment + finalize for a
    # DIFFERENT hypothesis not linked to scenario_a.
    fixture_b = await _seed_t04_e2e_fixture(db_session)
    proto = await plan_prototype_for_hypothesis(
        db_session,
        hypothesis_id=fixture_b["hypothesis_id"],
        metrics={"precision_at_10": "float"},
    )
    experiment_id = proto["experiment_id"]
    await db_session.commit()
    exec_result = await create_execution(
        db_session, experiment_id, requester="t04-tester"
    )
    exec_id = exec_result["execution_id"]
    await _set_operational_thresholds(db_session, exec_id)
    await record_observation(
        db_session, exec_id, metric="precision_at_10", observed_value=0.85
    )
    finalize_result = await finalize_execution(
        db_session, exec_id, status=ExecutionStatus.COMPLETED
    )
    assert finalize_result["assessment"]["result"] == AssessmentResult.SUPPORTING
    await assess_claim(db_session, fixture_b["hypothesis_id"], requester="t04-tester")
    await db_session.commit()

    # Reassess scenario_a — the experiment was for hypothesis_b, not linked
    # to scenario_a. So scenario_a's evaluation should be unchanged.
    result = await reassess_scenario(
        db_session,
        scenario_a["scenario_id"],
        trigger="new_experiment_result",
        trigger_ref=exec_id,
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is False, (
        "A finalized experiment for a hypothesis not linked to the scenario "
        "must NOT change the scenario's evaluation. The trigger_ref is "
        "informational but the material comparison reflects the actual KB "
        "state of the scenario's evidence_refs."
    )


async def plan_prototype_for_hypothesis(session, *, hypothesis_id, metrics):
    """Helper: build an innovation-independent experiment plan directly
    via plan_experiment. Used by Corr-E to create an experiment for a
    hypothesis NOT linked to any scenario."""
    result = await plan_experiment(
        session,
        hypothesis_id,
        protocol="Test protocol for Corr-E.",
        metrics=metrics,
    )
    # plan_experiment returns {"experiment": {"experiment_id": ...}, ...}
    return {
        "experiment_id": result["experiment"]["experiment_id"],
        "experiment": result["experiment"],
    }


# ── Test Corr-F: Explicit assumption association with supporting evidence ─


@pytest.mark.asyncio
async def test_corr_f_explicit_assumption_association(app, db_session):
    """G06-T03A Correction A: assumption_assessment uses explicit
    assumption-to-claim associations stored in
    ``scenario.risks["assumption_associations"]``.

    When the associated claim has a positive verification outcome, the
    assumption is "supported". When the associated claim is retracted,
    the assumption classification changes → material change → revised=true.
    """
    fixture = await _seed_two_fragment_kb(db_session)
    # Build scenario with explicit assumption-to-claim associations.
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["assumption-A", "assumption-B"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    # Manually add the assumption_associations in scenario.risks (the
    # scenario_builder doesn't set these; they're caller-controlled via
    # the scenario attributes JSON). The evaluator reads
    # risks["assumption_associations"] per Correction A.
    entity = (
        await db_session.execute(
            select(EntityRow).where(
                EntityRow.id == scenario["scenario_id"],
                EntityRow.kind == "scenario",
            )
        )
    ).scalar_one()
    attrs = json.loads(entity.attributes)
    attrs["risks"] = {
        "assumption_associations": {
            "assumption-A": "claim-t04-2A",
            "assumption-B": "claim-t04-2B",
        }
    }
    entity.attributes = json.dumps(attrs, default=str)
    await db_session.flush()
    await db_session.commit()

    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()
    # assumption_assessment is a LIST of per-assessment dicts.
    aa = eval1["assumption_assessment"]
    assert isinstance(aa, list)
    assert len(aa) == 2
    # Both assumptions should be "supported" because both claims are verified.
    classifications = {entry["assumption"]: entry["classification"] for entry in aa}
    assert classifications == {"assumption-A": "supported", "assumption-B": "supported"}

    # Retract claim-B → assumption-B should change classification to "unknown".
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-2B-retraction",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is True
    # assumption_assessment is a material field; the change list should
    # mention it.
    assert any("assumption_assessment" in c for c in result["changes"])
    # The new evaluation's assumption-B classification is "unknown".
    new_aa = result["new_evaluation"]["assumption_assessment"]
    assert isinstance(new_aa, list)
    new_classifications = {entry["assumption"]: entry["classification"] for entry in new_aa}
    assert new_classifications["assumption-B"] == "unknown"
    assert new_classifications["assumption-A"] == "supported"


# ── Test Corr-G: Contradictory evidence changes consistency ───────────────


@pytest.mark.asyncio
async def test_corr_g_contradictory_evidence_changes_consistency(app, db_session):
    """Adding a claim whose ``contradicting_refs`` lists one of the
    scenario's evidence fragments → ``contradiction_exposure`` increases
    → revised=true."""
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    assert eval1["contradiction_exposure"] == 0
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Add a new claim that LISTS the scenario's fragment as contradicting
    # evidence. The evaluator's second contradiction path finds claims
    # whose ``contradicting_refs`` field contains one of the scenario's
    # evidence_refs.
    src_x = await _make_source(session=db_session, sid="src-t04-contradict", uri="https://example.com/t04-contradict")
    frag_x = await _make_fragment(db_session, "frag-t04-contradict", src_x, "contradicting evidence.")
    await _make_claim(
        db_session,
        "claim-t04-contradict",
        "tool-A-t04 does NOT provide capability-A-t04.",
        subject_ref="ent-t04-toolA",
        object_ref="cap-t04-A",
        evidence_refs=["frag-t04-contradict"],
        # The contradicting_refs lists the SCENARIO's fragment, marking it
        # as contradicting evidence for this claim.
        contradicting_refs=[fixture["fragment_id"]],
        epistemic_state="supported",
    )
    await db_session.commit()

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        trigger_ref="claim-t04-contradict",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is True
    # contradiction_exposure increased from 0 to ≥1.
    assert result["new_evaluation"]["contradiction_exposure"] >= 1
    assert result["prior_evaluation"]["contradiction_exposure"] == 0
    # The change list should mention contradiction_exposure.
    assert any("contradiction_exposure" in c for c in result["changes"])


# ── Test Corr-H: F0-F4 horizon semantics preserved ─────────────────────────


@pytest.mark.asyncio
async def test_corr_h_f0_f4_horizon_semantics_preserved(app, db_session):
    """Reassessment does NOT change the persisted horizon_class. The
    horizon_alignment field may report drift, but the scenario's
    persisted F0-F4 label is read-only at reassessment time."""
    # F0 scenario
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    assert scenario["horizon_class"] == "F0"

    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Reassess — horizon_class should NOT change.
    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    # Either revised=False (no change) or revised=True with new_evaluation.horizon_class == "F0".
    assert result["new_evaluation"]["horizon_class"] == "F0"

    # Verify the scenario's persisted horizon is unchanged.
    fetched = await get_scenario(db_session, scenario["scenario_id"])
    assert fetched["horizon_class"] == "F0"

    # F4 scenario: with critique + genuine missing deps
    src2 = await _make_source(session=db_session, sid="src-t04-iso", uri="https://example.com/t04-iso")
    frag2 = await _make_fragment(db_session, "frag-t04-iso", src2, "isolated evidence.")
    await _make_entity(session=db_session, eid="cap-t04-isolated", kind="capability", name="isolated-cap")
    await _make_claim(
        db_session,
        "claim-t04-iso",
        "isolated-cap exists.",
        subject_ref="cap-t04-isolated",
        evidence_refs=["frag-t04-iso"],
    )
    r = await create_relationship(
        db_session,
        from_entity_id="ent-t04-toolA",
        to_entity_id="cap-t04-isolated",
        predicate="PROVIDES",
        evidence_refs=["frag-t04-iso"],
        origin="explicit",
    )
    assert r["ok"]
    await db_session.commit()
    await assess_claim(db_session, "claim-t04-iso", requester="t04-fixture")
    await db_session.commit()

    scenario_f4 = await build_scenario(
        db_session,
        capability_ids=["cap-t04-A", "cap-t04-isolated"],
        assumptions=["the integration is speculative"],
        horizon_class_override="F4",
        critique="Speculative; the integration is unproven.",
        missing_dependencies=["cap-t04-isolated"],
        requester="t04-tester",
    )
    await db_session.commit()
    assert scenario_f4["horizon_class"] == "F4"

    eval_f4 = await evaluate_scenario(db_session, scenario_f4["scenario_id"])
    await persist_evaluation_snapshot(
        db_session, scenario_f4["scenario_id"], eval_f4
    )
    await db_session.commit()

    result_f4 = await reassess_scenario(
        db_session,
        scenario_f4["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result_f4["new_evaluation"]["horizon_class"] == "F4"
    fetched_f4 = await get_scenario(db_session, scenario_f4["scenario_id"])
    assert fetched_f4["horizon_class"] == "F4"


# ── Test Corr-I: Deterministic revision identity ──────────────────────────


@pytest.mark.asyncio
async def test_corr_i_deterministic_revision_identity(app, db_session):
    """Same KB state, two reassess calls → same new_evaluation snapshot
    (excluding volatile evaluated_at).

    The first reassessment establishes the baseline (prior=None →
    revised=True). The second reassessment, with no KB change, compares
    against the baseline and returns revised=False. The two new_evaluation
    snapshots are identical on all material fields.
    """
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    # NOTE: deliberately DO NOT call evaluate_scenario() / persist_evaluation_snapshot()
    # before the first reassess — this way the first reassess sees prior=None
    # and establishes the baseline (revised=True). The second reassess then
    # compares against the baseline.

    # First reassess: prior=None → revised=True (baseline established).
    r1 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r1["revised"] is True
    assert r1["prior_evaluation"] is None
    assert r1["new_evaluation"] is not None

    # Second reassess: no KB change → prior=r1.new, new=r1.new (same)
    # → revised=False.
    r2 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r2["revised"] is False

    # The two new_evaluation snapshots are identical on all material fields.
    for field in MATERIAL_FIELDS:
        assert r1["new_evaluation"][field] == r2["new_evaluation"][field], (
            f"Material field {field!r} differs between two no-change reassessments"
        )


# ── Test Corr-K: Append-only history ──────────────────────────────────────


@pytest.mark.asyncio
async def test_corr_k_append_only_history(app, db_session):
    """After multiple revisions, all prior snapshots are preserved (not overwritten)."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    snapshots_before = [0.5]  # initial: claim-A verified, claim-B verified → 1.0
    # Wait, the seed has BOTH claims assessed. So initial coverage is 1.0.
    snapshots_before = [1.0]

    # Revision 1: retract claim-B → coverage drops to 0.5.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()
    r1 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r1["revised"] is True
    snapshots_before.append(r1["new_evaluation"]["evidence_coverage"])

    # Revision 2: re-assess claim-B → coverage back to 1.0.
    await assess_claim(db_session, "claim-t04-2B", requester="t04-tester")
    await db_session.commit()
    r2 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r2["revised"] is True
    snapshots_before.append(r2["new_evaluation"]["evidence_coverage"])

    # Fetch all revisions; verify each prior_evaluation snapshot is preserved.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 2
    # Newest first: revisions[0] = r2, revisions[1] = r1.
    assert revisions[0]["new_version"] == 3
    assert revisions[1]["new_version"] == 2
    # Prior snapshots preserved.
    assert revisions[0]["prior_evaluation"]["evidence_coverage"] == 0.5  # r1's new
    assert revisions[1]["prior_evaluation"]["evidence_coverage"] == 1.0  # eval1's


# ── Test Corr-L: Version monotonicity ─────────────────────────────────────


@pytest.mark.asyncio
async def test_corr_l_version_monotonicity(app, db_session):
    """Versions are strictly increasing by 1: 2, 3, 4, ..."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Trigger 3 revisions by toggling claim-B's verification state.
    for i in range(3):
        # Toggle: retract (odd iterations) or re-assess (even iterations).
        if i % 2 == 0:
            for row in (
                await db_session.execute(
                    select(AuditEventRow).where(
                        AuditEventRow.event_type == "verification.assessed",
                        AuditEventRow.target_id == "claim-t04-2B",
                    )
                )
            ).scalars().all():
                await db_session.delete(row)
        else:
            await assess_claim(db_session, "claim-t04-2B", requester="t04-tester")
        await db_session.commit()
        r = await reassess_scenario(
            db_session,
            scenario["scenario_id"],
            trigger="new_evidence",
            requester="t04-tester",
        )
        await db_session.commit()
        assert r["revised"] is True
        assert r["new_version"] == i + 2

    # Verify monotonicity in the audit trail.
    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    versions = [r["new_version"] for r in revisions]
    # Newest first → [4, 3, 2].
    assert versions == [4, 3, 2]
    # Strictly decreasing by 1 in newest-first order.
    for i in range(len(versions) - 1):
        assert versions[i] == versions[i + 1] + 1


# ── Test Corr-M: Duplicate revision prevention ────────────────────────────


@pytest.mark.asyncio
async def test_corr_m_duplicate_revision_prevention(app, db_session):
    """Reassessing the same KB state twice → only one revision."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # KB change → first reassessment creates a revision.
    for row in (
        await db_session.execute(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "verification.assessed",
                AuditEventRow.target_id == "claim-t04-2B",
            )
        )
    ).scalars().all():
        await db_session.delete(row)
    await db_session.commit()
    r1 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="new_evidence",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r1["revised"] is True

    # Same KB state → second reassessment must NOT create a duplicate.
    r2 = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert r2["revised"] is False

    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 1


# ── Test Corr-N: Authentication, 404, validation behavior ─────────────────


@pytest.mark.asyncio
async def test_corr_n_authentication_404_validation(app, client, db_session, auth_headers_admin, auth_headers_reader):
    """Comprehensive auth/404/validation behavior for both new endpoints."""
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    await db_session.commit()  # extra commit for cross-session visibility

    # REASSESS endpoint:
    # 401 without auth
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 401
    # 404 unknown scenario with auth
    r = client.post(
        "/api/v1/future/scenarios/scn-nonexistent/reassess",
        headers=auth_headers_admin,
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 404
    # 422 invalid trigger
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        headers=auth_headers_admin,
        json={"trigger": "not_a_real_trigger"},
    )
    assert r.status_code == 422
    # 200 reader scope can also reassess (no scope-tier restriction)
    r = client.post(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/reassess",
        headers=auth_headers_reader,
        json={"trigger": "explicit_reassess"},
    )
    assert r.status_code == 200

    # REVISIONS endpoint:
    # 401 without auth
    r = client.get(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/revisions"
    )
    assert r.status_code == 401
    # 404 unknown scenario
    r = client.get(
        "/api/v1/future/scenarios/scn-nonexistent/revisions",
        headers=auth_headers_admin,
    )
    assert r.status_code == 404
    # 200 reader can list
    r = client.get(
        f"/api/v1/future/scenarios/{scenario['scenario_id']}/revisions",
        headers=auth_headers_reader,
    )
    assert r.status_code == 200
    assert "data" in r.json()


# ── Test Corr-O: Newest-first ordering (explicit) ──────────────────────────


@pytest.mark.asyncio
async def test_corr_o_newest_first_ordering(app, db_session):
    """GET /revisions returns revisions newest-first by created_at."""
    fixture = await _seed_two_fragment_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=fixture["fragment_ids"],
        requester="t04-tester",
    )
    await db_session.commit()
    eval1 = await evaluate_scenario(db_session, scenario["scenario_id"])
    await persist_evaluation_snapshot(db_session, scenario["scenario_id"], eval1)
    await db_session.commit()

    # Three revisions by toggling claim-B.
    for i in range(3):
        if i % 2 == 0:
            for row in (
                await db_session.execute(
                    select(AuditEventRow).where(
                        AuditEventRow.event_type == "verification.assessed",
                        AuditEventRow.target_id == "claim-t04-2B",
                    )
                )
            ).scalars().all():
                await db_session.delete(row)
        else:
            await assess_claim(db_session, "claim-t04-2B", requester="t04-tester")
        await db_session.commit()
        await reassess_scenario(
            db_session,
            scenario["scenario_id"],
            trigger="new_evidence",
            requester="t04-tester",
        )
        await db_session.commit()

    revisions = await get_scenario_revisions(db_session, scenario["scenario_id"])
    assert len(revisions) == 3
    # Versions newest-first: 4, 3, 2.
    assert [r["new_version"] for r in revisions] == [4, 3, 2]
    # created_at descending.
    created_ats = [r["created_at"] for r in revisions if r["created_at"]]
    assert created_ats == sorted(created_ats, reverse=True)


# ── Test Corr-P: Material fields documentation ────────────────────────────


@pytest.mark.asyncio
async def test_corr_p_material_fields_documented(app, db_session):
    """MATERIAL_FIELDS is exactly the documented set."""
    expected = {
        "evidence_coverage",
        "distinct_source_uri_count",
        "independent_origin_count",
        "dependency_readiness",
        "contradiction_exposure",
        "assumption_assessment",
        "horizon_alignment",
        "overall",
    }
    assert set(MATERIAL_FIELDS) == expected
    # Order matters for the change list output — verify it's deterministic.
    assert MATERIAL_FIELDS[0] == "evidence_coverage"
    assert MATERIAL_FIELDS[-1] == "overall"


# ── Test Corr-Q: First reassessment establishes baseline ─────────────────


@pytest.mark.asyncio
async def test_corr_q_first_reassessment_establishes_baseline(app, db_session):
    """Reassessing a scenario that has never been evaluated (no
    last_evaluation snapshot) → revised=True, version 1 → 2,
    changes=["initial_revision: ..."]."""
    fixture = await _seed_evaluated_kb(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        evidence_refs=[fixture["fragment_id"]],
        requester="t04-tester",
    )
    await db_session.commit()
    # Note: NO evaluate_scenario call — last_evaluation is NOT persisted.

    result = await reassess_scenario(
        db_session,
        scenario["scenario_id"],
        trigger="explicit_reassess",
        requester="t04-tester",
    )
    await db_session.commit()
    assert result["revised"] is True
    assert result["prior_version"] == 1
    assert result["new_version"] == 2
    assert result["prior_evaluation"] is None
    assert result["new_evaluation"] is not None
    assert any("initial_revision" in c for c in result["changes"])
