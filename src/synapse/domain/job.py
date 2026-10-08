"""Job — a long-running async operation.

Per `DOMAIN_AND_API_CONTRACTS.md`: ``Job`` carries id, kind, requester,
status (queued|running|succeeded|failed|cancelled), progress, input_ref,
output_ref, created_at, updated_at, idempotency_key.

Job status transitions are validated against ``JOB_TRANSITIONS`` so that
terminal jobs cannot be revived and queued jobs cannot skip the running
state.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import (
    JOB_TRANSITIONS,
    DomainRecord,
    JobStatus,
    StrEnum,
)


class JobKind(StrEnum):
    DISCOVER = "discover"
    INGEST = "ingest"
    EXTRACT = "extract"
    VERIFY = "verify"
    SEARCH = "search"
    REASON = "reason"
    INNOVATE = "innovate"
    CRITIQUE = "critique"
    EXPERIMENT = "experiment"
    SCENARIO = "scenario"
    CUSTOM = "custom"


class Job(DomainRecord):
    kind: JobKind = JobKind.CUSTOM
    requester: str | None = None
    status: JobStatus = JobStatus.QUEUED
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    input_ref: str | None = None
    output_ref: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1)
    error_code: str | None = None
    error_message: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    previous_status: JobStatus | None = None

    @model_validator(mode="after")
    def _progress_consistent_with_status(self) -> Job:
        """A succeeded job must have progress=1.0."""
        if self.status == JobStatus.SUCCEEDED and self.progress != 1.0:
            raise ValueError("Job with status=succeeded must have progress=1.0")
        if self.status == JobStatus.QUEUED and self.progress != 0.0:
            raise ValueError("Job with status=queued must have progress=0.0")
        return self

    @model_validator(mode="after")
    def _failed_or_cancelled_may_have_error(self) -> Job:
        """Failed jobs should carry an error code."""
        if self.status == JobStatus.FAILED and not self.error_code:
            raise ValueError("Job with status=failed must have error_code")
        return self

    def transition_to(self, new_status: JobStatus) -> Job:
        """Validate a status transition; mutate immutably (return a copy)."""
        current = self.status
        allowed = JOB_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(f"Forbidden job transition {current} → {new_status}")
        new = self.model_copy(
            update={
                "status": new_status,
                "previous_status": current,
                "updated_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            }
        )
        return new
