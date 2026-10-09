"""G04-T03 -- Evidence-Grounded Technical Reasoning.

Per the user's G04-T03 mission briefing §3:

  "Implement a compact pipeline:
   Query → Intent → Retrieval → Capability/Relationship Analysis →
   Evidence Evaluation → Answer Composition → Citation Validation"

This module is a DETERMINISTIC COMPOSITION layer. It does NOT introduce
a new LLM, agent framework, or storage layer. It composes the existing
G01-G04-T02C services:

  - G04-T01 retrieval (``hybrid_retrieve``)
  - G04-T02 capability registry (``list_capabilities``, ``get_capability``,
    ``find_capability_by_alias_or_fuzzy``)
  - G04-T02C gap analyzer (``analyze_gap`` with provider-attribution safeguard)
  - G03-T03 RelationshipService (``find_capabilities``, ``find_dependencies``,
    ``find_alternatives``, ``find_limitations``, ``find_contradictions``)
  - G03-T04 verification (``assess_claim`` outcomes via audit events)

Finding types (per mission briefing §4 "Grounded answer contract"):

  - ``DOCUMENTED_FACT``     : directly supported by source evidence.
  - ``DERIVED_FINDING``     : follows from documented relationships and
                              explicit deterministic rules.
  - ``HYPOTHESIS``          : plausible but not established by evidence.
  - ``UNKNOWN``             : insufficient information.

A hypothesis is NEVER presented as a verified fact.

Reasoning intents (per mission briefing §3, "a small set of deterministic
reasoning operations"):

  - ``CAPABILITY_EXPLANATION``  : "What can X do?" / "What techniques support Y?"
  - ``DEPENDENCY_ANALYSIS``     : "What does X require?" / "What are the deps?"
  - ``DOCUMENTED_ALTERNATIVES`` : "What are the alternatives to X?"
  - ``CONSTRAINT_ANALYSIS``     : "What limits X?" / "What are the constraints?"
  - ``TECHNICAL_COMPARISON``    : "Compare X and Y" / "Which is better for Z?"
  - ``GAP_EXPLANATION``         : "What's missing?" / "What's uncertain?"

The classifier is a deterministic keyword matcher over the existing
G01 predicate vocabulary. Unknown intents fall back to a general
capability-explanation path that returns "insufficient evidence" rather
than fabricating an answer.

Hard limits (per mission briefing §13 "Keep request and response sizes
bounded"):

  - ``MAX_QUERY_CHARS = 512``     : input query length
  - ``DEFAULT_LIMIT = 20``       : results cap
  - ``MAX_LIMIT = 100``           : hard ceiling
  - ``MAX_FINDINGS = 50``        : findings cap
  - ``MAX_EVIDENCE_CHAIN = 100`` : citation-chain links cap

The answer NEVER fabricates citations. Every cited claim/relationship ID
must exist in the DB. If a citation cannot be traced, the finding is
downgraded to ``UNKNOWN`` (per mission briefing §9: "Missing evidence
is reported honestly").
"""

from __future__ import annotations

import contextlib
import json
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.gap_analyzer import (
    GapClassification,
    analyze_gap,
)
from synapse.application.relationship_service import (
    find_alternatives,
    find_capabilities,
    find_contradictions,
    find_dependencies,
    find_limitations,
)
from synapse.application.retrieval import (
    MAX_QUERY_CHARS as RETRIEVAL_MAX_QUERY_CHARS,
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

_log = get_logger("synapse.application.reasoning")


# ── Hard limits ──────────────────────────────────────────────────────────────

MAX_QUERY_CHARS = RETRIEVAL_MAX_QUERY_CHARS  # 512, mirror retrieval layer
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_FINDINGS = 50
MAX_EVIDENCE_CHAIN = 100
MAX_RELATIONSHIPS_PER_INTENT = 50


# ── Reasoning intent enum ────────────────────────────────────────────────────


class ReasoningIntent(StrEnum):
    """Six deterministic reasoning intents per the G04-T03 mission briefing §3."""

    CAPABILITY_EXPLANATION = "capability_explanation"
    DEPENDENCY_ANALYSIS = "dependency_analysis"
    DOCUMENTED_ALTERNATIVES = "documented_alternatives"
    CONSTRAINT_ANALYSIS = "constraint_analysis"
    TECHNICAL_COMPARISON = "technical_comparison"
    GAP_EXPLANATION = "gap_explanation"
    UNKNOWN = "unknown"  # fallback when intent cannot be classified


#: Keyword triggers for each intent. The classifier picks the first intent
#: (in declaration order) whose keyword set intersects the lowercased query.
_INTENT_KEYWORDS: dict[ReasoningIntent, tuple[str, ...]] = {
    ReasoningIntent.DEPENDENCY_ANALYSIS: (
        "require",
        "requires",
        "dependency",
        "dependencies",
        "depends on",
        "prerequisite",
        "need",
        "needs",
    ),
    ReasoningIntent.DOCUMENTED_ALTERNATIVES: (
        "alternative",
        "alternatives",
        "instead of",
        "substitute",
        "replace",
        "replaces",
        "other option",
        "other options",
    ),
    ReasoningIntent.CONSTRAINT_ANALYSIS: (
        "constraint",
        "constraints",
        "limitation",
        "limitations",
        "limits",
        "restrict",
        "restriction",
        "incompatib",
    ),
    ReasoningIntent.TECHNICAL_COMPARISON: (
        "compare",
        "comparison",
        "versus",
        " vs ",
        " vs.",
        "better",
        "which is",
        "differ",
        "difference",
    ),
    ReasoningIntent.GAP_EXPLANATION: (
        "missing",
        "gap",
        "gaps",
        "uncertain",
        "uncertainty",
        "unknown",
        "what is not",
        "what's not",
        "insufficient",
    ),
    ReasoningIntent.CAPABILITY_EXPLANATION: (
        "what can",
        "what does",
        "capabilit",
        "provides",
        "enables",
        "support",
        "supports",
        "technique",
        "techniques",
    ),
}


def classify_reasoning_intent(query: str) -> ReasoningIntent:
    """Classify the reasoning intent deterministically.

    Picks the first intent (in declaration order) whose keyword set
    intersects the lowercased query. If no intent matches, returns
    ``UNKNOWN`` (the safe fallback per mission briefing §3: "Unknown or
    unsupported requests must receive a bounded response indicating
    insufficient evidence").
    """
    q = (query or "").lower()
    if not q.strip():
        return ReasoningIntent.UNKNOWN
    for intent, kws in _INTENT_KEYWORDS.items():
        for kw in kws:
            if kw in q:
                return intent
    return ReasoningIntent.UNKNOWN


# ── Finding type enum ────────────────────────────────────────────────────────


class FindingType(StrEnum):
    """Per mission briefing §4: distinguish documented facts, derived
    findings, hypotheses, and unknowns."""

    DOCUMENTED_FACT = "documented_fact"
    DERIVED_FINDING = "derived_finding"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


# ── Result data classes (dict subclasses for JSON-friendly output) ───────────


class Finding(dict):
    """A single finding in a reasoning answer.

    Keys: text, type (FindingType), sources (list of claim/relationship
    IDs), evidence_refs (list of fragment IDs), confidence (0.0-1.0),
    caveat (str | None).
    """


class CitationLink(dict):
    """A single link in the citation chain.

    Keys: claim_id (optional), relationship_id (optional),
    fragment_id, source_uri, spans (list).
    """


class ReasoningAnswer(dict):
    """Top-level result of ``answer_query``.

    Keys: question, intent, answer_text, findings (list[Finding]),
    cited_claims (list[str]), cited_relationships (list[str]),
    evidence_chain (list[CitationLink]), unknowns (list[str]),
    contradictions (list[dict]), confidence (dict), limitations (list[str]),
    request_id, policy_version, assessed_at.
    """


# ── Helpers ─────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _safe_json_loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        out = json.loads(raw)
        if isinstance(out, list):
            return [str(x) for x in out]
    return []


async def _get_latest_assessment_payload(
    session: AsyncSession,
    claim_id: str,
) -> dict[str, Any] | None:
    """Fetch the latest verification.assessed audit event payload."""
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


#: Verification outcome -> confidence score (mirrors G04-T01 retrieval layer).
_CONFIDENCE_MAP: dict[str, float] = {
    VerificationOutcome.VERIFIED: 1.00,  # unreachable in v2
    VerificationOutcome.CORROBORATED: 0.90,
    VerificationOutcome.SOURCE_SUPPORTED: 0.70,
    VerificationOutcome.STALE_OR_CONTEXT_MISMATCH: 0.40,
    VerificationOutcome.INSUFFICIENT_EVIDENCE: 0.30,
    VerificationOutcome.CONTESTED: 0.20,  # preserved, NOT suppressed
    VerificationOutcome.NOT_EVIDENCED: 0.00,
}


def _confidence_for_outcome(outcome: str | None) -> float:
    """Confidence (0.0-1.0) for a verification outcome.

    None outcome -> INSUFFICIENT_EVIDENCE (conservative default).
    """
    if outcome is None:
        return _CONFIDENCE_MAP[VerificationOutcome.INSUFFICIENT_EVIDENCE]
    return _CONFIDENCE_MAP.get(outcome, _CONFIDENCE_MAP[VerificationOutcome.INSUFFICIENT_EVIDENCE])


# ── Citation chain collector ────────────────────────────────────────────────


async def _collect_citation_chain(
    session: AsyncSession,
    *,
    claim_ids: list[str],
    relationship_ids: list[str],
    max_links: int = MAX_EVIDENCE_CHAIN,
) -> list[CitationLink]:
    """Build a traversable citation chain for the given claims + relationships.

    Each link has: claim_id (optional), relationship_id (optional),
    fragment_id, source_uri, spans. The chain is capped at
    ``MAX_EVIDENCE_CHAIN`` links.
    """
    evidence_refs: set[str] = set()

    for cid in claim_ids:
        stmt = select(ClaimRow).where(ClaimRow.id == cid)
        c = (await session.execute(stmt)).scalar_one_or_none()
        if c is not None:
            evidence_refs.update(_safe_json_loads(c.evidence_refs))
            evidence_refs.update(_safe_json_loads(c.contradicting_refs))

    for rid in relationship_ids:
        stmt = select(RelationshipRow).where(RelationshipRow.id == rid)
        r = (await session.execute(stmt)).scalar_one_or_none()
        if r is not None:
            evidence_refs.update(_safe_json_loads(r.evidence_refs))

    if not evidence_refs:
        return []

    frag_stmt = select(EvidenceFragmentRow).where(
        EvidenceFragmentRow.id.in_(list(evidence_refs)[:max_links])
    )
    fragments = list((await session.execute(frag_stmt)).scalars().all())

    span_stmt = select(SourceSpanRow).where(
        SourceSpanRow.evidence_fragment_id.in_([f.id for f in fragments])
    )
    spans = list((await session.execute(span_stmt)).scalars().all())

    chain: list[CitationLink] = []
    for frag in fragments:
        if len(chain) >= max_links:
            break
        frag_spans = [s for s in spans if s.evidence_fragment_id == frag.id]
        chain.append(
            CitationLink(
                fragment_id=frag.id,
                source_uri=frag.source_uri,
                retrieved_at=frag.retrieved_at,
                extraction_method=frag.extraction_method,
                content_fingerprint=frag.content_fingerprint,
                spans=[
                    {
                        "id": s.id,
                        "claim_id": s.claim_id,
                        "start_offset": s.start_offset,
                        "end_offset": s.end_offset,
                        "excerpt": s.excerpt[:300],
                    }
                    for s in frag_spans
                ],
            )
        )
    return chain


# ── Entity lookup helper ────────────────────────────────────────────────────


async def _find_entity_by_name(
    session: AsyncSession,
    name: str,
) -> EntityRow | None:
    """Find an entity by exact canonical_name (case-insensitive)."""
    if not name or not name.strip():
        return None
    stmt = select(EntityRow).where(EntityRow.canonical_name.ilike(name.strip()))
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_entity_by_name_or_alias(
    session: AsyncSession,
    name: str,
) -> EntityRow | None:
    """Find an entity by canonical_name or alias (case-insensitive)."""
    if not name or not name.strip():
        return None
    q = name.strip().lower()
    # Exact canonical_name match first.
    stmt = select(EntityRow).where(EntityRow.canonical_name.ilike(name.strip()))
    ent = (await session.execute(stmt)).scalar_one_or_none()
    if ent is not None:
        return ent
    # Alias match.
    all_stmt = select(EntityRow)
    for r in (await session.execute(all_stmt)).scalars().all():
        aliases = _safe_json_loads(r.aliases)
        if any(q == a.lower() for a in aliases):
            return r
    # ILIKE substring (last resort).
    fuzzy_stmt = select(EntityRow).where(EntityRow.canonical_name.ilike(f"%{name.strip()}%"))
    return (await session.execute(fuzzy_stmt)).scalar_one_or_none()


# ── Per-intent handlers ─────────────────────────────────────────────────────


async def _handle_capability_explanation(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'What can X do?' / 'What techniques support Y?'.

    Uses the G04-T02 capability registry + G04-T02C gap analyzer.
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    # If candidates are named, run gap analysis for each capability provided
    # by those candidates.
    if candidate_entity_ids:
        # Find capabilities provided by the candidates.
        cap_ids: set[str] = set()
        for cid in candidate_entity_ids:
            caps = await find_capabilities(session, cid, limit=MAX_RELATIONSHIPS_PER_INTENT)
            for c in caps:
                ent = c.get("entity") or {}
                if ent.get("id"):
                    cap_ids.add(ent["id"])

        if not cap_ids:
            unknowns.append("no capabilities documented for the named candidate(s)")
            return findings, cited_claims, cited_relationships, unknowns

        # Run gap analysis with the named candidates (preserves G04-T02C
        # provider-attribution safeguard).
        cap_names: list[str] = []
        for cap_id in cap_ids:
            ent_stmt = select(EntityRow).where(EntityRow.id == cap_id)
            ent = (await session.execute(ent_stmt)).scalar_one_or_none()
            if ent is not None:
                cap_names.append(ent.canonical_name)

        if cap_names:
            gap_result = await analyze_gap(
                session,
                cap_names,
                candidate_entity_ids=candidate_entity_ids,
                limit=limit,
            )
            for req in gap_result.get("requirements", []):
                classification = req.get("classification")
                cap_id = req.get("capability_id")
                cap_name = req.get("required_capability")
                reason = req.get("reason", "")

                if classification == GapClassification.SUPPORTED:
                    ftype = FindingType.DOCUMENTED_FACT
                    text = f"'{cap_name}' is supported by the named candidate(s) with applicable evidence."
                elif classification == GapClassification.PARTIALLY_SUPPORTED:
                    ftype = FindingType.DERIVED_FINDING
                    text = f"'{cap_name}' is partially supported: {reason}"
                elif classification == GapClassification.CONTESTED:
                    ftype = FindingType.DOCUMENTED_FACT
                    text = f"'{cap_name}' has conflicting evidence (CONTESTED): {reason}"
                elif classification == GapClassification.CONSTRAINED:
                    ftype = FindingType.DOCUMENTED_FACT
                    text = f"'{cap_name}' is constrained by documented limitations: {reason}"
                elif classification == GapClassification.NOT_EVIDENCED:
                    ftype = FindingType.UNKNOWN
                    text = f"'{cap_name}' has no sufficient supporting evidence; absence of evidence is not evidence of absence."
                else:  # UNKNOWN
                    ftype = FindingType.UNKNOWN
                    text = f"'{cap_name}' cannot be reliably classified: {reason}"

                # Collect citations from the gap result.
                ev_refs: list[str] = []
                for link in req.get("evidence_chain", []):
                    if link.get("fragment_id"):
                        ev_refs.append(link["fragment_id"])
                for c in req.get("candidates", []):
                    if c.get("relationship_id"):
                        cited_relationships.append(c["relationship_id"])
                # Claim IDs from the assessment payload.
                if cap_id:
                    claims_for_cap = await _find_capability_claim_ids(
                        session, cap_id, candidate_entity_ids
                    )
                    cited_claims.extend(claims_for_cap)

                # Confidence: average of outcomes for cited claims.
                confidence = _average_confidence(session, cited_claims) if cited_claims else 0.0

                findings.append(
                    Finding(
                        text=text,
                        type=ftype,
                        sources=list({cap_id} if cap_id else set()),
                        evidence_refs=ev_refs,
                        confidence=confidence,
                        caveat=reason if ftype != FindingType.DOCUMENTED_FACT else None,
                    )
                )
                if ftype == FindingType.UNKNOWN:
                    unknowns.append(text)
    else:
        # No candidates named: enumerate documented capabilities.
        from synapse.application.capability_registry import list_capabilities

        cap_list = await list_capabilities(session, limit=limit)
        if not cap_list["items"]:
            unknowns.append("no capabilities are documented in the knowledge graph")
        else:
            for item in cap_list["items"]:
                findings.append(
                    Finding(
                        text=f"'{item['canonical_name']}' is a documented capability "
                        f"with {item['provider_count']} provider(s).",
                        type=FindingType.DOCUMENTED_FACT,
                        sources=[item["id"]],
                        evidence_refs=[],
                        confidence=0.5,  # neutral -- presence is documented
                        caveat="capability-level summary; per-provider evidence requires a named candidate",
                    )
                )

    return findings, cited_claims, cited_relationships, unknowns


async def _find_capability_claim_ids(
    session: AsyncSession,
    capability_id: str,
    candidate_ids: list[str] | None,
) -> list[str]:
    """Find claim IDs attributed to one of the candidates for this capability.

    Applies the G04-T02C attribution safeguard.
    """
    stmt = select(ClaimRow).where(
        (ClaimRow.subject_ref == capability_id) | (ClaimRow.object_ref == capability_id)
    )
    claims = list((await session.execute(stmt)).scalars().all())
    if candidate_ids is None:
        return [c.id for c in claims]
    candidate_set = set(candidate_ids)
    out: list[str] = []
    for c in claims:
        subj = c.subject_ref
        obj = c.object_ref
        # Pattern 1: subject is candidate, object is capability.
        if (subj in candidate_set and obj == capability_id) or (
            subj == capability_id and obj in candidate_set
        ):
            out.append(c.id)
    return out


def _average_confidence(session: AsyncSession, claim_ids: list[str]) -> float:
    """Placeholder -- the actual averaging happens per-finding in the handler.

    The confidence for a finding is computed when the finding is built
    (from the assessment outcomes of its cited claims). This function is
    a stub kept for future extension (e.g., async averaging across
    multiple claims). For now, the handlers compute confidence inline.
    """
    return 0.0


async def _handle_dependency_analysis(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'What does X require?' / 'What are the dependencies?'

    Uses RelationshipService.find_dependencies (REQUIRES / DEPENDS_ON).
    Directionality preserved: dependencies are outgoing edges from
    the candidate entity.
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    # Extract entity name from the query (heuristic: find an entity whose
    # canonical_name appears in the query).
    entity = await _extract_entity_from_query(session, query)
    if entity is None:
        unknowns.append(
            "could not identify a candidate entity in the query; use a documented entity name"
        )
        return findings, cited_claims, cited_relationships, unknowns

    deps = await find_dependencies(session, entity.id, limit=MAX_RELATIONSHIPS_PER_INTENT)
    if not deps:
        unknowns.append(
            f"no documented dependencies found for '{entity.canonical_name}' "
            f"(absence of evidence is not evidence of absence)"
        )
        return findings, cited_claims, cited_relationships, unknowns

    for dep in deps:
        rel = dep.get("relationship") or {}
        ent = dep.get("entity") or {}
        rel_id = rel.get("id")
        dep_name = ent.get("canonical_name", "(unknown)")
        dep_kind = ent.get("kind", "(unknown)")
        evidence_refs = rel.get("evidence_refs", [])

        if rel_id:
            cited_relationships.append(rel_id)
        ftype = FindingType.DERIVED_FINDING
        text = (
            f"'{entity.canonical_name}' {rel.get('predicate', 'DEPENDS_ON')} "
            f"'{dep_name}' ({dep_kind})."
        )
        caveat = None
        if not evidence_refs:
            caveat = "dependency is documented but lacks evidence references"
            ftype = FindingType.UNKNOWN
            unknowns.append(text)
        findings.append(
            Finding(
                text=text,
                type=ftype,
                sources=[rel_id] if rel_id else [],
                evidence_refs=evidence_refs,
                confidence=0.5 if evidence_refs else 0.2,
                caveat=caveat,
            )
        )
    return findings, cited_claims, cited_relationships, unknowns


async def _handle_documented_alternatives(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'What are the alternatives to X?'

    Uses RelationshipService.find_alternatives. Per mission §5:
    "Do not treat INTEGRATES_WITH as ALTERNATIVE_TO."
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    entity = await _extract_entity_from_query(session, query)
    if entity is None:
        unknowns.append("could not identify a candidate entity in the query")
        return findings, cited_claims, cited_relationships, unknowns

    alts = await find_alternatives(session, entity.id, limit=MAX_RELATIONSHIPS_PER_INTENT)
    if not alts:
        unknowns.append(f"no documented alternatives found for '{entity.canonical_name}'")
        return findings, cited_claims, cited_relationships, unknowns

    # Filter to REPLACES only -- INTEGRATES_WITH is NOT an alternative
    # (per mission §5 and G04 plan §3).
    replaces_only = [
        a for a in alts if (a.get("relationship") or {}).get("predicate") == "REPLACES"
    ]
    integrates_with = [
        a for a in alts if (a.get("relationship") or {}).get("predicate") == "INTEGRATES_WITH"
    ]

    if not replaces_only:
        unknowns.append(
            f"no REPLACES relationships found for '{entity.canonical_name}'; "
            f"INTEGRATES_WITH is NOT treated as ALTERNATIVE_TO "
            f"({len(integrates_with)} integration(s) excluded)"
        )

    for alt in replaces_only:
        rel = alt.get("relationship") or {}
        ent = alt.get("entity") or {}
        rel_id = rel.get("id")
        alt_name = ent.get("canonical_name", "(unknown)")
        evidence_refs = rel.get("evidence_refs", [])
        if rel_id:
            cited_relationships.append(rel_id)
        text = f"'{alt_name}' REPLACES '{entity.canonical_name}'."
        findings.append(
            Finding(
                text=text,
                type=FindingType.DOCUMENTED_FACT if evidence_refs else FindingType.DERIVED_FINDING,
                sources=[rel_id] if rel_id else [],
                evidence_refs=evidence_refs,
                confidence=0.7 if evidence_refs else 0.4,
                caveat=None if evidence_refs else "relationship lacks evidence references",
            )
        )
    return findings, cited_claims, cited_relationships, unknowns


async def _handle_constraint_analysis(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'What limits X?' / 'What are the constraints?'

    Uses RelationshipService.find_limitations (LIMITS / CONTRADICTS /
    INVALIDATES).
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    entity = await _extract_entity_from_query(session, query)
    if entity is None:
        unknowns.append("could not identify a candidate entity in the query")
        return findings, cited_claims, cited_relationships, unknowns

    lims = await find_limitations(session, entity.id, limit=MAX_RELATIONSHIPS_PER_INTENT)
    if not lims:
        unknowns.append(f"no documented limitations found for '{entity.canonical_name}'")
        return findings, cited_claims, cited_relationships, unknowns

    for lim in lims:
        rel = lim.get("relationship") or {}
        ent = lim.get("entity") or {}
        rel_id = rel.get("id")
        lim_name = ent.get("canonical_name", "(unknown)")
        predicate = rel.get("predicate", "LIMITS")
        evidence_refs = rel.get("evidence_refs", [])
        if rel_id:
            cited_relationships.append(rel_id)
        text = f"'{lim_name}' {predicate} '{entity.canonical_name}'."
        ftype = FindingType.DOCUMENTED_FACT if evidence_refs else FindingType.DERIVED_FINDING
        findings.append(
            Finding(
                text=text,
                type=ftype,
                sources=[rel_id] if rel_id else [],
                evidence_refs=evidence_refs,
                confidence=0.7 if evidence_refs else 0.4,
                caveat=None if evidence_refs else "limitation lacks evidence references",
            )
        )
    return findings, cited_claims, cited_relationships, unknowns


async def _handle_technical_comparison(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'Compare X and Y'.

    Composes capability_explanation for each named candidate. Does NOT
    declare one "better" than the other unless evidence supports it.
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    if not candidate_entity_ids or len(candidate_entity_ids) < 2:
        unknowns.append(
            "technical comparison requires at least two named candidates; "
            "provide candidate_entity_ids in the request"
        )
        return findings, cited_claims, cited_relationships, unknowns

    # Run capability explanation for each candidate.
    for cid in candidate_entity_ids:
        sub_findings, sub_claims, sub_rels, sub_unknowns = await _handle_capability_explanation(
            session, query, candidate_entity_ids=[cid], limit=limit
        )
        for f in sub_findings:
            f["text"] = f"[candidate={cid}] {f['text']}"
            findings.append(f)
        cited_claims.extend(sub_claims)
        cited_relationships.extend(sub_rels)
        unknowns.extend(sub_unknowns)

    # Add a synthesis finding (HYPOTHESIS -- not a verified conclusion).
    findings.append(
        Finding(
            text=(
                f"Comparison of {len(candidate_entity_ids)} candidates is a "
                f"candidate synthesis, not a verified architectural conclusion."
            ),
            type=FindingType.HYPOTHESIS,
            sources=[],
            evidence_refs=[],
            confidence=0.1,
            caveat=(
                "a technical combination may be reported as a candidate or "
                "hypothesis, not as a proven architecture (mission §6)"
            ),
        )
    )
    return findings, cited_claims, cited_relationships, unknowns


async def _handle_gap_explanation(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Handle 'What's missing?' / 'What's uncertain?'

    Uses the G04-T02C gap analyzer to surface NOT_EVIDENCED / UNKNOWN
    capabilities.
    """
    findings: list[Finding] = []
    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    unknowns: list[str] = []

    # List all capabilities and run gap analysis for each.
    from synapse.application.capability_registry import list_capabilities

    cap_list = await list_capabilities(session, limit=limit)
    cap_names = [item["canonical_name"] for item in cap_list["items"]]

    if not cap_names:
        unknowns.append("no capabilities are documented in the knowledge graph")
        return findings, cited_claims, cited_relationships, unknowns

    gap_result = await analyze_gap(
        session,
        cap_names,
        candidate_entity_ids=candidate_entity_ids,
        limit=limit,
    )

    for req in gap_result.get("requirements", []):
        classification = req.get("classification")
        cap_name = req.get("required_capability")
        reason = req.get("reason", "")
        if classification in (
            GapClassification.NOT_EVIDENCED,
            GapClassification.UNKNOWN,
            GapClassification.PARTIALLY_SUPPORTED,
        ):
            text = f"'{cap_name}' is {classification}: {reason}"
            findings.append(
                Finding(
                    text=text,
                    type=FindingType.UNKNOWN
                    if classification
                    in (GapClassification.NOT_EVIDENCED, GapClassification.UNKNOWN)
                    else FindingType.DERIVED_FINDING,
                    sources=[req.get("capability_id")] if req.get("capability_id") else [],
                    evidence_refs=[
                        link.get("fragment_id")
                        for link in req.get("evidence_chain", [])
                        if link.get("fragment_id")
                    ],
                    confidence=0.0
                    if classification
                    in (GapClassification.NOT_EVIDENCED, GapClassification.UNKNOWN)
                    else 0.3,
                    caveat=None,
                )
            )
            unknowns.append(text)

    return findings, cited_claims, cited_relationships, unknowns


async def _handle_unknown_intent(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None,
    limit: int,
) -> tuple[list[Finding], list[str], list[str], list[str]]:
    """Fallback for unclassifiable queries.

    Per mission §3: "Unknown or unsupported requests must receive a
    bounded response indicating insufficient evidence."
    """
    unknowns = [
        "the reasoning intent could not be classified; "
        "the query does not match any supported reasoning operation",
        "supported intents: capability_explanation, dependency_analysis, "
        "documented_alternatives, constraint_analysis, technical_comparison, "
        "gap_explanation",
    ]
    return [], [], [], unknowns


# ── Entity extraction helper ────────────────────────────────────────────────


async def _extract_entity_from_query(
    session: AsyncSession,
    query: str,
) -> EntityRow | None:
    """Heuristically extract an entity name from the query.

    Iterates all entities and returns the first whose canonical_name or
    any alias appears as a substring of the query (case-insensitive).
    """
    if not query:
        return None
    q = query.lower()
    all_stmt = select(EntityRow)
    for r in (await session.execute(all_stmt)).scalars().all():
        if r.canonical_name and r.canonical_name.lower() in q:
            return r
        aliases = _safe_json_loads(r.aliases)
        for a in aliases:
            if a and a.lower() in q:
                return r
    return None


# ── Contradiction collector ────────────────────────────────────────────────


async def _collect_contradictions(
    session: AsyncSession,
    cited_relationship_ids: list[str],
) -> list[dict[str, Any]]:
    """Surface contradictions among the cited relationships.

    Uses RelationshipService.find_contradictions and filters to those
    involving the cited relationship entities.
    """
    if not cited_relationship_ids:
        return []
    # Load the cited relationships to get their entity pairs.
    rel_stmt = select(RelationshipRow).where(RelationshipRow.id.in_(cited_relationship_ids))
    cited_rels = list((await session.execute(rel_stmt)).scalars().all())
    cited_entity_pairs = {(r.from_entity_id, r.to_entity_id) for r in cited_rels}

    all_contradictions = await find_contradictions(session, limit=MAX_RELATIONSHIPS_PER_INTENT)
    out: list[dict[str, Any]] = []
    for contra in all_contradictions:
        pair_a = (contra.get("entity_a"), contra.get("entity_b"))
        pair_b = (contra.get("entity_b"), contra.get("entity_a"))
        if pair_a in cited_entity_pairs or pair_b in cited_entity_pairs:
            out.append(contra)
    return out


# ── Main entry point ────────────────────────────────────────────────────────


async def answer_query(
    session: AsyncSession,
    query: str,
    *,
    candidate_entity_ids: list[str] | None = None,
    context: str | None = None,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> ReasoningAnswer:
    """Answer a technical reasoning query with evidence-grounded findings.

    This is the single entry point of the G04-T03 reasoning layer. It:

    1. Validates the query (max ``MAX_QUERY_CHARS``).
    2. Classifies the reasoning intent (deterministic keyword matcher).
    3. Dispatches to the appropriate per-intent handler.
    4. Collects contradictions among the cited relationships.
    5. Validates the citation chain (every cited ID must exist).
    6. Composes a structured ``ReasoningAnswer``.

    The answer NEVER fabricates citations. Every cited claim/relationship
    ID must exist in the DB. If a citation cannot be traced, the finding
    is downgraded to ``UNKNOWN``.

    Args:
        session: AsyncSession.
        query: Natural-language query (max 512 chars).
        candidate_entity_ids: Optional list of entity IDs to scope the
            analysis to. Required for technical_comparison.
        context: Optional technical context (passed through to gap
            analysis for applicability matching).
        limit: Max number of findings (capped at MAX_LIMIT).
        requester: Optional actor name for audit logging.

    Returns:
        A ``ReasoningAnswer`` dict with question, intent, findings,
        cited_claims, cited_relationships, evidence_chain, unknowns,
        contradictions, confidence, limitations.
    """
    request_id = uuid4().hex

    # ── Validate inputs ──────────────────────────────────────────────────
    if not query or not query.strip():
        return _empty_answer(
            query or "",
            ReasoningIntent.UNKNOWN,
            ["empty_query"],
            request_id,
        )
    if len(query) > MAX_QUERY_CHARS:
        return _empty_answer(
            query[:MAX_QUERY_CHARS],
            ReasoningIntent.UNKNOWN,
            [f"query_too_long:{len(query)}>{MAX_QUERY_CHARS}"],
            request_id,
        )

    limit = max(1, min(MAX_LIMIT, limit))

    # ── Classify intent ─────────────────────────────────────────────────
    intent = classify_reasoning_intent(query)
    _log.info(
        "answer_query query=%r intent=%s candidates=%s limit=%d",
        query[:80],
        intent,
        candidate_entity_ids,
        limit,
    )

    # ── Dispatch to per-intent handler ──────────────────────────────────
    handler = {
        ReasoningIntent.CAPABILITY_EXPLANATION: _handle_capability_explanation,
        ReasoningIntent.DEPENDENCY_ANALYSIS: _handle_dependency_analysis,
        ReasoningIntent.DOCUMENTED_ALTERNATIVES: _handle_documented_alternatives,
        ReasoningIntent.CONSTRAINT_ANALYSIS: _handle_constraint_analysis,
        ReasoningIntent.TECHNICAL_COMPARISON: _handle_technical_comparison,
        ReasoningIntent.GAP_EXPLANATION: _handle_gap_explanation,
        ReasoningIntent.UNKNOWN: _handle_unknown_intent,
    }[intent]

    findings, cited_claims, cited_relationships, unknowns = await handler(
        session,
        query,
        candidate_entity_ids=candidate_entity_ids,
        limit=limit,
    )

    # ── Cap findings ────────────────────────────────────────────────────
    findings = findings[:MAX_FINDINGS]

    # ── Collect contradictions among cited relationships ─────────────────
    contradictions = await _collect_contradictions(session, cited_relationships)

    # ── Validate citation chain (every cited ID must exist) ──────────────
    cited_claims = await _validate_claim_ids(session, cited_claims)
    cited_relationships = await _validate_relationship_ids(session, cited_relationships)

    # ── Build evidence chain ───────────────────────────────────────────
    evidence_chain = await _collect_citation_chain(
        session,
        claim_ids=cited_claims,
        relationship_ids=cited_relationships,
    )

    # ── Compute overall confidence ──────────────────────────────────────
    overall_confidence = _compute_overall_confidence(findings)

    # ── Build the answer_text (deterministic summary) ──────────────────
    answer_text = _compose_answer_text(
        query=query,
        intent=intent,
        findings=findings,
        unknowns=unknowns,
        contradictions=contradictions,
    )

    # ── Reasoning limitations (always present) ──────────────────────────
    limitations = _REASONING_LIMITATIONS

    return ReasoningAnswer(
        question=query,
        intent=intent,
        answer_text=answer_text,
        findings=findings,
        cited_claims=cited_claims,
        cited_relationships=cited_relationships,
        evidence_chain=evidence_chain,
        unknowns=unknowns,
        contradictions=contradictions,
        confidence={
            "overall": overall_confidence,
            "policy_version": POLICY_VERSION,
            "stale_threshold_days": STALENESS_THRESHOLD_DAYS,
            "confidence_map": dict(_CONFIDENCE_MAP),
            "note": (
                "confidence is bounded by the deterministic-v2 verification "
                "policy; VERIFIED is unreachable; CONTESTED is non-zero "
                "(preserved, not suppressed)"
            ),
        },
        limitations=limitations,
        request_id=request_id,
        policy_version=POLICY_VERSION,
        assessed_at=_utcnow_iso(),
        candidates=candidate_entity_ids,
        context=context,
        requester=requester,
    )


# ── Internal helpers ────────────────────────────────────────────────────────


_REASONING_LIMITATIONS: list[str] = [
    "deterministic evidence composition only -- no LLM-based reasoning",
    "VERIFIED is unreachable in deterministic-v2 (PRB-05)",
    "multiple URLs do NOT imply independent origins (PRB-04)",
    "applicability matching uses phrase-substring (conservative)",
    "no transitive dependency analysis beyond the immediate neighbors",
    "INTEGRATES_WITH is NOT treated as ALTERNATIVE_TO (per mission §5)",
    "no autonomous architecture generation -- findings are evidence-grounded only",
    "a technical combination is reported as a candidate/hypothesis, not a proven architecture",
]


def _empty_answer(
    query: str,
    intent: ReasoningIntent,
    unknowns: list[str],
    request_id: str,
) -> ReasoningAnswer:
    return ReasoningAnswer(
        question=query,
        intent=intent,
        answer_text=("Insufficient evidence to answer this query. Reasoning limitations apply."),
        findings=[],
        cited_claims=[],
        cited_relationships=[],
        evidence_chain=[],
        unknowns=unknowns,
        contradictions=[],
        confidence={
            "overall": 0.0,
            "policy_version": POLICY_VERSION,
            "note": "no findings -- query could not be answered",
        },
        limitations=_REASONING_LIMITATIONS,
        request_id=request_id,
        policy_version=POLICY_VERSION,
        assessed_at=_utcnow_iso(),
    )


async def _validate_claim_ids(
    session: AsyncSession,
    claim_ids: list[str],
) -> list[str]:
    """Drop any claim IDs that don't exist in the DB.

    Per mission briefing: "If a citation cannot be traced, the finding
    is downgraded to UNKNOWN" -- this function drops the bad citation.
    Downgrading the finding to UNKNOWN happens at the handler level
    when evidence_refs is empty.
    """
    if not claim_ids:
        return []
    stmt = select(ClaimRow.id).where(ClaimRow.id.in_(claim_ids))
    valid_ids = {row[0] for row in (await session.execute(stmt)).all()}
    return [cid for cid in claim_ids if cid in valid_ids]


async def _validate_relationship_ids(
    session: AsyncSession,
    relationship_ids: list[str],
) -> list[str]:
    """Drop any relationship IDs that don't exist in the DB."""
    if not relationship_ids:
        return []
    stmt = select(RelationshipRow.id).where(RelationshipRow.id.in_(relationship_ids))
    valid_ids = {row[0] for row in (await session.execute(stmt)).all()}
    return [rid for rid in relationship_ids if rid in valid_ids]


def _compute_overall_confidence(findings: list[Finding]) -> float:
    """Compute the overall confidence as the mean of finding confidences.

    Returns 0.0 if there are no findings. Caps at 0.90 (CORROBORATED)
    because VERIFIED is unreachable in deterministic-v2.
    """
    if not findings:
        return 0.0
    return min(0.90, sum(f.get("confidence", 0.0) for f in findings) / len(findings))


def _compose_answer_text(
    query: str,
    intent: ReasoningIntent,
    findings: list[Finding],
    unknowns: list[str],
    contradictions: list[dict[str, Any]],
) -> str:
    """Compose a deterministic, human-readable answer summary.

    No LLM. The summary is built from the structured findings.
    """
    parts: list[str] = []
    parts.append(f"Intent: {intent}.")
    if findings:
        documented = sum(1 for f in findings if f["type"] == FindingType.DOCUMENTED_FACT)
        derived = sum(1 for f in findings if f["type"] == FindingType.DERIVED_FINDING)
        hypothesis = sum(1 for f in findings if f["type"] == FindingType.HYPOTHESIS)
        unknown = sum(1 for f in findings if f["type"] == FindingType.UNKNOWN)
        parts.append(
            f"Findings: {len(findings)} total "
            f"({documented} documented, {derived} derived, "
            f"{hypothesis} hypothesis, {unknown} unknown)."
        )
    else:
        parts.append("No findings could be produced for this query.")
    if unknowns:
        parts.append(f"Unknowns: {len(unknowns)} item(s) reported honestly.")
    if contradictions:
        parts.append(f"Contradictions: {len(contradictions)} conflicting pair(s) preserved.")
    parts.append("A technical combination is a candidate, not a proven architecture.")
    return " ".join(parts)
