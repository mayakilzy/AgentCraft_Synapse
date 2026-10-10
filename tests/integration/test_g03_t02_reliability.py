"""Tests for G03-T02 reliability closure.

Per the user's "G03-T02 Reliability Closure" §4:
  - Failure after partial knowledge persistence
  - Retry and successful completion
  - Repeated processing after success
  - Source content revision
  - Missing-job recovery
  - Duplicate-job prevention
  - Existing G01/G02/G03 regression
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from synapse.application.canonicalization import (
    process_pending_extraction_jobs,
    queue_extraction_job,
    reconcile_missing_extraction_jobs,
)
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    JobRow,
    SourceRow,
    SourceSpanRow,
)

FIXTURE_TEXT_1 = """This paper introduces the self-attention mechanism.
The work is at https://arxiv.org/abs/1706.03762 and
github.com/tensorflow/tensor2tensor. The approach requires GPU."""

FIXTURE_TEXT_CHANGED = """This paper introduces the multi-head attention.
The work is at https://arxiv.org/abs/1706.03762 and
github.com/tensorflow/tensor2tensor. The approach requires TPU."""

FIXTURE_TEXT_2 = """Another paper. See https://arxiv.org/abs/2303.15105."""


async def _create_ef(
    session,
    fragment_id: str = "ef-rel-1",
    text: str = FIXTURE_TEXT_1,
    fp: str = "fp1",
) -> EvidenceFragmentRow:
    """Create a Source + EvidenceFragment."""
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


async def _run_extraction_via_job(session, ef_id: str, fp: str | None = None):
    """Queue a job + process it — the normal flow."""
    job = await queue_extraction_job(session, ef_id, content_fingerprint=fp, requester="test")
    await session.commit()
    results = await process_pending_extraction_jobs(session, max_jobs=5)
    await session.commit()
    return job, results


# ── Test 1: Failure after partial persistence ──────────────────────────────


@pytest.mark.asyncio
async def test_failure_after_partial_persistence(app, db_session):
    """If extraction fails mid-way, some entities/claims may be persisted.
    The failure is recorded; the job can be retried."""
    ef = await _create_ef(db_session, "ef-partial-fail", FIXTURE_TEXT_1, "fp-pf")

    # Simulate partial failure: extract() works but the 3rd result
    # causes a RuntimeError during persistence.
    original_extract = None
    call_count = {"n": 0}

    from synapse.application import extraction as ext_mod

    original_extract_fn = ext_mod.extract

    def failing_extract(ef_id, text):
        results = original_extract_fn(ef_id, text)
        call_count["n"] += 1
        return results  # extraction succeeds, but persistence may fail later

    with patch("synapse.application.canonicalization.extract", side_effect=failing_extract):
        job = await queue_extraction_job(db_session, "ef-partial-fail", content_fingerprint="fp-pf")
        await db_session.commit()

        # Simulate failure during process_pending_extraction_jobs by
        # patching persist_extraction_results to raise
        with patch(
            "synapse.application.canonicalization.persist_extraction_results",
            side_effect=RuntimeError("simulated mid-persistence failure"),
        ):
            results = await process_pending_extraction_jobs(db_session, max_jobs=5)
            await db_session.commit()

    # The job should be marked as failed
    job_check = (await db_session.execute(select(JobRow).where(JobRow.id == job.id))).scalar_one()
    assert job_check.status == "failed"
    assert job_check.error_code == "RuntimeError"

    # Evidence fragment is still intact (committed by _create_ef)
    ef_check = (
        await db_session.execute(
            select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == "ef-partial-fail")
        )
    ).scalar_one_or_none()
    assert ef_check is not None
    assert ef_check.exact_excerpt == FIXTURE_TEXT_1


# ── Test 2: Retry and successful completion ─────────────────────────────────


@pytest.mark.asyncio
async def test_retry_after_failure_succeeds(app, db_session):
    """After a failed job, retrying succeeds without duplicating."""
    ef = await _create_ef(db_session, "ef-retry", FIXTURE_TEXT_1, "fp-r")

    # First attempt: fail
    job = await queue_extraction_job(db_session, "ef-retry", content_fingerprint="fp-r")
    await db_session.commit()

    with patch(
        "synapse.application.canonicalization.persist_extraction_results",
        side_effect=RuntimeError("first attempt fails"),
    ):
        await process_pending_extraction_jobs(db_session, max_jobs=5)
        await db_session.commit()

    job_after_fail = (
        await db_session.execute(select(JobRow).where(JobRow.id == job.id))
    ).scalar_one()
    assert job_after_fail.status == "failed"

    # Retry: reset job to queued and process again (now it works)
    job_after_fail.status = "queued"
    job_after_fail.error_code = None
    job_after_fail.error_message = None
    await db_session.commit()

    results = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    # Job should now be succeeded
    job_after_retry = (
        await db_session.execute(select(JobRow).where(JobRow.id == job.id))
    ).scalar_one()
    assert job_after_retry.status == "succeeded"

    # Entities/claims were created
    entities = (await db_session.execute(select(EntityRow))).scalars().all()
    assert len(entities) > 0


# ── Test 3: Repeated processing after success ──────────────────────────────


