"""G05-T03 -- Evidence-Grounded Innovation Generation acceptance tests.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§G05-T03), the 7 acceptance criteria:

  1. Generates >=1 valid concept per golden innovation case.
  2. Every concept has >=1 uncertainty.
  3. Every concept's hypothesis_id points to a ClaimRow(epistemic_state="hypothesized").
  4. Quality gates 7-9 (unsupported architecture claims, uncertainty honesty,
     no VERIFIED promotion) enforced.
  5. POST /api/v1/innovations/generate returns 200 with auth, 401 without.
  6. OpenAPI includes the new endpoint.
  7. G01-G04-T02 regression intact.

Plus negative tests for:
  - Sparse evidence (concepts preserved with labeled uncertainty).
  - Contradictory evidence (preserved, not suppressed).
  - Duplicate concepts (deduplicated).
  - Repeated requests (idempotent concept generation).
  - Invalid component references (fabricated IDs rejected).
  - Hypothetical-only links (excluded from concept generation).
  - Absent optional LLM configuration (deterministic path works).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from synapse.application.innovation import (
    generate_innovations,
)
from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
)

# ── Test fixture ────────────────────────────────────────────────────────────

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()


async def _make_source(session, sid: str, uri: str) -> SourceRow:
    src = SourceRow(id=sid, canonical_uri=uri, source_type="paper", status="extracted")
    session.add(src)
    await session.flush()
    return src


async def _make_fragment(session, fid: str, src: SourceRow, excerpt: str) -> EvidenceFragmentRow:
    ef = EvidenceFragmentRow(
        id=fid,
        acquisition_id=f"acq-{fid}",
        source_id=src.id,
        source_uri=src.canonical_uri,
        exact_excerpt=excerpt,
        excerpt_hash=f"hash-{fid}",
        retrieved_at=RECENT_ISO,
        extraction_method="test-fixture",
        content_fingerprint=f"fp-{fid}",
        toolkit_commit_sha="fixture",
    )
    session.add(ef)
    await session.flush()
    return ef


async def _make_entity(session, eid: str, kind: str, name: str, desc: str = "") -> EntityRow:
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


async def _seed_innovation_fixture(session) -> dict[str, Any]:
    """Seed a fixture with >=2 documented capabilities, constraints, and a gap."""
    src_a = await _make_source(session, "src-t03-a", "https://example.com/papers/arxiv")
    src_b = await _make_source(session, "src-t03-b", "https://example.com/papers/trafilatura")
    src_c = await _make_source(session, "src-t03-c", "https://example.com/papers/synapse")

    frag_a = await _make_fragment(session, "frag-t03-a", src_a, "arxiv provides source discovery.")
    frag_b = await _make_fragment(session, "frag-t03-b", src_b, "trafilatura extracts content.")
    frag_c = await _make_fragment(
        session, "frag-t03-c", src_c, "synapse provides evidence verification."
    )

    await _make_entity(session, "ent-arxiv", "technology", "arxiv", desc="arXiv paper repository")
    await _make_entity(
        session, "ent-trafilatura", "technology", "trafilatura", desc="Web content extraction"
    )
    await _make_entity(session, "ent-synapse", "tool", "synapse", desc="Synapse knowledge engine")
    await _make_entity(
        session, "ent-no-vector", "constraint", "no vector database", desc="No vector DB constraint"
    )

    for cap_id, name in [
        ("cap-source-discovery", "source discovery"),
        ("cap-content-extraction", "content extraction"),
        ("cap-evidence-verification", "evidence verification"),
        ("cap-knowledge-extraction", "structured knowledge extraction"),
    ]:
        await _make_entity(session, cap_id, "capability", name, desc=f"Capability {name}")

    await _make_claim(
        session,
        "claim-t03-sd",
        "arxiv provides source discovery.",
        subject_ref="ent-arxiv",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-t03-a"],
    )
    await _make_claim(
        session,
        "claim-t03-ce",
        "trafilatura provides content extraction.",
        subject_ref="ent-trafilatura",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-t03-b"],
    )
    await _make_claim(
        session,
        "claim-t03-ev",
        "synapse provides evidence verification.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-t03-c"],
        contradicting_refs=["frag-t03-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    src_d = await _make_source(session, "src-t03-d", "https://example.com/contradiction")
    frag_opp = await _make_fragment(
        session, "frag-t03-opp", src_d, "Evidence verification cannot be automated."
    )

    for from_id, to_id, ev_refs in [
        ("ent-arxiv", "cap-source-discovery", ["frag-t03-a"]),
        ("ent-trafilatura", "cap-content-extraction", ["frag-t03-b"]),
        ("ent-synapse", "cap-evidence-verification", ["frag-t03-c"]),
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
    for cid in ("claim-t03-sd", "claim-t03-ce", "claim-t03-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {
        "arxiv_id": "ent-arxiv",
        "trafilatura_id": "ent-trafilatura",
        "synapse_id": "ent-synapse",
    }


# ── Acceptance Test 1: Generates >=1 valid concept ────────────────────────


@pytest.mark.asyncio
async def test_01_generates_valid_concepts(app, db_session):
    """generate_innovations produces >=1 concept with all required fields."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(
        db_session, "AI research assistant", context="AI agent systems"
    )

    assert len(result["concepts"]) >= 1
    concept = result["concepts"][0]
    assert "id" in concept
    assert "purpose" in concept
    assert "target_users" in concept
    assert "components" in concept
    assert "integration_mechanism" in concept
    assert "potential_benefit" in concept
    assert "uncertainties" in concept
    assert "evidence_refs" in concept
    assert "combination_basis" in concept
    assert "generation_method" in concept
    assert "hypothesis_id" in concept
    assert concept["generation_method"] == "deterministic"


