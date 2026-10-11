"""G07-PRE-02 — Minimal Read API Amendments acceptance tests.

Covers the 5 new GET endpoints:

- ``GET /api/v1/innovations`` — paginated list of innovation concepts.
- ``GET /api/v1/innovations/{innovation_id}`` — read concept detail.
- ``GET /api/v1/experiments`` — paginated list of experiment plans.
- ``GET /api/v1/executions`` — paginated list of execution records
  (with optional ``experiment_id`` filter).
- ``GET /api/v1/future/scenarios`` — paginated list of scenarios.

Per G07-PRE-02 §4, this file tests:

1. List endpoints: empty dataset, single row, multi-page dataset, page
   boundaries, stable order, cursor errors, bounded limits.
2. Innovation detail by ID: success, missing ID, wrong entity kind,
   persistence round-trip.
3. Auth: unauthorized access fails closed (401).
4. No side effects: snapshot DB rows and audit events before/after; assert
   no new writes.
5. Discriminator filtering: unrelated project entities must not appear as
   innovations.
6. Regression: existing G05/G06 routes still work (POST /innovations/generate,
   POST /innovations/{id}/critique, POST /experiments, POST /experiments/{id}/execute,
   POST /executions/{id}/observations, POST /executions/{id}/finalize,
   POST /future/scenarios, GET /future/scenarios/{id}, etc.).

All tests use the existing ``app`` + ``db_session`` + ``client`` fixtures
from ``tests/conftest.py`` and follow the same fixture patterns as the
G05/G06 tests (see ``tests/integration/test_g05_t03_innovation_generation.py``
and ``tests/integration/test_g06_t02_scenario_construction.py``).

The G07-PRE-02 cursor is opaque base64url(JSON{ts, id}). The tests
do not decode the cursor themselves — they treat it as a string and
pass it back as ``?cursor=...``. The cursor validation tests use
malformed strings to confirm 422.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from synapse.application.execution_record import create_execution
from synapse.application.experiment_planner import plan_experiment
from synapse.application.innovation import generate_innovations
from synapse.application.relationship_service import create_relationship
from synapse.application.scenario_builder import build_scenario
from synapse.application.verification import assess_claim
from synapse.domain.experiment import ExperimentExecutionMode
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityFingerprintRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

# ── Test fixture (mirrors G05-T03 + G06-T02) ────────────────────────────────

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()


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


async def _seed_innovation_fixture(session) -> dict[str, Any]:
    """Seed the same fixture used by G05-T03 tests (two capabilities + 4 claims).

    Returns a dict with ``capability_ids`` for scenario-building tests and
    ``fragment_id`` for evidence refs.
    """
    src_a = await _make_source(session, "src-pre02-a", "https://example.com/papers/arxiv")
    src_b = await _make_source(session, "src-pre02-b", "https://example.com/papers/trafilatura")
    frag_a = await _make_fragment(session, "frag-pre02-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-pre02-b", src_b, "trafilatura extracts content.")

    await _make_entity(session, "ent-pre02-arxiv", "technology", "arxiv", desc="arXiv paper repository")
    await _make_entity(
        session, "ent-pre02-trafilatura", "technology", "trafilatura", desc="Web content extraction"
    )

    for cap_id, name in [
        ("cap-pre02-source-discovery", "source discovery"),
        ("cap-pre02-content-extraction", "content extraction"),
    ]:
        await _make_entity(session, cap_id, "capability", name, desc=f"Capability {name}")

    await _make_claim(
        session,
        "claim-pre02-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-pre02-arxiv",
        object_ref="cap-pre02-source-discovery",
        evidence_refs=["frag-pre02-a"],
    )
    await _make_claim(
        session,
        "claim-pre02-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-pre02-trafilatura",
        object_ref="cap-pre02-content-extraction",
        evidence_refs=["frag-pre02-b"],
    )

    r1 = await create_relationship(
        session,
        from_entity_id="ent-pre02-arxiv",
        to_entity_id="cap-pre02-source-discovery",
        predicate="PROVIDES",
        evidence_refs=["frag-pre02-a"],
        origin="explicit",
    )
    assert r1["ok"] is True
    r2 = await create_relationship(
        session,
        from_entity_id="ent-pre02-trafilatura",
        to_entity_id="cap-pre02-content-extraction",
        predicate="PROVIDES",
        evidence_refs=["frag-pre02-b"],
        origin="explicit",
    )
    assert r2["ok"] is True

    await session.commit()
    await assess_claim(session, "claim-pre02-sd", requester="pre02-fixture")
    await assess_claim(session, "claim-pre02-ce", requester="pre02-fixture")
    await session.commit()

    return {
        "capability_ids": ["cap-pre02-source-discovery", "cap-pre02-content-extraction"],
        "fragment_id": "frag-pre02-a",
    }


async def _seed_scenario_f0_fixture(session) -> dict[str, Any]:
    """Seed an F0 KB (two capabilities with an evidenced INTEGRATES_WITH edge)
    so ``build_scenario`` can persist a real scenario for list tests.
    """
    src = await _make_source(session, "src-pre02-f0", "https://example.com/f0")
    frag = await _make_fragment(session, "frag-pre02-f0", src, "tool-A integrates with tool-B.")
    await _make_entity(session, "ent-pre02-toolA", "technology", "tool-A")
    await _make_entity(session, "ent-pre02-toolB", "technology", "tool-B")
    await _make_entity(session, "cap-pre02-A", "capability", "capability-A")
    await _make_entity(session, "cap-pre02-B", "capability", "capability-B")
    await _make_claim(
        session,
        "claim-pre02-A",
        "tool-A provides capability-A.",
        subject_ref="ent-pre02-toolA",
        object_ref="cap-pre02-A",
        evidence_refs=["frag-pre02-f0"],
    )
    await _make_claim(
        session,
        "claim-pre02-B",
        "tool-B provides capability-B.",
        subject_ref="ent-pre02-toolB",
        object_ref="cap-pre02-B",
        evidence_refs=["frag-pre02-f0"],
    )
    await create_relationship(
        session,
        from_entity_id="ent-pre02-toolA",
        to_entity_id="cap-pre02-A",
        predicate="PROVIDES",
        evidence_refs=["frag-pre02-f0"],
        origin="explicit",
    )
    await create_relationship(
        session,
        from_entity_id="ent-pre02-toolB",
        to_entity_id="cap-pre02-B",
        predicate="PROVIDES",
        evidence_refs=["frag-pre02-f0"],
        origin="explicit",
    )
    await create_relationship(
        session,
        from_entity_id="cap-pre02-A",
        to_entity_id="cap-pre02-B",
        predicate="INTEGRATES_WITH",
        evidence_refs=["frag-pre02-f0"],
        origin="explicit",
    )
    await session.commit()
    await assess_claim(session, "claim-pre02-A", requester="pre02-f0")
    await assess_claim(session, "claim-pre02-B", requester="pre02-f0")
    await session.commit()
    return {
        "capability_ids": ["cap-pre02-A", "cap-pre02-B"],
        "fragment_id": "frag-pre02-f0",
    }


# ── Helper: count AuditEventRows ──────────────────────────────────────────


async def _audit_count(session) -> int:
    """Count all audit_event rows (for no-side-effects assertions)."""
    result = await session.execute(select(func.count()).select_from(AuditEventRow))
    return int(result.scalar_one())


async def _fingerprint_count(session, *, kind: str) -> int:
    """Count EntityFingerprintRow entries of a given kind."""
    result = await session.execute(
        select(func.count())
        .select_from(EntityFingerprintRow)
        .where(EntityFingerprintRow.kind == kind)
    )
    return int(result.scalar_one())


# ══════════════════════════════════════════════════════════════════════════
# Part 1 — Innovation list + detail
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_innovations_list_empty(app, client, auth_headers_admin):
    """GET /api/v1/innovations on an empty KB returns 200 with empty page."""
    response = client.get("/api/v1/innovations", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
    # Empty page must NOT advertise a next cursor.
    assert body["meta"]["pagination"]["next_cursor"] is None
    assert body["meta"]["pagination"]["limit"] == 20


@pytest.mark.asyncio
async def test_innovations_list_after_generation(app, client, db_session, auth_headers_admin):
    """After ``generate_innovations`` persists ≥1 concept, GET /api/v1/innovations
    returns the concept in the list (lightweight row + attributes).
    """
    await _seed_innovation_fixture(db_session)

    # Generate a real concept via the application service (NOT the API — to
    # avoid coupling the list test to the POST /generate route).
    result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    assert len(result["concepts"]) >= 1
    expected_concept_ids = {c["id"] for c in result["concepts"]}

    response = client.get("/api/v1/innovations", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["data"]["count"] >= 1

    # Every concept returned by ``generate_innovations`` must appear in
    # the list (the list is newest-first, but concept ordering depends on
    # the deterministic fingerprints' creation order — multiple concepts
    # may share near-identical created_at timestamps).
    listed_ids = {item["id"] for item in body["data"]["items"]}
    assert expected_concept_ids.issubset(listed_ids)

    # Verify the first item's shape.
    first = body["data"]["items"][0]
    assert first["kind"] == "project"
    assert isinstance(first["attributes"], dict)
    assert "problem_domain" in first["attributes"]
    assert first["attributes"]["problem_domain"] == "AI research assistant"
    assert "concept_fingerprint" in first["attributes"]

    # Items must be ordered (created_at DESC, id DESC) — verify the
    # ordering is stable: every pair (i, i+1) satisfies the order.
    items = body["data"]["items"]
    for i in range(len(items) - 1):
        a, b = items[i], items[i + 1]
        ts_a = a["created_at"]
        ts_b = b["created_at"]
        assert (ts_a > ts_b) or (ts_a == ts_b and a["id"] > b["id"]), (
            f"list is not ordered DESC/DESC: {ts_a}/{a['id']} → {ts_b}/{b['id']}"
        )


@pytest.mark.asyncio
async def test_innovations_list_discriminator_excludes_non_innovations(
    app, client, db_session, auth_headers_admin
):
    """A ``kind='project'`` entity WITHOUT an ``EntityFingerprintRow`` (i.e.,
    not created by ``generate_innovations``) must NOT appear in the list.
    """
    # Create a stray kind='project' entity with no fingerprint row.
    stray = EntityRow(
        id="stray-project-1",
        kind="project",
        canonical_name="stray project (not an innovation)",
        aliases="[]",
        attributes="{}",
        description="should not appear in /innovations list",
        version=1,
    )
    db_session.add(stray)
    await db_session.commit()

    response = client.get("/api/v1/innovations", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    # The stray entity must NOT appear.
    ids = [item["id"] for item in body["data"]["items"]]
    assert "stray-project-1" not in ids


@pytest.mark.asyncio
async def test_innovations_detail_success(app, client, db_session, auth_headers_admin):
    """GET /api/v1/innovations/{id} returns the full concept payload."""
    await _seed_innovation_fixture(db_session)
    result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    concept_id = result["concepts"][0]["id"]

    response = client.get(f"/api/v1/innovations/{concept_id}", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    concept = body["data"]
    assert concept["concept_id"] == concept_id
    assert concept["canonical_name"]  # non-empty
    assert isinstance(concept["attributes"], dict)
    assert "problem_domain" in concept["attributes"]
    # Hypothesis ID is present (the G05 §5.3 stability contract).
    assert concept["hypothesis_id"] is not None
    assert concept["hypothesis_id"].startswith("hyp-")
    # Component links is a list (may be empty if no components were
    # persisted, but the field is present).
    assert isinstance(concept["component_links"], list)


@pytest.mark.asyncio
async def test_innovations_detail_missing_id_returns_404(app, client, auth_headers_admin):
    """GET /api/v1/innovations/{id} on a non-existent ID returns 404."""
    response = client.get("/api/v1/innovations/nonexistent-id-xyz", headers=auth_headers_admin)
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_innovations_detail_wrong_kind_returns_404(
    app, client, db_session, auth_headers_admin
):
    """GET /api/v1/innovations/{id} on a valid entity ID of a DIFFERENT kind
    (e.g., kind='technology') returns 404 — the helper filters by kind='project'.
    """
    other = await _make_entity(
        db_session, "ent-pre02-other", "technology", "some technology"
    )
    await db_session.commit()

    response = client.get(f"/api/v1/innovations/{other.id}", headers=auth_headers_admin)
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_innovations_detail_persistence_round_trip(
    app, client, db_session, auth_headers_admin
):
    """A concept persisted in one session can be retrieved by ID via the
    API in a fresh request (round-trip test)."""
    await _seed_innovation_fixture(db_session)
    result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    concept_id = result["concepts"][0]["id"]
    hypothesis_id = result["concepts"][0]["hypothesis_id"]

    # Fetch via the API (which uses a separate session per request).
    response = client.get(f"/api/v1/innovations/{concept_id}", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["concept_id"] == concept_id
    assert body["data"]["hypothesis_id"] == hypothesis_id


# ══════════════════════════════════════════════════════════════════════════
# Part 2 — Experiments list
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_experiments_list_empty(app, client, auth_headers_admin):
    response = client.get("/api/v1/experiments", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
    assert body["meta"]["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_experiments_list_after_planning(app, client, db_session, auth_headers_admin):
    """After ``plan_experiment`` persists an experiment, GET /api/v1/experiments
    returns it with the full plan payload.
    """
    await _seed_innovation_fixture(db_session)
    innov_result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    hypothesis_id = innov_result["concepts"][0]["hypothesis_id"]

    plan_result = await plan_experiment(
        db_session,
        hypothesis_id,
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    assert plan_result is not None
    expected_experiment_id = plan_result["experiment"]["experiment_id"]

    response = client.get("/api/v1/experiments", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["count"] >= 1
    items = body["data"]["items"]
    # The expected experiment must be in the list.
    by_id = {it["experiment_id"]: it for it in items}
    assert expected_experiment_id in by_id
    first = by_id[expected_experiment_id]
    # Plan payload fields are present.
    assert "objectives" in first
    assert "success_criteria" in first
    assert "failure_criteria" in first
    assert "metrics" in first
    assert first["execution_mode"] == "dry_run"


# ══════════════════════════════════════════════════════════════════════════
# Part 3 — Executions list (with optional experiment_id filter)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_executions_list_empty(app, client, auth_headers_admin):
    response = client.get("/api/v1/executions", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0


@pytest.mark.asyncio
async def test_executions_list_after_create(app, client, db_session, auth_headers_admin):
    """After ``create_execution`` persists an execution, GET /api/v1/executions
    returns it with the full execution payload.
    """
    await _seed_innovation_fixture(db_session)
    innov_result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    hypothesis_id = innov_result["concepts"][0]["hypothesis_id"]

    plan_result = await plan_experiment(
        db_session,
        hypothesis_id,
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    experiment_id = plan_result["experiment"]["experiment_id"]

    exec_result = await create_execution(
        db_session,
        experiment_id,
        run_id="run-pre02-1",
        requester="pre02-tester",
    )
    await db_session.commit()
    assert exec_result is not None
    expected_execution_id = exec_result["execution_id"]

    response = client.get("/api/v1/executions", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["count"] >= 1
    items = body["data"]["items"]
    by_id = {it["execution_id"]: it for it in items}
    assert expected_execution_id in by_id
    first = by_id[expected_execution_id]
    assert first["experiment_id"] == experiment_id
    assert first["status"] == "created"
    assert first["observations"] == []


@pytest.mark.asyncio
async def test_executions_list_filter_by_experiment_id(
    app, client, db_session, auth_headers_admin
):
    """The optional ``?experiment_id=`` filter narrows the list to executions
    of that experiment.
    """
    await _seed_innovation_fixture(db_session)
    innov_result = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    hypothesis_id = innov_result["concepts"][0]["hypothesis_id"]

    # Plan two experiments for the same hypothesis with DIFFERENT
    # protocols so they produce distinct fingerprints (and distinct
    # experiment_ids). plan_experiment is idempotent on the same
    # payload, so we vary the protocol.
    plan_a = await plan_experiment(
        db_session,
        hypothesis_id,
        protocol="Protocol A — measure latency under load.",
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    plan_b = await plan_experiment(
        db_session,
        hypothesis_id,
        protocol="Protocol B — measure throughput at steady state.",
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    exp_a = plan_a["experiment"]["experiment_id"]
    exp_b = plan_b["experiment"]["experiment_id"]
    assert exp_a != exp_b  # two distinct experiment IDs (different protocols)

    # Create one execution for each.
    await create_execution(db_session, exp_a, run_id="run-a-1", requester="pre02-tester")
    await create_execution(db_session, exp_b, run_id="run-b-1", requester="pre02-tester")
    await db_session.commit()

    # Sanity check: GET /api/v1/executions (no filter) returns both.
    r_all = client.get("/api/v1/executions?limit=100", headers=auth_headers_admin)
    assert r_all.status_code == 200
    body_all = r_all.json()
    all_executions_exp_ids = {it["experiment_id"] for it in body_all["data"]["items"]}
    assert exp_a in all_executions_exp_ids
    assert exp_b in all_executions_exp_ids

    # Filter by exp_a → only the execution of exp_a should be returned.
    response = client.get(
        f"/api/v1/executions?experiment_id={exp_a}", headers=auth_headers_admin
    )
    assert response.status_code == 200
    body = response.json()
    items = body["data"]["items"]
    assert len(items) == 1
    assert items[0]["experiment_id"] == exp_a


# ══════════════════════════════════════════════════════════════════════════
# Part 4 — Scenarios list
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scenarios_list_empty(app, client, auth_headers_admin):
    response = client.get("/api/v1/future/scenarios", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
    assert body["meta"]["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_scenarios_list_after_build(app, client, db_session, auth_headers_admin):
    """After ``build_scenario`` persists an F0 scenario, GET /api/v1/future/scenarios
    returns it with the full scenario payload.
    """
    fixture = await _seed_scenario_f0_fixture(db_session)

    result = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        proposed_combination="Combine capability-A with capability-B",
        evidence_refs=[fixture["fragment_id"]],
        requester="pre02-tester",
    )
    await db_session.commit()
    expected_scenario_id = result["scenario_id"]
    assert expected_scenario_id.startswith("scn-")

    response = client.get("/api/v1/future/scenarios", headers=auth_headers_admin)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["count"] >= 1
    items = body["data"]["items"]
    by_id = {it["scenario_id"]: it for it in items}
    assert expected_scenario_id in by_id
    first = by_id[expected_scenario_id]
    assert first["horizon_class"] == "F0"


# ══════════════════════════════════════════════════════════════════════════
# Part 5 — Pagination contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pagination_multi_page_scenarios(app, client, db_session, auth_headers_admin):
    """Build ≥3 scenarios with limit=2 and confirm we get 2 pages with
    correct ``next_cursor`` chaining. The third page is empty.
    """
    fixture = await _seed_scenario_f0_fixture(db_session)
    scenario_ids = []
    for i in range(3):
        # Different ``proposed_combination`` strings force different
        # fingerprints (otherwise build_scenario would reuse the same
        # scenario_id idempotently).
        result = await build_scenario(
            db_session,
            capability_ids=fixture["capability_ids"],
            assumptions=[f"assumption {i}"],
            proposed_combination=f"combination variant {i}",
            evidence_refs=[fixture["fragment_id"]],
            run_id=f"run-pre02-multi-{i}",
            requester="pre02-tester",
        )
        await db_session.commit()
        scenario_ids.append(result["scenario_id"])
    assert len(set(scenario_ids)) == 3  # all distinct

    # Page 1 — limit=2, no cursor.
    r1 = client.get("/api/v1/future/scenarios?limit=2", headers=auth_headers_admin)
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["data"]["count"] == 2
    assert b1["meta"]["pagination"]["limit"] == 2
    next1 = b1["meta"]["pagination"]["next_cursor"]
    assert next1 is not None  # more results exist

    # Page 2 — same limit, cursor=next1.
    r2 = client.get(
        f"/api/v1/future/scenarios?limit=2&cursor={next1}", headers=auth_headers_admin
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["data"]["count"] == 1  # only 1 left of the 3
    next2 = b2["meta"]["pagination"]["next_cursor"]
    assert next2 is None  # no more pages

    # All 3 scenarios should be visible across the two pages, with no
    # duplicates.
    page1_ids = {item["scenario_id"] for item in b1["data"]["items"]}
    page2_ids = {item["scenario_id"] for item in b2["data"]["items"]}
    assert page1_ids.isdisjoint(page2_ids)
    assert (page1_ids | page2_ids) == set(scenario_ids)


@pytest.mark.asyncio
async def test_pagination_stable_order(app, client, db_session, auth_headers_admin):
    """Two consecutive GET /api/v1/innovations calls (no writes between)
    must return the same ordering — confirming stability across equal
    timestamps via ID tie-break.
    """
    await _seed_innovation_fixture(db_session)
    # Generate 3 concepts (will share very close created_at timestamps).
    for _ in range(3):
        # Different ``problem_domain`` strings force different concepts.
        await generate_innovations(
            db_session, f"AI research assistant variant {_}", context="AI"
        )
    await db_session.commit()

    r1 = client.get("/api/v1/innovations?limit=100", headers=auth_headers_admin)
    r2 = client.get("/api/v1/innovations?limit=100", headers=auth_headers_admin)
    assert r1.status_code == 200 and r2.status_code == 200
    ids1 = [item["id"] for item in r1.json()["data"]["items"]]
    ids2 = [item["id"] for item in r2.json()["data"]["items"]]
    assert ids1 == ids2  # stable order


@pytest.mark.asyncio
async def test_pagination_limit_bounds(app, client, auth_headers_admin):
    """``limit`` out of [1, 100] returns 422."""
    # limit=0 → 422 (Pydantic ge=1 is enforced by FastAPI's query param validation,
    # but our helper also re-checks to be safe).
    r_zero = client.get("/api/v1/innovations?limit=0", headers=auth_headers_admin)
    assert r_zero.status_code == 422

    # limit=101 → 422.
    r_big = client.get("/api/v1/innovations?limit=101", headers=auth_headers_admin)
    assert r_big.status_code == 422

    # limit=1 (boundary) → 200.
    r_one = client.get("/api/v1/innovations?limit=1", headers=auth_headers_admin)
    assert r_one.status_code == 200

    # limit=100 (boundary) → 200.
    r_hundred = client.get("/api/v1/innovations?limit=100", headers=auth_headers_admin)
    assert r_hundred.status_code == 200


@pytest.mark.asyncio
async def test_pagination_malformed_cursor_returns_422(app, client, auth_headers_admin):
    """A malformed (non-base64url) cursor returns 422 ``validation_error``."""
    # "not base64url !!!" contains characters not in the base64url alphabet.
    r = client.get(
        "/api/v1/innovations?cursor=not-base64url-!!!",
        headers=auth_headers_admin,
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_pagination_valid_base64url_but_malformed_payload_returns_422(
    app, client, auth_headers_admin
):
    """A cursor that is valid base64url but encodes malformed JSON (or JSON
    without the required keys) returns 422.
    """
    import base64

    # Valid base64url of the string "garbage" — JSON parse will fail.
    payload = b"garbage"
    cursor = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    r = client.get(
        f"/api/v1/innovations?cursor={cursor}",
        headers=auth_headers_admin,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_pagination_stale_cursor_returns_empty_page(
    app, client, db_session, auth_headers_admin
):
    """A cursor that references rows that no longer exist (stale cursor)
    returns a 200 with an empty page (not 422, not 500). The cursor's
    ``(ts, id)`` is well-formed but points to rows past the end of the
    available data — which yields zero results.
    """
    import base64
    import json as _json

    # Construct a valid cursor pointing to a far-future timestamp and a
    # non-existent entity ID. This is "stale" in the sense that no rows
    # exist with created_at <= this cursor.
    payload = _json.dumps({"ts": "1970-01-01T00:00:00+00:00", "id": "never-existed"}).encode()
    cursor = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    r = client.get(
        f"/api/v1/innovations?cursor={cursor}",
        headers=auth_headers_admin,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["items"] == []
    assert body["data"]["count"] == 0
    assert body["meta"]["pagination"]["next_cursor"] is None


# ══════════════════════════════════════════════════════════════════════════
# Part 6 — Auth (fail-closed)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unauthorized_access_returns_401(app, client):
    """Without an ``Authorization`` header, all 5 new endpoints return 401."""
    endpoints = [
        "/api/v1/innovations",
        "/api/v1/innovations/some-id",
        "/api/v1/experiments",
        "/api/v1/executions",
        "/api/v1/future/scenarios",
    ]
    for url in endpoints:
        r = client.get(url)
        assert r.status_code == 401, f"{url} returned {r.status_code}, expected 401"
        body = r.json()
        assert body["error"]["code"] == "unauthenticated"


# ══════════════════════════════════════════════════════════════════════════
# Part 7 — No side effects (read-only contract)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_endpoints_no_audit_writes(app, client, db_session, auth_headers_admin):
    """Calling all 4 list endpoints + the detail endpoint creates NO new
    AuditEventRow entries and NO new EntityFingerprintRow entries.
    """
    # Seed the fixture + persist one concept, one experiment, one
    # execution, one scenario.
    await _seed_innovation_fixture(db_session)
    innov = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    concept_id = innov["concepts"][0]["id"]
    hypothesis_id = innov["concepts"][0]["hypothesis_id"]

    plan = await plan_experiment(
        db_session,
        hypothesis_id,
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    experiment_id = plan["experiment"]["experiment_id"]

    execution = await create_execution(
        db_session,
        experiment_id,
        run_id="run-pre02-side-effect",
        requester="pre02-tester",
    )
    await db_session.commit()
    execution_id = execution["execution_id"]

    fixture = await _seed_scenario_f0_fixture(db_session)
    scenario = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        proposed_combination="combination side-effect-test",
        evidence_refs=[fixture["fragment_id"]],
        run_id="run-pre02-side-effect-scenario",
        requester="pre02-tester",
    )
    await db_session.commit()
    scenario_id = scenario["scenario_id"]

    # Snapshot counts BEFORE the GET calls.
    audit_before = await _audit_count(db_session)
    fp_project_before = await _fingerprint_count(db_session, kind="project")
    fp_experiment_before = await _fingerprint_count(db_session, kind="experiment")
    fp_execution_before = await _fingerprint_count(db_session, kind="execution")
    fp_scenario_before = await _fingerprint_count(db_session, kind="scenario")

    # Hit all 5 new GET endpoints.
    urls = [
        "/api/v1/innovations",
        f"/api/v1/innovations/{concept_id}",
        "/api/v1/experiments",
        "/api/v1/executions",
        f"/api/v1/executions?experiment_id={experiment_id}",
        "/api/v1/future/scenarios",
    ]
    for url in urls:
        r = client.get(url, headers=auth_headers_admin)
        assert r.status_code == 200, f"{url} returned {r.status_code}"

    # Force a fresh session to read the latest committed state.
    # (The TestClient's session commits per-request; we need a fresh
    # query against the engine to see new rows.)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with factory() as fresh_session:
        audit_after = await _audit_count(fresh_session)
        fp_project_after = await _fingerprint_count(fresh_session, kind="project")
        fp_experiment_after = await _fingerprint_count(fresh_session, kind="experiment")
        fp_execution_after = await _fingerprint_count(fresh_session, kind="execution")
        fp_scenario_after = await _fingerprint_count(fresh_session, kind="scenario")

    assert audit_after == audit_before, "list GETs must not create audit rows"
    assert fp_project_after == fp_project_before, "list GETs must not create fingerprints"
    assert fp_experiment_after == fp_experiment_before
    assert fp_execution_after == fp_execution_before
    assert fp_scenario_after == fp_scenario_before


# ══════════════════════════════════════════════════════════════════════════
# Part 8 — Regression: existing routes still work
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_regression_post_innovations_generate_still_works(
    app, client, db_session, auth_headers_admin
):
    """POST /api/v1/innovations/generate still functions after the new
    GET endpoints were added.
    """
    await _seed_innovation_fixture(db_session)

    body = {
        "problem_domain": "AI research assistant",
        "context": "AI agent systems",
        "max_concepts": 3,
    }
    r = client.post("/api/v1/innovations/generate", json=body, headers=auth_headers_admin)
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert isinstance(body["data"]["concepts"], list)


@pytest.mark.asyncio
async def test_regression_post_innovations_critique_still_works(
    app, client, db_session, auth_headers_admin
):
    """POST /api/v1/innovations/{id}/critique still functions after the
    new GET endpoints were added.
    """
    await _seed_innovation_fixture(db_session)
    innov = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    concept_id = innov["concepts"][0]["id"]

    r = client.post(
        f"/api/v1/innovations/{concept_id}/critique",
        json={"context": "AI agent systems"},
        headers=auth_headers_admin,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert "feasibility_score" in body["data"]
    assert "overall_recommendation" in body["data"]


@pytest.mark.asyncio
async def test_regression_existing_get_scenario_by_id_still_works(
    app, client, db_session, auth_headers_admin
):
    """GET /api/v1/future/scenarios/{scenario_id} still functions after
    the new GET /api/v1/future/scenarios list was added.
    """
    fixture = await _seed_scenario_f0_fixture(db_session)
    result = await build_scenario(
        db_session,
        capability_ids=fixture["capability_ids"],
        assumptions=["the integration is stable"],
        proposed_combination="regression test combination",
        evidence_refs=[fixture["fragment_id"]],
        run_id="run-pre02-regression",
        requester="pre02-tester",
    )
    await db_session.commit()
    scenario_id = result["scenario_id"]

    r = client.get(
        f"/api/v1/future/scenarios/{scenario_id}",
        headers=auth_headers_admin,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["scenario_id"] == scenario_id
    assert body["data"]["horizon_class"] == "F0"


@pytest.mark.asyncio
async def test_regression_existing_get_experiment_by_id_still_works(
    app, client, db_session, auth_headers_admin
):
    """GET /api/v1/experiments/{experiment_id} still functions after the
    new GET /api/v1/experiments list was added.
    """
    await _seed_innovation_fixture(db_session)
    innov = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    hypothesis_id = innov["concepts"][0]["hypothesis_id"]

    plan = await plan_experiment(
        db_session,
        hypothesis_id,
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    experiment_id = plan["experiment"]["experiment_id"]

    r = client.get(f"/api/v1/experiments/{experiment_id}", headers=auth_headers_admin)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["experiment_id"] == experiment_id


@pytest.mark.asyncio
async def test_regression_existing_get_execution_by_id_still_works(
    app, client, db_session, auth_headers_admin
):
    """GET /api/v1/executions/{execution_id} still functions after the
    new GET /api/v1/executions list was added.
    """
    await _seed_innovation_fixture(db_session)
    innov = await generate_innovations(db_session, "AI research assistant", context="AI agent systems")
    await db_session.commit()
    hypothesis_id = innov["concepts"][0]["hypothesis_id"]

    plan = await plan_experiment(
        db_session,
        hypothesis_id,
        execution_mode=ExperimentExecutionMode.DRY_RUN,
        requester="pre02-tester",
    )
    await db_session.commit()
    experiment_id = plan["experiment"]["experiment_id"]

    execution = await create_execution(
        db_session,
        experiment_id,
        run_id="run-pre02-regression-exec",
        requester="pre02-tester",
    )
    await db_session.commit()
    execution_id = execution["execution_id"]

    r = client.get(f"/api/v1/executions/{execution_id}", headers=auth_headers_admin)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["execution_id"] == execution_id


# ══════════════════════════════════════════════════════════════════════════
# Part 9 — OpenAPI surface
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_openapi_includes_new_routes(app, client, auth_headers_admin):
    """The OpenAPI schema at ``/api/v1/openapi.json`` includes the 5 new
    GET endpoints AND retains all existing routes.
    """
    # Note: openapi.json is unauthenticated by FastAPI's docs URL config;
    # we still pass the auth header to be consistent.
    r = client.get("/api/v1/openapi.json", headers=auth_headers_admin)
    assert r.status_code == 200
    schema = r.json()
    paths = set(schema["paths"].keys())

    # New routes must be present.
    assert "/api/v1/innovations" in paths
    assert "/api/v1/innovations/{innovation_id}" in paths
    assert "/api/v1/experiments" in paths
    assert "/api/v1/executions" in paths
    assert "/api/v1/future/scenarios" in paths

    # Existing routes must be retained.
    existing_routes = [
        "/api/v1/innovations/generate",
        "/api/v1/innovations/{innovation_id}/critique",
        "/api/v1/experiments/{experiment_id}",
        "/api/v1/experiments/{experiment_id}/execute",
        "/api/v1/executions/{execution_id}",
        "/api/v1/executions/{execution_id}/observations",
        "/api/v1/executions/{execution_id}/finalize",
        "/api/v1/future/signals",
        "/api/v1/future/scenarios/{scenario_id}",
        "/api/v1/future/scenarios/{scenario_id}/evaluate",
        "/api/v1/future/scenarios/{scenario_id}/prototype-plan",
        "/api/v1/future/scenarios/{scenario_id}/reassess",
        "/api/v1/future/scenarios/{scenario_id}/revisions",
        "/health/live",
        "/health/ready",
    ]
    for route in existing_routes:
        assert route in paths, f"existing route {route} missing from OpenAPI"

    # The 5 new GET endpoints must have the correct HTTP method.
    new_get_specs = [
        ("/api/v1/innovations", "get"),
        ("/api/v1/innovations/{innovation_id}", "get"),
        ("/api/v1/experiments", "get"),
        ("/api/v1/executions", "get"),
        ("/api/v1/future/scenarios", "get"),
    ]
    for path, method in new_get_specs:
        assert method in schema["paths"][path], f"{method.upper()} {path} missing"
