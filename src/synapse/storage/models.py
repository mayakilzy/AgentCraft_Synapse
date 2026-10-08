"""Minimal ORM models for G01.

These tables exist so that:

1. Alembic has something to migrate up/down (acceptance evidence for G01-T03).
2. The idempotency table exists for the API contract (per ADR-0006).
3. The audit log table exists to record EvidenceDelta events (per ADR-0005).

The full domain tables (sources_full, acquisitions, evidence_fragments,
entities, claims, relationships, capabilities, hypotheses, experiments,
scenarios, jobs_full) are added in later groups as their use-cases go live.
G01 ships a minimal-but-real persistence layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from synapse.storage.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SourceRow(Base):
    """Persistent Source record (minimal G01 projection)."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    publisher: Mapped[str | None] = mapped_column(String(512), nullable=True)
    author: Mapped[str | None] = mapped_column(String(512), nullable=True)
    license: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_sources_canonical_uri", "canonical_uri"),
        Index("ix_sources_status", "status"),
    )


class JobRow(Base):
    """Persistent Job record."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    requester: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(default=0.0)
    input_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_idempotency_key", "idempotency_key"),
    )


class IdempotencyKeyRow(Base):
    """Stores Idempotency-Key → response mapping for safe retries.

    Per ADR-0006: full enforcement is deferred to G02, but the table
    exists so we can ship the contract now.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("key", "endpoint", name="uq_idempotency_key_endpoint"),)


class AuditEventRow(Base):
    """Append-only audit log. EvidenceDelta events land here in G01."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(256), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_audit_events_target", "target_type", "target_id"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_created_at", "created_at"),
    )


class CapabilityRow(Base):
    """Minimal Capability registry row (used by /capabilities endpoint)."""

    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provider_mappings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (UniqueConstraint("name", name="uq_capabilities_name"),)


class ProviderRow(Base):
    """Minimal Provider registry row (used by /providers endpoint)."""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # llm | fetch | search
    enabled: Mapped[bool] = mapped_column(default=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("name", name="uq_providers_name"),
        Index("ix_providers_kind", "kind"),
    )


class EvidenceFragmentRow(Base):
    """Persistent EvidenceFragment row.

    Added in G02 minimal slice (migration 0002_evidence_fragments).
    Persists extracted text + provenance per DOMAIN_AND_API_CONTRACTS.md
    invariant §1 ("every verified claim and explicit relationship has at
    least one inspectable evidence fragment").
    """

    __tablename__ = "evidence_fragments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    acquisition_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    document_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exact_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Provenance metadata per user's G02 authorization §Architecture.
    # Unknown metadata stays null (never fabricated).
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieved_at: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citation_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    toolkit_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str | None] = mapped_column(String(256), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_evidence_fragments_acquisition_id", "acquisition_id"),
        Index("ix_evidence_fragments_source_id", "source_id"),
        Index("ix_evidence_fragments_content_fingerprint", "content_fingerprint"),
        Index("ix_evidence_fragments_excerpt_hash", "excerpt_hash"),
    )
