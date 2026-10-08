"""Initial schema for AgentCraft Synapse.

Creates the minimal set of tables required by G01:
  - sources
  - jobs
  - idempotency_keys
  - audit_events
  - capabilities
  - providers

These tables are dialect-agnostic — they use only ANSI SQL types so they
work identically on PostgreSQL (production) and SQLite (tests / local dev).
pgvector-dependent columns are deferred to a later migration when measured
workload justifies them (per ADR-0002).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # sources
    op.create_table(
        "sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_uri", sa.String(2048), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("publisher", sa.String(512), nullable=True),
        sa.Column("author", sa.String(512), nullable=True),
        sa.Column("license", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="discovered"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sources_canonical_uri", "sources", ["canonical_uri"])
    op.create_index("ix_sources_status", "sources", ["status"])

    # jobs
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="custom"),
        sa.Column("requester", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("input_ref", sa.Text, nullable=True),
        sa.Column("output_ref", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"])

    # idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("endpoint", sa.String(256), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("response_body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("key", "endpoint", name="uq_idempotency_key_endpoint"),
    )

    # audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(256), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("target_type", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # capabilities
    op.create_table(
        "capabilities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("outputs", sa.JSON, nullable=False),
        sa.Column("provider_mappings", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_capabilities_name"),
    )

    # providers
    op.create_table(
        "providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_providers_name"),
    )
    op.create_index("ix_providers_kind", "providers", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_providers_kind", table_name="providers")
    op.drop_table("providers")
    op.drop_table("capabilities")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_jobs_idempotency_key", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_sources_status", table_name="sources")
    op.drop_index("ix_sources_canonical_uri", table_name="sources")
    op.drop_table("sources")
