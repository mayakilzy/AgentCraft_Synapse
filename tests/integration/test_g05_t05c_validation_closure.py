"""G05-T05C -- Experiment Planning Validation & Identity Closure.

Focused closure tests for G05-T05 per the T05C mission:

  Task 3 -- Experiment fingerprint correctness:
    - Every input that materially changes the resulting plan participates
      in deterministic identity: hypothesis_id, protocol, baseline, metric
      definitions and values, execution_mode, safety_limits, cost_limits.
    - Normalize equivalent inputs consistently.
    - Test that materially different requests do not reuse an incorrect
      experiment.

  Task 4 -- Measurable criteria integrity:
    - Distinguish fully measurable criteria with valid thresholds
      (``calibration_status="operational"``) from criteria requiring
      calibration (``"requires_calibration"``) from untestable assumptions
      (``"untestable"``).
    - A null threshold must never be presented as a fully operational
      pass/fail rule.
    - Do not fabricate thresholds or results.

  Task 5 -- Regression and scope:
    - Frozen Experiment contract preserved.
    - Multiple experiments per hypothesis preserved.
    - No observation or evidence delta during planning.
    - No hypothesis-state mutation.
    - No automatic VERIFIED promotion.
    - Existing API compatibility.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.experiment_planner import (
    _compute_experiment_fingerprint,
    _normalize_dict,
    _normalize_protocol,
    _normalize_text,
    plan_experiment,
)
from synapse.application.innovation import generate_innovations
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


# ── Fixture helpers (mirror T05 patterns) ──────────────────────────────────


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


async def _seed_t05c_fixture(session) -> dict[str, Any]:
    """Seed a fixture and generate an innovation concept + hypothesis."""
    src_a = await _make_source(session, "src-t05c-a", "https://example.com/arxiv-t05c")
    src_b = await _make_source(session, "src-t05c-b", "https://example.com/trafilatura-t05c")
    src_c = await _make_source(session, "src-t05c-c", "https://example.com/synapse-t05c")

    frag_a = await _make_fragment(session, "frag-t05c-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-t05c-b", src_b, "trafilatura extracts content.")
    frag_c = await _make_fragment(
        session, "frag-t05c-c", src_c, "synapse provides evidence verification."
    )

    await _make_entity(session, "ent-t05c-arxiv", "technology", "arxiv", desc="arXiv")
    await _make_entity(session, "ent-t05c-trafilatura", "technology", "trafilatura", desc="trafilatura")
    await _make_entity(session, "ent-t05c-synapse", "tool", "synapse", desc="Synapse")

    for cap_id, name in [
        ("cap-t05c-source-discovery", "source discovery"),
        ("cap-t05c-content-extraction", "content extraction"),
        ("cap-t05c-evidence-verification", "evidence verification"),
    ]:
        await _make_entity(session, cap_id, "capability", name)

    await _make_claim(
        session,
        "claim-t05c-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-t05c-arxiv",
        object_ref="cap-t05c-source-discovery",
        evidence_refs=["frag-t05c-a"],
    )
    await _make_claim(
        session,
        "claim-t05c-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-t05c-trafilatura",
        object_ref="cap-t05c-content-extraction",
        evidence_refs=["frag-t05c-b"],
    )
    await _make_claim(
        session,
        "claim-t05c-ev",
        "synapse provides evidence verification.",
        subject_ref="ent-t05c-synapse",
        object_ref="cap-t05c-evidence-verification",
        evidence_refs=["frag-t05c-c"],
    )

    for from_id, to_id, ev_refs in [
        ("ent-t05c-arxiv", "cap-t05c-source-discovery", ["frag-t05c-a"]),
        ("ent-t05c-trafilatura", "cap-t05c-content-extraction", ["frag-t05c-b"]),
        ("ent-t05c-synapse", "cap-t05c-evidence-verification", ["frag-t05c-c"]),
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

    await session.commit()
    for cid in ("claim-t05c-sd", "claim-t05c-ce", "claim-t05c-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

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


# ── Task 3: Fingerprint correctness ───────────────────────────────────────


class TestFingerprintCorrectness:
    """Verify every material input participates in deterministic identity."""

    def test_fingerprint_includes_hypothesis_id(self):
        """Different hypothesis_id → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-A",
            protocol="test protocol",
            baseline="baseline",
            metrics={"precision": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-B",
            protocol="test protocol",
            baseline="baseline",
            metrics={"precision": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_protocol(self):
        """Different protocol → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="protocol A",
            baseline="baseline",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="protocol B",
            baseline="baseline",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_baseline(self):
        """Different baseline → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test protocol",
            baseline="baseline A",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test protocol",
            baseline="baseline B",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_metric_keys(self):
        """Different metric keys → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={"precision": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={"recall": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_metric_values(self):
        """Different metric dtype → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={"precision": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={"precision": "int"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_execution_mode(self):
        """Different execution_mode → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="sandbox",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_safety_limits(self):
        """Different safety_limits → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="production",
            safety_limits={"approved_by": "alice"},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="production",
            safety_limits={"approved_by": "bob"},
            cost_limits={},
        )
        assert fp1 != fp2

    def test_fingerprint_includes_cost_limits(self):
        """Different cost_limits → different fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="networked",
            safety_limits={},
            cost_limits={"max_api_calls": 10},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="test",
            baseline="b",
            metrics={},
            execution_mode="networked",
            safety_limits={},
            cost_limits={"max_api_calls": 20},
        )
        assert fp1 != fp2

    def test_fingerprint_normalizes_equivalent_protocol_whitespace(self):
        """Equivalent protocol text (different whitespace) → same fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="Run   the    test  protocol.",
            baseline="b",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="run the test protocol.",
            baseline="b",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 == fp2

    def test_fingerprint_normalizes_equivalent_baseline_whitespace(self):
        """Equivalent baseline text (different whitespace/case) → same fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="Random   Sampling  of 50 papers",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="random sampling of 50 papers",
            metrics={},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 == fp2

    def test_fingerprint_normalizes_equivalent_dict_key_order(self):
        """Equivalent dict (different key insertion order) → same fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="b",
            metrics={},
            execution_mode="dry_run",
            safety_limits={"approved_by": "alice", "max_runtime_s": 60},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="b",
            metrics={},
            execution_mode="dry_run",
            safety_limits={"max_runtime_s": 60, "approved_by": "alice"},
            cost_limits={},
        )
        assert fp1 == fp2

    def test_fingerprint_normalizes_equivalent_metric_key_order(self):
        """Equivalent metrics (different key insertion order) → same fingerprint."""
        fp1 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="b",
            metrics={"precision": "float", "recall": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        fp2 = _compute_experiment_fingerprint(
            hypothesis_id="hyp-X",
            protocol="p",
            baseline="b",
            metrics={"recall": "float", "precision": "float"},
            execution_mode="dry_run",
            safety_limits={},
            cost_limits={},
        )
        assert fp1 == fp2


class TestFingerprintNoFalseReuse:
    """Materially different requests must NOT reuse an incorrect experiment."""

    @pytest.mark.asyncio
    async def test_different_baseline_creates_new_experiment(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        r1 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Run retrieval precision test.",
            baseline="Random sampling baseline A.",
            metrics={"precision_at_10": "float"},
        )
        r2 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Run retrieval precision test.",
            baseline="Random sampling baseline B.",
            metrics={"precision_at_10": "float"},
        )
        assert r1 is not None and r2 is not None
        assert r1["experiment"]["experiment_id"] != r2["experiment"]["experiment_id"]
        assert r1["experiment"]["was_reused"] is False
        assert r2["experiment"]["was_reused"] is False

    @pytest.mark.asyncio
    async def test_different_safety_limits_creates_new_experiment(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        r1 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Production test.",
            execution_mode="production",
            safety_limits={"approved_by": "alice"},
        )
        r2 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Production test.",
            execution_mode="production",
            safety_limits={"approved_by": "bob"},
        )
        assert r1 is not None and r2 is not None
        assert r1["experiment"]["experiment_id"] != r2["experiment"]["experiment_id"]

    @pytest.mark.asyncio
    async def test_different_metric_dtype_creates_new_experiment(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        r1 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Latency test.",
            metrics={"p95_latency_ms": "int"},
        )
        r2 = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Latency test.",
            metrics={"p95_latency_ms": "float"},
        )
        assert r1 is not None and r2 is not None
        assert r1["experiment"]["experiment_id"] != r2["experiment"]["experiment_id"]


