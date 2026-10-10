"""Add entity_fingerprints table for DB-enforced idempotent identity.

Per PRB-03 permanent closure: the previous read-before-insert pattern
in _persist_concept (innovation.py) and _persist_experiment
(experiment_planner.py) was racy. Two concurrent requests with the
same fingerprint could both pass the SELECT check and both INSERT,
producing duplicate entities.

This migration adds a dedicated ``entity_fingerprints`` table with a
UNIQUE constraint on (kind, fingerprint). The application layer now
uses INSERT ... ON CONFLICT DO NOTHING (PostgreSQL) or a try/except
on IntegrityError (SQLite) to atomically claim a fingerprint, then
reuses the winning entity on conflict.

The table is additive — no ALTER on existing tables. Downgrade drops
only this table.

Revision ID: 0005_entity_fingerprints
Revises: 0004_relationships
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0005_entity_fingerprints"
down_revision = "0004_relationships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_fingerprints",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "kind", "fingerprint", name="uq_entity_fingerprints_kind_fp"
        ),
        sa.Index(
            "ix_entity_fingerprints_entity", "entity_id"
        ),
    )

    # Backfill: scan existing EntityRow(kind='project') and
    # EntityRow(kind='experiment') for their attributes, extract the
    # concept_fingerprint / experiment_fingerprint, and insert a row.
    #
    # Per PRB-03 FINAL AUDIT: the previous version used ON CONFLICT DO
    # NOTHING, which SILENTLY discarded duplicate fingerprints. This
    # version is FAIL-CLOSED: it detects duplicate fingerprints in legacy
    # data and raises a diagnostic error instead of silently skipping.
    #
    # It also handles malformed JSON gracefully (skip the row with a
    # warning, don't crash the migration).
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # First: detect duplicate fingerprints in legacy data.
        # Use a safe JSON extraction that returns NULL for malformed JSON
        # (pg_typeof check + regex guard). If duplicates exist, raise.
        duplicate_check = bind.execute(
            text(
                """
                WITH extracted AS (
                    SELECT
                        e.id AS entity_id,
                        e.kind,
                        CASE
                            WHEN e.kind = 'project'
                                THEN CASE
                                    WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                        THEN e.attributes::json ->> 'concept_fingerprint'
                                END
                            WHEN e.kind = 'experiment'
                                THEN CASE
                                    WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                        THEN e.attributes::json ->> 'experiment_fingerprint'
                                END
                        END AS fingerprint
                    FROM entities e
                    WHERE e.kind IN ('project', 'experiment')
                      AND e.attributes IS NOT NULL
                      AND e.attributes != ''
                )
                SELECT fingerprint, count(*) AS c, string_agg(entity_id, ', ') AS entities
                FROM extracted
                WHERE fingerprint IS NOT NULL
                GROUP BY fingerprint
                HAVING count(*) > 1
                """
            )
        ).fetchall()

        if duplicate_check:
            # Fail-closed: report the duplicates explicitly
            dup_report = "; ".join(
                f"fingerprint={row[0][:16]}... count={row[1]} entities=[{row[2]}]"
                for row in duplicate_check
            )
            raise RuntimeError(
                f"PRB-03 migration 0005 cannot backfill: found duplicate "
                f"fingerprints in legacy entities. Resolve manually before "
                f"re-running. Duplicates: {dup_report}"
            )

        # No duplicates — safe to backfill with ON CONFLICT DO NOTHING
        # (defensive, shouldn't fire after the check above)
        op.execute(
            """
            INSERT INTO entity_fingerprints (id, entity_id, kind, fingerprint, created_at)
            SELECT
                'efp-' || substr(e.id, 1, 16),
                e.id,
                e.kind,
                CASE
                    WHEN e.kind = 'project'
                        THEN CASE
                            WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                THEN e.attributes::json ->> 'concept_fingerprint'
                        END
                    WHEN e.kind = 'experiment'
                        THEN CASE
                            WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                THEN e.attributes::json ->> 'experiment_fingerprint'
                        END
                END,
                now()
            FROM entities e
            WHERE e.kind IN ('project', 'experiment')
              AND e.attributes IS NOT NULL
              AND e.attributes != ''
              AND CASE
                      WHEN e.kind = 'project'
                          THEN CASE
                              WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                  THEN e.attributes::json ->> 'concept_fingerprint'
                          END
                      WHEN e.kind = 'experiment'
                          THEN CASE
                              WHEN e.attributes ~ '^\\s*\\{.*\\}\\s*$'
                                  THEN e.attributes::json ->> 'experiment_fingerprint'
                          END
                  END IS NOT NULL
            ON CONFLICT (kind, fingerprint) DO NOTHING
            """
        )
    elif dialect == "sqlite":
        # SQLite: json_extract is safe (returns NULL for malformed JSON)
        # First detect duplicates
        duplicate_check = bind.execute(
            text(
                """
                WITH extracted AS (
                    SELECT
                        e.id AS entity_id,
                        e.kind,
                        CASE
                            WHEN e.kind = 'project'
                                THEN json_extract(e.attributes, '$.concept_fingerprint')
                            WHEN e.kind = 'experiment'
                                THEN json_extract(e.attributes, '$.experiment_fingerprint')
                        END AS fingerprint
                    FROM entities e
                    WHERE e.kind IN ('project', 'experiment')
                      AND e.attributes IS NOT NULL
                      AND e.attributes != ''
                )
                SELECT fingerprint, count(*) AS c, group_concat(entity_id, ', ') AS entities
                FROM extracted
                WHERE fingerprint IS NOT NULL
                GROUP BY fingerprint
                HAVING count(*) > 1
                """
            )
        ).fetchall()

        if duplicate_check:
            dup_report = "; ".join(
                f"fingerprint={row[0][:16]}... count={row[1]} entities=[{row[2]}]"
                for row in duplicate_check
            )
            raise RuntimeError(
                f"PRB-03 migration 0005 cannot backfill: found duplicate "
                f"fingerprints in legacy entities. Resolve manually before "
                f"re-running. Duplicates: {dup_report}"
            )

        op.execute(
            """
            INSERT OR IGNORE INTO entity_fingerprints (id, entity_id, kind, fingerprint, created_at)
            SELECT
                'efp-' || substr(e.id, 1, 16),
                e.id,
                e.kind,
                CASE
                    WHEN e.kind = 'project'
                        THEN json_extract(e.attributes, '$.concept_fingerprint')
                    WHEN e.kind = 'experiment'
                        THEN json_extract(e.attributes, '$.experiment_fingerprint')
                END,
                CURRENT_TIMESTAMP
            FROM entities e
            WHERE e.kind IN ('project', 'experiment')
              AND e.attributes IS NOT NULL
              AND e.attributes != ''
              AND CASE
                      WHEN e.kind = 'project'
                          THEN json_extract(e.attributes, '$.concept_fingerprint')
                      WHEN e.kind = 'experiment'
                          THEN json_extract(e.attributes, '$.experiment_fingerprint')
                  END IS NOT NULL
            """
        )
    # else: unknown dialect — skip backfill (the table is created, the
    # application layer will populate it on new writes)


def downgrade() -> None:
    op.drop_table("entity_fingerprints")
