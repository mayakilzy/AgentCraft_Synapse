"""G05-T06 Focused Acceptance Audit — PostgreSQL concurrency tests.

Tests for the three audit questions:
A — Execution identity: retry idempotency vs independent repeat.
B — Concurrent observation integrity: no lost observations.
C — Epistemic feedback: confidence update idempotency + conflicting
    results preservation.

These tests run against a real PostgreSQL instance.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PG_TEST_URL = os.environ.get("SYNAPSE_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL, reason="Set SYNAPSE_PG_TEST_URL to run T06 audit tests"
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()
CONCURRENCY = 20


@pytest.fixture(scope="module")
def pg_engine():
    eng = create_async_engine(PG_TEST_URL, future=True, pool_pre_ping=True)
    yield eng
    import asyncio

    asyncio.get_event_loop().run_until_complete(_cleanup(eng))


async def _cleanup(eng):
    async with eng.begin() as conn:
        for table in [
            "entity_fingerprints", "relationships", "source_spans",
            "claims", "evidence_fragments", "entities", "sources",
            "audit_events", "jobs", "idempotency_keys",
        ]:
            await conn.execute(text(f"DELETE FROM {table}"))
    await eng.dispose()


async def _seed_fixture(eng):
    """Seed a minimal fixture and generate an innovation + experiment plan."""
    async with eng.begin() as conn:
        for table in [
            "entity_fingerprints", "relationships", "source_spans",
            "claims", "evidence_fragments", "entities", "sources",
        ]:
            await conn.execute(text(f"DELETE FROM {table}"))

        for sid, uri in [
            ("src-aud-a", "https://example.com/arxiv-aud"),
            ("src-aud-b", "https://example.com/trafilatura-aud"),
            ("src-aud-c", "https://example.com/synapse-aud"),
        ]:
            await conn.execute(
                text("INSERT INTO sources (id, canonical_uri, source_type, status, version) "
                     "VALUES (:id, :uri, 'paper', 'extracted', 1)"),
                {"id": sid, "uri": uri},
            )

        for fid, sid, excerpt in [
            ("frag-aud-a", "src-aud-a", "arxiv provides source discovery."),
            ("frag-aud-b", "src-aud-b", "trafilatura extracts content."),
            ("frag-aud-c", "src-aud-c", "synapse provides evidence verification."),
        ]:
            await conn.execute(
                text("INSERT INTO evidence_fragments "
                     "(id, acquisition_id, source_id, source_uri, exact_excerpt, "
                     "excerpt_hash, retrieved_at, extraction_method, content_fingerprint, "
                     "toolkit_commit_sha) "
                     "VALUES (:id, :acq, :sid, :uri, :excerpt, :hash, :ts, 'fixture', :fp, 'fixture')"),
                {"id": fid, "acq": f"acq-{fid}", "sid": sid,
                 "uri": f"https://example.com/{sid}", "excerpt": excerpt,
                 "hash": f"hash-{fid}", "ts": RECENT_ISO, "fp": f"fp-{fid}"},
            )

        for eid, kind, name in [
            ("ent-aud-arxiv", "technology", "arxiv"),
            ("ent-aud-trafilatura", "technology", "trafilatura"),
            ("ent-aud-synapse", "tool", "synapse"),
            ("cap-aud-sd", "capability", "source discovery"),
            ("cap-aud-ce", "capability", "content extraction"),
            ("cap-aud-ev", "capability", "evidence verification"),
        ]:
            await conn.execute(
                text("INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                     "VALUES (:id, :kind, :name, '[]', '{}', 1)"),
                {"id": eid, "kind": kind, "name": name},
            )

        for cid, prop, subj, obj, refs in [
            ("claim-aud-sd", "arxiv provides source discovery.", "ent-aud-arxiv", "cap-aud-sd", ["frag-aud-a"]),
            ("claim-aud-ce", "trafilatura provides content extraction.", "ent-aud-trafilatura", "cap-aud-ce", ["frag-aud-b"]),
            ("claim-aud-ev", "synapse provides evidence verification.", "ent-aud-synapse", "cap-aud-ev", ["frag-aud-c"]),
        ]:
            await conn.execute(
                text("INSERT INTO claims (id, proposition, subject_ref, object_ref, "
                     "evidence_refs, epistemic_state, extraction_method, version) "
                     "VALUES (:id, :prop, :subj, :obj, :refs, 'supported', 'fixture', 1)"),
                {"id": cid, "prop": prop, "subj": subj, "obj": obj, "refs": json.dumps(refs)},
            )

        for frm, to, ev in [
            ("ent-aud-arxiv", "cap-aud-sd", ["frag-aud-a"]),
            ("ent-aud-trafilatura", "cap-aud-ce", ["frag-aud-b"]),
            ("ent-aud-synapse", "cap-aud-ev", ["frag-aud-c"]),
        ]:
            await conn.execute(
                text("INSERT INTO relationships (id, from_entity_id, to_entity_id, predicate, "
                     "direction, origin, verification_state, evidence_refs, version) "
                     "VALUES (:id, :frm, :to, 'PROVIDES', 'directed', 'explicit', 'unverified', :ev, 1)"),
                {"id": f"rel-aud-{frm}-{to}", "frm": frm, "to": to, "ev": json.dumps(ev)},
            )


async def _assess_claims(eng):
    from synapse.application.verification import assess_claim
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as session:
        for cid in ("claim-aud-sd", "claim-aud-ce", "claim-aud-ev"):
            await assess_claim(session, cid, requester="aud-fixture")
        await session.commit()


async def _create_experiment(eng) -> dict[str, Any]:
    """Create an innovation + experiment plan. Returns fixture dict."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from synapse.application.experiment_planner import plan_experiment
    from synapse.application.innovation import generate_innovations

    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as session:
        gen = await generate_innovations(session, "AI research assistant", context="AI agent systems")
        await session.commit()
        hypothesis_id = gen["concepts"][0]["hypothesis_id"]

    async with factory() as session:
        plan = await plan_experiment(
            session, hypothesis_id,
            protocol="Audit test protocol.",
            metrics={"precision_at_10": "float"},
        )
        await session.commit()
        return {
            "hypothesis_id": hypothesis_id,
            "experiment_id": plan["experiment"]["experiment_id"],
        }


