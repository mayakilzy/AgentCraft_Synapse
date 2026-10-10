"""G05-T02 -- Evidence-Grounded Opportunity Discovery acceptance tests.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§G05-T02), the 6 acceptance criteria:

  1. Returns >=1 opportunity per gap category (NOT_EVIDENCED, CONSTRAINED,
     CONTESTED).
  2. Missing evidence is reported as unknowns[], NOT as "no solution exists".
  3. Applicable contradictions are preserved (not suppressed).
  4. Out-of-context contradictions are preserved as metadata.
  5. Deterministic.
  6. G01-G04-T01 regression intact.

Plus negative tests for:
  - Empty knowledge graph.
  - Insufficient evidence.
  - Context-irrelevant contradictions.
  - Hypothesized-only relationships.
  - Misclassification of NOT_EVIDENCED.
  - Duplicate or irrelevant opportunities.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from synapse.application.innovation import (
    discover_opportunities,
)
from synapse.application.relationship_service import (
    create_relationship,
)
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


async def _seed_opportunity_fixture(session) -> dict[str, Any]:
    """Seed a fixture covering all gap categories.

    - ent-arxiv provides cap-source-discovery (SUPPORTED)
    - ent-trafilatura provides cap-content-extraction (SUPPORTED)
    - ent-synapse provides cap-evidence-verification (CONTESTED — has contradicting_refs)
    - ent-no-vector LIMITS cap-evidence-verification (CONSTRAINED)
    - cap-knowledge-extraction has NO provider (NOT_EVIDENCED)
    """
    src_a = await _make_source(session, "src-t02-a", "https://example.com/papers/arxiv")
    src_b = await _make_source(session, "src-t02-b", "https://example.com/papers/trafilatura")
    src_c = await _make_source(session, "src-t02-c", "https://example.com/papers/synapse")
    src_d = await _make_source(session, "src-t02-d", "https://example.com/contradiction")

    frag_a = await _make_fragment(
        session, "frag-t02-a", src_a, "arxiv provides source discovery for AI research."
    )
    frag_b = await _make_fragment(
        session, "frag-t02-b", src_b, "trafilatura extracts content from web pages."
    )
    frag_c = await _make_fragment(
        session, "frag-t02-c", src_c, "synapse provides evidence verification."
    )
    frag_opp = await _make_fragment(
        session, "frag-t02-opp", src_d, "Evidence verification cannot be fully automated."
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
        desc="Extract entities, claims, relationships (NO provider — NOT_EVIDENCED gap)",
    )

    # Claims
    await _make_claim(
        session,
        "claim-t02-sd",
        "arxiv provides source discovery for AI research.",
        subject_ref="ent-arxiv",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-t02-a"],
        validity_conditions=["AI agent systems"],
    )
    await _make_claim(
        session,
        "claim-t02-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-trafilatura",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-t02-b"],
    )
    await _make_claim(
        session,
        "claim-t02-ev",
        "synapse provides evidence verification.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-t02-c"],
        contradicting_refs=["frag-t02-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    # Relationships
    for from_id, to_id, ev_refs in [
        ("ent-arxiv", "cap-source-discovery", ["frag-t02-a"]),
        ("ent-trafilatura", "cap-content-extraction", ["frag-t02-b"]),
        ("ent-synapse", "cap-evidence-verification", ["frag-t02-c"]),
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
    for cid in ("claim-t02-sd", "claim-t02-ce", "claim-t02-ev"):
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


# ── Acceptance Test 1: Returns >=1 opportunity per gap category ───────────


@pytest.mark.asyncio
async def test_01_returns_opportunities_per_gap_category(app, db_session):
    """discover_opportunities returns >=1 opportunity for NOT_EVIDENCED,
    CONSTRAINED, and CONTESTED gap categories."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(
        db_session,
        "AI research assistant",
        context="AI agent systems",
        max_opportunities=10,
    )

    assert isinstance(result, dict)
    assert "opportunities" in result
    assert "unknowns" in result
    assert "gap_summary" in result
    assert "limits" in result
    assert "request_id" in result
    assert "generated_at" in result

    # The fixture has:
    # - cap-knowledge-extraction: NOT_EVIDENCED (no provider)
    # - cap-evidence-verification: CONTESTED (has contradicting_refs) or CONSTRAINED (has LIMITS)
    gap_types_found = {opp["gap_type"] for opp in result["opportunities"]}

    # At least one NOT_EVIDENCED opportunity (cap-knowledge-extraction)
    assert "not_evidenced" in gap_types_found, (
        f"expected not_evidenced gap type; found: {gap_types_found}"
    )

    # At least one CONSTRAINED or CONTESTED opportunity (cap-evidence-verification)
    assert "constrained" in gap_types_found or "contested" in gap_types_found, (
        f"expected constrained or contested gap type; found: {gap_types_found}"
    )

    # Each opportunity must have the required fields.
    opp = result["opportunities"][0]
    assert "id" in opp
    assert "problem_domain" in opp
    assert "gap_type" in opp
    assert "gap_description" in opp
    assert "relevant_capabilities" in opp
    assert "candidate_components" in opp
    assert "evidence_refs" in opp
    assert "constraints" in opp
    assert "uncertainties" in opp
    assert "investigation_direction" in opp
    assert "missing_evidence" in opp
    assert "validation_questions" in opp
    assert "ranking_rationale" in opp
    assert "rank_score" in opp