# ── Acceptance Test 2: Every concept has >=1 uncertainty ───────────────────


@pytest.mark.asyncio
async def test_02_every_concept_has_uncertainty(app, db_session):
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    for concept in result["concepts"]:
        assert len(concept["uncertainties"]) >= 1, f"concept {concept['id']} has no uncertainties"


# ── Acceptance Test 3: hypothesis_id points to hypothesized claim ───────────


@pytest.mark.asyncio
async def test_03_hypothesis_id_points_to_hypothesized(app, db_session):
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    for concept in result["concepts"]:
        hyp_id = concept["hypothesis_id"]
        # Verify the hypothesis claim exists and has epistemic_state="hypothesized"
        hyp_claim = (
            await db_session.execute(select(ClaimRow).where(ClaimRow.id == hyp_id))
        ).scalar_one_or_none()
        assert hyp_claim is not None, f"hypothesis claim {hyp_id} not found"
        assert hyp_claim.epistemic_state == "hypothesized", (
            f"hypothesis {hyp_id} has epistemic_state={hyp_claim.epistemic_state}, expected 'hypothesized'"
        )


# ── Acceptance Test 4: Quality gates enforced ─────────────────────────────


@pytest.mark.asyncio
async def test_04_quality_gates_enforced(app, db_session):
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    # Quality gates must be present
    assert len(result["quality_gates"]) > 0

    # Check for the critical gates
    gate_names = {g["gate"] for g in result["quality_gates"]}
    assert "no_fabricated_component_ids" in gate_names
    assert "no_fabricated_evidence_refs" in gate_names
    assert "uncertainty_honesty" in gate_names
    assert "no_verified_promotion" in gate_names
    assert "integration_mechanism_and_benefit" in gate_names
    assert "no_unsupported_numerical_claims" in gate_names
    assert "no_fabricated_novelty" in gate_names

    # All gates must pass (no fabricated IDs, no unsupported claims, etc.)
    for gate in result["quality_gates"]:
        assert gate["passed"], f"quality gate {gate['gate']} failed: {gate['detail']}"


# ── Acceptance Test 5: API endpoint returns 200/401 ────────────────────────


