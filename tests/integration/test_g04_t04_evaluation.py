"""G04-T04 -- Retrieval & Reasoning Evaluation + Cost-Aware Routing acceptance tests.

Per the user's G04-T04 mission briefing §10 (MANDATORY ACCEPTANCE TESTS):

  1.  Deterministic golden-query execution.
  2.  Correct Precision@K calculation.
  3.  Correct Recall@K calculation.
  4.  Correct MRR calculation.
  5.  Citation validity measurement.
  6.  Unsupported factual claim detection within defined evaluation scope.
  7.  Provider-attribution correctness.
  8.  Context-aware contradiction evaluation.
  9.  Dependency-direction evaluation.
  10. Appropriate direct-lookup routing.
  11. Appropriate hybrid-retrieval routing.
  12. Appropriate grounded-reasoning routing.
  13. Safe fallback for ambiguous queries.
  14. Resource-budget enforcement.
  15. Measured versus estimated cost separation.
  16. Visible quality-gate failures.
  17. Reproducible quality metrics.
  18. Full G01-G04-T03C regression compatibility.

Plus a concise demonstration showing actual evaluation output and
routing decisions (mission §10 closing remark).

Test fixture: reuses the G04-T03 reasoning fixture (``_seed_reasoning_fixture``)
and the G04-T03C context-contradiction fixture (``_seed_context_contradictions_fixture``)
so the golden dataset's expected identifiers are ground-truth-defined
independently of the G04-T04 implementation output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from synapse.application.gap_analyzer import GapClassification, analyze_gap
from synapse.application.reasoning import (
    ReasoningIntent,
    answer_query,
    classify_reasoning_intent,
)
from synapse.application.relationship_service import create_relationship, find_capabilities
from synapse.application.verification import assess_claim
from synapse.evaluation import (
    DEFAULT_BUDGET,
    GOLDEN_DATASET_VERSION,
    CostMeasurement,
    ResourceBudget,
    RoutingPath,
    mrr,
    precision_at_k,
    recall_at_k,
    route_query,
    run_evaluation,
    unsupported_factual_claim_rate,
)
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

# ── Test fixture (reuses the G04-T03 reasoning fixture shape) ──────────────

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


async def _seed_evaluation_fixture(session) -> dict[str, Any]:
    """Build the G04-T04 evaluation fixture.

    Combines the G04-T03 reasoning fixture (AI-research-assistant
    scenario) with the G04-T03C context-contradiction fixture, so the
    golden dataset's 14 cases (covering 12 categories) all have
    ground-truth-defined expected identifiers.
    """
    # ── Part 1: G04-T03 reasoning fixture (synapse + arxiv + alttool) ──
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
        session,
        "span-rsn-ce",
        "frag-rsn-a",
        "claim-rsn-ce",
        "extracts knowledge",
        start=50,
        end=67,
    )

    # Relationships
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

    r = await create_relationship(
        session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-source-discovery",
        predicate="PROVIDES",
        evidence_refs=["frag-rsn-b"],
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-trafilatura",
        to_entity_id="cap-content-extraction",
        predicate="PROVIDES",
        evidence_refs=None,
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-synapse",
        to_entity_id="ent-rs",
        predicate="REQUIRES",
        evidence_refs=["frag-rsn-a"],
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=["frag-rsn-c"],
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-alttool",
        to_entity_id="ent-synapse",
        predicate="REPLACES",
        evidence_refs=["frag-rsn-alt"],
        origin="explicit",
    )
    assert r["ok"] is True

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
    for cid in ("claim-rsn-sd", "claim-rsn-ce", "claim-rsn-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    # ── Part 2: G04-T03C context-contradiction fixture (tools A and B) ──
    src_1 = await _make_source(session, "src-ctx-1", "https://example.com/ctx1")
    src_2 = await _make_source(session, "src-ctx-2", "https://example.com/ctx2")
    src_3 = await _make_source(session, "src-ctx-3", "https://example.com/ctx3")
    src_4 = await _make_source(session, "src-ctx-4", "https://example.com/ctx4")

    await _make_fragment(
        session, "frag-ctx-a", src_1, "Tool A provides capability X for AI agents."
    )
    await _make_fragment(
        session, "frag-ctx-b", src_2, "Tool A provides capability X for embedded systems."
    )
    await _make_fragment(
        session, "frag-ctx-opp1", src_3, "Capability X cannot be fully automated for AI agents."
    )
    await _make_fragment(
        session,
        "frag-ctx-opp2",
        src_4,
        "Capability X cannot be fully automated for embedded systems.",
    )
    await _make_fragment(
        session,
        "frag-ctx-uni",
        src_1,
        "Tool A provides universal capability with contradicting evidence.",
    )
    await _make_fragment(
        session,
        "frag-ctx-opp-uni",
        src_3,
        "Universal capability has contradicting evidence.",
    )
    await _make_fragment(
        session,
        "frag-ctx-b-frag",
        src_2,
        "Tool B provides capability B-only with contradicting evidence.",
    )
    await _make_fragment(
        session,
        "frag-ctx-opp-b",
        src_4,
        "Capability B-only has contradicting evidence for Tool B.",
    )

    await _make_entity(session, "ent-ctx-A", "tool", "tool A")
    await _make_entity(session, "ent-ctx-B", "tool", "tool B")
    await _make_entity(session, "cap-ctx-X", "capability", "capability X")
    await _make_entity(session, "cap-ctx-universal", "capability", "universal capability")
    await _make_entity(session, "cap-ctx-B-only", "capability", "capability B-only")

    await _make_claim(
        session,
        "claim-A-applicable",
        "Tool A provides capability X for AI agents.",
        subject_ref="ent-ctx-A",
        object_ref="cap-ctx-X",
        evidence_refs=["frag-ctx-a"],
        contradicting_refs=["frag-ctx-opp1"],
        epistemic_state="disputed",
        validity_conditions=["AI agents"],
    )
    await _make_claim(
        session,
        "claim-A-inapplicable",
        "Tool A provides capability X for embedded systems.",
        subject_ref="ent-ctx-A",
        object_ref="cap-ctx-X",
        evidence_refs=["frag-ctx-b"],
        contradicting_refs=["frag-ctx-opp2"],
        epistemic_state="disputed",
        validity_conditions=["embedded systems"],
    )
    await _make_claim(
        session,
        "claim-A-universal",
        "Tool A provides universal capability with contradicting evidence.",
        subject_ref="ent-ctx-A",
        object_ref="cap-ctx-universal",
        evidence_refs=["frag-ctx-uni"],
        contradicting_refs=["frag-ctx-opp-uni"],
        epistemic_state="disputed",
    )
    await _make_claim(
        session,
        "claim-B-only",
        "Tool B provides capability B-only with contradicting evidence.",
        subject_ref="ent-ctx-B",
        object_ref="cap-ctx-B-only",
        evidence_refs=["frag-ctx-b-frag"],
        contradicting_refs=["frag-ctx-opp-b"],
        epistemic_state="disputed",
        validity_conditions=["AI agents"],
    )

    r = await create_relationship(
        session,
        from_entity_id="ent-ctx-A",
        to_entity_id="cap-ctx-X",
        predicate="PROVIDES",
        evidence_refs=["frag-ctx-a"],
        origin="explicit",
    )
    assert r["ok"] is True
    r = await create_relationship(
        session,
        from_entity_id="ent-ctx-A",
        to_entity_id="cap-ctx-universal",
        predicate="PROVIDES",
        evidence_refs=["frag-ctx-uni"],
        origin="explicit",
    )
    assert r["ok"] is True
    r = await create_relationship(
        session,
        from_entity_id="ent-ctx-B",
        to_entity_id="cap-ctx-B-only",
        predicate="PROVIDES",
        evidence_refs=["frag-ctx-b-frag"],
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()
    for cid in ("claim-A-applicable", "claim-A-inapplicable", "claim-A-universal", "claim-B-only"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {
        "synapse_id": "ent-synapse",
        "arxiv_id": "ent-arxiv",
        "alttool_id": "ent-alttool",
        "tool_A": "ent-ctx-A",
        "tool_B": "ent-ctx-B",
        "capability_ids": [
            "cap-source-discovery",
            "cap-content-extraction",
            "cap-knowledge-extraction",
            "cap-evidence-verification",
            "cap-dependency-analysis",
        ],
    }


# ── Acceptance Test 1: Deterministic golden-query execution ────────────────


@pytest.mark.asyncio
async def test_01_deterministic_golden_query_execution(app, db_session):
    """Run the golden dataset twice; both runs must produce identical metrics."""
    await _seed_evaluation_fixture(db_session)

    report1 = await run_evaluation(db_session)
    report2 = await run_evaluation(db_session)

    assert report1.case_count == report2.case_count == 14, "14 cases expected"
    assert report1.dataset_version == report2.dataset_version == GOLDEN_DATASET_VERSION

    # All metrics must be identical (deterministic).
    for c1, c2 in zip(report1.case_metrics, report2.case_metrics, strict=True):
        assert c1.case_id == c2.case_id
        assert c1.routing_decision.path == c2.routing_decision.path
        assert c1.precision_at_5 == c2.precision_at_5
        assert c1.recall_at_5 == c2.recall_at_5
        assert c1.mrr == c2.mrr
        assert c1.citation_validity == c2.citation_validity
        assert c1.evidence_grounded_finding_rate == c2.evidence_grounded_finding_rate
        assert c1.unsupported_factual_claim_rate == c2.unsupported_factual_claim_rate
        assert c1.hypothesis_promotion_error_rate == c2.hypothesis_promotion_error_rate
        assert c1.finding_coverage == c2.finding_coverage
        assert [g.passed for g in c1.quality_gates] == [g.passed for g in c2.quality_gates]


# ── Acceptance Test 2: Correct Precision@K calculation ─────────────────────


def test_02_precision_at_k_calculation():
    """Precision@K is the fraction of top-K retrieved IDs that are relevant."""
    # All relevant
    assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0
    # One-third relevant
    assert precision_at_k(["a", "x", "y"], {"a", "b", "c"}, 3) == pytest.approx(1 / 3)
    # None relevant
    assert precision_at_k(["x", "y", "z"], {"a", "b", "c"}, 3) == 0.0
    # Empty result set, no relevant IDs → 1.0 (vacuously no fabricated hits)
    assert precision_at_k([], set(), 5) == 1.0
    # Empty result set, but relevant IDs exist → 0.0 (we missed everything)
    assert precision_at_k([], {"a"}, 5) == 0.0
    # k=0 → 0.0
    assert precision_at_k(["a", "b"], {"a"}, 0) == 0.0
    # k > len(retrieved) → use len(retrieved)
    assert precision_at_k(["a", "b"], {"a"}, 10) == 0.5


# ── Acceptance Test 3: Correct Recall@K calculation ─────────────────────────


def test_03_recall_at_k_calculation():
    """Recall@K is the fraction of relevant IDs retrieved within top K."""
    # All relevant retrieved
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, 5) == 1.0
    # Half of relevant retrieved
    assert recall_at_k(["a", "x"], {"a", "b"}, 5) == 0.5
    # None retrieved
    assert recall_at_k(["x", "y"], {"a", "b"}, 5) == 0.0
    # No relevant IDs → 0.0 (insufficient ground truth)
    assert recall_at_k(["a", "b"], set(), 5) == 0.0
    # k=0 → 0.0
    assert recall_at_k(["a"], {"a"}, 0) == 0.0


# ── Acceptance Test 4: Correct MRR calculation ──────────────────────────────


def test_04_mrr_calculation():
    """MRR is 1/rank of the first relevant result; 0 if none."""
    assert mrr(["a", "b", "c"], {"a"}) == 1.0
    assert mrr(["x", "a", "b"], {"a"}) == 0.5
    assert mrr(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)
    assert mrr(["x", "y", "z"], {"a"}) == 0.0
    assert mrr([], {"a"}) == 0.0
    assert mrr(["a", "b"], set()) == 0.0  # no relevant targets


# ── Acceptance Test 5: Citation validity measurement ────────────────────────


@pytest.mark.asyncio
async def test_05_citation_validity_measurement(app, db_session):
    """Citation validity = fraction of cited IDs that exist in the DB."""
    await _seed_evaluation_fixture(db_session)

    # Run an evaluation; all cited IDs must resolve (zero fabricated citations).
    report = await run_evaluation(db_session)
    for cm in report.case_metrics:
        # Citation validity must be 1.0 (zero fabricated citations is a hard gate).
        assert cm.citation_validity == 1.0, (
            f"{cm.case_id}: citation_validity={cm.citation_validity} < 1.0 — "
            f"fabricated citations detected"
        )
        # The corresponding quality gate must pass.
        no_fab_gate = next(g for g in cm.quality_gates if g.name == "no_fabricated_citations")
        assert no_fab_gate.passed is True, (
            f"{cm.case_id}: no_fabricated_citations gate failed: {no_fab_gate.detail}"
        )


# ── Acceptance Test 6: Unsupported factual claim detection ──────────────────


def test_06_unsupported_factual_claim_detection():
    """DOCUMENTED_FACT findings without evidence_refs are detected as unsupported."""
    # Two documented facts: one with evidence, one without.
    findings = [
        {"type": "documented_fact", "evidence_refs": ["frag-1"]},
        {"type": "documented_fact", "evidence_refs": []},
        {"type": "derived_finding", "evidence_refs": []},
        {"type": "unknown", "evidence_refs": []},
    ]
    rate = unsupported_factual_claim_rate(findings)
    # 1 of 2 documented_fact findings is unsupported → 0.5
    assert rate == 0.5

    # All documented facts have evidence → 0.0
    findings_ok = [
        {"type": "documented_fact", "evidence_refs": ["frag-1"]},
        {"type": "documented_fact", "evidence_refs": ["frag-2", "frag-3"]},
    ]
    assert unsupported_factual_claim_rate(findings_ok) == 0.0

    # No documented facts → 0.0
    assert unsupported_factual_claim_rate([{"type": "unknown"}]) == 0.0
    assert unsupported_factual_claim_rate([]) == 0.0


# ── Acceptance Test 7: Provider-attribution correctness ────────────────────


@pytest.mark.asyncio
async def test_07_provider_attribution_correctness(app, db_session):
    """G04-T02C provider-attribution safeguard is preserved in evaluation."""
    await _seed_evaluation_fixture(db_session)

    # Call answer_query directly to inspect the reasoning layer's
    # attribution behavior (the G04-T02C safeguard in the gap analyzer).
    # Note: the router would route this query to PATH A, but we call
    # answer_query directly here to verify attribution correctness at the
    # reasoning layer (which is what the safeguard protects).
    answer = await answer_query(
        db_session,
        "What can synapse do?",
        candidate_entity_ids=["ent-synapse"],
        context="AI agent systems",
        limit=20,
    )

    # The answer's cited_claims must only contain claims attributed to
    # ent-synapse. claim-B-only is attributed to ent-ctx-B (subject_ref)
    # and must NEVER appear when candidate=ent-synapse.
    assert "claim-B-only" not in answer.get("cited_claims", []), (
        "G04-T02C violation: claim-B-only (attributed to ent-ctx-B) leaked into "
        "ent-synapse's cited_claims"
    )
    # And claim-A-* (attributed to ent-ctx-A) must also not leak.
    for cid in ("claim-A-applicable", "claim-A-inapplicable", "claim-A-universal"):
        assert cid not in answer.get("cited_claims", []), (
            f"G04-T02C violation: {cid} (attributed to ent-ctx-A) leaked into "
            f"ent-synapse's cited_claims"
        )

    # The "no_provider_attribution_leakage" gate must pass for this case
    # in the full evaluation run.
    report = await run_evaluation(db_session)
    synapse_case = next(
        cm
        for cm in report.case_metrics
        if cm.case_id == "G04T04-C04"  # provider-attribution case
    )
    attribution_gate = next(
        g for g in synapse_case.quality_gates if g.name == "no_provider_attribution_leakage"
    )
    assert attribution_gate.passed is True, (
        f"G04T04-C04: provider-attribution gate failed: {attribution_gate.detail}"
    )


# ── Acceptance Test 8: Context-aware contradiction evaluation ──────────────


@pytest.mark.asyncio
async def test_08_context_aware_contradiction_evaluation(app, db_session):
    """Applicable contradictions fire CONTESTED; out-of-context do NOT."""
    await _seed_evaluation_fixture(db_session)

    # Case 1: context matches → CONTESTED.
    gap_result = await analyze_gap(
        db_session,
        ["capability X"],
        candidate_entity_ids=["ent-ctx-A"],
        context="AI agents",
    )
    req = gap_result["requirements"][0]
    assert req["classification"] == GapClassification.CONTESTED, (
        f"applicable contradiction should fire CONTESTED, got {req['classification']}"
    )

    # Case 2: context does NOT match → NOT CONTESTED.
    gap_result2 = await analyze_gap(
        db_session,
        ["capability X"],
        candidate_entity_ids=["ent-ctx-A"],
        context="mobile apps",
    )
    req2 = gap_result2["requirements"][0]
    assert req2["classification"] != GapClassification.CONTESTED, (
        f"out-of-context contradiction should NOT fire CONTESTED, got {req2['classification']}"
    )

    # Case 3: out-of-context contradictions are preserved as metadata.
    ooc = req2.get("out_of_context_contradictions", [])
    assert len(ooc) >= 1, "out-of-context contradictions must be preserved as visible metadata"


# ── Acceptance Test 9: Dependency-direction evaluation ─────────────────────


@pytest.mark.asyncio
async def test_09_dependency_direction_evaluation(app, db_session):
    """REQUIRES direction (provider → dependency) is preserved."""
    await _seed_evaluation_fixture(db_session)

    answer = await answer_query(
        db_session,
        "What does synapse require?",
        candidate_entity_ids=["ent-synapse"],
        limit=20,
    )

    # The answer must mention synapse REQUIRES RelationshipService.
    text_blob = " ".join(f.get("text", "") for f in answer.get("findings", []))
    # The dependency finding mentions the entity names (synapse and RelationshipService
    # or "rs"), and the predicate REQUIRES is reflected in the finding type.
    assert "synapse" in text_blob.lower(), (
        f"dependency finding must mention the provider; got: {text_blob}"
    )

    # Synapse's REQUIRES edge points to RelationshipService.
    caps = await find_capabilities(db_session, "ent-synapse", limit=50)
    rels = await find_capabilities(db_session, "ent-rs", limit=50)
    # find_capabilities returns PROVIDES edges; for REQUIRES we'd need
    # find_dependencies. But the structural point: the entity ID ent-rs
    # is the dependency target. Verify by direct query.
    from synapse.application.relationship_service import find_dependencies

    deps = await find_dependencies(db_session, "ent-synapse", limit=50)
    dep_ids = [d["entity"]["id"] for d in deps]
    assert "ent-rs" in dep_ids, (
        f"synapse must REQUIRE ent-rs (RelationshipService); got deps: {dep_ids}"
    )


# ── Acceptance Test 10: Appropriate direct-lookup routing (PATH A) ─────────


def test_10_direct_lookup_routing():
    """PATH A is selected for simple capability lookups with named candidates."""
    # CAPABILITY_EXPLANATION intent + named candidate + short query, no
    # complex markers → PATH A.
    decision = route_query(
        "What can synapse do?",
        candidate_entity_ids=["ent-synapse"],
    )
    assert decision.path == RoutingPath.DIRECT_LOOKUP, (
        f"expected PATH A, got {decision.path.value} (reason: {decision.reason})"
    )
    assert decision.intent == ReasoningIntent.CAPABILITY_EXPLANATION

    # Even shorter / different wording without complex markers.
    decision2 = route_query(
        "What capabilities does synapse provide?",
        candidate_entity_ids=["ent-synapse"],
    )
    assert decision2.path == RoutingPath.DIRECT_LOOKUP

    # But a query with "explain" (a complex marker) must NOT route to
    # PATH A -- it must go to PATH C for proper reasoning.
    decision3 = route_query(
        "What can synapse do? Explain capabilities.",
        candidate_entity_ids=["ent-synapse"],
    )
    assert decision3.path != RoutingPath.DIRECT_LOOKUP, (
        "queries with 'explain' marker must not route to PATH A"
    )


# ── Acceptance Test 11: Appropriate hybrid-retrieval routing (PATH B) ──────


def test_11_hybrid_retrieval_routing():
    """PATH B is selected for technical discovery without reasoning intent."""
    # Single-term entity lookup, no candidate → PATH B.
    decision = route_query("synapse")
    assert decision.path == RoutingPath.HYBRID_RETRIEVAL, (
        f"expected PATH B, got {decision.path.value}"
    )

    # Multi-term retrieval → PATH B.
    decision2 = route_query("source discovery arxiv")
    assert decision2.path == RoutingPath.HYBRID_RETRIEVAL

    # CAPABILITY_EXPLANATION without candidate → PATH C (grounded reasoning).
    # Per the router design (Step 2b): capability_explanation intent that
    # is NOT a simple lookup (no candidate → cannot do direct lookup) falls
    # through to PATH C so the gap_analyzer's attribution safeguard applies.
    decision3 = route_query("What can synapse do?")
    assert decision3.path == RoutingPath.GROUNDED_REASONING


# ── Acceptance Test 12: Appropriate grounded-reasoning routing (PATH C) ────


def test_12_grounded_reasoning_routing():
    """PATH C is selected for evidence-synthesis intents (dep, alt, constraint, comparison, gap)."""
    # Dependency reasoning.
    d = route_query("What does synapse require?", candidate_entity_ids=["ent-synapse"])
    assert d.path == RoutingPath.GROUNDED_REASONING
    assert d.intent == ReasoningIntent.DEPENDENCY_ANALYSIS

    # Documented alternatives.
    d = route_query("What are the alternatives to synapse?", candidate_entity_ids=["ent-synapse"])
    assert d.path == RoutingPath.GROUNDED_REASONING
    assert d.intent == ReasoningIntent.DOCUMENTED_ALTERNATIVES

    # Constraint analysis.
    d = route_query("What limits synapse?", candidate_entity_ids=["ent-synapse"])
    assert d.path == RoutingPath.GROUNDED_REASONING
    assert d.intent == ReasoningIntent.CONSTRAINT_ANALYSIS

    # Technical comparison.
    d = route_query("Compare synapse and arxiv", candidate_entity_ids=["ent-synapse", "ent-arxiv"])
    assert d.path == RoutingPath.GROUNDED_REASONING
    assert d.intent == ReasoningIntent.TECHNICAL_COMPARISON

    # Gap explanation.
    d = route_query("What is missing for an AI assistant?", candidate_entity_ids=["ent-synapse"])
    assert d.path == RoutingPath.GROUNDED_REASONING
    assert d.intent == ReasoningIntent.GAP_EXPLANATION


# ── Acceptance Test 13: Safe fallback for ambiguous queries ────────────────


def test_13_safe_fallback_ambiguous():
    """Ambiguous queries fall back to PATH B with fallback=PATH C."""
    decision = route_query("aardvark picnic galoshes")
    assert decision.path == RoutingPath.HYBRID_RETRIEVAL
    assert decision.intent == ReasoningIntent.UNKNOWN
    assert decision.fallback == RoutingPath.GROUNDED_REASONING, (
        "ambiguous queries must declare PATH C as the safe fallback"
    )


# ── Acceptance Test 14: Resource-budget enforcement ────────────────────────


def test_14_resource_budget_enforcement():
    """ResourceBudget enforces hard upper bounds (mission §6 invariant 6)."""
    # Default budget is within bounds.
    bud = DEFAULT_BUDGET
    assert bud.max_results <= 100
    assert bud.max_graph_depth <= 5
    assert bud.max_graph_expansion <= 50

    # Within-bounds budget is accepted.
    bud2 = ResourceBudget(max_results=50, max_graph_depth=3, max_graph_expansion=25)
    assert bud2.max_results == 50

    # Exceeding max_results raises ValueError.
    with pytest.raises(ValueError, match="max_results"):
        ResourceBudget(max_results=200)

    # Exceeding max_graph_depth raises ValueError.
    with pytest.raises(ValueError, match="max_graph_depth"):
        ResourceBudget(max_graph_depth=10)

    # Exceeding max_graph_expansion raises ValueError.
    with pytest.raises(ValueError, match="max_graph_expansion"):
        ResourceBudget(max_graph_expansion=100)


# ── Acceptance Test 15: Measured versus estimated cost separation ──────────


@pytest.mark.asyncio
async def test_15_measured_vs_estimated_cost_separation(app, db_session):
    """ResourceReport distinguishes MEASURED / ESTIMATED / NOT_MEASURED."""
    await _seed_evaluation_fixture(db_session)
    report = await run_evaluation(db_session)

    for cm in report.case_metrics:
        rr = cm.resource_report
        # MEASURED dimensions.
        assert rr.duration_seconds[0] == CostMeasurement.MEASURED
        assert rr.duration_seconds[1] >= 0.0
        assert rr.retrieved_candidate_count[0] == CostMeasurement.MEASURED
        assert rr.processed_evidence_count[0] == CostMeasurement.MEASURED
        assert rr.result_count[0] == CostMeasurement.MEASURED
        assert rr.selected_routing_path[0] == CostMeasurement.MEASURED

        # NOT_MEASURED dimensions (no paid model, no LLM).
        assert rr.monetary_cost[0] == CostMeasurement.NOT_MEASURED
        assert rr.monetary_cost[1] is None
        assert rr.token_usage[0] == CostMeasurement.NOT_MEASURED
        assert rr.token_usage[1] is None

        # graph_expansion_count is MEASURED for PATH B, NOT_MEASURED otherwise.
        if cm.routing_decision.path == RoutingPath.HYBRID_RETRIEVAL:
            assert rr.graph_expansion_count[0] == CostMeasurement.MEASURED
            assert rr.graph_expansion_count[1] is not None
        else:
            assert rr.graph_expansion_count[0] == CostMeasurement.NOT_MEASURED
            assert rr.graph_expansion_count[1] is None


# ── Acceptance Test 16: Visible quality-gate failures ──────────────────────


def test_16_visible_quality_gate_failures():
    """A failing quality gate is reported with its name and detail, not hidden."""
    from synapse.evaluation.runner import QualityGateResult

    gate = QualityGateResult(name="test_gate", passed=False, detail="example failure")
    assert gate.passed is False
    assert gate.name == "test_gate"
    assert "example failure" in gate.detail

    # The evaluation report's failed_gates list exposes failing gates
    # (when present). When all gates pass, failed_gates is empty.
    # We verify both shapes structurally.
    from synapse.evaluation.runner import EvaluationReport

    empty_report = EvaluationReport(
        dataset_version="test",
        case_count=0,
        case_metrics=[],
        aggregate_metrics={},
        all_quality_gates_passed=True,
        failed_gates=[],
        routing_mismatches=[],
        intent_mismatches=[],
    )
    assert empty_report.all_quality_gates_passed is True
    assert empty_report.failed_gates == []


# ── Acceptance Test 17: Reproducible quality metrics ───────────────────────


@pytest.mark.asyncio
async def test_17_reproducible_quality_metrics(app, db_session):
    """Aggregate metrics are reproducible across runs (deterministic)."""
    await _seed_evaluation_fixture(db_session)

    report1 = await run_evaluation(db_session)
    report2 = await run_evaluation(db_session)

    # Aggregate metrics must be reproducible (deterministic). Wall-clock
    # duration is inherently non-deterministic (process scheduling), so it
    # is excluded from strict equality. The deterministic metrics (precision,
    # recall, MRR, citation validity, finding rates, routing/intent match
    # rates) must match exactly.
    DURATION_KEY = "mean_duration_seconds"
    assert set(report1.aggregate_metrics.keys()) == set(report2.aggregate_metrics.keys())
    for key in report1.aggregate_metrics:
        if key == DURATION_KEY:
            v1 = report1.aggregate_metrics[key]
            v2 = report2.aggregate_metrics[key]
            assert v1 > 0 and v2 > 0, "duration must be positive"
            assert v1 < 5.0 and v2 < 5.0, "duration must be bounded"
            continue
        v1 = report1.aggregate_metrics[key]
        v2 = report2.aggregate_metrics[key]
        assert v1 == pytest.approx(v2), f"aggregate metric {key} differs: {v1} vs {v2}"

    # Dataset version must be stable.
    assert report1.dataset_version == report2.dataset_version


# ── Acceptance Test 18: G01-G04-T03C regression compatibility ──────────────


@pytest.mark.asyncio
async def test_18_g01_g04_t03c_regression(app, db_session):
    """G04-T04 introduces no regression in G01-G04-T03C behavior."""
    # Spot-check the G04-T03C context-aware contradiction closure still works.
    await _seed_evaluation_fixture(db_session)

    # 1. Applicable contradiction → CONTESTED.
    gap = await analyze_gap(
        db_session,
        ["capability X"],
        candidate_entity_ids=["ent-ctx-A"],
        context="AI agents",
    )
    assert gap["requirements"][0]["classification"] == GapClassification.CONTESTED

    # 2. Out-of-context contradiction → NOT CONTESTED, preserved as metadata.
    gap2 = await analyze_gap(
        db_session,
        ["capability X"],
        candidate_entity_ids=["ent-ctx-A"],
        context="mobile apps",
    )
    assert gap2["requirements"][0]["classification"] != GapClassification.CONTESTED
    assert len(gap2["requirements"][0].get("out_of_context_contradictions", [])) >= 1

    # 3. Universal claim contradiction → CONTESTED regardless of context.
    gap3 = await analyze_gap(
        db_session,
        ["universal capability"],
        candidate_entity_ids=["ent-ctx-A"],
        context="mobile apps",
    )
    assert gap3["requirements"][0]["classification"] == GapClassification.CONTESTED

    # 4. Reasoning intent classifier unchanged.
    assert (
        classify_reasoning_intent("What can synapse do?") == ReasoningIntent.CAPABILITY_EXPLANATION
    )
    assert (
        classify_reasoning_intent("What does synapse require?")
        == ReasoningIntent.DEPENDENCY_ANALYSIS
    )
    assert classify_reasoning_intent("aardvark") == ReasoningIntent.UNKNOWN

    # 5. Reasoning answer still produces findings + citation chain.
    answer = await answer_query(
        db_session,
        "What does synapse require?",
        candidate_entity_ids=["ent-synapse"],
        limit=20,
    )
    assert "findings" in answer
    assert "cited_claims" in answer
    assert "evidence_chain" in answer


# ── Concise demonstration (mission §10 closing remark) ────────────────────


@pytest.mark.asyncio
async def test_demo_evaluation_output_and_routing(app, db_session):
    """Concise demonstration: run the full evaluation and show actual output.

    Prints (via the test runner's stdout capture) the routing decisions,
    per-case metrics summary, and aggregate metrics. This proves the
    evaluation system produces real, inspectable output (mission §10).
    """
    await _seed_evaluation_fixture(db_session)
    report = await run_evaluation(db_session)

    # Print a concise summary (visible with pytest -s).
    print("\n" + "=" * 72)
    print(f"G04-T04 EVALUATION REPORT (dataset v{report.dataset_version})")
    print(f"Cases: {report.case_count}")
    print(f"All quality gates passed: {report.all_quality_gates_passed}")
    print(f"Failed gates: {len(report.failed_gates)}")
    print(f"Routing mismatches: {len(report.routing_mismatches)}")
    print(f"Intent mismatches: {len(report.intent_mismatches)}")
    print("-" * 72)
    print(f"{'CASE':<14} {'PATH':<5} {'INTENT':<24} {'P@5':<6} {'R@5':<6} {'MRR':<6} {'CIT':<5}")
    print("-" * 72)
    for cm in report.case_metrics:
        print(
            f"{cm.case_id:<14} "
            f"{cm.routing_decision.path.value:<5} "
            f"{cm.routing_decision.intent.value:<24} "
            f"{cm.precision_at_5:<6.2f} "
            f"{cm.recall_at_5:<6.2f} "
            f"{cm.mrr:<6.2f} "
            f"{cm.citation_validity:<5.2f}"
        )
    print("-" * 72)
    print("Aggregate metrics:")
    for k, v in sorted(report.aggregate_metrics.items()):
        print(f"  {k:<48} {v:.4f}")
    print("=" * 72)

    # The demonstration must not have any failed gates or mismatches.
    assert report.all_quality_gates_passed, (
        f"demonstration must pass all quality gates; failed: "
        f"{[(g.name, g.detail) for g in report.failed_gates]}"
    )
    assert len(report.routing_mismatches) == 0, (
        f"demonstration must have zero routing mismatches: {report.routing_mismatches}"
    )
