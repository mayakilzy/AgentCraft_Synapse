"""Realistic demonstration of G04-T01 hybrid retrieval.

Builds the same fixture used in the test suite, runs the realistic
mission-briefing query, and prints the structured retrieval result with
the full citation chain for at least one evidence-backed claim.
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

from synapse.application.relationship_service import create_relationship  # noqa: E402
from synapse.application.retrieval import hybrid_retrieve  # noqa: E402
from synapse.application.verification import assess_claim  # noqa: E402
from synapse.storage import models  # noqa: E402, F401  (register tables)
from synapse.storage.base import Base  # noqa: E402
from synapse.storage.models import (  # noqa: E402
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    SourceRow,
    SourceSpanRow,
)

NOW_ISO = datetime.now(UTC).isoformat()
RECENT_ISO = (datetime.now(UTC) - timedelta(days=10)).isoformat()
STALE_ISO = (datetime.now(UTC) - timedelta(days=500)).isoformat()


async def seed(session) -> None:
    # Sources
    src_a = SourceRow(
        id="src-a",
        canonical_uri="https://example.com/papers/hybrid-retrieval",
        source_type="paper",
        status="extracted",
    )
    src_b = SourceRow(
        id="src-b",
        canonical_uri="https://example.com/papers/agent-systems",
        source_type="paper",
        status="extracted",
    )
    src_c = SourceRow(
        id="src-c",
        canonical_uri="https://example.com/papers/no-vector-db",
        source_type="paper",
        status="extracted",
    )
    session.add_all([src_a, src_b, src_c])
    await session.flush()

    # Fragments
    frags = [
        EvidenceFragmentRow(
            id="frag-a",
            acquisition_id="acq-frag-a",
            source_id=src_a.id,
            source_uri=src_a.canonical_uri,
            exact_excerpt=(
                "Hybrid retrieval combines lexical, structured, and graph-assisted search "
                "to improve retrieval quality in AI agent systems. The approach requires "
                "an existing RelationshipService for graph traversal."
            ),
            excerpt_hash="hash-frag-a",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-a",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-b",
            acquisition_id="acq-frag-b",
            source_id=src_b.id,
            source_uri=src_b.canonical_uri,
            exact_excerpt=(
                "Evidence-grounded retrieval provides answers with citation chains, "
                "unlike keyword-only search which lacks provenance."
            ),
            excerpt_hash="hash-frag-b",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-b",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-c",
            acquisition_id="acq-frag-c",
            source_id=src_c.id,
            source_uri=src_c.canonical_uri,
            exact_excerpt=(
                "The approach is constrained by the absence of a vector database; "
                "semantic similarity is not supported in the minimal slice."
            ),
            excerpt_hash="hash-frag-c",
            retrieved_at=STALE_ISO,
            extraction_method="demo",
            content_fingerprint="fp-c",
            toolkit_commit_sha="demo",
        ),
        EvidenceFragmentRow(
            id="frag-opp",
            acquisition_id="acq-frag-opp",
            source_id=src_b.id,
            source_uri=src_b.canonical_uri,
            exact_excerpt="Vector embeddings are necessary for high-quality retrieval in agent systems.",
            excerpt_hash="hash-frag-opp",
            retrieved_at=RECENT_ISO,
            extraction_method="demo",
            content_fingerprint="fp-opp",
            toolkit_commit_sha="demo",
        ),
    ]
    session.add_all(frags)
    await session.flush()

    # Entities
    ents = [
        EntityRow(
            id="ent-hybrid",
            kind="technique",
            canonical_name="hybrid retrieval",
            aliases="[]",
            attributes="{}",
            description="Lexical+structured+graph retrieval",
            version=1,
        ),
        EntityRow(
            id="ent-synapse",
            kind="tool",
            canonical_name="synapse retriever",
            aliases="[]",
            attributes="{}",
            version=1,
        ),
        EntityRow(
            id="ent-evidence-grounded",
            kind="capability",
            canonical_name="evidence-grounded answers",
            aliases="[]",
            attributes="{}",
            version=1,
        ),
        EntityRow(
            id="ent-no-vector",
            kind="constraint",
            canonical_name="no vector database",
            aliases="[]",
            attributes="{}",
            version=1,
        ),
        EntityRow(
            id="ent-rs",
            kind="technology",
            canonical_name="RelationshipService",
            aliases="[]",
            attributes="{}",
            version=1,
        ),
    ]
    session.add_all(ents)
    await session.flush()

    # Claims
    claims = [
        ClaimRow(
            id="claim-main",
            proposition="Hybrid retrieval improves retrieval quality in AI agent systems.",
            subject_ref="ent-hybrid",
            evidence_refs='["frag-a"]',
            epistemic_state="supported",
            validity_conditions='["AI agent systems"]',
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-dep",
            proposition="Hybrid retrieval requires RelationshipService for graph traversal.",
            subject_ref="ent-hybrid",
            object_ref="ent-rs",
            evidence_refs='["frag-a"]',
            epistemic_state="supported",
            validity_conditions='["graph traversal"]',
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-constraint",
            proposition="The minimal slice is constrained by the absence of a vector database.",
            subject_ref="ent-hybrid",
            object_ref="ent-no-vector",
            evidence_refs='["frag-c"]',
            epistemic_state="supported",
            validity_conditions='["minimal slice"]',
            extraction_method="demo",
            version=1,
        ),
        ClaimRow(
            id="claim-contradicted",
            proposition="Vector embeddings are necessary for high-quality retrieval in agent systems.",
            subject_ref="ent-no-vector",
            evidence_refs='["frag-opp"]',
            contradicting_refs='["frag-c"]',
            epistemic_state="disputed",
            validity_conditions='["agent systems"]',
            extraction_method="demo",
            version=1,
        ),
    ]
    session.add_all(claims)
    await session.flush()

    # Spans
    spans = [
        SourceSpanRow(
            id="span-main",
            evidence_fragment_id="frag-a",
            claim_id="claim-main",
            start_offset=0,
            end_offset=66,
            excerpt="Hybrid retrieval combines lexical, structured, and graph-assisted search",
            context_before="",
            context_after="",
        ),
        SourceSpanRow(
            id="span-dep",
            evidence_fragment_id="frag-a",
            claim_id="claim-dep",
            start_offset=160,
            end_offset=231,
            excerpt="The approach requires an existing RelationshipService for graph traversal.",
            context_before="",
            context_after="",
        ),
        SourceSpanRow(
            id="span-constraint",
            evidence_fragment_id="frag-c",
            claim_id="claim-constraint",
            start_offset=0,
            end_offset=70,
            excerpt="The approach is constrained by the absence of a vector database",
            context_before="",
            context_after="",
        ),
    ]
    session.add_all(spans)

    # Relationships
    for args in [
        {
            "from_entity_id": "ent-synapse",
            "to_entity_id": "ent-hybrid",
            "predicate": "PROVIDES",
            "evidence_refs": ["frag-a"],
            "origin": "explicit",
        },
        {
            "from_entity_id": "ent-hybrid",
            "to_entity_id": "ent-rs",
            "predicate": "REQUIRES",
            "evidence_refs": ["frag-a"],
            "origin": "explicit",
        },
        {
            "from_entity_id": "ent-hybrid",
            "to_entity_id": "ent-evidence-grounded",
            "predicate": "ENABLES",
            "evidence_refs": ["frag-b"],
            "origin": "explicit",
        },
        {
            "from_entity_id": "ent-no-vector",
            "to_entity_id": "ent-hybrid",
            "predicate": "LIMITS",
            "evidence_refs": ["frag-c"],
            "origin": "explicit",
        },
        {
            "from_entity_id": "ent-no-vector",
            "to_entity_id": "ent-hybrid",
            "predicate": "CONTRADICTS",
            "evidence_refs": ["frag-opp"],
            "origin": "explicit",
        },
    ]:
        r = await create_relationship(session, **args)
        assert r["ok"] is True, args
    await session.commit()

    for cid in ("claim-main", "claim-dep", "claim-constraint", "claim-contradicted"):
        await assess_claim(session, cid, requester="demo")
    await session.commit()


def truncate(obj, max_str: int = 200, depth_limit: int = 6):
    """Recursively truncate long strings in a nested structure."""
    if depth_limit <= 0:
        return "..."
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + "..."
    if isinstance(obj, list):
        return [truncate(x, max_str, depth_limit - 1) for x in obj[:5]]
    if isinstance(obj, dict):
        return {k: truncate(v, max_str, depth_limit - 1) for k, v in list(obj.items())[:15]}
    return obj


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session)

        query = (
            "What techniques improve retrieval quality in AI agent systems, "
            "and what constraints or dependencies are documented?"
        )
        result = await hybrid_retrieve(session, query, max_depth=3, limit=20)

        print("=" * 78)
        print("G04-T01 — HYBRID RETRIEVAL DEMONSTRATION")
        print("=" * 78)
        print(f"\nQuery:\n  {query}\n")
        print(f"Query intent: {result['query_intent']}")
        print(f"Policy version: {result['policy_version']}")
        print(f"Limits: {json.dumps(result['limits'], indent=2)}")
        print(f"\nUnknowns: {result['unknowns']}")

        print("\n--- Reranking factors summary ---")
        print(json.dumps(truncate(result["reranking_factors"]), indent=2))

        print(f"\n--- Entities retrieved ({len(result['entities'])}) ---")
        for e in result["entities"][:5]:
            print(f"  • [{e['kind']}] {e['canonical_name']} (id={e['id']})")

        print(f"\n--- Relationships retrieved ({len(result['relationships'])}) ---")
        for r in result["relationships"][:5]:
            rel = r["relationship"]
            rf = r["reranking_factors"]
            print(f"  • [{rel['predicate']}] {rel['from_entity_id']} -> {rel['to_entity_id']}")
            print(
                f"      path_length={rf.get('path_length')} "
                f"weighted_score={rf['weighted_score']:.3f} "
                f"verification={rf.get('verification_outcome')}"
            )

        print(f"\n--- Claims retrieved ({len(result['claims'])}) ---")
        for c in result["claims"][:5]:
            cl = c["claim"]
            rf = c["reranking_factors"]
            print(f"\n  • Claim {cl['id']}: {cl['proposition']}")
            print(
                f"      epistemic_state={cl['epistemic_state']}  "
                f"verification_outcome={rf['verification_outcome']}"
            )
            print(f"      weighted_score={rf['weighted_score']:.3f}")
            print(
                f"      factors: text={rf['text_relevance']:.2f} "
                f"meta={rf['metadata_relevance']:.2f} "
                f"evidence={rf['evidence_traceability']:.2f} "
                f"verify={rf['verification_score']:.2f} "
                f"appl={rf['applicability_match']:.2f} "
                f"fresh={rf['freshness_score']:.2f} "
                f"prox={rf['proximity_score']:.2f}"
            )
            print(f"      evidence_bundles: {len(c['evidence_bundle'])}")
            for b in c["evidence_bundle"][:2]:
                print(f"        - fragment_id={b['fragment_id']}")
                print(f"          source_uri={b['source_uri']}")
                print(f"          retrieved_at={b['retrieved_at']}")
                print(f"          spans: {len(b['spans'])}")
                for s in b["spans"][:2]:
                    print(
                        f"            • [{s['start_offset']}:{s['end_offset']}] {s['excerpt'][:80]}"
                    )
            print("      citation_chain:")
            for entry in c["citation_chain"][:2]:
                print(
                    f"        - claim_id={entry['claim_id']} -> fragment_id={entry['fragment_id']} "
                    f"-> spans={len(entry['spans'])} -> source_uri={entry['source_uri']}"
                )

        print("\n" + "=" * 78)
        print("CITATION CHAIN EXAMPLE (full traversal)")
        print("=" * 78)
        # Pick the first claim with at least one bundle.
        for c in result["claims"]:
            if c["evidence_bundle"]:
                cl = c["claim"]
                b = c["evidence_bundle"][0]
                chain = c["citation_chain"][0] if c["citation_chain"] else None
                print("\n  Answer-level result:")
                print(f"    claim_id: {cl['id']}")
                print(f"    proposition: {cl['proposition']}")
                print(f"    epistemic_state: {cl['epistemic_state']}")
                print(f"    verification_outcome: {c['reranking_factors']['verification_outcome']}")
                print("\n  Evidence layer:")
                print(f"    fragment_id: {b['fragment_id']}")
                print(f"    source_uri: {b['source_uri']}")
                print(f"    retrieved_at: {b['retrieved_at']}")
                print(f"    extraction_method: {b['extraction_method']}")
                print(f"    content_fingerprint: {b['content_fingerprint']}")
                print("\n  Source span layer:")
                for s in b["spans"]:
                    print(f"    span_id: {s['id']}")
                    print(f"      claim_id: {s['claim_id']}")
                    print(f"      fragment_id: {s['evidence_fragment_id']}")
                    print(f"      offsets: [{s['start_offset']}, {s['end_offset']})")
                    print(f"      excerpt: {s['excerpt']}")
                if chain:
                    print("\n  Traversal summary:")
                    print(
                        f"    Result -> Claim({chain['claim_id']}) "
                        f"-> EvidenceFragment({chain['fragment_id']}) "
                        f"-> SourceSpan({len(chain['spans'])} spans) "
                        f"-> Original Source URI: {chain['source_uri']}"
                    )
                break

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
