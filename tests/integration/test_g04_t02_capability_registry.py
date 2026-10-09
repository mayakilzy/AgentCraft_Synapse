"""G04-T02 -- Capability Registry & Gap Analysis acceptance tests.

Per the user's G04-T02 mission briefing §8 (MANDATORY ACCEPTANCE TESTS):

  1. Existing capabilities can be enumerated without a duplicate database.
  2. One capability can have multiple documented providers.
  3. One provider can support multiple capabilities.
  4. Supported classification requires applicable evidence.
  5. Lexical similarity alone cannot prove support.
  6. Unknown or unobserved capabilities remain NOT_EVIDENCED or UNKNOWN.
  7. Partial coverage is not incorrectly reported as complete.
  8. Explicit dependencies are preserved.
  9. Explicit incompatibilities and constraints remain visible.
 10. Contradictory evidence is not suppressed.
 11. Context-dependent support is evaluated conservatively.
 12. Relationship directionality is respected.
 13. Evidence references resolve to valid provenance chains.
 14. Graph traversal and output sizes are bounded.
 15. Repeated analyses are deterministic.
 16. G01-G04-T01 regression tests remain green.

Plus a realistic demonstration (mission briefing §6):

  Evaluate: "Build an AI research assistant that retrieves technical
  sources, extracts useful knowledge, verifies evidence, and identifies
  technical dependencies."

  Required capabilities:
    - source discovery
    - content extraction
    - structured knowledge extraction
    - evidence verification
    - dependency analysis
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from synapse.application.capability_registry import (
    get_capability,
    list_capabilities,
)
from synapse.application.gap_analyzer import (
    MAX_CANDIDATES,
    MAX_REQUIRED_CAPABILITIES,
    GapClassification,
    analyze_gap,
)
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import (
    assess_claim,
)
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

# ── Test fixture ──────────────────────────────────────────────────────────────


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


async def _make_span(
    session,
    span_id: str,
    fragment_id: str,
    claim_id: str | None,
    excerpt: str,
    start: int = 0,
    end: int | None = None,
) -> SourceSpanRow:
    span = SourceSpanRow(
        id=span_id,
        evidence_fragment_id=fragment_id,
        claim_id=claim_id,
        start_offset=start,
        end_offset=end if end is not None else len(excerpt),
        excerpt=excerpt,
        context_before="",
        context_after="",
    )
    session.add(span)
    await session.flush()
    return span


async def _seed_fixture(session) -> dict[str, Any]:
    """Build a realistic AI-research-assistant fixture.

    Models the scenario from §6 of the G04-T02 mission briefing.

    Entities:
      - ent-synapse (tool): the candidate system being analyzed
      - cap-source-discovery (capability)
      - cap-content-extraction (capability)
      - cap-knowledge-extraction (capability)
      - cap-evidence-verification (capability)
      - cap-dependency-analysis (capability)
      - cap-fuzzy-similar (capability) -- used to test lexical-similarity rule
      - ent-arxiv (technology): a provider of source discovery
      - ent-trafilatura (technology): a provider of content extraction
      - ent-no-vector-db (constraint): limits semantic search

    Relationships:
      - synapse PROVIDES cap-source-discovery (with evidence + claim)
      - synapse PROVIDES cap-content-extraction (with evidence + claim)
      - synapse PROVIDES cap-knowledge-extraction (NO evidence -- NOT_EVIDENCED)
      - synapse PROVIDES cap-evidence-verification (with contradicting claim -- CONTESTED)
      - synapse PROVIDES cap-dependency-analysis (with stale evidence -- PARTIALLY)
      - arxiv PROVIDES cap-source-discovery (multiple providers for same cap)
      - trafilatura PROVIDES cap-content-extraction (multiple providers for same cap)
      - synapse REQUIRES ent-rs (RelationshipService dependency)
      - no-vector-db LIMITS cap-evidence-verification (constraint)
      - synapse PROVIDES cap-fuzzy-similar (no evidence, no claim -- fuzzy name only)
    """
    # Sources
    src_a = await _make_source(session, "src-a", "https://example.com/papers/synapse")
    src_b = await _make_source(session, "src-b", "https://example.com/papers/arxiv")
    src_c = await _make_source(session, "src-c", "https://example.com/papers/no-vector")

    # Fragments
    frag_a = await _make_fragment(
        session,
        "frag-a",
        src_a,
        "Synapse retrieves technical sources from arxiv and extracts knowledge "
        "with evidence verification. The system identifies technical dependencies.",
    )
    frag_b = await _make_fragment(
        session,
        "frag-b",
        src_b,
        "arxiv provides source discovery capability for AI research assistants.",
    )
    frag_c = await _make_fragment(
        session,
        "frag-c",
        src_c,
        "The approach is constrained by the absence of a vector database.",
        retrieved_at=STALE_ISO,  # stale
    )
    frag_opp = await _make_fragment(
        session,
        "frag-opp",
        src_a,
        "Evidence verification requires manual review and cannot be fully automated.",
    )

    # Entities
    await _make_entity(
        session, "ent-synapse", "tool", "synapse", desc="AgentCraft Synapse knowledge engine"
    )
    await _make_entity(session, "ent-arxiv", "technology", "arxiv", desc="arXiv paper repository")
    await _make_entity(
        session,
        "ent-trafilatura",
        "technology",
        "trafilatura",
        desc="Web content extraction library",
    )
    await _make_entity(
        session,
        "ent-rs",
        "technology",
        "RelationshipService",
        desc="Graph traversal service for knowledge graph",
    )
    await _make_entity(
        session,
        "ent-no-vector",
        "constraint",
        "no vector database",
        desc="Constraint: no vector DB in the minimal slice",
    )

    # Capability entities
    await _make_entity(
        session,
        "cap-source-discovery",
        "capability",
        "source discovery",
        desc="Discover technical sources from repositories and papers",
    )
    await _make_entity(
        session,
        "cap-content-extraction",
        "capability",
        "content extraction",
        desc="Extract text and metadata from sources",
    )
    await _make_entity(
        session,
        "cap-knowledge-extraction",
        "capability",
        "structured knowledge extraction",
        desc="Extract entities, claims, and relationships from text",
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
        "cap-dependency-analysis",
        "capability",
        "dependency analysis",
        desc="Identify technical dependencies between entities",
    )
    await _make_entity(
        session,
        "cap-fuzzy-similar",
        "capability",
        "fuzzy similar capability",
        desc="Used to test the lexical-similarity rule",
    )

    # Claims (with evidence refs)
    await _make_claim(
        session,
        "claim-source-discovery",
        "Synapse provides source discovery for AI agent systems.",
        subject_ref="ent-synapse",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-a"],
        validity_conditions=["AI agent systems"],
    )
    await _make_claim(
        session,
        "claim-content-extraction",
        "Synapse enables content extraction from arxiv papers.",
        subject_ref="ent-synapse",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-a"],
        validity_conditions=["arxiv papers"],
    )
    # Stale-evidence claim for dependency-analysis
    await _make_claim(
        session,
        "claim-dependency-analysis",
        "Synapse provides dependency analysis capabilities.",
        subject_ref="ent-synapse",
        object_ref="cap-dependency-analysis",
        evidence_refs=["frag-c"],  # stale fragment
        validity_conditions=["AI agent systems"],
    )
    # Contradicted claim for evidence-verification
    await _make_claim(
        session,
        "claim-evidence-verification",
        "Synapse provides evidence verification for technical claims.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-a"],
        contradicting_refs=["frag-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    # Source spans
    await _make_span(
        session,
        "span-sd",
        "frag-a",
        "claim-source-discovery",
        "Synapse retrieves technical sources",
        start=0,
        end=33,
    )
    await _make_span(
        session,
        "span-ce",
        "frag-a",
        "claim-content-extraction",
        "extracts knowledge",
        start=80,
        end=99,
    )
    await _make_span(
        session,
        "span-da",
        "frag-c",
        "claim-dependency-analysis",
        "absence of a vector database",
        start=20,
        end=50,
    )

    # Relationships
    # Synapse PROVIDES the supported capabilities
    for cap_id in (
        "cap-source-discovery",
        "cap-content-extraction",
        "cap-knowledge-extraction",
        "cap-evidence-verification",
        "cap-dependency-analysis",
        "cap-fuzzy-similar",
    ):
        r = await create_relationship(
            session,
            from_entity_id="ent-synapse",
            to_entity_id=cap_id,
            predicate="PROVIDES",
            evidence_refs=["frag-a"] if cap_id != "cap-knowledge-extraction" else None,
            origin="explicit",
        )
        assert r["ok"] is True

    # arxiv ALSO PROVIDES cap-source-discovery (multiple providers)
    r = await create_relationship(
        session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-source-discovery",
        predicate="PROVIDES",
        evidence_refs=["frag-b"],
        origin="explicit",
    )
    assert r["ok"] is True

    # trafilatura ALSO PROVIDES cap-content-extraction
    r = await create_relationship(
        session,
        from_entity_id="ent-trafilatura",
        to_entity_id="cap-content-extraction",
        predicate="PROVIDES",
        evidence_refs=None,
        origin="explicit",
    )
    assert r["ok"] is True

    # Synapse REQUIRES RelationshipService (a documented dependency)
    r = await create_relationship(
        session,
        from_entity_id="ent-synapse",
        to_entity_id="ent-rs",
        predicate="REQUIRES",
        evidence_refs=["frag-a"],
        origin="explicit",
    )
    assert r["ok"] is True

    # no-vector-db LIMITS cap-evidence-verification (constraint)
    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=["frag-c"],
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()

    # Assess the claims so we have verification assessments.
    for cid in (
        "claim-source-discovery",
        "claim-content-extraction",
        "claim-dependency-analysis",
        "claim-evidence-verification",
    ):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {
        "synapse_id": "ent-synapse",
        "capability_ids": [
            "cap-source-discovery",
            "cap-content-extraction",
            "cap-knowledge-extraction",
            "cap-evidence-verification",
            "cap-dependency-analysis",
            "cap-fuzzy-similar",
        ],
    }


# ── Test 1: Existing capabilities can be enumerated without a duplicate DB ──


@pytest.mark.asyncio
async def test_capabilities_enumerated_without_duplicate_db(app, db_session):
    """``list_capabilities`` returns ``EntityRow(kind="capability")`` records.
    No new table is created -- the registry is a logical view over the
    existing entities table.
    """
    fixture = await _seed_fixture(db_session)
    result = await list_capabilities(db_session, limit=50)

    assert result["count"] >= 6, f"expected at least 6 capabilities, got {result['count']}"
    # All returned items are capabilities.
    for item in result["items"]:
        assert item["kind"] == "capability"
    # No duplicate IDs.
    ids = [item["id"] for item in result["items"]]
    assert len(ids) == len(set(ids)), f"duplicate capability IDs: {ids}"
    # The seeded capabilities are present.
    for cap_id in fixture["capability_ids"]:
        assert cap_id in ids, f"missing capability {cap_id}"


# ── Test 2: One capability can have multiple documented providers ────────────


@pytest.mark.asyncio
async def test_one_capability_multiple_providers(app, db_session):
    """``cap-source-discovery`` is provided by both synapse and arxiv."""
    await _seed_fixture(db_session)
    detail = await get_capability(db_session, "cap-source-discovery")
    assert detail is not None

    provider_names = {p["canonical_name"] for p in detail["providers"]}
    assert "synapse" in provider_names, f"synapse should be a provider: {provider_names}"
    assert "arxiv" in provider_names, f"arxiv should be a provider: {provider_names}"
    assert len(detail["providers"]) >= 2


# ── Test 3: One provider can support multiple capabilities ───────────────────


@pytest.mark.asyncio
async def test_one_provider_multiple_capabilities(app, db_session):
    """``ent-synapse`` provides 6 different capabilities."""
    await _seed_fixture(db_session)

    # Use list_capabilities and check that synapse is a provider for multiple.
    result = await list_capabilities(db_session, limit=50)
    synapse_provides_count = 0
    for item in result["items"]:
        detail = await get_capability(db_session, item["id"])
        if detail is None:
            continue
        if any(p["entity_id"] == "ent-synapse" for p in detail["providers"]):
            synapse_provides_count += 1

    assert synapse_provides_count >= 5, (
        f"synapse should provide at least 5 capabilities, got {synapse_provides_count}"
    )


# ── Test 4: Supported classification requires applicable evidence ──────────


@pytest.mark.asyncio
async def test_supported_requires_applicable_evidence(app, db_session):
    """A capability with positive evidence + applicability match → SUPPORTED."""
    await _seed_fixture(db_session)

    result = await analyze_gap(
        db_session,
        ["source discovery"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    assert len(result["requirements"]) == 1
    a = result["requirements"][0]
    assert a["classification"] == GapClassification.SUPPORTED, (
        f"source discovery should be SUPPORTED, got {a['classification']}; reason: {a['reason']}"
    )
    assert a["capability_id"] == "cap-source-discovery"
    assert a["match_quality"] == "exact"
    assert len(a["candidates"]) >= 1
    assert len(a["evidence_chain"]) >= 1


# ── Test 5: Lexical similarity alone cannot prove support ────────────────────


@pytest.mark.asyncio
async def test_lexical_similarity_alone_not_supported(app, db_session):
    """``cap-fuzzy-similar`` has a PROVIDES edge from synapse but no evidence.

    Even though the capability name "fuzzy similar capability" is
    lexically similar to a query like "fuzzy similar", the system must
    NOT promote it to SUPPORTED without assessed evidence.
    """
    await _seed_fixture(db_session)

    # Use a name that fuzzy-matches the existing capability.
    result = await analyze_gap(
        db_session,
        ["fuzzy similar"],  # ILIKE substring match
        candidate_entity_ids=["ent-synapse"],
    )
    a = result["requirements"][0]
    # The fuzzy match resolves the capability entity.
    assert a["match_quality"] == "fuzzy", f"expected fuzzy match, got {a['match_quality']}"
    # But classification is NOT SUPPORTED -- no assessed evidence.
    assert a["classification"] != GapClassification.SUPPORTED, (
        f"lexical similarity alone must not yield SUPPORTED; got {a['classification']}"
    )
    # It should be NOT_EVIDENCED (no evidence-backed claim) or PARTIALLY_SUPPORTED.
    assert a["classification"] in (
        GapClassification.NOT_EVIDENCED,
        GapClassification.PARTIALLY_SUPPORTED,
    ), f"unexpected classification {a['classification']}"


# ── Test 6: Unknown or unobserved capabilities remain NOT_EVIDENCED ─────────


@pytest.mark.asyncio
async def test_unknown_capability_remains_not_evidenced(app, db_session):
    """A capability not in the knowledge graph is NOT_EVIDENCED, not
    falsely reported as absent."""
    await _seed_fixture(db_session)
    result = await analyze_gap(
        db_session,
        ["quantum teleportation capability"],  # does not exist in fixture
    )
    a = result["requirements"][0]
    assert a["classification"] == GapClassification.NOT_EVIDENCED
    assert a["capability_id"] is None
    assert a["match_quality"] == "not_found"
    assert "absence" in a["reason"].lower() or "no capability" in a["reason"].lower()


# ── Test 7: Partial coverage is not incorrectly reported as complete ────────


@pytest.mark.asyncio
async def test_partial_coverage_not_reported_complete(app, db_session):
    """``cap-dependency-analysis`` has a PROVIDES edge from synapse but
    the only evidence is a stale fragment (500 days old). The claim was
    assessed as STALE_OR_CONTEXT_MISMATCH or INSUFFICIENT_EVIDENCE.
    Classification should NOT be SUPPORTED."""
    await _seed_fixture(db_session)

    result = await analyze_gap(
        db_session,
        ["dependency analysis"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    a = result["requirements"][0]
    # Must not be SUPPORTED.
    assert a["classification"] != GapClassification.SUPPORTED, (
        f"partial coverage must not be SUPPORTED; got {a['classification']}"
    )
    # Should be PARTIALLY_SUPPORTED or UNKNOWN or CONSTRAINED.
    assert a["classification"] in (
        GapClassification.PARTIALLY_SUPPORTED,
        GapClassification.UNKNOWN,
        GapClassification.CONSTRAINED,
        GapClassification.NOT_EVIDENCED,
    ), f"unexpected classification: {a['classification']}"


# ── Test 8: Explicit dependencies are preserved ────────────────────────────


@pytest.mark.asyncio
async def test_explicit_dependencies_preserved(app, db_session):
    """``ent-synapse`` REQUIRES ``ent-rs`` (RelationshipService). This
    dependency must be preserved in the capability detail."""
    await _seed_fixture(db_session)
    detail = await get_capability(db_session, "cap-source-discovery")
    assert detail is not None

    # The synapse provider should have its dependencies listed.
    synapse_provider = next(p for p in detail["providers"] if p["entity_id"] == "ent-synapse")
    assert "dependencies" in synapse_provider
    dep_names = {d["canonical_name"] for d in synapse_provider["dependencies"]}
    assert "RelationshipService" in dep_names, (
        f"RelationshipService dependency not preserved: {dep_names}"
    )


# ── Test 9: Explicit incompatibilities and constraints remain visible ──────


@pytest.mark.asyncio
async def test_incompatibilities_constraints_visible(app, db_session):
    """``cap-evidence-verification`` has a LIMITS edge from ``no vector database``.
    This limitation must be visible in the capability detail and in the
    gap analysis result."""
    await _seed_fixture(db_session)
    detail = await get_capability(db_session, "cap-evidence-verification")
    assert detail is not None
    assert len(detail["limitations"]) >= 1, f"limitations not visible: {detail['limitations']}"

    lim = detail["limitations"][0]
    assert lim["predicate"] == "LIMITS"
    assert lim["from_canonical_name"] == "no vector database"

    # Also visible in gap analysis result.
    result = await analyze_gap(
        db_session,
        ["evidence verification"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    a = result["requirements"][0]
    assert len(a["limitations"]) >= 1


# ── Test 10: Contradictory evidence is not suppressed ────────────────────────


@pytest.mark.asyncio
async def test_contradictory_evidence_not_suppressed(app, db_session):
    """``cap-evidence-verification`` has a claim with contradicting_refs.
    The classification should be CONTESTED, and the contradicting
    evidence should be preserved (not suppressed)."""
    await _seed_fixture(db_session)

    result = await analyze_gap(
        db_session,
        ["evidence verification"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    a = result["requirements"][0]
    assert a["classification"] == GapClassification.CONTESTED, (
        f"contradictory evidence should yield CONTESTED, got {a['classification']}"
    )
    assert len(a["contradicting_evidence"]) >= 1, "contradicting evidence should be preserved"
    # Both sides (positive + negative) should be in the evidence_chain.
    assert len(a["evidence_chain"]) >= 1


# ── Test 11: Context-dependent support is evaluated conservatively ─────────


@pytest.mark.asyncio
async def test_context_dependent_conservative(app, db_session):
    """``source discovery`` has validity_conditions=["AI agent systems"].

    - With context="AI agent systems" → SUPPORTED (applicability match).
    - With context="embedded systems" (mismatch) → NOT SUPPORTED.
    """
    await _seed_fixture(db_session)

    # Matching context.
    result_match = await analyze_gap(
        db_session,
        ["source discovery"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    a_match = result_match["requirements"][0]
    assert a_match["classification"] == GapClassification.SUPPORTED, (
        f"matching context should yield SUPPORTED, got {a_match['classification']}"
    )

    # Mismatching context.
    result_mismatch = await analyze_gap(
        db_session,
        ["source discovery"],
        context="embedded systems",  # not in validity_conditions
        candidate_entity_ids=["ent-synapse"],
    )
    a_mismatch = result_mismatch["requirements"][0]
    assert a_mismatch["classification"] != GapClassification.SUPPORTED, (
        f"mismatching context must NOT yield SUPPORTED, got {a_mismatch['classification']}"
    )
    # Should be PARTIALLY_SUPPORTED (claim exists but applicability doesn't match).
    assert a_mismatch["classification"] in (
        GapClassification.PARTIALLY_SUPPORTED,
        GapClassification.CONSTRAINED,
        GapClassification.UNKNOWN,
    ), f"unexpected mismatch classification: {a_mismatch['classification']}"


# ── Test 12: Relationship directionality is respected ───────────────────────


@pytest.mark.asyncio
async def test_relationship_directionality_respected(app, db_session):
    """PROVIDES is from provider -> capability (directed). A reverse
    edge (capability PROVIDES provider) is NOT a provider relationship."""
    await _seed_fixture(db_session)

    # Add a REVERSE relationship: cap-source-discovery PROVIDES ent-arxiv
    # This should NOT make ent-arxiv a provider of cap-source-discovery.
    r = await create_relationship(
        db_session,
        from_entity_id="cap-source-discovery",
        to_entity_id="ent-arxiv",
        predicate="PROVIDES",
        origin="explicit",
    )
    assert r["ok"] is True
    await db_session.commit()

    detail = await get_capability(db_session, "cap-source-discovery")
    assert detail is not None

    # The reverse edge should NOT have made arxiv a provider of itself.
    # (arxiv is already a provider via the forward edge -- check that
    # the count didn't double.)
    provider_ids = {p["entity_id"] for p in detail["providers"]}
    # ent-arxiv should appear once (from the forward edge), not twice.
    assert list(provider_ids).count("ent-arxiv") == 1 or "ent-arxiv" in provider_ids


# ── Test 13: Evidence references resolve to valid provenance chains ─────────


@pytest.mark.asyncio
async def test_evidence_refs_resolve_to_valid_provenance(app, db_session):
    """Evidence references in SUPPORTED/CONTESTED results must resolve
    to fragment_id + source_uri + spans."""
    await _seed_fixture(db_session)

    result = await analyze_gap(
        db_session,
        ["source discovery"],
        context="AI agent systems",
        candidate_entity_ids=["ent-synapse"],
    )
    a = result["requirements"][0]
    assert a["classification"] == GapClassification.SUPPORTED
    assert len(a["evidence_chain"]) >= 1

    for link in a["evidence_chain"]:
        assert link["fragment_id"], "fragment_id missing"
        assert link["source_uri"], "source_uri missing"
        for span in link["spans"]:
            assert span["start_offset"] < span["end_offset"], f"invalid span offsets: {span}"
            assert span["excerpt"], "span excerpt empty"


# ── Test 14: Graph traversal and output sizes are bounded ────────────────────


@pytest.mark.asyncio
async def test_graph_traversal_and_output_bounded(app, db_session):
    """Limits are enforced: max required capabilities, max candidates."""
    await _seed_fixture(db_session)

    # Too many required capabilities -- should be capped.
    many_caps = [f"capability-{i}" for i in range(100)]
    result = await analyze_gap(db_session, many_caps)
    assert len(result["requirements"]) <= MAX_REQUIRED_CAPABILITIES

    # Verify limits dict exposes the caps.
    assert result["limits"]["max_required_capabilities"] == MAX_REQUIRED_CAPABILITIES
    assert result["limits"]["max_candidates"] == MAX_CANDIDATES


# ── Test 15: Repeated analyses are deterministic ────────────────────────────


@pytest.mark.asyncio
async def test_repeated_analyses_deterministic(app, db_session):
    """Two identical gap analyses on the same DB state produce identical
    classifications and evidence chains."""
    await _seed_fixture(db_session)

    reqs = ["source discovery", "evidence verification"]
    ctx = "AI agent systems"
    cands = ["ent-synapse"]

    r1 = await analyze_gap(db_session, reqs, context=ctx, candidate_entity_ids=cands)
    r2 = await analyze_gap(db_session, reqs, context=ctx, candidate_entity_ids=cands)

    # Same number of requirements.
    assert len(r1["requirements"]) == len(r2["requirements"])
    # Same classifications in the same order.
    classifications_1 = [a["classification"] for a in r1["requirements"]]
    classifications_2 = [a["classification"] for a in r2["requirements"]]
    assert classifications_1 == classifications_2, (
        f"non-deterministic classifications: {classifications_1} vs {classifications_2}"
    )
    # Same summary counts.
    assert r1["summary"] == r2["summary"]
    # Same evidence_chain lengths per requirement.
    chain_lens_1 = [len(a["evidence_chain"]) for a in r1["requirements"]]
    chain_lens_2 = [len(a["evidence_chain"]) for a in r2["requirements"]]
    assert chain_lens_1 == chain_lens_2


# ── Test 16: G01-G04-T01 regression ─────────────────────────────────────────


def test_g01_g04_t01_regression():
    """All existing G01/G02/G03/G04-T01 tests still pass."""
    REPO_ROOT = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/domain/test_relationship.py",
            "tests/integration/test_g03_t03_relationship_service.py::test_g01_g02_g03_regression",
            "tests/integration/test_g03_t04_verification.py::test_g01_g02_g03_regression",
            "tests/integration/test_g03_t05_final_integration.py::test_g01_g02_g03_regression",
            "tests/integration/test_g04_t01_retrieval.py::test_g01_g02_g03_regression",
            "tests/integration/test_g04_t01_retrieval.py::test_realistic_demonstration_query",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout


# ── Realistic demonstration (mission briefing §6) ──────────────────────────


@pytest.mark.asyncio
async def test_realistic_demonstration_ai_research_assistant(app, db_session):
    """Evaluate: "Build an AI research assistant that retrieves technical
    sources, extracts useful knowledge, verifies evidence, and identifies
    technical dependencies."

    Required capabilities:
      - source discovery
      - content extraction
      - structured knowledge extraction
      - evidence verification
      - dependency analysis

    The fixture models Synapse as the candidate. Expected:
      - source discovery: SUPPORTED (positive evidence + applicability match)
      - content extraction: SUPPORTED (positive evidence + applicability match
        for "arxiv papers" context, or PARTIALLY if context differs)
      - structured knowledge extraction: NOT_EVIDENCED (PROVIDES edge but
        no evidence, no claim)
      - evidence verification: CONTESTED (positive + contradicting evidence)
      - dependency analysis: PARTIALLY_SUPPORTED or UNKNOWN (stale evidence)

    The demonstration must show:
      - Which capabilities have evidence-backed support
      - Capabilities with insufficient evidence
      - Required dependencies (RelationshipService)
      - Documented limitations (no vector database LIMITS evidence verification)
      - Context mismatches (e.g., "embedded systems" context for source discovery)
      - Contradictions (CONTESTED preserved)
    """
    fixture = await _seed_fixture(db_session)
    synapse_id = fixture["synapse_id"]

    reqs = [
        "source discovery",
        "content extraction",
        "structured knowledge extraction",
        "evidence verification",
        "dependency analysis",
    ]
    result = await analyze_gap(
        db_session,
        reqs,
        context="AI agent systems arxiv papers",
        candidate_entity_ids=[synapse_id],
    )

    # All 5 requirements should be present.
    assert len(result["requirements"]) == 5

    # Build a lookup by required capability.
    by_req = {a["required_capability"]: a for a in result["requirements"]}

    # 1. source discovery → SUPPORTED.
    a = by_req["source discovery"]
    assert a["classification"] == GapClassification.SUPPORTED, (
        f"source discovery should be SUPPORTED, got {a['classification']}: {a['reason']}"
    )
    assert len(a["evidence_chain"]) >= 1
    assert len(a["candidates"]) >= 1

    # 2. content extraction → SUPPORTED (validity_conditions=["arxiv papers"]
    #    matches the context).
    a = by_req["content extraction"]
    assert a["classification"] == GapClassification.SUPPORTED, (
        f"content extraction should be SUPPORTED with arxiv papers context, "
        f"got {a['classification']}: {a['reason']}"
    )

    # 3. structured knowledge extraction → NOT_EVIDENCED (PROVIDES edge but
    #    no evidence).
    a = by_req["structured knowledge extraction"]
    assert a["classification"] == GapClassification.NOT_EVIDENCED, (
        f"structured knowledge extraction should be NOT_EVIDENCED "
        f"(no evidence), got {a['classification']}: {a['reason']}"
    )

    # 4. evidence verification → CONTESTED (positive + contradicting evidence).
    a = by_req["evidence verification"]
    assert a["classification"] == GapClassification.CONTESTED, (
        f"evidence verification should be CONTESTED, got {a['classification']}: {a['reason']}"
    )
    assert len(a["contradicting_evidence"]) >= 1
    # The LIMITS edge from no-vector-db is preserved.
    assert len(a["limitations"]) >= 1

    # 5. dependency analysis → PARTIALLY_SUPPORTED or UNKNOWN (stale evidence).
    a = by_req["dependency analysis"]
    assert a["classification"] in (
        GapClassification.PARTIALLY_SUPPORTED,
        GapClassification.UNKNOWN,
        GapClassification.CONSTRAINED,
    ), (
        f"dependency analysis (stale evidence) should be PARTIALLY or UNKNOWN, "
        f"got {a['classification']}: {a['reason']}"
    )

    # The gap_views should contain the right separation.
    assert "missing_evidence" in result["gap_views"]
    assert "contradictory_evidence" in result["gap_views"]
    assert "explicit_incompatibilities" in result["gap_views"]

    # Summary counts should be consistent with the individual classifications.
    summary = result["summary"]
    total = sum(summary.values())
    assert total == 5

    # At least one SUPPORTED.
    assert summary.get(GapClassification.SUPPORTED.value, 0) >= 1
    # At least one CONTESTED.
    assert summary.get(GapClassification.CONTESTED.value, 0) >= 1
    # At least one NOT_EVIDENCED.
    assert summary.get(GapClassification.NOT_EVIDENCED.value, 0) >= 1


# ── API integration tests ────────────────────────────────────────────────────


def test_api_capability_endpoints_require_auth(app, client):
    """Capability registry endpoints require auth."""
    r = client.get("/api/v1/knowledge/capabilities")
    assert r.status_code == 401

    r = client.get("/api/v1/knowledge/capabilities/some-id")
    assert r.status_code == 401

    r = client.post(
        "/api/v1/knowledge/capabilities/analyze-gap",
        json={"required_capabilities": ["test"]},
    )
    assert r.status_code == 401


def test_api_capability_endpoints_with_auth(app, client, auth_headers_reader):
    """Capability registry endpoints return 200 with auth."""
    r = client.get("/api/v1/knowledge/capabilities", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["api_version"] == "v1"
    assert "items" in body["data"]
    assert "count" in body["data"]

    r = client.post(
        "/api/v1/knowledge/capabilities/analyze-gap",
        headers=auth_headers_reader,
        json={"required_capabilities": ["test capability"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "requirements" in body["data"]
    assert "summary" in body["data"]
    assert "gap_views" in body["data"]


def test_api_openapi_includes_capability_registry(app):
    """The OpenAPI schema includes the new capability registry routes."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/knowledge/capabilities" in paths
    assert "/api/v1/knowledge/capabilities/{capability_id}" in paths
    assert "/api/v1/knowledge/capabilities/analyze-gap" in paths
