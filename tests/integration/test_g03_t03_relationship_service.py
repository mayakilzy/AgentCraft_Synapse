"""Tests for G03-T03: RelationshipService.

Per the user's G03-T03 authorization §Acceptance tests:
  1. Typed relationship creation and retrieval.
  2. Directionality and predicate correctness.
  3. Provenance and source-span preservation.
  4. Multiple supporting evidence references.
  5. Contradictory relationships coexist.
  6. Idempotent repeated creation.
  7. Bounded multi-hop traversal.
  8. Missing-capability and dependency queries.
  9. Atomicity and retry after failure.
 10. Existing G01/G02/G03 regression tests.
"""

from __future__ import annotations

import pytest

from synapse.application.relationship_service import (
    create_relationship,
    find_capabilities,
    find_contradictions,
    find_dependencies,
    find_missing_capabilities,
    find_paths,
    find_relationships,
    get_relationship_evidence,
)
from synapse.storage.models import (
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


async def _create_entity(
    session, entity_id: str, kind: str = "tool", name: str = "Test Entity"
) -> EntityRow:
    """Create an entity for testing."""
    row = EntityRow(
        id=entity_id,
        kind=kind,
        canonical_name=name,
        aliases="[]",
        attributes="{}",
        version=1,
    )
    session.add(row)
    await session.flush()
    return row


async def _create_evidence_fragment(session, fragment_id: str = "ef-rel-1") -> EvidenceFragmentRow:
    """Create a source + evidence fragment for testing."""
    source = SourceRow(
        id=f"src-{fragment_id}",
        canonical_uri=f"https://example.com/{fragment_id}",
        source_type="paper",
        status="extracted",
    )
    session.add(source)
    await session.flush()
    ef = EvidenceFragmentRow(
        id=fragment_id,
        acquisition_id=f"acq-{fragment_id}",
        source_id=source.id,
        source_uri=f"https://example.com/{fragment_id}",
        exact_excerpt="This paper introduces the self-attention mechanism.",
        excerpt_hash="abcdef0123456789",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint="fp-rel",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    session.add(ef)
    await session.flush()
    return ef


# ── Test 1: Typed relationship creation and retrieval ──────────────────────


@pytest.mark.asyncio
async def test_relationship_creation_and_retrieval(app, db_session):
    """Create a typed relationship and retrieve it."""
    await _create_entity(db_session, "ent-a", "tool", "Tool A")
    await _create_entity(db_session, "ent-b", "capability", "Capability B")

    r = await create_relationship(
        db_session,
        from_entity_id="ent-a",
        to_entity_id="ent-b",
        predicate="PROVIDES",
        origin="explicit",
        evidence_refs=["ef-rel-1"],
    )
    await db_session.commit()

    assert r["ok"] is True
    assert r["merged"] is False
    rel_id = r["relationship_id"]

    # Retrieve it
    rels = await find_relationships(db_session, entity_id="ent-a", predicate="PROVIDES")
    assert len(rels) == 1
    assert rels[0]["predicate"] == "PROVIDES"
    assert rels[0]["from_entity_id"] == "ent-a"
    assert rels[0]["to_entity_id"] == "ent-b"
    assert rels[0]["origin"] == "explicit"
    assert "ef-rel-1" in rels[0]["evidence_refs"]


# ── Test 2: Directionality and predicate correctness ───────────────────────


@pytest.mark.asyncio
async def test_directionality_and_predicate(app, db_session):
    """Outgoing vs incoming direction is correct."""
    await _create_entity(db_session, "ent-c", "tool", "Tool C")
    await _create_entity(db_session, "ent-d", "technique", "Technique D")

    await create_relationship(
        db_session,
        from_entity_id="ent-c",
        to_entity_id="ent-d",
        predicate="ENABLES",
        evidence_refs=["ef-1"],
    )
    await db_session.commit()

    # Outgoing from ent-c
    outgoing = await find_relationships(db_session, entity_id="ent-c", direction="outgoing")
    assert len(outgoing) == 1
    assert outgoing[0]["predicate"] == "ENABLES"

    # Incoming to ent-d
    incoming = await find_relationships(db_session, entity_id="ent-d", direction="incoming")
    assert len(incoming) == 1
    assert incoming[0]["predicate"] == "ENABLES"

    # Both directions from ent-c
    both = await find_relationships(db_session, entity_id="ent-c", direction="both")
    assert len(both) == 1  # only outgoing


# ── Test 3: Provenance and source-span preservation ───────────────────────


@pytest.mark.asyncio
async def test_provenance_and_source_span_preservation(app, db_session):
    """Relationship preserves origin, evidence refs, and source spans."""
    await _create_entity(db_session, "ent-e", "technique", "Technique E")
    await _create_entity(db_session, "ent-f", "constraint", "Constraint F")
    ef = await _create_evidence_fragment(db_session, "ef-prov-1")

    # Create a source span linked to the evidence fragment
    span = SourceSpanRow(
        id="span-1",
        evidence_fragment_id="ef-prov-1",
        claim_id=None,
        start_offset=0,
        end_offset=20,
        excerpt="requires GPU",
        context_before="The approach ",
        context_after=" for training.",
    )
    db_session.add(span)
    await db_session.flush()

    r = await create_relationship(
        db_session,
        from_entity_id="ent-e",
        to_entity_id="ent-f",
        predicate="LIMITS",
        origin="explicit",
        evidence_refs=["ef-prov-1"],
        conditions=["under low-memory conditions"],
    )
    await db_session.commit()
    assert r["ok"] is True

    # Verify the relationship has the evidence ref
    rels = await find_relationships(db_session, entity_id="ent-e")
    assert len(rels) == 1
    assert "ef-prov-1" in rels[0]["evidence_refs"]
    assert rels[0]["origin"] == "explicit"
    assert "under low-memory conditions" in rels[0]["conditions"]

    # Verify evidence lookup returns the fragment + span
    evidence = await get_relationship_evidence(db_session, r["relationship_id"])
    assert len(evidence) == 1
    assert evidence[0]["evidence_fragment_id"] == "ef-prov-1"
    assert len(evidence[0]["spans"]) >= 1
    assert evidence[0]["spans"][0]["excerpt"] == "requires GPU"


# ── Test 4: Multiple supporting evidence references ────────────────────────


@pytest.mark.asyncio
async def test_multiple_evidence_references(app, db_session):
    """A relationship can have multiple evidence refs from different sources."""
    await _create_entity(db_session, "ent-g", "tool", "Tool G")
    await _create_entity(db_session, "ent-h", "capability", "Capability H")
    await _create_evidence_fragment(db_session, "ef-multi-1")
    await _create_evidence_fragment(db_session, "ef-multi-2")

    # Create with one evidence ref
    r1 = await create_relationship(
        db_session,
        from_entity_id="ent-g",
        to_entity_id="ent-h",
        predicate="PROVIDES",
        evidence_refs=["ef-multi-1"],
    )
    await db_session.commit()
    assert r1["ok"] is True

    # Create again with a second evidence ref — should merge
    r2 = await create_relationship(
        db_session,
        from_entity_id="ent-g",
        to_entity_id="ent-h",
        predicate="PROVIDES",
        evidence_refs=["ef-multi-2"],
    )
    await db_session.commit()
    assert r2["ok"] is True
    assert r2["merged"] is True

    # Verify both evidence refs are present
    rels = await find_relationships(db_session, entity_id="ent-g", predicate="PROVIDES")
    assert len(rels) == 1
    assert "ef-multi-1" in rels[0]["evidence_refs"]
    assert "ef-multi-2" in rels[0]["evidence_refs"]
    assert len(rels[0]["evidence_refs"]) == 2


# ── Test 5: Contradictory relationships coexist ───────────────────────────


@pytest.mark.asyncio
async def test_contradictory_relationships_coexist(app, db_session):
    """SUPPORTS and CONTRADICTS on the same pair both exist — neither deleted."""
    await _create_entity(db_session, "ent-i", "paper", "Paper I")
    await _create_entity(db_session, "ent-j", "technique", "Technique J")

    # Create SUPPORTS
    r1 = await create_relationship(
        db_session,
        from_entity_id="ent-i",
        to_entity_id="ent-j",
        predicate="SUPPORTS",
        evidence_refs=["ef-1"],
    )
    await db_session.commit()

    # Create CONTRADICTS — should NOT overwrite SUPPORTS
    r2 = await create_relationship(
        db_session,
        from_entity_id="ent-i",
        to_entity_id="ent-j",
        predicate="CONTRADICTS",
        evidence_refs=["ef-2"],
    )
    await db_session.commit()

    # Both exist
    all_rels = await find_relationships(db_session, entity_id="ent-i")
    predicates = [r["predicate"] for r in all_rels]
    assert "SUPPORTS" in predicates
    assert "CONTRADICTS" in predicates

    # find_contradictions detects the pair
    contradictions = await find_contradictions(db_session)
    assert len(contradictions) >= 1
    assert contradictions[0]["both_preserved"] is True


# ── Test 6: Idempotent repeated creation ───────────────────────────────────


@pytest.mark.asyncio
async def test_idempotent_repeated_creation(app, db_session):
    """Creating the same relationship twice merges instead of duplicating."""
    await _create_entity(db_session, "ent-k", "tool", "Tool K")
    await _create_entity(db_session, "ent-l", "capability", "Capability L")

    r1 = await create_relationship(
        db_session,
        from_entity_id="ent-k",
        to_entity_id="ent-l",
        predicate="PROVIDES",
        evidence_refs=["ef-1"],
    )
    await db_session.commit()

    r2 = await create_relationship(
        db_session,
        from_entity_id="ent-k",
        to_entity_id="ent-l",
        predicate="PROVIDES",
        evidence_refs=["ef-1"],  # same evidence
    )
    await db_session.commit()

    assert r1["ok"] is True
    assert r2["ok"] is True
    assert r2["merged"] is True
    assert r1["relationship_id"] == r2["relationship_id"]

    # Only one relationship in DB
    rels = await find_relationships(db_session, entity_id="ent-k", predicate="PROVIDES")
    assert len(rels) == 1


# ── Test 7: Bounded multi-hop traversal ───────────────────────────────────


@pytest.mark.asyncio
async def test_bounded_multi_hop_traversal(app, db_session):
    """Multi-hop path: A → B → C with max_depth=5."""
    await _create_entity(db_session, "ent-a", "tool", "A")
    await _create_entity(db_session, "ent-b", "technique", "B")
    await _create_entity(db_session, "ent-c", "capability", "C")

    await create_relationship(
        db_session, from_entity_id="ent-a", to_entity_id="ent-b", predicate="ENABLES"
    )
    await create_relationship(
        db_session, from_entity_id="ent-b", to_entity_id="ent-c", predicate="PRODUCES"
    )
    await db_session.commit()

    paths = await find_paths(db_session, "ent-a", "ent-c", max_depth=5)
    assert len(paths) >= 1
    assert len(paths[0]) == 2  # two hops
    assert paths[0][0]["predicate"] == "ENABLES"
    assert paths[0][1]["predicate"] == "PRODUCES"

    # With max_depth=1, no path found
    paths_short = await find_paths(db_session, "ent-a", "ent-c", max_depth=1)
    assert len(paths_short) == 0


@pytest.mark.asyncio
async def test_cyclic_graph_safe(app, db_session):
    """Cyclic graph: A → B → A does not infinite-loop."""
    await _create_entity(db_session, "ent-cyc-a", "tool", "CycA")
    await _create_entity(db_session, "ent-cyc-b", "tool", "CycB")

    await create_relationship(
        db_session,
        from_entity_id="ent-cyc-a",
        to_entity_id="ent-cyc-b",
        predicate="INTEGRATES_WITH",
    )
    await create_relationship(
        db_session,
        from_entity_id="ent-cyc-b",
        to_entity_id="ent-cyc-a",
        predicate="INTEGRATES_WITH",
    )
    await db_session.commit()

    # Should not hang
    paths = await find_paths(db_session, "ent-cyc-a", "ent-cyc-b", max_depth=5)
    # Direct path exists
    assert any(len(p) == 1 for p in paths)


# ── Test 8: Missing-capability and dependency queries ─────────────────────


@pytest.mark.asyncio
async def test_capability_and_dependency_queries(app, db_session):
    """find_capabilities, find_dependencies, find_missing_capabilities."""
    await _create_entity(db_session, "ent-tool", "tool", "MyTool")
    await _create_entity(db_session, "ent-cap1", "capability", "WebFetch")
    await _create_entity(db_session, "ent-cap2", "capability", "TextExtract")
    await _create_entity(db_session, "ent-cap3", "capability", "UnusedCap")
    await _create_entity(db_session, "ent-dep1", "technology", "Python3.12")

    # MyTool PROVIDES WebFetch
    await create_relationship(
        db_session, from_entity_id="ent-tool", to_entity_id="ent-cap1", predicate="PROVIDES"
    )
    # MyTool ENABLES TextExtract
    await create_relationship(
        db_session, from_entity_id="ent-tool", to_entity_id="ent-cap2", predicate="ENABLES"
    )
    # MyTool REQUIRES Python3.12
    await create_relationship(
        db_session, from_entity_id="ent-tool", to_entity_id="ent-dep1", predicate="REQUIRES"
    )
    await db_session.commit()

    # Find capabilities
    caps = await find_capabilities(db_session, "ent-tool")
    assert len(caps) == 2
    cap_names = {c["entity"]["canonical_name"] for c in caps}
    assert "WebFetch" in cap_names
    assert "TextExtract" in cap_names

    # Find dependencies
    deps = await find_dependencies(db_session, "ent-tool")
    assert len(deps) == 1
    assert deps[0]["entity"]["canonical_name"] == "Python3.12"

    # Find missing capabilities
    missing = await find_missing_capabilities(db_session, "ent-tool")
    missing_names = {m["entity"]["canonical_name"] for m in missing}
    assert "UnusedCap" in missing_names
    assert "WebFetch" not in missing_names
    assert "TextExtract" not in missing_names


# ── Test 9: Atomicity and retry after failure ─────────────────────────────


@pytest.mark.asyncio
async def test_atomicity_and_retry(app, db_session):
    """If relationship creation fails, partial state is not corrupted."""
    await _create_entity(db_session, "ent-atom-a", "tool", "AtomA")
    await _create_entity(db_session, "ent-atom-b", "capability", "AtomB")

    # First attempt: succeeds
    r1 = await create_relationship(
        db_session,
        from_entity_id="ent-atom-a",
        to_entity_id="ent-atom-b",
        predicate="PROVIDES",
        evidence_refs=["ef-1"],
    )
    await db_session.commit()
    assert r1["ok"] is True

    # Simulate failure: try to create a relationship with a non-existent entity
    r2 = await create_relationship(
        db_session,
        from_entity_id="ent-atom-a",
        to_entity_id="nonexistent",
        predicate="PROVIDES",
        evidence_refs=["ef-2"],
    )
    # Should fail gracefully (not raise)
    assert r2["ok"] is False
    assert "entity_not_found" in r2["error"]

    # The first relationship is still intact
    rels = await find_relationships(db_session, entity_id="ent-atom-a")
    assert len(rels) == 1
    assert rels[0]["predicate"] == "PROVIDES"


# ── Test 10: G01/G02/G03 regression ────────────────────────────────────────


def test_g01_g02_g03_regression():
    """All existing tests still pass — no regression from G03-T03."""
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
            "tests/unit/domain/test_relationship.py",
            "tests/unit/security/test_ssrf.py",
            "tests/unit/application/test_extraction.py",
            "tests/integration/test_g03_t02_canonicalization.py::test_g01_g02_g03_t01_regression",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout
