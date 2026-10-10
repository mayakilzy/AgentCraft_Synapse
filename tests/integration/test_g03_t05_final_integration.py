"""G03-T05: End-to-end integration test with realistic technical demo.

Demonstrates the complete flow:
  EvidenceFragment → Extraction → Canonical Entity/Claim →
  Relationship → Verification Assessment → Queryable Knowledge.

Uses a realistic scenario: the Transformer architecture paper
(arXiv:1706.03762) with self-attention technique, GPU constraint,
and multiple evidence fragments.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from synapse.application.canonicalization import (
    process_pending_extraction_jobs,
    queue_extraction_job,
)
from synapse.application.relationship_service import (
    create_relationship,
    find_capabilities,
    find_paths,
)
from synapse.application.verification import (
    VerificationOutcome,
    assess_claim,
)
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

# ── Realistic fixture: Transformer architecture paper ───────────────────────

TRANSFORMER_TEXT = """The Transformer Architecture

This paper introduces the self-attention mechanism, a novel approach
to sequence modeling. The approach requires GPU acceleration for
practical training speeds. Our experiments show that Transformers
outperform RNNs on long sequences.

The reference implementation is hosted at github.com/tensorflow/tensor2tensor.
The DOI for this work is 10.48550/arXiv.1706.03762.
"""

SECOND_SOURCE_TEXT = """A Survey of Attention Mechanisms

