"""Shared base record & enums for the Synapse domain layer.

All canonical domain records inherit from ``DomainRecord`` and use the enums
defined here. The enums are deliberately string-backed so they serialize
cleanly to JSON and remain stable across Pydantic minor versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Aware UTC now — never naive."""
    return datetime.now(UTC)


class StrEnum(str, Enum):
    """String-backed enum that serializes as its value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ── Cross-cutting enums ───────────────────────────────────────────────────────


class Origin(StrEnum):
    """Where a piece of knowledge came from."""

    EXPLICIT = "explicit"  # directly stated by the source
    DERIVED = "derived"  # inferred from explicit knowledge
    HYPOTHESIZED = "hypothesized"  # proposed without evidence yet


class EpistemicState(StrEnum):
    """Five-axis state of a claim per Master Spec §9."""

    SUPPORTED = "supported"  # has supporting evidence, no contradiction
    INFERRED = "inferred"  # derived from other claims
    HYPOTHESIZED = "hypothesized"  # proposed, no evidence yet
    DISPUTED = "disputed"  # has supporting AND contradicting evidence
    REJECTED = "rejected"  # contradicting evidence outweighs support


class VerificationState(StrEnum):
    """Independent verification of a relationship."""

    UNVERIFIED = "unverified"
    WEAK = "weak"
    STRONG = "strong"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    REJECTED = "rejected"


class AcquisitionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcquisitionCompleteness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed Job transitions — enforced by model_validator on Job.
JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.SUCCEEDED: set(),  # terminal
    JobStatus.FAILED: set(),  # terminal
    JobStatus.CANCELLED: set(),  # terminal
}


class HorizonClass(StrEnum):
    """Future Intelligence horizon per Master Spec §13."""

    F0 = "F0"  # existing
    F1 = "F1"  # possible but uncommon
    F2 = "F2"  # near-term engineering
    F3 = "F3"  # research-dependent
    F4 = "F4"  # speculative


class EntityType(StrEnum):
    TOOL = "tool"
    TECHNOLOGY = "technology"
    TECHNIQUE = "technique"
    CAPABILITY = "capability"
    PROJECT = "project"
    CONSTRAINT = "constraint"
    PAPER = "paper"
    REPOSITORY = "repository"
    MODEL = "model"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    SCENARIO = "scenario"


class RelationshipDirection(StrEnum):
    DIRECTED = "directed"
    UNDIRECTED = "undirected"
    BIDIRECTIONAL = "bidirectional"


# ── Base record ───────────────────────────────────────────────────────────────


class DomainRecord(BaseModel):
    """Common fields for every domain record.

    - ``id`` is an opaque stable string (UUID4 hex by default).
    - ``created_at`` / ``updated_at`` are aware UTC datetimes.
    - ``version`` starts at 1 and is bumped on every persisted change.
    - ``extra="forbid"`` prevents silently adding unknown fields.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
    )

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    version: int = Field(default=1, ge=1)

    def touch(self) -> None:
        """Mark this record as modified (bump version + updated_at)."""
        # validate_assignment is on, so this triggers validators
        object.__setattr__(self, "updated_at", _utcnow())
        object.__setattr__(self, "version", self.version + 1)
