"""Source — a discoverable information origin.

A Source is the *canonical* record of where information came from. It is
immutable per version: when the same canonical URL changes content, we
create a new Source version rather than mutating the existing one
(per `DOMAIN_AND_API_CONTRACTS.md` invariant §4).
"""

from __future__ import annotations

from pydantic import HttpUrl, field_validator

from synapse.domain._base import DomainRecord, StrEnum


class SourceType(StrEnum):
    WEB = "web"
    REPOSITORY = "repository"
    PAPER = "paper"
    DATASET = "dataset"
    DOCUMENTATION = "documentation"
    BLOG = "blog"
    STANDARD = "standard"
    RSS = "rss"
    SOCIAL = "social"
    OTHER = "other"


class SourceStatus(StrEnum):
    DISCOVERED = "discovered"
    SCHEDULED = "scheduled"
    ACQUIRED = "acquired"
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    IGNORED = "ignored"


class Source(DomainRecord):
    canonical_uri: HttpUrl
    source_type: SourceType = SourceType.OTHER
    publisher: str | None = None
    author: str | None = None
    license: str | None = None
    usage_policy: str | None = None
    discovered_at: str | None = None  # ISO8601 string for portability
    status: SourceStatus = SourceStatus.DISCOVERED

    @field_validator("canonical_uri", mode="after")
    @classmethod
    def _strip_query_for_canonical(cls, v: HttpUrl) -> HttpUrl:
        """Canonical URIs are normalized — strip nothing yet, but ensure
        the URL has a scheme/host."""
        if not v.scheme or not v.host:
            raise ValueError("canonical_uri must have scheme and host")
        return v