This survey confirms that the self-attention mechanism, originally
introduced in arXiv:1706.03762, has become a standard technique.
The approach is applicable to NLP tasks with long context windows.
"""


async def _setup_demo_data(db_session):
    """Set up the demo: two sources, two evidence fragments, one about
    Transformers, one survey citing them."""
    # Source 1: original Transformer paper
    src1 = SourceRow(
        id="src-transformer",
        canonical_uri="https://arxiv.org/abs/1706.03762",
        source_type="paper",
        status="extracted",
    )
    db_session.add(src1)
    await db_session.flush()

    ef1 = EvidenceFragmentRow(
        id="ef-transformer",
        acquisition_id="acq-1",
        source_id=src1.id,
        source_uri=src1.canonical_uri,
        exact_excerpt=TRANSFORMER_TEXT,
        excerpt_hash="abcdef0123456789",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint="fp-transformer",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    db_session.add(ef1)

    # Source 2: survey paper
    src2 = SourceRow(
        id="src-survey",
        canonical_uri="https://example.com/survey",
        source_type="paper",
        status="extracted",
    )
    db_session.add(src2)
    await db_session.flush()

    ef2 = EvidenceFragmentRow(
        id="ef-survey",
        acquisition_id="acq-2",
        source_id=src2.id,
        source_uri=src2.canonical_uri,
        exact_excerpt=SECOND_SOURCE_TEXT,
        excerpt_hash="xyz7890123456789",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint="fp-survey",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    db_session.add(ef2)
    await db_session.flush()
    await db_session.commit()

    return ef1, ef2


# ── End-to-end: EvidenceFragment → Extraction → Entity/Claim →
#    Relationship → Verification → Queryable Result ──────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_pipeline(app, db_session):
    """Complete vertical slice: from evidence to queryable knowledge."""

    # ── 1. Set up evidence fragments (simulating G02 acquisition) ───────
    ef1, ef2 = await _setup_demo_data(db_session)

    # ── 2. Extract knowledge via job pipeline (G03-T01 + T02) ──────────
    job1 = await queue_extraction_job(
        db_session, "ef-transformer", content_fingerprint="fp-transformer", requester="demo"
    )
    job2 = await queue_extraction_job(
        db_session, "ef-survey", content_fingerprint="fp-survey", requester="demo"
    )
    await db_session.commit()

    results = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    # Verify extraction succeeded
    assert any(r.get("ok") for r in results), f"Extraction failed: {results}"

    # ── 3. Verify entities were created ─────────────────────────────────
    entities = (await db_session.execute(select(EntityRow))).scalars().all()
    entity_names = {e.canonical_name for e in entities}
    assert any("1706.03762" in n for n in entity_names), f"arXiv entity not found: {entity_names}"
    assert any("self-attention" in n.lower() for n in entity_names), (
        f"Technique entity not found: {entity_names}"
    )

    # ── 4. Create typed relationships (G03-T03) ────────────────────────
    # Find the technique entity
    technique_entities = [e for e in entities if "self-attention" in e.canonical_name.lower()]
    assert len(technique_entities) >= 1
    technique = technique_entities[0]

    # Find the paper entity
    paper_entities = [e for e in entities if "1706.03762" in e.canonical_name]
    assert len(paper_entities) >= 1
    paper = paper_entities[0]

    # Paper ENABLES Technique
    r1 = await create_relationship(
        db_session,
        from_entity_id=paper.id,
        to_entity_id=technique.id,
        predicate="ENABLES",
        evidence_refs=["ef-transformer"],
        origin="explicit",
    )
    await db_session.commit()
    assert r1["ok"] is True

    # ── 5. Verify claims exist and assess them (G03-T04) ────────────────
    claims = (await db_session.execute(select(ClaimRow))).scalars().all()
    assert len(claims) > 0

    # Assess each claim
    for claim in claims:
        r = await assess_claim(db_session, claim.id, requester="demo")
        await db_session.commit()
        assert r["ok"] is True

    # ── 6. Query the knowledge graph (API-ready, tested via service) ────

    # What capabilities does the paper enable?
    caps = await find_capabilities(db_session, paper.id)
    assert len(caps) > 0

    # Find paths from paper to its capabilities
    paths = await find_paths(db_session, paper.id, technique.id, max_depth=5)
    assert len(paths) >= 1

    # ── 7. Verify assessment outcomes ───────────────────────────────────
    # Claims with evidence from 1 source → SOURCE_SUPPORTED (not VERIFIED)
    for claim in claims:
        from synapse.storage.models import AuditEventRow

        assess_stmt = (
            select(AuditEventRow)
            .where(
                AuditEventRow.target_id == claim.id,
                AuditEventRow.event_type == "verification.assessed",
            )
            .order_by(AuditEventRow.created_at.desc())
            .limit(1)
        )
        assessment_row = (await db_session.execute(assess_stmt)).scalar_one_or_none()
        if assessment_row:
            outcome = assessment_row.payload.get("outcome")
            # deterministic-v2: VERIFIED is unreachable
            assert outcome != VerificationOutcome.VERIFIED, (
                f"Claim {claim.id} was auto-verified — VERIFIED is unreachable in v2"
            )

    # ── 8. Verify source spans are preserved ─────────────────────────────
    spans = (await db_session.execute(select(SourceSpanRow))).scalars().all()
    assert len(spans) > 0
    for span in spans:
        assert span.start_offset < span.end_offset
        assert span.excerpt is not None

    # ── 9. Idempotency: re-running extraction does not duplicate ────────
    entity_count_before = len(entities)
    job3 = await queue_extraction_job(
        db_session, "ef-transformer", content_fingerprint="fp-transformer", requester="demo"
    )
    await db_session.commit()
    # job3 should be None (already succeeded with same fingerprint)
    assert job3 is None, "Duplicate job created despite succeeded job"

    entities_after = (await db_session.execute(select(EntityRow))).scalars().all()
    assert len(entities_after) == entity_count_before


# ── Verification policy negative tests in integrated pipeline ──────────────


@pytest.mark.asyncio
async def test_integrated_multiple_urls_not_verified(app, db_session):
    """In the integrated pipeline, two different source_uris do NOT
    automatically produce VERIFIED — conservative origin independence."""
    src1 = SourceRow(
        id="src-url1",
        canonical_uri="https://a.example.com/paper",
        source_type="paper",
        status="extracted",
    )
    src2 = SourceRow(
        id="src-url2",
        canonical_uri="https://b.example.com/paper",
        source_type="paper",
        status="extracted",
    )
    db_session.add_all([src1, src2])
    await db_session.flush()

    ef1 = EvidenceFragmentRow(
        id="ef-url1",
        acquisition_id="acq-u1",
        source_id=src1.id,
        source_uri=src1.canonical_uri,
        exact_excerpt="Paper at URL A. See arxiv 1706.03762.",
        excerpt_hash="hash000000000001",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="test",
        content_fingerprint="fp-u1",
        toolkit_commit_sha="fd9df34",
    )
    ef2 = EvidenceFragmentRow(
        id="ef-url2",
        acquisition_id="acq-u2",
        source_id=src2.id,
        source_uri=src2.canonical_uri,
        exact_excerpt="Paper at URL B. See arxiv 1706.03762.",
        excerpt_hash="hash000000000002",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="test",
        content_fingerprint="fp-u2",
        toolkit_commit_sha="fd9df34",
    )
    db_session.add_all([ef1, ef2])
    await db_session.flush()

    # Extract from both
    await queue_extraction_job(db_session, "ef-url1", content_fingerprint="fp-u1")
    await queue_extraction_job(db_session, "ef-url2", content_fingerprint="fp-u2")
    await db_session.commit()
    await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    # Find claims that have evidence from BOTH fragments
    claims = (await db_session.execute(select(ClaimRow))).scalars().all()
    for claim in claims:
        refs = json.loads(claim.evidence_refs) if claim.evidence_refs else []
        if "ef-url1" in refs and "ef-url2" in refs:
            r = await assess_claim(db_session, claim.id, requester="test")
            await db_session.commit()
            assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED
            assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED


@pytest.mark.asyncio
async def test_integrated_contradictory_evidence_contested(app, db_session):
    """Contradictory evidence in the integrated pipeline → CONTESTED."""
    src1 = SourceRow(
        id="src-contra-sup",
        canonical_uri="https://example.com/sup",
        source_type="paper",
        status="extracted",
    )
    src2 = SourceRow(
        id="src-contra-opp",
        canonical_uri="https://example.com/opp",
        source_type="paper",
        status="extracted",
    )
    db_session.add_all([src1, src2])
    await db_session.flush()

    ef_sup = EvidenceFragmentRow(
        id="ef-contra-sup",
        acquisition_id="acq-cs",
        source_id=src1.id,
        source_uri=src1.canonical_uri,
        exact_excerpt="X requires GPU.",
        excerpt_hash="hash000000000003",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="test",
        content_fingerprint="fp-cs",
        toolkit_commit_sha="fd9df34",
    )
    ef_opp = EvidenceFragmentRow(
        id="ef-contra-opp",
        acquisition_id="acq-co",
        source_id=src2.id,
        source_uri=src2.canonical_uri,
        exact_excerpt="X requires TPU.",
        excerpt_hash="hash000000000004",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="test",
        content_fingerprint="fp-co",
        toolkit_commit_sha="fd9df34",
    )
    db_session.add_all([ef_sup, ef_opp])
    await db_session.flush()

    # Create a claim with supporting and contradicting evidence
    claim = ClaimRow(
        id="claim-contra",
        proposition="X requires GPU.",
        evidence_refs=json.dumps(["ef-contra-sup"]),
        contradicting_refs=json.dumps(["ef-contra-opp"]),
        epistemic_state="supported",
        extraction_method="test",
        version=1,
    )
    db_session.add(claim)
    await db_session.commit()

    r = await assess_claim(db_session, "claim-contra", requester="test")
    await db_session.commit()

    assert r["assessment"]["outcome"] == VerificationOutcome.CONTESTED
    assert r["assessment"]["supporting_evidence_count"] == 1
    assert r["assessment"]["opposing_evidence_count"] == 1


@pytest.mark.asyncio
async def test_integrated_missing_capability_not_evidenced(app, db_session):
    """Missing capability without negative evidence → NOT_EVIDENCED."""
    from synapse.application.verification import assess_missing_capability

    r = await assess_missing_capability(db_session, "ent-nonexistent", "SomeCap")
    assert r["outcome"] == VerificationOutcome.NOT_EVIDENCED
    assert "absence_of_evidence" in r["reason_code"]


@pytest.mark.asyncio
async def test_integrated_idempotent_reprocessing(app, db_session):
    """Re-running extraction + assessment does not duplicate anything."""
    ef1, ef2 = await _setup_demo_data(db_session)

    # First run
    await queue_extraction_job(db_session, "ef-transformer", content_fingerprint="fp-transformer")
    await db_session.commit()
    await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    entities_1 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()

    # Assess all claims
    for c in claims_1:
        await assess_claim(db_session, c.id, requester="test")
    await db_session.commit()

    # Re-run assessment — should be idempotent
    for c in claims_1:
        r = await assess_claim(db_session, c.id, requester="test")
        assert r["idempotent"] is True
    await db_session.commit()

    entities_2 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    assert len(entities_1) == len(entities_2)
    assert len(claims_1) == len(claims_2)


# ── API integration tests ─────────────────────────────────────────────────


def test_api_knowledge_endpoints_require_auth(app, client):
    """All knowledge endpoints require authentication."""
    r = client.get("/api/v1/entities")
    assert r.status_code == 401

    r = client.get("/api/v1/relationships")
    assert r.status_code == 401

    r = client.post("/api/v1/knowledge/search", json={"query": "test"})
    assert r.status_code == 401


def test_api_knowledge_endpoints_with_auth(app, client, auth_headers_reader):
    """Knowledge endpoints return 200 with auth."""
    r = client.get("/api/v1/entities", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["api_version"] == "v1"

    r = client.get("/api/v1/relationships", headers=auth_headers_reader)
    assert r.status_code == 200

    r = client.post("/api/v1/knowledge/search", headers=auth_headers_reader, json={"query": "test"})
    assert r.status_code == 200


def test_api_openapi_includes_knowledge_routes(app):
    """OpenAPI schema includes the knowledge endpoints."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/entities" in paths
    assert "/api/v1/entities/{entity_id}" in paths
    assert "/api/v1/relationships" in paths
    assert "/api/v1/relationships/{relationship_id}" in paths
    assert "/api/v1/claims/{claim_id}/evidence" in paths
    assert "/api/v1/knowledge/search" in paths


def test_api_remaining_501_placeholders(app):
    """Reasoning/innovation/experiment endpoints remain 501 (not in G03 scope)."""
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/reasoning/queries" in paths
    assert "/api/v1/innovations/generate" in paths
    assert "/api/v1/experiments" in paths
    assert "/api/v1/future/scenarios" in paths
