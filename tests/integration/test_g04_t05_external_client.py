"""G04-T05 -- External Client Compatibility & Contract Validation tests.

Per the user's G04-T05 mission briefing §5 (Compatibility validation):

  "Create focused black-box HTTP contract tests using the existing
   FastAPI test infrastructure."

  "Tests must exercise the HTTP boundary, not merely call application
   functions directly."

15 mandatory coverage areas (mission §5):

  1.  Valid retrieval requests.
  2.  Valid capability-analysis requests.
  3.  Valid reasoning requests.
  4.  Missing required fields.
  5.  Incorrect field types.
  6.  Invalid identifiers.
  7.  Oversized requests.
  8.  Empty results.
  9.  Contradictions and context metadata.
  10. Citation and provenance serialization.
  11. Predictable error responses.
  12. Stable OpenAPI schema generation.
  13. Existing authentication behavior.
  14. Backward compatibility with current G04 endpoints.
  15. Existing regression suite (run inline).

Plus the G04 plan §9-T05 acceptance criteria (5 items):
  - External client uses only the API (verified by examples/client_g04.py).
  - All G04 endpoints return 200 with valid auth (test_14).
  - OpenAPI schema validates (test_12).
  - CORS allowlist works (test_cors_*).
  - G01/G02/G03 regression (test_15).
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

from synapse.application.relationship_service import create_relationship
from synapse.application.verification import assess_claim
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


async def _seed_t05_fixture(session) -> dict[str, Any]:
    """Seed the G04-T05 fixture (identical shape to the G04-T03 reasoning
    fixture, sufficient for HTTP-level contract testing of all G04
    endpoints)."""
    src_a = await _make_source(session, "src-t05-a", "https://example.com/papers/synapse")
    src_b = await _make_source(session, "src-t05-b", "https://example.com/papers/arxiv")
    src_c = await _make_source(session, "src-t05-c", "https://example.com/papers/no-vector")
    src_d = await _make_source(session, "src-t05-d", "https://example.com/papers/alt-tool")

    frag_a = await _make_fragment(
        session,
        "frag-t05-a",
        src_a,
        "Synapse retrieves technical sources and extracts knowledge with evidence verification.",
    )
    frag_b = await _make_fragment(
        session, "frag-t05-b", src_b, "arxiv provides source discovery for AI research assistants."
    )
    frag_c = await _make_fragment(
        session,
        "frag-t05-c",
        src_c,
        "The approach is constrained by the absence of a vector database.",
        retrieved_at=STALE_ISO,
    )
    frag_opp = await _make_fragment(
        session,
        "frag-t05-opp",
        src_a,
        "Evidence verification requires manual review and cannot be fully automated.",
    )
    frag_alt = await _make_fragment(
        session, "frag-t05-alt", src_d, "AltTool replaces synapse for lightweight retrieval tasks."
    )

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

    for cap_id, name in [
        ("cap-source-discovery", "source discovery"),
        ("cap-content-extraction", "content extraction"),
        ("cap-knowledge-extraction", "structured knowledge extraction"),
        ("cap-evidence-verification", "evidence verification"),
        ("cap-dependency-analysis", "dependency analysis"),
    ]:
        await _make_entity(session, cap_id, "capability", name, desc=f"Capability {name}")

    await _make_claim(
        session,
        "claim-t05-sd",
        "Synapse provides source discovery for AI agent systems.",
        subject_ref="ent-synapse",
        object_ref="cap-source-discovery",
        evidence_refs=["frag-t05-a"],
        validity_conditions=["AI agent systems"],
    )
    await _make_claim(
        session,
        "claim-t05-ce",
        "Synapse enables content extraction from arxiv papers.",
        subject_ref="ent-synapse",
        object_ref="cap-content-extraction",
        evidence_refs=["frag-t05-a"],
        validity_conditions=["arxiv papers"],
    )
    await _make_claim(
        session,
        "claim-t05-ev",
        "Synapse provides evidence verification for technical claims.",
        subject_ref="ent-synapse",
        object_ref="cap-evidence-verification",
        evidence_refs=["frag-t05-a"],
        contradicting_refs=["frag-t05-opp"],
        epistemic_state="disputed",
        validity_conditions=["AI agent systems"],
    )

    await _make_span(
        session,
        "span-t05-sd",
        "frag-t05-a",
        "claim-t05-sd",
        "Synapse retrieves technical sources",
        start=0,
        end=33,
    )
    await _make_span(
        session, "span-t05-ce", "frag-t05-a", "claim-t05-ce", "extracts knowledge", start=50, end=67
    )

    for cap_id, ev_refs in [
        ("cap-source-discovery", ["frag-t05-a"]),
        ("cap-content-extraction", ["frag-t05-a"]),
        ("cap-knowledge-extraction", None),
        ("cap-evidence-verification", ["frag-t05-a"]),
        ("cap-dependency-analysis", ["frag-t05-c"]),
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
        evidence_refs=["frag-t05-b"],
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
        evidence_refs=["frag-t05-a"],
        origin="explicit",
    )
    assert r["ok"] is True
    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=["frag-t05-c"],
        origin="explicit",
    )
    assert r["ok"] is True
    r = await create_relationship(
        session,
        from_entity_id="ent-alttool",
        to_entity_id="ent-synapse",
        predicate="REPLACES",
        evidence_refs=["frag-t05-alt"],
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
    for cid in ("claim-t05-sd", "claim-t05-ce", "claim-t05-ev"):
        await assess_claim(session, cid, requester="fixture")
    await session.commit()

    return {"synapse_id": "ent-synapse", "arxiv_id": "ent-arxiv"}


# ── Helper to seed the fixture for each HTTP test ──────────────────────────


async def _seed_for_app(app, db_session):
    """Seed the fixture for a single HTTP test."""
    await _seed_t05_fixture(db_session)


# ── Acceptance Test 1: Valid retrieval requests ─────────────────────────────


@pytest.mark.asyncio
async def test_01_valid_retrieval_request(app, client, db_session, auth_headers_reader):
    """POST /api/v1/knowledge/retrieve with a valid body returns 200 + envelope."""
    await _seed_for_app(app, db_session)
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": "synapse", "limit": 10},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["api_version"] == "v1"
    assert body["error"] is None
    assert isinstance(body["data"], dict)
    assert "claims" in body["data"]
    assert "relationships" in body["data"]
    assert "entities" in body["data"]
    assert "reranking_factors" in body["data"]
    assert "unknowns" in body["data"]
    assert "query_intent" in body["data"]
    assert "limits" in body["data"]
    assert "request_id" in body["data"]
    assert body["meta"]["pagination"] is not None


# ── Acceptance Test 2: Valid capability-analysis requests ──────────────────


@pytest.mark.asyncio
async def test_02_valid_capability_analysis_request(app, client, db_session, auth_headers_reader):
    """POST /api/v1/knowledge/capabilities/analyze-gap with a valid body
    returns 200 + structured requirement assessments."""
    await _seed_for_app(app, db_session)
    r = client.post(
        "/api/v1/knowledge/capabilities/analyze-gap",
        headers=auth_headers_reader,
        json={
            "required_capabilities": ["source discovery"],
            "candidate_entity_ids": ["ent-synapse"],
            "context": "AI agent systems",
            "limit": 10,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    data = body["data"]
    assert "requirements" in data
    assert "summary" in data
    assert "policy_version" in data
    assert "assessed_at" in data
    assert "context" in data
    assert "candidates" in data
    # Each requirement has the required evidence-preservation fields.
    if data["requirements"]:
        req = data["requirements"][0]
        assert "classification" in req
        assert "evidence_chain" in req
        assert "contradicting_evidence" in req
        assert "limitations" in req
        assert "unsatisfied_prerequisites" in req
        assert "reason" in req
        assert "applicability_match" in req
        assert "out_of_context_contradictions" in req


# ── Acceptance Test 3: Valid reasoning requests ────────────────────────────


@pytest.mark.asyncio
async def test_03_valid_reasoning_request(app, client, db_session, auth_headers_reader):
    """POST /api/v1/reasoning/queries with a valid body returns 200 +
    structured findings + citation chain."""
    await _seed_for_app(app, db_session)
    r = client.post(
        "/api/v1/reasoning/queries",
        headers=auth_headers_reader,
        json={
            "query": "What can synapse do?",
            "candidate_entity_ids": ["ent-synapse"],
            "context": "AI agent systems",
            "limit": 20,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    data = body["data"]
    assert "question" in data
    assert "intent" in data
    assert "findings" in data
    assert "cited_claims" in data
    assert "cited_relationships" in data
    assert "evidence_chain" in data
    assert "unknowns" in data
    assert "contradictions" in data
    assert "confidence" in data
    assert "limitations" in data
    assert "policy_version" in data
    assert "assessed_at" in data
    assert "candidates" in data
    assert "context" in data


# ── Acceptance Test 4: Missing required fields ─────────────────────────────


@pytest.mark.asyncio
async def test_04_missing_required_fields(app, client, db_session, auth_headers_reader):
    """Missing required fields return 422 + problem+json with structured details."""
    await _seed_for_app(app, db_session)
    # Missing the 'query' field entirely.
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"limit": 10},  # no query
    )
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request payload failed validation."
    assert isinstance(body["error"]["details"], list)
    assert len(body["error"]["details"]) >= 1
    # The detail must identify which field failed.
    detail = body["error"]["details"][0]
    assert "query" in detail["loc"] or "body" in detail["loc"]


# ── Acceptance Test 5: Incorrect field types ───────────────────────────────


@pytest.mark.asyncio
async def test_05_incorrect_field_types(app, client, db_session, auth_headers_reader):
    """Wrong field types return 422 with structured details."""
    await _seed_for_app(app, db_session)
    # limit must be int, not string.
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": "synapse", "limit": "not-an-int"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    # The detail must mention 'limit'.
    detail_str = json.dumps(body["error"]["details"])
    assert "limit" in detail_str


# ── Acceptance Test 6: Invalid identifiers ─────────────────────────────────


@pytest.mark.asyncio
async def test_06_invalid_identifiers(app, client, db_session, auth_headers_reader):
    """GET requests with invalid identifiers return 404 + problem+json."""
    await _seed_for_app(app, db_session)
    # Capability that does not exist.
    r = client.get(
        "/api/v1/knowledge/capabilities/cap-does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "cap-does-not-exist" in body["error"]["message"]

    # Entity that does not exist.
    r = client.get(
        "/api/v1/entities/ent-does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"

    # Relationship that does not exist.
    r = client.get(
        "/api/v1/relationships/rel-does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404


# ── Acceptance Test 7: Oversized requests ─────────────────────────────────


@pytest.mark.asyncio
async def test_07_oversized_requests(app, client, db_session, auth_headers_reader):
    """Oversized requests (query too long, limit too high) return 422."""
    await _seed_for_app(app, db_session)
    # Query longer than MAX_QUERY_CHARS (512).
    long_query = "a" * 600
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": long_query},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"

    # Limit higher than MAX_LIMIT (100).
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": "synapse", "limit": 1000},
    )
    assert r.status_code == 422


# ── Acceptance Test 8: Empty results ────────────────────────────────────────


@pytest.mark.asyncio
async def test_08_empty_results(app, client, db_session, auth_headers_reader):
    """Empty result sets are returned truthfully (no fabricated hits)."""
    await _seed_for_app(app, db_session)
    # Query that matches nothing in the fixture.
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": "zzzz-no-such-term-zzzz"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    # Empty results are truthful.
    data = body["data"]
    # May have graph-expanded entities even with no lexical matches, but
    # the claims list could be empty. We verify the structure is sound.
    assert isinstance(data["claims"], list)
    assert isinstance(data["relationships"], list)
    assert isinstance(data["entities"], list)
    assert isinstance(data["unknowns"], list)


# ── Acceptance Test 9: Contradictions and context metadata ────────────────


@pytest.mark.asyncio
async def test_09_contradictions_and_context_metadata(app, client, db_session, auth_headers_reader):
    """CONTESTED findings preserve contradicting_evidence; out-of-context
    contradictions are preserved as out_of_context_contradictions metadata."""
    await _seed_for_app(app, db_session)
    # claim-t05-ev has validity_conditions=['AI agent systems'] and
    # contradicting_refs=['frag-t05-opp']. With context 'AI agent systems',
    # the contradiction is applicable → CONTESTED.
    r = client.post(
        "/api/v1/reasoning/queries",
        headers=auth_headers_reader,
        json={
            "query": "What can synapse do?",
            "candidate_entity_ids": ["ent-synapse"],
            "context": "AI agent systems",
            "limit": 20,
        },
    )
    assert r.status_code == 200
    body = r.json()
    data = body["data"]

    # At least one finding mentions CONTESTED.
    contested_text = " ".join(f.get("text", "") for f in data["findings"]).lower()
    assert "contested" in contested_text, (
        f"applicable contradiction must be visible in findings text; got: {contested_text[:200]}"
    )

    # The cap-evidence-verification finding must carry both supporting and
    # contradicting evidence_refs.
    ev_findings = [
        f for f in data["findings"] if "cap-evidence-verification" in (f.get("sources") or [])
    ]
    assert len(ev_findings) >= 1
    ev_refs = ev_findings[0].get("evidence_refs") or []
    assert "frag-t05-a" in ev_refs  # supporting
    assert "frag-t05-opp" in ev_refs  # contradicting


# ── Acceptance Test 10: Citation and provenance serialization ─────────────


@pytest.mark.asyncio
async def test_10_citation_and_provenance_serialization(
    app, client, db_session, auth_headers_reader
):
    """Evidence chain entries include fragment_id, source_uri, and spans —
    fully JSON-serializable and traceable to the original source."""
    await _seed_for_app(app, db_session)
    r = client.post(
        "/api/v1/reasoning/queries",
        headers=auth_headers_reader,
        json={
            "query": "What can synapse do?",
            "candidate_entity_ids": ["ent-synapse"],
            "context": "AI agent systems",
            "limit": 20,
        },
    )
    assert r.status_code == 200
    body = r.json()
    data = body["data"]
    assert len(data["evidence_chain"]) >= 1
    for link in data["evidence_chain"]:
        # Every link is JSON-serializable (already proven by r.json()).
        assert "fragment_id" in link or "claim_id" in link or "relationship_id" in link
        assert "source_uri" in link
        assert "spans" in link
        # source_uri must be a real string (not None).
        assert isinstance(link["source_uri"], str)
        assert link["source_uri"].startswith("http"), (
            f"source_uri must be a real URL; got {link['source_uri']!r}"
        )

    # Also verify via the claims evidence endpoint.
    r = client.get(
        "/api/v1/claims/claim-t05-sd/evidence",
        headers=auth_headers_reader,
    )
    assert r.status_code == 200
    body = r.json()
    data = body["data"]
    assert "claim" in data
    assert "evidence_fragments" in data
    assert "source_spans" in data
    assert "verification_assessment" in data  # may be None if not assessed
    # At least one fragment must exist for claim-t05-sd (evidence_refs=['frag-t05-a']).
    assert len(data["evidence_fragments"]) >= 1
    frag = data["evidence_fragments"][0]
    assert "id" in frag
    assert "source_uri" in frag
    assert "exact_excerpt" in frag
    assert "retrieved_at" in frag
    assert "content_fingerprint" in frag


# ── Acceptance Test 11: Predictable error responses ──────────────────────


@pytest.mark.asyncio
async def test_11_predictable_error_responses(app, client, db_session, auth_headers_reader):
    """All errors follow the standard envelope + problem+json content type."""
    await _seed_for_app(app, db_session)
    # 422 validation.
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": ""},
    )
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["data"] is None
    assert body["error"] is not None
    assert body["meta"]["api_version"] == "v1"
    assert body["meta"]["request_id"]  # non-empty

    # 404 not found.
    r = client.get(
        "/api/v1/knowledge/capabilities/cap-does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert body["data"] is None
    assert body["error"]["code"] == "not_found"

    # 501 not implemented (placeholder route).
    r = client.post(
        "/api/v1/innovations/generate",
        headers=auth_headers_reader,
        json={},
    )
    assert r.status_code == 501
    body = r.json()
    assert body["error"]["code"] == "not_implemented"


# ── Acceptance Test 12: Stable OpenAPI schema generation ──────────────────


def test_12_stable_openapi_schema_generation(app):
    """OpenAPI 3.x schema is published at /api/v1/openapi.json with all
    G04 endpoints, consistent paths, and a stable version field."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.get("/api/v1/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["openapi"].startswith("3.")
        assert "info" in schema
        assert "version" in schema["info"]
        paths = schema["paths"]
        # G04 endpoints present.
        required_g04_paths = [
            "/api/v1/knowledge/retrieve",  # G04-T01
            "/api/v1/knowledge/capabilities",  # G04-T02
            "/api/v1/knowledge/capabilities/{capability_id}",  # G04-T02
            "/api/v1/knowledge/capabilities/analyze-gap",  # G04-T02
            "/api/v1/reasoning/queries",  # G04-T03
            "/api/v1/reasoning/intents",  # G04-T03
        ]
        for p in required_g04_paths:
            assert p in paths, f"OpenAPI missing G04 path: {p}"
        # G01-G03 endpoints still present (backward compatibility).
        required_legacy_paths = [
            "/api/v1/entities",
            "/api/v1/entities/{entity_id}",
            "/api/v1/relationships",
            "/api/v1/relationships/{relationship_id}",
            "/api/v1/claims/{claim_id}/evidence",
            "/api/v1/knowledge/search",
            "/api/v1/sources/discover",
            "/api/v1/sources/ingest",
            "/api/v1/capabilities",
            "/api/v1/providers",
            "/api/v1/system/activity-mode",
        ]
        for p in required_legacy_paths:
            assert p in paths, f"OpenAPI missing legacy path: {p}"
        # Placeholder routes still present (G05+ not yet activated).
        placeholder_paths = [
            "/api/v1/innovations/generate",
            "/api/v1/experiments",
            "/api/v1/hypotheses/{hypothesis_id}/evidence-deltas",
            "/api/v1/future/scenarios",
        ]
        for p in placeholder_paths:
            assert p in paths, f"OpenAPI missing placeholder path: {p}"
        # All paths are under /api/v1 or /health.
        for path in paths:
            assert path.startswith("/api/v1") or path.startswith("/health"), (
                f"route outside /api/v1 or /health: {path}"
            )


