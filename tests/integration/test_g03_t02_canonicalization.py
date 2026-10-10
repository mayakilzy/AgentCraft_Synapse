"""Tests for G03-T02: canonicalization and idempotent knowledge persistence.

Per the user's G03-T02 authorization §Acceptance tests.
Updated for reliability closure: uses JobRow-based completion markers.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from synapse.application.canonicalization import (
    canonical_name_for,
    canonical_uri_for,
    normalize_arxiv_id,
    normalize_doi,
    normalize_github_repo,
    normalize_url,
    persist_extraction_results,
    process_pending_extraction_jobs,
    queue_extraction_job,
)
from synapse.domain._base import EntityType, EpistemicState
from synapse.domain.extraction_contract import (
    ExtractionResult,
    KnowledgeUnitType,
    SourceSpan,
)
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

FIXTURE_TEXT_1 = """This paper introduces the self-attention mechanism.
The work is at https://arxiv.org/abs/1706.03762 and
github.com/tensorflow/tensor2tensor. The approach requires GPU."""

FIXTURE_TEXT_2 = """Another paper citing the Transformer architecture.
See https://arxiv.org/abs/1706.03762 for the original. The DOI is
10.48550/arXiv.1706.03762."""

FIXTURE_TEXT_CHANGED = """This paper introduces the multi-head attention.
The work is at https://arxiv.org/abs/1706.03762 and
github.com/tensorflow/tensor2tensor. The approach requires TPU."""


async def _create_ef(session, fragment_id="ef-1", text=FIXTURE_TEXT_1, fp="fp1"):
    source = SourceRow(
        id=f"src-{fragment_id}",
        canonical_uri=f"https://arxiv.org/abs/0000.{fragment_id}",
        source_type="paper",
        status="extracted",
    )
    session.add(source)
    await session.flush()
    ef = EvidenceFragmentRow(
        id=fragment_id,
        acquisition_id=f"acq-{fragment_id}",
        source_id=source.id,
        source_uri=f"https://arxiv.org/abs/0000.{fragment_id}",
        exact_excerpt=text,
        excerpt_hash="abcdef0123456789",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint=fp,
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    session.add(ef)
    await session.flush()
    return ef


async def _run_extraction(session, ef_id, fp=None):
    """Run extraction via the job pipeline (creates a succeeded JobRow)."""
    job = await queue_extraction_job(session, ef_id, content_fingerprint=fp, requester="test")
    await session.commit()
    results = await process_pending_extraction_jobs(session, max_jobs=5)
    await session.commit()
    return job, results


# ── Acceptance Test 1: Canonical identifier normalization ──────────────────


class TestCanonicalNormalization:
    def test_arxiv_id_normalization(self):
        assert normalize_arxiv_id("1706.03762") == "1706.03762"
        assert normalize_arxiv_id("arXiv:1706.03762") == "1706.03762"
        assert normalize_arxiv_id("https://arxiv.org/abs/1706.03762") == "1706.03762"
        assert normalize_arxiv_id("1706.03762v2") == "1706.03762"

    def test_doi_normalization(self):
        assert normalize_doi("10.48550/arXiv.1706.03762") == "10.48550/arXiv.1706.03762"
        assert normalize_doi("DOI:10.48550/arXiv.1706.03762") == "10.48550/arXiv.1706.03762"
        assert normalize_doi("10.48550/arXiv.1706.03762.") == "10.48550/arXiv.1706.03762"

    def test_github_repo_normalization(self):
        assert (
            normalize_github_repo("github.com/tensorflow/tensor2tensor")
            == "tensorflow/tensor2tensor"
        )
        assert (
            normalize_github_repo("github.com/tensorflow/tensor2tensor.")
            == "tensorflow/tensor2tensor"
        )

    def test_url_normalization(self):
        assert normalize_url("https://example.com/path.") == "https://example.com/path"

    def test_canonical_name_for_arxiv(self):
        result = ExtractionResult(
            evidence_fragment_id="ef1",
            source_span=SourceSpan(
                evidence_fragment_id="ef1", start_offset=0, end_offset=10, excerpt="1706.03762"
            ),
            unit_type=KnowledgeUnitType.IDENTIFIER,
            entity_kind=EntityType.PAPER,
            canonical_name="arXiv:1706.03762",
            attributes={"pattern": "arxiv_id"},
            extraction_method="regex-arxiv_id-v1",
            epistemic_state=EpistemicState.SUPPORTED,
        )
        assert canonical_name_for(result) == "arXiv:1706.03762"

    def test_canonical_uri_for_arxiv(self):
        result = ExtractionResult(
            evidence_fragment_id="ef1",
            source_span=SourceSpan(
                evidence_fragment_id="ef1", start_offset=0, end_offset=10, excerpt="1706.03762"
            ),
            unit_type=KnowledgeUnitType.IDENTIFIER,
            entity_kind=EntityType.PAPER,
            canonical_name="arXiv:1706.03762",
            attributes={"pattern": "arxiv_id"},
            extraction_method="regex-arxiv_id-v1",
            epistemic_state=EpistemicState.SUPPORTED,
        )
        assert canonical_uri_for(result) == "https://arxiv.org/abs/1706.03762"

    def test_canonical_name_for_doi(self):
        result = ExtractionResult(
            evidence_fragment_id="ef1",
            source_span=SourceSpan(
                evidence_fragment_id="ef1", start_offset=0, end_offset=4, excerpt="test"
            ),
            unit_type=KnowledgeUnitType.IDENTIFIER,
            entity_kind=EntityType.PAPER,
            canonical_name="DOI:10.48550/arXiv.1706.03762",
            attributes={"pattern": "doi"},
            extraction_method="regex-doi-v1",
            epistemic_state=EpistemicState.SUPPORTED,
        )
        assert canonical_name_for(result) == "DOI:10.48550/arXiv.1706.03762"


# ── Acceptance Test 2: Duplicate entity and claim prevention ───────────────


@pytest.mark.asyncio
async def test_duplicate_entity_prevention(app, db_session):
    await _create_ef(db_session, "ef-dup-1", FIXTURE_TEXT_1, "fp1")
    _job, results = await _run_extraction(db_session, "ef-dup-1", "fp1")
    assert any(r.get("ok") for r in results)

    entities_1 = (await db_session.execute(select(EntityRow))).scalars().all()
    count_1 = len(entities_1)
    assert count_1 > 0

    # Re-extract via job pipeline — should skip (succeeded job exists)
    _job2, _results2 = await _run_extraction(db_session, "ef-dup-1", "fp1")
    # job2 should be None (duplicate prevented) or results should show skip
    entities_2 = (await db_session.execute(select(EntityRow))).scalars().all()
    assert len(entities_2) == count_1, f"Duplicate entities: {count_1} → {len(entities_2)}"


@pytest.mark.asyncio
async def test_duplicate_claim_prevention(app, db_session):
    await _create_ef(db_session, "ef-dup-claim", FIXTURE_TEXT_1, "fp1")
    _job, results = await _run_extraction(db_session, "ef-dup-claim", "fp1")

    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()
    count_1 = len(claims_1)

    # Re-extract — should not create duplicates
    _job2, _results2 = await _run_extraction(db_session, "ef-dup-claim", "fp1")
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    assert len(claims_2) == count_1


# ── Acceptance Test 3: Multiple evidence sources referencing one entity ────


@pytest.mark.asyncio
async def test_multiple_evidence_sources_one_entity(app, db_session):
    await _create_ef(db_session, "ef-multi-1", FIXTURE_TEXT_1, "fp1")
    source2 = SourceRow(
        id="src-multi-2",
        canonical_uri="https://example.com/other",
        source_type="paper",
        status="extracted",
    )
    db_session.add(source2)
    await db_session.flush()
    ef2 = EvidenceFragmentRow(
        id="ef-multi-2",
        acquisition_id="acq-2",
        source_id=source2.id,
        source_uri="https://example.com/other",
        exact_excerpt=FIXTURE_TEXT_2,
        excerpt_hash="xyz7890123456789",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint="fp2",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    db_session.add(ef2)
    await db_session.flush()

    await _run_extraction(db_session, "ef-multi-1", "fp1")
    await _run_extraction(db_session, "ef-multi-2", "fp2")

    stmt = select(EntityRow).where(EntityRow.canonical_name == "arXiv:1706.03762")
    entities = (await db_session.execute(stmt)).scalars().all()
    assert len(entities) == 1, f"Expected 1 arXiv entity, found {len(entities)}"

    entity = entities[0]
    claims = (
        (await db_session.execute(select(ClaimRow).where(ClaimRow.subject_ref == entity.id)))
        .scalars()
        .all()
    )
    all_refs = []
    for c in claims:
        if c.evidence_refs:
            all_refs.extend(json.loads(c.evidence_refs))
    assert "ef-multi-1" in all_refs or "ef-multi-2" in all_refs


# ── Acceptance Test 4: Conflicting claims preserved ────────────────────────


@pytest.mark.asyncio
async def test_conflicting_claims_preserved(app, db_session):
    text_a = "The approach requires GPU acceleration for training."
    text_b = "The approach requires TPU acceleration for training."

    await _create_ef(db_session, "ef-conflict-1", text_a, "fp-a")
    source2 = SourceRow(
        id="src-c2", canonical_uri="https://example.com/b", source_type="paper", status="extracted"
    )
    db_session.add(source2)
    await db_session.flush()
    ef2 = EvidenceFragmentRow(
        id="ef-conflict-2",
        acquisition_id="acq-c2",
        source_id=source2.id,
        source_uri="https://example.com/b",
        exact_excerpt=text_b,
        excerpt_hash="hash000000000001",
        retrieved_at="2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint="fp-b",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    db_session.add(ef2)
    await db_session.flush()

    await _run_extraction(db_session, "ef-conflict-1", "fp-a")
    await _run_extraction(db_session, "ef-conflict-2", "fp-b")

    all_claims = (await db_session.execute(select(ClaimRow))).scalars().all()
    propositions = [c.proposition for c in all_claims]
    assert any("GPU" in p for p in propositions), f"GPU claim lost: {propositions}"
    assert any("TPU" in p for p in propositions), f"TPU claim lost: {propositions}"


# ── Acceptance Test 5: Idempotent repeated extraction ───────────────────────


@pytest.mark.asyncio
async def test_idempotent_repeated_extraction(app, db_session):
    await _create_ef(db_session, "ef-idem", FIXTURE_TEXT_1, "fp1")
    _job, results = await _run_extraction(db_session, "ef-idem", "fp1")
    assert any(r.get("ok") for r in results)

    entities_1 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()
    spans_1 = (await db_session.execute(select(SourceSpanRow))).scalars().all()

    # Re-run — queue_extraction_job should return None (already succeeded)
    job2 = await queue_extraction_job(db_session, "ef-idem", content_fingerprint="fp1")
    assert job2 is None, "Duplicate job created despite succeeded job"

    entities_2 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    spans_2 = (await db_session.execute(select(SourceSpanRow))).scalars().all()

    assert len(entities_1) == len(entities_2)
    assert len(claims_1) == len(claims_2)
    assert len(spans_1) == len(spans_2)


# ── Acceptance Test 6: Changed-content reprocessing ────────────────────────


@pytest.mark.asyncio
async def test_changed_content_reprocessing(app, db_session):
    await _create_ef(db_session, "ef-change", FIXTURE_TEXT_1, "fp-original")
    _job1, results1 = await _run_extraction(db_session, "ef-change", "fp-original")
    assert any(r.get("ok") for r in results1)

    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()
    claim_count_1 = len(claims_1)
    assert claim_count_1 > 0

    # Content changes
    ef_row = (
        await db_session.execute(
            select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == "ef-change")
        )
    ).scalar_one()
    ef_row.exact_excerpt = FIXTURE_TEXT_CHANGED
    ef_row.content_fingerprint = "fp-changed"
    await db_session.commit()

    # Re-extract with new fingerprint — should NOT skip
    job2, _results2 = await _run_extraction(db_session, "ef-change", "fp-changed")
    # job2 should be a new job (different fingerprint)
    assert job2 is not None, "New job should be created for changed content"

    # Old claims preserved
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    assert len(claims_2) >= claim_count_1, f"Old claims lost: {claim_count_1} → {len(claims_2)}"


# ── Acceptance Test 7: Failure recovery ────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_recovery_preserves_acquisition(app, db_session):
    await _create_ef(db_session, "ef-fail", FIXTURE_TEXT_1, "fp1")

    with patch(
        "synapse.application.canonicalization.extract", side_effect=RuntimeError("simulated")
    ):
        with pytest.raises(RuntimeError, match="simulated"):
            await persist_extraction_results(db_session, "ef-fail", FIXTURE_TEXT_1)

    ef_check = (
        await db_session.execute(
            select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == "ef-fail")
        )
    ).scalar_one_or_none()
    assert ef_check is not None
    assert ef_check.exact_excerpt == FIXTURE_TEXT_1

    # Retry — works now
    r = await persist_extraction_results(
        db_session, "ef-fail", FIXTURE_TEXT_1, content_fingerprint="fp1"
    )
    assert r["ok"] is True
    assert r["extracted_count"] > 0


@pytest.mark.asyncio
async def test_job_queue_decoupled_from_acquisition(app, db_session):
    await _create_ef(db_session, "ef-job", FIXTURE_TEXT_1, "fp1")
    await db_session.commit()

    job = await queue_extraction_job(
        db_session, "ef-job", content_fingerprint="fp1", requester="test"
    )
    assert job is not None
    assert job.kind == "extract_knowledge"
    await db_session.commit()

    results = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()
    assert any(r.get("ok") for r in results)
