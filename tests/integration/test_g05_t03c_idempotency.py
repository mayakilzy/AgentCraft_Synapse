"""G05-T03C — Innovation Identity & Idempotency Closure tests.

Per the G05-T03C mission briefing: focused tests for:
  - Same request repeated sequentially → same concept IDs.
  - Equivalent normalized requests → same concept IDs.
  - Different requests → distinct concept IDs.
  - Same concept under repeated API calls.
  - Duplicate relationship prevention.
  - Idempotency-key reuse with conflicting payload.
  - Concurrent requests (where feasible).
  - Existing G01-G05-T03 regression.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.innovation import generate_innovations
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
    SourceRow,
)

# ── Test fixture (reused from G05-T03) ──────────────────────────────────────

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
    epistemic_state: str = "supported",
    validity_conditions: list[str] | None = None,
) -> ClaimRow:
    c = ClaimRow(
        id=cid,
        proposition=proposition,
        subject_ref=subject_ref,
        object_ref=object_ref,
        evidence_refs=json.dumps(evidence_refs or []),
        contradicting_refs="[]",
        epistemic_state=epistemic_state,
        validity_conditions=json.dumps(validity_conditions or []),
        extraction_method="test-fixture",
        version=1,
    )
    session.add(c)
    await session.flush()
    return c


async def _seed_idempotency_fixture(session) -> dict[str, Any]:
    """Seed a fixture for idempotency testing."""
    src_a = await _make_source(session, "src-t03c-a", "https://example.com/papers/arxiv")
    src_b = await _make_source(session, "src-t03c-b", "https://example.com/papers/trafilatura")
    frag_a = await _make_fragment(session, "frag-t03c-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-t03c-b", src_b, "trafilatura extracts content.")

    await _make_entity(session, "ent-arxiv", "technology", "arxiv", desc="arXiv")
    await _make_entity(session, "ent-trafilatura", "technology", "trafilatura", desc="trafilatura")
    await _make_entity(session, "cap-source-discovery", "capability", "source discovery")
    await _make_entity(session, "cap-content-extraction", "capability", "content extraction")

    await _make_claim(
        session,
        "claim-t03c-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-arxiv",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-t03c-a"],
    )
    await _make_claim(
        session,
        "claim-t03c-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-trafilatura",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-t03c-b"],
    )

    for from_id, to_id, ev_refs in [
        ("ent-arxiv", "cap-source-discovery", ["frag-t03c-a"]),
        ("ent-trafilatura", "cap-content-extraction", ["frag-t03c-b"]),
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
    for cid in ("claim-t03c-sd", "claim-t03c-ce"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {"arxiv_id": "ent-arxiv", "trafilatura_id": "ent-trafilatura"}


# ── Test 1: Same request repeated sequentially ─────────────────────────────


@pytest.mark.asyncio
async def test_01_same_request_repeated_sequentially(app, db_session):
    """Same request repeated sequentially produces the same concept IDs."""
    await _seed_idempotency_fixture(db_session)

    result1 = await generate_innovations(db_session, "AI research assistant", context="AI agents")
    result2 = await generate_innovations(db_session, "AI research assistant", context="AI agents")

    # Same number of concepts
    assert len(result1["concepts"]) == len(result2["concepts"])

    # Same concept IDs (idempotent reuse)
    ids1 = [c["id"] for c in result1["concepts"]]
    ids2 = [c["id"] for c in result2["concepts"]]
    assert ids1 == ids2, f"concept IDs differ: {ids1} vs {ids2}"

    # Same hypothesis IDs
    hyps1 = [c["hypothesis_id"] for c in result1["concepts"]]
    hyps2 = [c["hypothesis_id"] for c in result2["concepts"]]
    assert hyps1 == hyps2, f"hypothesis IDs differ: {hyps1} vs {hyps2}"


# ── Test 2: Equivalent normalized requests ────────────────────────────────


@pytest.mark.asyncio
async def test_02_equivalent_normalized_requests(app, db_session):
    """Equivalent requests with different casing/whitespace produce same IDs."""
    await _seed_idempotency_fixture(db_session)

    # Different casing and whitespace
    result1 = await generate_innovations(
        db_session, "  AI Research Assistant  ", context="AI Agents"
    )
    result2 = await generate_innovations(db_session, "ai research assistant", context="ai agents")

    # Same number of concepts
    assert len(result1["concepts"]) == len(result2["concepts"])

    # Same concept IDs (normalized → same fingerprint)
    ids1 = sorted(c["id"] for c in result1["concepts"])
    ids2 = sorted(c["id"] for c in result2["concepts"])
    assert ids1 == ids2, f"normalized concept IDs differ: {ids1} vs {ids2}"


# ── Test 3: Different requests remain distinct ────────────────────────────


@pytest.mark.asyncio
async def test_03_different_requests_remain_distinct(app, db_session):
    """Different problem domains produce distinct concept IDs."""
    await _seed_idempotency_fixture(db_session)

    result1 = await generate_innovations(db_session, "AI research assistant", context="AI agents")
    result2 = await generate_innovations(
        db_session, "web scraping pipeline", context="data extraction"
    )

    # Concepts should have different IDs
    ids1 = {c["id"] for c in result1["concepts"]}
    ids2 = {c["id"] for c in result2["concepts"]}

    # No overlap (unless the combinations happen to be identical — which they
    # shouldn't be since the problem domains are different)
    overlap = ids1 & ids2
    # Some overlap is possible if the same combination of components is used
    # for both problem domains with the same template. But the fingerprint
    # includes the problem_domain, so they should be distinct.
    assert len(overlap) == 0, f"unexpected concept ID overlap: {overlap}"


# ── Test 4: Same concept under repeated API calls ────────────────────────


@pytest.mark.asyncio
async def test_04_same_concept_under_repeated_api(app, client, db_session, auth_headers_reader):
    """POST /api/v1/innovations/generate called twice returns same concept IDs."""
    await _seed_idempotency_fixture(db_session)

    r1 = client.post(
        "/api/v1/innovations/generate",
        headers=auth_headers_reader,
        json={"problem_domain": "AI research assistant", "context": "AI agents"},
    )
    assert r1.status_code == 200
    ids1 = sorted(c["id"] for c in r1.json()["data"]["concepts"])

    r2 = client.post(
        "/api/v1/innovations/generate",
        headers=auth_headers_reader,
        json={"problem_domain": "AI research assistant", "context": "AI agents"},
    )
    assert r2.status_code == 200
    ids2 = sorted(c["id"] for c in r2.json()["data"]["concepts"])

    assert ids1 == ids2, f"API concept IDs differ: {ids1} vs {ids2}"


# ── Test 5: Duplicate relationship prevention ─────────────────────────────


@pytest.mark.asyncio
async def test_05_duplicate_relationship_prevention(app, db_session):
    """Repeated requests don't create duplicate INTEGRATES_WITH relationships."""
    await _seed_idempotency_fixture(db_session)

    # Generate twice
    await generate_innovations(db_session, "AI research assistant", context="AI agents")
    await generate_innovations(db_session, "AI research assistant", context="AI agents")

    # Count INTEGRATES_WITH relationships with origin="hypothesized"
    stmt = select(RelationshipRow).where(
        RelationshipRow.predicate == "INTEGRATES_WITH",
        RelationshipRow.origin == "hypothesized",
    )
    rels = list((await db_session.execute(stmt)).scalars().all())

    # Each (innovation_entity, component_entity) pair should appear at most once
    seen_pairs: set[tuple[str, str]] = set()
    for rel in rels:
        pair = (rel.from_entity_id, rel.to_entity_id)
        assert pair not in seen_pairs, f"duplicate INTEGRATES_WITH relationship: {pair}"
        seen_pairs.add(pair)