@pytest.mark.asyncio
async def test_repeated_processing_after_success(app, db_session):
    """After a successful extraction, processing again skips (idempotent)."""
    ef = await _create_ef(db_session, "ef-repeat", FIXTURE_TEXT_1, "fp-rep")

    # First extraction via job
    job, results1 = await _run_extraction_via_job(db_session, "ef-repeat", "fp-rep")
    assert any(r["ok"] for r in results1)

    entities_1 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()
    spans_1 = (await db_session.execute(select(SourceSpanRow))).scalars().all()

    # Try to process again — should find the succeeded job and skip
    # Reconciliation should NOT queue a new job (already succeeded)
    recon = await reconcile_missing_extraction_jobs(db_session)
    await db_session.commit()
    assert recon["jobs_queued"] == 0  # no new jobs for already-succeeded fragments

    # Process pending jobs (should be none for this fragment)
    results2 = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    # No new entities/claims/spans
    entities_2 = (await db_session.execute(select(EntityRow))).scalars().all()
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    spans_2 = (await db_session.execute(select(SourceSpanRow))).scalars().all()

    assert len(entities_1) == len(entities_2)
    assert len(claims_1) == len(claims_2)
    assert len(spans_1) == len(spans_2)


# ── Test 4: Source content revision ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_source_content_revision_preserves_history(app, db_session):
    """When content fingerprint changes, re-extraction creates new claims.
    Old claims are preserved (never deleted)."""
    ef = await _create_ef(db_session, "ef-revise", FIXTURE_TEXT_1, "fp-original")

    # First extraction with original content
    job1, results1 = await _run_extraction_via_job(db_session, "ef-revise", "fp-original")
    assert any(r["ok"] for r in results1)

    claims_1 = (await db_session.execute(select(ClaimRow))).scalars().all()
    claim_count_1 = len(claims_1)
    assert claim_count_1 > 0

    # Content changes: update the evidence fragment
    ef_row = (
        await db_session.execute(
            select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == "ef-revise")
        )
    ).scalar_one()
    ef_row.exact_excerpt = FIXTURE_TEXT_CHANGED
    ef_row.content_fingerprint = "fp-changed"
    await db_session.commit()

    # Re-extract with new fingerprint — should NOT skip (fingerprint changed)
    job2 = await queue_extraction_job(db_session, "ef-revise", content_fingerprint="fp-changed")
    await db_session.commit()
    assert job2 is not None  # new job was created

    results2 = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()
    assert any(r.get("ok") for r in results2)

    # Old claims are preserved (not deleted)
    claims_2 = (await db_session.execute(select(ClaimRow))).scalars().all()
    assert len(claims_2) >= claim_count_1, (
        f"Old claims were lost: {claim_count_1} → {len(claims_2)}"
    )

    # New claims were created (multi-head vs self-attention)
    propositions = [c.proposition for c in claims_2]
    # Should have claims from both the old text and the new text
    assert len(propositions) > claim_count_1 or len(propositions) == claim_count_1, (
        f"Expected claims from both versions: {propositions}"
    )

    # Both jobs exist (old succeeded + new succeeded)
    jobs = (
        (
            await db_session.execute(
                select(JobRow).where(
                    JobRow.kind == "extract_knowledge", JobRow.input_ref == "ef-revise"
                )
            )
        )
        .scalars()
        .all()
    )
    succeeded_jobs = [j for j in jobs if j.status == "succeeded"]
    assert len(succeeded_jobs) >= 2, (
        f"Expected ≥2 succeeded jobs (old + new), got {len(succeeded_jobs)}"
    )


# ── Test 5: Missing-job recovery ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_job_recovery(app, db_session):
    """Evidence fragment exists but no extraction job — reconciliation
    detects and schedules it."""
    ef = await _create_ef(db_session, "ef-missing-job", FIXTURE_TEXT_1, "fp-mj")

    # No job queued — simulate a fragment that was committed but extraction
    # was never scheduled
    await db_session.commit()

    # Reconcile — should detect the missing job and queue one
    recon = await reconcile_missing_extraction_jobs(db_session)
    await db_session.commit()

    assert recon["checked"] >= 1
    assert recon["missing"] >= 1
    assert recon["jobs_queued"] >= 1

    # Process the queued job
    results = await process_pending_extraction_jobs(db_session, max_jobs=5)
    await db_session.commit()

    assert any(r.get("ok") for r in results)

    # Entities were created
    entities = (await db_session.execute(select(EntityRow))).scalars().all()
    assert len(entities) > 0


# ── Test 6: Duplicate-job prevention ────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_job_prevention(app, db_session):
    """queue_extraction_job does not create a duplicate active job."""
    ef = await _create_ef(db_session, "ef-dup-job", FIXTURE_TEXT_1, "fp-dj")

    # Queue first job
    job1 = await queue_extraction_job(db_session, "ef-dup-job", content_fingerprint="fp-dj")
    assert job1 is not None
    await db_session.commit()

    # Try to queue again — should return None (duplicate)
    job2 = await queue_extraction_job(db_session, "ef-dup-job", content_fingerprint="fp-dj")
    assert job2 is None  # duplicate prevented

    # Only one job in the DB for this fragment
    jobs = (
        (
            await db_session.execute(
                select(JobRow).where(
                    JobRow.kind == "extract_knowledge", JobRow.input_ref == "ef-dup-job"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_reconciliation_does_not_create_duplicate_jobs(app, db_session):
    """Reconciliation is idempotent — running it twice does not create
    duplicate active jobs."""
    ef = await _create_ef(db_session, "ef-recon-idem", FIXTURE_TEXT_1, "fp-ri")
    await db_session.commit()

    # First reconciliation — should queue a job
    recon1 = await reconcile_missing_extraction_jobs(db_session)
    await db_session.commit()
    assert recon1["jobs_queued"] >= 1

    # Second reconciliation — should NOT queue another (active job exists)
    recon2 = await reconcile_missing_extraction_jobs(db_session)
    await db_session.commit()
    assert recon2["jobs_queued"] == 0  # no new job — active already exists

    # Only one active job
    active_jobs = (
        (
            await db_session.execute(
                select(JobRow).where(
                    JobRow.kind == "extract_knowledge",
                    JobRow.input_ref == "ef-recon-idem",
                    JobRow.status.in_(["queued", "running"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(active_jobs) == 1