# ── Task 4: Measurable criteria integrity ──────────────────────────────────


class TestMeasurableCriteriaIntegrity:
    """Verify calibration_status distinguishes operational / calibration /
    untestable criteria, and that null thresholds are never presented as
    operational."""

    @pytest.mark.asyncio
    async def test_metric_criteria_are_requires_calibration(self, app, db_session):
        """Criteria with user-supplied metrics have null thresholds and are
        labeled requires_calibration -- NOT operational."""
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            metrics={"precision_at_10": "float"},
        )
        assert result is not None
        plan = result["experiment"]
        # The success criteria tied to metrics must be requires_calibration
        metric_criteria = [
            c for c in plan["success_criteria"] if c["metric"] == "precision_at_10"
        ]
        assert len(metric_criteria) >= 1
        for c in metric_criteria:
            assert c["threshold"] is None
            assert c["calibration_status"] == "requires_calibration"

    @pytest.mark.asyncio
    async def test_no_null_threshold_is_operational(self, app, db_session):
        """A null threshold must NEVER be presented as operational."""
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            metrics={"precision_at_10": "float"},
        )
        assert result is not None
        plan = result["experiment"]
        for c in plan["success_criteria"] + plan["failure_criteria"]:
            if c["threshold"] is None:
                assert c["calibration_status"] in ("requires_calibration", "untestable"), (
                    f"Criterion {c['metric']} has null threshold but status="
                    f"{c['calibration_status']} -- null thresholds must NOT be operational"
                )

    @pytest.mark.asyncio
    async def test_operational_criteria_have_non_null_threshold(self, app, db_session):
        """Operational criteria must have a non-null threshold."""
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None
        plan = result["experiment"]
        for c in plan["success_criteria"] + plan["failure_criteria"]:
            if c["calibration_status"] == "operational":
                assert c["threshold"] is not None, (
                    f"Criterion {c['metric']} is operational but threshold is null"
                )

    @pytest.mark.asyncio
    async def test_no_fabricated_thresholds(self, app, db_session):
        """The planner never fabricates a numeric threshold value.

        Thresholds are either:
        - None (requires_calibration / untestable)
        - True (operational boolean -- for qualitative criteria)
        - "unspecified" string only when dtype is also "unspecified"
        """
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None
        plan = result["experiment"]
        for c in plan["success_criteria"] + plan["failure_criteria"]:
            t = c["threshold"]
            # Allowed threshold values: None, True, or the literal string "unspecified"
            # (only when dtype=="unspecified"). Numbers (int/float) are NEVER fabricated.
            assert t is None or t is True or (
                isinstance(t, str) and t == "unspecified" and c["dtype"] == "unspecified"
            ), f"Criterion {c['metric']} has fabricated threshold: {t!r} (type {type(t).__name__})"

    @pytest.mark.asyncio
    async def test_calibration_status_field_always_present(self, app, db_session):
        """Every criterion has a calibration_status field."""
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None
        plan = result["experiment"]
        for c in plan["success_criteria"] + plan["failure_criteria"]:
            assert "calibration_status" in c
            assert c["calibration_status"] in ("operational", "requires_calibration", "untestable")

    @pytest.mark.asyncio
    async def test_untestable_assumptions_are_separate_field(self, app, db_session):
        """Untestable assumptions are reported in the untestable_assumptions[]
        field, NOT coerced into a metric criterion."""
        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None
        plan = result["experiment"]
        # The field exists and is a list
        assert "untestable_assumptions" in plan
        assert isinstance(plan["untestable_assumptions"], list)
        # If any criterion is "untestable", there must be a corresponding entry
        # in untestable_assumptions explaining why
        untestable_criteria = [
            c for c in plan["success_criteria"] if c["calibration_status"] == "untestable"
        ]
        if untestable_criteria:
            assert len(plan["untestable_assumptions"]) >= 1