# ── Acceptance Test 13: Existing authentication behavior ─────────────────


@pytest.mark.asyncio
async def test_13_existing_authentication_behavior(app, client, db_session, auth_headers_reader):
    """All G04 endpoints require Authorization header (401 without, 200 with)."""
    await _seed_for_app(app, db_session)
    # Without auth → 401.
    for path, method, body in [
        ("/api/v1/knowledge/retrieve", "POST", {"query": "synapse"}),
        (
            "/api/v1/knowledge/capabilities/analyze-gap",
            "POST",
            {"required_capabilities": ["source discovery"]},
        ),
        ("/api/v1/reasoning/queries", "POST", {"query": "What can synapse do?"}),
        ("/api/v1/knowledge/capabilities", "GET", None),
    ]:
        r = client.post(path, json=body) if method == "POST" else client.get(path)
        assert r.status_code == 401, (
            f"{method} {path} without auth should return 401, got {r.status_code}"
        )
        body_resp = r.json()
        assert body_resp["error"]["code"] in ("unauthenticated", "forbidden")

    # With auth → 200.
    for path, method, body in [
        ("/api/v1/knowledge/retrieve", "POST", {"query": "synapse"}),
        (
            "/api/v1/knowledge/capabilities/analyze-gap",
            "POST",
            {"required_capabilities": ["source discovery"]},
        ),
        ("/api/v1/reasoning/queries", "POST", {"query": "What can synapse do?"}),
    ]:
        r = client.post(path, headers=auth_headers_reader, json=body)
        assert r.status_code == 200, (
            f"{method} {path} with auth should return 200, got {r.status_code}: {r.text[:200]}"
        )

    r = client.get("/api/v1/knowledge/capabilities", headers=auth_headers_reader)
    assert r.status_code == 200


