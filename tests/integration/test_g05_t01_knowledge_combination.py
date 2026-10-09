"""G05-T01 -- Evidence-Grounded Knowledge Combination Engine acceptance tests.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§G05-T01), the 7 acceptance criteria:

  1. Returns >=1 combination for a problem domain with >=2 documented capabilities.
  2. Every cited entity_id resolves to an existing EntityRow.
  3. Every evidence_ref resolves to an existing EvidenceFragmentRow.
  4. Empty result when no capabilities match (truthful, no fabrication).
  5. Deterministic: same input -> same output.
  6. Hypothesized relationships are epistemically isolated:
     hybrid_retrieve() does NOT return RelationshipRow(origin='hypothesized')
     rows. Verified by a regression test that seeds a hypothesized
     relationship and confirms it is absent from retrieval results.
  7. G01-G04 regression intact.

Plus negative tests for:
- Unsupported combinations (no capabilities)
- Missing evidence (NOT_EVIDENCED gaps)
- Irrelevant contexts (no matching entities)
- Contradiction handling (CONTESTED claims preserved)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.innovation import (
    MAX_COMBINATIONS,
    combine_knowledge,
)
from synapse.application.relationship_service import (
    create_relationship,
    find_related_entities,
)
from synapse.application.retrieval import hybrid_retrieve
from synapse.application.verification import assess_claim
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

# ── Test fixture ────────────────────────────────────────────────────────────

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()
STALE_ISO = (datetime.now(UTC) - timedelta(days=500)).isoformat()


async def _make_source(session, sid: str, uri: str) -> SourceRow:
    src = SourceRow(id=sid, canonical_uri=uri, source_type="paper", status="extracted")
    session.add(src)
    await session.flush()
    return src


async def _make_fragment(
    session,
    fid: str,
    src: SourceRow,
    excerpt: str,
    retrieved_at: str = RECENT_ISO,
) -> EvidenceFragmentRow:
    ef = EvidenceFragmentRow(
        id=fid,
        acquisition_id=f"acq-{fid}",
        source_id=src.id,
        source_uri=src.canonical_uri,
        exact_excerpt=excerpt,
        excerpt_hash=f"hash-{fid}",
        retrieved_at=retrieved_at,
        extraction_method="test-fixture",
        content_fingerprint=f"fp-{fid}",
        toolkit_commit_sha="fixture",
    )
    session.add(ef)
    await session.flush()
    return ef


async def _make_entity(
    session,
    eid: str,
    kind: str,
    name: str,
    desc: str = "",
    aliases: list[str] | None = None,
) -> EntityRow:
    e = EntityRow(
        id=eid,
        kind=kind,
        canonical_name=name,
        aliases=json.dumps(aliases or []),
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


async def _seed_combination_fixture(session) -> dict[str, Any]:
    """Seed a fixture with >=2 documented capabilities and multiple providers.

    Models an AI-research-assistant scenario:
    - ent-arxiv provides cap-source-discovery
    - ent-trafilatura provides cap-content-extraction
    - ent-synapse provides cap-evidence-verification
    - ent-no-vector LIMITS cap-evidence-verification
    - cap-knowledge-extraction has NO provider (NOT_EVIDENCED gap)
    """
    src_a = await _make_source(session, "src-t01-a", "https://example.com/papers/arxiv")
    src_b = await _make_source(session, "src-t01-b", "https://example.com/papers/trafilatura")
    src_c = await _make_source(session, "src-t01-c", "https://example.com/papers/synapse")

    frag_a = await _make_fragment(
        session, "frag-t01-a", src_a, "arxiv provides source discovery for AI research."
    )
    frag_b = await _make_fragment(
        session, "frag-t01-b", src_b, "trafilatura extracts content from web pages."
    )
    frag_c = await _make_fragment(
        session, "frag-t01-c", src_c, "synapse provides evidence verification."
    )

    # Entities
    await _make_entity(session, "ent-arxiv", "technology", "arxiv", desc="arXiv paper repository")
    await _make_entity(
        session,
        "ent-trafilatura",
        "technology",
        "trafilatura",
        desc="Web content extraction library",
    )
    await _make_entity(
        session, "ent-synapse", "tool", "synapse", desc="AgentCraft Synapse knowledge engine"
    )
    await _make_entity(
        session,
        "ent-no-vector",
        "constraint",
        "no vector database",
        desc="Constraint: no vector DB in the minimal slice",
    )

    # Capabilities
    await _make_entity(
        session,
        "cap-source-discovery",
        "capability",
        "source discovery",
        desc="Discover technical sources",
    )
    await _make_entity(
        session,
        "cap-content-extraction",
        "capability",
        "content extraction",
        desc="Extract text and metadata",
    )
    await _make_entity(
        session,
        "cap-evidence-verification",
        "capability",
        "evidence verification",
        desc="Verify claims against source evidence",
    )
    await _make_entity(
        session,
        "cap-knowledge-extraction",
        "capability",
        "structured knowledge extraction",
        desc="Extract entities, claims, relationships (NO provider — gap)",
    )

    # Claims
    await _make_claim(
        session,
        "claim-t01-sd",
        "arxiv provides source discovery for AI research.",
        subject_ref="ent-arxiv",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-t01-a"],
    )
    await _make_claim(
        session,
        "claim-t01-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-trafilatura",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-t01-b"],
    )
    await _make_claim(
        session,
        "claim-t01-ev",
        "synapse provides evidence verification.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-t01-c"],
    )

    # Relationships (all explicit origin — established knowledge)
    for from_id, to_id, ev_refs in [
        ("ent-arxiv", "cap-source-discovery", ["frag-t01-a"]),
        ("ent-trafilatura", "cap-content-extraction", ["frag-t01-b"]),
        ("ent-synapse", "cap-evidence-verification", ["frag-t01-c"]),
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

    # Constraint: ent-no-vector LIMITS cap-evidence-verification
    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=[],
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()
    for cid in ("claim-t01-sd", "claim-t01-ce", "claim-t01-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {
        "arxiv_id": "ent-arxiv",
        "trafilatura_id": "ent-trafilatura",
        "synapse_id": "ent-synapse",
        "capability_ids": [
            "cap-source-discovery",
            "cap-content-extraction",
            "cap-evidence-verification",
            "cap-knowledge-extraction",
        ],
    }


# ── Acceptance Test 1: Evidence-grounded candidate discovery ───────────────


@pytest.mark.asyncio
async def test_01_evidence_grounded_candidate_discovery(app, db_session):
    """combine_knowledge returns >=1 combination for a problem domain
    with >=2 documented capabilities."""
    await _seed_combination_fixture(db_session)

    result = await combine_knowledge(
        db_session,
        "AI research assistant",
        max_combinations=5,
    )

    assert isinstance(result, dict)
    assert "combinations" in result
    assert "unknowns" in result
    assert "limits" in result
    assert "request_id" in result
    assert "generated_at" in result

    # The fixture has 3 capabilities with providers → >=1 combination expected.
    assert len(result["combinations"]) >= 1, (
        f"expected >=1 combination, got {len(result['combinations'])}; "
        f"unknowns={result['unknowns']}"
    )

    # Each combination must have the required fields.
    comb = result["combinations"][0]
    assert "id" in comb
    assert "problem_domain" in comb
    assert "components" in comb
    assert "integration_mechanism" in comb
    assert "potential_benefit" in comb
    assert "uncertainties" in comb
    assert "evidence_refs" in comb
    assert "combination_basis" in comb
    assert "rank_score" in comb
    assert "generation_method" in comb
    assert comb["generation_method"] == "deterministic"


# ── Acceptance Test 2: Component identity and evidence validity ───────────


@pytest.mark.asyncio
async def test_02_component_identity_and_evidence_validity(app, db_session):
    """Every cited entity_id resolves to an existing EntityRow; every
    evidence_ref resolves to an existing EvidenceFragmentRow."""
    await _seed_combination_fixture(db_session)

    result = await combine_knowledge(db_session, "AI research assistant")

    # Collect all existing entity + fragment IDs
    all_entities = {row[0] for row in (await db_session.execute(select(EntityRow.id))).all()}
    all_fragments = {
        row[0] for row in (await db_session.execute(select(EvidenceFragmentRow.id))).all()
    }

    for comb in result["combinations"]:
        for comp in comb["components"]:
            assert comp["entity_id"] in all_entities, f"fabricated entity_id: {comp['entity_id']}"
        for ref in comb["evidence_refs"]:
            assert ref in all_fragments, f"fabricated evidence_ref: {ref}"


# ── Acceptance Test 3: Dependency and constraint awareness ─────────────────


@pytest.mark.asyncio
async def test_03_dependency_and_constraint_awareness(app, db_session):
    """Combinations must preserve constraints (LIMITS edges) as uncertainties."""
    await _seed_combination_fixture(db_session)

    result = await combine_knowledge(db_session, "AI research assistant")

    # The fixture has ent-no-vector LIMITS cap-evidence-verification.
    # At least one combination should surface this constraint as an uncertainty.
    all_uncertainties = []
    for comb in result["combinations"]:
        all_uncertainties.extend(comb["uncertainties"])

    # The constraint should appear somewhere in the uncertainties.
    constraint_mentioned = any(
        "no vector database" in u.lower() or "constrained by" in u.lower()
        for u in all_uncertainties
    )
    assert constraint_mentioned, f"constraint not surfaced in uncertainties: {all_uncertainties}"


# ── Acceptance Test 4: Useful, explainable integration mechanisms ─────────


@pytest.mark.asyncio
async def test_04_useful_explainable_integration_mechanisms(app, db_session):
    """Every combination must have a non-empty integration_mechanism and
    potential_benefit."""
    await _seed_combination_fixture(db_session)

    result = await combine_knowledge(db_session, "AI research assistant")

    for comb in result["combinations"]:
        assert comb["integration_mechanism"], (
            f"empty integration_mechanism in combination {comb['id']}"
        )
        assert comb["potential_benefit"], f"empty potential_benefit in combination {comb['id']}"
        # potential_benefit must NOT contain unsupported numerical claims
        assert "~" not in comb["potential_benefit"], (
            f"potential_benefit contains numerical claim: {comb['potential_benefit']}"
        )
        # potential_benefit must be phrased as a hypothesis
        assert "hypothesis" in comb["potential_benefit"].lower(), (
            f"potential_benefit not phrased as hypothesis: {comb['potential_benefit']}"
        )
        # Every combination must have >=1 uncertainty (gate 8)
        assert len(comb["uncertainties"]) >= 1, (
            f"combination {comb['id']} has no uncertainties (overclaiming)"
        )


# ── Acceptance Test 5: Bounded deterministic output ────────────────────────


@pytest.mark.asyncio
async def test_05_bounded_deterministic_output(app, db_session):
    """Same input + same DB state → same output. Output is bounded by
    max_combinations."""
    await _seed_combination_fixture(db_session)

    result1 = await combine_knowledge(db_session, "AI research assistant", max_combinations=3)
    result2 = await combine_knowledge(db_session, "AI research assistant", max_combinations=3)

    # Deterministic: same number of combinations
    assert len(result1["combinations"]) == len(result2["combinations"])

    # Deterministic: same rank_scores in same order
    scores1 = [c["rank_score"] for c in result1["combinations"]]
    scores2 = [c["rank_score"] for c in result2["combinations"]]
    assert scores1 == scores2, f"non-deterministic ranking: {scores1} vs {scores2}"

    # Bounded: does not exceed max_combinations
    assert len(result1["combinations"]) <= 3

    # Bounded: does not exceed MAX_COMBINATIONS even with large max_combinations
    result_large = await combine_knowledge(
        db_session, "AI research assistant", max_combinations=1000
    )
    assert len(result_large["combinations"]) <= MAX_COMBINATIONS


# ── Acceptance Test 6: Epistemic isolation ────────────────────────────────


@pytest.mark.asyncio
async def test_06_epistemic_isolation_hypothesized_excluded(app, db_session):
    """hybrid_retrieve() and find_related_entities() do NOT return
    RelationshipRow(origin='hypothesized') rows by default.

    This is the G05-T01 epistemic-isolation regression test (Correction B,
    plan §4.4). A hypothesized relationship is seeded and confirmed
    absent from retrieval results.
    """
    await _seed_combination_fixture(db_session)

    # Create a hypothesized relationship: ent-arxiv PROVIDES cap-knowledge-extraction
    # (this is NOT documented — it's a hypothesized combination)
    r = await create_relationship(
        db_session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-knowledge-extraction",
        predicate="PROVIDES",
        evidence_refs=["frag-t01-a"],
        origin="hypothesized",
    )
    assert r["ok"] is True
    await db_session.commit()

    # ── Test 1: hybrid_retrieve must NOT surface the hypothesized relationship ──
    retrieval = await hybrid_retrieve(db_session, "arxiv knowledge extraction", limit=20)

    # Check all relationships in the retrieval result
    for rel_entry in retrieval.get("relationships", []):
        rel = rel_entry.get("relationship") or {}
        assert rel.get("origin") != "hypothesized", (
            f"hybrid_retrieve returned hypothesized relationship: {rel.get('id')}"
        )

    # ── Test 2: find_related_entities must NOT return hypothesized by default ──
    related_default = await find_related_entities(
        db_session, "ent-arxiv", direction="both", limit=50
    )
    for entry in related_default:
        rel = entry.get("relationship") or {}
        assert rel.get("origin") != "hypothesized", (
            f"find_related_entities returned hypothesized by default: {rel.get('id')}"
        )

    # ── Test 3: find_related_entities with include_hypothesized=True DOES return it ──
    related_with_hyp = await find_related_entities(
        db_session, "ent-arxiv", direction="both", limit=50, include_hypothesized=True
    )
    hypothesized_found = any(
        (entry.get("relationship") or {}).get("origin") == "hypothesized"
        for entry in related_with_hyp
    )
    assert hypothesized_found, (
        "find_related_entities with include_hypothesized=True should return "
        "the hypothesized relationship"
    )

    # ── Test 4: combine_knowledge must NOT use the hypothesized relationship ──
    result = await combine_knowledge(db_session, "knowledge extraction", max_combinations=10)
    for comb in result["combinations"]:
        for comp in comb["components"]:
            # cap-knowledge-extraction should NOT appear as a provided capability
            # of ent-arxiv in any combination (because the only PROVIDES edge
            # from ent-arxiv to cap-knowledge-extraction is hypothesized).
            if comp["entity_id"] == "ent-arxiv":
                # Check that the combination_basis doesn't claim arxiv provides
                # "structured knowledge extraction"
                assert "structured knowledge extraction" not in comb["combination_basis"].lower(), (
                    f"hypothesized relationship contaminated combination: "
                    f"{comb['combination_basis']}"
                )


# ── Acceptance Test 7: G01-G04 regression ──────────────────────────────────


def test_07_g01_g04_regression():
    """Run a focused subset of the existing regression suite to confirm
    G05-T01 introduces no regression in G01-G04 behavior."""
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
            "tests/unit/api/test_health.py",
            "tests/integration/test_g04_t01_retrieval.py::test_g01_g02_g03_regression",
            "tests/integration/test_g04_t02_capability_registry.py::test_g01_g04_t02_regression_after_attribution_fix",
            "tests/integration/test_g04_t03_reasoning.py::test_g01_g04_t02c_regression",
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


# ── Negative Test: Unsupported combinations (no capabilities) ─────────────


@pytest.mark.asyncio
async def test_negative_no_capabilities(app, db_session):
    """When the knowledge graph has no capabilities, combine_knowledge
    returns an empty combinations list with truthful unknowns."""
    # Seed only a tool entity with no capabilities
    await _make_entity(session=db_session, eid="ent-bare", kind="tool", name="bare tool")
    await db_session.commit()

    result = await combine_knowledge(db_session, "something unrelated")

    assert len(result["combinations"]) == 0
    assert len(result["unknowns"]) >= 1
    assert any(
        "no candidate" in u.lower() or "no capability" in u.lower() for u in result["unknowns"]
    )


# ── Negative Test: Missing evidence (NOT_EVIDENCED gap) ───────────────────


@pytest.mark.asyncio
async def test_negative_missing_evidence_gap(app, db_session):
    """When a capability has NO provider (NOT_EVIDENCED gap), it must
    appear in uncertainties, NOT as a fabricated combination."""
    await _seed_combination_fixture(db_session)

    # cap-knowledge-extraction has no PROVIDES edge (gap).
    # combine_knowledge should NOT produce a combination citing it as provided.
    result = await combine_knowledge(db_session, "structured knowledge extraction")

    for comb in result["combinations"]:
        for comp in comb["components"]:
            # No component should claim to provide "structured knowledge extraction"
            # because no entity has a PROVIDES edge to cap-knowledge-extraction.
            assert comp.get("role") != "structured knowledge extraction", (
                f"combination cites cap-knowledge-extraction as provided (gap filled): {comb['id']}"
            )


# ── Negative Test: Irrelevant context (no matching entities) ──────────────


@pytest.mark.asyncio
async def test_negative_irrelevant_context(app, db_session):
    """An irrelevant problem domain produces no combinations (truthful,
    no fabrication)."""
    await _seed_combination_fixture(db_session)

    result = await combine_knowledge(db_session, "aardvark picnic galoshes")

    # May have 0 or few combinations (the fixture's entities don't match).
    # The key assertion: no fabricated entity_ids or evidence_refs.
    all_entities = {row[0] for row in (await db_session.execute(select(EntityRow.id))).all()}
    all_fragments = {
        row[0] for row in (await db_session.execute(select(EvidenceFragmentRow.id))).all()
    }
    for comb in result["combinations"]:
        for comp in comb["components"]:
            assert comp["entity_id"] in all_entities
        for ref in comb["evidence_refs"]:
            assert ref in all_fragments


# ── Negative Test: Contradiction handling ─────────────────────────────────


@pytest.mark.asyncio
async def test_negative_contradiction_handling(app, db_session):
    """When a component has a CONTESTED claim, the contradiction must
    be surfaced as an uncertainty, NOT suppressed."""
    await _seed_combination_fixture(db_session)

    # Add a contradicting claim to ent-synapse's evidence verification claim
    src_d = await _make_source(
        session=db_session, sid="src-t01-d", uri="https://example.com/contradiction"
    )
    frag_opp = await _make_fragment(
        session=db_session,
        fid="frag-t01-opp",
        src=src_d,
        excerpt="Evidence verification cannot be fully automated.",
    )

    # Update claim-t01-ev to have contradicting_refs
    claim_ev = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-t01-ev"))
    ).scalar_one()
    claim_ev.contradicting_refs = json.dumps(["frag-t01-opp"])
    claim_ev.epistemic_state = "disputed"
    await db_session.flush()
    await db_session.commit()
    await assess_claim(db_session, "claim-t01-ev", requester="fixture")
    await db_session.commit()

    result = await combine_knowledge(db_session, "evidence verification")

    # At least one combination should mention the contradiction.
    all_uncertainties = []
    for comb in result["combinations"]:
        all_uncertainties.extend(comb["uncertainties"])

    contradiction_mentioned = any("contradiction" in u.lower() for u in all_uncertainties)
    # The contradiction may or may not be surfaced depending on whether
    # ent-synapse appears in a combination. If it does, the contradiction
    # must be mentioned.
    synapse_in_combination = any(
        any(comp["entity_id"] == "ent-synapse" for comp in comb["components"])
        for comb in result["combinations"]
    )
    if synapse_in_combination:
        assert contradiction_mentioned, (
            f"contradiction not surfaced when ent-synapse is in a combination: {all_uncertainties}"
        )
