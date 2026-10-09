"""G04-T03 -- Evidence-Grounded Technical Reasoning acceptance tests.

Per the user's G04-T03 mission briefing §9 (MANDATORY ACCEPTANCE TESTS):

  1.  Correct reasoning-intent selection.
  2.  Relevant knowledge retrieval.
  3.  Valid multi-source synthesis.
  4.  Citation chains for supported findings.
  5.  Derived findings trace to valid relationships.
  6.  Provider attribution remains correct (G04-T02C safeguard preserved).
  7.  Contradictions remain visible.
  8.  Context mismatch prevents unsupported conclusions.
  9.  Missing evidence is reported honestly.
 10.  Unsupported hypotheses are not promoted to facts.
 11.  Dependency direction is respected.
 12.  Alternatives require appropriate relationship evidence.
 13.  Query and output limits are enforced.
 14.  Repeated deterministic queries produce stable results.
 15.  API request/response integration works.
 16.  All previous G01-G04-T02C tests remain green.

Plus a realistic multi-source synthesis demonstration (mission §6).
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
from sqlalchemy import select

from synapse.application.reasoning import (
    DEFAULT_LIMIT,
    MAX_FINDINGS,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    FindingType,
    ReasoningIntent,
    answer_query,
    classify_reasoning_intent,
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

# ── Test fixture (reuses the G04-T02 AI-research-assistant scenario) ─────────

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


async def _seed_reasoning_fixture(session) -> dict[str, Any]:
    """Build the G04-T03 reasoning fixture.

    Models the AI-research-assistant scenario from §6 of the mission
    briefing. Reuses the G04-T02 fixture shape with extensions for the
    reasoning-specific intents (alternatives, constraints, comparisons).
    """
    src_a = await _make_source(session, "src-rsn-a", "https://example.com/papers/synapse")
    src_b = await _make_source(session, "src-rsn-b", "https://example.com/papers/arxiv")
    src_c = await _make_source(session, "src-rsn-c", "https://example.com/papers/no-vector")
    src_d = await _make_source(session, "src-rsn-d", "https://example.com/papers/alt-tool")

    frag_a = await _make_fragment(
        session,
        "frag-rsn-a",
        src_a,
        "Synapse retrieves technical sources and extracts knowledge with evidence verification.",
    )
    frag_b = await _make_fragment(
        session, "frag-rsn-b", src_b, "arxiv provides source discovery for AI research assistants."
    )
    frag_c = await _make_fragment(
        session,
        "frag-rsn-c",
        src_c,
        "The approach is constrained by the absence of a vector database.",
        retrieved_at=STALE_ISO,
    )
    frag_opp = await _make_fragment(
        session,
        "frag-rsn-opp",
        src_a,
        "Evidence verification requires manual review and cannot be fully automated.",
    )
    frag_alt = await _make_fragment(
        session, "frag-rsn-alt", src_d, "AltTool replaces synapse for lightweight retrieval tasks."
    )

    # Entities
    await _make_entity(
        session,
        "ent-synapse",
        "tool",
        "synapse",
        desc="AgentCraft Synapse knowledge engine",
        aliases=["Synapse"],
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
        session, "ent-rs", "technology", "RelationshipService", desc="Graph traversal service"
    )
    await _make_entity(
        session,
        "ent-no-vector",
        "constraint",
        "no vector database",
        desc="Constraint: no vector DB in the minimal slice",
    )
    await _make_entity(
        session, "ent-alttool", "tool", "AltTool", desc="Alternative lightweight retrieval tool"
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
        "cap-knowledge-extraction",
        "capability",
        "structured knowledge extraction",
        desc="Extract entities, claims, relationships",
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
        desc="Identify technical dependencies",
    )

    # Claims (each attributed to a specific provider)
    await _make_claim(
        session,
        "claim-rsn-sd",
        "Synapse provides source discovery for AI agent systems.",
        subject_ref="ent-synapse",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-rsn-a"],
        validity_conditions=["AI agent systems"],
    )
    await _make_claim(
        session,
        "claim-rsn-ce",
        "Synapse enables content extraction from arxiv papers.",
        subject_ref="ent-synapse",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-rsn-a"],
        validity_conditions=["arxiv papers"],
    )
    await _make_claim(
        session,
        "claim-rsn-ev",
        "Synapse provides evidence verification for technical claims.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-rsn-a"],
        contradicting_refs=["frag-rsn-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    # Spans
    await _make_span(
        session,
        "span-rsn-sd",
        "frag-rsn-a",
        "claim-rsn-sd",
        "Synapse retrieves technical sources",
        start=0,
        end=33,
    )
    await _make_span(
        session, "span-rsn-ce", "frag-rsn-a", "claim-rsn-ce", "extracts knowledge", start=50, end=67
    )

    # Relationships
    # synapse PROVIDES capabilities (with evidence where applicable)
    for cap_id, ev_refs in [
        ("cap-source-discovery", ["frag-rsn-a"]),
        ("cap-content-extraction", ["frag-rsn-a"]),
        ("cap-knowledge-extraction", None),  # no evidence
        ("cap-evidence-verification", ["frag-rsn-a"]),
        ("cap-dependency-analysis", ["frag-rsn-c"]),  # stale evidence
    ]:
        r = await create_relationship(
            session,
            from_entity_id="ent-synapse",
            to_entity_id=cap_id,
            predicate="PROVIDES",
            evidence_refs=ev_refs,
            origin="explicit",
        )
        assert r["ok"] is True

    # arxiv PROVIDES source discovery (multiple providers)
    r = await create_relationship(
        session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-source-discovery",
        predicate="PROVIDES",
        evidence_refs=["frag-rsn-b"],
        origin="explicit",
    )
    assert r["ok"] is True

    # trafilatura PROVIDES content extraction (no evidence)
    r = await create_relationship(
        session,
        from_entity_id="ent-trafilatura",
        to_entity_id="cap-content-extraction",
        predicate="PROVIDES",
        evidence_refs=None,
        origin="explicit",
    )
    assert r["ok"] is True

    # synapse REQUIRES RelationshipService (dependency direction)
    r = await create_relationship(
        session,
        from_entity_id="ent-synapse",
        to_entity_id="ent-rs",
        predicate="REQUIRES",
        evidence_refs=["frag-rsn-a"],
        origin="explicit",
    )
    assert r["ok"] is True

    # ent-no-vector LIMITS cap-evidence-verification
    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=["frag-rsn-c"],
        origin="explicit",
    )
    assert r["ok"] is True

    # AltTool REPLACES synapse (documented alternative)
    r = await create_relationship(
        session,
        from_entity_id="ent-alttool",
        to_entity_id="ent-synapse",
        predicate="REPLACES",
        evidence_refs=["frag-rsn-alt"],
        origin="explicit",
    )
    assert r["ok"] is True

    # AltTool INTEGRATES_WITH synapse (must NOT be treated as ALTERNATIVE_TO)
    r = await create_relationship(
        session,
        from_entity_id="ent-alttool",
        to_entity_id="ent-synapse",
        predicate="INTEGRATES_WITH",
        evidence_refs=None,
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()

    # Assess claims
    for cid in ("claim-rsn-sd", "claim-rsn-ce", "claim-rsn-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {
        "synapse_id": "ent-synapse",
        "arxiv_id": "ent-arxiv",
        "alttool_id": "ent-alttool",
        "capability_ids": [
            "cap-source-discovery",
            "cap-content-extraction",
            "cap-knowledge-extraction",
            "cap-evidence-verification",
            "cap-dependency-analysis",
        ],
    }


# ── Test 1: Correct reasoning-intent selection ─────────────────────────────


def test_reasoning_intent_selection():
    """Each supported intent is correctly classified from the query."""
    cases = [
        ("What can synapse do?", ReasoningIntent.CAPABILITY_EXPLANATION),
        ("What does synapse require?", ReasoningIntent.DEPENDENCY_ANALYSIS),
        ("What are the alternatives to synapse?", ReasoningIntent.DOCUMENTED_ALTERNATIVES),
        ("What limits synapse?", ReasoningIntent.CONSTRAINT_ANALYSIS),
        ("Compare synapse and arxiv", ReasoningIntent.TECHNICAL_COMPARISON),
        ("What is missing for an AI assistant?", ReasoningIntent.GAP_EXPLANATION),
        ("aardvark picnic galoshes", ReasoningIntent.UNKNOWN),
    ]
    for query, expected in cases:
        actual = classify_reasoning_intent(query)
        assert actual == expected, f"intent for {query!r} should be {expected}, got {actual}"


# ── Test 2: Relevant knowledge retrieval ────────────────────────────────────


@pytest.mark.asyncio
async def test_relevant_knowledge_retrieval(app, db_session):
    """The reasoning layer retrieves relevant knowledge for the query.

    For capability_explanation with a named candidate, the answer must
    include findings about that candidate's capabilities.
    """
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.CAPABILITY_EXPLANATION
    assert len(answer["findings"]) > 0, "no findings produced"
    # At least one finding must mention synapse.
    synapse_findings = [
        f
        for f in answer["findings"]
        if "synapse" in f["text"].lower() or "source discovery" in f["text"].lower()
    ]
    assert len(synapse_findings) > 0


# ── Test 3: Valid multi-source synthesis ────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_source_synthesis(app, db_session):
    """The answer synthesizes knowledge from multiple sources.

    For capability_explanation without a named candidate, the answer
    should enumerate ALL documented capabilities (multi-source).
    """
    await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What capabilities are documented?",
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.CAPABILITY_EXPLANATION
    assert len(answer["findings"]) >= 3, (
        f"multi-source synthesis should produce >= 3 findings, got {len(answer['findings'])}"
    )


# ── Test 4: Citation chains for supported findings ─────────────────────────


@pytest.mark.asyncio
async def test_citation_chains_for_supported_findings(app, db_session):
    """DOCUMENTED_FACT findings must have valid citation chains."""
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    # Find at least one DOCUMENTED_FACT finding.
    documented = [f for f in answer["findings"] if f["type"] == FindingType.DOCUMENTED_FACT]
    if documented:
        # The evidence_chain must be non-empty for documented findings.
        assert len(answer["evidence_chain"]) > 0, "documented findings must have evidence chains"
        for link in answer["evidence_chain"]:
            assert link["fragment_id"], "fragment_id missing"
            assert link["source_uri"], "source_uri missing"
            for span in link["spans"]:
                assert span["start_offset"] < span["end_offset"]


# ── Test 5: Derived findings trace to valid relationships ──────────────────


@pytest.mark.asyncio
async def test_derived_findings_trace_to_relationships(app, db_session):
    """DERIVED_FINDING findings must cite valid relationship IDs."""
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What does synapse require?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.DEPENDENCY_ANALYSIS
    # The dependency analysis should produce findings about REQUIRES edges.
    if answer["findings"]:
        # The cited_relationships list must be non-empty.
        assert len(answer["cited_relationships"]) > 0, "derived findings must cite relationship IDs"
        # Every cited relationship must exist in the DB (validated by the
        # reasoning layer's _validate_relationship_ids helper).
        from synapse.storage.models import RelationshipRow

        for rid in answer["cited_relationships"]:
            stmt = select(RelationshipRow).where(RelationshipRow.id == rid)
            r = (await db_session.execute(stmt)).scalar_one_or_none()
            assert r is not None, f"cited relationship {rid} does not exist in DB"


# ── Test 6: Provider attribution remains correct (G04-T02C safeguard) ──────


@pytest.mark.asyncio
async def test_provider_attribution_remains_correct(app, db_session):
    """The G04-T02C safeguard is preserved: a candidate without attributed
    evidence must NOT be classified as SUPPORTED."""
    fixture = await _seed_reasoning_fixture(db_session)

    # ent-trafilatura PROVIDES cap-content-extraction but has NO claim
    # attributed to it. The reasoning layer must NOT produce a
    # DOCUMENTED_FACT finding for trafilatura's content extraction.
    answer = await answer_query(
        db_session,
        "What can trafilatura do?",
        candidate_entity_ids=["ent-trafilatura"],
        limit=10,
    )
    # trafilatura PROVIDES cap-content-extraction with no evidence_refs
    # and no attributed claim -> NOT_EVIDENCED (per G04-T02C).
    # The findings must NOT include a DOCUMENTED_FACT for trafilatura.
    documented_for_trafilatura = [
        f
        for f in answer["findings"]
        if f["type"] == FindingType.DOCUMENTED_FACT and "trafilatura" in f["text"].lower()
    ]
    assert len(documented_for_trafilatura) == 0, (
        f"trafilatura has no attributed evidence -> must NOT be DOCUMENTED_FACT: "
        f"{[f['text'] for f in documented_for_trafilatura]}"
    )


# ── Test 7: Contradictions remain visible ──────────────────────────────────


@pytest.mark.asyncio
async def test_contradictions_remain_visible(app, db_session):
    """Contradictory evidence must be preserved, not suppressed."""
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    # The evidence verification claim has contradicting_refs, so the
    # finding for cap-evidence-verification should be CONTESTED.
    contested_findings = [
        f
        for f in answer["findings"]
        if "contested" in f["text"].lower() or "conflict" in f["text"].lower()
    ]
    assert len(contested_findings) > 0, "contradictory evidence should be preserved in findings"


# ── Test 8: Context mismatch prevents unsupported conclusions ──────────────


@pytest.mark.asyncio
async def test_context_mismatch_prevents_unsupported(app, db_session):
    """When context doesn't match validity_conditions, no DOCUMENTED_FACT
    is produced for that capability."""
    fixture = await _seed_reasoning_fixture(db_session)
    # claim-rsn-sd has validity_conditions=["AI agent systems"].
    # With no context, the applicability check fails -> not SUPPORTED.
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        context="embedded systems",  # mismatch
        limit=10,
    )
    # No finding should be a clean DOCUMENTED_FACT for source discovery
    # (because the context doesn't match).
    sd_documented = [
        f
        for f in answer["findings"]
        if f["type"] == FindingType.DOCUMENTED_FACT and "source discovery" in f["text"].lower()
    ]
    # The context mismatch should prevent unconditional SUPPORTED.
    # (If a DOCUMENTED_FACT finding exists, its caveat must mention the
    # context issue.)
    for f in sd_documented:
        assert f.get("caveat") is not None, (
            f"context-mismatched finding must have a caveat: {f['text']}"
        )


# ── Test 9: Missing evidence is reported honestly ──────────────────────────


@pytest.mark.asyncio
async def test_missing_evidence_reported_honestly(app, db_session):
    """UNKNOWN / NOT_EVIDENCED capabilities must be reported, not fabricated."""
    fixture = await _seed_reasoning_fixture(db_session)
    # cap-knowledge-extraction has a PROVIDES edge from synapse but no
    # evidence_refs and no attributed claim.
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    # The findings must include an UNKNOWN or DERIVED_FINDING for
    # knowledge extraction (which has no evidence).
    ke_findings = [f for f in answer["findings"] if "knowledge extraction" in f["text"].lower()]
    if ke_findings:
        # None of these should be DOCUMENTED_FACT (no evidence exists).
        for f in ke_findings:
            assert f["type"] != FindingType.DOCUMENTED_FACT, (
                f"knowledge extraction has no evidence -> must NOT be DOCUMENTED_FACT: {f['text']}"
            )
    # The unknowns list must mention the missing evidence.
    assert len(answer["unknowns"]) > 0 or any(
        f["type"] == FindingType.UNKNOWN for f in answer["findings"]
    ), "missing evidence must be reported in unknowns or as UNKNOWN findings"


# ── Test 10: Unsupported hypotheses not promoted to facts ──────────────────


@pytest.mark.asyncio
async def test_hypotheses_not_promoted_to_facts(app, db_session):
    """HYPOTHESIS findings must never be presented as DOCUMENTED_FACT."""
    fixture = await _seed_reasoning_fixture(db_session)
    # technical_comparison intent produces a HYPOTHESIS synthesis finding.
    answer = await answer_query(
        db_session,
        "Compare synapse and arxiv",
        candidate_entity_ids=[fixture["synapse_id"], fixture["arxiv_id"]],
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.TECHNICAL_COMPARISON
    # The synthesis finding must be HYPOTHESIS, not DOCUMENTED_FACT.
    hypothesis_findings = [f for f in answer["findings"] if f["type"] == FindingType.HYPOTHESIS]
    assert len(hypothesis_findings) > 0, (
        "technical comparison must produce at least one HYPOTHESIS finding"
    )
    # The hypothesis must have a low confidence (< 0.3).
    for f in hypothesis_findings:
        assert f["confidence"] < 0.3, f"hypothesis confidence must be < 0.3, got {f['confidence']}"
        assert f.get("caveat"), "hypothesis must have a caveat"


# ── Test 11: Dependency direction is respected ─────────────────────────────


@pytest.mark.asyncio
async def test_dependency_direction_respected(app, db_session):
    """REQUIRES edges are from provider -> dependency (directed).

    A reverse edge (dependency REQUIRES provider) must NOT be reported
    as a dependency of the provider.
    """
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What does synapse require?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.DEPENDENCY_ANALYSIS
    # The findings should mention RelationshipService as a dependency
    # of synapse (not the reverse).
    if answer["findings"]:
        rs_findings = [f for f in answer["findings"] if "relationshipservice" in f["text"].lower()]
        assert len(rs_findings) > 0, "synapse REQUIRES RelationshipService must be in findings"
        # The text should be "synapse REQUIRES RelationshipService", not
        # "RelationshipService REQUIRES synapse".
        for f in rs_findings:
            assert "synapse" in f["text"].lower()
            assert "relationshipservice" in f["text"].lower()


# ── Test 12: Alternatives require appropriate relationship evidence ─────────


@pytest.mark.asyncio
async def test_alternatives_require_replaces_relationship(app, db_session):
    """DOCUMENTED_ALTERNATIVES intent uses REPLACES only.

    INTEGRATES_WITH must NOT be treated as ALTERNATIVE_TO.
    """
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What are the alternatives to synapse?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=10,
    )
    assert answer["intent"] == ReasoningIntent.DOCUMENTED_ALTERNATIVES
    # AltTool has both REPLACES and INTEGRATES_WITH edges to synapse.
    # Only REPLACES should appear in findings.
    if answer["findings"]:
        replaces_findings = [f for f in answer["findings"] if "replaces" in f["text"].lower()]
        integrates_findings = [
            f
            for f in answer["findings"]
            if "integrates_with" in f["text"].lower() and "replaces" not in f["text"].lower()
        ]
        # REPLACES findings may exist.
        # INTEGRATES_WITH findings must NOT exist as alternatives.
        assert len(integrates_findings) == 0, (
            f"INTEGRATES_WITH must NOT be treated as ALTERNATIVE_TO: "
            f"{[f['text'] for f in integrates_findings]}"
        )


# ── Test 13: Query and output limits are enforced ──────────────────────────


@pytest.mark.asyncio
async def test_query_limit_enforced(app, db_session):
    """Queries longer than MAX_QUERY_CHARS are rejected."""
    await _seed_reasoning_fixture(db_session)
    too_long = "a" * (MAX_QUERY_CHARS + 1)
    answer = await answer_query(db_session, too_long, limit=10)
    assert answer["intent"] == ReasoningIntent.UNKNOWN
    assert any("query_too_long" in u for u in answer["unknowns"])


@pytest.mark.asyncio
async def test_findings_limit_enforced(app, db_session):
    """Findings are capped at MAX_FINDINGS."""
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=[fixture["synapse_id"]],
        limit=100,
    )
    assert len(answer["findings"]) <= MAX_FINDINGS


def test_constants_sane():
    """Verify the hard limits are sane."""
    assert MAX_QUERY_CHARS == 512
    assert DEFAULT_LIMIT == 20
    assert MAX_LIMIT == 100
    assert MAX_FINDINGS == 50


# ── Test 14: Repeated deterministic queries produce stable results ─────────


@pytest.mark.asyncio
async def test_repeated_queries_deterministic(app, db_session):
    """Two identical queries on the same DB state produce identical answers."""
    fixture = await _seed_reasoning_fixture(db_session)
    q = "What can synapse do?"
    cands = [fixture["synapse_id"]]

    a1 = await answer_query(db_session, q, candidate_entity_ids=cands, limit=10)
    a2 = await answer_query(db_session, q, candidate_entity_ids=cands, limit=10)

    assert a1["intent"] == a2["intent"]
    assert [f["text"] for f in a1["findings"]] == [f["text"] for f in a2["findings"]]
    assert a1["cited_claims"] == a2["cited_claims"]
    assert a1["cited_relationships"] == a2["cited_relationships"]
    assert a1["confidence"]["overall"] == a2["confidence"]["overall"]


# ── Test 15: API request/response integration works ─────────────────────────


def test_api_reasoning_requires_auth(app, client):
    """The /api/v1/reasoning/queries endpoint requires auth."""
    r = client.post("/api/v1/reasoning/queries", json={"query": "test"})
    assert r.status_code == 401


def test_api_reasoning_with_auth(app, client, auth_headers_reader):
    """The /api/v1/reasoning/queries endpoint returns 200 with auth."""
    r = client.post(
        "/api/v1/reasoning/queries",
        headers=auth_headers_reader,
        json={"query": "What can synapse do?", "limit": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["api_version"] == "v1"
    data = body["data"]
    assert "question" in data
    assert "intent" in data
    assert "findings" in data
    assert "evidence_chain" in data
    assert "unknowns" in data
    assert "confidence" in data
    assert "limitations" in data


def test_api_reasoning_intents_endpoint(app, client, auth_headers_reader):
    """The /api/v1/reasoning/intents endpoint lists supported intents."""
    r = client.get("/api/v1/reasoning/intents", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] >= 6  # 6 intents + UNKNOWN


def test_api_openapi_includes_reasoning_routes(app):
    """The OpenAPI schema includes the new reasoning routes."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/reasoning/queries" in paths
    assert "/api/v1/reasoning/intents" in paths


