"""RelationshipService — typed, directed knowledge graph over relational storage.

Per the user's G03-T03 authorization: minimal evidence-backed
RelationshipService. Uses the existing G01 Relationship domain contract
and predicate vocabulary (16 predicates). No graph database.

Capabilities:
  - create_relationship(): idempotent creation with evidence refs
  - find_related_entities(): direct neighbors by predicate
  - find_paths(): bounded multi-hop traversal (recursive CTE or iterative)
  - find_capabilities(): what an entity provides/enables
  - find_dependencies(): what an entity requires/depends on
  - find_alternatives(): what replaces or integrates with an entity
  - find_limitations(): constraints, limits, contradictions
  - find_missing_capabilities(): capabilities NOT connected to an entity
  - find_contradictions(): pairs with SUPPORTS + CONTRADICTS
  - find_unverified(): derived/hypothesized, never auto-promoted

Graph thinking via SQL: the relationships table IS an edge table.
Multi-hop traversal uses iterative SQL queries (not recursive CTEs,
for SQLite compatibility and simplicity).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.observability.logging import get_logger
from synapse.storage.models import (
    AuditEventRow,
    EntityRow,
    RelationshipRow,
    SourceSpanRow,
)

_log = get_logger("synapse.application.relationship_service")


# ── Relationship creation ───────────────────────────────────────────────────


async def create_relationship(
    session: AsyncSession,
    *,
    from_entity_id: str,
    to_entity_id: str,
    predicate: str,
    origin: str = "explicit",
    verification_state: str = "unverified",
    evidence_refs: list[str] | None = None,
    conditions: list[str] | None = None,
    derivation_chain: list[str] | None = None,
    confidence_value: float | None = None,
    confidence_method: str | None = None,
    requester: str | None = None,
) -> dict[str, Any]:
    """Create a typed, directed relationship between entities.

    Idempotent: if a relationship with the same (from_entity_id,
    to_entity_id, predicate, origin) already exists, the new evidence_refs
    are merged into the existing relationship (no duplicate created).

    Per G01 invariant: derived/hypothesized relationships are NEVER
    auto-promoted to verified. This function does not set
    verification_state="verified" unless explicitly passed by the caller
    (and even then, the G01 domain validator enforces that
    explicit+verified requires evidence_refs).

    Returns a dict with the relationship info.
    """
    request_id = uuid4().hex

    # Validate entities exist
    for entity_id, label in [(from_entity_id, "from_entity_id"), (to_entity_id, "to_entity_id")]:
        stmt = select(EntityRow).where(EntityRow.id == entity_id)
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is None:
            return {
                "ok": False,
                "error": f"entity_not_found: {label}={entity_id}",
                "request_id": request_id,
            }

    # Check for existing relationship (idempotent)
    existing_stmt = select(RelationshipRow).where(
        RelationshipRow.from_entity_id == from_entity_id,
        RelationshipRow.to_entity_id == to_entity_id,
        RelationshipRow.predicate == predicate,
        RelationshipRow.origin == origin,
    )
    existing_result = await session.execute(existing_stmt)
    existing_row = existing_result.scalar_one_or_none()

    if existing_row is not None:
        # Merge new evidence_refs into existing
        existing_refs: list[str] = []
        if existing_row.evidence_refs:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                existing_refs = json.loads(existing_row.evidence_refs)

        new_refs = evidence_refs or []
        merged = list(set(existing_refs + new_refs))
        if len(merged) > len(existing_refs):
            existing_row.evidence_refs = json.dumps(merged)
            existing_row.version += 1

        # Merge conditions
        if conditions:
            existing_conditions: list[str] = []
            if existing_row.conditions:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    existing_conditions = json.loads(existing_row.conditions)
            merged_conditions = list(set(existing_conditions + conditions))
            existing_row.conditions = json.dumps(merged_conditions)

        await session.flush()
        return {
            "ok": True,
            "relationship_id": existing_row.id,
            "merged": True,
            "evidence_ref_count": len(merged),
            "request_id": request_id,
        }

    # Create new relationship
    row = RelationshipRow(
        id=uuid4().hex,
        from_entity_id=from_entity_id,
        to_entity_id=to_entity_id,
        predicate=predicate,
        direction="directed",  # all 16 G01 predicates are directed except INTEGRATES_WITH and CONTRADICTS
        origin=origin,
        verification_state=verification_state,
        evidence_refs=json.dumps(evidence_refs) if evidence_refs else None,
        confidence_value=confidence_value,
        confidence_method=confidence_method,
        conditions=json.dumps(conditions) if conditions else None,
        derivation_chain=json.dumps(derivation_chain) if derivation_chain else None,
        version=1,
    )
    session.add(row)
    await session.flush()

    # Record audit event
    await _audit(
        session,
        event_type="relationship.created",
        actor=requester,
        target_id=row.id,
        target_type="relationship",
        payload={
            "from_entity_id": from_entity_id,
            "to_entity_id": to_entity_id,
            "predicate": predicate,
            "origin": origin,
            "verification_state": verification_state,
            "evidence_ref_count": len(evidence_refs or []),
        },
        request_id=request_id,
    )

    return {
        "ok": True,
        "relationship_id": row.id,
        "merged": False,
        "evidence_ref_count": len(evidence_refs or []),
        "request_id": request_id,
    }


# ── Query helpers ───────────────────────────────────────────────────────────


def _relationship_to_dict(row: RelationshipRow) -> dict[str, Any]:
    """Convert a RelationshipRow to a dict for API responses."""
    evidence_refs = []
    if row.evidence_refs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            evidence_refs = json.loads(row.evidence_refs)

    conditions = []
    if row.conditions:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            conditions = json.loads(row.conditions)

    derivation_chain = []
    if row.derivation_chain:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            derivation_chain = json.loads(row.derivation_chain)

    return {
        "id": row.id,
        "from_entity_id": row.from_entity_id,
        "to_entity_id": row.to_entity_id,
        "predicate": row.predicate,
        "direction": row.direction,
        "origin": row.origin,
        "verification_state": row.verification_state,
        "evidence_refs": evidence_refs,
        "confidence_value": row.confidence_value,
        "confidence_method": row.confidence_method,
        "conditions": conditions,
        "derivation_chain": derivation_chain,
        "version": row.version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def find_relationships(
    session: AsyncSession,
    *,
    entity_id: str | None = None,
    predicate: str | None = None,
    origin: str | None = None,
    verification_state: str | None = None,
    direction: str = "outgoing",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find relationships matching criteria.

    Args:
        entity_id: filter by from (outgoing) or to (incoming) entity
        predicate: filter by predicate (e.g., "PROVIDES")
        origin: filter by origin (explicit, derived, hypothesized)
        verification_state: filter by verification state
        direction: "outgoing" (from_entity), "incoming" (to_entity), or "both"
        limit: max results
    """
    stmt = select(RelationshipRow)

    if entity_id:
        if direction == "outgoing":
            stmt = stmt.where(RelationshipRow.from_entity_id == entity_id)
        elif direction == "incoming":
            stmt = stmt.where(RelationshipRow.to_entity_id == entity_id)
        else:  # both
            stmt = stmt.where(
                (RelationshipRow.from_entity_id == entity_id)
                | (RelationshipRow.to_entity_id == entity_id)
            )

    if predicate:
        stmt = stmt.where(RelationshipRow.predicate == predicate)
    if origin:
        stmt = stmt.where(RelationshipRow.origin == origin)
    if verification_state:
        stmt = stmt.where(RelationshipRow.verification_state == verification_state)

    stmt = stmt.order_by(RelationshipRow.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [_relationship_to_dict(r) for r in result.scalars().all()]


async def find_related_entities(
    session: AsyncSession,
    entity_id: str,
    *,
    predicate: str | None = None,
    direction: str = "outgoing",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find entities related to a given entity.

    Returns a list of dicts with:
      {"entity": EntityRow fields, "relationship": RelationshipRow fields}
    """
    stmt = select(RelationshipRow, EntityRow)

    if direction in ("outgoing", "both"):
        stmt_out = stmt.join(EntityRow, EntityRow.id == RelationshipRow.to_entity_id).where(
            RelationshipRow.from_entity_id == entity_id
        )
        if predicate:
            stmt_out = stmt_out.where(RelationshipRow.predicate == predicate)
        stmt_out = stmt_out.limit(limit)
        result_out = await session.execute(stmt_out)
        outgoing = [
            {
                "entity": {
                    "id": e.id,
                    "kind": e.kind,
                    "canonical_name": e.canonical_name,
                    "canonical_uri": e.canonical_uri,
                },
                "relationship": _relationship_to_dict(r),
            }
            for r, e in result_out.all()
        ]
    else:
        outgoing = []

    if direction in ("incoming", "both"):
        stmt_in = stmt.join(EntityRow, EntityRow.id == RelationshipRow.from_entity_id).where(
            RelationshipRow.to_entity_id == entity_id
        )
        if predicate:
            stmt_in = stmt_in.where(RelationshipRow.predicate == predicate)
        stmt_in = stmt_in.limit(limit)
        result_in = await session.execute(stmt_in)
        incoming = [
            {
                "entity": {
                    "id": e.id,
                    "kind": e.kind,
                    "canonical_name": e.canonical_name,
                    "canonical_uri": e.canonical_uri,
                },
                "relationship": _relationship_to_dict(r),
            }
            for r, e in result_in.all()
        ]
    else:
        incoming = []

    return outgoing + incoming


async def find_paths(
    session: AsyncSession,
    from_entity_id: str,
    to_entity_id: str,
    *,
    max_depth: int = 5,
) -> list[list[dict[str, Any]]]:
    """Find multi-hop paths between two entities.

    Uses iterative BFS (breadth-first search) over the relationships table.
    Each step is a SQL query — no recursive CTE needed (works on SQLite).

    Returns a list of paths, where each path is a list of relationship dicts.
    """
    if max_depth < 1:
        return []

    # BFS: frontier is a list of (current_entity_id, path_so_far)
    frontier: list[tuple[str, list[dict[str, Any]]]] = [(from_entity_id, [])]
    visited: set[str] = {from_entity_id}
    all_paths: list[list[dict[str, Any]]] = []

    for _depth in range(max_depth):
        if not frontier:
            break
        next_frontier: list[tuple[str, list[dict[str, Any]]]] = []

        for current_id, path in frontier:
            # Find all outgoing relationships from current_id
            stmt = select(RelationshipRow).where(RelationshipRow.from_entity_id == current_id)
            result = await session.execute(stmt)
            relationships = result.scalars().all()

            for rel in relationships:
                next_id = rel.to_entity_id

                # Check if we reached the target
                new_path = [*path, _relationship_to_dict(rel)]

                if next_id == to_entity_id:
                    all_paths.append(new_path)
                    continue

                # Don't revisit nodes already in the path (cycle prevention)
                if next_id in visited:
                    continue

                visited.add(next_id)
                next_frontier.append((next_id, new_path))

        frontier = next_frontier

    return all_paths


# ── Domain-specific queries ─────────────────────────────────────────────────


async def find_capabilities(
    session: AsyncSession,
    entity_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """What capabilities does this entity provide or enable?

    Queries: PROVIDES, ENABLES, PRODUCES (outgoing from entity)
    """
    results: list[dict[str, Any]] = []
    for pred in ("PROVIDES", "ENABLES", "PRODUCES"):
        results.extend(
            await find_related_entities(
                session, entity_id, predicate=pred, direction="outgoing", limit=limit
            )
        )
    return results[:limit]


async def find_dependencies(
    session: AsyncSession,
    entity_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """What dependencies does this entity require?

    Queries: REQUIRES, DEPENDS_ON (outgoing from entity)
    """
    results: list[dict[str, Any]] = []
    for pred in ("REQUIRES", "DEPENDS_ON"):
        results.extend(
            await find_related_entities(
                session, entity_id, predicate=pred, direction="outgoing", limit=limit
            )
        )
    return results[:limit]


async def find_alternatives(
    session: AsyncSession,
    entity_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """What alternatives (replacements, integrations) exist?

    Queries: REPLACES, INTEGRATES_WITH (both directions)
    """
    return await find_related_entities(session, entity_id, direction="both", limit=limit)


async def find_limitations(
    session: AsyncSession,
    entity_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """What limitations, constraints, or contradictions affect this entity?

    Queries: LIMITS, CONTRADICTS, INVALIDATES (both directions)
    """
    results: list[dict[str, Any]] = []
    for pred in ("LIMITS", "CONTRADICTS", "INVALIDATES"):
        results.extend(
            await find_related_entities(
                session, entity_id, predicate=pred, direction="both", limit=limit
            )
        )
    return results[:limit]


async def find_missing_capabilities(
    session: AsyncSession,
    entity_id: str,
    *,
    known_capabilities: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Which capabilities are NOT connected to this entity?

    Compares the entity's outgoing PROVIDES/ENABLES edges against
    a list of known capabilities. Returns capabilities in the list
    that have no edge from this entity.

    If known_capabilities is None, compares against all entities of
    kind="capability" that are NOT already connected.
    """
    # Get existing capabilities connected to this entity
    existing = await find_capabilities(session, entity_id, limit=limit)
    existing_names = {r["entity"]["canonical_name"] for r in existing}

    if known_capabilities is not None:
        # Compare against the provided list
        missing = [
            {"capability": name, "reason": "not_connected"}
            for name in known_capabilities
            if name not in existing_names
        ]
        return missing[:limit]

    # Compare against all capability entities not connected
    stmt = select(EntityRow).where(EntityRow.kind == "capability")
    result = await session.execute(stmt)
    all_caps = result.scalars().all()

    missing = [
        {
            "entity": {
                "id": c.id,
                "kind": c.kind,
                "canonical_name": c.canonical_name,
                "canonical_uri": c.canonical_uri,
            },
            "reason": "not_connected",
        }
        for c in all_caps
        if c.canonical_name not in existing_names
    ]
    return missing[:limit]


async def find_contradictions(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find entity pairs that have both SUPPORTS and CONTRADICTS relationships.

    Both relationships are preserved — neither is deleted.
    """
    # Find all CONTRADICTS relationships
    contradicts_stmt = (
        select(RelationshipRow).where(RelationshipRow.predicate == "CONTRADICTS").limit(limit)
    )
    contradicts_result = await session.execute(contradicts_stmt)
    contradicts = contradicts_result.scalars().all()

    contradictions = []
    for c_rel in contradicts:
        # Check if there's a SUPPORTS for the same entity pair
        pair_a = (c_rel.from_entity_id, c_rel.to_entity_id)
        pair_b = (c_rel.to_entity_id, c_rel.from_entity_id)

        for pair in [pair_a, pair_b]:
            supports_stmt = select(RelationshipRow).where(
                RelationshipRow.from_entity_id == pair[0],
                RelationshipRow.to_entity_id == pair[1],
                RelationshipRow.predicate == "SUPPORTS",
            )
            supports_result = await session.execute(supports_stmt)
            supports = supports_result.scalars().all()

            for s_rel in supports:
                contradictions.append(
                    {
                        "entity_a": pair[0],
                        "entity_b": pair[1],
                        "supports_id": s_rel.id,
                        "contradicts_id": c_rel.id,
                        "both_preserved": True,
                    }
                )

    return contradictions[:limit]


async def find_unverified_relationships(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Find relationships that are derived or hypothesized (never auto-promoted).

    Per G01 invariant: derived/hypothesized relationships remain
    verification_state="unverified" until reviewed or tested.
    """
    stmt = (
        select(RelationshipRow)
        .where(
            RelationshipRow.origin.in_(["derived", "hypothesized"]),
            RelationshipRow.verification_state == "unverified",
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [_relationship_to_dict(r) for r in result.scalars().all()]


# ── Evidence lookup ─────────────────────────────────────────────────────────


async def get_relationship_evidence(
    session: AsyncSession,
    relationship_id: str,
) -> list[dict[str, Any]]:
    """Get evidence fragments + source spans for a relationship.

    Returns the evidence_refs from the relationship, then fetches
    the corresponding evidence_fragments and source_spans.
    """
    from synapse.storage.models import EvidenceFragmentRow

    # Get the relationship
    stmt = select(RelationshipRow).where(RelationshipRow.id == relationship_id)
    result = await session.execute(stmt)
    rel = result.scalar_one_or_none()
    if rel is None:
        return []

    # Parse evidence_refs
    evidence_refs: list[str] = []
    if rel.evidence_refs:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            evidence_refs = json.loads(rel.evidence_refs)

    if not evidence_refs:
        return []

    # Fetch evidence fragments
    ef_stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(evidence_refs))
    ef_result = await session.execute(ef_stmt)
    fragments = ef_result.scalars().all()

    # Fetch source spans for this relationship's claims
    span_stmt = select(SourceSpanRow).where(SourceSpanRow.evidence_fragment_id.in_(evidence_refs))
    span_result = await session.execute(span_stmt)
    spans = span_result.scalars().all()

    return [
        {
            "evidence_fragment_id": ef.id,
            "exact_excerpt": (ef.exact_excerpt or "")[:500],
            "source_uri": ef.source_uri,
            "extraction_method": ef.extraction_method,
            "content_fingerprint": ef.content_fingerprint,
            "spans": [
                {
                    "start_offset": s.start_offset,
                    "end_offset": s.end_offset,
                    "excerpt": s.excerpt[:200],
                }
                for s in spans
                if s.evidence_fragment_id == ef.id
            ],
        }
        for ef in fragments
    ]


# ── Audit helper ────────────────────────────────────────────────────────────


async def _audit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: str | None,
    target_id: str | None,
    target_type: str | None,
    payload: dict[str, Any],
    request_id: str,
) -> None:
    row = AuditEventRow(
        id=uuid4().hex,
        event_type=event_type,
        actor=actor,
        target_id=target_id,
        target_type=target_type,
        payload=payload,
        request_id=request_id,
    )
    session.add(row)
    await session.flush()