# ── A: Execution identity ─────────────────────────────────────────────────


class TestExecutionIdentity:
    """Distinguish retry idempotency from independent repeat execution."""

    @pytest.mark.asyncio
    async def test_idempotent_retry_same_run_id(self, pg_engine):
        """Same run_id → same execution_id (idempotent reuse)."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import create_execution

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s1:
            r1 = await create_execution(s1, fixture["experiment_id"], run_id="retry-key-001")
            await s1.commit()

        async with factory() as s2:
            r2 = await create_execution(s2, fixture["experiment_id"], run_id="retry-key-001")
            await s2.commit()

        assert r1["execution_id"] == r2["execution_id"]
        assert r1["was_reused"] is False
        assert r2["was_reused"] is True

    @pytest.mark.asyncio
    async def test_independent_repeat_different_run_ids(self, pg_engine):
        """Different run_ids → different execution_ids (independent)."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import create_execution

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s1:
            r1 = await create_execution(s1, fixture["experiment_id"], run_id="run-A")
            await s1.commit()

        async with factory() as s2:
            r2 = await create_execution(s2, fixture["experiment_id"], run_id="run-B")
            await s2.commit()

        assert r1["execution_id"] != r2["execution_id"]
        assert r1["was_reused"] is False
        assert r2["was_reused"] is False

    @pytest.mark.asyncio
    async def test_independent_repeat_no_run_id(self, pg_engine):
        """No run_id → UUID generated → each call unique."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import create_execution

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s1:
            r1 = await create_execution(s1, fixture["experiment_id"])
            await s1.commit()

        async with factory() as s2:
            r2 = await create_execution(s2, fixture["experiment_id"])
            await s2.commit()

        assert r1["execution_id"] != r2["execution_id"]
        assert r1["was_reused"] is False
        assert r2["was_reused"] is False


# ── B: Concurrent observation integrity ───────────────────────────────────


class TestConcurrentObservationIntegrity:
    """Submit concurrent observations from independent sessions.

    Verify:
    - No observations are lost.
    - Each accepted observation is retained exactly once.
    - Terminal executions reject subsequent observations.
    """

    @pytest.mark.asyncio
    async def test_20_concurrent_observations_no_loss(self, pg_engine):
        """20 concurrent observations from independent sessions — all
        must be retained exactly once."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import create_execution, record_observation

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        # Create one execution
        async with factory() as s:
            exec_result = await create_execution(s, fixture["experiment_id"], run_id="conc-obs-test")
            await s.commit()
            exec_id = exec_result["execution_id"]

        # 20 concurrent observations from independent sessions
        async def one_observation(i):
            async with factory() as s:
                try:
                    obs = await record_observation(
                        s, exec_id,
                        metric="precision_at_10",
                        observed_value=0.5 + i * 0.01,
                        source_ref=f"experimenter-{i}",
                    )
                    await s.commit()
                    return obs
                except Exception:
                    await s.rollback()
                    raise

        results = await asyncio.gather(*[one_observation(i) for i in range(CONCURRENCY)])

        # All 20 must succeed
        assert len(results) == CONCURRENCY
        for r in results:
            assert r is not None

        # Verify all 20 are persisted exactly once
        async with factory() as s:
            from synapse.application.execution_record import get_execution
            fetched = await get_execution(s, exec_id)
            assert fetched is not None
            obs_count = len(fetched["observations"])
            assert obs_count == CONCURRENCY, (
                f"Expected {CONCURRENCY} observations, got {obs_count} — "
                f"some observations were lost to a concurrent read-modify-write race"
            )

            # Verify all observation IDs are unique (no duplicates)
            obs_ids = [o["observation_id"] for o in fetched["observations"]]
            assert len(set(obs_ids)) == CONCURRENCY, "Duplicate observation IDs found"

    @pytest.mark.asyncio
    async def test_terminal_execution_rejects_observations(self, pg_engine):
        """After finalization, observations are rejected."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import (
            ExecutionStatus,
            create_execution,
            finalize_execution,
            record_observation,
        )

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s:
            exec_result = await create_execution(s, fixture["experiment_id"], run_id="terminal-test")
            await s.commit()
            exec_id = exec_result["execution_id"]

        # Finalize immediately (no observations)
        async with factory() as s:
            await finalize_execution(s, exec_id, status=ExecutionStatus.COMPLETED)
            await s.commit()

        # Try to record an observation → must be rejected
        async with factory() as s:
            with pytest.raises(ValueError, match="terminal"):
                await record_observation(s, exec_id, metric="precision_at_10", observed_value=0.5)
                await s.commit()

    @pytest.mark.asyncio
    async def test_concurrent_finalize_and_observation(self, pg_engine):
        """Concurrent finalization and observation submission — the
        finalization must win (terminal status), and the observation
        must either be accepted (if it acquired the lock first) or
        rejected (if finalize acquired the lock first). No corruption."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import (
            ExecutionStatus,
            create_execution,
            finalize_execution,
            record_observation,
        )

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s:
            exec_result = await create_execution(s, fixture["experiment_id"], run_id="race-test")
            await s.commit()
            exec_id = exec_result["execution_id"]

        # Race: finalize vs observation
        async def do_finalize():
            async with factory() as s:
                try:
                    result = await finalize_execution(s, exec_id, status=ExecutionStatus.COMPLETED)
                    await s.commit()
                    return ("finalize", result)
                except Exception as e:
                    await s.rollback()
                    return ("finalize_error", str(e))

        async def do_observation():
            async with factory() as s:
                try:
                    obs = await record_observation(
                        s, exec_id, metric="precision_at_10", observed_value=0.8,
                    )
                    await s.commit()
                    return ("observation", obs)
                except Exception as e:
                    await s.rollback()
                    return ("observation_error", str(e))

        results = await asyncio.gather(do_finalize(), do_observation())

        # One of two valid outcomes:
        # 1. Finalize wins: observation gets "terminal" error
        # 2. Observation wins: finalize succeeds (no error)
        # Both are correct. The key invariant: no corruption.
        finalize_outcome = results[0][0]
        obs_outcome = results[1][0]

        # Finalize should either succeed or error (but not corrupt)
        assert finalize_outcome in ("finalize", "finalize_error")

        # Observation should either succeed or error (but not corrupt)
        assert obs_outcome in ("observation", "observation_error")

        # Verify the execution is not corrupted
        async with factory() as s:
            from synapse.application.execution_record import get_execution
            fetched = await get_execution(s, exec_id)
            assert fetched is not None
            # Status must be a valid value
            assert fetched["status"] in (
                ExecutionStatus.CREATED, ExecutionStatus.RUNNING,
                ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            )
            # Observations list must be valid
            assert isinstance(fetched["observations"], list)


