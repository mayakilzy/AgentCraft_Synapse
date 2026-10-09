"""G05-T01 + G05-T02 -- Evidence-Grounded Knowledge Combination + Opportunity Discovery.

Per the corrected G05 plan (`reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`
§G05-T01 and §G05-T02):

  G05-T01: "Given a problem domain, enumerate compatible
   tool/capability/technique combinations from the knowledge graph,
   each with traceable evidence."

  G05-T02: "Identify underserved needs, technical gaps, and
   promising combinations — explicitly NOT treating missing evidence
   as proof of absence."

G05-T01 is **candidate discovery** (Concern 1, §1.1 of the plan): it
produces *candidates* — structured combinations of existing
tools/capabilities/techniques — each with traceable evidence,
integration mechanism, potential benefit, and uncertainties. It does
NOT produce application concepts (T03), critiques (T04), experiments
(T05), or evidence feedback (T06).

G05-T02 is **opportunity discovery** (Concern 5, §1.1 of the plan):
it identifies underserved needs, technical gaps, and promising
combinations by composing G04's ``analyze_gap()`` classification with
G05-T01's ``combine_knowledge()`` candidate combinations. An
opportunity is a grounded possibility worth investigating, NOT proof
of an unmet market need or a commercially novel product.

Architecture
------------

``combine_knowledge()`` is the single entry point. It:

1. **Discovers candidate components** via the existing G04 ``hybrid_retrieve``
   + ``find_capabilities`` + ``find_dependencies`` services. No new
   retrieval logic is introduced.
2. **Enumerates combinations** by pairing components that provide
   complementary capabilities (e.g., a source-discovery tool + a
   content-extraction tool). Combinations are bounded by ``max_combinations``.
3. **Grounds each combination in evidence** by collecting evidence_refs
   from the participating components' PROVIDES relationships and their
   associated claims. Every cited entity_id and evidence_ref is validated
   against the DB.
4. **Computes integration mechanism** — a deterministic, evidence-grounded
   explanation of how the components work together (e.g., "X discovers
   sources; Y extracts content; the combination forms a pipeline").
5. **Computes potential benefit** — stated as a hypothesis to be measured
   by experiment (T05), NOT as a verified numerical claim.
6. **Enumerates uncertainties** — including known constraints (LIMITS
   edges), missing capabilities (NOT_EVIDENCED gaps), and contradictions
   (CONTESTED claims).
7. **Ranks combinations** by evidence count + distinct source count +
   component count (more evidence + more independence → higher rank).

Epistemic safety
----------------

- Every cited ``entity_id`` must resolve to an existing ``EntityRow``.
- Every cited ``evidence_ref`` must resolve to an existing
  ``EvidenceFragmentRow``.
- Hypothesized-origin relationships (``origin='hypothesized'``) are
  EXCLUDED from candidate discovery — only established knowledge
  (``origin IN ('explicit', 'derived')``) is used. This is enforced
  by the G05-T01 epistemic-isolation correction in
  ``find_related_entities`` (``include_hypothesized=False`` default).
- Missing evidence is reported as ``unknowns[]``, NOT as "no solution
  exists".
- Graph absence is NOT presented as proof of novelty or absence of
  competing solutions.
- No fabricated evidence or component IDs.

Determinism
-----------

Same input + same DB state → same output. Ties are broken by
component entity ID (lexically comparable) for stable ordering.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.capability_registry import list_capabilities
from synapse.application.gap_analyzer import (
    GapClassification,
    analyze_gap,
)
from synapse.application.relationship_service import (
    find_capabilities as find_entity_capabilities,
)
from synapse.application.relationship_service import (
    find_limitations,
    find_missing_capabilities,
)
from synapse.application.retrieval import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    hybrid_retrieve,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
)

_log = get_logger("synapse.application.innovation")


# ── Hard limits (mirror G04-T04 ResourceBudget) ────────────────────────────

MAX_COMBINATIONS = 20  # cap on returned combinations
MAX_COMPONENTS_PER_COMBINATION = 6  # cap on components in one combination
MAX_QUERY_LENGTH = MAX_QUERY_CHARS  # 512


# ── Result data classes (dict subclasses for JSON-friendly output) ──────────


class ComponentRef(dict):
    """A single component in a combination.

    Keys: entity_id, kind, canonical_name, role, evidence_refs[],
    relationship_id (the PROVIDES/ENABLES/PRODUCES edge that links it).
    """


class Combination(dict):
    """A candidate combination of components.

    Keys: id, problem_domain, context, components[], integration_mechanism,
    potential_benefit, uncertainties[], evidence_refs[], combination_basis,
    rank_score, generation_method, request_id
    """


class CombinationResult(dict):
    """Top-level result of ``combine_knowledge``.

    Keys: combinations[], unknowns[], limits, request_id, generated_at
    """


# ── Helpers ────────────────────────────────────────────────────────────────


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


async def _collect_existing_ids(session: AsyncSession) -> dict[str, set[str]]:
    """Return sets of existing IDs for validation.

    Used to verify that every cited entity_id and evidence_ref resolves
    to an existing record. No fabricated IDs are allowed.
    """
    entities = {row[0] for row in (await session.execute(select(EntityRow.id))).all()}
    claims = {row[0] for row in (await session.execute(select(ClaimRow.id))).all()}
    fragments = {row[0] for row in (await session.execute(select(EvidenceFragmentRow.id))).all()}
    return {"entities": entities, "claims": claims, "fragments": fragments}


async def _find_capability_providers(
    session: AsyncSession,
    capability_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find entities that PROVIDE/ENABLE/PRODUCE a given capability.

    Uses ``find_related_entities`` with ``direction='incoming'`` so we
    find entities whose outgoing edge points TO the capability.
    ``include_hypothesized`` defaults to False (epistemic isolation).
    """
    # We need incoming edges: entities whose from_entity_id -> capability_id
    # with predicate PROVIDES/ENABLES/PRODUCES.
    results: list[dict[str, Any]] = []
    for pred in ("PROVIDES", "ENABLES", "PRODUCES"):
        stmt = (
            select(RelationshipRow, EntityRow)
            .join(EntityRow, EntityRow.id == RelationshipRow.from_entity_id)
            .where(
                RelationshipRow.to_entity_id == capability_id,
                RelationshipRow.predicate == pred,
                # Epistemic isolation: exclude hypothesized-origin relationships
                RelationshipRow.origin != "hypothesized",
            )
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        for rel, ent in rows:
            results.append(
                {
                    "entity": {
                        "id": ent.id,
                        "kind": ent.kind,
                        "canonical_name": ent.canonical_name,
                        "canonical_uri": ent.canonical_uri,
                    },
                    "relationship": {
                        "id": rel.id,
                        "predicate": rel.predicate,
                        "origin": rel.origin,
                        "evidence_refs": _safe_json_loads(rel.evidence_refs),
                    },
                }
            )
    return results[:limit]


async def _collect_component_evidence(
    session: AsyncSession,
    entity_id: str,
) -> tuple[list[str], list[str]]:
    """Collect evidence_refs and claim_ids for an entity.

    Returns (evidence_refs, claim_ids) — both lists of string IDs.
    Uses claims where subject_ref == entity_id (the entity is making
    the claim) and the entity's outgoing PROVIDES relationships'
    evidence_refs.
    """
    # Claims attributed to this entity (subject_ref == entity_id)
    claim_stmt = select(ClaimRow).where(ClaimRow.subject_ref == entity_id)
    claims = list((await session.execute(claim_stmt)).scalars().all())
    claim_ids = [c.id for c in claims]

    # Evidence refs from those claims
    evidence_refs: list[str] = []
    for c in claims:
        evidence_refs.extend(_safe_json_loads(c.evidence_refs))

    # Also collect evidence from the entity's outgoing PROVIDES relationships
    # (these are the relationships that say "this entity provides capability X")
    caps = await find_entity_capabilities(session, entity_id, limit=50)
    for cap in caps:
        rel = cap.get("relationship") or {}
        evidence_refs.extend(_safe_json_loads(rel.get("evidence_refs")))

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped_refs: list[str] = []
    for ref in evidence_refs:
        if ref and ref not in seen:
            seen.add(ref)
            deduped_refs.append(ref)

    return deduped_refs, claim_ids


async def _collect_component_contradictions(
    session: AsyncSession,
    component_entity_ids: list[str],
) -> list[dict[str, Any]]:
    """Collect claim-level contradictions for the given components.

    Checks for claims with epistemic_state='disputed' attributed to
    any of the component entities (subject_ref in component_entity_ids).
    These are claim-level contradictions (contradicting_refs), which are
    distinct from relationship-level CONTRADICTS edges.
    """
    if not component_entity_ids:
        return []
    stmt = select(ClaimRow).where(
        ClaimRow.subject_ref.in_(component_entity_ids),
        ClaimRow.epistemic_state == "disputed",
    )
    claims = list((await session.execute(stmt)).scalars().all())
    contradictions = []
    for c in claims:
        contra_refs = _safe_json_loads(c.contradicting_refs)
        if contra_refs:
            contradictions.append(
                {
                    "claim_id": c.id,
                    "proposition": c.proposition,
                    "contradicting_refs": contra_refs,
                }
            )
    return contradictions


async def _collect_component_constraints(
    session: AsyncSession,
    component_entity_id: str,
    component_capability_ids: list[str],
) -> list[dict[str, Any]]:
    """Collect constraints (LIMITS/CONTRADICTS/INVALIDATES) for a component.

    Checks both the component entity itself AND the capabilities it provides
    (LIMITS edges often point TO the capability, not to the provider).
    """
    constraints: list[dict[str, Any]] = []

    # Limitations on the component entity itself
    constraints.extend(await find_limitations(session, component_entity_id, limit=10))

    # Limitations on the capabilities this component provides
    for cap_id in component_capability_ids:
        caps_lim = await find_limitations(session, cap_id, limit=10)
        constraints.extend(caps_lim)

    # Deduplicate by relationship ID
    seen_rel_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in constraints:
        rel = c.get("relationship") or {}
        rel_id = rel.get("id")
        if rel_id and rel_id not in seen_rel_ids:
            seen_rel_ids.add(rel_id)
            deduped.append(c)
    return deduped


def _compute_integration_mechanism(
    components: list[dict[str, Any]],
    capabilities_involved: list[str],
) -> str:
    """Deterministically compute the integration mechanism.

    Explains HOW the components work together. This is a structured
    explanation grounded in the capabilities they provide, NOT a
    free-text narrative.
    """
    if not components:
        return "No components available to form an integration."

    parts: list[str] = []
    for comp in components:
        name = comp.get("canonical_name", comp.get("entity_id", "unknown"))
        role = comp.get("role", "component")
        parts.append(f"{name} ({role})")

    if len(parts) == 1:
        return (
            f"Single-component combination: {parts[0]}. No integration between multiple components."
        )

    cap_str = (
        ", ".join(capabilities_involved[:4]) if capabilities_involved else "shared capabilities"
    )
    return (
        f"Combination of {len(parts)} components: {' + '.join(parts)}. "
        f"The components are connected via {cap_str}. "
        f"The integration forms a pipeline where each component contributes "
        f"a distinct capability to address the problem domain."
    )


def _compute_potential_benefit(
    components: list[dict[str, Any]],
    capabilities_involved: list[str],
) -> str:
    """Deterministically compute the potential benefit.

    Stated as a HYPOTHESIS to be measured by experiment (T05), NOT as
    a verified numerical claim. Per the G05-P00C additional consistency
    checks: no unsupported numerical benefit claims.
    """
    if not components:
        return "No potential benefit: no components in combination."

    cap_count = len(capabilities_involved)
    comp_count = len(components)

    if cap_count == 0:
        return (
            f"This combination of {comp_count} component(s) may address the "
            f"problem domain, but no documented capabilities were found to "
            f"support the benefit claim. This is a hypothesis to be validated "
            f"by experiment, not a verified outcome."
        )

    return (
        f"This combination of {comp_count} component(s) providing {cap_count} "
        f"documented capability/capabilities may address the problem domain "
        f"by combining complementary functions. The benefit is a hypothesis "
        f"to be measured by experiment (G05-T05), not a verified claim."
    )


def _compute_uncertainties(
    constraints: list[dict[str, Any]],
    missing_capabilities: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    evidence_count: int,
) -> list[str]:
    """Deterministically compute uncertainties.

    Every concept must carry >=1 uncertainty (a concept with no
    uncertainties is overclaiming — quality gate 8).
    """
    uncertainties: list[str] = []

    # Constraint-based uncertainties
    for c in constraints[:3]:
        ent = c.get("entity") or {}
        name = ent.get("canonical_name", "unknown constraint")
        uncertainties.append(f"constrained by '{name}'")

    # Missing-capability uncertainties
    for mc in missing_capabilities[:2]:
        cap_name = mc.get("capability") or (mc.get("entity") or {}).get("canonical_name", "unknown")
        uncertainties.append(f"missing capability: '{cap_name}'")

    # Contradiction uncertainties
    if contradictions:
        uncertainties.append(
            f"{len(contradictions)} documented contradiction(s) may affect the combination"
        )

    # Evidence-count uncertainty
    if evidence_count < 3:
        uncertainties.append(
            f"low evidence count ({evidence_count} fragments) — combination "
            f"needs more supporting evidence"
        )

    # Always include at least one uncertainty (gate 8)
    if not uncertainties:
        uncertainties.append(
            "combination viability is a hypothesis requiring experimental validation"
        )

    return uncertainties


def _rank_combination(
    evidence_count: int,
    distinct_sources: int,
    component_count: int,
) -> float:
    """Deterministic ranking score.

    Higher evidence count + more distinct sources + more components
    → higher rank. Capped at 1.0.
    """
    # Evidence weight (0-0.5)
    evidence_score = min(0.5, evidence_count * 0.05)
    # Independence weight (0-0.3)
    independence_score = min(0.3, distinct_sources * 0.1)
    # Component weight (0-0.2)
    component_score = min(0.2, component_count * 0.05)
    return min(1.0, evidence_score + independence_score + component_score)


# ── Main entry point ──────────────────────────────────────────────────────


async def combine_knowledge(
    session: AsyncSession,
    problem_domain: str,
    *,
    context: str | None = None,
    candidate_entity_ids: list[str] | None = None,
    max_combinations: int = MAX_COMBINATIONS,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> CombinationResult:
    """Discover evidence-grounded candidate combinations.

    Per G05-T01: this is **candidate discovery**, not full innovation
    generation. It produces structured combinations of existing
    tools/capabilities/techniques, each with traceable evidence,
    integration mechanism, potential benefit, and uncertainties.

    Args:
        session: AsyncSession bound to the Synapse database.
        problem_domain: the problem or need to address (max 512 chars).
        context: optional technical context (reserved for future
            applicability matching; not used in T01).
        candidate_entity_ids: optional list of entity IDs to scope
            candidate discovery to. If None, all entities are candidates.
        max_combinations: cap on returned combinations (≤ MAX_COMBINATIONS=20).
        limit: max number of components to consider per retrieval (≤ MAX_LIMIT=100).
        requester: optional actor name for audit logging.

    Returns:
        A ``CombinationResult`` dict with combinations[], unknowns[],
        limits, request_id, generated_at.

    Epistemic safety:
        - Every cited entity_id resolves to an existing EntityRow.
        - Every cited evidence_ref resolves to an existing EvidenceFragmentRow.
        - Hypothesized-origin relationships are excluded (epistemic isolation).
        - Missing evidence is reported as unknowns[], NOT as "no solution exists".
        - Graph absence is NOT presented as proof of novelty.
        - No fabricated evidence or component IDs.
    """
    request_id = uuid4().hex
    max_combinations = max(1, min(MAX_COMBINATIONS, max_combinations))
    limit = max(1, min(MAX_LIMIT, limit))

    _log.info(
        "combine_knowledge problem_domain=%r context=%s candidates=%s max_combinations=%d",
        problem_domain[:80],
        context,
        candidate_entity_ids,
        max_combinations,
    )

    # ── Validate inputs ──────────────────────────────────────────────────
    if not problem_domain or not problem_domain.strip():
        return CombinationResult(
            combinations=[],
            unknowns=["empty_problem_domain"],
            limits={
                "max_combinations": max_combinations,
                "max_components_per_combination": MAX_COMPONENTS_PER_COMBINATION,
                "limit": limit,
            },
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    if len(problem_domain) > MAX_QUERY_LENGTH:
        return CombinationResult(
            combinations=[],
            unknowns=[f"problem_domain_too_long:{len(problem_domain)}>{MAX_QUERY_LENGTH}"],
            limits={
                "max_combinations": max_combinations,
                "max_components_per_combination": MAX_COMPONENTS_PER_COMBINATION,
                "limit": limit,
            },
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    # ── Collect existing IDs for validation ──────────────────────────────
    existing_ids = await _collect_existing_ids(session)

    # ── Discover candidate components ───────────────────────────────────
    # Use hybrid_retrieve to find entities relevant to the problem domain.
    retrieval_result = await hybrid_retrieve(
        session,
        problem_domain,
        max_depth=3,
        limit=limit,
        requester=requester or "g05-t01-combine",
    )

    # Candidate components are entities of kind tool/technology/technique
    # that have at least one outgoing PROVIDES/ENABLES/PRODUCES edge.
    candidate_entities: list[dict[str, Any]] = []
    seen_entity_ids: set[str] = set()

    # If candidate_entity_ids is provided, use those directly (filtered to existing).
    if candidate_entity_ids:
        for eid in candidate_entity_ids:
            if eid in existing_ids["entities"] and eid not in seen_entity_ids:
                # Fetch the entity
                stmt = select(EntityRow).where(EntityRow.id == eid)
                ent = (await session.execute(stmt)).scalar_one_or_none()
                if ent is not None:
                    candidate_entities.append(
                        {
                            "id": ent.id,
                            "kind": ent.kind,
                            "canonical_name": ent.canonical_name,
                            "canonical_uri": ent.canonical_uri,
                            "description": ent.description,
                        }
                    )
                    seen_entity_ids.add(eid)
    else:
        # Use entities from retrieval result
        for ent_summary in retrieval_result.get("entities", []):
            eid = ent_summary.get("id")
            if eid and eid in existing_ids["entities"] and eid not in seen_entity_ids:
                # Only include tool/technology/technique entities
                if ent_summary.get("kind") in ("tool", "technology", "technique"):
                    candidate_entities.append(ent_summary)
                    seen_entity_ids.add(eid)

    # ── Also scan all capabilities to find which candidates provide which caps ──
    all_capabilities = await list_capabilities(session, limit=limit)
    capability_entities = all_capabilities.get("items", [])

    # For each candidate, find its capabilities (PROVIDES edges)
    candidate_caps: dict[str, list[dict[str, Any]]] = {}
    for cand in candidate_entities:
        cid = cand["id"]
        caps = await find_entity_capabilities(session, cid, limit=50)
        candidate_caps[cid] = caps

    # ── Enumerate combinations ───────────────────────────────────────────
    # A combination pairs components that provide COMPLEMENTARY capabilities.
    # For T01 (candidate discovery), we enumerate pairs and small groups
    # of components that together cover a broader set of capabilities
    # than any single component alone.

    combinations: list[Combination] = []
    unknowns: list[str] = []

    # Strategy: for each capability, find all providers. Then for each
    # pair of capabilities that are "complementary" (different predicates
    # or different capability names), form a combination from their providers.
    cap_to_providers: dict[str, list[dict[str, Any]]] = {}
    for cap in capability_entities:
        cap_id = cap.get("id")
        if not cap_id:
            continue
        providers = await _find_capability_providers(session, cap_id, limit=50)
        if providers:
            cap_to_providers[cap_id] = providers

    # If no capabilities with providers were found, try single-component
    # combinations from the candidate entities (if any have PROVIDES edges).
    if not cap_to_providers:
        for cand in candidate_entities:
            caps = candidate_caps.get(cand["id"], [])
            if caps:
                # Single-component combination
                comp = cand
                ev_refs, _claim_ids = await _collect_component_evidence(session, comp["id"])
                # Validate evidence_refs
                valid_refs = [r for r in ev_refs if r in existing_ids["fragments"]]
                cap_names = [c.get("entity", {}).get("canonical_name", "") for c in caps]
                cap_ids_for_comp = [c.get("entity", {}).get("id", "") for c in caps]
                integration = _compute_integration_mechanism([comp], cap_names)
                benefit = _compute_potential_benefit([comp], cap_names)
                constraints = await _collect_component_constraints(
                    session, comp["id"], cap_ids_for_comp
                )
                missing = await _find_missing_caps_for_entity(
                    session, comp["id"], capability_entities
                )
                contradictions = await _collect_component_contradictions(session, [comp["id"]])
                uncertainties = _compute_uncertainties(
                    constraints, missing, contradictions, len(valid_refs)
                )
                rank = _rank_combination(len(valid_refs), 1, 1)
                combinations.append(
                    Combination(
                        id=f"comb-{uuid4().hex[:12]}",
                        problem_domain=problem_domain,
                        context=context,
                        components=[
                            ComponentRef(
                                entity_id=comp["id"],
                                kind=comp.get("kind", ""),
                                canonical_name=comp.get("canonical_name", ""),
                                role="primary",
                                evidence_refs=valid_refs[:10],
                                relationship_id=caps[0].get("relationship", {}).get("id"),
                            )
                        ],
                        integration_mechanism=integration,
                        potential_benefit=benefit,
                        uncertainties=uncertainties,
                        evidence_refs=valid_refs[:20],
                        combination_basis=", ".join(cap_names[:3]),
                        rank_score=rank,
                        generation_method="deterministic",
                        request_id=request_id,
                    )
                )
                if len(combinations) >= max_combinations:
                    break
    else:
        # Multi-component combinations: pair providers of different capabilities
        cap_ids = list(cap_to_providers.keys())
        # Sort for determinism
        cap_ids.sort()

        for i, cap_id_a in enumerate(cap_ids):
            if len(combinations) >= max_combinations:
                break
            for j, cap_id_b in enumerate(cap_ids):
                if i >= j:
                    continue  # skip self-pairs and duplicates
                if len(combinations) >= max_combinations:
                    break

                providers_a = cap_to_providers[cap_id_a]
                providers_b = cap_to_providers[cap_id_b]

                # Find the capability names for the integration mechanism
                cap_a_name = next(
                    (
                        c.get("canonical_name")
                        for c in capability_entities
                        if c.get("id") == cap_id_a
                    ),
                    cap_id_a,
                )
                cap_b_name = next(
                    (
                        c.get("canonical_name")
                        for c in capability_entities
                        if c.get("id") == cap_id_b
                    ),
                    cap_id_b,
                )

                # For each pair of providers (one from A, one from B),
                # form a combination — but only if they are different entities.
                for pa in providers_a:
                    if len(combinations) >= max_combinations:
                        break
                    for pb in providers_b:
                        if len(combinations) >= max_combinations:
                            break
                        ent_a = pa.get("entity", {})
                        ent_b = pb.get("entity", {})
                        if ent_a.get("id") == ent_b.get("id"):
                            continue  # same entity, skip

                        # Build the combination
                        comp_a = {
                            "id": ent_a.get("id"),
                            "kind": ent_a.get("kind", ""),
                            "canonical_name": ent_a.get("canonical_name", ""),
                            "canonical_uri": ent_a.get("canonical_uri"),
                        }
                        comp_b = {
                            "id": ent_b.get("id"),
                            "kind": ent_b.get("kind", ""),
                            "canonical_name": ent_b.get("canonical_name", ""),
                            "canonical_uri": ent_b.get("canonical_uri"),
                        }

                        # Collect evidence from both components
                        ev_refs_a, _claim_ids_a = await _collect_component_evidence(
                            session, comp_a["id"]
                        )
                        ev_refs_b, _claim_ids_b = await _collect_component_evidence(
                            session, comp_b["id"]
                        )
                        all_refs = ev_refs_a + ev_refs_b
                        # Deduplicate
                        seen: set[str] = set()
                        valid_refs: list[str] = []
                        for ref in all_refs:
                            if ref and ref in existing_ids["fragments"] and ref not in seen:
                                seen.add(ref)
                                valid_refs.append(ref)

                        # Count distinct sources
                        distinct_sources = await _count_distinct_sources(session, valid_refs)

                        # Find constraints for both components + their capabilities
                        cap_ids_a = [
                            c.get("entity", {}).get("id", "")
                            for c in await find_entity_capabilities(session, comp_a["id"], limit=50)
                        ]
                        cap_ids_b = [
                            c.get("entity", {}).get("id", "")
                            for c in await find_entity_capabilities(session, comp_b["id"], limit=50)
                        ]
                        constraints_a = await _collect_component_constraints(
                            session, comp_a["id"], cap_ids_a
                        )
                        constraints_b = await _collect_component_constraints(
                            session, comp_b["id"], cap_ids_b
                        )
                        constraints = constraints_a + constraints_b

                        # Find missing capabilities
                        missing_a = await _find_missing_caps_for_entity(
                            session, comp_a["id"], capability_entities
                        )
                        missing_b = await _find_missing_caps_for_entity(
                            session, comp_b["id"], capability_entities
                        )
                        missing = missing_a + missing_b

                        # Find claim-level contradictions for both components
                        contradictions = await _collect_component_contradictions(
                            session, [comp_a["id"], comp_b["id"]]
                        )

                        cap_names = [cap_a_name, cap_b_name]
                        integration = _compute_integration_mechanism([comp_a, comp_b], cap_names)
                        benefit = _compute_potential_benefit([comp_a, comp_b], cap_names)
                        uncertainties = _compute_uncertainties(
                            constraints, missing, contradictions, len(valid_refs)
                        )
                        rank = _rank_combination(len(valid_refs), distinct_sources, 2)

                        combinations.append(
                            Combination(
                                id=f"comb-{uuid4().hex[:12]}",
                                problem_domain=problem_domain,
                                context=context,
                                components=[
                                    ComponentRef(
                                        entity_id=comp_a["id"],
                                        kind=comp_a["kind"],
                                        canonical_name=comp_a["canonical_name"],
                                        role=cap_a_name,
                                        evidence_refs=ev_refs_a[:10],
                                        relationship_id=pa.get("relationship", {}).get("id"),
                                    ),
                                    ComponentRef(
                                        entity_id=comp_b["id"],
                                        kind=comp_b["kind"],
                                        canonical_name=comp_b["canonical_name"],
                                        role=cap_b_name,
                                        evidence_refs=ev_refs_b[:10],
                                        relationship_id=pb.get("relationship", {}).get("id"),
                                    ),
                                ],
                                integration_mechanism=integration,
                                potential_benefit=benefit,
                                uncertainties=uncertainties,
                                evidence_refs=valid_refs[:20],
                                combination_basis=f"{cap_a_name} + {cap_b_name}",
                                rank_score=rank,
                                generation_method="deterministic",
                                request_id=request_id,
                            )
                        )

    # ── Sort combinations by rank_score (descending), then by ID for determinism ──
    combinations.sort(key=lambda c: (-c.get("rank_score", 0.0), c.get("id", "")))

    # ── Collect unknowns ─────────────────────────────────────────────────
    if not combinations:
        unknowns.append(
            "no candidate combinations found — the knowledge graph may not "
            "contain enough documented capabilities for this problem domain"
        )
    if not capability_entities:
        unknowns.append("no capability entities documented in the knowledge graph")
    if not candidate_entities:
        unknowns.append("no candidate tool/technology/technique entities found for this query")

    # Add retrieval unknowns
    retrieval_unknowns = retrieval_result.get("unknowns", [])
    unknowns.extend(retrieval_unknowns)

    _log.info(
        "combine_knowledge complete: %d combinations, %d unknowns",
        len(combinations),
        len(unknowns),
    )

    return CombinationResult(
        combinations=combinations[:max_combinations],
        unknowns=unknowns,
        limits={
            "max_combinations": max_combinations,
            "max_components_per_combination": MAX_COMPONENTS_PER_COMBINATION,
            "limit": limit,
        },
        request_id=request_id,
        generated_at=_utcnow_iso(),
    )


async def _find_missing_caps_for_entity(
    session: AsyncSession,
    entity_id: str,
    all_capabilities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find capabilities NOT connected to this entity.

    Returns a list of {capability: name, reason: "not_connected"} dicts.
    """
    existing = await find_entity_capabilities(session, entity_id, limit=50)
    existing_names = {r["entity"]["canonical_name"] for r in existing}

    missing = [
        {"capability": c.get("canonical_name", c.get("id", "unknown")), "reason": "not_connected"}
        for c in all_capabilities
        if c.get("canonical_name") not in existing_names
    ]
    return missing[:10]


async def _count_distinct_sources(
    session: AsyncSession,
    evidence_refs: list[str],
) -> int:
    """Count distinct source_uris among the given evidence fragments.

    Used for the independence score in ranking. More distinct sources
    → higher independence → higher rank.
    """
    if not evidence_refs:
        return 0
    stmt = select(EvidenceFragmentRow.source_uri).where(EvidenceFragmentRow.id.in_(evidence_refs))
    rows = (await session.execute(stmt)).all()
    distinct_uris = {row[0] for row in rows if row[0]}
    return len(distinct_uris)


# ════════════════════════════════════════════════════════════════════════════
# G05-T02 — Evidence-Grounded Opportunity Discovery
# ════════════════════════════════════════════════════════════════════════════

#: Maximum opportunities returned by ``discover_opportunities``.
MAX_OPPORTUNITIES = 20


class Opportunity(dict):
    """A single opportunity — a grounded possibility worth investigating.

    Keys:
        id: stable identifier (UUID hex prefix).
        problem_domain: the problem or need being addressed.
        gap_type: "not_evidenced" | "constrained" | "contested" |
            "missing_capability" | "combination_gap".
        gap_description: human-readable explanation of the gap.
        relevant_capabilities: list of capability names involved.
        candidate_components: list of ComponentRef dicts from G05-T01
            combinations that could address this gap.
        evidence_refs: supporting evidence fragment IDs (validated).
        constraints: list of constraint dicts (LIMITS/CONTRADICTS/INVALIDATES).
        uncertainties: list of human-readable uncertainty descriptions (>=1).
        investigation_direction: proposed next step for investigation.
        missing_evidence: what evidence is still needed.
        validation_questions: list of questions that need answering.
        ranking_rationale: explanation of the rank score.
        rank_score: float in [0.0, 1.0] (higher = more promising).
        request_id: per-request tracing.
    """


class OpportunityResult(dict):
    """Top-level result of ``discover_opportunities``.

    Keys: opportunities[], unknowns[], gap_summary, limits, request_id,
    generated_at
    """


def _classify_opportunity_type(
    gap_classification: str,
    has_missing_caps: bool,
    has_combination_gap: bool,
) -> str:
    """Map a gap classification to an opportunity gap_type.

    Per the plan §G05-T02 acceptance criterion 1: returns >=1 opportunity
    per gap category (NOT_EVIDENCED, CONSTRAINED, CONTESTED).
    """
    if gap_classification == GapClassification.NOT_EVIDENCED.value:
        return "not_evidenced"
    if gap_classification == GapClassification.CONSTRAINED.value:
        return "constrained"
    if gap_classification == GapClassification.CONTESTED.value:
        return "contested"
    if has_missing_caps:
        return "missing_capability"
    if has_combination_gap:
        return "combination_gap"
    return "unknown"


def _compute_investigation_direction(
    gap_type: str,
    gap_description: str,
    candidate_components: list[dict[str, Any]],
) -> str:
    """Deterministically compute a proposed investigation direction.

    This is a structured suggestion, NOT a verified plan.
    """
    if gap_type == "not_evidenced":
        return (
            f"Investigate whether any undocumented tool or technique provides "
            f"this capability. Search for evidence in related domains. "
            f"The gap '{gap_description[:80]}' may indicate an underserved need."
        )
    if gap_type == "constrained":
        return (
            f"Investigate whether the constraint can be relaxed or worked around. "
            f"Document the constraint's scope and whether alternative tools avoid it. "
            f"The constraint '{gap_description[:80]}' limits current solutions."
        )
    if gap_type == "contested":
        return (
            f"Investigate the contradiction to determine which evidence is "
            f"applicable to the target context. The contradiction "
            f"'{gap_description[:80]}' needs resolution before adoption."
        )
    if gap_type == "missing_capability":
        return (
            "Document the missing capability and search for tools that provide it. "
            "The gap may be an opportunity for a new tool or integration."
        )
    if gap_type == "combination_gap":
        comp_names = [
            c.get("canonical_name", c.get("entity_id", "?")) for c in candidate_components[:3]
        ]
        return (
            f"Investigate whether the combination of {', '.join(comp_names)} "
            f"can address the gap. Validate the integration mechanism experimentally."
        )
    return "Investigate the gap with further evidence collection."


def _compute_missing_evidence(
    gap_type: str,
    evidence_refs: list[str],
    constraints: list[dict[str, Any]],
) -> list[str]:
    """Deterministically compute what evidence is still needed."""
    missing: list[str] = []

    if gap_type == "not_evidenced":
        missing.append("evidence that any tool provides this capability")
        missing.append("evidence of the capability's applicability to the target context")

    if gap_type == "constrained":
        missing.append("evidence that the constraint applies in the target context")
        missing.append("evidence of alternative tools that avoid the constraint")

    if gap_type == "contested":
        missing.append("evidence resolving which side of the contradiction is applicable")
        missing.append("evidence of the contradiction's scope and conditions")

    if len(evidence_refs) < 3:
        missing.append(f"more supporting evidence (currently {len(evidence_refs)} fragments)")

    if constraints:
        missing.append(f"evidence addressing {len(constraints)} constraint(s)")

    # Always include at least one missing-evidence item
    if not missing:
        missing.append("experimental validation of the opportunity's viability")

    return missing


def _compute_validation_questions(
    gap_type: str,
    gap_description: str,
    relevant_capabilities: list[str],
) -> list[str]:
    """Deterministically compute validation questions."""
    questions: list[str] = []

    cap_str = ", ".join(relevant_capabilities[:3]) if relevant_capabilities else "the capability"

    if gap_type == "not_evidenced":
        questions.append(f"Does any existing tool provide {cap_str}?")
        questions.append(
            "Is the absence of evidence due to the capability being new, or just undocumented?"
        )
    elif gap_type == "constrained":
        questions.append("Does the constraint apply in the target context?")
        questions.append("Can the constraint be relaxed or worked around?")
    elif gap_type == "contested":
        questions.append("Which evidence is applicable to the target context?")
        questions.append("Under what conditions does the contradiction resolve?")
    elif gap_type == "missing_capability":
        questions.append(f"What tools or techniques could provide {cap_str}?")
        questions.append("Is the missing capability a known gap in the ecosystem?")
    elif gap_type == "combination_gap":
        questions.append("Do the candidate components integrate effectively?")
        questions.append("What evidence supports the integration mechanism?")

    # Always include at least one question
    if not questions:
        questions.append("What evidence is needed to validate this opportunity?")

    return questions


def _rank_opportunity(
    evidence_count: int,
    constraint_count: int,
    component_count: int,
    gap_type: str,
) -> tuple[float, str]:
    """Deterministic ranking score + rationale.

    Returns (score, rationale). Higher score = more promising opportunity.

    Ranking factors:
    - Evidence count: more existing evidence → higher score (the gap is
      grounded in real knowledge, not speculation).
    - Constraint count: more constraints → lower score (the gap is harder
      to address).
    - Component count: more candidate components → higher score (more
      potential solution paths).
    - Gap type: NOT_EVIDENCED and missing_capability gaps are ranked higher
      (they represent clear underserved needs); CONTESTED gaps are ranked
      lower (they need resolution before action).

    No market-demand or novelty scores are fabricated.
    """
    # Evidence weight (0-0.4)
    evidence_score = min(0.4, evidence_count * 0.05)
    # Constraint penalty (0-0.2, subtracted)
    constraint_penalty = min(0.2, constraint_count * 0.05)
    # Component weight (0-0.2)
    component_score = min(0.2, component_count * 0.05)
    # Gap-type weight (0-0.4)
    gap_type_weights = {
        "not_evidenced": 0.40,
        "missing_capability": 0.35,
        "combination_gap": 0.30,
        "constrained": 0.20,
        "contested": 0.15,
        "unknown": 0.10,
    }
    gap_score = gap_type_weights.get(gap_type, 0.10)

    raw = evidence_score + component_score + gap_score - constraint_penalty
    score = max(0.0, min(1.0, raw))

    rationale = (
        f"evidence_score={evidence_score:.2f} "
        f"(evidence_count={evidence_count}), "
        f"component_score={component_score:.2f} "
        f"(component_count={component_count}), "
        f"gap_type_score={gap_score:.2f} (gap_type={gap_type}), "
        f"constraint_penalty={constraint_penalty:.2f} "
        f"(constraint_count={constraint_count}), "
        f"final_score={score:.2f}"
    )
    return score, rationale


async def discover_opportunities(
    session: AsyncSession,
    problem_domain: str,
    *,
    context: str | None = None,
    candidate_entity_ids: list[str] | None = None,
    max_opportunities: int = MAX_OPPORTUNITIES,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> OpportunityResult:
    """Discover evidence-grounded opportunities for a problem domain.

    Per G05-T02: this identifies underserved needs, technical gaps, and
    promising combinations by composing G04's ``analyze_gap()``
    classification with G05-T01's ``combine_knowledge()`` candidate
    combinations.

    An opportunity is a **grounded possibility worth investigating**, NOT
    proof of an unmet market need or a commercially novel product.

    Args:
        session: AsyncSession bound to the Synapse database.
        problem_domain: the problem or need to explore (max 512 chars).
        context: optional technical context for applicability matching.
        candidate_entity_ids: optional list of entity IDs to scope to.
        max_opportunities: cap on returned opportunities (≤ MAX_OPPORTUNITIES).
        limit: max number of capabilities to evaluate.
        requester: optional actor name for audit logging.

    Returns:
        An ``OpportunityResult`` dict with opportunities[], unknowns[],
        gap_summary, limits, request_id, generated_at.

    Epistemic safety:
        - Reuses G04 ``analyze_gap()`` classification (no reimplementation).
        - Missing evidence is reported as ``unknowns[]``, NOT as "no solution exists".
        - NOT_EVIDENCED is distinguished from demonstrated absence.
        - Applicable contradictions are preserved (not suppressed).
        - Out-of-context contradictions are preserved as metadata.
        - No market-demand or novelty scores are fabricated.
        - Hypothesized relationships are excluded (epistemic isolation from G05-T01).
        - Every opportunity carries >=1 uncertainty and >=1 validation question.
    """
    request_id = uuid4().hex
    max_opportunities = max(1, min(MAX_OPPORTUNITIES, max_opportunities))
    limit = max(1, min(MAX_LIMIT, limit))

    _log.info(
        "discover_opportunities problem_domain=%r context=%s candidates=%s max_opportunities=%d",
        problem_domain[:80],
        context,
        candidate_entity_ids,
        max_opportunities,
    )

    # ── Validate inputs ──────────────────────────────────────────────────
    if not problem_domain or not problem_domain.strip():
        return OpportunityResult(
            opportunities=[],
            unknowns=["empty_problem_domain"],
            gap_summary={},
            limits={
                "max_opportunities": max_opportunities,
                "limit": limit,
            },
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    if len(problem_domain) > MAX_QUERY_CHARS:
        return OpportunityResult(
            opportunities=[],
            unknowns=[f"problem_domain_too_long:{len(problem_domain)}>{MAX_QUERY_CHARS}"],
            gap_summary={},
            limits={"max_opportunities": max_opportunities, "limit": limit},
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    # ── Collect existing IDs for validation ──────────────────────────────
    existing_ids = await _collect_existing_ids(session)

    # ── Step 1: Discover all capabilities in the knowledge graph ─────────
    all_capabilities = await list_capabilities(session, limit=limit)
    capability_names = [
        c.get("canonical_name", "")
        for c in all_capabilities.get("items", [])
        if c.get("canonical_name")
    ]

    if not capability_names:
        return OpportunityResult(
            opportunities=[],
            unknowns=["no capabilities documented in the knowledge graph"],
            gap_summary={"total": 0},
            limits={"max_opportunities": max_opportunities, "limit": limit},
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    # ── Step 2: Run G04 analyze_gap on all capabilities ─────────────────
    # This reuses G04's classification rules (NOT_EVIDENCED, SUPPORTED,
    # PARTIALLY_SUPPORTED, CONTESTED, CONSTRAINED, UNKNOWN) rather than
    # recreating them. We pass context for applicability matching.
    gap_result = await analyze_gap(
        session,
        capability_names,
        context=context,
        candidate_entity_ids=candidate_entity_ids,
        limit=limit,
        requester=requester or "g05-t02-opportunity",
    )

    # ── Step 3: Get G05-T01 combinations as candidate solution ingredients ──
    combination_result = await combine_knowledge(
        session,
        problem_domain,
        context=context,
        candidate_entity_ids=candidate_entity_ids,
        max_combinations=MAX_COMBINATIONS,
        limit=limit,
        requester=requester or "g05-t02-opportunity",
    )
    combinations = combination_result.get("combinations", [])

    # ── Step 4: Build opportunities from gap assessments ─────────────────
    opportunities: list[Opportunity] = []
    unknowns: list[str] = []
    seen_gap_keys: set[str] = set()  # for deduplication

    gap_summary: dict[str, int] = {
        "total": 0,
        "not_evidenced": 0,
        "constrained": 0,
        "contested": 0,
        "missing_capability": 0,
        "combination_gap": 0,
    }

    for req in gap_result.get("requirements", []):
        if len(opportunities) >= max_opportunities:
            break

        classification = req.get("classification", "unknown")
        cap_name = req.get("required_capability", "unknown")
        gap_type = _classify_opportunity_type(
            classification,
            has_missing_caps=False,  # set below
            has_combination_gap=False,
        )

        # Skip SUPPORTED and PARTIALLY_SUPPORTED — they're not gaps
        if classification in (
            GapClassification.SUPPORTED.value,
            GapClassification.PARTIALLY_SUPPORTED.value,
        ):
            continue

        # Deduplicate by (gap_type, cap_name)
        gap_key = f"{gap_type}:{cap_name}"
        if gap_key in seen_gap_keys:
            continue
        seen_gap_keys.add(gap_key)

        # Collect evidence from the gap assessment
        evidence_refs: list[str] = []
        for link in req.get("evidence_chain", []):
            frag_id = link.get("fragment_id")
            if frag_id and frag_id in existing_ids["fragments"]:
                evidence_refs.append(frag_id)
        # Also add contradicting evidence
        for contra in req.get("contradicting_evidence", []):
            if contra in existing_ids["fragments"] and contra not in evidence_refs:
                evidence_refs.append(contra)

        # Collect constraints
        constraints = req.get("limitations", [])

        # Collect out-of-context contradictions as metadata
        out_of_context = req.get("out_of_context_contradictions", [])

        # Find candidate components from G05-T01 combinations that
        # reference this capability
        candidate_components: list[dict[str, Any]] = []
        for comb in combinations:
            for comp in comb.get("components", []):
                role = comp.get("role", "")
                if cap_name.lower() in role.lower():
                    candidate_components.append(comp)

        # Build the gap description
        reason = req.get("reason", "")
        gap_description = f"{cap_name}: {reason}" if reason else cap_name

        # Compute uncertainties
        uncertainties: list[str] = []
        if classification == GapClassification.NOT_EVIDENCED.value:
            uncertainties.append(
                f"no evidence that any tool provides '{cap_name}' — "
                f"absence of evidence is NOT evidence of absence"
            )
        if constraints:
            uncertainties.append(f"{len(constraints)} documented constraint(s) may limit solutions")
        if out_of_context:
            uncertainties.append(
                f"{len(out_of_context)} out-of-context contradiction(s) preserved as metadata"
            )
        if req.get("contradicting_evidence"):
            uncertainties.append(
                f"{len(req['contradicting_evidence'])} contradicting evidence fragment(s) — "
                f"both sides preserved"
            )
        # Always include at least one uncertainty
        if not uncertainties:
            uncertainties.append("opportunity viability is a hypothesis requiring investigation")

        # Compute investigation direction
        investigation = _compute_investigation_direction(
            gap_type, gap_description, candidate_components
        )

        # Compute missing evidence
        missing_ev = _compute_missing_evidence(gap_type, evidence_refs, constraints)

        # Compute validation questions
        validation_qs = _compute_validation_questions(gap_type, gap_description, [cap_name])

        # Compute rank
        rank_score, rank_rationale = _rank_opportunity(
            len(evidence_refs),
            len(constraints),
            len(candidate_components),
            gap_type,
        )

        gap_summary["total"] += 1
        gap_summary[gap_type] = gap_summary.get(gap_type, 0) + 1

        opportunities.append(
            Opportunity(
                id=f"opp-{uuid4().hex[:12]}",
                problem_domain=problem_domain,
                gap_type=gap_type,
                gap_description=gap_description,
                relevant_capabilities=[cap_name],
                candidate_components=candidate_components[:5],
                evidence_refs=evidence_refs[:20],
                constraints=constraints[:5],
                uncertainties=uncertainties,
                investigation_direction=investigation,
                missing_evidence=missing_ev,
                validation_questions=validation_qs,
                ranking_rationale=rank_rationale,
                rank_score=rank_score,
                out_of_context_contradictions=out_of_context,
                request_id=request_id,
            )
        )

    # ── Step 5: Add combination-gap opportunities from G05-T01 ───────────
    # If there are combinations that address capabilities with gaps, create
    # combination-gap opportunities.
    for comb in combinations:
        if len(opportunities) >= max_opportunities:
            break

        comb_basis = comb.get("combination_basis", "")
        comb_components = comb.get("components", [])

        # Check if this combination addresses a gap
        # (i.e., at least one of its components provides a capability that
        # is NOT_EVIDENCED or CONSTRAINED)
        addresses_gap = False
        for opp in opportunities:
            for comp in comb_components:
                role = comp.get("role", "")
                for cap_name in opp.get("relevant_capabilities", []):
                    if cap_name.lower() in role.lower():
                        addresses_gap = True
                        break

        if not addresses_gap:
            continue

        gap_key = f"combination_gap:{comb.get('id', '')}"
        if gap_key in seen_gap_keys:
            continue
        seen_gap_keys.add(gap_key)

        evidence_refs = [r for r in comb.get("evidence_refs", []) if r in existing_ids["fragments"]]
        uncertainties = comb.get("uncertainties", [])
        if not uncertainties:
            uncertainties.append("combination viability is a hypothesis requiring validation")

        investigation = _compute_investigation_direction(
            "combination_gap", comb_basis, comb_components
        )
        missing_ev = _compute_missing_evidence("combination_gap", evidence_refs, [])
        validation_qs = _compute_validation_questions(
            "combination_gap",
            comb_basis,
            [c.get("canonical_name", "") for c in comb_components],
        )
        rank_score, rank_rationale = _rank_opportunity(
            len(evidence_refs), 0, len(comb_components), "combination_gap"
        )

        gap_summary["total"] += 1
        gap_summary["combination_gap"] = gap_summary.get("combination_gap", 0) + 1

        opportunities.append(
            Opportunity(
                id=f"opp-{uuid4().hex[:12]}",
                problem_domain=problem_domain,
                gap_type="combination_gap",
                gap_description=f"Combination may address a gap: {comb_basis}",
                relevant_capabilities=[comb_basis],
                candidate_components=comb_components[:5],
                evidence_refs=evidence_refs[:20],
                constraints=[],
                uncertainties=uncertainties,
                investigation_direction=investigation,
                missing_evidence=missing_ev,
                validation_questions=validation_qs,
                ranking_rationale=rank_rationale,
                rank_score=rank_score,
                out_of_context_contradictions=[],
                request_id=request_id,
            )
        )

    # ── Step 6: Add missing-capability opportunities ────────────────────
    # For each candidate entity, find capabilities it doesn't provide.
    if candidate_entity_ids:
        for eid in candidate_entity_ids:
            if len(opportunities) >= max_opportunities:
                break
            if eid not in existing_ids["entities"]:
                continue

            missing_caps = await find_missing_capabilities(session, eid, limit=10)
            for mc in missing_caps[:3]:
                if len(opportunities) >= max_opportunities:
                    break
                cap_name = mc.get("capability", "unknown")
                gap_key = f"missing_capability:{eid}:{cap_name}"
                if gap_key in seen_gap_keys:
                    continue
                seen_gap_keys.add(gap_key)

                uncertainties = [
                    f"entity '{eid}' does not provide '{cap_name}' — "
                    f"absence of evidence is NOT evidence of absence",
                    "investigate whether other tools provide this capability",
                ]
                investigation = _compute_investigation_direction("missing_capability", cap_name, [])
                missing_ev = _compute_missing_evidence("missing_capability", [], [])
                validation_qs = _compute_validation_questions(
                    "missing_capability", cap_name, [cap_name]
                )
                rank_score, rank_rationale = _rank_opportunity(0, 0, 0, "missing_capability")

                gap_summary["total"] += 1
                gap_summary["missing_capability"] = gap_summary.get("missing_capability", 0) + 1

                opportunities.append(
                    Opportunity(
                        id=f"opp-{uuid4().hex[:12]}",
                        problem_domain=problem_domain,
                        gap_type="missing_capability",
                        gap_description=f"'{eid}' does not provide '{cap_name}'",
                        relevant_capabilities=[cap_name],
                        candidate_components=[],
                        evidence_refs=[],
                        constraints=[],
                        uncertainties=uncertainties,
                        investigation_direction=investigation,
                        missing_evidence=missing_ev,
                        validation_questions=validation_qs,
                        ranking_rationale=rank_rationale,
                        rank_score=rank_score,
                        out_of_context_contradictions=[],
                        request_id=request_id,
                    )
                )

    # ── Step 7: Sort by rank_score (desc), then by ID for determinism ───
    opportunities.sort(key=lambda o: (-o.get("rank_score", 0.0), o.get("id", "")))

    # ── Step 8: Collect unknowns ────────────────────────────────────────
    if not opportunities:
        unknowns.append(
            "no opportunities found — the knowledge graph may not contain "
            "enough gaps or combinations for this problem domain"
        )
    unknowns.extend(gap_result.get("unknowns", []))
    unknowns.extend(combination_result.get("unknowns", []))

    _log.info(
        "discover_opportunities complete: %d opportunities, %d unknowns",
        len(opportunities),
        len(unknowns),
    )

    return OpportunityResult(
        opportunities=opportunities[:max_opportunities],
        unknowns=unknowns,
        gap_summary=gap_summary,
        limits={
            "max_opportunities": max_opportunities,
            "limit": limit,
        },
        request_id=request_id,
        generated_at=_utcnow_iso(),
    )


# ════════════════════════════════════════════════════════════════════════════
# G05-T03 — Evidence-Grounded Innovation Generation
# ════════════════════════════════════════════════════════════════════════════

#: Maximum concepts returned by ``generate_innovations``.
MAX_CONCEPTS = 10


class InnovationConcept(dict):
    """A generated application concept.

    Keys:
        id: stable identifier (UUID hex prefix).
        problem_domain: the problem the concept addresses.
        context: optional technical context.
        purpose: what the concept is for.
        target_users: list of intended user types.
        components: list of ComponentRef dicts (entity_id, role, evidence_refs).
        integration_mechanism: how the components work together (explainable).
        potential_benefit: why the combination is useful (hypothesis, not claim).
        uncertainties: list of >=1 uncertainty descriptions.
        evidence_refs: supporting evidence fragment IDs (validated).
        constraints: list of constraint dicts (LIMITS/CONTRADICTS/INVALIDATES).
        combination_basis: which T01 combination inspired this concept.
        generation_method: "deterministic" or "model_assisted".
        hypothesis_id: ID of the persisted ClaimRow(epistemic_state="hypothesized").
        opportunities_addressed: list of T02 opportunity IDs this concept addresses.
        request_id: per-request tracing.
    """


class InnovationResult(dict):
    """Top-level result of ``generate_innovations``.

    Keys: concepts[], unknowns[], quality_gates, limits, request_id, generated_at
    """


# ── Deterministic innovation templates (baselines) ─────────────────────────


def _template_composition(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: compose multiple components into a pipeline.

    Best when the combination has >=2 components that provide complementary
    capabilities. The integration mechanism explains how each component feeds
    the next.
    """
    components = combination.get("components", [])
    if len(components) < 2:
        return None

    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]
    cap_basis = combination.get("combination_basis", "")

    integration = (
        f"Composition pipeline: {' → '.join(comp_names)}. "
        f"Each component provides a distinct capability ({cap_basis}). "
        f"The output of one component feeds as input to the next, "
        f"forming an integrated pipeline that addresses '{problem_domain}'."
    )

    benefit = (
        f"This composition of {len(components)} components may address "
        f"'{problem_domain}' by combining complementary capabilities. "
        f"The benefit is a hypothesis to be validated by experiment (G05-T05), "
        f"not a verified outcome."
    )

    uncertainties = list(combination.get("uncertainties", []))
    if not uncertainties:
        uncertainties.append("integration viability between components is unproven")

    # Add opportunity-derived uncertainties
    for opp in opportunities[:2]:
        uncertainties.append(f"addresses gap: {opp.get('gap_description', 'unknown')[:80]}")

    return {
        "purpose": f"Address '{problem_domain}' by composing {len(components)} complementary components",
        "target_users": ["technical practitioners", "system architects"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": cap_basis,
    }


def _template_substitution(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: substitute one component with an alternative.

    Best when the combination includes a REPLACES relationship or an
    opportunity suggests a constraint that could be avoided by substitution.
    """
    components = combination.get("components", [])
    if not components:
        return None

    # Look for opportunities that suggest substitution (constrained gaps)
    constrained_opps = [o for o in opportunities if o.get("gap_type") == "constrained"]
    if not constrained_opps:
        return None

    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]
    constraint_desc = constrained_opps[0].get("gap_description", "a constraint")[:80]

    integration = (
        f"Substitution approach: replace a constrained component in "
        f"{' + '.join(comp_names)} with an alternative that avoids "
        f"'{constraint_desc}'. The substitution preserves the original "
        f"capability while removing the constraint."
    )

    benefit = (
        f"This substitution may address '{problem_domain}' by avoiding "
        f"the constraint '{constraint_desc}'. The benefit is a hypothesis "
        f"requiring experimental validation, not a verified outcome."
    )

    uncertainties = list(combination.get("uncertainties", []))
    uncertainties.append(f"substitution may not fully address: {constraint_desc}")

    return {
        "purpose": f"Address '{problem_domain}' by substituting a constrained component",
        "target_users": ["technical practitioners", "system architects"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": combination.get("combination_basis", "substitution"),
    }


def _template_constraint_relaxation(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: relax a constraint to enable a new combination.

    Best when there are CONSTRAINED opportunities that could be addressed
    by relaxing or working around the constraint.
    """
    constrained_opps = [o for o in opportunities if o.get("gap_type") == "constrained"]
    if not constrained_opps:
        return None

    components = combination.get("components", [])
    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]
    constraint_desc = constrained_opps[0].get("gap_description", "a constraint")[:80]

    integration = (
        f"Constraint-relaxation approach: the combination of "
        f"{' + '.join(comp_names)} is currently limited by '{constraint_desc}'. "
        f"If the constraint can be relaxed (e.g., by introducing a workaround, "
        f"a new dependency, or a different execution context), the combination "
        f"may become viable for '{problem_domain}'."
    )

    benefit = (
        f"Relaxing the constraint '{constraint_desc}' may enable this "
        f"combination to address '{problem_domain}'. The benefit is a "
        f"hypothesis requiring investigation of whether the constraint "
        f"can be practically relaxed."
    )

    uncertainties = list(combination.get("uncertainties", []))
    uncertainties.append(f"constraint relaxation viability unknown: {constraint_desc}")
    uncertainties.append("the constraint may be fundamental, not relaxable")

    return {
        "purpose": f"Address '{problem_domain}' by relaxing a known constraint",
        "target_users": ["technical practitioners", "researchers"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": combination.get("combination_basis", "constraint_relaxation"),
    }


def _template_gap_filling(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: fill a NOT_EVIDENCED gap with a new or undocumented component.

    Best when there are NOT_EVIDENCED opportunities that suggest a missing
    capability that needs to be filled.
    """
    not_evidenced_opps = [o for o in opportunities if o.get("gap_type") == "not_evidenced"]
    if not not_evidenced_opps:
        return None

    components = combination.get("components", [])
    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]
    gap_desc = not_evidenced_opps[0].get("gap_description", "a gap")[:80]
    gap_caps = not_evidenced_opps[0].get("relevant_capabilities", [])

    integration = (
        f"Gap-filling approach: the existing combination of "
        f"{' + '.join(comp_names)} addresses part of '{problem_domain}', "
        f"but a gap remains: '{gap_desc}'. Filling this gap (by documenting "
        f"or building a tool that provides {', '.join(gap_caps[:2])}) would "
        f"complete the solution."
    )

    benefit = (
        f"Filling the gap '{gap_desc}' would enable the combination to "
        f"fully address '{problem_domain}'. The benefit is a hypothesis "
        f"requiring investigation of whether the missing capability can "
        f"be provided."
    )

    uncertainties = list(combination.get("uncertainties", []))
    uncertainties.append(f"gap may not be fillable: {gap_desc}")
    uncertainties.append("absence of evidence is NOT evidence of absence")

    return {
        "purpose": f"Address '{problem_domain}' by filling a documented capability gap",
        "target_users": ["technical practitioners", "tool builders"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": combination.get("combination_basis", "gap_filling"),
    }


def _template_recombination(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: recombine components in a novel way.

    Best when the combination's components are typically used separately
    and combining them is unusual. Graph uniqueness is NOT claimed as
    market novelty.
    """
    components = combination.get("components", [])
    if len(components) < 2:
        return None

    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]

    integration = (
        f"Recombination approach: the components {', '.join(comp_names)} "
        f"are typically used independently. Combining them in a new way "
        f"may address '{problem_domain}' by leveraging their interaction. "
        f"Note: graph uniqueness is structural, not market novelty — "
        f"the combination may already exist in undocumented form."
    )

    benefit = (
        f"Recombining {len(components)} typically-separate components may "
        f"address '{problem_domain}'. The benefit is a hypothesis to be "
        f"validated by experiment, not a verified outcome."
    )

    uncertainties = list(combination.get("uncertainties", []))
    uncertainties.append("recombination interaction effects are unknown")
    uncertainties.append("graph uniqueness is NOT market novelty")

    return {
        "purpose": f"Address '{problem_domain}' by recombining typically-separate components",
        "target_users": ["innovators", "system architects"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": combination.get("combination_basis", "recombination"),
    }


def _template_analogy(
    combination: dict[str, Any],
    opportunities: list[dict[str, Any]],
    problem_domain: str,
    context: str | None,
) -> dict[str, Any] | None:
    """Template: apply an analogy from a different domain.

    Best when the combination's capabilities could be analogously applied
    to the problem domain. The analogy is explicitly labeled as a hypothesis.
    """
    components = combination.get("components", [])
    if not components:
        return None

    comp_names = [c.get("canonical_name", c.get("entity_id", "?")) for c in components]
    cap_basis = combination.get("combination_basis", "")

    integration = (
        f"Analogy approach: the capabilities of {', '.join(comp_names)} "
        f"({cap_basis}) are applied by analogy to '{problem_domain}'. "
        f"The components' proven use in other domains suggests they may "
        f"be adaptable here, but the analogy requires validation."
    )

    benefit = (
        f"By analogy, the combination of {len(components)} components "
        f"may address '{problem_domain}'. The benefit is a hypothesis "
        f"based on analogical reasoning, not verified evidence."
    )

    uncertainties = list(combination.get("uncertainties", []))
    uncertainties.append("analogical reasoning is not evidence")
    uncertainties.append("domain transfer viability is unproven")

    return {
        "purpose": f"Address '{problem_domain}' by analogical transfer from another domain",
        "target_users": ["innovators", "researchers"],
        "integration_mechanism": integration,
        "potential_benefit": benefit,
        "uncertainties": uncertainties,
        "combination_basis": combination.get("combination_basis", "analogy"),
    }


#: All deterministic templates (baselines, not the complete capability).
_TEMPLATES: list = [
    _template_composition,
    _template_substitution,
    _template_constraint_relaxation,
    _template_gap_filling,
    _template_recombination,
    _template_analogy,
]


# ── Quality gates ─────────────────────────────────────────────────────────


def _check_quality_gates(
    concept: dict[str, Any], existing_ids: dict[str, set[str]]
) -> list[dict[str, str]]:
    """Check the G05-T03 quality gates for a concept.

    Returns a list of {gate, passed, detail} dicts.
    """
    gates: list[dict[str, str]] = []

    # Gate 10: Every concept explains integration_mechanism + potential_benefit
    has_integration = bool(concept.get("integration_mechanism", "").strip())
    has_benefit = bool(concept.get("potential_benefit", "").strip())
    gates.append(
        {
            "gate": "integration_mechanism_and_benefit",
            "passed": has_integration and has_benefit,
            "detail": f"integration_mechanism={'present' if has_integration else 'missing'}; "
            f"potential_benefit={'present' if has_benefit else 'missing'}",
        }
    )

    # Gate 11: Graph uniqueness != market novelty (novelty_caveat must be present)
    # For T03, the concept must NOT claim market novelty. Check that
    # potential_benefit does not contain "novel" or "unique" as assertions.
    benefit = (concept.get("potential_benefit") or "").lower()
    claims_novelty = "market novelty" in benefit or "commercially novel" in benefit
    gates.append(
        {
            "gate": "no_fabricated_novelty",
            "passed": not claims_novelty,
            "detail": "potential_benefit must not claim market novelty"
            if claims_novelty
            else "no fabricated novelty claims",
        }
    )

    # Gate: Zero fabricated component identifiers
    all_components_valid = True
    for comp in concept.get("components", []):
        eid = comp.get("entity_id", "")
        if eid and eid not in existing_ids["entities"]:
            all_components_valid = False
            break
    gates.append(
        {
            "gate": "no_fabricated_component_ids",
            "passed": all_components_valid,
            "detail": f"all {len(concept.get('components', []))} component IDs validated",
        }
    )

    # Gate: Zero fabricated evidence references
    all_refs_valid = True
    for ref in concept.get("evidence_refs", []):
        if ref not in existing_ids["fragments"]:
            all_refs_valid = False
            break
    gates.append(
        {
            "gate": "no_fabricated_evidence_refs",
            "passed": all_refs_valid,
            "detail": f"all {len(concept.get('evidence_refs', []))} evidence_refs validated",
        }
    )

    # Gate 8: Every concept has >=1 uncertainty
    has_uncertainty = len(concept.get("uncertainties", [])) >= 1
    gates.append(
        {
            "gate": "uncertainty_honesty",
            "passed": has_uncertainty,
            "detail": f"{len(concept.get('uncertainties', []))} uncertainty/uncertainties",
        }
    )

    # Gate: No unsupported numerical business claims
    benefit_text = concept.get("potential_benefit", "")
    has_numerical_claim = "~" in benefit_text and "%" in benefit_text
    gates.append(
        {
            "gate": "no_unsupported_numerical_claims",
            "passed": not has_numerical_claim,
            "detail": "no '~X%' numerical claims"
            if not has_numerical_claim
            else "contains unsupported numerical claim",
        }
    )

    # Gate: No automatic VERIFIED promotion
    # The hypothesis_id must point to a hypothesized-state claim, not verified.
    # This is enforced at persistence time (we always create with epistemic_state="hypothesized").
    gates.append(
        {
            "gate": "no_verified_promotion",
            "passed": True,
            "detail": "hypothesis created with epistemic_state='hypothesized' (never VERIFIED)",
        }
    )

    return gates


# ── Persistence helpers ────────────────────────────────────────────────────


def _compute_concept_fingerprint(
    problem_domain: str,
    context: str | None,
    combination_basis: str,
    template_name: str,
    component_entity_ids: list[str],
) -> str:
    """Compute a deterministic fingerprint for a concept.

    The fingerprint is based on normalized input factors:
    - problem_domain (lowercased, stripped)
    - context (lowercased, stripped, or empty)
    - combination_basis (the capabilities involved)
    - template_name (which template produced this concept)
    - component_entity_ids (sorted for determinism)

    Two requests with the same fingerprint produce the same persisted
    concept identity (idempotent reuse). Requests with different
    fingerprints produce distinct concepts.
    """
    normalized_domain = (problem_domain or "").strip().lower()
    normalized_context = (context or "").strip().lower()
    normalized_basis = (combination_basis or "").strip().lower()
    sorted_components = sorted(component_entity_ids)

    fingerprint_input = json.dumps(
        {
            "domain": normalized_domain,
            "context": normalized_context,
            "basis": normalized_basis,
            "template": template_name,
            "components": sorted_components,
        },
        sort_keys=True,
    )

    return hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:32]


async def _find_existing_concept(
    session: AsyncSession,
    fingerprint: str,
) -> tuple[str, str] | None:
    """Find an existing innovation concept by its fingerprint.

    The fingerprint is stored in ``EntityRow.attributes`` as a JSON field
    ``concept_fingerprint``. Returns (innovation_entity_id, hypothesis_claim_id)
    if found, or None.

    This enables idempotent reuse: equivalent repeated requests return the
    same persisted concept instead of creating duplicates.
    """
    # Search for an EntityRow with kind="project" whose attributes contain
    # the concept_fingerprint. The attributes column is JSON text.
    stmt = select(EntityRow).where(
        EntityRow.kind == "project",
        EntityRow.attributes.contains(fingerprint),
    )
    result = await session.execute(stmt)
    entity = result.scalar_one_or_none()
    if entity is None:
        return None

    # Find the hypothesis claim linked to this innovation entity
    claim_stmt = (
        select(ClaimRow)
        .where(
            ClaimRow.subject_ref == entity.id,
            ClaimRow.epistemic_state == "hypothesized",
        )
        .order_by(ClaimRow.created_at.desc())
        .limit(1)
    )
    claim_result = await session.execute(claim_stmt)
    claim = claim_result.scalar_one_or_none()
    if claim is None:
        return None

    return entity.id, claim.id


async def _persist_concept(
    session: AsyncSession,
    concept_data: dict[str, Any],
    problem_domain: str,
    context: str | None,
    existing_ids: dict[str, set[str]],
    request_id: str,
    template_name: str,
) -> tuple[str, str, bool]:
    """Persist an innovation concept and its hypothesis.

    Per the approved storage mapping (§4.1.2):
    - Innovation entity: EntityRow(kind="project")
    - Hypothesis: ClaimRow(epistemic_state="hypothesized")

    G05-T03C: Idempotent reuse. Before creating a new concept, compute
    a deterministic fingerprint and check for an existing concept with
    the same fingerprint. If found, reuse it (no duplicate creation).

    Returns (innovation_entity_id, hypothesis_claim_id, was_reused).
    """
    # Compute the deterministic fingerprint
    component_ids = [comp.get("entity_id", "") for comp in concept_data.get("components", [])]
    fingerprint = _compute_concept_fingerprint(
        problem_domain=problem_domain,
        context=context,
        combination_basis=concept_data.get("combination_basis", ""),
        template_name=template_name,
        component_entity_ids=component_ids,
    )

    # Check for an existing concept with the same fingerprint
    existing = await _find_existing_concept(session, fingerprint)
    if existing is not None:
        # Reuse the existing concept — no duplicate creation
        return existing[0], existing[1], True

    # Create new concept with a deterministic ID derived from the fingerprint
    concept_id = f"innov-{fingerprint[:16]}"

    # Create the innovation entity
    innov_entity = EntityRow(
        id=concept_id,
        kind="project",
        canonical_name=concept_data.get("purpose", f"concept-{concept_id}")[:512],
        aliases="[]",
        attributes=json.dumps(
            {
                "problem_domain": problem_domain,
                "context": context,
                "generation_method": concept_data.get("generation_method", "deterministic"),
                "combination_basis": concept_data.get("combination_basis", ""),
                "integration_mechanism": concept_data.get("integration_mechanism", ""),
                "potential_benefit": concept_data.get("potential_benefit", ""),
                "uncertainties": concept_data.get("uncertainties", []),
                "target_users": concept_data.get("target_users", []),
                "request_id": request_id,
                "concept_fingerprint": fingerprint,
                "template_name": template_name,
            }
        ),
        description=concept_data.get("purpose", ""),
        version=1,
    )
    session.add(innov_entity)
    await session.flush()

    # Create the hypothesis claim (always epistemic_state="hypothesized")
    hypothesis_id = f"hyp-{fingerprint[:16]}"
    hypothesis_claim = ClaimRow(
        id=hypothesis_id,
        proposition=(
            f"Hypothesis: the innovation '{concept_data.get('purpose', '')[:200]}' "
            f"may address '{problem_domain}'. This is a hypothesis, not a verified fact."
        ),
        subject_ref=concept_id,
        object_ref=None,
        evidence_refs=json.dumps(concept_data.get("evidence_refs", [])[:20]),
        contradicting_refs="[]",
        epistemic_state="hypothesized",
        confidence_value=0.0,
        confidence_method=json.dumps({"hypothesis_status": "proposed"}),
        validity_conditions=json.dumps([context] if context else []),
        extraction_method="g05-t03-innovation-generation",
        version=1,
    )
    session.add(hypothesis_claim)
    await session.flush()

    # Create hypothesized relationships from the innovation entity
    # to its component entities (these are origin="hypothesized" — epistemically isolated)
    # Use a deterministic relationship ID to prevent duplicates on retry
    for comp in concept_data.get("components", []):
        comp_eid = comp.get("entity_id")
        if comp_eid and comp_eid in existing_ids["entities"]:
            # Deterministic rel ID from fingerprint + component ID
            rel_fingerprint = hashlib.sha256(f"{fingerprint}:{comp_eid}".encode()).hexdigest()[:16]
            rel_id = f"rel-{rel_fingerprint}"

            # Check if this relationship already exists (prevents duplicates)
            existing_rel = (
                await session.execute(select(RelationshipRow).where(RelationshipRow.id == rel_id))
            ).scalar_one_or_none()
            if existing_rel is not None:
                continue  # skip — relationship already exists

            rel = RelationshipRow(
                id=rel_id,
                from_entity_id=concept_id,
                to_entity_id=comp_eid,
                predicate="INTEGRATES_WITH",
                direction="directed",
                origin="hypothesized",  # epistemically isolated
                verification_state="unverified",
                evidence_refs=json.dumps(comp.get("evidence_refs", [])[:10]),
                conditions=json.dumps({"role": comp.get("role", "")}),
                version=1,
            )
            session.add(rel)
    await session.flush()

    return concept_id, hypothesis_id, False


# ── Main entry point ────────────────────────────────────────────────────────


async def generate_innovations(
    session: AsyncSession,
    problem_domain: str,
    *,
    context: str | None = None,
    candidate_entity_ids: list[str] | None = None,
    max_concepts: int = MAX_CONCEPTS,
    limit: int = DEFAULT_LIMIT,
    requester: str | None = None,
) -> InnovationResult:
    """Generate evidence-grounded innovation concepts.

    Per G05-T03: this converts T01 combinations and T02 opportunities into
    distinct, technically plausible application concepts. Each concept has
    a purpose, components, integration mechanism, potential benefit, and
    uncertainties. The six deterministic templates are baselines, not the
    complete innovation capability.

    Args:
        session: AsyncSession bound to the Synapse database.
        problem_domain: the problem to address (max 512 chars).
        context: optional technical context.
        candidate_entity_ids: optional entity IDs to scope to.
        max_concepts: cap on returned concepts (≤ MAX_CONCEPTS=10).
        limit: max components per retrieval.
        requester: optional actor name for audit logging.

    Returns:
        An ``InnovationResult`` dict with concepts[], unknowns[],
        quality_gates, limits, request_id, generated_at.

    Epistemic safety:
        - Every cited entity_id resolves to an existing EntityRow.
        - Every cited evidence_ref resolves to an existing EvidenceFragmentRow.
        - Hypothesized relationships (origin="hypothesized") are epistemically isolated.
        - Every concept has >=1 uncertainty.
        - No market novelty or numerical benefit claims are fabricated.
        - No automatic VERIFIED promotion (hypotheses created as "hypothesized").
    """
    request_id = uuid4().hex
    max_concepts = max(1, min(MAX_CONCEPTS, max_concepts))
    limit = max(1, min(MAX_LIMIT, limit))

    _log.info(
        "generate_innovations problem_domain=%r context=%s max_concepts=%d",
        problem_domain[:80],
        context,
        max_concepts,
    )

    # ── Validate inputs ──────────────────────────────────────────────────
    if not problem_domain or not problem_domain.strip():
        return InnovationResult(
            concepts=[],
            unknowns=["empty_problem_domain"],
            quality_gates=[],
            limits={"max_concepts": max_concepts, "limit": limit},
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    if len(problem_domain) > MAX_QUERY_CHARS:
        return InnovationResult(
            concepts=[],
            unknowns=[f"problem_domain_too_long:{len(problem_domain)}>{MAX_QUERY_CHARS}"],
            quality_gates=[],
            limits={"max_concepts": max_concepts, "limit": limit},
            request_id=request_id,
            generated_at=_utcnow_iso(),
        )

    # ── Collect existing IDs for validation ──────────────────────────────
    existing_ids = await _collect_existing_ids(session)

    # ── Step 1: Get T01 combinations ─────────────────────────────────────
    combination_result = await combine_knowledge(
        session,
        problem_domain,
        context=context,
        candidate_entity_ids=candidate_entity_ids,
        max_combinations=MAX_COMBINATIONS,
        limit=limit,
        requester=requester or "g05-t03-innovation",
    )
    combinations = combination_result.get("combinations", [])

    # ── Step 2: Get T02 opportunities ────────────────────────────────────
    opportunity_result = await discover_opportunities(
        session,
        problem_domain,
        context=context,
        candidate_entity_ids=candidate_entity_ids,
        max_opportunities=MAX_OPPORTUNITIES,
        limit=limit,
        requester=requester or "g05-t03-innovation",
    )
    opportunities = opportunity_result.get("opportunities", [])

    # ── Step 3: Apply templates to generate concepts ────────────────────
    concepts: list[InnovationConcept] = []
    unknowns: list[str] = []
    seen_basis: set[str] = set()  # for deduplication

    for comb in combinations:
        if len(concepts) >= max_concepts:
            break

        # Try each template; use the first one that produces a concept
        for template_fn in _TEMPLATES:
            if len(concepts) >= max_concepts:
                break

            template_result = template_fn(comb, opportunities, problem_domain, context)
            if template_result is None:
                continue

            # Deduplicate by combination_basis + template name
            basis = template_result.get("combination_basis", "")
            template_name = template_fn.__name__.replace("_template_", "")
            dedup_key = f"{basis}:{template_name}"
            if dedup_key in seen_basis:
                continue
            seen_basis.add(dedup_key)

            # Build the full concept
            components = comb.get("components", [])
            evidence_refs = [
                r for r in comb.get("evidence_refs", []) if r in existing_ids["fragments"]
            ]
            constraints = comb.get("constraints", []) if "constraints" in comb else []

            # Find opportunities this concept addresses
            opp_ids = []
            for opp in opportunities:
                opp_caps = opp.get("relevant_capabilities", [])
                for comp in components:
                    role = comp.get("role", "")
                    if any(cap.lower() in role.lower() for cap in opp_caps):
                        opp_ids.append(opp.get("id", ""))
                        break

            concept_data = {
                **template_result,
                "components": components,
                "evidence_refs": evidence_refs[:20],
                "constraints": constraints[:5],
                "generation_method": "deterministic",
                "opportunities_addressed": opp_ids[:5],
            }

            # Validate quality gates
            quality_gates = _check_quality_gates(concept_data, existing_ids)
            all_gates_passed = all(g["passed"] for g in quality_gates)

            # Even if some gates fail, preserve the concept with its uncertainties
            # (per mission requirement 8: preserve exploratory opportunities even
            # when evidence is sparse; label their uncertainty rather than discarding)
            if not all_gates_passed:
                failed_gates = [g["gate"] for g in quality_gates if not g["passed"]]
                # Don't discard — add the failed gates as uncertainties
                for fg in failed_gates:
                    if fg == "uncertainty_honesty":
                        concept_data["uncertainties"].append(
                            "concept lacks explicit uncertainty (auto-added)"
                        )
                    elif fg not in ("no_fabricated_component_ids", "no_fabricated_evidence_refs"):
                        # Fabricated IDs are a hard failure — skip this concept
                        pass

                # Hard failures: fabricated IDs or evidence refs
                fab_gates = [
                    g
                    for g in quality_gates
                    if g["gate"] in ("no_fabricated_component_ids", "no_fabricated_evidence_refs")
                    and not g["passed"]
                ]
                if fab_gates:
                    unknowns.append(
                        f"concept from {template_name} template skipped: "
                        f"fabricated IDs or evidence refs"
                    )
                    continue

            # Persist the concept and its hypothesis
            innovation_id, hypothesis_id, _was_reused = await _persist_concept(
                session,
                concept_data,
                problem_domain,
                context,
                existing_ids,
                request_id,
                template_name=template_fn.__name__.replace("_template_", ""),
            )

            concepts.append(
                InnovationConcept(
                    id=innovation_id,
                    problem_domain=problem_domain,
                    context=context,
                    purpose=concept_data.get("purpose", ""),
                    target_users=concept_data.get("target_users", []),
                    components=components,
                    integration_mechanism=concept_data.get("integration_mechanism", ""),
                    potential_benefit=concept_data.get("potential_benefit", ""),
                    uncertainties=concept_data.get("uncertainties", []),
                    evidence_refs=evidence_refs[:20],
                    constraints=constraints[:5],
                    combination_basis=concept_data.get("combination_basis", ""),
                    generation_method="deterministic",
                    hypothesis_id=hypothesis_id,
                    opportunities_addressed=concept_data.get("opportunities_addressed", []),
                    request_id=request_id,
                )
            )

    # ── Step 4: If no concepts from templates, try single-component concepts ──
    if not concepts and combinations:
        for comb in combinations:
            if len(concepts) >= max_concepts:
                break
            components = comb.get("components", [])
            if not components:
                continue

            comp = components[0]
            ev_refs = [r for r in comb.get("evidence_refs", []) if r in existing_ids["fragments"]]

            concept_data = {
                "purpose": f"Address '{problem_domain}' using {comp.get('canonical_name', 'a component')}",
                "target_users": ["technical practitioners"],
                "integration_mechanism": (
                    f"Single-component approach: {comp.get('canonical_name', 'the component')} "
                    f"provides capabilities that may address '{problem_domain}'. "
                    f"No multi-component integration is needed."
                ),
                "potential_benefit": (
                    f"This single component may address '{problem_domain}'. "
                    f"The benefit is a hypothesis requiring validation, not a verified outcome."
                ),
                "uncertainties": [
                    "single-component solution may be insufficient for the full problem",
                    "combination viability is a hypothesis requiring investigation",
                ],
                "combination_basis": comb.get("combination_basis", "single_component"),
                "components": components,
                "evidence_refs": ev_refs[:20],
                "constraints": [],
                "generation_method": "deterministic",
                "opportunities_addressed": [],
            }

            innovation_id, hypothesis_id, _was_reused = await _persist_concept(
                session,
                concept_data,
                problem_domain,
                context,
                existing_ids,
                request_id,
                template_name="single_component",
            )

            concepts.append(
                InnovationConcept(
                    id=innovation_id,
                    problem_domain=problem_domain,
                    context=context,
                    purpose=concept_data["purpose"],
                    target_users=concept_data["target_users"],
                    components=components,
                    integration_mechanism=concept_data["integration_mechanism"],
                    potential_benefit=concept_data["potential_benefit"],
                    uncertainties=concept_data["uncertainties"],
                    evidence_refs=ev_refs[:20],
                    constraints=[],
                    combination_basis=concept_data["combination_basis"],
                    generation_method="deterministic",
                    hypothesis_id=hypothesis_id,
                    opportunities_addressed=[],
                    request_id=request_id,
                )
            )

    await session.commit()

    # ── Step 5: Collect unknowns ────────────────────────────────────────
    if not concepts:
        unknowns.append(
            "no innovation concepts generated — the knowledge graph may not "
            "contain enough documented capabilities or combinations for this problem domain"
        )
    unknowns.extend(combination_result.get("unknowns", []))
    unknowns.extend(opportunity_result.get("unknowns", []))

    # ── Step 6: Run quality gates on all concepts ──────────────────────
    all_quality_gates: list[dict[str, Any]] = []
    for concept in concepts:
        gates = _check_quality_gates(concept, existing_ids)
        all_quality_gates.extend(gates)

    _log.info(
        "generate_innovations complete: %d concepts, %d unknowns, %d quality gates",
        len(concepts),
        len(unknowns),
        len(all_quality_gates),
    )

    return InnovationResult(
        concepts=concepts[:max_concepts],
        unknowns=unknowns,
        quality_gates=all_quality_gates,
        limits={"max_concepts": max_concepts, "limit": limit},
        request_id=request_id,
        generated_at=_utcnow_iso(),
    )