# ── Task 5: Regression and scope ───────────────────────────────────────────


class TestRegressionScope:
    """Verify the frozen contracts and invariants are preserved."""

    @pytest.mark.asyncio
    async def test_frozen_experiment_contract_preserved(self, app, db_session):
        """The Experiment domain record is unchanged -- production mode still
        requires safety_limits.approved_by."""
        fixture = await _seed_t05c_fixture(db_session)
        with pytest.raises(ValueError, match="approved_by"):
            await plan_experiment(
                db_session,
                fixture["hypothesis_id"],
                execution_mode="production",
                safety_limits={},
            )

    @pytest.mark.asyncio
    async def test_multiple_experiments_per_hypothesis_preserved(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        r_a = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Protocol A.",
            metrics={"precision": "float"},
        )
        r_b = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            protocol="Protocol B.",
            metrics={"recall": "float"},
        )
        assert r_a is not None and r_b is not None
        assert r_a["experiment"]["experiment_id"] != r_b["experiment"]["experiment_id"]
        assert r_a["experiment"]["hypothesis_id"] == r_b["experiment"]["hypothesis_id"]

    @pytest.mark.asyncio
    async def test_no_observation_or_evidence_delta(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        before = len(
            (
                await db_session.execute(
                    select(AuditEventRow).where(AuditEventRow.event_type == "evidence.delta")
                )
            ).scalars().all()
        )
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None
        after = len(
            (
                await db_session.execute(
                    select(AuditEventRow).where(AuditEventRow.event_type == "evidence.delta")
                )
            ).scalars().all()
        )
        assert after == before
        assert result["experiment"]["result"] is None
        assert result["experiment"]["artifact_refs"] == []

    @pytest.mark.asyncio
    async def test_no_hypothesis_state_mutation(self, app, db_session):
        fixture = await _seed_t05c_fixture(db_session)
        stmt = select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
        hyp_before = (await db_session.execute(stmt)).scalar_one()
        state_before = hyp_before.epistemic_state
        confidence_before = hyp_before.confidence_value
        version_before = hyp_before.version

        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None

        await db_session.refresh(hyp_before)
        assert hyp_before.epistemic_state == state_before
        assert hyp_before.confidence_value == confidence_before
        assert hyp_before.version == version_before

    @pytest.mark.asyncio
    async def test_no_verified_promotion(self, app, db_session):
        from synapse.domain._base import EpistemicState

        allowed = {e.value for e in EpistemicState}
        assert "verified" not in allowed

        fixture = await _seed_t05c_fixture(db_session)
        result = await plan_experiment(db_session, fixture["hypothesis_id"])
        assert result is not None

        stmt = select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
        hyp = (await db_session.execute(stmt)).scalar_one()
        assert hyp.epistemic_state in allowed

    @pytest.mark.asyncio
    async def test_api_compatibility(self, app, client, db_session, auth_headers_reader):
        """POST + GET endpoints remain functional and honor auth/envelope.

        Note: the API endpoint uses a separate session per request (via the
        conftest dependency override). To verify GET retrieves a persisted
        experiment, we first plan one through ``plan_experiment()`` directly
        (db_session) and commit, so the API GET can see it. We then also
        verify POST creates a NEW experiment via the API and returns 200.
        """
        fixture = await _seed_t05c_fixture(db_session)

        # Plan one experiment directly + commit, so GET can find it.
        result = await plan_experiment(
            db_session,
            fixture["hypothesis_id"],
            metrics={"precision_at_10": "float"},
        )
        assert result is not None
        exp_id = result["experiment"]["experiment_id"]
        await db_session.commit()

        # GET without auth → 401
        r = client.get(f"/api/v1/experiments/{exp_id}")
        assert r.status_code == 401

        # GET with auth → 200, returns the persisted plan
        r = client.get(
            f"/api/v1/experiments/{exp_id}",
            headers=auth_headers_reader,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["error"] is None
        assert body["data"]["experiment_id"] == exp_id
        assert body["data"]["hypothesis_id"] == fixture["hypothesis_id"]

        # GET missing → 404
        r = client.get(
            "/api/v1/experiments/exp-does-not-exist",
            headers=auth_headers_reader,
        )
        assert r.status_code == 404

        # POST without auth → 401
        r = client.post("/api/v1/experiments", json={"hypothesis_id": fixture["hypothesis_id"]})
        assert r.status_code == 401

        # POST with auth → 200 (creates a new experiment via the API)
        r = client.post(
            "/api/v1/experiments",
            headers=auth_headers_reader,
            json={
                "hypothesis_id": fixture["hypothesis_id"],
                "metrics": {"recall_at_10": "float"},  # different metric → new fingerprint
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["error"] is None
        assert "experiment" in body["data"]
        new_exp_id = body["data"]["experiment"]["experiment_id"]
        assert new_exp_id != exp_id  # different fingerprint → different ID


# ── Normalization unit tests (pure functions) ──────────────────────────────


class TestNormalization:
    def test_normalize_protocol_collapses_whitespace(self):
        assert _normalize_protocol("  Hello   World  ") == "hello world"

    def test_normalize_protocol_lowercases(self):
        assert _normalize_protocol("HELLO World") == "hello world"

    def test_normalize_text_collapses_whitespace(self):
        assert _normalize_text("  Baseline   Text  ") == "baseline text"

    def test_normalize_dict_sorts_by_key(self):
        assert _normalize_dict({"b": 2, "a": 1}) == _normalize_dict({"a": 1, "b": 2})

    def test_normalize_dict_drops_none_values(self):
        result = _normalize_dict({"a": 1, "b": None})
        assert "b=" not in result
        assert "a=1" in result

    def test_normalize_dict_empty_returns_empty_string(self):
        assert _normalize_dict({}) == ""
        assert _normalize_dict(None) == ""

    def test_normalize_dict_different_values_different_output(self):
        assert _normalize_dict({"a": 1}) != _normalize_dict({"a": 2})