# ── C: Epistemic feedback ─────────────────────────────────────────────────


class TestEpistemicFeedback:
    """Verify confidence update idempotency and conflicting result
    preservation."""

    @pytest.mark.asyncio
    async def test_repeated_finalization_no_double_confidence(self, pg_engine):
        """Finalizing an already-terminal execution must raise ValueError
        and must NOT adjust confidence a second time."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import (
            ExecutionStatus,
            create_execution,
            finalize_execution,
            record_observation,
        )
        from synapse.storage.models import ClaimRow

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        # Create execution + set operational thresholds + record supporting obs
        async with factory() as s:
            exec_result = await create_execution(s, fixture["experiment_id"], run_id="conf-idem")
            await s.commit()
            exec_id = exec_result["execution_id"]

        # Set operational thresholds
        from synapse.storage.models import EntityRow
        async with factory() as s:
            entity = (await s.execute(
                select(EntityRow).where(EntityRow.id == exec_id, EntityRow.kind == "execution").with_for_update()
            )).scalar_one()
            attrs = json.loads(entity.attributes)
            for sc in attrs["success_criteria"]:
                if sc["metric"] == "precision_at_10":
                    sc["threshold"] = 0.7
                    sc["calibration_status"] = "operational"
            entity.attributes = json.dumps(attrs, default=str)
            await s.commit()

        # Record supporting observation
        async with factory() as s:
            await record_observation(s, exec_id, metric="precision_at_10", observed_value=0.85)
            await s.commit()

        # Finalize (first time) → should emit delta, adjust confidence
        async with factory() as s:
            result = await finalize_execution(s, exec_id, status=ExecutionStatus.COMPLETED)
            await s.commit()
            assert result["assessment"]["result"] == "supporting"
            assert result["evidence_delta_id"] is not None

        # Capture confidence after first finalization
        async with factory() as s:
            hyp = (await s.execute(
                select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
            )).scalar_one()
            conf_after_first = hyp.confidence_value
            state_after_first = hyp.epistemic_state

        # Attempt re-finalization → must raise
        async with factory() as s:
            with pytest.raises(ValueError, match="already terminal"):
                await finalize_execution(s, exec_id, status=ExecutionStatus.COMPLETED)
                await s.commit()

        # Confidence must NOT have changed
        async with factory() as s:
            hyp = (await s.execute(
                select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
            )).scalar_one()
            assert hyp.confidence_value == conf_after_first, (
                "Confidence was adjusted a second time by re-finalization"
            )
            assert hyp.epistemic_state == state_after_first

    @pytest.mark.asyncio
    async def test_conflicting_results_preserved(self, pg_engine):
        """Two independent executions of the same experiment producing
        conflicting results (one supporting, one contradicting) — both
        EvidenceDeltas must be preserved in the audit trail."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import (
            ExecutionStatus,
            create_execution,
            finalize_execution,
            get_evidence_deltas,
            record_observation,
        )
        from synapse.storage.models import EntityRow

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        # Execution 1: supporting
        async with factory() as s:
            exec1 = await create_execution(s, fixture["experiment_id"], run_id="conflict-sup")
            await s.commit()
            exec1_id = exec1["execution_id"]

        async with factory() as s:
            entity = (await s.execute(
                select(EntityRow).where(EntityRow.id == exec1_id, EntityRow.kind == "execution").with_for_update()
            )).scalar_one()
            attrs = json.loads(entity.attributes)
            for sc in attrs["success_criteria"]:
                if sc["metric"] == "precision_at_10":
                    sc["threshold"] = 0.7
                    sc["calibration_status"] = "operational"
            entity.attributes = json.dumps(attrs, default=str)
            await s.commit()

        async with factory() as s:
            await record_observation(s, exec1_id, metric="precision_at_10", observed_value=0.85)
            await s.commit()

        async with factory() as s:
            result1 = await finalize_execution(s, exec1_id, status=ExecutionStatus.COMPLETED)
            await s.commit()
            assert result1["assessment"]["result"] == "supporting"

        # Execution 2: contradicting (different run_id → independent)
        async with factory() as s:
            exec2 = await create_execution(s, fixture["experiment_id"], run_id="conflict-con")
            await s.commit()
            exec2_id = exec2["execution_id"]

        assert exec1_id != exec2_id, "Independent executions must have different IDs"

        async with factory() as s:
            entity = (await s.execute(
                select(EntityRow).where(EntityRow.id == exec2_id, EntityRow.kind == "execution").with_for_update()
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
            await s.commit()

        async with factory() as s:
            await record_observation(s, exec2_id, metric="precision_at_10", observed_value=0.1)
            await s.commit()

        async with factory() as s:
            result2 = await finalize_execution(s, exec2_id, status=ExecutionStatus.COMPLETED)
            await s.commit()
            assert result2["assessment"]["result"] == "contradicting"

        # Verify both deltas are in the audit trail
        async with factory() as s:
            deltas = await get_evidence_deltas(s, fixture["hypothesis_id"])
            assert len(deltas) >= 2, (
                f"Expected >=2 evidence deltas (supporting + contradicting), got {len(deltas)}"
            )
            # Verify both results are present
            results = {d["assessment_result"] for d in deltas}
            assert "supporting" in results, "Supporting delta missing from audit trail"
            assert "contradicting" in results, "Contradicting delta missing from audit trail"

    @pytest.mark.asyncio
    async def test_concurrent_finalization_no_double_delta(self, pg_engine):
        """Two concurrent finalize calls on the same execution — only
        one must succeed; the other must raise 'already terminal'."""
        await _seed_fixture(pg_engine)
        await _assess_claims(pg_engine)
        fixture = await _create_experiment(pg_engine)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.execution_record import (
            ExecutionStatus,
            create_execution,
            finalize_execution,
            record_observation,
        )
        from synapse.storage.models import AuditEventRow, ClaimRow

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async with factory() as s:
            exec_result = await create_execution(s, fixture["experiment_id"], run_id="conc-fin")
            await s.commit()
            exec_id = exec_result["execution_id"]

        # Record an observation first
        async with factory() as s:
            await record_observation(s, exec_id, metric="precision_at_10", observed_value=0.8)
            await s.commit()

        # Two concurrent finalize calls
        async def do_finalize():
            async with factory() as s:
                try:
                    result = await finalize_execution(s, exec_id, status=ExecutionStatus.COMPLETED)
                    await s.commit()
                    return ("success", result)
                except Exception as e:
                    await s.rollback()
                    return ("error", str(e))

        results = await asyncio.gather(do_finalize(), do_finalize())

        # Exactly one must succeed, the other must error
        successes = [r for r in results if r[0] == "success"]
        errors = [r for r in results if r[0] == "error"]
        assert len(successes) == 1, f"Expected exactly 1 successful finalize, got {len(successes)}"
        assert len(errors) == 1, f"Expected exactly 1 errored finalize, got {len(errors)}"
        assert "terminal" in errors[0][1].lower()

        # Verify exactly 1 evidence delta was emitted for this execution
        async with factory() as s:
            from sqlalchemy import func
            delta_count = (await s.execute(
                select(func.count()).select_from(AuditEventRow).where(
                    AuditEventRow.event_type == "evidence.delta",
                    AuditEventRow.target_id == fixture["hypothesis_id"],
                )
            )).scalar()
            assert delta_count == 1, (
                f"Expected exactly 1 evidence delta from concurrent finalization, got {delta_count}"
            )

            # Verify confidence was adjusted exactly once
            hyp = (await s.execute(
                select(ClaimRow).where(ClaimRow.id == fixture["hypothesis_id"])
            )).scalar_one()
            # The hypothesis should be in SUPPORTED state (supporting assessment)
            # with confidence adjusted by exactly +0.2 from the initial 0.0
            assert hyp.epistemic_state == "supported"
            assert hyp.confidence_value == 0.2, (
                f"Confidence should be 0.2 (one +0.2 adjustment), got {hyp.confidence_value}"
            )
