"""G04-T02 -- Gap Analysis: evidence-grounded classification of capability coverage.

Per the user's G04-T02 mission briefing §4:

  "Implement a deterministic, bounded gap-analysis service."

Input: a list of required capabilities (names) + optional technical
context + optional candidate technologies.

Output: a structured comparison between required capabilities and
available documented evidence, with one of six classifications per
(capability, candidate) pair:

  - SUPPORTED            : positive evidence exists, no contradictions,
                          applicability matches the requested context.
  - PARTIALLY_SUPPORTED : evidence covers only part of the requirement,
                          or important conditions remain unresolved.
  - NOT_EVIDENCED       : no sufficient supporting evidence was found.
                          This does NOT mean the capability is absent.
  - CONTESTED           : relevant positive and negative evidence conflict.
  - CONSTRAINED         : a documented limitation, dependency, or
                          applicability condition restricts the capability.
  - UNKNOWN             : the system cannot determine a reliable
                          classification from available information.

Classification rules (deterministic decision tree, in priority order):

  1. If no capability entity with this name exists in the knowledge graph
     → NOT_EVIDENCED (the capability is not documented; absence of
     evidence is not evidence of absence).

  2. If capability entity exists but no candidate has a PROVIDES / ENABLES
     / PRODUCES edge to it
     → NOT_EVIDENCED.

  3. If both SUPPORTS and CONTRADICTS evidence exist for the capability
     (in this candidate's context)
     → CONTESTED. Both sides are preserved in the result; neither is
     suppressed.

  4. If a LIMITS / INVALIDATES edge exists from a constraint entity to
     the capability, AND the constraint applies in the requested context
     → CONSTRAINED. The constraint, the limiting entity, and the
     evidence are preserved.

  5. If at least one assessed claim has outcome SOURCE_SUPPORTED or
     CORROBORATED, AND no contradicting evidence, AND validity_conditions
     overlap the requested context (or are empty meaning universally
     applicable)
     → SUPPORTED.

  6. If the candidate has a PROVIDES edge but no assessed claim, OR
     the assessed claim has outcome INSUFFICIENT_EVIDENCE, OR
     validity_conditions do not overlap the requested context but no
     contradiction
     → PARTIALLY_SUPPORTED.

  7. Otherwise (e.g., assessed claim is STALE_OR_CONTEXT_MISMATCH, or
     any state not covered above)
     → UNKNOWN.

Lexical similarity is NOT sufficient for SUPPORTED. A capability name
that fuzzy-matches an existing entity is recorded with
``match_quality="fuzzy"`` but is NOT promoted to SUPPORTED without
evidence (per mission briefing §5).

A "gap" is an evidence or requirement gap, not necessarily a proven
product deficiency. The result reports separately:
  - missing_evidence      : NOT_EVIDENCED entries
  - unsatisfied_prerequisites: CONSTRAINED entries with unmet dependencies
  - explicit_incompatibilities: CONSTRAINED entries with LIMITS edges
  - partially_covered_requirements: PARTIALLY_SUPPORTED entries
  - contradictory_evidence: CONTESTED entries

(per mission briefing §4 -- "Important distinction").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.capability_registry import (
    PROVIDER_PREDICATES,
    _find_capability_claims,
    _find_capability_limitations,
    _find_capability_providers,
    _find_provider_dependencies,
    _get_latest_assessment,
    _safe_json_loads,
    find_capability_by_alias_or_fuzzy,
)
from synapse.application.verification import (
    POLICY_VERSION,
    STALENESS_THRESHOLD_DAYS,
    VerificationOutcome,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
    SourceSpanRow,
)

_log = get_logger("synapse.application.gap_analyzer")


# ── Gap classification enum ─────────────────────────────────────────────────


class GapClassification(StrEnum):
    """The six classifications per the G04-T02 mission briefing §4."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    NOT_EVIDENCED = "not_evidenced"
    CONTESTED = "contested"
    CONSTRAINED = "constrained"
    UNKNOWN = "unknown"


# ── Hard limits ─────────────────────────────────────────────────────────────

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_REQUIRED_CAPABILITIES = 50
MAX_CANDIDATES = 20


# ── Result data classes ─────────────────────────────────────────────────────