# ── Test 6: No duplicate EntityRow or ClaimRow ──────────────────────────


@pytest.mark.asyncio
async def test_06_no_duplicate_entities_or_claims(app, db_session):
    """Repeated requests don't create duplicate EntityRow or ClaimRow records."""
    await _seed_idempotency_fixture(db_session)

    await generate_innovations(db_session, "AI research assistant", context="AI agents")
    await generate_innovations(db_session, "AI research assistant", context="AI agents")

    # Count innovation entities (kind="project")
    innov_stmt = select(EntityRow).where(EntityRow.kind == "project")
    innovs = list((await db_session.execute(innov_stmt)).scalars().all())
    innov_ids = [e.id for e in innovs]

    # No duplicate IDs
    assert len(innov_ids) == len(set(innov_ids)), f"duplicate innovation entity IDs: {innov_ids}"

    # Count hypothesis claims (extraction_method="g05-t03-innovation-generation")
    hyp_stmt = select(ClaimRow).where(ClaimRow.extraction_method == "g05-t03-innovation-generation")
    hyps = list((await db_session.execute(hyp_stmt)).scalars().all())
    hyp_ids = [c.id for c in hyps]

    # No duplicate IDs
    assert len(hyp_ids) == len(set(hyp_ids)), f"duplicate hypothesis claim IDs: {hyp_ids}"


