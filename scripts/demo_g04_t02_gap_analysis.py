"""Realistic demonstration of G04-T02 capability registry + gap analysis.

Builds the same fixture used in the test suite (an AI research assistant
scenario) and runs the gap analysis from mission briefing §6.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

os.environ.setdefault("SYNAPSE_ENV", "test")
os.environ.setdefault("SYNAPSE_DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNAPSE_AUTH_MODE", "development")
os.environ.setdefault("SYNAPSE_DEV_API_KEYS", "test-key-reader")
os.environ.setdefault("SYNAPSE_ADMIN_API_KEYS", "test-key-admin")
os.environ.setdefault("SYNAPSE_CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("SYNAPSE_CORS_ALLOW_CREDENTIALS", "true")
os.environ.setdefault("SYNAPSE_RATE_LIMIT_PER_MINUTE", "10000")
os.environ.setdefault("SYNAPSE_RATE_LIMIT_BURST", "1000")
os.environ.setdefault("SYNAPSE_LOG_LEVEL", "WARNING")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from synapse.application.capability_registry import (  # noqa: E402
    get_capability,
    list_capabilities,
)
from synapse.application.gap_analyzer import analyze_gap  # noqa: E402
from synapse.application.relationship_service import create_relationship  # noqa: E402
from synapse.application.verification import assess_claim  # noqa: E402
from synapse.storage import models  # noqa: E402, F401
from synapse.storage.base import Base  # noqa: E402
from synapse.storage.models import (  # noqa: E402
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()
STALE_ISO = (datetime.now(UTC) - timedelta(days=500)).isoformat()


async def seed(session) -> None:
    src_a = SourceRow(
        id="src-a",
        canonical_uri="https://example.com/papers/synapse",
        source_type="paper",
        status="extracted",
    )
    src_b = SourceRow(
        id="src-b",
        canonical_uri="https://example.com/papers/arxiv",
        source_type="paper",
        status="extracted",
    )
    src_c = SourceRow(
        id="src-c",
        canonical_uri="https://example.com/papers/no-vector",
        source_type="paper",
        status="extracted",
    )
    session.add_all([src_a, src_b, src_c])
    await session.flush()

    frags = [
        EvidenceFragmentRow(
            id="frag-a",
            acquisition_id="acq-a",
            source_id=src_a.id,
            source_uri=src_a.canonical_uri,
            exact_excerpt="Synapse retrieves technical sources from arxiv and extracts knowledge with evidence verification. The system identifies technical dependencies.",
            excerpt_hash="h-a",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-a",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-b",
            acquisition_id="acq-b",
            source_id=src_b.id,
            source_uri=src_b.canonical_uri,
            exact_excerpt="arxiv provides source discovery capability for AI research assistants.",
            excerpt_hash="h-b",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-b",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-c",
            acquisition_id="acq-c",
            source_id=src_c.id,
            source_uri=src_c.canonical_uri,
            exact_excerpt="The approach is constrained by the absence of a vector database.",
            excerpt_hash="h-c",
            retrieved_at=STALE_ISO,
            extraction_method="demo",
            content_fingerprint="fp-c",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-opp",
            acquisition_id="acq-opp",
            source_id=src_a.id,
            source_uri=src_a.canonical_uri,
            exact_excerpt="Evidence verification requires manual review and cannot be fully automated.",
            excerpt_hash="h-o",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-o",
            toolkit_commit_sha="demo",
        ),
    ]
    session.add_all(frags)
    await session.flush()

    ents = [
        EntityRow(
            id="ent-synapse",
            kind="tool",
            canonical_name="synapse",
            aliases="[]",
            attributes="{}",
            description="AgentCraft Synapse knowledge engine",
            version=1,
        ),
        EntityRow(
            id="ent-arxiv",
            kind="technology",
            canonical_name="arxiv",
            aliases="[]",
            attributes="{}",
            description="arXiv paper repository",
            version=1,
        ),
        EntityRow(
            id="ent-trafilatura",
            kind="technology",
            canonical_name="trafilatura",
            aliases="[]",
            attributes="{}",
            description="Web content extraction library",
            version=1,
        ),
        EntityRow(
            id="ent-rs",
            kind="technology",
            canonical_name="RelationshipService",
            aliases="[]",
            attributes="{}",
            description="Graph traversal service for knowledge graph",
            version=1,
        ),
        EntityRow(
            id="ent-no-vector",
            kind="constraint",
            canonical_name="no vector database",
            aliases="[]",
            attributes="{}",
            description="Constraint: no vector DB in the minimal slice",
            version=1,
        ),
        EntityRow(
            id="cap-source-discovery",
            kind="capability",
            canonical_name="source discovery",
            aliases="[]",
            attributes="{}",
            description="Discover technical sources",
            version=1,
        ),
        EntityRow(
            id="cap-content-extraction",
            kind="capability",
            canonical_name="content extraction",
            aliases="[]",
            attributes="{}",
            description="Extract text and metadata",
            version=1,
        ),
        EntityRow(
            id="cap-knowledge-extraction",
            kind="capability",
            canonical_name="structured knowledge extraction",
            aliases="[]",
            attributes="{}",
            description="Extract entities, claims, relationships",
            version=1,
        ),
        EntityRow(
            id="cap-evidence-verification",
            kind="capability",
            canonical_name="evidence verification",
            aliases="[]",
            attributes="{}",
            description="Verify claims against source evidence",
            version=1,
        ),
        EntityRow(
            id="cap-dependency-analysis",
            kind="capability",
            canonical_name="dependency analysis",
            aliases="[]",
            attributes="{}",
            description="Identify technical dependencies",
            version=1,
        ),
        EntityRow(
            id="cap-fuzzy-similar",
            kind="capability",
            canonical_name="fuzzy similar capability",
            aliases="[]",
            attributes="{}",
            description="Used to test the lexical-similarity rule",
            version=1,
        ),
    ]
    session.add_all(ents)
    await session.flush()

    claims = [
        ClaimRow(
            id="claim-source-discovery",
            proposition="Synapse provides source discovery for AI agent systems.",
            subject_ref="ent-synapse",
            object_ref="cap-source-discovery",
            evidence_refs='["frag-a"]',
            validity_conditions='["AI agent systems"]',
            epistemic_state="supported",
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-content-extraction",
            proposition="Synapse enables content extraction from arxiv papers.",
            subject_ref="ent-synapse",
            object_ref="cap-content-extraction",
            evidence_refs='["frag-a"]',
            validity_conditions='["arxiv papers"]',
            epistemic_state="supported",
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-dependency-analysis",
            proposition="Synapse provides dependency analysis capabilities.",
            subject_ref="ent-synapse",
            object_ref="cap-dependency-analysis",
            evidence_refs='["frag-c"]',
            validity_conditions='["AI agent systems"]',
            epistemic_state="supported",
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-evidence-verification",
            proposition="Synapse provides evidence verification for technical claims.",
            subject_ref="ent-synapse",
            object_ref="cap-evidence-verification",
            evidence_refs='["frag-a"]',
            contradicting_refs='["frag-opp"]',
            epistemic_state="disputed",
            validity_conditions='["AI agent systems"]',
            extraction_method="demo",
            version=1,
        ),
    ]
    session.add_all(claims)
    await session.flush()

    spans = [
        SourceSpanRow(
            id="span-sd",
            evidence_fragment_id="frag-a",
            claim_id="claim-source-discovery",
            start_offset=0,
            end_offset=33,
            excerpt="Synapse retrieves technical sources",
            context_before="",
            context_after="",
        ),
        SourceSpanRow(
            id="span-ce",
            evidence_fragment_id="frag-a",
            claim_id="claim-content-extraction",
            start_offset=80,
            end_offset=99,
            excerpt="extracts knowledge",
            context_before="",
            context_after="",
        ),
        SourceSpanRow(
            id="span-da",
            evidence_fragment_id="frag-c",
            claim_id="claim-dependency-analysis",
            start_offset=20,
            end_offset=50,
            excerpt="absence of a vector database",
            context_before="",
            context_after="",
        ),
    ]
    session.add_all(spans)

    for cap_id in (
        "cap-source-discovery",
        "cap-content-extraction",
        "cap-knowledge-extraction",
        "cap-evidence-verification",
        "cap-dependency-analysis",
        "cap-fuzzy-similar",
    ):
        ev_refs = ["frag-a"] if cap_id != "cap-knowledge-extraction" else None
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
        evidence_refs=["frag-b"],
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
        evidence_refs=["frag-a"],
        origin="explicit",
    )
    assert r["ok"] is True

    r = await create_relationship(
        session,
        from_entity_id="ent-no-vector",
        to_entity_id="cap-evidence-verification",
        predicate="LIMITS",
        evidence_refs=["frag-c"],
        origin="explicit",
    )
    assert r["ok"] is True

    await session.commit()
    for cid in (
        "claim-source-discovery",
        "claim-content-extraction",
        "claim-dependency-analysis",
        "claim-evidence-verification",
    ):
        await assess_claim(session, cid, requester="demo")
    await session.commit()


def truncate(obj, max_str: int = 150, depth: int = 6):
    if depth <= 0:
        return "..."
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "..."
    if isinstance(obj, list):
        return [truncate(x, max_str, depth - 1) for x in obj[:5]]
    if isinstance(obj, dict):
        return {k: truncate(v, max_str, depth - 1) for k, v in list(obj.items())[:15]}
    return obj


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session)

        print("=" * 78)
        print("G04-T02 -- CAPABILITY REGISTRY + GAP ANALYSIS DEMONSTRATION")
        print("=" * 78)

        # Part 1: list capabilities
        print("\n--- PART 1: list_capabilities() ---")
        cap_list = await list_capabilities(session, limit=50)
        print(f"Total capabilities: {cap_list['count']}")
        for item in cap_list["items"]:
            print(
                f"  - [{item['kind']}] {item['canonical_name']}  "
                f"providers={item['provider_count']}  "
                f"limitations={item['limitation_count']}"
            )

        # Part 2: get_capability detail for cap-source-discovery
        print("\n--- PART 2: get_capability(cap-source-discovery) ---")
        detail = await get_capability(session, "cap-source-discovery")
        if detail:
            print(f"Capability: {detail['canonical_name']}  (id={detail['id']})")
            print(f"  description: {detail.get('description', '(none)')}")
            print(f"  providers ({len(detail['providers'])}):")
            for p in detail["providers"]:
                print(
                    f"    - {p['canonical_name']} ({p['kind']}) via {p['predicate']}"
                    f"  has_evidence={p['has_evidence']}"
                )
                for d in p.get("dependencies", []):
                    print(f"      dep: {d['canonical_name']} ({d['predicate']})")
            print(f"  limitations ({len(detail['limitations'])}):")
            for lim in detail["limitations"]:
                print(
                    f"    - {lim['from_canonical_name']} {lim['predicate']} "
                    f"this capability  has_evidence={lim['has_evidence']}"
                )

        # Part 3: gap analysis
        print("\n--- PART 3: analyze_gap() -- AI research assistant scenario ---")
        print("Objective: Build an AI research assistant that retrieves technical")
        print("sources, extracts useful knowledge, verifies evidence, and")
        print("identifies technical dependencies.")
        print()
        print("Required capabilities:")
        reqs = [
            "source discovery",
            "content extraction",
            "structured knowledge extraction",
            "evidence verification",
            "dependency analysis",
        ]
        for r in reqs:
            print(f"  - {r}")
        print()
        print("Context: 'AI agent systems arxiv papers'")
        print("Candidate: ent-synapse")
        print()

        result = await analyze_gap(
            session,
            reqs,
            context="AI agent systems arxiv papers",
            candidate_entity_ids=["ent-synapse"],
        )

        print("=== Gap analysis summary ===")
        print(json.dumps(result["summary"], indent=2))
        print()
        print("=== Gap views (separated per mission briefing §4) ===")
        print(json.dumps(truncate(result["gap_views"]), indent=2))

        print()
        print("=== Per-requirement assessments ===")
        for a in result["requirements"]:
            print(f"\n  Requirement: {a['required_capability']}")
            print(f"    match_quality: {a['match_quality']}")
            print(f"    classification: {a['classification']}")
            print(f"    reason: {a['reason']}")
            print(f"    candidates: {len(a['candidates'])}")
            for c in a["candidates"][:2]:
                print(
                    f"      - {c['provider_canonical_name']} ({c['predicate']})"
                    f"  has_evidence={c['has_evidence']}"
                )
            print(f"    limitations: {len(a['limitations'])}")
            for lim in a["limitations"][:2]:
                print(f"      - {lim['from_canonical_name']} {lim['predicate']}")
            print(f"    contradicting_evidence: {a['contradicting_evidence']}")
            print(f"    unsatisfied_prerequisites: {a['unsatisfied_prerequisites']}")
            print(f"    evidence_chain: {len(a['evidence_chain'])} links")
            for link in a["evidence_chain"][:2]:
                print(f"      - fragment_id={link['fragment_id']}  source_uri={link['source_uri']}")
                for span in link["spans"][:2]:
                    print(
                        f"        span: [{span['start_offset']}:{span['end_offset']}] {span['excerpt'][:80]}"
                    )

        # Part 4: citation chain for the SUPPORTED result
        print("\n=== CITATION CHAIN EXAMPLE (source discovery = SUPPORTED) ===")
        for a in result["requirements"]:
            if a["required_capability"] == "source discovery":
                print(f"Required capability: {a['required_capability']}")
                print(f"  capability_id: {a['capability_id']}")
                print(f"  classification: {a['classification']}")
                print(f"  reason: {a['reason']}")
                print("\n  Citation chain:")
                for i, link in enumerate(a["evidence_chain"], 1):
                    print(f"    Link {i}:")
                    print(f"      fragment_id: {link['fragment_id']}")
                    print(f"      source_uri: {link['source_uri']}")
                    print(f"      retrieved_at: {link['retrieved_at']}")
                    for span in link["spans"]:
                        print(f"      span: id={span['id']}")
                        print(f"             claim_id={span['claim_id']}")
                        print(
                            f"             offsets=[{span['start_offset']}, {span['end_offset']})"
                        )
                        print(f"             excerpt: {span['excerpt']}")
                if a["candidates"]:
                    c = a["candidates"][0]
                    print("\n  Provider relationship:")
                    print(f"    relationship_id: {c['relationship_id']}")
                    print(f"    predicate: {c['predicate']}")
                    print(
                        f"    provider: {c['provider_canonical_name']} (kind={c['provider_kind']})"
                    )
                    print(f"    provider_id: {c['provider_entity_id']}")
                break

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