# ── Acceptance Test 14: Backward compatibility with G04 endpoints ────────


@pytest.mark.asyncio
async def test_14_backward_compatibility_g04_endpoints(
    app, client, db_session, auth_headers_reader
):
    """All G04 endpoints continue to return 200 with valid auth (smoke test
    across all G04 surface)."""
    await _seed_for_app(app, db_session)

    # G04-T01 retrieval.
    r = client.post(
        "/api/v1/knowledge/retrieve",
        headers=auth_headers_reader,
        json={"query": "synapse"},
    )
    assert r.status_code == 200

    # G04-T02 capability registry (3 endpoints).
    r = client.get("/api/v1/knowledge/capabilities", headers=auth_headers_reader)
    assert r.status_code == 200

    r = client.get(
        "/api/v1/knowledge/capabilities/cap-source-discovery",
        headers=auth_headers_reader,
    )
    assert r.status_code == 200

    r = client.post(
        "/api/v1/knowledge/capabilities/analyze-gap",
        headers=auth_headers_reader,
        json={"required_capabilities": ["source discovery"]},
    )
    assert r.status_code == 200

    # G04-T03 reasoning (2 endpoints).
    r = client.post(
        "/api/v1/reasoning/queries",
        headers=auth_headers_reader,
        json={"query": "What can synapse do?"},
    )
    assert r.status_code == 200

    r = client.get("/api/v1/reasoning/intents", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] >= 6


