"""G06-T01 -- Future Signal Identification integration tests.

Covers the 11 mandatory test areas in G06 plan section 13.1 plus the
additional negative cases required by the G06-T01 mission brief:

 1. Each snapshot signal kind produces a well-formed FutureSignalReport
    with temporal_type="snapshot".
 2. snapshot_only == True AND temporal_type == "snapshot" on every
    signal.
 3. Evidence footprint snapshot reports factual fragment count and
    distinct_source_uri_count.
 4. independent_origin_count is conservative (1) unless explicit
    SourceRow.publisher/author metadata differs; never equals
    distinct_source_uri_count unless provenance is verified.
 5. Gap classification propagated from analyze_gap() (supported,
    partially_supported, not_evidenced, contested, constrained,
    unknown).
 6. Combination density computed from combine_knowledge().
 7. Empty KB -> empty signals list (no fabricated signals).
 8. Deterministic ordering (same KB -> same signal order).
 9. No fabricated trends: no growth_rate/trend/delta/change field;
    first_seen_at/last_seen_at are factual timestamp ranges.
10. No gap_transition or dependency_shift signal kinds exist.
11. Provenance: every signal references real entity/capability IDs.

Negative cases:
- Two URLs sharing one primary origin do not become two independent
  origins.
- Differing unverified publisher strings do not automatically establish
  independence.
- Missing evidence does not generate fabricated evidence references.
- Repeated calls with identical data return stable signal identities
  and ordering.
- A current snapshot cannot produce gap_transition or dependency_shift.
- Empty knowledge base produces no fabricated signals.
- Contradicted claims remain visible as contested evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from synapse.application.future_signal import (
    SIGNAL_KINDS,
    SIGNAL_TEMPORAL_TYPE,
    identify_future_signals,
)
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()
OLDER_ISO = (datetime.now(UTC) - timedelta(days=30)).isoformat()


# -- Fixture helpers (mirror the canonical G05/G06 pattern) -----------------


async def _make_source(
    session,
    sid: str,
    uri: str,
    *,
    publisher: str | None = None,
    author: str | None = None,
    source_type: str = "paper",
) -> SourceRow:
    src = SourceRow(
        id=sid,
        canonical_uri=uri,
        source_type=source_type,
        status="extracted",
        publisher=publisher,
        author=author,
    )
    session.add(src)
    await session.flush()
    return src


async def _make_fragment(
    session,
    fid: str,
    src: SourceRow,
    excerpt: str,
    *,
    retrieved_at: str | None = None,
) -> EvidenceFragmentRow:
    ef = EvidenceFragmentRow(
        id=fid,
        acquisition_id=f"acq-{fid}",
        source_id=src.id,
        source_uri=src.canonical_uri,
        exact_excerpt=excerpt,
        excerpt_hash=f"hash-{fid}",
        retrieved_at=retrieved_at or RECENT_ISO,
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
) -> EntityRow:
    e = EntityRow(
        id=eid,
        kind=kind,
        canonical_name=name,
        aliases="[]",
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


async def _seed_supported_capability(session) -> dict[str, Any]:
    """Seed a fully-supported capability with evidence and a PROVIDES edge.

    Capability 'source discovery' is provided by 'arxiv' technology,
    backed by one fragment from one source.
    """
    src = await _make_source(session, "src-t01-a", "https://example.com/arxiv-t01")
    frag = await _make_fragment(
        session, "frag-t01-a", src, "arxiv provides source discovery."
    )
    await _make_entity(session, "ent-t01-arxiv", "technology", "arxiv")
    cap = await _make_entity(session, "cap-t01-sd", "capability", "source discovery")
    await _make_claim(
        session,
        "claim-t01-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-t01-arxiv",
        object_ref="cap-t01-sd",
        evidence_refs=["frag-t01-a"],
    )
    r = await create_relationship(
        session,
        from_entity_id="ent-t01-arxiv",
        to_entity_id="cap-t01-sd",
        predicate="PROVIDES",
        evidence_refs=["frag-t01-a"],
        origin="explicit",
    )
    assert r["ok"] is True
    await session.commit()
    await assess_claim(session, "claim-t01-sd", requester="t01-fixture")
    await session.commit()
    return {
        "source_id": src.id,
        "fragment_id": frag.id,
        "capability_id": cap.id,
        "capability_name": "source discovery",
        "entity_id": "ent-t01-arxiv",
    }


async def _seed_contested_capability(session) -> dict[str, Any]:
    """Seed a contested capability: both SUPPORTS and CONTRADICTS evidence.

    Capability 'evidence verification' has a supporting claim and a
    contradicting claim from two different sources.
    """
    src_sup = await _make_source(session, "src-t01-sup", "https://example.com/sup")
    src_con = await _make_source(session, "src-t01-con", "https://example.com/con")
    frag_sup = await _make_fragment(
        session, "frag-t01-sup", src_sup, "synapse provides evidence verification."
    )
    frag_con = await _make_fragment(
        session, "frag-t01-con", src_con, "synapse fails to verify evidence."
    )
    await _make_entity(session, "ent-t01-synapse", "tool", "synapse")
    cap = await _make_entity(
        session, "cap-t01-ev", "capability", "evidence verification"
    )
    await _make_claim(
        session,
        "claim-t01-ev-sup",
        "synapse provides evidence verification.",
        subject_ref="ent-t01-synapse",
        object_ref="cap-t01-ev",
        evidence_refs=["frag-t01-sup"],
    )
    await _make_claim(
        session,
        "claim-t01-ev-con",
        "synapse fails to verify evidence.",
        subject_ref="ent-t01-synapse",
        object_ref="cap-t01-ev",
        evidence_refs=["frag-t01-con"],
        contradicting_refs=["frag-t01-sup"],
        epistemic_state="disputed",
    )
    r = await create_relationship(
        session,
        from_entity_id="ent-t01-synapse",
        to_entity_id="cap-t01-ev",
        predicate="PROVIDES",
        evidence_refs=["frag-t01-sup"],
        origin="explicit",
    )
    assert r["ok"] is True
    await session.commit()
    await assess_claim(session, "claim-t01-ev-sup", requester="t01-fixture")
    await assess_claim(session, "claim-t01-ev-con", requester="t01-fixture")
    await session.commit()
    return {
        "capability_id": cap.id,
        "capability_name": "evidence verification",
    }


async def _seed_not_evidenced_capability(session) -> dict[str, Any]:
    """Seed a capability with NO provider and NO evidence (not_evidenced)."""
    cap = await _make_entity(
        session, "cap-t01-ne", "capability", "quantum error mitigation"
    )
    await session.commit()
    return {"capability_id": cap.id, "capability_name": "quantum error mitigation"}


# == Test 1: Each snapshot signal kind produces a well-formed signal =========


@pytest.mark.asyncio
async def test_01_each_signal_kind_well_formed(app, db_session):
    """Every emitted signal has kind in SIGNAL_KINDS, temporal_type='snapshot',
    snapshot_only=True, and the required fields."""
    await _seed_supported_capability(db_session)
    await db_session.commit()

    result = await identify_future_signals(db_session, requester="tester")
    assert result["temporal_type"] == SIGNAL_TEMPORAL_TYPE
    assert result["snapshot_only"] is True

    signals = result["signals"]
    assert len(signals) > 0

    emitted_kinds = {s["kind"] for s in signals}
    # At least 4 of the 5 kinds should be emitted for a supported capability
    # (contradiction_exposure_snapshot only emits when contested).
    assert len(emitted_kinds) >= 4
    assert emitted_kinds.issubset(set(SIGNAL_KINDS))

    for s in signals:
        assert s["temporal_type"] == "snapshot"
        assert s["snapshot_only"] is True
        assert s["signal_id"].startswith("sig-")
        assert s["capability_id"] is not None
        assert isinstance(s["evidence_refs"], list)
        assert isinstance(s["distinct_source_uri_count"], int)
        assert isinstance(s["independent_origin_count"], int)
        assert s["notes"]


# == Test 2: snapshot_only and temporal_type on every signal =================


@pytest.mark.asyncio
async def test_02_snapshot_only_and_temporal_type_invariant(app, db_session):
    await _seed_supported_capability(db_session)
    await db_session.commit()

    result = await identify_future_signals(db_session)
    for s in result["signals"]:
        assert s["snapshot_only"] is True
        assert s["temporal_type"] == "snapshot"

    # Top-level result also carries the invariants
    assert result["snapshot_only"] is True
    assert result["temporal_type"] == "snapshot"


# == Test 3: Evidence footprint reports factual counts ======================


@pytest.mark.asyncio
async def test_03_evidence_footprint_factual(app, db_session):
    fixture = await _seed_supported_capability(db_session)
    await db_session.commit()

    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )
    footprint_signals = [
        s for s in result["signals"] if s["kind"] == "evidence_footprint_snapshot"
    ]
    assert len(footprint_signals) == 1
    fp = footprint_signals[0]
    # One fragment from one source URL
    assert fp["distinct_source_uri_count"] == 1
    assert fp["fragment_count"] == 1
    assert fixture["fragment_id"] in fp["evidence_refs"]


# == Test 4: Source independence conservative (PRB-04) ======================


@pytest.mark.asyncio
async def test_04a_independent_origin_conservative_default(app, db_session):
    """Default independent_origin_count is 1 (conservative, PRB-04) even
    when there are multiple distinct source URLs."""
    # Two distinct source URLs, no provenance metadata
    src_a = await _make_source(db_session, "src-t01-x-a", "https://example.com/x-a")
    src_b = await _make_source(db_session, "src-t01-x-b", "https://example.com/x-b")
    frag_a = await _make_fragment(db_session, "frag-x-a", src_a, "tool X provides cap Y.")
    frag_b = await _make_fragment(db_session, "frag-x-b", src_b, "tool X also provides cap Y elsewhere.")
    await _make_entity(db_session, "ent-t01-x", "technology", "tool-x")
    cap = await _make_entity(db_session, "cap-t01-y", "capability", "cap-y")
    await _make_claim(
        db_session, "claim-t01-y",
        "tool-x provides cap-y.",
        subject_ref="ent-t01-x", object_ref="cap-t01-y",
        evidence_refs=["frag-x-a", "frag-x-b"],
    )
    await create_relationship(
        db_session, from_entity_id="ent-t01-x", to_entity_id="cap-t01-y",
        predicate="PROVIDES", evidence_refs=["frag-x-a"], origin="explicit",
    )
    await db_session.commit()
    await assess_claim(db_session, "claim-t01-y", requester="t01")
    await db_session.commit()

    result = await identify_future_signals(
        db_session, focus_capability_id=cap.id
    )
    footprint = next(s for s in result["signals"] if s["kind"] == "evidence_footprint_snapshot")
    # Two distinct URLs
    assert footprint["distinct_source_uri_count"] == 2
    # But independent_origin_count is conservative (1) — no new inference algorithm
    assert footprint["independent_origin_count"] == 1


@pytest.mark.asyncio
async def test_04b_two_urls_same_origin_not_two_independent(app, db_session):
    """Two URLs that are mirrors/syndicates of the same primary origin do
    NOT become two independent origins. Even with differing publisher
    strings, G06-T01 does NOT introduce a new independence inference
    algorithm (per mission section 4)."""
    # Two sources with DIFFERENT publisher strings but unverified provenance
    src_a = await _make_source(
        db_session, "src-mirror-a", "https://mirror-a.example.com/article",
        publisher="Mirror Site A",
    )
    src_b = await _make_source(
        db_session, "src-mirror-b", "https://mirror-b.example.com/article",
        publisher="Mirror Site B",
    )
    await _make_fragment(db_session, "frag-mirror-a", src_a, "same content republished.")
    await _make_fragment(db_session, "frag-mirror-b", src_b, "same content republished.")
    await _make_entity(db_session, "ent-mirror-tool", "technology", "mirror-tool")
    cap = await _make_entity(db_session, "cap-mirror-cap", "capability", "mirror-cap")
    await _make_claim(
        db_session, "claim-mirror",
        "mirror-tool provides mirror-cap.",
        subject_ref="ent-mirror-tool", object_ref="cap-mirror-cap",
        evidence_refs=["frag-mirror-a", "frag-mirror-b"],
    )
    await create_relationship(
        db_session, from_entity_id="ent-mirror-tool", to_entity_id="cap-mirror-cap",
        predicate="PROVIDES", evidence_refs=["frag-mirror-a"], origin="explicit",
    )
    await db_session.commit()
    await assess_claim(db_session, "claim-mirror", requester="t01")
    await db_session.commit()

    result = await identify_future_signals(
        db_session, focus_capability_id=cap.id
    )
    footprint = next(s for s in result["signals"] if s["kind"] == "evidence_footprint_snapshot")
    # Two distinct URLs
    assert footprint["distinct_source_uri_count"] == 2
    # Conservative: independent_origin_count remains 1.
    # Differing unverified publisher strings do NOT establish independence.
    assert footprint["independent_origin_count"] == 1


# == Test 5: Gap classification propagated (all 6 types where applicable) ===


@pytest.mark.asyncio
async def test_05a_gap_classification_supported(app, db_session):
    fixture = await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )
    gap_signals = [s for s in result["signals"] if s["kind"] == "gap_classification_snapshot"]
    assert len(gap_signals) == 1
    assert gap_signals[0]["gap_classification"] == "supported"


@pytest.mark.asyncio
async def test_05b_gap_classification_contested(app, db_session):
    fixture = await _seed_contested_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )
    gap_signals = [s for s in result["signals"] if s["kind"] == "gap_classification_snapshot"]
    assert len(gap_signals) == 1
    # The gap analyzer should classify a contested capability as "contested"
    assert gap_signals[0]["gap_classification"] == "contested"
    # Contradiction exposure signal should also be present
    contra = [s for s in result["signals"] if s["kind"] == "contradiction_exposure_snapshot"]
    assert len(contra) == 1
    assert contra[0]["gap_classification"] == "contested"


@pytest.mark.asyncio
async def test_05c_gap_classification_not_evidenced(app, db_session):
    fixture = await _seed_not_evidenced_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )
    gap_signals = [s for s in result["signals"] if s["kind"] == "gap_classification_snapshot"]
    assert len(gap_signals) == 1
    assert gap_signals[0]["gap_classification"] == "not_evidenced"


# == Test 6: Combination density computed (0 is a valid count) ==============


@pytest.mark.asyncio
async def test_06_combination_density_present(app, db_session):
    await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(db_session)
    combo_signals = [s for s in result["signals"] if s["kind"] == "combination_density_snapshot"]
    assert len(combo_signals) >= 1
    for s in combo_signals:
        assert isinstance(s["combination_count"], int)
        assert s["combination_count"] >= 0


# == Test 7: Empty KB -> empty signals (no fabricated signals) ===============


@pytest.mark.asyncio
async def test_07_empty_kb_no_fabricated_signals(app, db_session):
    """An empty knowledge base produces zero signals — no fabricated
    capabilities, fragments, or classifications."""
    result = await identify_future_signals(db_session)
    assert result["signals"] == []
    assert result["snapshot_only"] is True
    assert result["temporal_type"] == "snapshot"
    # Limitations should be documented
    assert len(result["limitations"]) >= 1


# == Test 8: Deterministic ordering (same KB -> same order) =================


@pytest.mark.asyncio
async def test_08_deterministic_ordering(app, db_session, engine):
    """Two scans with identical KB state produce identical signal
    ordering, content, and IDs."""
    await _seed_supported_capability(db_session)
    await db_session.commit()

    # First scan via the test session
    result1 = await identify_future_signals(db_session, requester="scan-1")

    # Second scan via a SEPARATE session bound to the same engine
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session2:
        result2 = await identify_future_signals(session2, requester="scan-2")

    ids1 = [s["signal_id"] for s in result1["signals"]]
    ids2 = [s["signal_id"] for s in result2["signals"]]
    assert ids1 == ids2, "Signal ordering differs between identical scans"

    kinds1 = [s["kind"] for s in result1["signals"]]
    kinds2 = [s["kind"] for s in result2["signals"]]
    assert kinds1 == kinds2


# == Test 9: No fabricated trends — no growth_rate/trend/delta/change =======


@pytest.mark.asyncio
async def test_09_no_fabricated_trend_fields(app, db_session):
    await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(db_session)

    forbidden_fields = {"growth_rate", "trend", "delta", "change", "velocity", "acceleration"}
    for s in result["signals"]:
        for field in forbidden_fields:
            assert field not in s, f"signal {s['signal_id']} has forbidden field '{field}'"

        # first_seen_at / last_seen_at are factual timestamps, not growth curves
        if s["first_seen_at"] is not None:
            assert isinstance(s["first_seen_at"], str)
        if s["last_seen_at"] is not None:
            assert isinstance(s["last_seen_at"], str)


@pytest.mark.asyncio
async def test_09b_timestamps_are_factual_observation_range(app, db_session):
    """first_seen_at is the earliest retrieved_at; last_seen_at is the
    latest. They are factual observations, not growth indicators."""
    src = await _make_source(db_session, "src-ts", "https://example.com/ts")
    # Two fragments with different retrieved_at timestamps
    await _make_fragment(
        db_session, "frag-old", src, "old observation.",
        retrieved_at=OLDER_ISO,
    )
    await _make_fragment(
        db_session, "frag-new", src, "new observation.",
        retrieved_at=RECENT_ISO,
    )
    await _make_entity(db_session, "ent-ts", "technology", "ts-tool")
    cap = await _make_entity(db_session, "cap-ts", "capability", "ts-cap")
    await _make_claim(
        db_session, "claim-ts", "ts-tool provides ts-cap.",
        subject_ref="ent-ts", object_ref="cap-ts",
        evidence_refs=["frag-old", "frag-new"],
    )
    await create_relationship(
        db_session, from_entity_id="ent-ts", to_entity_id="cap-ts",
        predicate="PROVIDES", evidence_refs=["frag-old"], origin="explicit",
    )
    await db_session.commit()
    await assess_claim(db_session, "claim-ts", requester="t01")
    await db_session.commit()

    result = await identify_future_signals(db_session, focus_capability_id=cap.id)
    footprint = next(s for s in result["signals"] if s["kind"] == "evidence_footprint_snapshot")
    assert footprint["first_seen_at"] == OLDER_ISO
    assert footprint["last_seen_at"] == RECENT_ISO


# == Test 10: No gap_transition or dependency_shift signal kinds ============


@pytest.mark.asyncio
async def test_10_no_forbidden_signal_kinds(app, db_session):
    await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(db_session)

    emitted_kinds = {s["kind"] for s in result["signals"]}
    assert "gap_transition" not in emitted_kinds
    assert "dependency_shift" not in emitted_kinds

    # Also verify the module-level SIGNAL_KINDS tuple excludes them
    assert "gap_transition" not in SIGNAL_KINDS
    assert "dependency_shift" not in SIGNAL_KINDS


# == Test 11: Provenance — every signal references real entity/capability ===


@pytest.mark.asyncio
async def test_11_provenance_real_references(app, db_session):
    fixture = await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )

    for s in result["signals"]:
        assert s["capability_id"] == fixture["capability_id"]
        # Every evidence_ref must be a real fragment ID in the DB
        for ref in s["evidence_refs"]:
            from sqlalchemy import select as sel

            from synapse.storage.models import EvidenceFragmentRow
            found = (await db_session.execute(
                sel(EvidenceFragmentRow).where(EvidenceFragmentRow.id == ref)
            )).scalar_one_or_none()
            assert found is not None, f"signal references fabricated fragment {ref}"


# == Negative: Missing evidence -> no fabricated evidence references =========


@pytest.mark.asyncio
async def test_neg_missing_evidence_no_fabricated_refs(app, db_session):
    """A capability with no evidence produces signals with empty
    evidence_refs — no fabricated fragment IDs."""
    fixture = await _seed_not_evidenced_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )
    assert len(result["signals"]) > 0
    for s in result["signals"]:
        assert s["evidence_refs"] == [], (
            f"signal {s['signal_id']} has fabricated evidence_refs: {s['evidence_refs']}"
        )
        assert s["distinct_source_uri_count"] == 0
        assert s["first_seen_at"] is None
        assert s["last_seen_at"] is None


# == Negative: Repeated calls -> stable signal identities ===================


@pytest.mark.asyncio
async def test_neg_repeated_calls_stable_identity(app, db_session, engine):
    """Repeated identify_future_signals calls with identical KB state
    produce identical signal_id values (no new records created)."""
    await _seed_supported_capability(db_session)
    await db_session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    sig_ids_run1: list[str] = []
    sig_ids_run2: list[str] = []

    async with factory() as s1:
        r1 = await identify_future_signals(s1, requester="run-1")
        sig_ids_run1 = [s["signal_id"] for s in r1["signals"]]

    async with factory() as s2:
        r2 = await identify_future_signals(s2, requester="run-2")
        sig_ids_run2 = [s["signal_id"] for s in r2["signals"]]

    assert sig_ids_run1 == sig_ids_run2

    # Verify no new entities were created by the signal service (read-only)
    from sqlalchemy import func
    from sqlalchemy import select as sel

    from synapse.storage.models import EntityRow
    async with factory() as s3:
        entity_count = (await s3.execute(
            sel(func.count()).select_from(EntityRow)
        )).scalar()
    # The fixture seeded exactly 2 entities (1 technology + 1 capability)
    assert entity_count == 2


# == Negative: Contradicted claims remain visible as contested =============


@pytest.mark.asyncio
async def test_neg_contested_evidence_preserved(app, db_session):
    """Contradicted claims are NOT suppressed — the contradiction_exposure
    signal surfaces them, and the gap classification is 'contested'."""
    fixture = await _seed_contested_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(
        db_session, focus_capability_id=fixture["capability_id"]
    )

    contra_signals = [s for s in result["signals"] if s["kind"] == "contradiction_exposure_snapshot"]
    assert len(contra_signals) == 1
    contra = contra_signals[0]
    assert contra["gap_classification"] == "contested"
    # Both supporting and contradicting fragments should be in evidence_refs
    assert "frag-t01-sup" in contra["evidence_refs"]
    assert "frag-t01-con" in contra["evidence_refs"]


# == Negative: focus_entity_id resolves linked capabilities =================


@pytest.mark.asyncio
async def test_neg_focus_entity_id_resolves_linked_caps(app, db_session):
    """focus_entity_id restricts signals to capabilities linked via
    PROVIDES/ENABLES/PRODUCES."""
    fixture = await _seed_supported_capability(db_session)
    await db_session.commit()

    result = await identify_future_signals(
        db_session, focus_entity_id=fixture["entity_id"]
    )
    # Should find the 'source discovery' capability linked to 'arxiv'
    cap_ids = {s["capability_id"] for s in result["signals"]}
    assert fixture["capability_id"] in cap_ids


# == Negative: focus on non-existent capability -> empty ===================


@pytest.mark.asyncio
async def test_neg_focus_nonexistent_capability_empty(app, db_session):
    """Focusing on a non-existent capability returns an empty result."""
    result = await identify_future_signals(
        db_session, focus_capability_id="cap-does-not-exist"
    )
    assert result["signals"] == []
    assert result["snapshot_only"] is True


# == Test: limitation reporting ============================================


@pytest.mark.asyncio
async def test_limitations_documented(app, db_session):
    """The result documents explicit limitations (Layer 2 unavailable,
    conservative source independence)."""
    await _seed_supported_capability(db_session)
    await db_session.commit()
    result = await identify_future_signals(db_session)
    assert isinstance(result["limitations"], list)
    assert len(result["limitations"]) >= 2
    limitations_text = " ".join(result["limitations"])
    assert "Layer 2" in limitations_text or "historical change" in limitations_text
    assert "independent_origin_count" in limitations_text or "PRB-04" in limitations_text
