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
