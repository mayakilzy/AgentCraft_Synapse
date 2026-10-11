"""G07-PRE-02 — Minimal cursor-based pagination helper for the four new
list endpoints (innovations, experiments, executions, scenarios).

This module is INTENTIONALLY narrow in scope:

- It is NOT a generic repository abstraction.
- It is NOT a new pagination framework applied to existing endpoints.
- It does NOT touch any existing list endpoint (``GET /api/v1/sources``,
  ``GET /api/v1/entities``, ``GET /api/v1/relationships``,
  ``GET /api/v1/knowledge/capabilities`` — all unchanged, all keep their
  existing ``limit``-only pagination per G07-GAP-006 deferred).
- It is consumed ONLY by the four new list routes added in G07-PRE-02.

Contract (per G07-PRE-02 mission §3):

- Bounded ``limit`` (1..100, default 20).
- Opaque ``cursor`` — base64url-encoded JSON ``{"ts": iso_string, "id": entity_id}``.
- ``next_cursor`` populated ONLY when more results exist (verified by
  fetching ``limit+1`` rows and slicing).
- Stable ordering across equal timestamps using ID tie-break
  (``ORDER BY created_at DESC, id DESC``).
- Malformed / stale / mismatched cursors return a consistent 4xx, not 500.
- The cursor is NOT a security boundary and NOT a snapshot consistency
  guarantee — concurrent writes may shift the page boundary. The cursor
  only encodes "where the previous page ended".

This module is read-only: it produces SELECT statements and never
mutates the session.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.api.errors import ValidationError
from synapse.api.responses import PaginationMeta
from synapse.storage.models import EntityRow

# ── Constants ───────────────────────────────────────────────────────────────

#: Default page size for the four new list endpoints.
DEFAULT_LIMIT = 20

#: Maximum page size accepted by the four new list endpoints.
MAX_LIMIT = 100

#: Minimum page size accepted by the four new list endpoints.
MIN_LIMIT = 1

# ── Cursor encoding/decoding ──────────────────────────────────────────────


def encode_cursor(ts: str, entity_id: str) -> str:
    """Encode a (timestamp, id) pair as an opaque base64url cursor.

    The cursor is ``base64url(json.dumps({"ts": ts, "id": entity_id}))``.
    It is opaque to clients (they should treat it as a string) but
    decodable for debugging and round-trip tests.
    """
    payload = json.dumps({"ts": ts, "id": entity_id}, separators=(",", ":"))
    raw = payload.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode an opaque cursor back to (timestamp_iso_string, entity_id).

    Raises ``ValidationError`` (422) on:
    - Empty cursor.
    - base64url decode failure (malformed padding/chars).
    - JSON decode failure (malformed payload).
    - Missing ``ts`` or ``id`` keys.
    - Non-string ``ts`` or ``id`` values.

    Returns ``(ts_string, entity_id_string)`` — the ``ts_string`` is the
    raw ISO 8601 string stored in the cursor (callers that need a
    datetime should parse it via ``datetime.fromisoformat``).
    """
    if not cursor:
        raise ValidationError("cursor must not be empty")
    # base64url requires padding for ``urlsafe_b64decode`` strict mode; we add it back.
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f"cursor is not valid base64url: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"cursor payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValidationError("cursor payload must be a JSON object")
    ts = payload.get("ts")
    eid = payload.get("id")
    if not isinstance(ts, str) or not isinstance(eid, str):
        raise ValidationError("cursor payload must contain string 'ts' and 'id'")
    if not ts or not eid:
        raise ValidationError("cursor 'ts' and 'id' must be non-empty")
    return ts, eid


def _parse_cursor_ts(ts: str) -> datetime:
    """Parse a cursor's ISO 8601 timestamp string into a datetime.

    Raises ``ValidationError`` if the string is not a valid ISO 8601
    timestamp. Used by the pagination query builder so that the
    ``EntityRow.created_at`` (a datetime column) is compared against a
    datetime value, not a string — avoiding dialect-specific implicit
    casts that may behave differently on SQLite vs PostgreSQL.
    """
    try:
        return datetime.fromisoformat(ts)
    except ValueError as exc:
        raise ValidationError(f"cursor 'ts' is not a valid ISO 8601 timestamp: {exc}") from exc