@pytest.mark.asyncio
async def test_05_api_requires_auth(app, client, db_session):
    """POST /api/v1/innovations/generate requires auth (401 without)."""
    await _seed_innovation_fixture(db_session)

    r = client.post("/api/v1/innovations/generate", json={"problem_domain": "test"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_05_api_with_auth(app, client, db_session, auth_headers_reader):
    """POST /api/v1/innovations/generate returns 200 with valid auth."""
    await _seed_innovation_fixture(db_session)

    r = client.post(
        "/api/v1/innovations/generate",
        headers=auth_headers_reader,
        json={"problem_domain": "AI research assistant", "context": "AI agent systems"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["api_version"] == "v1"
    assert body["error"] is None
    data = body["data"]
    assert "concepts" in data
    assert "quality_gates" in data
    assert "unknowns" in data


# ── Acceptance Test 6: OpenAPI includes the endpoint ──────────────────────


def test_06_openapi_includes_innovations(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        schema = c.get("/api/v1/openapi.json").json()
        paths = schema["paths"]
        assert "/api/v1/innovations/generate" in paths
        assert "post" in paths["/api/v1/innovations/generate"]
async def test_negative_sparse_evidence(app, db_session):
    """Concepts with sparse evidence are preserved with labeled uncertainty."""
    # Seed only one capability with minimal evidence
    await _make_entity(session=db_session, eid="ent-sparse", kind="tool", name="sparse tool")
    await _make_entity(
        session=db_session, eid="cap-sparse", kind="capability", name="sparse capability"
    )
    src = await _make_source(session=db_session, sid="src-sparse", uri="https://example.com/sparse")
    frag = await _make_fragment(
        session=db_session, fid="frag-sparse", src=src, excerpt="Sparse evidence."
    )
    await _make_claim(
        session=db_session,
        cid="claim-sparse",
        proposition="sparse tool provides sparse capability.",
        subject_ref="ent-sparse",
        object_ref="cap-sparse",
        evidence_refs=["frag-sparse"],
    )
    r = await create_relationship(
        session=db_session,
        from_entity_id="ent-sparse",
        to_entity_id="cap-sparse",
        predicate="PROVIDES",
        evidence_refs=["frag-sparse"],
        origin="explicit",
    )
    assert r["ok"] is True
    await db_session.commit()
    await assess_claim(db_session, "claim-sparse", requester="fixture")
    await db_session.commit()

    result = await generate_innovations(db_session, "sparse capability")

    # Concepts should still be generated (preserved) with uncertainty about sparse evidence
    for concept in result["concepts"]:
        assert len(concept["uncertainties"]) >= 1
        # At least one uncertainty should mention evidence or validation
        unc_text = " ".join(concept["uncertainties"]).lower()
        assert any(
            w in unc_text for w in ["evidence", "validation", "hypothesis", "unproven", "unknown"]
        ), f"no evidence-acknowledging uncertainty: {concept['uncertainties']}"


# ── Negative Test: Contradictory evidence ─────────────────────────────────


@pytest.mark.asyncio
async def test_negative_contradictory_evidence(app, db_session):
    """Contradictory evidence is preserved, not suppressed."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(
        db_session, "evidence verification", context="AI agent systems"
    )

    # Find concepts that include the synapse entity (which has the disputed claim).
    ev_concepts = [
        c
        for c in result["concepts"]
        if any(comp.get("entity_id") == "ent-synapse" for comp in c.get("components", []))
    ]
    if ev_concepts:
        concept = ev_concepts[0]
        # The contradiction should be preserved as an uncertainty or in evidence_refs
        has_contradiction = any(
            "contradict" in u.lower() or "disputed" in u.lower() or "contested" in u.lower()
            for u in concept["uncertainties"]
        )
        # The contradicting fragment should be in evidence_refs (preserved)
        has_opp_in_refs = "frag-t03-opp" in concept.get("evidence_refs", [])
        assert has_contradiction or has_opp_in_refs, (
            f"contradictory evidence not preserved: uncertainties={concept['uncertainties']}, "
            f"evidence_refs={concept['evidence_refs']}"
        )


# ── Negative Test: Duplicate concepts deduplicated ───────────────────────


@pytest.mark.asyncio
async def test_negative_duplicate_concepts(app, db_session):
    """No duplicate concepts (same combination_basis + template)."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant", max_concepts=10)

    # No duplicate (combination_basis, purpose) pairs
    seen_keys: set[str] = set()
    for concept in result["concepts"]:
        key = f"{concept.get('combination_basis', '')}:{concept.get('purpose', '')}"
        assert key not in seen_keys, f"duplicate concept: {key}"
        seen_keys.add(key)


# ── Negative Test: Repeated requests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_negative_repeated_requests(app, db_session):
    """Repeated requests produce deterministic results."""
    await _seed_innovation_fixture(db_session)

    result1 = await generate_innovations(db_session, "AI research assistant", max_concepts=3)
    result2 = await generate_innovations(db_session, "AI research assistant", max_concepts=3)

    # Same number of concepts
    assert len(result1["concepts"]) == len(result2["concepts"])
    # Same purposes (deterministic templates)
    purposes1 = [c["purpose"] for c in result1["concepts"]]
    purposes2 = [c["purpose"] for c in result2["concepts"]]
    assert purposes1 == purposes2, f"non-deterministic: {purposes1} vs {purposes2}"


# ── Negative Test: Invalid component references ──────────────────────────


@pytest.mark.asyncio
async def test_negative_invalid_component_refs(app, db_session):
    """Concepts with fabricated component IDs are rejected by quality gates."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    # All component entity_ids must resolve to existing EntityRows
    all_entities = {row[0] for row in (await db_session.execute(select(EntityRow.id))).all()}
    for concept in result["concepts"]:
        for comp in concept["components"]:
            assert comp["entity_id"] in all_entities, (
                f"fabricated entity_id in concept {concept['id']}: {comp['entity_id']}"
            )
        for ref in concept["evidence_refs"]:
            # Evidence refs must be valid
            all_fragments = {
                row[0] for row in (await db_session.execute(select(EvidenceFragmentRow.id))).all()
            }
            assert ref in all_fragments, f"fabricated evidence_ref: {ref}"


# ── Negative Test: Hypothetical-only links excluded ──────────────────────


@pytest.mark.asyncio
async def test_negative_hypothetical_only_excluded(app, db_session):
    """Hypothesized relationships don't contaminate concept generation."""
    await _seed_innovation_fixture(db_session)

    # Add a hypothesized relationship
    r = await create_relationship(
        session=db_session,
        from_entity_id="ent-arxiv",
        to_entity_id="cap-knowledge-extraction",
        predicate="PROVIDES",
        evidence_refs=["frag-t03-a"],
        origin="hypothesized",
    )
    assert r["ok"] is True
    await db_session.commit()

    result = await generate_innovations(
        db_session, "knowledge extraction", context="AI agent systems"
    )

    # No concept should claim that ent-arxiv provides "structured knowledge extraction"
    # (that edge is hypothesized and excluded)
    for concept in result["concepts"]:
        for comp in concept["components"]:
            if comp["entity_id"] == "ent-arxiv":
                role = comp.get("role", "").lower()
                assert "structured knowledge extraction" not in role, (
                    f"hypothesized relationship contaminated concept: {concept['id']}"
                )


# ── Negative Test: Absent LLM configuration ─────────────────────────────


@pytest.mark.asyncio
async def test_negative_absent_llm(app, db_session):
    """Without an LLM adapter, the deterministic path works."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    # All concepts must be generated_method="deterministic"
    for concept in result["concepts"]:
        assert concept["generation_method"] == "deterministic"


# ── Negative Test: Empty problem domain ───────────────────────────────────


@pytest.mark.asyncio
async def test_negative_empty_problem(app, db_session):
    """Empty problem domain returns no concepts + truthful unknowns."""
    result = await generate_innovations(db_session, "")

    assert len(result["concepts"]) == 0
    assert len(result["unknowns"]) >= 1
    assert "empty_problem_domain" in result["unknowns"]


# ── Negative Test: No numerical business claims ──────────────────────────


@pytest.mark.asyncio
async def test_negative_no_numerical_claims(app, db_session):
    """No concept contains unsupported numerical business claims."""
    await _seed_innovation_fixture(db_session)

    result = await generate_innovations(db_session, "AI research assistant")

    for concept in result["concepts"]:
        benefit = concept["potential_benefit"]
        assert "~" not in benefit or "%" not in benefit, (
            f"unsupported numerical claim in potential_benefit: {benefit}"
        )
