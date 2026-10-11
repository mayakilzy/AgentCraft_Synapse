"""G07-PRE-02 + G07-PRE-02A — Minimal cursor-based pagination helper for
the four list endpoints (innovations, experiments, executions, scenarios).

This module is INTENTIONALLY narrow in scope:

- It is NOT a generic repository abstraction.
- It is NOT a new pagination framework applied to existing endpoints.
- It does NOT touch any existing list endpoint (``GET /api/v1/sources``,
  ``GET /api/v1/entities``, ``GET /api/v1/relationships``,
  ``GET /api/v1/knowledge/capabilities`` — all unchanged, all keep their
  existing ``limit``-only pagination per G07-GAP-006 deferred).
- It is consumed ONLY by the four new list routes added in G07-PRE-02.

Contract (per G07-PRE-02 mission §3 + G07-PRE-02A mission §4):

- Bounded ``limit`` (1..100, default 20).
- Opaque ``cursor`` — base64url-encoded JSON with the shape::

      {"ts": "<ISO 8601>", "id": "<entity_id>", "k": "<kind>", "f": "<filter|None>"}

  The ``k`` (resource kind) and ``f`` (effective filter signature) fields
  bind each cursor to the resource type and filter that issued it. A
  cursor issued by ``GET /api/v1/innovations`` cannot be reused for
  ``GET /api/v1/experiments`` — the ``k`` mismatch produces 422. A
  cursor issued by ``GET /api/v1/executions?experiment_id=X`` cannot be
  reused for ``GET /api/v1/executions`` (no filter) or with a different
  ``experiment_id`` — the ``f`` mismatch produces 422.

  Cursor tokens issued by G07-PRE-02 (without ``k`` and ``f`` fields)
  are now INVALID because no frontend has been released yet (per
  G07-PRE-02A §4: "explicitly document their invalidation").

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

from sqlalchemy import Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import JSON

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


def encode_cursor(ts: str, entity_id: str, *, kind: str, filter_sig: str | None) -> str:
    """Encode a (timestamp, id, kind, filter_sig) tuple as an opaque
    base64url cursor.

    The cursor is ``base64url(json.dumps({"ts": ts, "id": entity_id,
    "k": kind, "f": filter_sig}))``. It is opaque to clients (they
    should treat it as a string) but decodable for debugging and
    round-trip tests.

    The ``kind`` and ``filter_sig`` fields bind the cursor to the
    resource type and effective filter, preventing cross-resource
    or cross-filter cursor reuse (per G07-PRE-02A §4).
    """
    payload = json.dumps(
        {"ts": ts, "id": entity_id, "k": kind, "f": filter_sig},
        separators=(",", ":"),
    )
    raw = payload.encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(
    cursor: str,
    *,
    expected_kind: str,
    expected_filter: str | None,
) -> tuple[str, str]:
    """Decode and validate an opaque cursor.

    Args:
        cursor: the base64url cursor string from the client.
        expected_kind: the resource kind this cursor must match (e.g.
            ``"project"`` for innovations, ``"experiment"`` for
            experiments, ``"execution"`` for executions, ``"scenario"``
            for scenarios).
        expected_filter: the effective filter signature this cursor must
            match. For endpoints without a filter, pass ``None``. For
            ``GET /api/v1/executions?experiment_id=X``, pass ``X``.

    Raises ``ValidationError`` (422) on:
    - Empty cursor.
    - base64url decode failure (malformed padding/chars).
    - JSON decode failure (malformed payload).
    - Missing or non-string ``ts``, ``id``, ``k`` fields.
    - Missing or impossible timestamp value.
    - ``k`` field does not match ``expected_kind`` (cross-resource cursor
      reuse).
    - ``f`` field does not match ``expected_filter`` (cross-filter cursor
      reuse — e.g. using a cursor from ``?experiment_id=X`` on an
      unfiltered request, or vice versa).
    - Cursor payload missing ``k`` or ``f`` fields entirely (cursor
      issued by G07-PRE-02 before the kind/filter binding was added —
      now invalid per G07-PRE-02A §4).

    Returns ``(ts_string, entity_id_string)``.
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
    k = payload.get("k")
    f = payload.get("f")

    # Required fields: ts, id, k (string, non-empty).
    if not isinstance(ts, str) or not isinstance(eid, str) or not isinstance(k, str):
        raise ValidationError(
            "cursor payload must contain string 'ts', 'id', and 'k'"
        )
    if not ts or not eid or not k:
        raise ValidationError("cursor 'ts', 'id', and 'k' must be non-empty")

    # f may be None (no filter) or a non-empty string.
    if f is not None and not isinstance(f, str):
        raise ValidationError("cursor 'f' must be a string or null")
    if f is not None and not f:
        raise ValidationError("cursor 'f' must be non-empty when present")

    # Validate the timestamp is parseable.
    _parse_cursor_ts(ts)

    # Cross-resource check: cursor kind must match the endpoint's kind.
    if k != expected_kind:
        raise ValidationError(
            f"cursor was issued for resource kind '{k}' but this endpoint "
            f"serves '{expected_kind}' — cursor cannot be reused across "
            f"resource types"
        )

    # Cross-filter check: cursor filter must match the endpoint's filter.
    expected_f = expected_filter  # None or string
    if f != expected_f:
        f_desc = f"filter '{f}'" if f else "no filter"
        ef_desc = f"filter '{expected_f}'" if expected_f else "no filter"
        raise ValidationError(
            f"cursor was issued with {f_desc} but this request has "
            f"{ef_desc} — cursor cannot be reused across different filters"
        )

    return ts, eid