class EvidenceChainLink(dict):
    """A single link in the citation chain.

    Keys: claim_id (optional), relationship_id (optional),
    fragment_id, source_uri, spans (list).
    """


class RequirementAssessment(dict):
    """Assessment of one required capability against one or more candidates.

    Keys:
        required_capability: the requested capability name
        capability_id: the resolved EntityRow id (or None if not found)
        match_quality: "exact" | "alias" | "fuzzy" | "not_found"
        classification: GapClassification value
        candidates: list of per-candidate assessments
        evidence_chain: list of EvidenceChainLink
        contradicting_evidence: list of contradicting fragment IDs
        limitations: list of limitation dicts (predicate, from_entity, evidence)
        unsatisfied_prerequisites: list of unmet dependency descriptions
        reason: human-readable explanation
        applicability_match: bool -- whether validity_conditions overlap context
    """


class GapAnalysisResult(dict):
    """Top-level result of ``analyze_gap``.

    Keys:
        requirements: list[RequirementAssessment]
        summary: dict with per-classification counts
        unknowns: list[str] -- what the system could NOT determine
        limits: dict
        request_id: str
        policy_version: str
        assessed_at: ISO timestamp
        context: the requested context (if any)
        candidates: list of candidate entity IDs considered
    """


# ── Helpers ─────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# NOTE: The previous _context_keywords() helper was removed in favor of
# phrase-substring matching in _applicability_matches(). Token-set overlap
# produced false-positive applicability matches (e.g., "AI agent systems" vs
# "embedded systems" share the token "systems"); phrase-substring matching
# is the conservative choice required by mission briefing §11.


def _applicability_matches(
    validity_conditions: list[str],
    context: str | None,
) -> bool:
    """Whether the claim's validity_conditions are compatible with the context.

    Uses **phrase-substring matching** (conservative) rather than
    token-set overlap. A condition matches only if the entire condition
    phrase appears as a substring of the context (case-insensitive).

    Per the conservative epistemic policy:
      - If validity_conditions is empty, the claim is universally
        applicable -> match.
      - If validity_conditions is non-empty AND context is empty/None, the
        claim has conditions that the caller didn't address -> no match.
      - If validity_conditions is non-empty AND context is non-empty,
        match only if at least one condition phrase appears as a
        substring of the context.

    Token-set overlap (e.g., "AI agent systems" vs "embedded systems"
    share the token "systems") is rejected because it produces
    false-positive applicability matches. The phrase-substring approach
    is stricter and respects the conservative evaluation requirement
    in mission briefing §11.
    """
    if not validity_conditions:
        return True  # universally applicable
    if not context:
        return False  # conditions exist but caller gave no context
    ctx_lower = context.lower()
    for cond in validity_conditions:
        if not cond:
            continue
        if cond.lower() in ctx_lower:
            return True
    return False


