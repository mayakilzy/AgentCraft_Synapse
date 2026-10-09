"""Tests for G03-T04: Evidence Verification Engine.

Per the user's G03-T04 authorization §Acceptance tests:
  1. Source-supported claim is not automatically verified.
  2. Independent corroboration is distinguished from repeated copies.
  3. Contradictory evidence is preserved.
  4. Context-specific apparent contradictions are not falsely merged.
  5. Stale evidence is identified.
  6. Unsupported hypotheses are not promoted.
  7. Verification outcomes include clear reason codes.
  8. EvidenceDelta records meaningful assessment changes.
  9. Repeated assessment is idempotent.
 10. New evidence triggers appropriate reassessment.
 11. G01/G02/G03 regression suite remains green.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from synapse.application.verification import (
    VerificationOutcome,
    assess_claim,
)
from synapse.storage.models import (
    ClaimRow,
    EvidenceFragmentRow,
    SourceRow,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


async def _create_source(session, source_id: str, uri: str) -> SourceRow:
    """Create a source row."""
    source = SourceRow(
        id=source_id,
        canonical_uri=uri,
        source_type="paper",
        status="extracted",
    )
    session.add(source)
    await session.flush()
    return source


async def _create_evidence(
    session,
    fragment_id: str,
    source_id: str,
    source_uri: str,
    *,
    retrieved_at: str | None = "2026-10-08T00:00:00Z",
    text: str = "Evidence text.",
) -> EvidenceFragmentRow:
    """Create an evidence fragment."""
    ef = EvidenceFragmentRow(
        id=fragment_id,
        acquisition_id=f"acq-{fragment_id}",
        source_id=source_id,
        source_uri=source_uri,
        exact_excerpt=text,
        excerpt_hash="abcdef0123456789",
        retrieved_at=retrieved_at or "2026-10-08T00:00:00Z",
        extraction_method="trafilatura-2.3.1",
        content_fingerprint=f"fp-{fragment_id}",
        toolkit_commit_sha="fd9df34c51781bd12effab62762022ab04dbd771",
    )
    session.add(ef)
    await session.flush()
    return ef


async def _create_claim(
    session,
    claim_id: str,
    *,
    proposition: str = "Test claim",
    evidence_refs: list[str] | None = None,
    contradicting_refs: list[str] | None = None,
    epistemic_state: str = "hypothesized",
    validity_conditions: list[str] | None = None,
) -> ClaimRow:
    """Create a claim row."""
    claim = ClaimRow(
        id=claim_id,
        proposition=proposition,
        evidence_refs=json.dumps(evidence_refs) if evidence_refs else None,
        contradicting_refs=json.dumps(contradicting_refs) if contradicting_refs else None,
        epistemic_state=epistemic_state,
        validity_conditions=json.dumps(validity_conditions) if validity_conditions else None,
        extraction_method="test",
        version=1,
    )
    session.add(claim)
    await session.flush()
    return claim


# ── Test 1: Source-supported claim is not automatically verified ───────────


@pytest.mark.asyncio
async def test_source_supported_not_auto_verified(app, db_session):
    """A claim with evidence from ONE source should be SOURCE_SUPPORTED,
    not VERIFIED. VERIFIED requires ≥2 independent sources."""
    src = await _create_source(db_session, "src-1", "https://example.com/paper-1")
    ef = await _create_evidence(db_session, "ef-1", src.id, src.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-1",
        proposition="Transformers use self-attention.",
        evidence_refs=["ef-1"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-1")
    await db_session.commit()

    assert r["ok"] is True
    assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert "single_source" in r["assessment"]["reason_code"]
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED


# ── Test 2: Independent corroboration vs repeated copies ──────────────────


@pytest.mark.asyncio
async def test_independent_corroboration_vs_copies(app, db_session):
    """Two evidence fragments from DIFFERENT sources → VERIFIED.
    Two fragments from the SAME source → still SOURCE_SUPPORTED (1 independent)."""
    # Two DIFFERENT sources
    src1 = await _create_source(db_session, "src-a", "https://example.com/paper-a")
    src2 = await _create_source(db_session, "src-b", "https://example.com/paper-b")
    ef1 = await _create_evidence(db_session, "ef-a1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-b1", src2.id, src2.canonical_uri)

    # Two fragments from the SAME source (src-a)
    ef3 = await _create_evidence(db_session, "ef-a2", src1.id, src1.canonical_uri)

    # Claim with 2 independent sources → VERIFIED
    claim_ind = await _create_claim(
        db_session,
        "claim-ind",
        proposition="Independent corroboration.",
        evidence_refs=["ef-a1", "ef-b1"],
        epistemic_state="supported",
    )
    await db_session.commit()
    r_ind = await assess_claim(db_session, "claim-ind")
    await db_session.commit()
    assert r_ind["assessment"]["outcome"] == VerificationOutcome.VERIFIED
    assert r_ind["assessment"]["independent_source_count"] == 2

    # Claim with 2 fragments from SAME source → SOURCE_SUPPORTED (1 independent)
    claim_copy = await _create_claim(
        db_session,
        "claim-copy",
        proposition="Copied evidence.",
        evidence_refs=["ef-a1", "ef-a2"],  # both from src-a
        epistemic_state="supported",
    )
    await db_session.commit()
    r_copy = await assess_claim(db_session, "claim-copy")
    await db_session.commit()
    assert r_copy["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert r_copy["assessment"]["independent_source_count"] == 1


# ── Test 3: Contradictory evidence is preserved ────────────────────────────


@pytest.mark.asyncio
async def test_contradictory_evidence_preserved(app, db_session):
    """A claim with supporting AND opposing evidence → CONTESTED.
    Both supporting and opposing are preserved."""
    src1 = await _create_source(db_session, "src-sup", "https://example.com/sup")
    src2 = await _create_source(db_session, "src-opp", "https://example.com/opp")
    ef_sup = await _create_evidence(
        db_session, "ef-sup", src1.id, src1.canonical_uri, text="Transformers are fast."
    )
    ef_opp = await _create_evidence(
        db_session, "ef-opp", src2.id, src2.canonical_uri, text="Transformers are slow."
    )

    claim = await _create_claim(
        db_session,
        "claim-contested",
        proposition="Transformers are fast.",
        evidence_refs=["ef-sup"],
        contradicting_refs=["ef-opp"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-contested")
    await db_session.commit()

    assert r["assessment"]["outcome"] == VerificationOutcome.CONTESTED
    assert "contradicting_evidence" in r["assessment"]["reason_code"]
    assert r["assessment"]["supporting_evidence_count"] == 1
    assert r["assessment"]["opposing_evidence_count"] == 1


# ── Test 4: Context-specific contradictions not falsely merged ─────────────


@pytest.mark.asyncio
async def test_context_specific_contradictions(app, db_session):
    """Two claims with different validity conditions are NOT contradictions
    even if their propositions seem opposing."""
    src1 = await _create_source(db_session, "src-ctx-1", "https://example.com/ctx1")
    src2 = await _create_source(db_session, "src-ctx-2", "https://example.com/ctx2")
    ef1 = await _create_evidence(db_session, "ef-ctx-1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-ctx-2", src2.id, src2.canonical_uri)

    # Claim A: "X is fast" under condition "small input"
    claim_a = await _create_claim(
        db_session,
        "claim-ctx-a",
        proposition="X is fast.",
        evidence_refs=["ef-ctx-1"],
        epistemic_state="supported",
        validity_conditions=["small input size"],
    )

    # Claim B: "X is slow" under condition "large input"
    # This is NOT a contradiction of Claim A — different contexts.
    # In the minimal slice, we don't auto-detect this; the test verifies
    # that the assessment does NOT falsely flag it as CONTESTED.
    claim_b = await _create_claim(
        db_session,
        "claim-ctx-b",
        proposition="X is slow.",
        evidence_refs=["ef-ctx-2"],
        epistemic_state="supported",
        validity_conditions=["large input size"],
    )
    await db_session.commit()

    r_a = await assess_claim(db_session, "claim-ctx-a")
    r_b = await assess_claim(db_session, "claim-ctx-b")
    await db_session.commit()

    # Both should be SOURCE_SUPPORTED (no contradictions within their own context)
    assert r_a["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert r_b["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    # Context notes should include the validity conditions
    assert any("small input" in note for note in r_a["assessment"]["context_notes"])
    assert any("large input" in note for note in r_b["assessment"]["context_notes"])


# ── Test 5: Stale evidence is identified ────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_evidence_identified(app, db_session):
    """Evidence older than the staleness threshold is flagged."""
    src = await _create_source(db_session, "src-stale", "https://example.com/stale")
    # Create evidence with an old retrieval date (> 365 days ago)
    old_date = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    ef = await _create_evidence(
        db_session,
        "ef-stale",
        src.id,
        src.canonical_uri,
        retrieved_at=old_date,
    )
    claim = await _create_claim(
        db_session,
        "claim-stale",
        proposition="Old claim.",
        evidence_refs=["ef-stale"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-stale")
    await db_session.commit()

    # Single source + stale → STALE_OR_CONTEXT_MISMATCH
    assert r["assessment"]["outcome"] == VerificationOutcome.STALE_OR_CONTEXT_MISMATCH
    assert len(r["assessment"]["stale_evidence"]) == 1
    assert "ef-stale" in r["assessment"]["stale_evidence"]


# ── Test 6: Unsupported hypotheses are not promoted ──────────────────────────


@pytest.mark.asyncio
async def test_unsupported_hypothesis_not_promoted(app, db_session):
    """A hypothesized claim with NO evidence → INSUFFICIENT_EVIDENCE.
    It stays as hypothesized — never promoted to supported or verified."""
    claim = await _create_claim(
        db_session,
        "claim-hyp",
        proposition="Maybe X causes Y.",
        evidence_refs=None,
        epistemic_state="hypothesized",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-hyp")
    await db_session.commit()

    assert r["assessment"]["outcome"] == VerificationOutcome.INSUFFICIENT_EVIDENCE
    assert "no_evidence_for_hypothesis" in r["assessment"]["reason_code"]

    # The claim's epistemic_state stays as "hypothesized"
    claim_check = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-hyp"))
    ).scalar_one()
    assert claim_check.epistemic_state == "hypothesized"


# ── Test 7: Verification outcomes include clear reason codes ────────────────


@pytest.mark.asyncio
async def test_reason_codes_are_clear(app, db_session):
    """Every outcome has a reason_code that explains WHY."""
    src = await _create_source(db_session, "src-rc", "https://example.com/rc")
    ef = await _create_evidence(db_session, "ef-rc", src.id, src.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-rc",
        proposition="Test reason code.",
        evidence_refs=["ef-rc"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-rc")
    await db_session.commit()

    assert r["ok"] is True
    assert r["assessment"]["reason_code"] is not None
    assert len(r["assessment"]["reason_code"]) > 0
    # Reason code should be informative (not just a generic "ok")
    assert (
        "single_source" in r["assessment"]["reason_code"]
        or "independent_sources" in r["assessment"]["reason_code"]
    )


# ── Test 8: EvidenceDelta records meaningful changes ───────────────────────


@pytest.mark.asyncio
async def test_evidence_delta_records_changes(app, db_session):
    """When the assessment outcome changes, an EvidenceDelta is recorded."""
    src1 = await _create_source(db_session, "src-delta-1", "https://example.com/d1")
    ef1 = await _create_evidence(db_session, "ef-d1", src1.id, src1.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-delta",
        proposition="Delta test.",
        evidence_refs=["ef-d1"],
        epistemic_state="supported",
    )
    await db_session.commit()

    # First assessment: SOURCE_SUPPORTED (1 source)
    r1 = await assess_claim(db_session, "claim-delta")
    await db_session.commit()
    assert r1["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED

    # Add a second independent source
    src2 = await _create_source(db_session, "src-delta-2", "https://example.com/d2")
    ef2 = await _create_evidence(db_session, "ef-d2", src2.id, src2.canonical_uri)
    # Update the claim to include the new evidence
    claim_row = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-delta"))
    ).scalar_one()
    claim_row.evidence_refs = json.dumps(["ef-d1", "ef-d2"])
    await db_session.flush()
    await db_session.commit()

    # Second assessment: should change to VERIFIED (2 independent sources)
    r2 = await assess_claim(db_session, "claim-delta")
    await db_session.commit()
    assert r2["assessment"]["outcome"] == VerificationOutcome.VERIFIED
    assert r2["idempotent"] is False  # outcome changed

    # EvidenceDelta should be recorded in audit_events
    from synapse.storage.models import AuditEventRow

    deltas = (
        (
            await db_session.execute(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "evidence_delta.recorded",
                    AuditEventRow.target_id == "claim-delta",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(deltas) >= 1
    delta_payload = deltas[0].payload
    assert delta_payload["prior_outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert delta_payload["new_outcome"] == VerificationOutcome.VERIFIED


# ── Test 9: Repeated assessment is idempotent ───────────────────────────────


@pytest.mark.asyncio
async def test_repeated_assessment_idempotent(app, db_session):
    """Re-running assessment with same evidence → same outcome, idempotent=True."""
    src = await _create_source(db_session, "src-idem", "https://example.com/idem")
    ef = await _create_evidence(db_session, "ef-idem", src.id, src.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-idem",
        proposition="Idempotent test.",
        evidence_refs=["ef-idem"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r1 = await assess_claim(db_session, "claim-idem")
    await db_session.commit()
    r2 = await assess_claim(db_session, "claim-idem")
    await db_session.commit()

    assert r1["assessment"]["outcome"] == r2["assessment"]["outcome"]
    assert r2["idempotent"] is True


# ── Test 10: New evidence triggers reassessment ────────────────────────────


@pytest.mark.asyncio
async def test_new_evidence_triggers_reassessment(app, db_session):
    """When new evidence is added, re-assessment produces a different outcome."""
    src1 = await _create_source(db_session, "src-reassess-1", "https://example.com/r1")
    ef1 = await _create_evidence(db_session, "ef-r1", src1.id, src1.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-reassess",
        proposition="Reassessment test.",
        evidence_refs=["ef-r1"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r1 = await assess_claim(db_session, "claim-reassess")
    await db_session.commit()
    assert r1["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED

    # Add second independent source
    src2 = await _create_source(db_session, "src-reassess-2", "https://example.com/r2")
    ef2 = await _create_evidence(db_session, "ef-r2", src2.id, src2.canonical_uri)
    claim_row = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-reassess"))
    ).scalar_one()
    claim_row.evidence_refs = json.dumps(["ef-r1", "ef-r2"])
    await db_session.commit()

    r2 = await assess_claim(db_session, "claim-reassess")
    await db_session.commit()
    assert r2["assessment"]["outcome"] == VerificationOutcome.VERIFIED
    assert r2["idempotent"] is False


# ── Test 11: G01/G02/G03 regression ────────────────────────────────────────


def test_g01_g02_g03_regression():
    """All existing tests still pass — no regression from G03-T04."""
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
            "tests/unit/domain/test_claim.py",
            "tests/unit/application/test_extraction.py",
            "tests/integration/test_g03_t03_relationship_service.py::test_g01_g02_g03_regression",
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