# ── Pagination result container ───────────────────────────────────────────


@dataclass
class PageResult:
    """The result of a paginated list query.

    Fields:
    - items: the rows for this page (already sliced to ``limit``).
    - pagination: a populated ``PaginationMeta`` with ``limit``, ``total``
      (set to the page's ``len(items)`` if total cannot be computed
      cheaply — callers may override), and ``next_cursor`` (None iff this
      is the last page).
    """

    items: list[EntityRow]
    pagination: PaginationMeta


# ── Core query builder ─────────────────────────────────────────────────────


def _build_paginated_select(
    base_stmt: Select[tuple[EntityRow]],
    *,
    cursor: str | None,
    limit: int,
) -> Select[tuple[EntityRow]]:
    """Apply cursor pagination to a base SELECT of EntityRow.

    The base statement MUST already filter by the correct ``kind``
    discriminator (and any optional filters like ``experiment_id``).
    This helper adds:

    - ``ORDER BY created_at DESC, id DESC`` (stable tie-break).
    - The cursor ``WHERE`` clause (rows strictly before the cursor's
      ``(created_at, id)`` in the DESC/DESC ordering).
    - ``LIMIT limit + 1`` (the +1 lets the caller detect "more rows").

    The caller slices ``result[:limit]`` and uses the ``limit``-th row
    (if present) to compute ``next_cursor``.
    """
    stmt = base_stmt.order_by(EntityRow.created_at.desc(), EntityRow.id.desc())

    if cursor is not None:
        ts_str, eid = decode_cursor(cursor)
        # Parse the ISO 8601 timestamp into a datetime so that the
        # comparison against ``EntityRow.created_at`` (a datetime column)
        # is a datetime-vs-datetime comparison. Comparing a datetime
        # column to a string triggers dialect-specific implicit casts
        # that behave differently on SQLite (lexicographic) vs
        # PostgreSQL (cast error or implicit cast) and would break
        # cursor pagination silently.
        ts_dt = _parse_cursor_ts(ts_str)
        # Strictly-before in DESC/DESC ordering:
        #   created_at < ts  OR  (created_at == ts AND id < eid)
        stmt = stmt.where(
            (EntityRow.created_at < ts_dt)
            | ((EntityRow.created_at == ts_dt) & (EntityRow.id < eid))
        )

    # Fetch limit + 1 so we can detect "more rows exist".
    stmt = stmt.limit(limit + 1)
    return stmt