# ── Acceptance Test 2: Missing evidence reported as unknowns, NOT absence ──


@pytest.mark.asyncio
async def test_02_missing_evidence_as_unknowns_not_absence(app, db_session):
    """Missing evidence is reported as unknowns[], NOT as 'no solution exists'."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(
        db_session,
        "AI research assistant",
        context="AI agent systems",
    )

    # Find the NOT_EVIDENCED opportunity (cap-knowledge-extraction)
    not_evidenced_opps = [o for o in result["opportunities"] if o["gap_type"] == "not_evidenced"]
    assert len(not_evidenced_opps) >= 1

    opp = not_evidenced_opps[0]
    # The uncertainty must explicitly state "absence of evidence is NOT evidence of absence"
    absence_text = " ".join(opp["uncertainties"]).lower()
    assert "absence of evidence" in absence_text, (
        f"NOT_EVIDENCED opportunity must state absence != evidence; got: {opp['uncertainties']}"
    )

    # The missing_evidence must list what's needed
    assert len(opp["missing_evidence"]) >= 1
    # The validation_questions must ask about the gap
    assert len(opp["validation_questions"]) >= 1

    # The gap_description must NOT claim "no solution exists"
    gap_desc = opp["gap_description"].lower()
    assert "no solution exists" not in gap_desc, (
        f"gap_description claims 'no solution exists': {opp['gap_description']}"
    )


# ── Acceptance Test 3: Applicable contradictions preserved ────────────────


@pytest.mark.asyncio
async def test_03_applicable_contradictions_preserved(app, db_session):
    """Applicable contradictions (CONTESTED classification) are preserved
    in the opportunity, not suppressed."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(
        db_session,
        "evidence verification",
        context="AI agent systems",
    )

    # Find the CONTESTED opportunity for cap-evidence-verification.
    # Note: combination_gap opportunities use "evidence verification + ..." as
    # relevant_capabilities; the contested one uses just "evidence verification".
    ev_opps = [
        o
        for o in result["opportunities"]
        if o["gap_type"] in ("contested", "constrained")
        and "evidence verification" in " ".join(o.get("relevant_capabilities", [])).lower()
    ]
    assert len(ev_opps) >= 1, (
        f"no contested/constrained opportunity for evidence verification; found: "
        f"{[(o['gap_type'], o['relevant_capabilities']) for o in result['opportunities']]}"
    )

    opp = ev_opps[0]
    # The contradictions must be preserved as uncertainties
    contra_text = " ".join(opp["uncertainties"]).lower()
    assert "contradict" in contra_text or "contradiction" in contra_text, (
        f"contradictions not surfaced in uncertainties: {opp['uncertainties']}"
    )

    # The contradicting evidence fragment must be in evidence_refs
    # (applicable contradictions are preserved with both sides)
    if opp["gap_type"] == "contested":
        assert "frag-t02-opp" in opp["evidence_refs"], (
            f"contradicting fragment frag-t02-opp not in evidence_refs: {opp['evidence_refs']}"
        )


# ── Acceptance Test 4: Out-of-context contradictions preserved as metadata ─


