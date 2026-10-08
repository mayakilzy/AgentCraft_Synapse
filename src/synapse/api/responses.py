"""Standard response envelope & problem+json errors.

Per ADR-0006 and `DOMAIN_AND_API_CONTRACTS.md`:

    {
      "data": ... | null,
      "meta": { "request_id": "...", "api_version": "v1", "pagination": ... },
      "error": null | { "code": "...", "message": "...", ... }
    }
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationMeta(BaseModel):
    next_cursor: str | None = None
    prev_cursor: str | None = None
    limit: int = 50
    total: int | None = None


class ResponseMeta(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    api_version: str = "v1"
    job_id: str | None = None
    pagination: PaginationMeta | None = None


class ErrorPayload(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | dict[str, Any] | None = None
    retryable: bool = False
    retry_after: int | None = None  # seconds
    trace_id: str | None = None


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    meta: ResponseMeta = Field(default_factory=ResponseMeta)
    error: ErrorPayload | None = None

    @classmethod
    def success(
        cls,
        data: Any = None,
        *,
        request_id: str | None = None,
        pagination: PaginationMeta | None = None,
    ) -> Envelope[Any]:
        meta = ResponseMeta(
            request_id=request_id or uuid4().hex,
            pagination=pagination,
        )
        return cls(data=data, meta=meta, error=None)

    @classmethod
    def failure(
        cls,
        error: ErrorPayload,
        *,
        request_id: str | None = None,
    ) -> Envelope[Any]:
        meta = ResponseMeta(request_id=request_id or uuid4().hex)
        return cls(data=None, meta=meta, error=error)


class ProblemDetail(BaseModel):
    """RFC 9457 problem detail — serialized as the body of error responses.

    Same fields as ``ErrorPayload`` plus the envelope metadata, so callers
    parsing the body always see ``error.code`` etc.
    """

    type: str = "about:blank"
    title: str | None = None
    status: int
    code: str
    message: str
    details: Any | None = None
    retryable: bool = False
    retry_after: int | None = None
    trace_id: str | None = None
    request_id: str | None = None
