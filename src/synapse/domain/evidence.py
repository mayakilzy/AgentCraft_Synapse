"""EvidenceFragment — an inspectable piece of evidence extracted from a Source.

Every verified claim and explicit relationship MUST have at least one
EvidenceFragment (invariant §1 in DOMAIN_AND_API_CONTRACTS.md). The
fragment preserves the original source linkage — losing it is forbidden.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord


class Locator(DomainRecord):
    """Where inside a document the fragment came from."""

    section: str | None = None
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    span: tuple[int, int] | None = None  # (start, end) char offsets
    xpath: str | None = None  # optional structured selector

    @model_validator(mode="after")
    def _span_valid(self) -> Locator:
        if self.span is not None and self.span[0] > self.span[1]:
            raise ValueError("Locator.span must satisfy start <= end")
        return self


class EvidenceFragment(DomainRecord):
    acquisition_id: str = Field(..., min_length=1)
    document_version: str | None = None
    locator: Locator | None = None
    exact_excerpt: str | None = Field(default=None, min_length=1)
    excerpt_hash: str | None = Field(default=None, min_length=8)
    extraction_method: str = Field(..., min_length=1)
    timestamp: str | None = None  # ISO8601 — when extraction happened
    source_id: str | None = None  # denormalized for query convenience
    source_uri: str | None = None  # never lost — even if Source record is gone

    @model_validator(mode="after")
    def _must_have_excerpt_or_hash(self) -> EvidenceFragment:
        """Either the exact text or its hash must be present."""
        if not self.exact_excerpt and not self.excerpt_hash:
            raise ValueError("EvidenceFragment must have exact_excerpt or excerpt_hash")
        if not self.source_id and not self.source_uri:
            raise ValueError(
                "EvidenceFragment must preserve source linkage (source_id or source_uri required)"
            )
        return self