async def _collect_evidence_chain(
    session: AsyncSession,
    *,
    claim_ids: list[str],
    relationship_ids: list[str],
) -> list[EvidenceChainLink]:
    """Build a traversable citation chain for the given claims + relationships.

    Each link has: claim_id (optional), relationship_id (optional),
    fragment_id, source_uri, spans.
    """
    evidence_refs: set[str] = set()

    # Evidence refs from claims.
    for cid in claim_ids:
        stmt = select(ClaimRow).where(ClaimRow.id == cid)
        c = (await session.execute(stmt)).scalar_one_or_none()
        if c is not None:
            evidence_refs.update(_safe_json_loads(c.evidence_refs))
            evidence_refs.update(_safe_json_loads(c.contradicting_refs))

    # Evidence refs from relationships.
    for rid in relationship_ids:
        stmt = select(RelationshipRow).where(RelationshipRow.id == rid)
        r = (await session.execute(stmt)).scalar_one_or_none()
        if r is not None:
            evidence_refs.update(_safe_json_loads(r.evidence_refs))

    if not evidence_refs:
        return []

    frag_stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(list(evidence_refs)))
    fragments = list((await session.execute(frag_stmt)).scalars().all())

    span_stmt = select(SourceSpanRow).where(
        SourceSpanRow.evidence_fragment_id.in_([f.id for f in fragments])
    )
    spans = list((await session.execute(span_stmt)).scalars().all())

    chain: list[EvidenceChainLink] = []
    for frag in fragments:
        frag_spans = [s for s in spans if s.evidence_fragment_id == frag.id]
        chain.append(
            EvidenceChainLink(
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


async def _find_candidate_provider_links(
    session: AsyncSession,
    capability_id: str,
    candidate_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Find PROVIDES / ENABLES / PRODUCES edges from candidates to the capability.

    If ``candidate_ids`` is None, returns ALL providers of this capability
    (the entire knowledge graph is the candidate set).
    """
    links: list[dict[str, Any]] = []
    seen_rel_ids: set[str] = set()

    for predicate in PROVIDER_PREDICATES:
        stmt = (
            select(RelationshipRow, EntityRow)
            .join(EntityRow, EntityRow.id == RelationshipRow.from_entity_id)
            .where(
                RelationshipRow.to_entity_id == capability_id,
                RelationshipRow.predicate == predicate,
            )
            .limit(MAX_CANDIDATES)
        )
        if candidate_ids is not None:
            stmt = stmt.where(RelationshipRow.from_entity_id.in_(candidate_ids))
        result = await session.execute(stmt)
        for rel, ent in result.all():
            if rel.id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel.id)
            links.append(
                {
                    "relationship_id": rel.id,
                    "predicate": predicate,
                    "provider_entity_id": ent.id,
                    "provider_kind": ent.kind,
                    "provider_canonical_name": ent.canonical_name,
                    "provider_canonical_uri": ent.canonical_uri,
                    "evidence_refs": _safe_json_loads(rel.evidence_refs),
                    "origin": rel.origin,
                    "verification_state": rel.verification_state,
                    "conditions": _safe_json_loads(rel.conditions),
                    "has_evidence": bool(_safe_json_loads(rel.evidence_refs)),
                }
            )
    return links


async def _collect_capability_evidence_fragments(
    session: AsyncSession,
    capability_id: str,
    provider_links: list[dict[str, Any]],
    *,
    candidate_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[ClaimRow]]:
    """Collect assessed claims + their assessments for this capability.

    When ``candidate_ids`` is provided, claims are filtered to those
    attributed to one of the candidates (per the G04-T02C provider-
    attribution safeguard). A claim is "attributed to" a candidate if
    the non-capability endpoint of the claim is one of the candidates.

    Pattern 1: ``claim.subject_ref == candidate_id`` AND
              ``claim.object_ref == capability_id``
              (the candidate is the provider/subject, the capability is
              the object)

    Pattern 2: ``claim.subject_ref == capability_id`` AND
              ``claim.object_ref == candidate_id``
              (the candidate is the recipient/object)

    Claims with ``subject_ref=None`` or ``object_ref=None`` are NOT
    attributed to any candidate (ambiguous attribution -> conservative
    classification, per G04-T02C §6).

    When ``candidate_ids`` is None, no filtering is applied (the analysis
    is at the capability level: "is this capability supported by anyone?").

    Returns (assessments_with_claim_info, claims).
    """
    claims = await _find_capability_claims(session, capability_id)

    # G04-T02C provider-attribution safeguard: when candidates are named,
    # filter claims to those attributed to one of the candidates. A
    # capability-level claim about an unrelated provider must NOT count
    # as evidence for the selected candidate's support (per mission §5).
    if candidate_ids is not None:
        candidate_id_set = set(candidate_ids)
        claims = [
            c for c in claims if _claim_attributed_to_candidate(c, capability_id, candidate_id_set)
        ]

    assessments: list[dict[str, Any]] = []
    for c in claims:
        assessment = await _get_latest_assessment(session, c.id)
        assessments.append(
            {
                "claim_id": c.id,
                "proposition": c.proposition,
                "epistemic_state": c.epistemic_state,
                "evidence_refs": _safe_json_loads(c.evidence_refs),
                "contradicting_refs": _safe_json_loads(c.contradicting_refs),
                "validity_conditions": _safe_json_loads(c.validity_conditions),
                "assessment": assessment,
            }
        )
    return assessments, claims


def _claim_attributed_to_candidate(
    claim: ClaimRow,
    capability_id: str,
    candidate_id_set: set[str],
) -> bool:
    """Whether a claim is attributed to one of the candidate providers.

    Per G04-T02C mission §4-5: a SUPPORTED result for a named candidate
    must require a valid, evidence-grounded association between that
    candidate and the capability. A capability-level claim about an
    unrelated provider must NOT count as evidence for the selected
    candidate's support.

    A claim is "attributed to" a candidate if the non-capability endpoint
    of the claim is one of the candidates:

      Pattern 1 (subject is the provider):
          claim.subject_ref in candidate_id_set
          AND claim.object_ref == capability_id

      Pattern 2 (object is the provider, capability is the subject):
          claim.subject_ref == capability_id
          AND claim.object_ref in candidate_id_set

    Claims with no subject_ref (or no object_ref when capability is the
    subject) are NOT attributed to any candidate. This is the
    "conservative classification when attribution is ambiguous" rule
    (per G04-T02C §6).
    """
    subj = claim.subject_ref
    obj = claim.object_ref

    # Pattern 1: subject is the candidate, object is the capability.
    # Pattern 2: subject is the capability, object is the candidate.
    return (subj is not None and subj in candidate_id_set and obj == capability_id) or (
        subj == capability_id and obj is not None and obj in candidate_id_set
    )


def _positive_outcome(outcome: str | None) -> bool:
    """Whether a verification outcome counts as positive evidence."""
    return outcome in (
        VerificationOutcome.SOURCE_SUPPORTED,
        VerificationOutcome.CORROBORATED,
        VerificationOutcome.VERIFIED,  # unreachable in v2 but listed for completeness
    )


def _negative_outcome(outcome: str | None) -> bool:
    """Whether a verification outcome counts as negative / contradicting."""
    return outcome == VerificationOutcome.CONTESTED


def _stale_outcome(outcome: str | None) -> bool:
    """Whether a verification outcome counts as stale / context-mismatch."""
    return outcome == VerificationOutcome.STALE_OR_CONTEXT_MISMATCH


# ── Main entry point ────────────────────────────────────────────────────────


async def analyze_gap(
    session: AsyncSession,
    required_capabilities: list[str],
    *,
    context: str | None = None,
    candidate_entity_ids: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> GapAnalysisResult:
    """Analyze capability coverage against documented evidence.

    Args:
        session: AsyncSession.
        required_capabilities: list of capability names to evaluate. Each
            name is resolved against ``EntityRow(kind="capability")`` by
            exact match, alias match, or fuzzy ILIKE match (in that order).
        context: optional technical context string (e.g., "AI agent
            systems in Python"). Used for applicability matching against
            claim validity_conditions.
        candidate_entity_ids: optional list of entity IDs to restrict the
            analysis to (e.g., the candidate technology to evaluate). If
            None, all entities are candidates.
        limit: max number of requirements to evaluate (capped at MAX_LIMIT).
        requester: optional actor name for audit logging.

    Returns:
        GapAnalysisResult with one RequirementAssessment per required
        capability.
    """
    request_id = uuid4().hex
    limit = max(1, min(MAX_LIMIT, limit))

    # Cap the input size.
    required = list(required_capabilities)[:MAX_REQUIRED_CAPABILITIES]

    assessments: list[RequirementAssessment] = []

    for req_name in required:
        assessments.append(
            await _assess_single_requirement(
                session,
                req_name,
                context=context,  # pass the original context string
                candidate_ids=candidate_entity_ids,
            )
        )

    # Build the summary.
    summary: dict[str, int] = {c.value: 0 for c in GapClassification}
    for a in assessments:
        summary[a["classification"]] = summary.get(a["classification"], 0) + 1

    # Build the gap-separated views (per mission briefing §4 "Important
    # distinction").
    missing_evidence = [
        a["required_capability"]
        for a in assessments
        if a["classification"] == GapClassification.NOT_EVIDENCED
    ]
    partially_covered = [
        a["required_capability"]
        for a in assessments
        if a["classification"] == GapClassification.PARTIALLY_SUPPORTED
    ]
    contradictory = [
        a["required_capability"]
        for a in assessments
        if a["classification"] == GapClassification.CONTESTED
    ]
    [
        a["required_capability"]
        for a in assessments
        if a["classification"] == GapClassification.CONSTRAINED
    ]
    unknown = [
        a["required_capability"]
        for a in assessments
        if a["classification"] == GapClassification.UNKNOWN
    ]

    unsatisfied_prerequisites: list[str] = []
    explicit_incompatibilities: list[str] = []
    for a in assessments:
        if a["classification"] == GapClassification.CONSTRAINED:
            for lim in a.get("limitations", []):
                if lim["predicate"] in ("LIMITS", "INVALIDATES"):
                    explicit_incompatibilities.append(
                        f"{a['required_capability']}: limited by {lim.get('from_canonical_name')}"
                    )
                if lim["predicate"] == "CONTRADICTS":
                    contradictory.append(a["required_capability"])
            for up in a.get("unsatisfied_prerequisites", []):
                unsatisfied_prerequisites.append(f"{a['required_capability']}: {up}")

    # De-duplicate the contradictory list (a CONTESTED entry may also be
    # listed via CONSTRAINED + CONTRADICTS).
    contradictory = sorted(set(contradictory))

    unknowns: list[str] = []
    if not assessments:
        unknowns.append("no_required_capabilities_provided")
    if candidate_entity_ids is not None and not candidate_entity_ids:
        unknowns.append("empty_candidate_list_analyzed_all_providers")

    return GapAnalysisResult(
        requirements=assessments,
        summary=summary,
        gap_views={
            "missing_evidence": missing_evidence,
            "partially_covered_requirements": partially_covered,
            "contradictory_evidence": contradictory,
            "unsatisfied_prerequisites": unsatisfied_prerequisites,
            "explicit_incompatibilities": explicit_incompatibilities,
            "unknown": unknown,
        },
        unknowns=unknowns,
        limits={
            "limit": limit,
            "max_limit": MAX_LIMIT,
            "max_required_capabilities": MAX_REQUIRED_CAPABILITIES,
            "max_candidates": MAX_CANDIDATES,
        },
        request_id=request_id,
        policy_version=POLICY_VERSION,
        stale_threshold_days=STALENESS_THRESHOLD_DAYS,
        assessed_at=_utcnow_iso(),
        context=context,
        candidates=candidate_entity_ids,
        requester=requester,
    )


async def _assess_single_requirement(
    session: AsyncSession,
    required_capability: str,
    *,
    context: str | None,
    candidate_ids: list[str] | None,
) -> RequirementAssessment:
    """Assess a single required capability against the knowledge graph.

    Implements the deterministic decision tree documented at the top of
    this module. The tree is evaluated in priority order; the first
    matching rule wins.
    """
    # ── 1. Resolve the capability entity (exact / alias / fuzzy / not_found)
    detail = await find_capability_by_alias_or_fuzzy(session, required_capability)
    if detail is None:
        # Capability not documented in the knowledge graph.
        # Per mission briefing §4: NOT_EVIDENCED -- absence of evidence
        # is not evidence of absence.
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=None,
            match_quality="not_found",
            classification=GapClassification.NOT_EVIDENCED,
            candidates=[],
            evidence_chain=[],
            contradicting_evidence=[],
            limitations=[],
            unsatisfied_prerequisites=[],
            reason=(
                "no capability entity with this name in the knowledge graph; "
                "absence of evidence is not evidence of absence"
            ),
            applicability_match=False,
        )

    capability_id = detail["id"]
    match_quality = detail.get("match_quality", "exact")

    # ── 2. Find candidate providers (PROVIDES / ENABLES / PRODUCES edges)
    provider_links = await _find_candidate_provider_links(session, capability_id, candidate_ids)

    # If no provider has an edge → NOT_EVIDENCED.
    if not provider_links:
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.NOT_EVIDENCED,
            candidates=[],
            evidence_chain=[],
            contradicting_evidence=[],
            limitations=[],
            unsatisfied_prerequisites=[],
            reason=(
                "capability entity exists but no entity has a PROVIDES/ENABLES/"
                "PRODUCES edge to it; no positive evidence found"
            ),
            applicability_match=False,
        )

    # ── 3. Collect claims + assessments for this capability.
    # G04-T02C: pass candidate_ids so claims are filtered by attribution
    # to one of the candidates. A capability-level claim about an
    # unrelated provider must NOT count as evidence for the selected
    # candidate's support.
    assessments_with_claim, _ = await _collect_capability_evidence_fragments(
        session, capability_id, provider_links, candidate_ids=candidate_ids
    )

    # ── 4. Collect limitations on this capability.
    limitations = await _find_capability_limitations(session, capability_id)

    # ── 5. Collect unsatisfied prerequisites (dependencies of candidates
    # that are themselves not provided by anyone).
    unsatisfied_prereqs: list[str] = []
    for link in provider_links:
        deps = await _find_provider_dependencies(session, link["provider_entity_id"])
        for d in deps:
            # Check if the dependency (to-entity) is itself a capability that
            # is provided by anyone. If not, it's an unsatisfied prerequisite.
            dep_ent_stmt = select(EntityRow).where(EntityRow.id == d["entity_id"])
            dep_ent = (await session.execute(dep_ent_stmt)).scalar_one_or_none()
            if dep_ent is not None and dep_ent.kind == "capability":
                # Is this dependency capability provided by anyone?
                dep_providers = await _find_capability_providers(session, dep_ent.id, limit=5)
                if not dep_providers:
                    unsatisfied_prereqs.append(
                        f"candidate '{link['provider_canonical_name']}' "
                        f"REQUIRES '{dep_ent.canonical_name}' but no provider "
                        f"documents it"
                    )

    # ── 6. Evaluate applicability of claims against the context.
    applicable_claims: list[dict[str, Any]] = []
    inapplicable_claims: list[dict[str, Any]] = []
    for ac in assessments_with_claim:
        vcs = ac.get("validity_conditions", [])
        if _applicability_matches(vcs, context):
            applicable_claims.append(ac)
        else:
            inapplicable_claims.append(ac)

    # ── 7. Determine the classification (deterministic decision tree).

    # 7a. CONTESTED: any assessed claim has outcome CONTESTED, OR any claim
    # has explicit contradicting_refs. Both sides are preserved, NOT
    # suppressed.
    has_positive = any(
        _positive_outcome((ac.get("assessment") or {}).get("outcome")) for ac in applicable_claims
    )
    has_negative = any(
        _negative_outcome((ac.get("assessment") or {}).get("outcome")) for ac in applicable_claims
    )
    has_contested_outcome = any(
        (ac.get("assessment") or {}).get("outcome") == VerificationOutcome.CONTESTED
        for ac in applicable_claims
    )
    has_contradicting_refs = any(ac.get("contradicting_refs") for ac in applicable_claims)
    if has_contested_outcome or has_contradicting_refs:
        # Build the contradicting evidence list.
        contra_frags: list[str] = []
        for ac in applicable_claims:
            contra_frags.extend(ac.get("contradicting_refs", []))
        chain = await _collect_evidence_chain(
            session,
            claim_ids=[ac["claim_id"] for ac in applicable_claims],
            relationship_ids=[link["relationship_id"] for link in provider_links],
        )
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.CONTESTED,
            candidates=provider_links,
            evidence_chain=chain,
            contradicting_evidence=sorted(set(contra_frags)),
            limitations=limitations,
            unsatisfied_prerequisites=unsatisfied_prereqs,
            reason=(
                "supporting and contradicting evidence present; "
                "both sides preserved, neither suppressed"
            ),
            applicability_match=True,
            applicable_claim_count=len(applicable_claims),
            inapplicable_claim_count=len(inapplicable_claims),
        )

    # 7b. CONSTRAINED: documented LIMITS / INVALIDATES edge that applies.
    # A LIMITS edge is "applicable" if the limiting entity's conditions
    # don't conflict with the context (or have no conditions).
    applicable_limitations: list[dict[str, Any]] = []
    for lim in limitations:
        if lim["predicate"] in ("LIMITS", "INVALIDATES"):
            applicable_limitations.append(lim)

    if applicable_limitations and not has_positive:
        # If we have limitations AND no positive evidence to override
        # them, the capability is constrained.
        chain = await _collect_evidence_chain(
            session,
            claim_ids=[ac["claim_id"] for ac in applicable_claims],
            relationship_ids=[link["relationship_id"] for link in provider_links]
            + [lim["relationship_id"] for lim in applicable_limitations],
        )
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.CONSTRAINED,
            candidates=provider_links,
            evidence_chain=chain,
            contradicting_evidence=[],
            limitations=limitations,
            unsatisfied_prerequisites=unsatisfied_prereqs,
            reason=(
                f"{len(applicable_limitations)} documented limitation(s) apply "
                f"and no positive evidence overrides them"
            ),
            applicability_match=True,
            applicable_claim_count=len(applicable_claims),
            inapplicable_claim_count=len(inapplicable_claims),
        )

    # 7c. SUPPORTED: positive outcome + no contradictions + applicability matches.
    if has_positive and not (has_negative or has_contradicting_refs):
        chain = await _collect_evidence_chain(
            session,
            claim_ids=[ac["claim_id"] for ac in applicable_claims],
            relationship_ids=[link["relationship_id"] for link in provider_links],
        )
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.SUPPORTED,
            candidates=provider_links,
            evidence_chain=chain,
            contradicting_evidence=[],
            limitations=limitations,
            unsatisfied_prerequisites=unsatisfied_prereqs,
            reason=(
                f"{sum(1 for ac in applicable_claims if _positive_outcome((ac.get('assessment') or {}).get('outcome')))} "
                f"assessed claim(s) with positive outcome (SOURCE_SUPPORTED or CORROBORATED), "
                f"no contradicting evidence, applicability matches context"
            ),
            applicability_match=True,
            applicable_claim_count=len(applicable_claims),
            inapplicable_claim_count=len(inapplicable_claims),
        )

    # 7d. PARTIALLY_SUPPORTED: provider exists but evidence incomplete.
    has_provider_with_evidence = any(link["has_evidence"] for link in provider_links)
    has_insufficient = any(
        (ac.get("assessment") or {}).get("outcome") == VerificationOutcome.INSUFFICIENT_EVIDENCE
        for ac in applicable_claims
    )
    if (
        provider_links
        and not has_positive
        and (has_provider_with_evidence or has_insufficient or inapplicable_claims)
    ):
        chain = await _collect_evidence_chain(
            session,
            claim_ids=[ac["claim_id"] for ac in applicable_claims],
            relationship_ids=[link["relationship_id"] for link in provider_links],
        )
        reason_parts = []
        if inapplicable_claims:
            reason_parts.append(
                f"{len(inapplicable_claims)} claim(s) have validity_conditions "
                f"not matching the requested context"
            )
        if has_insufficient:
            reason_parts.append("at least one assessed claim has outcome INSUFFICIENT_EVIDENCE")
        if not has_positive and not has_insufficient and has_provider_with_evidence:
            reason_parts.append("provider has evidence-backed PROVIDES edge but no assessed claim")
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.PARTIALLY_SUPPORTED,
            candidates=provider_links,
            evidence_chain=chain,
            contradicting_evidence=[],
            limitations=limitations,
            unsatisfied_prerequisites=unsatisfied_prereqs,
            reason="; ".join(reason_parts) or "evidence covers only part of the requirement",
            applicability_match=bool(applicable_claims) and not inapplicable_claims,
            applicable_claim_count=len(applicable_claims),
            inapplicable_claim_count=len(inapplicable_claims),
        )

    # 7e. NOT_EVIDENCED: provider exists but no evidence at all.
    if provider_links and not has_positive and not has_insufficient and not inapplicable_claims:
        return RequirementAssessment(
            required_capability=required_capability,
            capability_id=capability_id,
            match_quality=match_quality,
            classification=GapClassification.NOT_EVIDENCED,
            candidates=provider_links,
            evidence_chain=[],
            contradicting_evidence=[],
            limitations=limitations,
            unsatisfied_prerequisites=unsatisfied_prereqs,
            reason=(
                "provider has a PROVIDES edge but no evidence-backed claim; "
                "no sufficient supporting evidence was found"
            ),
            applicability_match=False,
            applicable_claim_count=0,
            inapplicable_claim_count=0,
        )

    # 7f. UNKNOWN: anything else (e.g., stale evidence, ambiguous state).
    chain = await _collect_evidence_chain(
        session,
        claim_ids=[ac["claim_id"] for ac in applicable_claims],
        relationship_ids=[link["relationship_id"] for link in provider_links],
    )
    return RequirementAssessment(
        required_capability=required_capability,
        capability_id=capability_id,
        match_quality=match_quality,
        classification=GapClassification.UNKNOWN,
        candidates=provider_links,
        evidence_chain=chain,
        contradicting_evidence=[],
        limitations=limitations,
        unsatisfied_prerequisites=unsatisfied_prereqs,
        reason="cannot determine a reliable classification from available information",
        applicability_match=False,
        applicable_claim_count=len(applicable_claims),
        inapplicable_claim_count=len(inapplicable_claims),
    )
