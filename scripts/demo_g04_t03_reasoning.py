"""Realistic demonstration of G04-T03 evidence-grounded reasoning.

Builds the same AI-research-assistant fixture and runs the reasoning
layer over multiple intents, showing the structured answer, findings,
citation chains, contradictions, and unknowns.
"""

from __future__ import annotations

import asyncio
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

from synapse.application.reasoning import answer_query  # noqa: E402
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
    sources = [
        SourceRow(
            id=f"src-rsn-{s}",
            canonical_uri=f"https://example.com/{s}",
            source_type="paper",
            status="extracted",
        )
        for s in ("a", "b", "c", "d")
    ]
    session.add_all(sources)
    await session.flush()
    src_a, src_b, src_c, src_d = sources

    frags = [
        EvidenceFragmentRow(
            id="frag-rsn-a",
            acquisition_id="acq-a",
            source_id=src_a.id,
            source_uri=src_a.canonical_uri,
            exact_excerpt="Synapse retrieves technical sources and extracts knowledge with evidence verification.",
            excerpt_hash="h-a",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-a",
            toolkit_commit_sha="d",
        ),
        EvidenceFragmentRow(
            id="frag-rsn-b",
            acquisition_id="acq-b",
            source_id=src_b.id,
            source_uri=src_b.canonical_uri,
            exact_excerpt="arxiv provides source discovery for AI research assistants.",
            excerpt_hash="h-b",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-b",
            toolkit_commit_sha="d",
        ),
        EvidenceFragmentRow(
            id="frag-rsn-c",
            acquisition_id="acq-c",
            source_id=src_c.id,
            source_uri=src_c.canonical_uri,
            exact_excerpt="The approach is constrained by the absence of a vector database.",
            excerpt_hash="h-c",
            retrieved_at=STALE_ISO,
            extraction_method="demo",
            content_fingerprint="fp-c",
            toolkit_commit_sha="d",
        ),
        EvidenceFragmentRow(
            id="frag-rsn-opp",
            acquisition_id="acq-opp",
            source_id=src_a.id,
            source_uri=src_a.canonical_uri,
            exact_excerpt="Evidence verification requires manual review and cannot be fully automated.",
            excerpt_hash="h-o",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-o",
            toolkit_commit_sha="d",
        ),
        EvidenceFragmentRow(
            id="frag-rsn-alt",
            acquisition_id="acq-alt",
            source_id=src_d.id,
            source_uri=src_d.canonical_uri,
            exact_excerpt="AltTool replaces synapse for lightweight retrieval tasks.",
            excerpt_hash="h-alt",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-alt",
            toolkit_commit_sha="d",
        ),
    ]
    session.add_all(frags)
    await session.flush()

    ents = [
        EntityRow(
            id="ent-synapse",
            kind="tool",
            canonical_name="synapse",
            aliases='["Synapse"]',
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
            description="Graph traversal service",
            version=1,
        ),
        EntityRow(
            id="ent-no-vector",
            kind="constraint",
            canonical_name="no vector database",
            aliases="[]",
            attributes="{}",
            description="Constraint: no vector DB",
            version=1,
        ),
        EntityRow(
            id="ent-alttool",
            kind="tool",
            canonical_name="AltTool",
            aliases="[]",
            attributes="{}",
            description="Alternative lightweight retrieval tool",
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
    ]
    session.add_all(ents)
    await session.flush()

    claims = [
        ClaimRow(
            id="claim-rsn-sd",
            proposition="Synapse provides source discovery for AI agent systems.",
            subject_ref="ent-synapse",
            object_ref="cap-source-discovery",
            evidence_refs='["frag-rsn-a"]',
            validity_conditions='["AI agent systems"]',
            epistemic_state="supported",
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-rsn-ce",
            proposition="Synapse enables content extraction from arxiv papers.",
            subject_ref="ent-synapse",
            object_ref="cap-content-extraction",
            evidence_refs='["frag-rsn-a"]',
            validity_conditions='["arxiv papers"]',
            epistemic_state="supported",
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-rsn-ev",
            proposition="Synapse provides evidence verification for technical claims.",
            subject_ref="ent-synapse",
            object_ref="cap-evidence-verification",
            evidence_refs='["frag-rsn-a"]',
            contradicting_refs='["frag-rsn-opp"]',
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
            id="span-rsn-sd",
            evidence_fragment_id="frag-rsn-a",
            claim_id="claim-rsn-sd",
            start_offset=0,
            end_offset=33,
            excerpt="Synapse retrieves technical sources",
            context_before="",
            context_after="",
        ),
        SourceSpanRow(
            id="span-rsn-ce",
            evidence_fragment_id="frag-rsn-a",
            claim_id="claim-rsn-ce",
            start_offset=50,
            end_offset=67,
            excerpt="extracts knowledge",
            context_before="",
            context_after="",
        ),
    ]
    session.add_all(spans)

    for cap_id, ev_refs in [
        ("cap-source-discovery", ["frag-rsn-a"]),
        ("cap-content-extraction", ["frag-rsn-a"]),
        ("cap-knowledge-extraction", None),
        ("cap-evidence-verification", ["frag-rsn-a"]),
        ("cap-dependency-analysis", ["frag-rsn-c"]),
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
        await assess_claim(session, cid, requester="demo")
    await session.commit()


def truncate(obj, max_str=150, depth=5):
    if depth <= 0:
        return "..."
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "..."
    if isinstance(obj, list):
        return [truncate(x, max_str, depth - 1) for x in obj[:5]]
    if isinstance(obj, dict):
        return {k: truncate(v, max_str, depth - 1) for k, v in list(obj.items())[:15]}
    return obj


def print_answer(label, answer):
    print(f"\n{'=' * 78}")
    print(f"  {label}")
    print(f"{'=' * 78}")
    print(f"\nQuestion: {answer['question']}")
    print(f"Intent: {answer['intent']}")
    print(f"Answer: {answer['answer_text']}")
    print(f"\nFindings ({len(answer['findings'])}):")
    for i, f in enumerate(answer["findings"], 1):
        print(f"  {i}. [{f['type']}] {f['text']}")
        print(f"     confidence={f.get('confidence', 0):.2f}  caveat={f.get('caveat')}")
        print(f"     sources={f.get('sources', [])}  evidence_refs={f.get('evidence_refs', [])}")
    print(f"\nCited claims: {answer['cited_claims']}")
    print(f"Cited relationships: {len(answer['cited_relationships'])} total")
    print(f"\nEvidence chain ({len(answer['evidence_chain'])} links):")
    for link in answer["evidence_chain"][:3]:
        print(f"  - fragment_id={link['fragment_id']}")
        print(f"    source_uri={link['source_uri']}")
        for span in link.get("spans", [])[:2]:
            print(f"    span [{span['start_offset']}:{span['end_offset']}]: {span['excerpt'][:80]}")
    print(f"\nUnknowns ({len(answer['unknowns'])}):")
    for u in answer["unknowns"][:5]:
        print(f"  - {u}")
    print(f"\nContradictions: {len(answer['contradictions'])}")
    print(f"Overall confidence: {answer['confidence']['overall']:.2f}")
    print("\nLimitations:")
    for lim in answer["limitations"][:5]:
        print(f"  - {lim}")


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session)

        # Query 1: capability_explanation
        a1 = await answer_query(
            session,
            "What can synapse do?",
            candidate_entity_ids=["ent-synapse"],
            context="AI agent systems arxiv papers",
            limit=10,
        )
        print_answer("QUERY 1: Capability Explanation", a1)

        # Query 2: dependency_analysis
        a2 = await answer_query(
            session,
            "What does synapse require?",
            candidate_entity_ids=["ent-synapse"],
            limit=10,
        )
        print_answer("QUERY 2: Dependency Analysis", a2)

        # Query 3: documented_alternatives
        a3 = await answer_query(
            session,
            "What are the alternatives to synapse?",
            candidate_entity_ids=["ent-synapse"],
            limit=10,
        )
        print_answer("QUERY 3: Documented Alternatives", a3)

        # Query 4: gap_explanation
        a4 = await answer_query(
            session,
            "What capabilities are missing or uncertain?",
            candidate_entity_ids=["ent-synapse"],
            context="AI agent systems",
            limit=10,
        )
        print_answer("QUERY 4: Gap Explanation", a4)

        # Citation chain example
        print(f"\n{'=' * 78}")
        print("  CITATION CHAIN EXAMPLE (from Query 1)")
        print(f"{'=' * 78}")
        if a1["evidence_chain"]:
            link = a1["evidence_chain"][0]
            print(f"\n  Finding: {a1['findings'][0]['text'] if a1['findings'] else '(none)'}")
            print("\n  Citation chain:")
            print(f"    Result → Claim({a1['cited_claims'][0] if a1['cited_claims'] else 'n/a'})")
            print(f"           → EvidenceFragment({link['fragment_id']})")
            print(f"           → SourceSpan({len(link.get('spans', []))} spans)")
            for span in link.get("spans", [])[:2]:
                print(
                    f"             [{span['start_offset']}:{span['end_offset']}] {span['excerpt']}"
                )
            print(f"           → Source URI: {link['source_uri']}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
