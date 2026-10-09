"""Add relationships table for G03-T03.

Per the user's G03-T03 authorization: minimal evidence-backed
RelationshipService. Adds one table:

  - relationships: typed, directed edges between entities

All additive — no ALTER on existing tables. Downgrade drops only
this table.

Revision ID: 0004_relationships
Revises: 0003_knowledge_tables
Create Date: 2026-10-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_relationships"
down_revision: str | None = "0003_knowledge_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "relationships",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("from_entity_id", sa.String(64), nullable=False),
        sa.Column("to_entity_id", sa.String(64), nullable=False),
        sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("direction", sa.String(32), nullable=False, server_default="directed"),
        sa.Column("origin", sa.String(32), nullable=False, server_default="explicit"),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="unverified"),
        sa.Column("evidence_refs", sa.Text, nullable=True),  # JSON array of fragment IDs
        sa.Column("confidence_value", sa.Float, nullable=True),
        sa.Column("confidence_method", sa.String(256), nullable=True),
        sa.Column("conditions", sa.Text, nullable=True),  # JSON array
        sa.Column("valid_from", sa.String(64), nullable=True),
        sa.Column("valid_to", sa.String(64), nullable=True),
        sa.Column("superseded_by", sa.String(64), nullable=True),
        sa.Column("derivation_chain", sa.Text, nullable=True),  # JSON array
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_relationships_from_predicate", "relationships", ["from_entity_id", "predicate"])
    op.create_index("ix_relationships_to_predicate", "relationships", ["to_entity_id", "predicate"])
    op.create_index("ix_relationships_origin_verification", "relationships", ["origin", "verification_state"])
    op.create_index("ix_relationships_from_to", "relationships", ["from_entity_id", "to_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_relationships_from_to", table_name="relationships")
    op.drop_index("ix_relationships_origin_verification", table_name="relationships")
    op.drop_index("ix_relationships_to_predicate", table_name="relationships")
    op.drop_index("ix_relationships_from_predicate", table_name="relationships")
    op.drop_table("relationships")