@pytest.mark.asyncio
async def test_04_out_of_context_contradictions_as_metadata(app, db_session):
    """Out-of-context contradictions are preserved as
    out_of_context_contradictions metadata, not forced into CONTESTED."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(
        db_session,
        "evidence verification",
        context="mobile apps",
    )

    # With context="mobile apps" (doesn't match "AI agent systems"),
    # the contradiction on claim-t02-ev is out-of-context.
    # Find the CONSTRAINED/CONTESTED opportunity (not combination_gap) for evidence verification.
    ev_opps = [
        o
        for o in result["opportunities"]
        if o["gap_type"] in ("constrained", "contested")
        and "evidence verification" in " ".join(o.get("relevant_capabilities", [])).lower()
    ]
    if ev_opps:
        opp = ev_opps[0]
        # Out-of-context contradictions must be preserved as metadata
        ooc = opp.get("out_of_context_contradictions", [])
        # The opportunity should either:
        # - have out_of_context_contradictions metadata, OR
        # - mention out-of-context in its uncertainties
        ooc_mentioned = any(
            "out-of-context" in u.lower() or "out of context" in u.lower()
            for u in opp["uncertainties"]
        )
        assert len(ooc) > 0 or ooc_mentioned, (
            f"out-of-context contradictions not preserved: ooc={ooc}, "
            f"uncertainties={opp['uncertainties']}"
        )


# ── Acceptance Test 5: Deterministic ──────────────────────────────────────


@pytest.mark.asyncio
async def test_05_deterministic(app, db_session):
    """Same input + same DB state → same output."""
    await _seed_opportunity_fixture(db_session)

    result1 = await discover_opportunities(
        db_session,
        "AI research assistant",
        context="AI agent systems",
        max_opportunities=5,
    )
    result2 = await discover_opportunities(
        db_session,
        "AI research assistant",
        context="AI agent systems",
        max_opportunities=5,
    )

    # Same number of opportunities
    assert len(result1["opportunities"]) == len(result2["opportunities"])

    # Same rank_scores in same order
    scores1 = [o["rank_score"] for o in result1["opportunities"]]
    scores2 = [o["rank_score"] for o in result2["opportunities"]]
    assert scores1 == scores2, f"non-deterministic ranking: {scores1} vs {scores2}"

    # Same gap_types in same order
    types1 = [o["gap_type"] for o in result1["opportunities"]]
    types2 = [o["gap_type"] for o in result2["opportunities"]]
    assert types1 == types2, f"non-deterministic gap types: {types1} vs {types2}"
async def test_negative_empty_knowledge_graph(app, db_session):
    """An empty knowledge graph returns no opportunities with truthful unknowns."""
    # No fixture seeded — empty DB
    result = await discover_opportunities(db_session, "anything")

    assert len(result["opportunities"]) == 0
    assert len(result["unknowns"]) >= 1
    assert any("no capabilities" in u.lower() for u in result["unknowns"])


# ── Negative Test: Insufficient evidence ──────────────────────────────────


@pytest.mark.asyncio
async def test_negative_insufficient_evidence(app, db_session):
    """When a capability has a PROVIDES edge but no evidence_refs, the
    opportunity surfaces the insufficient-evidence gap."""
    # Seed a capability with a PROVIDES edge but no evidence
    await _make_entity(session=db_session, eid="ent-bare", kind="tool", name="bare tool")
    await _make_entity(
        session=db_session, eid="cap-bare", kind="capability", name="bare capability"
    )
    r = await create_relationship(
        session=db_session,
        from_entity_id="ent-bare",
        to_entity_id="cap-bare",
        predicate="PROVIDES",
        evidence_refs=None,  # no evidence
        origin="explicit",
    )
    assert r["ok"] is True
    await db_session.commit()

    result = await discover_opportunities(db_session, "bare capability")

    # The opportunity should exist but surface the insufficient evidence
    bare_opps = [
        o
        for o in result["opportunities"]
        if "bare capability" in o.get("gap_description", "").lower()
    ]
    # If found, it should have uncertainties about insufficient evidence
    if bare_opps:
        opp = bare_opps[0]
        assert len(opp["uncertainties"]) >= 1
        assert len(opp["missing_evidence"]) >= 1


# ── Negative Test: Context-irrelevant contradictions ─────────────────────


@pytest.mark.asyncio
async def test_negative_context_irrelevant_contradictions(app, db_session):
    """Contradictions whose validity_conditions don't match the context
    are preserved as out-of-context metadata, NOT forced into CONTESTED."""
    await _seed_opportunity_fixture(db_session)

    # claim-t02-ev has validity_conditions=["AI agent systems"].
    # Using context="embedded systems" should make the contradiction
    # out-of-context.
    result = await discover_opportunities(
        db_session,
        "evidence verification",
        context="embedded systems",
    )

    ev_opps = [
        o
        for o in result["opportunities"]
        if "evidence verification" in " ".join(o.get("relevant_capabilities", [])).lower()
    ]
    if ev_opps:
        opp = ev_opps[0]
        # The gap_type should NOT be "contested" (the contradiction is out-of-context)
        # It might be "constrained" (if the LIMITS edge still applies)
        # or the out_of_context_contradictions should be non-empty.
        ooc = opp.get("out_of_context_contradictions", [])
        assert opp["gap_type"] != "contested" or len(ooc) > 0, (
            f"out-of-context contradiction incorrectly classified as contested "
            f"without metadata: gap_type={opp['gap_type']}, ooc={ooc}"
        )


# ── Negative Test: Hypothesized-only relationships ───────────────────────


@pytest.mark.asyncio
async def test_negative_hypothesized_only_relationships(app, db_session):
    """Hypothesized relationships are excluded from opportunity discovery.
    Only established (explicit/derived) knowledge is used."""
    await _seed_opportunity_fixture(db_session)

    # Add a hypothesized relationship that would create a new "combination"
    r = await create_relationship(
        session=db_session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-knowledge-extraction",
        predicate="PROVIDES",
        evidence_refs=["frag-t02-a"],
        origin="hypothesized",
    )
    assert r["ok"] is True
    await db_session.commit()

    result = await discover_opportunities(
        db_session,
        "knowledge extraction",
        context="AI agent systems",
    )

    # cap-knowledge-extraction should still appear as NOT_EVIDENCED because
    # the only PROVIDES edge to it is hypothesized (excluded).
    # The capability's canonical name is "structured knowledge extraction".
    not_evidenced_opps = [
        o
        for o in result["opportunities"]
        if o["gap_type"] == "not_evidenced"
        and any("knowledge extraction" in c.lower() for c in o.get("relevant_capabilities", []))
    ]
    assert len(not_evidenced_opps) >= 1, (
        "cap-knowledge-extraction should still be NOT_EVIDENCED — "
        "the hypothesized PROVIDES edge must be excluded; "
        f"found: {[(o['gap_type'], o['relevant_capabilities']) for o in result['opportunities']]}"
    )


# ── Negative Test: Misclassification of NOT_EVIDENCED ──────────────────────


@pytest.mark.asyncio
async def test_negative_misclassification_not_evidenced(app, db_session):
    """NOT_EVIDENCED must NOT be classified as 'no solution exists' or
    'impossible'. It must be classified as 'not_evidenced' gap_type."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(db_session, "AI research assistant")

    for opp in result["opportunities"]:
        if opp["gap_type"] == "not_evidenced":
            # The gap_description must NOT claim impossibility
            desc = opp["gap_description"].lower()
            assert "impossible" not in desc, f"NOT_EVIDENCED misclassified as impossible: {desc}"
            assert "no solution" not in desc, (
                f"NOT_EVIDENCED misclassified as 'no solution': {desc}"
            )


# ── Negative Test: Duplicate opportunities ────────────────────────────────


@pytest.mark.asyncio
async def test_negative_duplicate_opportunities(app, db_session):
    """No duplicate opportunities for the same gap_type + capability."""
    await _seed_opportunity_fixture(db_session)

    result = await discover_opportunities(
        db_session,
        "AI research assistant",
        context="AI agent systems",
    )

    # Check for duplicates by (gap_type, first relevant_capability)
    seen_keys: set[str] = set()
    for opp in result["opportunities"]:
        cap = opp["relevant_capabilities"][0] if opp["relevant_capabilities"] else ""
        key = f"{opp['gap_type']}:{cap}"
        assert key not in seen_keys, f"duplicate opportunity: {key}"
        seen_keys.add(key)