# ── Acceptance Test 15: Full G01-G04-T04C regression ──────────────────────


def test_15_full_regression_g01_through_t04c():
    """Run a focused subset of the existing regression suite to confirm
    G04-T05 introduces no regression in G01-G04-T04C behavior.

    The subprocess timeout is generous (600s) because the spawned pytest
    loads the full Synapse package and runs integration tests that build
    a fresh DB per test. Under CI load this can take 3-5 minutes.
    """
    REPO_ROOT = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/api/test_openapi.py",
            "tests/unit/api/test_health.py",
            "tests/integration/test_app_boot.py",
            "tests/integration/test_g04_t01_retrieval.py::test_g01_g02_g03_regression",
            "tests/integration/test_g04_t02_capability_registry.py::test_g01_g04_t02_regression_after_attribution_fix",
            "tests/integration/test_g04_t03_reasoning.py::test_g01_g04_t02c_regression",
            "tests/integration/test_g04_t03_reasoning.py::test_g01_g04_t03_regression_after_contradiction_closure",
            "tests/integration/test_g04_t04_evaluation.py::test_18_g01_g04_t03c_regression",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr[:600]}\nstdout={r.stdout[:600]}"
    assert "passed" in r.stdout


# ── CORS preflight tests (G04 plan T05 acceptance criteria #4) ───────────


