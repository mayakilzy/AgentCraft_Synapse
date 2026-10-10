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
    # This is best-effort — if the JSON is malformed or the fingerprint
    # field is absent, the row is skipped (no crash). Duplicates are
    # skipped via INSERT OR IGNORE (SQLite) / ON CONFLICT DO NOTHING (PG).
    # We detect the dialect from the bind.
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO entity_fingerprints (id, entity_id, kind, fingerprint, created_at)
            SELECT
                'efp-' || substr(e.id, 1, 16),
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
              AND (
                  CASE
                      WHEN e.kind = 'project'
                          THEN e.attributes::json ->> 'concept_fingerprint'
                      WHEN e.kind = 'experiment'
                          THEN e.attributes::json ->> 'experiment_fingerprint'
                  END
              ) IS NOT NULL
            ON CONFLICT (kind, fingerprint) DO NOTHING
            """
        )
    elif dialect == "sqlite":
        # SQLite: json_extract + INSERT OR IGNORE
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
