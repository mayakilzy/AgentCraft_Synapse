"""G04-T02 -- Capability Registry: logical view over the existing knowledge graph.

Per the user's G04-T02 mission briefing §3:

  "Build a logical capability registry over the existing structured
   knowledge store. Do not create a separate capability database."

This module is a LOGICAL VIEW, not a new persistence layer. A
"capability" in this registry is an ``EntityRow(kind="capability")``.
A "provider" is any entity with an outgoing PROVIDES / ENABLES /
PRODUCES edge to that capability entity. Dependencies and limitations
are derived from existing RelationshipService predicates.

This module does NOT duplicate canonicalization, evidence verification,
or storage -- it composes the existing G01-G04-T01 infrastructure.

Relationship semantics (per the G04-T02 mission briefing §3):

  - PROVIDES  : the from-entity is a documented provider of the capability
  - ENABLES   : the from-entity enables the capability (slightly weaker)
  - PRODUCES  : the from-entity produces the capability as output
  - REQUIRES  : the from-entity requires the to-entity (a dependency)
  - DEPENDS_ON: same semantics as REQUIRES (alias in the G01 vocabulary)
  - LIMITS    : a documented limitation applies (from a constraint entity)
  - CONTRADICTS: contradicts another claim/relationship (preserved, not suppressed)
  - INVALIDATES: invalidates the target
  - INTEGRATES_WITH: undirected -- NOT interpreted as ALTERNATIVE_TO
  - REPLACES  : the from-entity replaces the to-entity (alternative)

Directionality is preserved: PROVIDES is from provider -> capability.
INTEGRATES_WITH is undirected and is NOT collapsed into ALTERNATIVE_TO
per the G04-T02 mission briefing §5 and the G04 plan §3 (carry-forward
correction).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.verification import (
    POLICY_VERSION,
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

_log = get_logger("synapse.application.capability_registry")


# ── Predicates that constitute "provides a capability" ──────────────────────

#: A "provider" of a capability is any entity with one of these outgoing
#: edges to a capability entity. PROVIDES is the canonical predicate;
#: ENABLES and PRODUCES are weaker but still indicate the entity
#: contributes the capability.
PROVIDER_PREDICATES: tuple[str, ...] = ("PROVIDES", "ENABLES", "PRODUCES")

#: Predicates that express a documented dependency. REQUIRES and
#: DEPENDS_ON are treated as aliases (per the G01 vocabulary).
DEPENDENCY_PREDICATES: tuple[str, ...] = ("REQUIRES", "DEPENDS_ON")

#: Predicates that express a documented limitation, constraint, or
#: contradiction affecting the capability.
LIMITATION_PREDICATES: tuple[str, ...] = ("LIMITS", "CONTRADICTS", "INVALIDATES")

#: Hard limits to prevent unbounded scans.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_PROVIDERS_PER_CAPABILITY = 50
MAX_DEPENDENCIES_PER_PROVIDER = 50
MAX_LIMITATIONS_PER_CAPABILITY = 50
MAX_CLAIMS_PER_CAPABILITY = 50


# ── Result data classes (dict subclasses for JSON-friendly output) ──────────


class ProviderInfo(dict):
    """Summary of a single provider of a capability.

    Keys: entity_id, kind, canonical_name, canonical_uri, predicate
    (PROVIDES / ENABLES / PRODUCES), relationship_id, evidence_refs,
    has_evidence (bool).
    """


class DependencyInfo(dict):
    """Summary of a single documented dependency.

    Keys: entity_id, kind, canonical_name, canonical_uri, predicate
    (REQUIRES / DEPENDS_ON), relationship_id, evidence_refs, has_evidence.
    """


class LimitationInfo(dict):
    """Summary of a single documented limitation.

    Keys: from_entity_id, from_kind, from_canonical_name, predicate
    (LIMITS / CONTRADICTS / INVALIDATES), relationship_id, evidence_refs,
    has_evidence.
    """


class EvidenceRefInfo(dict):
    """A single evidence reference with provenance.

    Keys: fragment_id, source_uri, retrieved_at, extraction_method,
    spans (list of dicts with start_offset, end_offset, excerpt).
    """


class ClaimAssessmentInfo(dict):
    """A claim + its latest verification assessment.

    Keys: claim_id, proposition, epistemic_state, outcome, reason_code,
    supporting_evidence_count, opposing_evidence_count,
    independent_source_count, policy_version, assessed_at.
    """


class CapabilitySummary(dict):
    """Lightweight summary of a capability (for list views).

    Keys: id, kind, canonical_name, canonical_uri, description,
    provider_count, has_limitations, domain (best-effort from
    attributes/description).
    """


class CapabilityDetail(dict):
    """Full detail for a single capability.

    Keys: id, kind, canonical_name, canonical_uri, description, aliases,
    attributes, providers (list[ProviderInfo]), dependencies (list[
    DependencyInfo]), limitations (list[LimitationInfo]), claims (list[
    ClaimAssessmentInfo]), evidence_refs (list[EvidenceRefInfo]),
    policy_version, retrieved_at.
    """


class CapabilityRegistryResult(dict):
    """List-capabilities result envelope.

    Keys: items (list[CapabilitySummary]), count, limits.
    """


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_json_loads(raw: str | None) -> list[str]:
    if not raw:
        return []
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        out = json.loads(raw)
        if isinstance(out, list):
            return [str(x) for x in out]
    return []


def _entity_summary_dict(row: EntityRow) -> dict[str, Any]:
    aliases = _safe_json_loads(row.aliases) if row.aliases else []
    attributes: dict[str, Any] = {}
    if row.attributes:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(row.attributes)
            if isinstance(parsed, dict):
                attributes = parsed
    return {
        "id": row.id,
        "kind": row.kind,
        "canonical_name": row.canonical_name,
        "canonical_uri": row.canonical_uri,
        "description": row.description,
        "aliases": aliases,
        "attributes": attributes,
        "version": row.version,
    }


def _utcnow_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ── Provider / dependency / limitation lookups ──────────────────────────────


async def _find_capability_providers(
    session: AsyncSession,
    capability_id: str,
    *,
    limit: int = MAX_PROVIDERS_PER_CAPABILITY,
) -> list[ProviderInfo]:
    """Find entities that PROVIDE / ENABLE / PRODUCE this capability.

    Looks for incoming edges to ``capability_id`` with one of the
    PROVIDER_PREDICATES. Directionality is preserved: the provider is
    the from_entity, the capability is the to_entity.
    """
    providers: list[ProviderInfo] = []
    seen_rel_ids: set[str] = set()

    for predicate in PROVIDER_PREDICATES:
        stmt = (
            select(RelationshipRow, EntityRow)
            .join(EntityRow, EntityRow.id == RelationshipRow.from_entity_id)
            .where(
                RelationshipRow.to_entity_id == capability_id,
                RelationshipRow.predicate == predicate,
            )
            .limit(limit)
        )
        result = await session.execute(stmt)
        for rel, ent in result.all():
            if rel.id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel.id)
            evidence_refs = _safe_json_loads(rel.evidence_refs)
            providers.append(
                ProviderInfo(
                    entity_id=ent.id,
                    kind=ent.kind,
                    canonical_name=ent.canonical_name,
                    canonical_uri=ent.canonical_uri,
                    predicate=predicate,
                    relationship_id=rel.id,
                    evidence_refs=evidence_refs,
                    has_evidence=bool(evidence_refs),
                    origin=rel.origin,
                    verification_state=rel.verification_state,
                )
            )
            if len(providers) >= limit:
                return providers
    return providers


async def _find_provider_dependencies(
    session: AsyncSession,
    provider_id: str,
    *,
    limit: int = MAX_DEPENDENCIES_PER_PROVIDER,
) -> list[DependencyInfo]:
    """Find entities that the provider REQUIRES / DEPENDS_ON.

    Directionality is preserved: the dependency is the to_entity of an
    outgoing REQUIRES / DEPENDS_ON edge from the provider.
    """
    deps: list[DependencyInfo] = []
    seen_rel_ids: set[str] = set()

    for predicate in DEPENDENCY_PREDICATES:
        stmt = (
            select(RelationshipRow, EntityRow)
            .join(EntityRow, EntityRow.id == RelationshipRow.to_entity_id)
            .where(
                RelationshipRow.from_entity_id == provider_id,
                RelationshipRow.predicate == predicate,
            )
            .limit(limit)
        )
        result = await session.execute(stmt)
        for rel, ent in result.all():
            if rel.id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel.id)
            evidence_refs = _safe_json_loads(rel.evidence_refs)
            deps.append(
                DependencyInfo(
                    entity_id=ent.id,
                    kind=ent.kind,
                    canonical_name=ent.canonical_name,
                    canonical_uri=ent.canonical_uri,
                    predicate=predicate,
                    relationship_id=rel.id,
                    evidence_refs=evidence_refs,
                    has_evidence=bool(evidence_refs),
                    origin=rel.origin,
                    verification_state=rel.verification_state,
                )
            )
            if len(deps) >= limit:
                return deps
    return deps


async def _find_capability_limitations(
    session: AsyncSession,
    capability_id: str,
    *,
    limit: int = MAX_LIMITATIONS_PER_CAPABILITY,
) -> list[LimitationInfo]:
    """Find documented limitations / constraints / contradictions on a capability.

    Looks for incoming edges to ``capability_id`` with one of the
    LIMITATION_PREDICATES. A LIMITS edge from a constraint entity to
    the capability is the canonical pattern. CONTRADICTS edges
    (undirected) are also captured here, with from/to preserved.
    """
    limitations: list[LimitationInfo] = []
    seen_rel_ids: set[str] = set()

    for predicate in LIMITATION_PREDICATES:
        stmt = (
            select(RelationshipRow, EntityRow)
            .join(EntityRow, EntityRow.id == RelationshipRow.from_entity_id)
            .where(
                RelationshipRow.to_entity_id == capability_id,
                RelationshipRow.predicate == predicate,
            )
            .limit(limit)
        )
        result = await session.execute(stmt)
        for rel, ent in result.all():
            if rel.id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel.id)
            evidence_refs = _safe_json_loads(rel.evidence_refs)
            limitations.append(
                LimitationInfo(
                    from_entity_id=ent.id,
                    from_kind=ent.kind,
                    from_canonical_name=ent.canonical_name,
                    from_canonical_uri=ent.canonical_uri,
                    predicate=predicate,
                    relationship_id=rel.id,
                    evidence_refs=evidence_refs,
                    has_evidence=bool(evidence_refs),
                    origin=rel.origin,
                    verification_state=rel.verification_state,
                )
            )
            if len(limitations) >= limit:
                return limitations
    return limitations


async def _find_capability_claims(
    session: AsyncSession,
    capability_id: str,
    *,
    limit: int = MAX_CLAIMS_PER_CAPABILITY,
) -> list[ClaimRow]:
    """Find claims whose subject_ref or object_ref is this capability."""
    stmt = (
        select(ClaimRow)
        .where((ClaimRow.subject_ref == capability_id) | (ClaimRow.object_ref == capability_id))
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _get_latest_assessment(
    session: AsyncSession,
    claim_id: str,
) -> dict[str, Any] | None:
    """Fetch the latest verification.assessed audit event for a claim."""
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


def _claim_assessment_info(
    claim: ClaimRow,
    assessment: dict[str, Any] | None,
) -> ClaimAssessmentInfo:
    return ClaimAssessmentInfo(
        claim_id=claim.id,
        proposition=claim.proposition,
        epistemic_state=claim.epistemic_state,
        outcome=(assessment or {}).get("outcome") if assessment else None,
        reason_code=(assessment or {}).get("reason_code") if assessment else None,
        supporting_evidence_count=(assessment or {}).get("supporting_evidence_count", 0)
        if assessment
        else 0,
        opposing_evidence_count=(assessment or {}).get("opposing_evidence_count", 0)
        if assessment
        else 0,
        independent_source_count=(assessment or {}).get("independent_source_count", 0)
        if assessment
        else 0,
        policy_version=(assessment or {}).get("policy_version", POLICY_VERSION)
        if assessment
        else POLICY_VERSION,
        assessed_at=(assessment or {}).get("assessed_at") if assessment else None,
        has_assessment=assessment is not None,
        contradicting_refs=_safe_json_loads(claim.contradicting_refs),
        validity_conditions=_safe_json_loads(claim.validity_conditions),
    )


async def _collect_evidence_refs(
    session: AsyncSession,
    evidence_refs: list[str],
) -> list[EvidenceRefInfo]:
    """Build evidence bundles with source spans for a list of fragment IDs."""
    if not evidence_refs:
        return []
    stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(evidence_refs))
    fragments = list((await session.execute(stmt)).scalars().all())

    span_stmt = select(SourceSpanRow).where(
        SourceSpanRow.evidence_fragment_id.in_([f.id for f in fragments])
    )
    spans = list((await session.execute(span_stmt)).scalars().all())

    out: list[EvidenceRefInfo] = []
    for frag in fragments:
        frag_spans = [s for s in spans if s.evidence_fragment_id == frag.id]
        out.append(
            EvidenceRefInfo(
                fragment_id=frag.id,
                source_uri=frag.source_uri,
                retrieved_at=frag.retrieved_at,
                extraction_method=frag.extraction_method,
                content_fingerprint=frag.content_fingerprint,
                spans=[
                    {
                        "id": s.id,
                        "start_offset": s.start_offset,
                        "end_offset": s.end_offset,
                        "excerpt": s.excerpt[:300],
                    }
                    for s in frag_spans
                ],
            )
        )
    return out


# ── Public API ──────────────────────────────────────────────────────────────


async def list_capabilities(
    session: AsyncSession,
    *,
    name_contains: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> CapabilityRegistryResult:
    """List all documented capabilities in the knowledge graph.

    A capability is an ``EntityRow(kind="capability")``. This function
    enumerates them and computes a lightweight summary (provider count,
    limitation flag) per capability. It does NOT load full evidence
    bundles -- use ``get_capability()`` for that.
    """
    limit = max(1, min(MAX_LIMIT, limit))
    stmt = select(EntityRow).where(EntityRow.kind == "capability")
    if name_contains:
        stmt = stmt.where(EntityRow.canonical_name.ilike(f"%{name_contains}%"))
    stmt = stmt.order_by(EntityRow.canonical_name.asc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())

    items: list[CapabilitySummary] = []
    for r in rows:
        providers = await _find_capability_providers(session, r.id)
        limitations = await _find_capability_limitations(session, r.id)
        items.append(
            CapabilitySummary(
                id=r.id,
                kind=r.kind,
                canonical_name=r.canonical_name,
                canonical_uri=r.canonical_uri,
                description=r.description,
                provider_count=len(providers),
                has_limitations=len(limitations) > 0,
                limitation_count=len(limitations),
            )
        )

    return CapabilityRegistryResult(
        items=items,
        count=len(items),
        limits={
            "limit": limit,
            "max_limit": MAX_LIMIT,
            "max_providers_per_capability": MAX_PROVIDERS_PER_CAPABILITY,
            "max_limitations_per_capability": MAX_LIMITATIONS_PER_CAPABILITY,
        },
    )


async def get_capability(
    session: AsyncSession,
    capability_id: str,
) -> CapabilityDetail | None:
    """Get full detail for a single capability.

    Returns None if the entity does not exist or is not a capability.
    """
    stmt = select(EntityRow).where(
        EntityRow.id == capability_id,
        EntityRow.kind == "capability",
    )
    ent = (await session.execute(stmt)).scalar_one_or_none()
    if ent is None:
        return None

    providers = await _find_capability_providers(session, ent.id)
    limitations = await _find_capability_limitations(session, ent.id)
    claims = await _find_capability_claims(session, ent.id)

    # Collect dependencies of each provider (a provider's REQUIRES edges).
    for p in providers:
        p_deps = await _find_provider_dependencies(session, p["entity_id"])
        p["dependencies"] = p_deps

    # Build claim assessments.
    claim_infos: list[ClaimAssessmentInfo] = []
    for c in claims:
        assessment = await _get_latest_assessment(session, c.id)
        claim_infos.append(_claim_assessment_info(c, assessment))

    # Collect all evidence refs across providers + claims.
    all_evidence_refs: set[str] = set()
    for p in providers:
        all_evidence_refs.update(p.get("evidence_refs", []))
    for c in claims:
        all_evidence_refs.update(_safe_json_loads(c.evidence_refs))
    evidence_bundles = await _collect_evidence_refs(session, list(all_evidence_refs))

    summary = _entity_summary_dict(ent)
    return CapabilityDetail(
        id=ent.id,
        kind=ent.kind,
        canonical_name=ent.canonical_name,
        canonical_uri=ent.canonical_uri,
        description=ent.description,
        aliases=summary["aliases"],
        attributes=summary["attributes"],
        providers=providers,
        limitations=limitations,
        claims=claim_infos,
        evidence_refs=evidence_bundles,
        policy_version=POLICY_VERSION,
        retrieved_at=_utcnow_iso(),
    )


async def find_capability_by_name(
    session: AsyncSession,
    name: str,
) -> CapabilityDetail | None:
    """Find a capability by exact canonical_name (case-insensitive).

    Used by the gap analyzer to resolve a required capability name to
    a capability entity. Returns None if no entity of kind=capability
    has this canonical_name.
    """
    if not name or not name.strip():
        return None
    stmt = select(EntityRow).where(
        EntityRow.kind == "capability",
        EntityRow.canonical_name.ilike(name.strip()),
    )
    ent = (await session.execute(stmt)).scalar_one_or_none()
    if ent is None:
        return None
    return await get_capability(session, ent.id)


async def find_capability_by_alias_or_fuzzy(
    session: AsyncSession,
    name: str,
) -> CapabilityDetail | None:
    """Find a capability by canonical_name, alias, or ILIKE substring.

    Used by the gap analyzer as a fallback when exact match fails.
    Note: a fuzzy match is recorded as ``match_quality="fuzzy"`` in the
    result and the gap analyzer does NOT promote it to SUPPORTED on
    lexical similarity alone (per §5 of the mission briefing).
    """
    if not name or not name.strip():
        return None
    q = name.strip().lower()

    # Try exact (case-insensitive) canonical_name first.
    stmt = select(EntityRow).where(
        EntityRow.kind == "capability",
        EntityRow.canonical_name.ilike(name.strip()),
    )
    ent = (await session.execute(stmt)).scalar_one_or_none()
    if ent is not None:
        detail = await get_capability(session, ent.id)
        if detail is not None:
            detail["match_quality"] = "exact"
        return detail

    # Try alias match.
    all_caps_stmt = select(EntityRow).where(EntityRow.kind == "capability")
    for r in (await session.execute(all_caps_stmt)).scalars().all():
        aliases = _safe_json_loads(r.aliases)
        if any(q == a.lower() for a in aliases):
            detail = await get_capability(session, r.id)
            if detail is not None:
                detail["match_quality"] = "alias"
            return detail

    # Try ILIKE substring (fuzzy -- NOT sufficient for SUPPORTED status).
    fuzzy_stmt = select(EntityRow).where(
        EntityRow.kind == "capability",
        EntityRow.canonical_name.ilike(f"%{name.strip()}%"),
    )
    ent = (await session.execute(fuzzy_stmt)).scalar_one_or_none()
    if ent is not None:
        detail = await get_capability(session, ent.id)
        if detail is not None:
            detail["match_quality"] = "fuzzy"
        return detail

    return None
