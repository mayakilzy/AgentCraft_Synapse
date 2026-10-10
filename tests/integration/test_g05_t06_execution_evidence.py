"""G05-T06 -- Experiment Execution Records + Evidence Feedback.

16 mandatory tests + API contract tests + OpenAPI verification.

Tests cover:
 1. Successful execution-record creation.
 2. Invalid experiment reference.
 3. Valid metric observation.
 4. Invalid or missing metric.
 5. Supported hypothesis.
 6. Contradicted hypothesis.
 7. Inconclusive evidence.
 8. Missing or insufficient evidence.
 9. Duplicate equivalent submission.
10. Conflicting idempotency payload.
11. Failed execution and rollback.
12. Cross-session persistence.
13. Evidence provenance preservation.
14. No automatic VERIFIED promotion.
15. No fabricated evidence or unsupported evidence delta.
16. Existing G05 experiment-planning compatibility.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.execution_record import (
    AssessmentResult,
    ExecutionStatus,
    create_execution,
    finalize_execution,
    get_execution,
    record_observation,
)
from synapse.application.experiment_planner import plan_experiment
from synapse.application.innovation import generate_innovations
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


# ── Fixture helpers ────────────────────────────────────────────────────────


async def _make_source(session, sid: str, uri: str) -> SourceRow:
    src = SourceRow(id=sid, canonical_uri=uri, source_type="paper", status="extracted")
    session.add(src)
    await session.flush()
    return src


async def _make_fragment(session, fid: str, src: SourceRow, excerpt: str) -> EvidenceFragmentRow:
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


async def _make_entity(session, eid: str, kind: str, name: str, desc: str = "") -> EntityRow:
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


async def _seed_t06_fixture(session) -> dict[str, Any]:
    """Seed a fixture and generate an innovation + experiment plan."""
    src_a = await _make_source(session, "src-t06-a", "https://example.com/arxiv-t06")
    src_b = await _make_source(session, "src-t06-b", "https://example.com/trafilatura-t06")
    src_c = await _make_source(session, "src-t06-c", "https://example.com/synapse-t06")

    frag_a = await _make_fragment(session, "frag-t06-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-t06-b", src_b, "trafilatura extracts content.")
    frag_c = await _make_fragment(session, "frag-t06-c", src_c, "synapse provides evidence verification.")

    await _make_entity(session, "ent-t06-arxiv", "technology", "arxiv")
    await _make_entity(session, "ent-t06-trafilatura", "technology", "trafilatura")
    await _make_entity(session, "ent-t06-synapse", "tool", "synapse")

    for cap_id, name in [
        ("cap-t06-sd", "source discovery"),
        ("cap-t06-ce", "content extraction"),
        ("cap-t06-ev", "evidence verification"),
    ]:
        await _make_entity(session, cap_id, "capability", name)

    await _make_claim(session, "claim-t06-sd", "arxiv provides source discovery.",
                      subject_ref="ent-t06-arxiv", object_ref="cap-t06-sd", evidence_refs=["frag-t06-a"])
    await _make_claim(session, "claim-t06-ce", "trafilatura provides content extraction.",
                      subject_ref="ent-t06-trafilatura", object_ref="cap-t06-ce", evidence_refs=["frag-t06-b"])
    await _make_claim(session, "claim-t06-ev", "synapse provides evidence verification.",
                      subject_ref="ent-t06-synapse", object_ref="cap-t06-ev", evidence_refs=["frag-t06-c"])

    for frm, to, ev in [
        ("ent-t06-arxiv", "cap-t06-sd", ["frag-t06-a"]),
        ("ent-t06-trafilatura", "cap-t06-ce", ["frag-t06-b"]),
        ("ent-t06-synapse", "cap-t06-ev", ["frag-t06-c"]),
    ]:
        r = await create_relationship(session, from_entity_id=frm, to_entity_id=to,
                                       predicate="PROVIDES", evidence_refs=ev, origin="explicit")
        assert r["ok"] is True

    await session.commit()
    for cid in ("claim-t06-sd", "claim-t06-ce", "claim-t06-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    gen_result = await generate_innovations(session, "AI research assistant", context="AI agent systems")
    assert len(gen_result["concepts"]) >= 1
    concept_id = gen_result["concepts"][0]["id"]
    hypothesis_id = gen_result["concepts"][0]["hypothesis_id"]

    # Create an experiment plan with a user-supplied metric (so we can
    # control the success criteria for testing)
    plan_result = await plan_experiment(
        session, hypothesis_id,
        protocol="Test protocol for T06.",
        metrics={"precision_at_10": "float"},
    )
    assert plan_result is not None
    experiment_id = plan_result["experiment"]["experiment_id"]
    await session.commit()

    return {
        "concept_id": concept_id,
        "hypothesis_id": hypothesis_id,
        "experiment_id": experiment_id,
        "experiment_plan": plan_result["experiment"],
    }


# ── Test 1: Successful execution-record creation ──────────────────────────


@pytest.mark.asyncio
async def test_01_successful_execution_creation(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    result = await create_execution(db_session, fixture["experiment_id"], requester="tester")
    assert result is not None
    assert result["experiment_id"] == fixture["experiment_id"]
    assert result["hypothesis_id"] == fixture["hypothesis_id"]
    assert result["status"] == ExecutionStatus.CREATED
    assert result["started_at"] is not None
    assert result["completed_at"] is None
    assert result["observations"] == []
    assert result["assessment"] is None
    assert result["evidence_delta_id"] is None
    assert result["was_reused"] is False


# ── Test 2: Invalid experiment reference ──────────────────────────────────


@pytest.mark.asyncio
async def test_02_invalid_experiment_reference(app, db_session):
    result = await create_execution(db_session, "exp-does-not-exist")
    assert result is None


# ── Test 3: Valid metric observation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_03_valid_metric_observation(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    obs = await record_observation(
        db_session, exec_id,
        metric="precision_at_10",
        observed_value=0.85,
        measurement_method="manual",
        source_ref="experimenter-001",
    )
    assert obs is not None
    assert obs["metric"] == "precision_at_10"
    assert obs["observed_value"] == 0.85
    assert obs["source_ref"] == "experimenter-001"
    assert obs["observation_id"].startswith("obs-")

    # Verify the execution transitioned to RUNNING
    updated = await get_execution(db_session, exec_id)
    assert updated["status"] == ExecutionStatus.RUNNING
    assert len(updated["observations"]) == 1


# ── Test 4: Invalid or missing metric ─────────────────────────────────────


@pytest.mark.asyncio
async def test_04_invalid_metric_rejected(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    with pytest.raises(ValueError, match="not declared"):
        await record_observation(
            db_session, exec_id,
            metric="nonexistent_metric",
            observed_value=0.5,
        )


# ── Test 5: Supported hypothesis ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_05_supported_hypothesis(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Record an observation that meets the success criterion
    # The plan's success_criteria has "precision_at_10" with threshold=None
    # (requires_calibration). We need to manually set a threshold to test
    # the supporting path. Let's update the execution's criteria.
    from sqlalchemy import select as sel
    entity = (await db_session.execute(
        sel(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution")
    )).scalar_one()
    attrs = json.loads(entity.attributes)
    # Set operational thresholds
    for sc in attrs["success_criteria"]:
        if sc["metric"] == "precision_at_10":
            sc["threshold"] = 0.7
            sc["calibration_status"] = "operational"
    for fc in attrs["failure_criteria"]:
        if fc["metric"] == "precision_at_10":
            fc["threshold"] = 0.3
            fc["calibration_status"] = "operational"
    entity.attributes = json.dumps(attrs, default=str)
    await db_session.flush()

    # Record observation = 0.85 (meets success >= 0.7, does not trigger failure < 0.3)
    await record_observation(db_session, exec_id, metric="precision_at_10", observed_value=0.85)

    # Finalize
    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)
    assert result is not None
    assert result["assessment"]["result"] == AssessmentResult.SUPPORTING
    assert result["evidence_delta_id"] is not None

    # Verify the hypothesis state changed
    hyp = (await db_session.execute(
        sel(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.SUPPORTED.value


# ── Test 6: Contradicted hypothesis ───────────────────────────────────────


@pytest.mark.asyncio
async def test_06_contradicted_hypothesis(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Set operational thresholds
    from sqlalchemy import select as sel
    entity = (await db_session.execute(
        sel(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution")
    )).scalar_one()
    attrs = json.loads(entity.attributes)
    for sc in attrs["success_criteria"]:
        if sc["metric"] == "precision_at_10":
            sc["threshold"] = 0.7
            sc["calibration_status"] = "operational"
    for fc in attrs["failure_criteria"]:
        if fc["metric"] == "precision_at_10":
            fc["threshold"] = 0.3
            fc["calibration_status"] = "operational"
            fc["comparator"] = "<"
    entity.attributes = json.dumps(attrs, default=str)
    await db_session.flush()

    # Record observation = 0.1 (triggers failure < 0.3)
    await record_observation(db_session, exec_id, metric="precision_at_10", observed_value=0.1)

    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)
    assert result is not None
    assert result["assessment"]["result"] == AssessmentResult.CONTRADICTING
    assert result["evidence_delta_id"] is not None

    hyp = (await db_session.execute(
        sel(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.DISPUTED.value


# ── Test 7: Inconclusive evidence ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_07_inconclusive_evidence(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Set operational threshold for success only (no failure criteria operational)
    from sqlalchemy import select as sel
    entity = (await db_session.execute(
        sel(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution")
    )).scalar_one()
    attrs = json.loads(entity.attributes)
    for sc in attrs["success_criteria"]:
        if sc["metric"] == "precision_at_10":
            sc["threshold"] = 0.9  # high bar
            sc["calibration_status"] = "operational"
    # Leave failure criteria as requires_calibration
    entity.attributes = json.dumps(attrs, default=str)
    await db_session.flush()

    # Record observation = 0.5 (does not meet success >= 0.9, no failure triggered)
    await record_observation(db_session, exec_id, metric="precision_at_10", observed_value=0.5)

    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)
    assert result is not None
    assert result["assessment"]["result"] == AssessmentResult.INCONCLUSIVE
    # No state change → no delta
    assert result["evidence_delta_id"] is None


# ── Test 8: Missing or insufficient evidence ──────────────────────────────


@pytest.mark.asyncio
async def test_08_insufficient_evidence(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Finalize without any observations
    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)
    assert result is not None
    assert result["assessment"]["result"] == AssessmentResult.INSUFFICIENT_DATA
    assert result["evidence_delta_id"] is None


# ── Test 9: Duplicate equivalent submission ───────────────────────────────


@pytest.mark.asyncio
async def test_09_duplicate_equivalent_submission(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    r1 = await create_execution(db_session, fixture["experiment_id"])
    r2 = await create_execution(db_session, fixture["experiment_id"])
    assert r1 is not None and r2 is not None
    assert r1["execution_id"] == r2["execution_id"]
    assert r1["was_reused"] is False
    assert r2["was_reused"] is True


# ── Test 10: Conflicting idempotency payload ──────────────────────────────


@pytest.mark.asyncio
async def test_10_conflicting_idempotency_payload(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    # Same experiment but different execution_mode → different fingerprint
    r1 = await create_execution(db_session, fixture["experiment_id"], execution_mode="dry_run")
    r2 = await create_execution(db_session, fixture["experiment_id"], execution_mode="sandbox")
    assert r1 is not None and r2 is not None
    assert r1["execution_id"] != r2["execution_id"]
    assert r1["execution_mode"] == "dry_run"
    assert r2["execution_mode"] == "sandbox"


# ── Test 11: Failed execution and rollback ────────────────────────────────


@pytest.mark.asyncio
async def test_11_failed_execution_rollback(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    result = await finalize_execution(
        db_session, exec_id,
        status=ExecutionStatus.FAILED,
        error_details={"reason": "test failure"},
    )
    assert result is not None
    assert result["status"] == ExecutionStatus.FAILED
    assert result["error_details"]["reason"] == "test failure"
    # No assessment for failed executions
    assert result["assessment"] is None
    assert result["evidence_delta_id"] is None


# ── Test 12: Cross-session persistence ────────────────────────────────────


@pytest.mark.asyncio
async def test_12_cross_session_persistence(app, db_session, engine):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]
    await db_session.commit()

    # Retrieve via a separate session
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session2:
        fetched = await get_execution(session2, exec_id)

    assert fetched is not None
    assert fetched["execution_id"] == exec_id
    assert fetched["experiment_id"] == fixture["experiment_id"]


# ── Test 13: Evidence provenance preservation ─────────────────────────────


@pytest.mark.asyncio
async def test_13_evidence_provenance_preservation(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    await record_observation(
        db_session, exec_id,
        metric="precision_at_10",
        observed_value=0.85,
        source_ref="experimenter-001",
        measurement_method="automated_test",
    )

    fetched = await get_execution(db_session, exec_id)
    assert len(fetched["observations"]) == 1
    obs = fetched["observations"][0]
    assert obs["source_ref"] == "experimenter-001"
    assert obs["measurement_method"] == "automated_test"
    assert obs["evidence_origin"] == "experiment_execution"
    assert obs["timestamp"] is not None


# ── Test 14: No automatic VERIFIED promotion ──────────────────────────────


@pytest.mark.asyncio
async def test_14_no_auto_verified_promotion(app, db_session):
    """EpistemicState has no VERIFIED value — impossible by construction."""
    allowed = {e.value for e in EpistemicState}
    assert "verified" not in allowed

    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Set operational thresholds and record a supporting observation
    from sqlalchemy import select as sel
    entity = (await db_session.execute(
        sel(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution")
    )).scalar_one()
    attrs = json.loads(entity.attributes)
    for sc in attrs["success_criteria"]:
        if sc["metric"] == "precision_at_10":
            sc["threshold"] = 0.7
            sc["calibration_status"] = "operational"
    entity.attributes = json.dumps(attrs, default=str)
    await db_session.flush()

    await record_observation(db_session, exec_id, metric="precision_at_10", observed_value=0.85)
    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)

    # The hypothesis moved to SUPPORTED, NOT VERIFIED
    hyp = (await db_session.execute(
        sel(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
    )).scalar_one()
    assert hyp.epistemic_state == EpistemicState.SUPPORTED.value
    assert hyp.epistemic_state in allowed
    assert hyp.epistemic_state != "verified"


# ── Test 15: No fabricated evidence or unsupported evidence delta ──────────


@pytest.mark.asyncio
async def test_15_no_fabricated_evidence_delta(app, db_session):
    fixture = await _seed_t06_fixture(db_session)
    exec_result = await create_execution(db_session, fixture["experiment_id"])
    exec_id = exec_result["execution_id"]

    # Count audit events before
    before_count = len((await db_session.execute(
        select(AuditEventRow).where(AuditEventRow.event_type == "evidence.delta")
    )).scalars().all())

    # Finalize without observations → insufficient_data → NO delta
    result = await finalize_execution(db_session, exec_id, status=ExecutionStatus.COMPLETED)
    assert result["assessment"]["result"] == AssessmentResult.INSUFFICIENT_DATA

    after_count = len((await db_session.execute(
        select(AuditEventRow).where(AuditEventRow.event_type == "evidence.delta")
    )).scalars().all())
    assert after_count == before_count, "EvidenceDelta was created without evidence"


# ── Test 16: Existing G05 experiment-planning compatibility ───────────────


@pytest.mark.asyncio
async def test_16_experiment_planning_compatibility(app, db_session):
    """G05-T05 experiment planning still works after T06 changes."""
    fixture = await _seed_t06_fixture(db_session)
    # The experiment plan is valid
    assert fixture["experiment_id"].startswith("exp-")
    assert fixture["hypothesis_id"].startswith("hyp-")
    # Can create another experiment plan
    plan2 = await plan_experiment(
        db_session, fixture["hypothesis_id"],
        protocol="Another protocol.",
        metrics={"recall_at_10": "float"},
    )
    assert plan2 is not None
    assert plan2["experiment"]["experiment_id"] != fixture["experiment_id"]


# ── API contract tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_execute_requires_auth(app, client, db_session, auth_headers_reader):
    fixture = await _seed_t06_fixture(db_session)
    await db_session.commit()
    # No auth → 401
    r = client.post(f"/api/v1/experiments/{fixture['experiment_id']}/execute", json={})
    assert r.status_code == 401

    # With auth → 200
    r = client.post(
        f"/api/v1/experiments/{fixture['experiment_id']}/execute",
        headers=auth_headers_reader, json={},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["experiment_id"] == fixture["experiment_id"]
    exec_id = data["execution_id"]

    # Commit for cross-session visibility (API uses separate session)
    await db_session.commit()

    # GET execution
    r = client.get(f"/api/v1/executions/{exec_id}", headers=auth_headers_reader)
    assert r.status_code == 200
    assert r.json()["data"]["execution_id"] == exec_id

    # Record observation via API
    r = client.post(
        f"/api/v1/executions/{exec_id}/observations",
        headers=auth_headers_reader,
        json={"metric": "precision_at_10", "observed_value": 0.8},
    )
    assert r.status_code == 200, r.text

    # Finalize via API
    await db_session.commit()
    r = client.post(
        f"/api/v1/executions/{exec_id}/finalize",
        headers=auth_headers_reader,
        json={"status": "completed"},
    )
    assert r.status_code == 200, r.text

    # GET evidence-deltas
    await db_session.commit()
    r = client.get(
        f"/api/v1/hypotheses/{fixture['hypothesis_id']}/evidence-deltas",
        headers=auth_headers_reader,
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_api_404_missing_experiment(app, client, db_session, auth_headers_reader):
    await db_session.commit()
    r = client.post(
        "/api/v1/experiments/exp-does-not-exist/execute",
        headers=auth_headers_reader, json={},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_422_invalid_metric(app, client, db_session, auth_headers_reader):
    fixture = await _seed_t06_fixture(db_session)
    await db_session.commit()
    r = client.post(
        f"/api/v1/experiments/{fixture['experiment_id']}/execute",
        headers=auth_headers_reader, json={},
    )
    exec_id = r.json()["data"]["execution_id"]
    await db_session.commit()

    r = client.post(
        f"/api/v1/executions/{exec_id}/observations",
        headers=auth_headers_reader,
        json={"metric": "nonexistent", "observed_value": 0.5},
    )
    assert r.status_code == 422


# ── OpenAPI verification ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openapi_includes_t06_endpoints(app):
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/experiments/{experiment_id}/execute" in paths
    assert "post" in paths["/api/v1/experiments/{experiment_id}/execute"]
    assert "/api/v1/executions/{execution_id}" in paths
    assert "get" in paths["/api/v1/executions/{execution_id}"]
    assert "/api/v1/executions/{execution_id}/observations" in paths
    assert "post" in paths["/api/v1/executions/{execution_id}/observations"]
    assert "/api/v1/executions/{execution_id}/finalize" in paths
    assert "post" in paths["/api/v1/executions/{execution_id}/finalize"]
    assert "/api/v1/hypotheses/{hypothesis_id}/evidence-deltas" in paths
    assert "get" in paths["/api/v1/hypotheses/{hypothesis_id}/evidence-deltas"]
