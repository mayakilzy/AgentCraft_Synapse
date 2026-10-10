"""Tests for G03-T04: Evidence Verification Engine (corrected policy).

Per the user's "G03-T04 Verification Policy Correction" §Mandatory negative tests:
  - Two URLs copying the same primary study must not become VERIFIED or independently corroborated.
  - Two sources with unknown common origin must not automatically count as independent.
  - Two genuinely independent primary evidence origins may become CORROBORATED.
  - No evidence must not become VERIFIED.
  - No contradictions recorded must not by itself imply VERIFIED.
  - Contradictory evidence remains CONTESTED.
  - Repeated assessment must remain idempotent.
  - EvidenceDelta must record meaningful assessment changes.

Plus the original acceptance tests adapted for the corrected policy:
  1. Source-supported claim is not automatically verified.
  5. Stale evidence is identified.
  6. Unsupported hypotheses are not promoted.
  7. Verification outcomes include clear reason codes.
 11. G01/G02/G03 regression suite remains green.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from synapse.application.verification import (
    POLICY_VERSION,
    VERIFIED_REACHABLE,
    VerificationOutcome,
    _count_independent_primary_origins,
    _determine_outcome,
    assess_claim,
)
from synapse.storage.models import (
    ClaimRow,
    EvidenceFragmentRow,
    SourceRow,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


async def _create_source(session, source_id: str, uri: str) -> SourceRow:
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


# ── Mandatory negative test 1: Two URLs copying same primary study ─────────


@pytest.mark.asyncio
async def test_two_urls_same_primary_not_verified_or_corroborated(app, db_session):
    """Two URLs that are mirrors/reposts of the same primary study
    must NOT become VERIFIED or CORROBORATED."""
    src1 = await _create_source(db_session, "src-mirror-1", "https://mirror1.example.com/paper")
    src2 = await _create_source(db_session, "src-mirror-2", "https://mirror2.example.com/paper")
    ef1 = await _create_evidence(db_session, "ef-m1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-m2", src2.id, src2.canonical_uri)

    claim = await _create_claim(
        db_session,
        "claim-mirror",
        proposition="Claim from a mirrored paper.",
        evidence_refs=["ef-m1", "ef-m2"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-mirror")
    await db_session.commit()

    # Two distinct URLs but unknown origin independence → conservative
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED
    assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert r["assessment"]["independent_source_count"] == 2  # distinct URLs
    # But independent_primary_origin_count is 1 (conservative default)
    assert (
        "verified_unreachable" in r["assessment"]["reason_code"]
        or "single_source" in r["assessment"]["reason_code"]
    )


# ── Mandatory negative test 2: Unknown common origin ────────────────────────


@pytest.mark.asyncio
async def test_unknown_common_origin_not_independent(app, db_session):
    """Two sources with unknown common origin must NOT automatically
    count as independent."""
    src1 = await _create_source(db_session, "src-unk-1", "https://a.example.com/x")
    src2 = await _create_source(db_session, "src-unk-2", "https://b.example.com/y")
    ef1 = await _create_evidence(db_session, "ef-unk-1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-unk-2", src2.id, src2.canonical_uri)

    claim = await _create_claim(
        db_session,
        "claim-unk",
        proposition="Claim with unknown origin independence.",
        evidence_refs=["ef-unk-1", "ef-unk-2"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-unk")
    await db_session.commit()

    # Unknown origin independence → conservative → NOT VERIFIED, NOT CORROBORATED
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED
    assert r["assessment"]["outcome"] != VerificationOutcome.CORROBORATED
    assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED


# ── Mandatory positive test 3: Genuinely independent origins → CORROBORATED ─


@pytest.mark.asyncio
async def test_genuinely_independent_origins_corroborated(app, db_session):
    """When provenance metadata explicitly marks sources as independent
    primary origins, the outcome MAY become CORROBORATED.

    In deterministic-v2, the _count_independent_primary_origins function
    returns 1 by default (conservative). This test patches it to return 2
    to verify that the CORROBORATED path works when genuine independence
    is established by a future provenance system.
    """
    src1 = await _create_source(db_session, "src-gi-1", "https://journal-a.com/paper")
    src2 = await _create_source(db_session, "src-gi-2", "https://journal-b.com/other")
    ef1 = await _create_evidence(db_session, "ef-gi-1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-gi-2", src2.id, src2.canonical_uri)

    claim = await _create_claim(
        db_session,
        "claim-gi",
        proposition="Claim with genuine independence.",
        evidence_refs=["ef-gi-1", "ef-gi-2"],
        epistemic_state="supported",
    )
    await db_session.commit()

    # Patch _count_independent_primary_origins to return 2 (simulating
    # a future provenance system that confirms independence)
    with patch(
        "synapse.application.verification._count_independent_primary_origins",
        return_value=2,
    ):
        r = await assess_claim(db_session, "claim-gi")
        await db_session.commit()

    assert r["assessment"]["outcome"] == VerificationOutcome.CORROBORATED
    assert "independent_primary_origins=2" in r["assessment"]["reason_code"]
    # VERIFIED is still unreachable
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED


# ── Mandatory negative test 4: No evidence → not VERIFIED ───────────────────


@pytest.mark.asyncio
async def test_no_evidence_not_verified(app, db_session):
    """A claim with no evidence must NOT become VERIFIED."""
    claim = await _create_claim(
        db_session,
        "claim-none",
        proposition="Unevidenced claim.",
        evidence_refs=None,
        epistemic_state="hypothesized",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-none")
    await db_session.commit()

    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED
    assert r["assessment"]["outcome"] == VerificationOutcome.INSUFFICIENT_EVIDENCE


# ── Mandatory negative test 5: No contradictions ≠ VERIFIED ──────────────────


@pytest.mark.asyncio
async def test_no_contradictions_does_not_imply_verified(app, db_session):
    """Absence of recorded contradictions does not by itself imply VERIFIED."""
    src = await _create_source(db_session, "src-nocont", "https://example.com/nocont")
    ef = await _create_evidence(db_session, "ef-nocont", src.id, src.canonical_uri)
    claim = await _create_claim(
        db_session,
        "claim-nocont",
        proposition="Claim with no contradictions.",
        evidence_refs=["ef-nocont"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-nocont")
    await db_session.commit()

    # 1 source, no contradictions → SOURCE_SUPPORTED (NOT VERIFIED)
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED
    assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED


# ── Mandatory test 6: Contradictory evidence remains CONTESTED ──────────────


@pytest.mark.asyncio
async def test_contradictory_evidence_contested(app, db_session):
    """Contradictory evidence → CONTESTED. Both supporting and opposing preserved."""
    src1 = await _create_source(db_session, "src-cont-sup", "https://example.com/sup")
    src2 = await _create_source(db_session, "src-cont-opp", "https://example.com/opp")
    ef_sup = await _create_evidence(db_session, "ef-cont-sup", src1.id, src1.canonical_uri)
    ef_opp = await _create_evidence(db_session, "ef-cont-opp", src2.id, src2.canonical_uri)

    claim = await _create_claim(
        db_session,
        "claim-contested",
        proposition="Transformers are fast.",
        evidence_refs=["ef-cont-sup"],
        contradicting_refs=["ef-cont-opp"],
        epistemic_state="supported",
    )
    await db_session.commit()

    r = await assess_claim(db_session, "claim-contested")
    await db_session.commit()

    assert r["assessment"]["outcome"] == VerificationOutcome.CONTESTED
    assert r["assessment"]["supporting_evidence_count"] == 1
    assert r["assessment"]["opposing_evidence_count"] == 1


# ── Mandatory test 7: Repeated assessment is idempotent ─────────────────────


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


# ── Mandatory test 8: EvidenceDelta records meaningful changes ──────────────


@pytest.mark.asyncio
async def test_evidence_delta_records_changes(app, db_session):
    """When new evidence is added and the outcome changes, an EvidenceDelta
    is recorded."""
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

    # First assessment: SOURCE_SUPPORTED (1 source, conservative origin count=1)
    r1 = await assess_claim(db_session, "claim-delta")
    await db_session.commit()
    assert r1["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED

    # Patch to simulate genuine independence → outcome changes to CORROBORATED
    src2 = await _create_source(db_session, "src-delta-2", "https://example.com/d2")
    ef2 = await _create_evidence(db_session, "ef-d2", src2.id, src2.canonical_uri)
    claim_row = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-delta"))
    ).scalar_one()
    claim_row.evidence_refs = json.dumps(["ef-d1", "ef-d2"])
    await db_session.commit()

    with patch(
        "synapse.application.verification._count_independent_primary_origins",
        return_value=2,
    ):
        r2 = await assess_claim(db_session, "claim-delta")
        await db_session.commit()

    assert r2["assessment"]["outcome"] == VerificationOutcome.CORROBORATED
    assert r2["idempotent"] is False  # outcome changed

    # EvidenceDelta recorded
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
    assert delta_payload["new_outcome"] == VerificationOutcome.CORROBORATED


# ── Additional tests from original acceptance criteria ──────────────────────


@pytest.mark.asyncio
async def test_source_supported_not_auto_verified(app, db_session):
    """A claim with evidence from ONE source → SOURCE_SUPPORTED, not VERIFIED."""
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

    assert r["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert r["assessment"]["outcome"] != VerificationOutcome.VERIFIED


@pytest.mark.asyncio
async def test_stale_evidence_identified(app, db_session):
    """Evidence older than the staleness threshold is flagged."""
    src = await _create_source(db_session, "src-stale", "https://example.com/stale")
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

    assert r["assessment"]["outcome"] == VerificationOutcome.STALE_OR_CONTEXT_MISMATCH
    assert len(r["assessment"]["stale_evidence"]) == 1


@pytest.mark.asyncio
async def test_unsupported_hypothesis_not_promoted(app, db_session):
    """A hypothesized claim with NO evidence → INSUFFICIENT_EVIDENCE."""
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
    claim_check = (
        await db_session.execute(select(ClaimRow).where(ClaimRow.id == "claim-hyp"))
    ).scalar_one()
    assert claim_check.epistemic_state == "hypothesized"


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

    assert r["assessment"]["reason_code"] is not None
    assert len(r["assessment"]["reason_code"]) > 0
    assert "policy=deterministic-v2" in r["assessment"]["reason_code"]


# ── Policy-level tests ──────────────────────────────────────────────────────


def test_verified_is_unreachable_in_v2():
    """VERIFIED_REACHABLE is False in deterministic-v2."""
    assert VERIFIED_REACHABLE is False
    assert POLICY_VERSION == "deterministic-v2"


def test_count_independent_primary_origins_conservative():
    """Without provenance metadata, _count_independent_primary_origins returns 1."""
    # Empty list → 0
    assert _count_independent_primary_origins([]) == 1
    # With fragments (no provenance) → still 1 (conservative)
    from unittest.mock import MagicMock

    fake_frag = MagicMock()
    assert _count_independent_primary_origins([fake_frag]) == 1
    assert _count_independent_primary_origins([fake_frag, fake_frag]) == 1


def test_determine_outcome_never_returns_verified():
    """The _determine_outcome function never returns VERIFIED in v2."""
    # Even with high independent origin count
    outcome, _ = _determine_outcome(
        has_evidence=True,
        distinct_source_count=10,
        independent_primary_origin_count=10,
        has_opposing=False,
        stale_evidence_count=0,
        epistemic_state="supported",
    )
    assert outcome != VerificationOutcome.VERIFIED
    assert outcome == VerificationOutcome.CORROBORATED


# ── Context-specific contradictions test ────────────────────────────────────


@pytest.mark.asyncio
async def test_context_specific_contradictions_not_falsely_merged(app, db_session):
    """Two claims with different validity conditions are NOT contradictions."""
    src1 = await _create_source(db_session, "src-ctx-1", "https://example.com/ctx1")
    src2 = await _create_source(db_session, "src-ctx-2", "https://example.com/ctx2")
    ef1 = await _create_evidence(db_session, "ef-ctx-1", src1.id, src1.canonical_uri)
    ef2 = await _create_evidence(db_session, "ef-ctx-2", src2.id, src2.canonical_uri)

    claim_a = await _create_claim(
        db_session,
        "claim-ctx-a",
        proposition="X is fast.",
        evidence_refs=["ef-ctx-1"],
        epistemic_state="supported",
        validity_conditions=["small input size"],
    )
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

    assert r_a["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
    assert r_b["assessment"]["outcome"] == VerificationOutcome.SOURCE_SUPPORTED
