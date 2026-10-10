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
    # Per PRB-03 FINAL AUDIT + JSON safety patch: the previous version
    # used a regex ('^\s*\{.*\}\s*$') as a JSON guard, but a regex is NOT
    # a JSON validator — strings like '{"name": }' pass the regex but
    # crash the PostgreSQL ::json cast with InvalidTextRepresentation.
    #
    # This version uses GENUINE JSON validation:
    # - PostgreSQL: a PL/pgSQL DO block with BEGIN/EXCEPTION catches
    #   invalid_text_representation. Malformed JSON entities are
    #   collected and the migration FAILS CLOSED with a diagnostic
    #   listing the entity IDs.
    # - SQLite: json_valid() function (available since SQLite 3.9).
    #   Malformed JSON entities are also fail-closed.
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Step 1: Genuinely validate JSON for all candidate entities.
        # Use a PL/pgSQL DO block with exception handling — NOT a regex.
        # Collect entity IDs with malformed JSON attributes, then raise.
        # Use op.execute() (not bind.execute(text(...))) to avoid
        # SQLAlchemy parameter-binding interference with PL/pgSQL syntax.
        op.execute(
            """
            DO $$
            DECLARE
                rec RECORD;
                malformed_ids text[] := '{}';
                fp text;
            BEGIN
                FOR rec IN
                    SELECT e.id, e.kind, e.attributes
                    FROM entities e
                    WHERE e.kind IN ('project', 'experiment')
                      AND e.attributes IS NOT NULL
                      AND e.attributes != ''
                LOOP
                    BEGIN
                        IF rec.kind = 'project' THEN
                            fp := rec.attributes::json ->> 'concept_fingerprint';
                        ELSE
                            fp := rec.attributes::json ->> 'experiment_fingerprint';
                        END IF;
                    EXCEPTION
                        WHEN invalid_text_representation THEN
                            malformed_ids := array_append(malformed_ids, rec.id);
                        WHEN others THEN
                            malformed_ids := array_append(malformed_ids, rec.id);
                    END;
                END LOOP;

                IF array_length(malformed_ids, 1) IS NOT NULL THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'PRB-03_MALFORMED_JSON_ENTITIES: ' ||
                        array_to_string(malformed_ids, ', ');
                END IF;
            END;
            $$;
            """
        )

        # Step 2: Detect duplicate fingerprints (fail-closed).
        # All JSON is now validated — safe to cast without regex guard.
        duplicate_check = bind.execute(
            text(
                """
                WITH extracted AS (
                    SELECT
                        e.id AS entity_id,
                        e.kind,
                        CASE
                            WHEN e.kind = 'project'
                                THEN e.attributes::json ->> 'concept_fingerprint'
                            WHEN e.kind = 'experiment'
                                THEN e.attributes::json ->> 'experiment_fingerprint'
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
            dup_report = "; ".join(
                f"fingerprint={row[0][:16]}... count={row[1]} entities=[{row[2]}]"
                for row in duplicate_check
            )
            raise RuntimeError(
                f"PRB-03 migration 0005 cannot backfill: found duplicate "
                f"fingerprints in legacy entities. Resolve manually before "
                f"re-running. Duplicates: {dup_report}"
            )

        # Step 3: Backfill (no regex guard needed — JSON is validated).
        op.execute(
            """
            INSERT INTO entity_fingerprints (id, entity_id, kind, fingerprint, created_at)
            SELECT
                'efp-' || e.id,
                e.id,
                e.kind,
                CASE
                    WHEN e.kind = 'project'
                        THEN e.attributes::json ->> 'concept_fingerprint'
                    WHEN e.kind = 'experiment'
                        THEN e.attributes::json ->> 'experiment_fingerprint'
                END,
                now()
            FROM entities e
            WHERE e.kind IN ('project', 'experiment')
              AND e.attributes IS NOT NULL
              AND e.attributes != ''
              AND CASE
                      WHEN e.kind = 'project'
                          THEN e.attributes::json ->> 'concept_fingerprint'
                      WHEN e.kind = 'experiment'
                          THEN e.attributes::json ->> 'experiment_fingerprint'
                  END IS NOT NULL
            ON CONFLICT (kind, fingerprint) DO NOTHING
            """
        )
    elif dialect == "sqlite":
        # SQLite: json_valid() is available since SQLite 3.9 (2016).
        # Step 1: Detect malformed JSON (fail-closed).
        malformed_check = bind.execute(
            text(
                """
                SELECT group_concat(e.id, ', ') AS malformed_entities
                FROM entities e
                WHERE e.kind IN ('project', 'experiment')
                  AND e.attributes IS NOT NULL
                  AND e.attributes != ''
                  AND json_valid(e.attributes) = 0
                """
            )
        ).fetchone()

        if malformed_check and malformed_check[0]:
            raise RuntimeError(
                f"PRB-03 migration 0005 cannot backfill: found entities with "
                f"malformed JSON in attributes column. Fix before re-running. "
                f"Malformed entity IDs: {malformed_check[0]}"
            )

        # Step 2: Detect duplicate fingerprints (fail-closed).
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

        # Step 3: Backfill.
        op.execute(
            """
            INSERT OR IGNORE INTO entity_fingerprints (id, entity_id, kind, fingerprint, created_at)
            SELECT
                'efp-' || e.id,
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
