"""Add evidence_fragments table for G02 minimal slice.

The G01 schema created sources, jobs, idempotency_keys, audit_events,
capabilities, and providers. The minimal G02 slice needs an
evidence_fragments table to persist extracted text + provenance per
`DOMAIN_AND_API_CONTRACTS.md` invariant §1.

Per ADR-0009 §1: this migration is additive — it does NOT modify any
existing table. Downgrade drops only `evidence_fragments`.

Revision ID: 0002_evidence_fragments
Revises: 0001_initial
Create Date: 2026-10-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_evidence_fragments"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_fragments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("acquisition_id", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("source_uri", sa.String(2048), nullable=True),
        sa.Column("document_version", sa.String(64), nullable=True),
        sa.Column("exact_excerpt", sa.Text, nullable=True),
        sa.Column("excerpt_hash", sa.String(128), nullable=True),
        # Provenance metadata (per user's G02 authorization §Architecture)
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("author", sa.String(1024), nullable=True),
        sa.Column("published_at", sa.String(64), nullable=True),
        sa.Column("retrieved_at", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=True),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("citation_ids", sa.Text, nullable=True),  # JSON array of strings
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("toolkit_commit_sha", sa.String(64), nullable=True),
        sa.Column("extraction_method", sa.String(64), nullable=False),
        sa.Column("section", sa.String(256), nullable=True),
        sa.Column("page", sa.Integer, nullable=True),
        sa.Column("line", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_evidence_fragments_acquisition_id",
        "evidence_fragments",
        ["acquisition_id"],
    )
    op.create_index(
        "ix_evidence_fragments_source_id",
        "evidence_fragments",
        ["source_id"],
    )
    op.create_index(
        "ix_evidence_fragments_content_fingerprint",
        "evidence_fragments",
        ["content_fingerprint"],
    )
    op.create_index(
        "ix_evidence_fragments_excerpt_hash",
        "evidence_fragments",
        ["excerpt_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_fragments_excerpt_hash", table_name="evidence_fragments"
    )
    op.drop_index(
        "ix_evidence_fragments_content_fingerprint",
        table_name="evidence_fragments",
    )
    op.drop_index(
        "ix_evidence_fragments_source_id", table_name="evidence_fragments"
    )
    op.drop_index(
        "ix_evidence_fragments_acquisition_id",
        table_name="evidence_fragments",
    )
    op.drop_table("evidence_fragments")