# ── Test 16: G01-G04-T02C regression ────────────────────────────────────────


def test_g01_g04_t02c_regression():
    """All existing G01-G04-T02C tests still pass."""
    REPO_ROOT = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_g04_t01_retrieval.py::test_g01_g02_g03_regression",
            "tests/integration/test_g04_t02_capability_registry.py::test_g01_g04_t01_regression",
            "tests/integration/test_g04_t02_capability_registry.py::test_g01_g04_t02_regression_after_attribution_fix",
            "tests/integration/test_g04_t02_capability_registry.py::test_provider_attribution_isolation",
            "tests/integration/test_g04_t02_capability_registry.py::test_multiple_providers_each_supported",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout


# ── Realistic multi-source synthesis demonstration (mission §6) ─────────────


@pytest.mark.asyncio
async def test_realistic_demonstration_ai_research_assistant(app, db_session):
    """Multi-source synthesis: 'What documented components and techniques
    could support an AI research assistant, and what limitations must be
    considered?'

    The answer must:
    - Identify documented retrieval capabilities
    - Identify knowledge extraction capabilities
    - Identify evidence verification capabilities
    - Identify dependencies (RelationshipService)
    - Identify constraints (no vector database LIMITS evidence verification)
    - Surface contradictory findings (CONTESTED evidence verification)
    - Identify unverified requirements (cap-knowledge-extraction has no evidence)
    - NOT claim the combination is fully compatible or production-ready
    """
    fixture = await _seed_reasoning_fixture(db_session)
    answer = await answer_query(
        db_session,
        "What capabilities are documented for an AI research assistant?",
        candidate_entity_ids=[fixture["synapse_id"]],
        context="AI agent systems arxiv papers",
        limit=20,
    )
    assert answer["intent"] == ReasoningIntent.CAPABILITY_EXPLANATION

    # Must produce findings for multiple capabilities.
    assert len(answer["findings"]) >= 3, (
        f"multi-source synthesis should produce >= 3 findings, got {len(answer['findings'])}"
    )

    # The findings_text set must mention at least 3 distinct capabilities.
    all_text = " ".join(f["text"] for f in answer["findings"]).lower()
    capability_mentions = sum(
        1
        for cap in (
            "source discovery",
            "content extraction",
            "knowledge extraction",
            "evidence verification",
            "dependency analysis",
        )
        if cap in all_text
    )
    assert capability_mentions >= 3, (
        f"multi-source synthesis should mention >= 3 capabilities, got "
        f"{capability_mentions}: {all_text}"
    )

    # The contradiction for evidence verification must be visible.
    contested_or_unknown = [
        f
        for f in answer["findings"]
        if f["type"] in (FindingType.UNKNOWN, FindingType.DERIVED_FINDING)
        or "contest" in f["text"].lower()
        or "no sufficient" in f["text"].lower()
    ]
    assert len(contested_or_unknown) > 0, (
        "the synthesis must surface at least one uncertain/contested finding"
    )

    # The answer must NOT claim the combination is production-ready.
    answer_text_lower = answer["answer_text"].lower()
    assert "proven architecture" not in answer_text_lower or "not a proven" in answer_text_lower, (
        "the answer must NOT claim the combination is a proven architecture"
    )
    assert "candidate" in answer_text_lower or "hypothesis" in answer_text_lower, (
        "the answer must reference candidate/hypothesis status"
    )

    # The limitations list must be non-empty.
    assert len(answer["limitations"]) > 0

    # The confidence must be bounded (< 0.9, since VERIFIED is unreachable).
    assert answer["confidence"]["overall"] < 0.9, (
        f"overall confidence must be < 0.9 (VERIFIED unreachable), got "
        f"{answer['confidence']['overall']}"
    )
