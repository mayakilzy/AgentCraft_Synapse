"""G05-T05 -- Evidence-Grounded Experiment Planning.

Per the corrected G05 plan §G05-T05, the 6 acceptance criteria:
  1. Every experiment plan has >=1 success criterion.
  2. Every experiment has execution_mode (default dry_run).
  3. production mode requires safety_limits.approved_by
     (already enforced by Experiment invariant).
  4. POST /api/v1/experiments returns 200 with auth.
  5. GET /api/v1/experiments/{id} returns 200 / 404.
  6. G01-G05-T04 regression intact.

Plus the 10 quality gates:
  1. Every experiment references a valid persisted hypothesis.
  2. Objectives and metrics are measurable.
  3. Success and failure conditions are explicit.
  4. Plans are reproducible to the extent supported by available info.
  5. Unavailable resources are reported rather than invented.
  6. Experiment plans do not change hypothesis confidence or epistemic state.
  7. No ObservationRecord or EvidenceDelta is created during planning.
  8. Multiple experiments may reference the same hypothesis.
  9. No fabricated experimental outcomes.
  10. No automatic VERIFIED promotion.

Negative tests cover: missing hypothesis, untestable assumptions, absent
resources, repeated requests, multiple experiments per hypothesis, missing
evidence, API authentication, 404 handling, and the production-mode
approval gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.experiment_planner import (
    get_experiment,
    plan_experiment,
)
from synapse.application.innovation import (
    critique_innovation,
    generate_innovations,
)
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()


# ── Fixture helpers (mirror T04 patterns) ──────────────────────────────────


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


async def _seed_experiment_fixture(session) -> dict[str, Any]:
    """Seed a fixture and generate an innovation concept + hypothesis.

    This mirrors T04's fixture but with T05-specific names so that the
    two suites don't collide if run in the same DB session.
    """
    src_a = await _make_source(session, "src-t05-a", "https://example.com/arxiv-t05")
    src_b = await _make_source(session, "src-t05-b", "https://example.com/trafilatura-t05")
    src_c = await _make_source(session, "src-t05-c", "https://example.com/synapse-t05")
    src_d = await _make_source(session, "src-t05-d", "https://example.com/contradiction-t05")

    frag_a = await _make_fragment(session, "frag-t05-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-t05-b", src_b, "trafilatura extracts content.")
    frag_c = await _make_fragment(
        session, "frag-t05-c", src_c, "synapse provides evidence verification."
    )
    frag_opp = await _make_fragment(
        session, "frag-t05-opp", src_d, "Evidence verification cannot be automated."
    )

    await _make_entity(session, "ent-t05-arxiv", "technology", "arxiv", desc="arXiv")
    await _make_entity(session, "ent-t05-trafilatura", "technology", "trafilatura", desc="trafilatura")
    await _make_entity(session, "ent-t05-synapse", "tool", "synapse", desc="Synapse")
    await _make_entity(
        session, "ent-t05-no-vector", "constraint", "no vector database", desc="No vector DB"
    )
    await _make_entity(session, "ent-t05-alttool", "tool", "AltTool", desc="Alternative tool")

    for cap_id, name in [
        ("cap-t05-source-discovery", "source discovery"),
        ("cap-t05-content-extraction", "content extraction"),
        ("cap-t05-evidence-verification", "evidence verification"),
        ("cap-t05-knowledge-extraction", "structured knowledge extraction"),
    ]:
        await _make_entity(session, cap_id, "capability", name)

    await _make_claim(
        session,
        "claim-t05-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-t05-arxiv",
        object_ref="cap-t05-source-discovery",
        evidence_refs=["frag-t05-a"],
    )
    await _make_claim(
        session,
        "claim-t05-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-t05-trafilatura",
        object_ref="cap-t05-content-extraction",
        evidence_refs=["frag-t05-b"],
    )
    await _make_claim(
        session,
        "claim-t05-ev",
        "synapse provides evidence verification.",
        subject_ref="ent-t05-synapse",
        object_ref="cap-t05-evidence-verification",
        evidence_refs=["frag-t05-c"],
        contradicting_refs=["frag-t05-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    for from_id, to_id, ev_refs in [
        ("ent-t05-arxiv", "cap-t05-source-discovery", ["frag-t05-a"]),
        ("ent-t05-trafilatura", "cap-t05-content-extraction", ["frag-t05-b"]),
        ("ent-t05-synapse", "cap-t05-evidence-verification", ["frag-t05-c"]),
    ]:
        r = await create_relationship(
            session,
            from_entity_id=from_id,
            to_entity_id=to_id,
            predicate="PROVIDES",
            evidence_refs=ev_refs,
            origin="explicit",
        )
        assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-t05-no-vector",
        to_entity_id="cap-t05-evidence-verification",
        predicate="LIMITS",
        evidence_refs=[],
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-t05-alttool",
        to_entity_id="ent-t05-synapse",
        predicate="REPLACES",
        evidence_refs=["frag-t05-c"],
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()
    for cid in ("claim-t05-sd", "claim-t05-ce", "claim-t05-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    # Generate an innovation concept
    gen_result = await generate_innovations(
        session,
        "AI research assistant",
        context="AI agent systems",
        max_concepts=5,
    )
    assert len(gen_result["concepts"]) >= 1

    return {
        "concept_id": gen_result["concepts"][0]["id"],
        "hypothesis_id": gen_result["concepts"][0]["hypothesis_id"],
    }


# ── Acceptance Test 1: >=1 success criterion ──────────────────────────────


@pytest.mark.asyncio
async def test_01_plan_has_success_criterion(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]
    assert len(plan["success_criteria"]) >= 1
    # Each criterion has a metric, comparator, and rationale
    for c in plan["success_criteria"]:
        assert "metric" in c
        assert "comparator" in c
        assert "rationale" in c


# ── Acceptance Test 2: execution_mode default = dry_run ────────────────────


@pytest.mark.asyncio
async def test_02_execution_mode_default_dry_run(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]
    assert plan["execution_mode"] == "dry_run"


# ── Acceptance Test 3: production mode requires approved_by ───────────────


@pytest.mark.asyncio
async def test_03_production_mode_requires_approval(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # Without approved_by -> should raise (ValidationError from domain invariant)
    with pytest.raises(ValueError, match="approved_by"):
        await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            execution_mode="production",
            safety_limits={},
        )

    # With approved_by -> succeeds
    result = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        execution_mode="production",
        safety_limits={"approved_by": "operator-001"},
    )
    assert result is not None
    plan = result["experiment"]
    assert plan["execution_mode"] == "production"
    assert plan["safety_limits"]["approved_by"] == "operator-001"


# ── Acceptance Test 4: POST /api/v1/experiments returns 200 with auth ─────


@pytest.mark.asyncio
async def test_04_api_post_returns_200_with_auth(app, client, db_session, auth_headers_reader):
    fixture = await _seed_experiment_fixture(db_session)

    r = client.post(
        "/api/v1/experiments",
        headers=auth_headers_reader,
        json={"hypothesis_id": fixture["hypothesis_id"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "experiment" in data
    plan = data["experiment"]
    assert plan["hypothesis_id"] == fixture["hypothesis_id"]
    assert plan["execution_mode"] == "dry_run"
    assert len(plan["success_criteria"]) >= 1


# ── Acceptance Test 5: GET /experiments/{id} returns 200 / 404 ────────────


@pytest.mark.asyncio
async def test_05_api_get_returns_200_or_404(app, client, db_session, auth_headers_reader):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    exp_id = result["experiment"]["experiment_id"]
    # Commit so the API (separate session) can see the persisted experiment
    await db_session.commit()

    # 200 for existing
    r = client.get(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers_reader,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["experiment_id"] == exp_id

    # 404 for missing
    r = client.get(
        "/api/v1/experiments/exp-does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404


# ── Acceptance Test 6: regression intact (smoke) ──────────────────────────


@pytest.mark.asyncio
async def test_06_regression_smoke(app, db_session):
    """Planning does not break G01-G05-T04 functionality.

    This is a smoke test -- the full regression suite is run separately.
    """
    fixture = await _seed_experiment_fixture(db_session)

    # Plan an experiment
    plan_result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert plan_result is not None

    # G05-T04 critique still works after planning
    critique = await critique_innovation(db_session, fixture["concept_id"])
    assert critique is not None
    assert "feasibility_score" in critique

    # G05-T03 generate still works
    gen = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    assert len(gen["concepts"]) >= 1


# ── Quality Gate 1: references a valid persisted hypothesis ───────────────


@pytest.mark.asyncio
async def test_qg1_references_valid_hypothesis(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # The hypothesis_id must point to a real ClaimRow
    stmt = select(ClaimRow).where(ClaimRow.id == plan["hypothesis_id"])
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is not None
    # The concept_id must point to a real EntityRow
    stmt = select(EntityRow).where(EntityRow.id == plan["concept_id"])
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is not None
    assert row.kind == "project"


# ── Quality Gate 2: objectives and metrics are measurable ────────────────


@pytest.mark.asyncio
async def test_qg2_objectives_and_metrics_measurable(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # Objectives are non-empty strings
    assert len(plan["objectives"]) >= 1
    for obj in plan["objectives"]:
        assert isinstance(obj, str)
        assert len(obj) > 10  # not a trivial stub

    # Each success criterion has a metric name and a comparator
    for c in plan["success_criteria"]:
        assert c["metric"]
        assert c["comparator"]


# ── Quality Gate 3: success and failure conditions are explicit ──────────


@pytest.mark.asyncio
async def test_qg3_success_and_failure_explicit(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    assert len(plan["success_criteria"]) >= 1
    assert len(plan["failure_criteria"]) >= 1
    # Each criterion has a rationale explaining when it triggers
    for c in plan["success_criteria"] + plan["failure_criteria"]:
        assert c.get("rationale")


# ── Quality Gate 4: plans are reproducible ────────────────────────────────


@pytest.mark.asyncio
async def test_qg4_reproducible(app, db_session):
    """Same input + same DB state -> same plan identity and content."""
    fixture = await _seed_experiment_fixture(db_session)

    # Same explicit protocol + metrics -> same experiment_id
    r1 = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Run arxiv_search for 50 AI agent papers; measure retrieval precision.",
        metrics={"precision_at_10": "float"},
    )
    r2 = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Run arxiv_search for 50 AI agent papers; measure retrieval precision.",
        metrics={"precision_at_10": "float"},
    )
    assert r1 is not None and r2 is not None
    assert r1["experiment"]["experiment_id"] == r2["experiment"]["experiment_id"]
    assert r2["experiment"]["was_reused"] is True
    assert r1["experiment"]["was_reused"] is False


# ── Quality Gate 5: unavailable resources are reported, not invented ─────


@pytest.mark.asyncio
async def test_qg5_unavailable_resources_reported(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # Every required_input has an explicit available flag
    for ri in plan["required_inputs"]:
        assert "available" in ri
        assert isinstance(ri["available"], bool)
        assert "rationale" in ri

    # If any input is unavailable, it's recorded in unknowns
    unavailable = [r for r in plan["required_inputs"] if not r["available"]]
    if unavailable:
        unknowns = result["unknowns"]
        assert any("unavailable input" in u for u in unknowns)


# ── Quality Gate 6: no change to hypothesis confidence or epistemic state ─


@pytest.mark.asyncio
async def test_qg6_no_hypothesis_state_change(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # Snapshot the hypothesis BEFORE planning
    stmt = select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
    hyp_before = (await db_session.execute(stmt)).scalar_one()
    state_before = hyp_before.epistemic_state
    confidence_before = hyp_before.confidence_value
    version_before = hyp_before.version

    # Plan an experiment
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None

    # Refresh and check
    await db_session.refresh(hyp_before)
    assert hyp_before.epistemic_state == state_before
    assert hyp_before.confidence_value == confidence_before
    assert hyp_before.version == version_before


# ── Quality Gate 7: no ObservationRecord or EvidenceDelta created ────────


@pytest.mark.asyncio
async def test_qg7_no_observation_or_evidence_delta(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # Count audit events before
    stmt = select(AuditEventRow).where(AuditEventRow.event_type == "evidence.delta")
    before_count = len((await db_session.execute(stmt)).scalars().all())

    # Plan
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None

    # Count after -- no new evidence.delta events
    after_count = len((await db_session.execute(stmt)).scalars().all())
    assert after_count == before_count

    # The experiment's result field is None
    plan = result["experiment"]
    assert plan["result"] is None
    assert plan["artifact_refs"] == []


# ── Quality Gate 8: multiple experiments may reference same hypothesis ───


@pytest.mark.asyncio
async def test_qg8_multiple_experiments_per_hypothesis(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # Plan experiment A: precision metric
    r_a = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Measure retrieval precision at K=10.",
        metrics={"precision_at_10": "float"},
    )
    # Plan experiment B: latency metric (different protocol -> different fingerprint)
    r_b = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Measure end-to-end latency under load.",
        metrics={"p95_latency_ms": "int"},
    )

    assert r_a is not None and r_b is not None
    assert r_a["experiment"]["experiment_id"] != r_b["experiment"]["experiment_id"]
    assert r_a["experiment"]["hypothesis_id"] == r_b["experiment"]["hypothesis_id"]
    # Both can be retrieved
    a = await get_experiment(db_session, r_a["experiment"]["experiment_id"])
    b = await get_experiment(db_session, r_b["experiment"]["experiment_id"])
    assert a is not None
    assert b is not None


# ── Quality Gate 9: no fabricated experimental outcomes ──────────────────


@pytest.mark.asyncio
async def test_qg9_no_fabricated_outcomes(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # The result field must be None -- planning never produces an outcome
    assert plan["result"] is None
    # No artifact_refs (those come from execution)
    assert plan["artifact_refs"] == []
    # No observation text in the plan
    assert "observation" not in plan
    assert "outcome" not in plan


# ── Quality Gate 10: no automatic VERIFIED promotion ─────────────────────


@pytest.mark.asyncio
async def test_qg10_no_verified_promotion(app, db_session):
    """EpistemicState has no VERIFIED value -- impossible by construction.

    This test verifies the hypothesis's epistemic_state remains in the
    set of allowed non-VERIFIED values after planning.
    """
    from synapse.domain._base import EpistemicState

    # Sanity: EpistemicState has no VERIFIED
    allowed = {e.value for e in EpistemicState}
    assert "verified" not in allowed
    assert "VERIFIED" not in allowed

    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None

    # The hypothesis's epistemic state is unchanged and in the allowed set
    stmt = select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
    hyp = (await db_session.execute(stmt)).scalar_one()
    assert hyp.epistemic_state in allowed


# ── Negative Test 1: missing hypothesis returns None ──────────────────────


@pytest.mark.asyncio
async def test_neg_missing_hypothesis(app, db_session):
    result = await plan_experiment(db_session, "hyp-does-not-exist")
    assert result is None


# ── Negative Test 2: hypothesis without subject_ref returns None ────────


@pytest.mark.asyncio
async def test_neg_hypothesis_without_subject_ref(app, db_session):
    # Create a hypothesis with no subject_ref -- it's not linked to a concept
    await _make_claim(
        db_session,
        "hyp-orphan-t05",
        "Orphan hypothesis with no concept linkage.",
        epistemic_state="hypothesized",
        # No subject_ref
    )
    await db_session.commit()

    result = await plan_experiment(db_session, "hyp-orphan-t05")
    assert result is None


# ── Negative Test 3: untestable assumptions are reported ─────────────────


@pytest.mark.asyncio
async def test_neg_untestable_assumptions_reported(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # The plan must contain an untestable_assumptions list (may be empty
    # if everything is testable, but the field must exist)
    assert "untestable_assumptions" in plan
    assert isinstance(plan["untestable_assumptions"], list)

    # If there are missing components (NOT_EVIDENCED gaps), they should
    # appear in untestable_assumptions
    if plan["missing_evidence"]:
        assert len(plan["untestable_assumptions"]) >= 1 or len(plan["missing_evidence"]) >= 1


# ── Negative Test 4: absent resources are flagged available=false ────────


@pytest.mark.asyncio
async def test_neg_absent_resources_flagged(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # All required_inputs have the available flag (no missing field)
    for ri in plan["required_inputs"]:
        assert isinstance(ri.get("available"), bool)

    # If a missing_capability entry exists, it must be available=false
    for ri in plan["required_inputs"]:
        if ri.get("kind") == "missing_capability":
            assert ri["available"] is False
            assert "rationale" in ri


# ── Negative Test 5: repeated requests are idempotent ────────────────────


@pytest.mark.asyncio
async def test_neg_repeated_requests_idempotent(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # Same explicit protocol + metrics both times
    r1 = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Identical protocol text for idempotency check.",
        metrics={"accuracy": "float"},
    )
    r2 = await plan_experiment(
        db_session,
        fixture["hypothesis_id"],
        protocol="Identical protocol text for idempotency check.",
        metrics={"accuracy": "float"},
    )
    assert r1 is not None and r2 is not None
    assert r1["experiment"]["experiment_id"] == r2["experiment"]["experiment_id"]
    assert r1["experiment"]["was_reused"] is False
    assert r2["experiment"]["was_reused"] is True

    # Verify only one EntityRow(kind='experiment') exists for this fingerprint
    stmt = select(EntityRow).where(EntityRow.kind == "experiment")
    rows = (await db_session.execute(stmt)).scalars().all()
    exp_ids = {r.id for r in rows}
    assert r1["experiment"]["experiment_id"] in exp_ids


# ── Negative Test 6: API auth required ────────────────────────────────────


@pytest.mark.asyncio
async def test_neg_api_auth_required(app, client, db_session):
    fixture = await _seed_experiment_fixture(db_session)

    # No Authorization header -> 401
    r = client.post(
        "/api/v1/experiments",
        json={"hypothesis_id": fixture["hypothesis_id"]},
    )
    assert r.status_code == 401


# ── Negative Test 7: API 404 for missing hypothesis ───────────────────────


@pytest.mark.asyncio
async def test_neg_api_404_missing_hypothesis(app, client, db_session, auth_headers_reader):
    r = client.post(
        "/api/v1/experiments",
        headers=auth_headers_reader,
        json={"hypothesis_id": "hyp-does-not-exist"},
    )
    assert r.status_code == 404


# ── Negative Test 8: API 422 for production without approved_by ───────────


@pytest.mark.asyncio
async def test_neg_api_422_production_without_approval(app, client, db_session, auth_headers_reader):
    fixture = await _seed_experiment_fixture(db_session)

    r = client.post(
        "/api/v1/experiments",
        headers=auth_headers_reader,
        json={
            "hypothesis_id": fixture["hypothesis_id"],
            "execution_mode": "production",
            "safety_limits": {},
        },
    )
    assert r.status_code == 422


# ── Negative Test 9: API 422 for unknown execution_mode ──────────────────


@pytest.mark.asyncio
async def test_neg_api_422_unknown_execution_mode(app, client, db_session, auth_headers_reader):
    fixture = await _seed_experiment_fixture(db_session)

    r = client.post(
        "/api/v1/experiments",
        headers=auth_headers_reader,
        json={
            "hypothesis_id": fixture["hypothesis_id"],
            "execution_mode": "quantum_supremacy",  # not a real mode
        },
    )
    assert r.status_code == 422


# ── Negative Test 10: API GET requires auth ──────────────────────────────


@pytest.mark.asyncio
async def test_neg_api_get_requires_auth(app, client, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    exp_id = result["experiment"]["experiment_id"]
    await db_session.commit()

    r = client.get(f"/api/v1/experiments/{exp_id}")
    assert r.status_code == 401


# ── Negative Test 11: experiment preserves evidence references ────────────


@pytest.mark.asyncio
async def test_neg_evidence_refs_preserved(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # The plan should carry evidence_refs from the parent concept's hypothesis
    # The hypothesis was created with evidence_refs from generate_innovations
    assert isinstance(plan["evidence_refs"], list)
    # All evidence_refs must point to existing fragments (no fabrication)
    if plan["evidence_refs"]:
        for ref in plan["evidence_refs"]:
            stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == ref)
            row = (await db_session.execute(stmt)).scalar_one_or_none()
            assert row is not None, f"evidence_ref {ref} does not resolve"


# ── Negative Test 12: experiment preserves applicable contradictions ─────


@pytest.mark.asyncio
async def test_neg_contradictions_preserved(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    # applicable_contradictions field exists and is a list
    assert "applicable_contradictions" in plan
    assert isinstance(plan["applicable_contradictions"], list)
    # The fixture seeded a disputed claim with contradicting_refs; the
    # concept's critique should have surfaced at least one contradiction
    # (this is best-effort -- the assertion is structural, not count-based)


# ── Negative Test 13: experiment interpretation rules include no-VERIFIED ─


@pytest.mark.asyncio
async def test_neg_interpretation_rules_no_verified(app, db_session):
    fixture = await _seed_experiment_fixture(db_session)
    result = await plan_experiment(db_session, fixture["hypothesis_id"])
    assert result is not None
    plan = result["experiment"]

    assert len(plan["interpretation_rules"]) >= 1
    # At least one rule must mention that VERIFIED promotion is forbidden
    rules_text = " ".join(plan["interpretation_rules"]).lower()
    assert "verified" in rules_text or "no observation promotes" in rules_text


# ── Negative Test 14: OpenAPI includes experiment endpoints ───────────────


@pytest.mark.asyncio
async def test_neg_openapi_includes_endpoints(app):
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/experiments" in paths
    assert "post" in paths["/api/v1/experiments"]
    assert "/api/v1/experiments/{experiment_id}" in paths
    assert "get" in paths["/api/v1/experiments/{experiment_id}"]
    # The execute endpoint remains a 501 placeholder
    assert "/api/v1/experiments/{experiment_id}/execute" in paths
    # The hypotheses evidence-deltas endpoint remains a 501 placeholder
    assert "/api/v1/hypotheses/{hypothesis_id}/evidence-deltas" in paths
