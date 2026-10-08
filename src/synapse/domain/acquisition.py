"""Acquisition — a single fetch event against a Source.

An Acquisition records *one* attempt to acquire the content of a Source via
a specific provider (HTTP, RSS, browser, etc.). Multiple Acquisitions may
exist for the same Source (re-fetches, partial recovery, different
providers).
"""

from __future__ import annotations

from pydantic import Field, model_validator

from synapse.domain._base import (
    AcquisitionCompleteness,
    AcquisitionStatus,
    DomainRecord,
)


class Acquisition(DomainRecord):
    source_id: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    provider_version: str | None = None
    acquired_at: str | None = None
    content_hash: str | None = Field(default=None, min_length=8)
    content_type: str | None = None
    artifact_ref: str | None = None  # path or URI to immutable artifact
    completeness: AcquisitionCompleteness = AcquisitionCompleteness.PARTIAL
    status: AcquisitionStatus = AcquisitionStatus.QUEUED
    error_code: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    cost_estimate: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _completeness_consistency(self) -> Acquisition:
        """Per invariant §3: partial fetch is never represented as full.
        And: a fully-failed acquisition cannot be marked as full."""
        if self.completeness == AcquisitionCompleteness.FULL:
            if self.error_code:
                raise ValueError("Acquisition with completeness=full cannot have error_code")
            if self.status != AcquisitionStatus.SUCCEEDED:
                raise ValueError("Acquisition with completeness=full must have status=succeeded")
            if not self.content_hash:
                raise ValueError("Acquisition with completeness=full must have content_hash")
        if self.completeness == AcquisitionCompleteness.FAILED and (
            self.status not in {AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED}
        ):
            raise ValueError(
                "Acquisition with completeness=failed must have status in {failed, cancelled}"
            )
        return self
