"""Add entities, claims, and source_spans tables for G03-T01.

Per the user's G03-T01 authorization: minimal knowledge extraction
slice. Adds three tables:

  - entities: canonical entity registry (Entity domain model)
  - claims: propositions with epistemic state (Claim domain model)
  - source_spans: precise evidence references (SourceSpan contract)

All tables are additive — no ALTER on existing tables. Downgrade drops
only these three tables.

Revision ID: 0003_knowledge_tables
Revises: 0002_evidence_fragments
Create Date: 2026-10-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_knowledge_tables"
down_revision: str | None = "0002_evidence_fragments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── entities ───────────────────────────────────────────────────
    op.create_table(
        "entities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.String(512), nullable=False),
        sa.Column("aliases", sa.Text, nullable=True),  # JSON array of strings
        sa.Column("attributes", sa.Text, nullable=True),  # JSON dict
        sa.Column("canonical_uri", sa.String(2048), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_entities_canonical_name", "entities", ["canonical_name"])
    op.create_index("ix_entities_kind", "entities", ["kind"])
    op.create_index("ix_entities_canonical_uri", "entities", ["canonical_uri"])

    # ── claims ──────────────────────────────────────────────────────
    op.create_table(
        "claims",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("proposition", sa.String(2048), nullable=False),
        sa.Column("subject_ref", sa.String(64), nullable=True),  # Entity ID
        sa.Column("object_ref", sa.String(64), nullable=True),   # Entity ID
        sa.Column("evidence_refs", sa.Text, nullable=True),  # JSON array of fragment IDs
        sa.Column("epistemic_state", sa.String(32), nullable=False, server_default="hypothesized"),
        sa.Column("confidence_value", sa.Float, nullable=True),
        sa.Column("confidence_method", sa.String(256), nullable=True),
        sa.Column("validity_conditions", sa.Text, nullable=True),  # JSON array
        sa.Column("contradicting_refs", sa.Text, nullable=True),  # JSON array
        sa.Column("superseded_by", sa.String(64), nullable=True),
        sa.Column("extraction_method", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_claims_epistemic_state", "claims", ["epistemic_state"])
    op.create_index("ix_claims_subject_ref", "claims", ["subject_ref"])
    op.create_index("ix_claims_object_ref", "claims", ["object_ref"])

    # ── source_spans ────────────────────────────────────────────────
    op.create_table(
        "source_spans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evidence_fragment_id", sa.String(64), nullable=False),
        sa.Column("claim_id", sa.String(64), nullable=True),  # nullable: spans can predate the claim
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("excerpt", sa.Text, nullable=False),
        sa.Column("context_before", sa.Text, nullable=True),
        sa.Column("context_after", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_source_spans_evidence_fragment_id", "source_spans", ["evidence_fragment_id"])
    op.create_index("ix_source_spans_claim_id", "source_spans", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_source_spans_claim_id", table_name="source_spans")
    op.drop_index("ix_source_spans_evidence_fragment_id", table_name="source_spans")
    op.drop_table("source_spans")
    op.drop_index("ix_claims_object_ref", table_name="claims")
    op.drop_index("ix_claims_subject_ref", table_name="claims")
    op.drop_index("ix_claims_epistemic_state", table_name="claims")
    op.drop_table("claims")
    op.drop_index("ix_entities_canonical_uri", table_name="entities")
    op.drop_index("ix_entities_kind", table_name="entities")
    op.drop_index("ix_entities_canonical_name", table_name="entities")
    op.drop_table("entities")