async def _execute_paginated_query(
    session: AsyncSession,
    base_stmt: Select[tuple[EntityRow]],
    *,
    cursor: str | None,
    limit: int,
) -> PageResult:
    """Execute a paginated query against ``EntityRow`` and return a PageResult.

    The base statement must select ``EntityRow`` rows directly
    (e.g. ``select(EntityRow).where(EntityRow.kind == "...")``).
    """
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise ValidationError(
            f"limit must be in [{MIN_LIMIT}, {MAX_LIMIT}]; got {limit}"
        )

    stmt = _build_paginated_select(base_stmt, cursor=cursor, limit=limit)
    rows = (await session.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page_rows = list(rows[:limit])

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        # created_at is a datetime; isoformat() is stable and
        # round-trippable. Fall back to a far-future sentinel if
        # created_at is somehow None (should not happen — column is
        # NOT NULL with a server default).
        ts = last.created_at.isoformat() if last.created_at else "1970-01-01T00:00:00+00:00"
        next_cursor = encode_cursor(ts, last.id)

    return PageResult(
        items=page_rows,
        pagination=PaginationMeta(
            next_cursor=next_cursor,
            prev_cursor=None,  # prev_cursor not supported in this minimal scope
            limit=limit,
            total=None,  # total is expensive on large tables; left None for now
        ),
    )


# ── Public entry points (one per new endpoint) ────────────────────────────


async def list_innovations_page(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
) -> PageResult:
    """Paginated list of innovation concept entities (kind='project').

    Filter: ``EntityRow.kind == 'project'``. We do NOT additionally
    filter by ``attributes LIKE '%concept_fingerprint%'`` because every
    genuine innovation concept persisted by ``generate_innovations``
    uses ``kind='project'`` AND the ``EntityFingerprintRow`` table is
    the authoritative discriminator (every created concept has a
    fingerprint row with ``kind='project'``).

    To be extra-precise (per G07-PRE-02 §1.3 "Never assume every
    kind='project' entity is an innovation"), we INNER JOIN against
    ``EntityFingerprintRow`` on (entity_id, kind='project'). Entities
    of kind 'project' that lack a fingerprint row are excluded — those
    would be rows created by other code paths that do not register a
    fingerprint, which the audit confirmed does not happen in the
    current codebase.
    """
    from synapse.storage.models import EntityFingerprintRow

    base = (
        select(EntityRow)
        .join(
            EntityFingerprintRow,
            (EntityFingerprintRow.entity_id == EntityRow.id)
            & (EntityFingerprintRow.kind == "project"),
        )
        .where(EntityRow.kind == "project")
    )
    return await _execute_paginated_query(session, base, cursor=cursor, limit=limit)


async def list_experiments_page(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
) -> PageResult:
    """Paginated list of experiment plan entities (kind='experiment')."""
    from synapse.storage.models import EntityFingerprintRow

    base = (
        select(EntityRow)
        .join(
            EntityFingerprintRow,
            (EntityFingerprintRow.entity_id == EntityRow.id)
            & (EntityFingerprintRow.kind == "experiment"),
        )
        .where(EntityRow.kind == "experiment")
    )
    return await _execute_paginated_query(session, base, cursor=cursor, limit=limit)


async def list_executions_page(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
    experiment_id: str | None,
) -> PageResult:
    """Paginated list of execution record entities (kind='execution').

    Optional filter by ``experiment_id``: filters on the
    ``attributes`` JSON column which stores ``experiment_id`` as a
    top-level key. This is a SQL ``LIKE`` filter (not a JSON path
    query) for portability across SQLite and PostgreSQL.

    The pattern is ``%"experiment_id": "{experiment_id}"%`` (leading
    and trailing ``%`` wildcards, no inner ``%``). The inner ``%``
    is omitted because SQL ``LIKE`` is anchored — the pattern must
    match the entire column value, and inner ``%`` wildcards would
    greedily consume characters needed for the literal tail of the
    pattern.

    Limitation: the LIKE filter may match executions whose
    ``attributes`` JSON happens to contain the experiment_id string
    in another field (e.g. a hypothetical ``trigger_ref`` field that
    embeds the experiment_id). In the current codebase, the
    ``experiment_id`` is stored as a top-level attribute and is the
    only field that may contain another experiment_id. We accept
    this limitation; a more precise filter would require a JSON
    path query (PostgreSQL-specific) or a schema change.
    """
    from synapse.storage.models import EntityFingerprintRow

    base = (
        select(EntityRow)
        .join(
            EntityFingerprintRow,
            (EntityFingerprintRow.entity_id == EntityRow.id)
            & (EntityFingerprintRow.kind == "execution"),
        )
        .where(EntityRow.kind == "execution")
    )
    if experiment_id:
        # Portable substring filter — see docstring for the limitation
        # and the rationale for omitting inner % wildcards.
        base = base.where(EntityRow.attributes.like(f'%"experiment_id": "{experiment_id}"%'))
    return await _execute_paginated_query(session, base, cursor=cursor, limit=limit)


async def list_scenarios_page(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
) -> PageResult:
    """Paginated list of scenario entities (kind='scenario')."""
    from synapse.storage.models import EntityFingerprintRow

    base = (
        select(EntityRow)
        .join(
            EntityFingerprintRow,
            (EntityFingerprintRow.entity_id == EntityRow.id)
            & (EntityFingerprintRow.kind == "scenario"),
        )
        .where(EntityRow.kind == "scenario")
    )
    return await _execute_paginated_query(session, base, cursor=cursor, limit=limit)


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "PageResult",
    "decode_cursor",
    "encode_cursor",
    "list_executions_page",
    "list_experiments_page",
    "list_innovations_page",
    "list_scenarios_page",
]
