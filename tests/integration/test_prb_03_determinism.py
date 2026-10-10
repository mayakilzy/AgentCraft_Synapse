"""PRB-03 Final Audit — Equivalent-request determinism tests.

Distinguishes two guarantees:
1. Persistence uniqueness: one stored entity per fingerprint.
2. Result determinism: equivalent requests produce the same logical
   result set when the underlying knowledge snapshot is unchanged.

The previous PRB-03 closure established persistence uniqueness. This
test module investigates the reported combine_knowledge ordering
instability to determine whether it is:
(a) Pure ordering nondeterminism (same candidate set, different order),
(b) Materially different candidate selection (different set), or
(c) A localized ordering issue that can be safely fixed.

Per the audit mission: 'If a stable ordering or tie-breaker can resolve
the issue safely, implement the smallest localized correction and add a
regression test. Do not introduce new ranking algorithms or change
established retrieval semantics.'
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PG_TEST_URL = os.environ.get("SYNAPSE_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL, reason="Set SYNAPSE_PG_TEST_URL to run determinism tests"
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()


async def _seed_minimal_fixture(eng):
    """Seed a minimal, fixed knowledge snapshot for determinism testing."""
    async with eng.begin() as conn:
        for table in [
            "entity_fingerprints", "relationships", "source_spans",
            "claims", "evidence_fragments", "entities", "sources",
        ]:
            await conn.execute(text(f"DELETE FROM {table}"))

        # Sources + fragments
        for sid, uri in [
            ("src-det-a", "https://example.com/arxiv-det"),
            ("src-det-b", "https://example.com/trafilatura-det"),
            ("src-det-c", "https://example.com/synapse-det"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO sources (id, canonical_uri, source_type, status, version) "
                    "VALUES (:id, :uri, 'paper', 'extracted', 1)"
                ),
                {"id": sid, "uri": uri},
            )

        for fid, sid, excerpt in [
            ("frag-det-a", "src-det-a", "arxiv provides source discovery."),
            ("frag-det-b", "src-det-b", "trafilatura extracts content."),
            ("frag-det-c", "src-det-c", "synapse provides evidence verification."),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO evidence_fragments "
                    "(id, acquisition_id, source_id, source_uri, exact_excerpt, "
                    "excerpt_hash, retrieved_at, extraction_method, content_fingerprint, "
                    "toolkit_commit_sha) "
                    "VALUES (:id, :acq, :sid, :uri, :excerpt, :hash, :ts, 'fixture', :fp, 'fixture')"
                ),
                {
                    "id": fid, "acq": f"acq-{fid}", "sid": sid,
                    "uri": f"https://example.com/{sid}", "excerpt": excerpt,
                    "hash": f"hash-{fid}", "ts": RECENT_ISO, "fp": f"fp-{fid}",
                },
            )

        # Entities — fixed IDs, fixed order of insertion
        for eid, kind, name in [
            ("ent-det-arxiv", "technology", "arxiv"),
            ("ent-det-trafilatura", "technology", "trafilatura"),
            ("ent-det-synapse", "tool", "synapse"),
            ("cap-det-sd", "capability", "source discovery"),
            ("cap-det-ce", "capability", "content extraction"),
            ("cap-det-ev", "capability", "evidence verification"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                    "VALUES (:id, :kind, :name, '[]', '{}', 1)"
                ),
                {"id": eid, "kind": kind, "name": name},
            )

        # Claims
        for cid, prop, subj, obj, refs in [
            ("claim-det-sd", "arxiv provides source discovery.", "ent-det-arxiv", "cap-det-sd", ["frag-det-a"]),
            ("claim-det-ce", "trafilatura provides content extraction.", "ent-det-trafilatura", "cap-det-ce", ["frag-det-b"]),
            ("claim-det-ev", "synapse provides evidence verification.", "ent-det-synapse", "cap-det-ev", ["frag-det-c"]),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO claims (id, proposition, subject_ref, object_ref, "
                    "evidence_refs, epistemic_state, extraction_method, version) "
                    "VALUES (:id, :prop, :subj, :obj, :refs, 'supported', 'fixture', 1)"
                ),
                {"id": cid, "prop": prop, "subj": subj, "obj": obj, "refs": json.dumps(refs)},
            )

        # Relationships
        for frm, to, ev in [
            ("ent-det-arxiv", "cap-det-sd", ["frag-det-a"]),
            ("ent-det-trafilatura", "cap-det-ce", ["frag-det-b"]),
            ("ent-det-synapse", "cap-det-ev", ["frag-det-c"]),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO relationships (id, from_entity_id, to_entity_id, predicate, "
                    "direction, origin, verification_state, evidence_refs, version) "
                    "VALUES (:id, :frm, :to, 'PROVIDES', 'directed', 'explicit', 'unverified', :ev, 1)"
                ),
                {"id": f"rel-det-{frm}-{to}", "frm": frm, "to": to, "ev": json.dumps(ev)},
            )


async def _assess_claims(eng):
    from synapse.application.verification import assess_claim
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as session:
        for cid in ("claim-det-sd", "claim-det-ce", "claim-det-ev"):
            await assess_claim(session, cid, requester="det-fixture")
        await session.commit()


@pytest.fixture(scope="module")
def pg_engine():
    """Module-scoped async engine bound to the real PostgreSQL DB."""
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


class TestEquivalentRequestDeterminism:
    """Run repeated equivalent requests against an unchanged snapshot.

    Distinguish:
    - Pure ordering nondeterminism: same concept SET, different order.
    - Materially different selection: different concept SET.
    """

    @pytest.mark.asyncio
    async def test_sequential_repeated_requests_same_set(self, pg_engine):
        """Sequential repeated requests must produce the same concept SET."""
        await _seed_minimal_fixture(pg_engine)
        await _assess_claims(pg_engine)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.innovation import generate_innovations

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)
        concept_sets = []
        for _ in range(10):
            async with factory() as session:
                r = await generate_innovations(
                    session, "AI research assistant", context="AI agent systems"
                )
                await session.commit()
                concept_sets.append(frozenset(c["id"] for c in r["concepts"]))

        first = concept_sets[0]
        for i, s in enumerate(concept_sets[1:], 1):
            if s != first:
                # Document the nature of the difference
                only_in_first = first - s
                only_in_i = s - first
                pytest.fail(
                    f"Sequential request {i} produced a different concept SET.\n"
                    f"  Concepts in run 0 but not run {i}: {only_in_first}\n"
                    f"  Concepts in run {i} but not run 0: {only_in_i}\n"
                    f"  This is materially different candidate selection, "
                    f"not just ordering nondeterminism."
                )

    @pytest.mark.asyncio
    async def test_concurrent_repeated_requests_same_set(self, pg_engine):
        """Concurrent repeated requests must produce the same concept SET."""
        await _seed_minimal_fixture(pg_engine)
        await _assess_claims(pg_engine)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.innovation import generate_innovations

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        async def one_request():
            async with factory() as session:
                try:
                    r = await generate_innovations(
                        session, "AI research assistant", context="AI agent systems"
                    )
                    await session.commit()
                    return frozenset(c["id"] for c in r["concepts"])
                except Exception:
                    await session.rollback()
                    raise

        concept_sets = await asyncio.gather(*[one_request() for _ in range(10)])
        first = concept_sets[0]
        for i, s in enumerate(concept_sets[1:], 1):
            if s != first:
                only_in_first = first - s
                only_in_i = s - first
                pytest.fail(
                    f"Concurrent request {i} produced a different concept SET.\n"
                    f"  Concepts in run 0 but not run {i}: {only_in_first}\n"
                    f"  Concepts in run {i} but not run 0: {only_in_i}\n"
                    f"  This is materially different candidate selection under "
                    f"concurrency, not just ordering nondeterminism."
                )

    @pytest.mark.asyncio
    async def test_component_set_determinism(self, pg_engine):
        """Investigate whether the candidate component SET is deterministic."""
        await _seed_minimal_fixture(pg_engine)
        await _assess_claims(pg_engine)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.innovation import combine_knowledge

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        combination_sets = []
        for _ in range(5):
            async with factory() as session:
                r = await combine_knowledge(
                    session, "AI research assistant", context="AI agent systems"
                )
                await session.commit()
                # Extract the component entity IDs from each combination
                comps_per_combination = []
                for comb in r.get("combinations", []):
                    comp_ids = frozenset(c.get("entity_id", "") for c in comb.get("components", []))
                    comps_per_combination.append(comp_ids)
                combination_sets.append(frozenset(frozenset(s) for s in comps_per_combination))

        first = combination_sets[0]
        for i, s in enumerate(combination_sets[1:], 1):
            if s != first:
                pytest.fail(
                    f"combine_knowledge run {i} produced a different combination SET.\n"
                    f"  This indicates the candidate component selection is "
                    f"nondeterministic — hybrid_retrieve returns different "
                    f"components on different calls."
                )


class TestPersistenceUniquenessUnderDeterminismGap:
    """Even if result determinism has a gap, persistence uniqueness must hold."""

    @pytest.mark.asyncio
    async def test_no_duplicate_entities_across_nondeterministic_runs(self, pg_engine):
        """Even if different runs produce different concept sets, each
        unique concept must be persisted exactly once."""
        await _seed_minimal_fixture(pg_engine)
        await _assess_claims(pg_engine)
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from synapse.application.innovation import generate_innovations

        factory = async_sessionmaker(pg_engine, expire_on_commit=False)

        all_concept_ids = set()
        for _ in range(10):
            async with factory() as session:
                r = await generate_innovations(
                    session, "AI research assistant", context="AI agent systems"
                )
                await session.commit()
                for c in r["concepts"]:
                    all_concept_ids.add(c["id"])

        # Verify no duplicates in entities table
        async with pg_engine.begin() as conn:
            ent_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM entities WHERE kind='project' AND id LIKE 'innov-%'"
                    )
                )
            ).scalar()
            assert ent_count == len(all_concept_ids), (
                f"Duplicate entities: {ent_count} rows for {len(all_concept_ids)} unique IDs"
            )

            # Verify no duplicate fingerprints
            dup_fp = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM (SELECT kind, fingerprint, count(*) as c "
                        "FROM entity_fingerprints WHERE kind='project' "
                        "GROUP BY kind, fingerprint HAVING count(*) > 1) as dups"
                    )
                )
            ).scalar()
            assert dup_fp == 0, f"Found {dup_fp} duplicate fingerprint rows"
