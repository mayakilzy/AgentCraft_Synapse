"""Atomic fingerprint-claim helper for idempotent entity persistence.

Per PRB-03 permanent closure: the previous read-before-insert pattern
(SELECT fingerprint, then INSERT if not found) was racy. Two concurrent
requests with the same fingerprint could both pass the SELECT and both
INSERT, producing duplicate entities.

This module provides ``claim_fingerprint()`` — a single atomic
operation that:

1. Attempts an INSERT into ``entity_fingerprints`` with the given
   (kind, fingerprint, entity_id).
2. On PostgreSQL, uses ``INSERT ... ON CONFLICT (kind, fingerprint)
   DO NOTHING`` so the INSERT is a single statement that either wins
   or is silently ignored.
3. On SQLite, catches ``IntegrityError`` and retries once (SELECT to
   find the winner).
4. After the claim attempt, SELECTs the ``entity_fingerprints`` row
   for the (kind, fingerprint) pair. If the returned ``entity_id``
   matches the one we tried to insert, we won — proceed to INSERT the
   entity. If it differs, we lost — reuse the winning entity_id.

This is deterministic, race-free, and works on both PostgreSQL and
SQLite. No distributed lock, no Redis, no message broker.

Critical safety rules:
- The caller MUST have the entity_id deterministic (derived from the
  fingerprint) so that winning the claim and inserting the entity are
  the same operation.
- The caller MUST NOT suppress unrelated ``IntegrityError`` exceptions.
  This helper catches ``IntegrityError`` ONLY on the fingerprint claim
  INSERT, and ONLY when the underlying dialect is SQLite (PostgreSQL
  uses ON CONFLICT which never raises). Any other IntegrityError
  propagates.
- The claim is bounded: at most 1 retry on SQLite. No infinite loop.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.observability.logging import get_logger
from synapse.storage.models import EntityFingerprintRow

_log = get_logger("synapse.storage.fingerprint")


async def claim_fingerprint(
    session: AsyncSession,
    *,
    kind: str,
    fingerprint: str,
    entity_id: str,
    claim_id: str,
) -> tuple[str, bool]:
    """Atomically claim a (kind, fingerprint) pair.

    Args:
        session: AsyncSession bound to the Synapse database.
        kind: entity kind (e.g. ``"project"``, ``"experiment"``).
        fingerprint: the deterministic fingerprint (SHA-256 hex).
        entity_id: the deterministic entity ID derived from the fingerprint
            (e.g. ``f"innov-{fingerprint[:16]}"``). The caller must use
            this same ID when inserting the EntityRow.
        claim_id: a unique ID for the EntityFingerprintRow itself (e.g.
            ``f"efp-{fingerprint[:16]}"``).

    Returns:
        ``(winning_entity_id, we_won)`` where:
        - ``winning_entity_id`` is the entity_id that owns this fingerprint.
        - ``we_won`` is ``True`` if we are the owner (we should proceed to
          insert the EntityRow), ``False`` if another request already owns
          it (we should reuse their entity_id).

    Raises:
        IntegrityError: only if an unexpected integrity violation occurs
        (not the expected (kind, fingerprint) unique violation, which is
        handled). This ensures unrelated IntegrityErrors propagate.

    Notes:
        - On PostgreSQL, uses ``INSERT ... ON CONFLICT DO NOTHING`` — a
          single atomic statement. No retry needed.
        - On SQLite, ON CONFLICT is not reliably supported across versions,
          so we use try/except IntegrityError + a single SELECT retry.
        - The session is NOT committed here — the caller owns the
          transaction. The INSERT is flushed so the UNIQUE constraint can
          fire within the current transaction.
    """
    # Detect dialect
    dialect_name = session.bind.dialect.name if session.bind else "unknown"

    if dialect_name == "postgresql":
        # Use a transaction-scoped advisory lock to serialize claims on
        # the same (kind, fingerprint) pair. This prevents deadlocks that
        # occur when concurrent transactions try ON CONFLICT DO NOTHING on
        # overlapping fingerprint sets (e.g., generate_innovations creates
        # multiple concepts per call, and concurrent calls can deadlock
        # when they acquire index locks in different orders).
        #
        # The advisory lock is:
        # - Transaction-scoped (released on commit/rollback automatically)
        # - Non-blocking for DIFFERENT fingerprints (parallelism preserved)
        # - Bounded (no infinite loop — pg_advisory_xact_lock waits until
        #   the holder commits/rolls back, then proceeds)
        #
        # We use a stable hash of (kind, fingerprint) → bigint for the
        # lock key. PostgreSQL advisory lock keys are int64.
        import hashlib

        lock_key = int.from_bytes(
            hashlib.sha256(f"{kind}:{fingerprint}".encode()).digest()[:8],
            "little",
            signed=True,
        )
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

        # Now we hold the lock — SELECT then INSERT. No other transaction
        # can be concurrently inserting the same fingerprint.
        existing = await session.execute(
            select(EntityFingerprintRow).where(
                EntityFingerprintRow.kind == kind,
                EntityFingerprintRow.fingerprint == fingerprint,
            )
        )
        winner = existing.scalar_one_or_none()
        if winner is not None:
            return winner.entity_id, False

        # No existing row — we win. INSERT (cannot conflict because we
        # hold the advisory lock).
        row = EntityFingerprintRow(
            id=claim_id,
            entity_id=entity_id,
            kind=kind,
            fingerprint=fingerprint,
        )
        session.add(row)
        await session.flush()
        return entity_id, True
    else:
        # SQLite (or unknown dialect): try INSERT inside a savepoint so
        # that an IntegrityError only rolls back the savepoint, not the
        # entire session (which would undo prior seeded data in tests).
        row = EntityFingerprintRow(
            id=claim_id,
            entity_id=entity_id,
            kind=kind,
            fingerprint=fingerprint,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            # Savepoint committed — we won.
            return entity_id, True
        except IntegrityError as exc:
            # The savepoint was rolled back automatically by
            # begin_nested()'s context manager on IntegrityError. The
            # outer transaction is intact.
            _log.debug(
                "fingerprint claim conflict (expected for concurrent requests): "
                "kind=%s fingerprint=%s — %s",
                kind,
                fingerprint[:16],
                str(exc)[:120],
            )

    # We lost the claim (or we're on SQLite and caught the conflict).
    # SELECT the winning row.
    select_stmt = select(EntityFingerprintRow).where(
        EntityFingerprintRow.kind == kind,
        EntityFingerprintRow.fingerprint == fingerprint,
    )
    result = await session.execute(select_stmt)
    winner = result.scalar_one_or_none()

    if winner is None:
        # On PostgreSQL with ON CONFLICT, this shouldn't happen — if we
        # lost, the winner row exists (ON CONFLICT DO NOTHING means another
        # row with the same (kind, fingerprint) already exists). Re-raise.
        if dialect_name == "postgresql":
            raise IntegrityError(
                "ON CONFLICT DO NOTHING returned 0 rows but SELECT found no "
                f"winner for kind={kind} fingerprint={fingerprint[:16]} — "
                "this indicates a schema or transaction bug",
                params=None,
                orig=None,
            ) from None
        # SQLite: the winner may be in a separate session that hasn't
        # committed yet (concurrent test). Retry the claim once inside a
        # new savepoint (bounded — no infinite loop).
        try:
            async with session.begin_nested():
                session.add(
                    EntityFingerprintRow(
                        id=claim_id,
                        entity_id=entity_id,
                        kind=kind,
                        fingerprint=fingerprint,
                    )
                )
                await session.flush()
            return entity_id, True
        except IntegrityError as exc2:
            # Still conflicting but no winner found — genuine error.
            raise IntegrityError(
                "SQLite fingerprint claim failed twice with no winner — "
                "possible transaction isolation issue",
                params=None,
                orig=exc2,
            ) from exc2

    return winner.entity_id, False


async def find_entity_id_by_fingerprint(
    session: AsyncSession,
    *,
    kind: str,
    fingerprint: str,
) -> str | None:
    """Find the entity_id that owns a (kind, fingerprint) pair.

    Returns the entity_id, or ``None`` if no row exists. This is the
    non-claiming lookup (used after a lost claim to find the winner).
    """
    stmt = select(EntityFingerprintRow).where(
        EntityFingerprintRow.kind == kind,
        EntityFingerprintRow.fingerprint == fingerprint,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.entity_id if row else None