def _parse_cursor_ts(ts: str) -> datetime:
    """Parse a cursor's ISO 8601 timestamp string into a datetime.

    Raises ``ValidationError`` if the string is not a valid ISO 8601
    timestamp. Used by the pagination query builder so that the
    ``EntityRow.created_at`` (a datetime column) is compared against a
    datetime value, not a string — avoiding dialect-specific implicit
    casts that behave differently on SQLite vs PostgreSQL.
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
    expected_kind: str,
    expected_filter: str | None,
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
        ts_str, eid = decode_cursor(
            cursor,
            expected_kind=expected_kind,
            expected_filter=expected_filter,
        )
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
    expected_kind: str,
    expected_filter: str | None,
) -> PageResult:
    """Execute a paginated query against ``EntityRow`` and return a PageResult.

    The base statement must select ``EntityRow`` rows directly
    (e.g. ``select(EntityRow).where(EntityRow.kind == "...")``).
    """
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise ValidationError(
            f"limit must be in [{MIN_LIMIT}, {MAX_LIMIT}]; got {limit}"
        )

    stmt = _build_paginated_select(
        base_stmt,
        cursor=cursor,
        limit=limit,
        expected_kind=expected_kind,
        expected_filter=expected_filter,
    )
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
        next_cursor = encode_cursor(ts, last.id, kind=expected_kind, filter_sig=expected_filter)

    return PageResult(
        items=page_rows,
        pagination=PaginationMeta(
            next_cursor=next_cursor,
            prev_cursor=None,  # prev_cursor not supported in this minimal scope
            limit=limit,
            total=None,  # total is expensive on large tables; left None for now
        ),
    )


# ── Exact JSON-path filter (G07-PRE-02A Task A) ────────────────────────────


def _json_key_equals(column, key: str, value: str, dialect_name: str):
    """Build a portable WHERE clause that compares a top-level key in a
    Text-column-of-JSON against an exact string value.

    On SQLite: uses ``json_extract(column, '$.key') = value``.
    On PostgreSQL: uses ``CAST(column AS JSON) ->> 'key' = value``
    (which is equivalent to ``column::json ->> 'key' = value``).

    This is an EXACT match — ``%`` and ``_`` in the value are treated as
    ordinary characters (not LIKE wildcards), and the value must match
    the full key value (substrings do not match). Only the top-level key
    is compared; the value will not match if it appears only in a
    nested field or a different top-level field.

    Args:
        column: the SQLAlchemy Text column (e.g. ``EntityRow.attributes``).
        key: the top-level JSON key to extract.
        value: the exact string value to compare against.
        dialect_name: ``session.bind.dialect.name`` — ``"sqlite"`` or
            ``"postgresql"``.
    """
    if dialect_name == "postgresql":
        # PostgreSQL: cast Text to JSON, then use the ->> operator for
        # text extraction. as_string() ensures a text comparison.
        return cast(column, JSON)[key].as_string() == value
    # SQLite (default): use the native json_extract function.
    return func.json_extract(column, f"$.{key}") == value


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
    return await _execute_paginated_query(
        session, base, cursor=cursor, limit=limit,
        expected_kind="project", expected_filter=None,
    )


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
    return await _execute_paginated_query(
        session, base, cursor=cursor, limit=limit,
        expected_kind="experiment", expected_filter=None,
    )


async def list_executions_page(
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
    experiment_id: str | None,
) -> PageResult:
    """Paginated list of execution record entities (kind='execution').

    Optional filter by ``experiment_id``: filters on the
    ``attributes`` JSON column's top-level ``experiment_id`` key using an
    EXACT match (not a SQL LIKE substring match). The match is portable
    across SQLite (``json_extract``) and PostgreSQL (``->>`` operator).

    The exact match treats ``%``, ``_``, quotes, and other special
    characters as ordinary data — they are not interpreted as LIKE
    wildcards. Only the top-level ``experiment_id`` key is compared; the
    value will not match if the same ID appears only in a different
    field (e.g. a ``trigger_ref`` field that embeds an experiment_id).

    Per G07-PRE-02A Task A: this replaces the previous SQL LIKE approach
    which could match substrings and wildcard characters.
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
    filter_sig: str | None = None
    if experiment_id:
        filter_sig = experiment_id
        dialect_name = session.bind.dialect.name if session.bind else "sqlite"
        base = base.where(
            _json_key_equals(
                EntityRow.attributes, "experiment_id", experiment_id, dialect_name
            )
        )
    return await _execute_paginated_query(
        session, base, cursor=cursor, limit=limit,
        expected_kind="execution", expected_filter=filter_sig,
    )


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
    return await _execute_paginated_query(
        session, base, cursor=cursor, limit=limit,
        expected_kind="scenario", expected_filter=None,
    )


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
