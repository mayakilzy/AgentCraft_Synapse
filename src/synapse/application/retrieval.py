"""G04-T01 -- Hybrid Retrieval: lexical + structured + graph + evidence-aware reranking.

Per the user's G04-T01 authorization (Synapse G04 Session Handover §4 and
``reports/G04_MINIMAL_IMPLEMENTATION_PLAN.md`` §9), this module is the
compact evidence-grounded retrieval layer for the existing G01-G03 knowledge
infrastructure. It does NOT introduce a vector database, a graph database,
or any LLM provider.

Architecture
------------

``hybrid_retrieve()`` is the single entry point. It:

1. **Lexical search** -- SQL ``ILIKE`` on ``EntityRow.canonical_name``,
   ``ClaimRow.proposition`` and ``EvidenceFragmentRow.exact_excerpt``.
   This is the baseline textual-relevance signal.
2. **Structured retrieval** -- filters by ``entity_kind``,
   ``predicate``, and ``epistemic_state``. Metadata matches are an
   independent ranking signal (``metadata_relevance``).
3. **Graph-assisted retrieval** -- bounded BFS via the existing
   ``RelationshipService.find_related_entities`` to discover related
   knowledge (capabilities, dependencies, alternatives, limitations)
   within ``max_depth`` hops. Each graph-derived result is tagged with
   its path length so graph proximity can be used as a (small) ranking
   factor -- but NEVER outweighing direct textual relevance.
4. **Evidence-aware reranking** -- for each retrieved claim/relationship,
   fetch the evidence fragments + source spans + the most recent
   ``VerificationAssessment`` and compute a deterministic score.

Ranking formula (documented)
-----------------------------

::

    score = w_text   * text_relevance        # 0.0-1.0, ILIKE match quality
          + w_meta   * metadata_relevance    # 0.0 or 1.0, structured filter match
          + w_evid   * evidence_traceability  # 0.0-1.0, evidence chain completeness
          + w_verify * verification_score     # 0.0-1.0, outcome mapping (see below)
          + w_appl   * applicability_match    # 0.0 or 1.0, validity_conditions overlap
          + w_fresh  * freshness_score        # 0.0-1.0, recency of retrieved_at
          + w_prox   * proximity_score        # 0.0-1.0, 1.0/(1+path_length)

    w_text   = 0.30   (DOMINANT -- direct textual relevance)
    w_evid   = 0.20   (DOMINANT -- evidence chain completeness)
    w_verify = 0.20   (DOMINANT -- verification outcome)
    w_meta   = 0.15
    w_appl   = 0.05
    w_fresh  = 0.05
    w_prox   = 0.05

The DOMINANT block (text + evidence + verify = 0.70) intentionally outweighs
graph proximity (max 0.05) by 14x. This enforces the requirement:

    "Graph popularity or number of relationships must never outweigh
     clearly stronger direct relevance and evidence."

Verification outcome → numeric score (deterministic-v2 mapping)::

    VERIFIED                   → 1.00  (UNREACHABLE in deterministic-v2)
    CORROBORATED               → 0.90
    SOURCE_SUPPORTED           → 0.70
    STALE_OR_CONTEXT_MISMATCH  → 0.40
    INSUFFICIENT_EVIDENCE      → 0.30
    CONTESTED                  → 0.20  (preserved, NOT suppressed)
    NOT_EVIDENCED              → 0.00

VERIFIED is intentionally listed for completeness but is unreachable in
deterministic-v2 (PRB-05). CONTESTED is intentionally NOT zero -- the policy
requires that contradictory evidence remains visible, not buried.

Citation-chain integrity
------------------------

Every evidence-backed result carries a fully traceable chain:

    Result → Claim/Relationship → EvidenceFragment → SourceSpan → source_uri

If a chain is incomplete (e.g., the fragment is missing or the span is
missing), the ``evidence_traceability`` score is reduced and the bundle's
``incomplete_chain`` flag is set. The system never fabricates a fragment
or span to paper over the gap.

Epistemic safety
----------------

The retrieval layer preserves all G01-G03 epistemic distinctions:
SOURCE_SUPPORTED, CORROBORATED, CONTESTED, HYPOTHESIZED, STALE_OR_CONTEXT_MISMATCH,
INSUFFICIENT_EVIDENCE, NOT_EVIDENCED. It does NOT auto-promote anything to
VERIFIED. It does NOT suppress CONTESTED. Multiple URLs do NOT imply
independent origins -- the existing ``assess_claim()`` policy is the sole
arbiter of independence.

Query limits
------------

The function enforces:

- ``MAX_QUERY_CHARS = 512`` for the input query
- ``DEFAULT_LIMIT = 20`` for returned claims/relationships
- ``MAX_LIMIT = 100`` hard ceiling
- ``DEFAULT_MAX_DEPTH = 3`` graph traversal depth
- ``MAX_DEPTH = 5`` hard ceiling
- ``MAX_GRAPH_EXPANSION = 50`` total related entities per seed

Repeated queries are deterministic: same input → same scores, same ordering,
stable tie-breaking by ``id`` (UUID hex, lexically comparable).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.relationship_service import (
    find_related_entities,
)
from synapse.application.verification import (
    POLICY_VERSION,
    STALENESS_THRESHOLD_DAYS,
    VerificationOutcome,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
    SourceSpanRow,
)

_log = get_logger("synapse.application.retrieval")


# ── Hard limits ────────────────────────────────────────────────────────────

MAX_QUERY_CHARS = 512
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_MAX_DEPTH = 3
MAX_DEPTH = 5
MAX_GRAPH_EXPANSION = 50


# ── Ranking weights (documented above) ─────────────────────────────────────

W_TEXT = 0.30
W_META = 0.15
W_EVIDENCE = 0.20
W_VERIFY = 0.20
W_APPLICABILITY = 0.05
W_FRESHNESS = 0.05
W_PROXIMITY = 0.05

# ── Verification outcome → numeric score ────────────────────────────────────
# VERIFIED is unreachable in deterministic-v2 (PRB-05). CONTESTED is intentionally
# non-zero so contradictions remain visible in the ranking, not buried.
VERIFICATION_SCORE: dict[str, float] = {
    VerificationOutcome.VERIFIED: 1.00,
    VerificationOutcome.CORROBORATED: 0.90,
    VerificationOutcome.SOURCE_SUPPORTED: 0.70,
    VerificationOutcome.STALE_OR_CONTEXT_MISMATCH: 0.40,
    VerificationOutcome.INSUFFICIENT_EVIDENCE: 0.30,
    VerificationOutcome.CONTESTED: 0.20,
    VerificationOutcome.NOT_EVIDENCED: 0.00,
}

# Stale threshold in days (mirrors verification.py for self-containment).
_STALE_DAYS = STALENESS_THRESHOLD_DAYS


# ── Query intent ────────────────────────────────────────────────────────────


class QueryIntent(StrEnum):
    """Minimal deterministic query-intent classifier.

    Each intent is a small bag of trigger keywords. The classifier picks
    the first intent whose keyword set intersects the query. If no intent
    matches, the query is treated as ``GENERAL_KNOWLEDGE`` and falls back
    to general retrieval (lexical + structured + graph). This is the safe
    default per §7.3 of the user's mission briefing.

    The classifier is intentionally NOT an NLP framework -- it is a
    deterministic keyword matcher over the existing G01 predicate
    vocabulary plus a few high-signal English query words.
    """

    CAPABILITY_LOOKUP = "capability_lookup"
    DEPENDENCY_QUERY = "dependency_query"
    ALTERNATIVES = "alternatives"
    CONSTRAINTS_LIMITATIONS = "constraints_limitations"
    GENERAL_KNOWLEDGE = "general_knowledge"


_INTENT_KEYWORDS: dict[QueryIntent, tuple[str, ...]] = {
    QueryIntent.CAPABILITY_LOOKUP: (
        "capability",
        "capabilities",
        "provides",
        "enables",
        "what can",
        "what does",
        "offers",
    ),
    QueryIntent.DEPENDENCY_QUERY: (
        "requires",
        "depends",
        "dependency",
        "dependencies",
        "needs",
        "prerequisite",
    ),
    QueryIntent.ALTERNATIVES: (
        "alternative",
        "alternatives",
        "replaces",
        "instead of",
        "substitute",
        "swap",
    ),
    QueryIntent.CONSTRAINTS_LIMITATIONS: (
        "constraint",
        "constraints",
        "limitation",
        "limitations",
        "limits",
        "restrict",
        "restriction",
    ),
}


def classify_intent(query: str) -> QueryIntent:
    """Classify the query intent deterministically.

    Picks the first intent (in declaration order) whose keyword set
    intersects the lowercased query. If no intent matches, returns
    ``GENERAL_KNOWLEDGE`` (the safe fallback).
    """
    q = (query or "").lower()
    if not q.strip():
        return QueryIntent.GENERAL_KNOWLEDGE
    for intent, kws in _INTENT_KEYWORDS.items():
        for kw in kws:
            if kw in q:
                return intent
    return QueryIntent.GENERAL_KNOWLEDGE


# ── Result data classes (Pydantic v2) ────────────────────────────────────────


class SpanInfo(dict):
    """A single source span (subclass of dict for JSON-friendly output).

    Keys: id, claim_id, evidence_fragment_id, start_offset, end_offset,
    excerpt, context_before, context_after.
    """


class EvidenceBundle(dict):
    """Evidence bundle for a claim or relationship.

    Keys:
        fragment_id, source_uri, exact_excerpt (truncated),
        retrieved_at, extraction_method, content_fingerprint,
        spans (list[SpanInfo]), incomplete_chain (bool).
    """


class RerankingFactors(dict):
    """Per-result ranking breakdown.

    Keys: text_relevance, metadata_relevance, evidence_traceability,
    verification_score, applicability_match, freshness_score,
    proximity_score, weighted_score, verification_outcome.
    """


class ClaimWithEvidence(dict):
    """A claim plus its evidence bundle plus ranking factors.

    Keys: claim (dict), evidence_bundle (list[EvidenceBundle]),
    reranking_factors (RerankingFactors).
    """


class RelationshipWithEvidence(dict):
    """A relationship plus its evidence bundle plus ranking factors."""


class EntitySummary(dict):
    """A minimal entity summary for retrieval results."""


class RetrievalResult(dict):
    """The top-level result of ``hybrid_retrieve``.

    Keys:
        claims: list[ClaimWithEvidence]
        relationships: list[RelationshipWithEvidence]
        entities: list[EntitySummary]
        reranking_factors: dict[str, Any] (summary of weights + policy)
        unknowns: list[str]
        query_intent: str
        query: str
        limits: dict[str, int]
        request_id: str
    """


# ── Helpers ─────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_json_loads(raw: str | None) -> list[str]:
    """Parse a JSON-encoded list[str]; return [] on any error."""
    if not raw:
        return []
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        out = json.loads(raw)
        if isinstance(out, list):
            return [str(x) for x in out]
    return []


def _entity_summary(row: EntityRow) -> EntitySummary:
    aliases: list[str] = _safe_json_loads(row.aliases) if row.aliases else []
    attributes: dict[str, Any] = {}
    if row.attributes:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(row.attributes)
            if isinstance(parsed, dict):
                attributes = parsed
    return EntitySummary(
        id=row.id,
        kind=row.kind,
        canonical_name=row.canonical_name,
        canonical_uri=row.canonical_uri,
        aliases=aliases,
        attributes=attributes,
        description=row.description,
        version=row.version,
    )


def _claim_to_dict(row: ClaimRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "proposition": row.proposition,
        "subject_ref": row.subject_ref,
        "object_ref": row.object_ref,
        "evidence_refs": _safe_json_loads(row.evidence_refs),
        "epistemic_state": row.epistemic_state,
        "confidence_value": row.confidence_value,
        "confidence_method": row.confidence_method,
        "validity_conditions": _safe_json_loads(row.validity_conditions),
        "contradicting_refs": _safe_json_loads(row.contradicting_refs),
        "superseded_by": row.superseded_by,
        "extraction_method": row.extraction_method,
        "version": row.version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _relationship_to_dict(row: RelationshipRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "from_entity_id": row.from_entity_id,
        "to_entity_id": row.to_entity_id,
        "predicate": row.predicate,
        "direction": row.direction,
        "origin": row.origin,
        "verification_state": row.verification_state,
        "evidence_refs": _safe_json_loads(row.evidence_refs),
        "confidence_value": row.confidence_value,
        "confidence_method": row.confidence_method,
        "conditions": _safe_json_loads(row.conditions),
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "superseded_by": row.superseded_by,
        "derivation_chain": _safe_json_loads(row.derivation_chain),
        "version": row.version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── Evidence bundle construction ────────────────────────────────────────────


async def _fetch_evidence_fragments(
    session: AsyncSession,
    evidence_refs: list[str],
) -> list[EvidenceFragmentRow]:
    if not evidence_refs:
        return []
    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(evidence_refs))
    return list((await session.execute(stmt)).scalars().all())


async def _fetch_spans_for_claim(
    session: AsyncSession,
    claim_id: str,
) -> list[SourceSpanRow]:
    stmt = select(SourceSpanRow).where(SourceSpanRow.claim_id == claim_id)
    return list((await session.execute(stmt)).scalars().all())


async def _fetch_spans_for_fragments(
    session: AsyncSession,
    fragment_ids: list[str],
) -> list[SourceSpanRow]:
    if not fragment_ids:
        return []
    stmt = select(SourceSpanRow).where(SourceSpanRow.evidence_fragment_id.in_(fragment_ids))
    return list((await session.execute(stmt)).scalars().all())


def _fragment_to_bundle(
    frag: EvidenceFragmentRow,
    spans: list[SourceSpanRow],
) -> EvidenceBundle:
    """Build an EvidenceBundle for one fragment + its spans.

    A chain is "incomplete" if the fragment has no exact_excerpt AND no
    excerpt_hash, OR if there are zero spans attached. The system never
    fabricates spans -- it just lowers the evidence_traceability score.
    """
    fragment_spans = [s for s in spans if s.evidence_fragment_id == frag.id]
    has_text = bool(frag.exact_excerpt) or bool(frag.excerpt_hash)
    incomplete = (not has_text) or (len(fragment_spans) == 0)
    return EvidenceBundle(
        fragment_id=frag.id,
        source_uri=frag.source_uri,
        source_id=frag.source_id,
        exact_excerpt=(frag.exact_excerpt or "")[:500],
        excerpt_hash=frag.excerpt_hash,
        retrieved_at=frag.retrieved_at,
        extraction_method=frag.extraction_method,
        content_fingerprint=frag.content_fingerprint,
        spans=[
            SpanInfo(
                id=s.id,
                claim_id=s.claim_id,
                evidence_fragment_id=s.evidence_fragment_id,
                start_offset=s.start_offset,
                end_offset=s.end_offset,
                excerpt=s.excerpt[:300],
                context_before=(s.context_before or "")[:200],
                context_after=(s.context_after or "")[:200],
            )
            for s in fragment_spans
        ],
        incomplete_chain=incomplete,
    )


# ── Verification assessment lookup ──────────────────────────────────────────


async def _get_latest_assessment_for_claim(
    session: AsyncSession,
    claim_id: str,
) -> dict[str, Any] | None:
    """Fetch the most recent verification.assessed audit event for a claim.

    The verification engine (G03-T04) records assessments as AuditEventRow
    entries with event_type="verification.assessed" and target_id=claim_id.
    We pick the newest one by created_at.
    """
    stmt = (
        select(AuditEventRow)
        .where(
            AuditEventRow.target_id == claim_id,
            AuditEventRow.target_type == "claim",
            AuditEventRow.event_type == "verification.assessed",
        )
        .order_by(AuditEventRow.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return row.payload


# ── Ranking factor computation ───────────────────────────────────────────────


def _text_relevance(query: str, candidate_text: str | None) -> float:
    """Direct textual relevance -- keyword-overlap based.

    Splits the query into keywords (after stopword removal), then computes
    the fraction of query keywords that appear in ``candidate_text``. An
    exact full-query match returns 1.0; a substring match of the full query
    returns 0.6+; otherwise the score is ``0.6 * (matched_keywords / total_keywords)``.

    Returns 0.0 for no keyword overlap, 1.0 for an exact match.
    Deterministic, no fuzzy matching, no embeddings.
    """
    if not candidate_text:
        return 0.0
    q = (query or "").lower().strip()
    t = candidate_text.lower()
    if not q:
        return 0.0
    if q == t:
        return 1.0
    if q in t:
        # Full query is a substring of the candidate -- high relevance.
        return max(0.6, min(1.0, 0.6 + 0.4 * (len(q) / max(len(t), 1))))
    # Otherwise, score by keyword overlap.
    keywords = _query_keywords(query)
    if not keywords:
        return 0.0
    matched = sum(1 for kw in keywords if kw in t)
    if matched == 0:
        return 0.0
    overlap = matched / len(keywords)
    # Cap at 0.6 -- full substring match is the only way to get higher.
    return min(0.6, 0.6 * overlap)


def _metadata_relevance(
    *,
    entity_kind_filter: str | None,
    predicate_filter: str | None,
    epistemic_state_filter: str | None,
    row_kind: str | None,
    row_predicate: str | None,
    row_epistemic_state: str | None,
) -> float:
    """1.0 if the row matches ALL provided structured filters, else 0.0.

    A row passes the metadata filter only if every non-None filter matches
    its corresponding row attribute. If no filters are provided, returns
    0.5 (neutral -- metadata was not asked for, so it neither helps nor hurts).
    """
    filters = [
        (entity_kind_filter, row_kind),
        (predicate_filter, row_predicate),
        (epistemic_state_filter, row_epistemic_state),
    ]
    active = [(f, v) for f, v in filters if f is not None]
    if not active:
        return 0.5
    return 1.0 if all(f == v for f, v in active) else 0.0


def _evidence_traceability(
    bundles: list[EvidenceBundle],
    evidence_ref_count: int,
) -> float:
    """Evidence chain completeness.

    Returns 0.0 if there are no evidence_refs.
    Returns the fraction of evidence_refs that produced a complete bundle
    (fragment found + at least one span attached).
    """
    if evidence_ref_count == 0:
        return 0.0
    complete = sum(1 for b in bundles if not b.get("incomplete_chain", True))
    # If the fragment was found but has no spans, that's still partial evidence.
    found = len(bundles)
    if found == 0:
        return 0.0
    # Weight: half credit for finding the fragment, full credit for a complete chain.
    return 0.5 * (found / evidence_ref_count) + 0.5 * (complete / evidence_ref_count)


def _verification_score(outcome: str | None) -> float:
    """Map a verification outcome to a numeric score.

    Unknown / None outcomes map to INSUFFICIENT_EVIDENCE (0.30) -- the
    conservative default per the deterministic-v2 policy.
    """
    if outcome is None:
        return VERIFICATION_SCORE[VerificationOutcome.INSUFFICIENT_EVIDENCE]
    return VERIFICATION_SCORE.get(
        outcome, VERIFICATION_SCORE[VerificationOutcome.INSUFFICIENT_EVIDENCE]
    )


def _freshness_score(retrieved_at: str | None) -> float:
    """Recency of the evidence fragment's retrieval timestamp.

    Returns 1.0 if retrieved within 30 days, decaying linearly to 0.0 at
    the staleness threshold (365 days), then 0.0 thereafter. Missing
    timestamp returns 0.5 (neutral -- we cannot prove freshness OR staleness).
    """
    if not retrieved_at:
        return 0.5
    try:
        retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.5
    now = datetime.now(UTC)
    if retrieved > now:
        # Future timestamps are suspect -- treat as neutral.
        return 0.5
    age_days = (now - retrieved).days
    if age_days <= 30:
        return 1.0
    if age_days >= _STALE_DAYS:
        return 0.0
    return 1.0 - (age_days - 30) / (_STALE_DAYS - 30)


def _proximity_score(path_length: int | None) -> float:
    """Graph proximity score -- shorter path = higher score.

    Returns 1.0 for direct (path_length=1) matches, decaying as 1/(1+path).
    Non-graph-derived results (path_length=None) get 0.5 -- they are not
    graph-proximate but they are not graph-distant either.
    """
    if path_length is None:
        return 0.5
    if path_length <= 0:
        return 0.5
    return 1.0 / (1.0 + path_length)


def _applicability_match(
    query: str,
    validity_conditions: list[str],
) -> float:
    """Whether the claim's validity_conditions overlap the query.

    Returns 1.0 if any validity_condition keyword appears in the query,
    0.0 otherwise. Intentionally conservative: validity_conditions are
    a small list, and we only mark a match when there is a clear overlap.
    """
    if not validity_conditions:
        return 0.5  # neutral -- no conditions to check against
    q = (query or "").lower()
    if not q:
        return 0.0
    for cond in validity_conditions:
        if cond and cond.lower() in q:
            return 1.0
    return 0.0


def _weighted_score(factors: RerankingFactors) -> float:
    return (
        W_TEXT * factors["text_relevance"]
        + W_META * factors["metadata_relevance"]
        + W_EVIDENCE * factors["evidence_traceability"]
        + W_VERIFY * factors["verification_score"]
        + W_APPLICABILITY * factors["applicability_match"]
        + W_FRESHNESS * factors["freshness_score"]
        + W_PROXIMITY * factors["proximity_score"]
    )


# ── Lexical search ───────────────────────────────────────────────────────────


def _query_keywords(query: str) -> list[str]:
    """Split a query into individual keyword tokens for OR-style matching.

    Drops stopwords (the, a, an, of, in, what, is, are, and, or, to, for,
    with, on, by, be, this, that, these, those) and tokens shorter than 3
    chars. Returns lowercased keywords in the order they appeared.

    This is intentionally simple -- no stemming, no NLP. It just lets
    multi-word queries match any of their significant words.
    """
    stopwords = {
        "the",
        "a",
        "an",
        "of",
        "in",
        "what",
        "is",
        "are",
        "and",
        "or",
        "to",
        "for",
        "with",
        "on",
        "by",
        "be",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "how",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "as",
        "at",
        "from",
        "into",
        "it",
        "its",
        "has",
        "have",
        "had",
        "was",
        "were",
        "will",
        "would",
        "should",
        "could",
        "can",
        "may",
        "might",
        "must",
    }
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in query.lower().split():
        tok = tok.strip(".,;:!?\"'()[]{}-")
        if not tok or len(tok) < 3:
            continue
        if tok in stopwords:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return tokens


def _ilike_pattern(query: str) -> str:
    """Build an SQL ILIKE pattern from the FULL query (whitespace-normalized).

    Used for exact phrase matching. For multi-keyword OR matching, use
    ``_query_keywords`` and build separate patterns per keyword.
    """
    cleaned = " ".join(query.split())
    return f"%{cleaned}%"


async def _lexical_entity_search(
    session: AsyncSession,
    query: str,
    *,
    entity_kind: str | None = None,
    limit: int,
) -> list[EntityRow]:
    """Lexical search over entities by canonical_name (and aliases).

    Matches if ANY keyword from the query appears in canonical_name OR aliases.
    If no significant keywords remain (after stopword removal), falls back
    to the whole-query phrase pattern.
    """
    keywords = _query_keywords(query)
    if not keywords:
        # No keywords -- fall back to phrase match.
        keywords = [query.strip().lower()]

    rows: list[EntityRow] = []
    seen_ids: set[str] = set()
    for kw in keywords:
        pattern = f"%{kw}%"
        stmt = select(EntityRow).where(EntityRow.canonical_name.ilike(pattern))
        if entity_kind:
            stmt = stmt.where(EntityRow.kind == entity_kind)
        stmt = stmt.limit(limit)
        for r in (await session.execute(stmt)).scalars().all():
            if r.id not in seen_ids:
                rows.append(r)
                seen_ids.add(r.id)

    # Also match aliases (stored as JSON array -- Python-side filter).
    if query:
        ql_tokens = set(keywords)
        all_stmt = select(EntityRow)
        if entity_kind:
            all_stmt = all_stmt.where(EntityRow.kind == entity_kind)
        all_rows = list((await session.execute(all_stmt)).scalars().all())
        for r in all_rows:
            if r.id in seen_ids:
                continue
            aliases = _safe_json_loads(r.aliases)
            alias_text = " ".join(aliases).lower()
            if any(kw in alias_text for kw in ql_tokens):
                rows.append(r)
                seen_ids.add(r.id)
                if len(rows) >= limit:
                    break
    return rows[:limit]


async def _lexical_claim_search(
    session: AsyncSession,
    query: str,
    *,
    epistemic_state: str | None = None,
    limit: int,
) -> list[ClaimRow]:
    """Lexical search over claims by proposition text.

    Matches if ANY keyword from the query appears in the proposition.
    """
    keywords = _query_keywords(query)
    if not keywords:
        keywords = [query.strip().lower()]

    rows: list[ClaimRow] = []
    seen_ids: set[str] = set()
    for kw in keywords:
        pattern = f"%{kw}%"
        stmt = select(ClaimRow).where(ClaimRow.proposition.ilike(pattern))
        if epistemic_state:
            stmt = stmt.where(ClaimRow.epistemic_state == epistemic_state)
        stmt = stmt.limit(limit)
        for r in (await session.execute(stmt)).scalars().all():
            if r.id not in seen_ids:
                rows.append(r)
                seen_ids.add(r.id)
    return rows[:limit]


async def _lexical_evidence_search(
    session: AsyncSession,
    query: str,
    *,
    limit: int,
) -> list[EvidenceFragmentRow]:
    """Lexical search over evidence fragments by exact_excerpt.

    Matches if ANY keyword from the query appears in the excerpt.
    """
    keywords = _query_keywords(query)
    if not keywords:
        keywords = [query.strip().lower()]

    rows: list[EvidenceFragmentRow] = []
    seen_ids: set[str] = set()
    for kw in keywords:
        pattern = f"%{kw}%"
        stmt = (
            select(EvidenceFragmentRow)
            .where(EvidenceFragmentRow.exact_excerpt.ilike(pattern))
            .limit(limit)
        )
        for r in (await session.execute(stmt)).scalars().all():
            if r.id not in seen_ids:
                rows.append(r)
                seen_ids.add(r.id)
    return rows[:limit]


# ── Graph-assisted retrieval ────────────────────────────────────────────────


async def _graph_expand(
    session: AsyncSession,
    seed_entity_ids: list[str],
    *,
    predicate: str | None = None,
    max_depth: int,
    max_expansion: int,
) -> dict[str, list[dict[str, Any]]]:
    """Bounded BFS over the relationships table.

    Returns a dict mapping entity_id → list of (related_entity_summary,
    relationship_dict, path_length). The path_length is the BFS depth at
    which the related entity was discovered. We never exceed max_depth,
    and we cap the total expansion at ``max_expansion`` to prevent
    uncontrolled graph growth.

    The traversal uses the existing ``RelationshipService.find_related_entities``
    for each hop -- no recursive CTE, no graph database, works on SQLite.
    """
    if not seed_entity_ids or max_depth < 1:
        return {}

    discovered: dict[str, list[dict[str, Any]]] = {sid: [] for sid in seed_entity_ids}
    visited: set[str] = set(seed_entity_ids)
    frontier: list[tuple[str, int]] = [(sid, 0) for sid in seed_entity_ids]
    total_expansion = 0

    while frontier and total_expansion < max_expansion:
        next_frontier: list[tuple[str, int]] = []
        for current_id, depth in frontier:
            if depth >= max_depth:
                continue
            if total_expansion >= max_expansion:
                break
            related = await find_related_entities(
                session,
                current_id,
                predicate=predicate,
                direction="both",
                limit=max_expansion - total_expansion,
            )
            for r in related:
                rel = r.get("relationship") or {}
                ent = r.get("entity") or {}
                ent_id = ent.get("id")
                if not ent_id or ent_id in visited:
                    continue
                visited.add(ent_id)
                total_expansion += 1
                entry = {
                    "entity": ent,
                    "relationship": rel,
                    "path_length": depth + 1,
                    "via_seed": current_id,
                }
                discovered.setdefault(current_id, []).append(entry)
                # Also attribute the discovery to all seeds that share this entity
                # indirectly -- but for ranking purposes, the via_seed is what
                # we use to compute proximity.
                next_frontier.append((ent_id, depth + 1))
                if total_expansion >= max_expansion:
                    break
        frontier = next_frontier

    return discovered


# ── Main entry point ─────────────────────────────────────────────────────────


async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    *,
    entity_kind: str | None = None,
    predicate: str | None = None,
    epistemic_state: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> RetrievalResult:
    """Hybrid retrieval: lexical + structured + graph + evidence-aware reranking.

    Args:
        session: AsyncSession bound to the Synapse database.
        query: Natural-language query string (max 512 chars).
        entity_kind: Optional EntityType value to filter entities/claims by.
        predicate: Optional relationship predicate to filter graph expansion.
        epistemic_state: Optional Claim epistemic_state to filter claims by.
        max_depth: Maximum BFS depth for graph expansion (1-5, default 3).
        limit: Maximum number of claims/relationships to return (1-100, default 20).
        requester: Optional actor name for audit logging.

    Returns:
        A ``RetrievalResult`` dict with claims, relationships, entities,
        reranking_factors summary, unknowns, query_intent, and limits.

    The function is deterministic: the same input on the same DB state
    always produces the same scores and the same ordering. Ties are broken
    by ``id`` (UUID hex, lexically comparable) for stable pagination.
    """
    request_id = uuid4().hex

    # ── Validate inputs ──────────────────────────────────────────────────
    if not query or not query.strip():
        return RetrievalResult(
            claims=[],
            relationships=[],
            entities=[],
            reranking_factors=_summary_factors(),
            unknowns=["empty_query"],
            query_intent=QueryIntent.GENERAL_KNOWLEDGE,
            query=query or "",
            limits=_limits_dict(max_depth, limit),
            request_id=request_id,
        )
    if len(query) > MAX_QUERY_CHARS:
        return RetrievalResult(
            claims=[],
            relationships=[],
            entities=[],
            reranking_factors=_summary_factors(),
            unknowns=[f"query_too_long:{len(query)}>{MAX_QUERY_CHARS}"],
            query_intent=QueryIntent.GENERAL_KNOWLEDGE,
            query=query[:MAX_QUERY_CHARS],
            limits=_limits_dict(max_depth, limit),
            request_id=request_id,
        )

    max_depth = max(1, min(MAX_DEPTH, max_depth))
    limit = max(1, min(MAX_LIMIT, limit))

    intent = classify_intent(query)
    _log.info(
        "hybrid_retrieve query=%r intent=%s kind=%s predicate=%s epistemic=%s depth=%d limit=%d",
        query[:80],
        intent,
        entity_kind,
        predicate,
        epistemic_state,
        max_depth,
        limit,
    )

    unknowns: list[str] = []

    # ── Lexical + structured search ──────────────────────────────────────
    entity_rows = await _lexical_entity_search(session, query, entity_kind=entity_kind, limit=limit)
    claim_rows = await _lexical_claim_search(
        session,
        query,
        epistemic_state=epistemic_state,
        limit=limit,
    )
    # We do not directly return evidence fragments as top-level results --
    # they are bundled into claims. But we DO use evidence-fragment lexical
    # matches to surface additional claims whose evidence mentions the query.
    evidence_rows = await _lexical_evidence_search(session, query, limit=limit)

    # If evidence fragments matched lexically, find the claims that cite them.
    extra_claim_ids: set[str] = set()
    if evidence_rows:
        ef_ids = [e.id for e in evidence_rows]
        # Claims whose evidence_refs include any matched fragment.
        # SQLite-safe: load all claims and filter in Python.
        all_claims_stmt = select(ClaimRow)
        if epistemic_state:
            all_claims_stmt = all_claims_stmt.where(ClaimRow.epistemic_state == epistemic_state)
        all_claims = list((await session.execute(all_claims_stmt)).scalars().all())
        ef_id_set = set(ef_ids)
        for c in all_claims:
            refs = set(_safe_json_loads(c.evidence_refs))
            if refs & ef_id_set and c.id not in {x.id for x in claim_rows}:
                extra_claim_ids.add(c.id)
                claim_rows.append(c)

    # ── Graph expansion ──────────────────────────────────────────────────
    seed_entity_ids = [e.id for e in entity_rows]
    graph = await _graph_expand(
        session,
        seed_entity_ids,
        predicate=predicate,
        max_depth=max_depth,
        max_expansion=MAX_GRAPH_EXPANSION,
    )

    # Collect graph-derived relationship rows (with path_length info).
    graph_relationships: list[tuple[RelationshipRow, int, str]] = []
    graph_entity_summaries: list[EntitySummary] = []
    seen_rel_ids: set[str] = set()
    seen_graph_entity_ids: set[str] = set()
    for seed_id, entries in graph.items():
        for entry in entries:
            rel_dict = entry.get("relationship") or {}
            rel_id = rel_dict.get("id")
            ent_dict = entry.get("entity") or {}
            ent_id = ent_dict.get("id")
            path_len = entry.get("path_length", 1)

            # Load the actual RelationshipRow for full data.
            if rel_id and rel_id not in seen_rel_ids:
                rel_row = (
                    await session.execute(
                        select(RelationshipRow).where(RelationshipRow.id == rel_id)
                    )
                ).scalar_one_or_none()
                if rel_row is not None:
                    graph_relationships.append((rel_row, path_len, seed_id))
                    seen_rel_ids.add(rel_id)

            # Load the actual EntityRow for full data.
            if ent_id and ent_id not in seen_graph_entity_ids:
                ent_row = (
                    await session.execute(select(EntityRow).where(EntityRow.id == ent_id))
                ).scalar_one_or_none()
                if ent_row is not None:
                    graph_entity_summaries.append(_entity_summary(ent_row))
                    seen_graph_entity_ids.add(ent_id)

    # ── Also include direct relationships of the seed entities (depth 1) ──
    # so that direct PROVIDES/REQUIRES edges appear even if the lexical
    # search did not surface them through evidence fragment matches.
    direct_rels: list[tuple[RelationshipRow, int, str]] = []
    for ent in entity_rows:
        rels_out = await find_related_entities(
            session, ent.id, predicate=predicate, direction="both", limit=limit
        )
        for r in rels_out:
            rel_dict = r.get("relationship") or {}
            rel_id = rel_dict.get("id")
            if rel_id and rel_id not in seen_rel_ids:
                rel_row = (
                    await session.execute(
                        select(RelationshipRow).where(RelationshipRow.id == rel_id)
                    )
                ).scalar_one_or_none()
                if rel_row is not None:
                    direct_rels.append((rel_row, 1, ent.id))
                    seen_rel_ids.add(rel_id)

    all_relationships = graph_relationships + direct_rels

    # ── Build claim results with evidence + ranking ─────────────────────
    claim_results: list[ClaimWithEvidence] = []
    for c in claim_rows:
        evidence_refs = _safe_json_loads(c.evidence_refs)
        fragments = await _fetch_evidence_fragments(session, evidence_refs)
        spans = await _fetch_spans_for_fragments(session, [f.id for f in fragments])
        # Attach claim-specific spans too (some spans may have claim_id set).
        claim_spans = await _fetch_spans_for_claim(session, c.id)
        all_spans = spans + [s for s in claim_spans if s not in spans]
        bundles = [_fragment_to_bundle(f, all_spans) for f in fragments]

        assessment = await _get_latest_assessment_for_claim(session, c.id)
        outcome = (assessment or {}).get("outcome")

        text_rel = max(
            _text_relevance(query, c.proposition),
            max(
                (_text_relevance(query, b.get("exact_excerpt", "")) for b in bundles),
                default=0.0,
            ),
        )
        meta_rel = _metadata_relevance(
            entity_kind_filter=None,  # claim has no kind; check via subject entity later
            predicate_filter=None,
            epistemic_state_filter=epistemic_state,
            row_kind=None,
            row_predicate=None,
            row_epistemic_state=c.epistemic_state,
        )
        evi_rel = _evidence_traceability(bundles, len(evidence_refs))
        ver_score = _verification_score(outcome)
        validity = _safe_json_loads(c.validity_conditions)
        appl_match = _applicability_match(query, validity)
        retrieved_ats = [b.get("retrieved_at") for b in bundles if b.get("retrieved_at")]
        fresh = max((_freshness_score(r) for r in retrieved_ats), default=0.5)
        prox = _proximity_score(None)  # claims are not graph-derived here

        factors = RerankingFactors(
            text_relevance=text_rel,
            metadata_relevance=meta_rel,
            evidence_traceability=evi_rel,
            verification_score=ver_score,
            applicability_match=appl_match,
            freshness_score=fresh,
            proximity_score=prox,
            verification_outcome=outcome or VerificationOutcome.INSUFFICIENT_EVIDENCE,
        )
        factors["weighted_score"] = _weighted_score(factors)

        claim_results.append(
            ClaimWithEvidence(
                claim=_claim_to_dict(c),
                evidence_bundle=bundles,
                reranking_factors=factors,
                citation_chain=[
                    {
                        "claim_id": c.id,
                        "fragment_id": b.get("fragment_id"),
                        "spans": b.get("spans", []),
                        "source_uri": b.get("source_uri"),
                    }
                    for b in bundles
                ],
            )
        )

    # ── Build relationship results with evidence + ranking ─────────────
    rel_results: list[RelationshipWithEvidence] = []
    for rel, path_len, via_seed in all_relationships:
        evidence_refs = _safe_json_loads(rel.evidence_refs)
        fragments = await _fetch_evidence_fragments(session, evidence_refs)
        spans = await _fetch_spans_for_fragments(session, [f.id for f in fragments])
        bundles = [_fragment_to_bundle(f, spans) for f in fragments]

        # Load the related entities for the relationship's text matching.
        from_ent = (
            await session.execute(select(EntityRow).where(EntityRow.id == rel.from_entity_id))
        ).scalar_one_or_none()
        to_ent = (
            await session.execute(select(EntityRow).where(EntityRow.id == rel.to_entity_id))
        ).scalar_one_or_none()
        candidate_text = " ".join(
            [
                rel.predicate,
                from_ent.canonical_name if from_ent else "",
                to_ent.canonical_name if to_ent else "",
            ]
        )

        text_rel = max(
            _text_relevance(query, candidate_text),
            max(
                (_text_relevance(query, b.get("exact_excerpt", "")) for b in bundles),
                default=0.0,
            ),
        )
        meta_rel = _metadata_relevance(
            entity_kind_filter=entity_kind,
            predicate_filter=predicate,
            epistemic_state_filter=None,
            row_kind=(to_ent.kind if to_ent else None),
            row_predicate=rel.predicate,
            row_epistemic_state=None,
        )
        evi_rel = _evidence_traceability(bundles, len(evidence_refs))
        # Relationships don't have assess_claim() applied directly; we use
        # their verification_state as a coarse signal. The mapping below
        # is intentionally conservative -- relationships only get a high
        # verification_score if they are explicitly verified, which per
        # deterministic-v2 only happens for explicit+evidence-backed ones.
        ver_score = _relationship_verification_score(rel.verification_state, rel.origin)
        appl_match = _applicability_match(query, _safe_json_loads(rel.conditions))
        retrieved_ats = [b.get("retrieved_at") for b in bundles if b.get("retrieved_at")]
        fresh = max((_freshness_score(r) for r in retrieved_ats), default=0.5)
        prox = _proximity_score(path_len)

        factors = RerankingFactors(
            text_relevance=text_rel,
            metadata_relevance=meta_rel,
            evidence_traceability=evi_rel,
            verification_score=ver_score,
            applicability_match=appl_match,
            freshness_score=fresh,
            proximity_score=prox,
            verification_outcome=f"relationship.verification_state={rel.verification_state}",
            path_length=path_len,
            via_seed=via_seed,
        )
        factors["weighted_score"] = _weighted_score(factors)

        rel_results.append(
            RelationshipWithEvidence(
                relationship=_relationship_to_dict(rel),
                from_entity=_entity_summary(from_ent) if from_ent else None,
                to_entity=_entity_summary(to_ent) if to_ent else None,
                evidence_bundle=bundles,
                reranking_factors=factors,
                citation_chain=[
                    {
                        "relationship_id": rel.id,
                        "fragment_id": b.get("fragment_id"),
                        "spans": b.get("spans", []),
                        "source_uri": b.get("source_uri"),
                    }
                    for b in bundles
                ],
            )
        )

    # ── Deterministic ordering + limit ─────────────────────────────────
    # Sort by weighted_score desc, then by id asc (stable tie-break).
    claim_results.sort(key=lambda r: (-r["reranking_factors"]["weighted_score"], r["claim"]["id"]))
    rel_results.sort(
        key=lambda r: (-r["reranking_factors"]["weighted_score"], r["relationship"]["id"])
    )

    claim_results = claim_results[:limit]
    rel_results = rel_results[:limit]

    # ── Entity summaries ─────────────────────────────────────────────────
    entity_summaries = [_entity_summary(e) for e in entity_rows]
    # Include graph-derived entities that aren't already in the seed list.
    seed_ids = {e.id for e in entity_rows}
    for ge in graph_entity_summaries:
        if ge["id"] not in seed_ids:
            entity_summaries.append(ge)
            seed_ids.add(ge["id"])
    entity_summaries = entity_summaries[:limit]

    # ── Unknowns ────────────────────────────────────────────────────────
    if not claim_results and not rel_results and not entity_summaries:
        unknowns.append("no_evidence_found_for_query")
        unknowns.append("absence_of_evidence_is_not_evidence_of_absence")
    else:
        if not claim_results:
            unknowns.append("no_claims_matched_query")
        if not rel_results:
            unknowns.append("no_relationships_matched_query")
        if not entity_summaries:
            unknowns.append("no_entities_matched_query")

    return RetrievalResult(
        claims=claim_results,
        relationships=rel_results,
        entities=entity_summaries,
        reranking_factors=_summary_factors(),
        unknowns=unknowns,
        query_intent=intent,
        query=query,
        limits=_limits_dict(max_depth, limit),
        request_id=request_id,
        requester=requester,
        policy_version=POLICY_VERSION,
        assessed_at=_utcnow_iso(),
    )


# ── Internal helpers (rank summary, limits, relationship verification) ─────


def _relationship_verification_score(verification_state: str, origin: str) -> float:
    """Coarse verification score for a relationship.

    Relationships don't have assess_claim() applied directly (the G03-T04
    engine operates on claims). We use the relationship's verification_state
    as a coarse signal, with the following conservative mapping:

        verified (explicit only -- invariant §1) → 0.90
        strong                                  → 0.60
        weak                                    → 0.40
        unverified (explicit)                   → 0.30  (has potential, not yet reviewed)
        unverified (derived/hypothesized)       → 0.20  (lower -- never auto-promoted)
        contradicted                            → 0.20  (preserved, NOT suppressed)
        rejected                                → 0.00
    """
    state = verification_state or "unverified"
    origin = origin or "explicit"
    if state == "verified":
        # Per G01 invariant §2: derived/hypothesized relationships are never
        # auto-promoted to verified. If we see verified+non-explicit, it must
        # have been set by an explicit review action. Treat as 0.90 (slightly
        # below CORROBORATED since we have no assess_claim trail for it).
        return 0.90
    if state == "strong":
        return 0.60
    if state == "weak":
        return 0.40
    if state == "contradicted":
        return 0.20
    if state == "rejected":
        return 0.00
    # unverified
    if origin in ("derived", "hypothesized"):
        return 0.20
    return 0.30


def _summary_factors() -> dict[str, Any]:
    """Static summary of the ranking weights + policy for transparency."""
    return {
        "weights": {
            "text": W_TEXT,
            "metadata": W_META,
            "evidence": W_EVIDENCE,
            "verification": W_VERIFY,
            "applicability": W_APPLICABILITY,
            "freshness": W_FRESHNESS,
            "proximity": W_PROXIMITY,
        },
        "dominant_block_sum": W_TEXT + W_EVIDENCE + W_VERIFY,
        "graph_popularity_max_weight": W_PROXIMITY,
        "policy_version": POLICY_VERSION,
        "stale_threshold_days": _STALE_DAYS,
        "verification_score_map": dict(VERIFICATION_SCORE),
        "note": (
            "Graph popularity (proximity, max weight="
            f"{W_PROXIMITY}) cannot outweigh the dominant block "
            f"(text+evidence+verification="
            f"{W_TEXT + W_EVIDENCE + W_VERIFY}). "
            "Contradictions are preserved, not suppressed."
        ),
    }


def _limits_dict(max_depth: int, limit: int) -> dict[str, int]:
    return {
        "max_query_chars": MAX_QUERY_CHARS,
        "max_depth": max_depth,
        "limit": limit,
        "max_graph_expansion": MAX_GRAPH_EXPANSION,
        "hard_max_depth": MAX_DEPTH,
        "hard_max_limit": MAX_LIMIT,
    }