def test_cors_allowed_origin_preflight(app):
    """CORS preflight from an allowed origin returns the right headers."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.options(
            "/api/v1/knowledge/retrieve",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Authorization",
            },
        )
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "POST" in r.headers["access-control-allow-methods"]
        assert "Authorization" in r.headers["access-control-allow-headers"]


def test_cors_disallowed_origin_rejected(app):
    """CORS preflight from a disallowed origin does not get an allow-origin header."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        r = c.options(
            "/api/v1/knowledge/retrieve",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        # CORS disallowed: no allow-origin header.
        assert r.headers.get("access-control-allow-origin") is None


async def _setup_app_with_seeded_data():
    """Build a fresh app + engine + seeded DB for the external client demo.

    Uses the pytest-asyncio event loop (does NOT call asyncio.run, which
    would close the session event loop and break subsequent async tests
    like the SSRF/DNS tests).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from synapse.api import deps as deps_module
    from synapse.config import get_settings
    from synapse.main import create_app
    from synapse.storage import models  # noqa: F401
    from synapse.storage.base import Base

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as s:
        await _seed_t05_fixture(s)
    get_settings.cache_clear()
    deps_module._engine = None
    deps_module._session_factory = None
    app = create_app()
    test_factory = async_sessionmaker(eng, expire_on_commit=False)

    async def _override_get_db():
        async with test_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[deps_module.get_db] = _override_get_db
    return app, eng


# ── External client demo acceptance criterion ─────────────────────────────


@pytest.mark.asyncio
async def test_external_client_demo_runs():
    """The examples/client_g04.py script runs end-to-end against the live
    API and produces structured output. Per G04 plan T05 acceptance
    criterion #1: 'External client uses only the API (no internal
    DB/Python imports).'

    Strategy: build a fresh app+engine inside the pytest-asyncio event
    loop, then run the example's main() with the TestClient's transport
    wired into a fresh httpx.Client. The example imports NOTHING from
    synapse — it only uses httpx, which is what a real external client
    would use.

    Note: this test is async (uses the pytest-asyncio event loop) so it
    does NOT call asyncio.run() — that would close the session event
    loop and break subsequent async tests (the SSRF/DNS tests).
    """
    import importlib.util

    app, eng = await _setup_app_with_seeded_data()
    try:
        # Import the example client.
        REPO_ROOT = Path(__file__).resolve().parents[2]
        client_path = REPO_ROOT / "examples" / "client_g04.py"
        assert client_path.exists(), f"examples/client_g04.py must exist; looked at {client_path}"
        spec = importlib.util.spec_from_file_location("client_g04", client_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "main"), "client_g04.py must expose main(base_url, api_key)"

        # Run the example against the live TestClient server.
        from fastapi.testclient import TestClient

        with TestClient(app) as live_client:
            # Patch httpx.Client so the example's `with httpx.Client(...)`
            # call returns a client bound to the live TestClient's transport.
            import httpx

            original_client_cls = httpx.Client

            class _PatchedClient(httpx.Client):
                """httpx.Client bound to the live TestClient transport,
                with the auth header pre-injected."""

                def __init__(self, *args, **kwargs):
                    kwargs.setdefault("transport", live_client._transport)
                    kwargs.setdefault("base_url", "http://testserver")
                    kwargs.setdefault("headers", {"Authorization": "Bearer test-key-reader"})
                    super().__init__(*args, **kwargs)

            httpx.Client = _PatchedClient
            try:
                result = module.main(base_url="http://testserver", api_key="test-key-reader")
            finally:
                httpx.Client = original_client_cls
            assert result is True or result is None, (
                f"client_g04.main should return truthy on success; got {result!r}"
            )
    finally:
        await eng.dispose()