# ── Test 7: Idempotency-key reuse with conflicting payload ───────────────


@pytest.mark.asyncio
async def test_07_idempotency_key_conflict(app, client, db_session, auth_headers_reader):
    """Different problem_domain with the same Idempotency-Key header still
    works — the idempotency key is NOT enforced at the HTTP layer (it's
    a convention, not a hard constraint per ADR-0006). The concept identity
    is determined by the input payload, not the header."""
    await _seed_idempotency_fixture(db_session)

    # First request
    r1 = client.post(
        "/api/v1/innovations/generate",
        headers={**auth_headers_reader, "Idempotency-Key": "key-001"},
        json={"problem_domain": "AI research assistant"},
    )
    assert r1.status_code == 200
    ids1 = sorted(c["id"] for c in r1.json()["data"]["concepts"])

    # Second request with same Idempotency-Key but different payload
    r2 = client.post(
        "/api/v1/innovations/generate",
        headers={**auth_headers_reader, "Idempotency-Key": "key-001"},
        json={"problem_domain": "web scraping pipeline"},
    )
    assert r2.status_code == 200
    ids2 = sorted(c["id"] for c in r2.json()["data"]["concepts"])

    # Different payloads → different concept IDs (identity is payload-based,
    # not key-based — the Idempotency-Key is a convention per ADR-0006)
    assert ids1 != ids2, (
        "different payloads with same Idempotency-Key should produce "
        "different concept IDs (identity is payload-based)"
    )


# ── Test 8: Concurrent requests (within existing storage constraints) ─────


@pytest.mark.asyncio
async def test_08_concurrent_requests(app, db_session):
    """Concurrent equivalent requests don't produce duplicate concepts.

    NOTE: This test uses a single session (not truly concurrent across
    sessions). True cross-session concurrency safety requires DB-level
    UNIQUE constraints (PRB-03) which are NOT yet implemented.

    This test verifies that within a single session, sequential calls
    are idempotent. Cross-session concurrency is documented as LIMITED.
    """
    await _seed_idempotency_fixture(db_session)

    # Two sequential calls (not truly concurrent — single session)
    result1 = await generate_innovations(db_session, "AI research", context="AI agents")
    result2 = await generate_innovations(db_session, "AI research", context="AI agents")

    # Same concept IDs
    ids1 = sorted(c["id"] for c in result1["concepts"])
    ids2 = sorted(c["id"] for c in result2["concepts"])
    assert ids1 == ids2

    # No duplicate entities in the DB
    innov_stmt = select(EntityRow).where(EntityRow.kind == "project")
    innovs = list((await db_session.execute(innov_stmt)).scalars().all())
    assert len(innovs) == len({e.id for e in innovs}), "duplicate entities despite idempotency"


# ── Test 9: Regression — G01-G05-T03 ─────────────────────────────────────


def test_09_regression():
    import os
    import subprocess
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/api/test_openapi.py",
            "tests/integration/test_g05_t01_knowledge_combination.py::test_05_bounded_deterministic_output",
            "tests/integration/test_g05_t03_innovation_generation.py::test_01_generates_valid_concepts",
            "tests/integration/test_g05_t03_innovation_generation.py::test_02_every_concept_has_uncertainty",
            "tests/integration/test_g05_t03_innovation_generation.py::test_03_hypothesis_id_points_to_hypothesized",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr[:600]}\nstdout={r.stdout[:600]}"
    assert "passed" in r.stdout
