"""API error taxonomy & FastAPI exception handlers.

Maps Synapse exceptions to the standard error envelope (ADR-0006).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from synapse.api.responses import Envelope, ErrorPayload, ProblemDetail
from synapse.observability.logging import get_logger
from synapse.security.auth import AuthError

_log = get_logger("synapse.api.errors")


class DomainError(Exception):
    """Generic domain error — base class."""

    code: str = "domain_error"
    http_status: int = 400
    retryable: bool = False


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404


class ConflictError(DomainError):
    code = "conflict"
    http_status = 409


class ValidationError(DomainError):
    code = "validation_error"
    http_status = 422


class RateLimitError(DomainError):
    code = "rate_limited"
    http_status = 429
    retryable = True


class ServiceUnavailableError(DomainError):
    code = "service_unavailable"
    http_status = 503
    retryable = True


class UnsupportedFeatureError(DomainError):
    """Raised by placeholder handlers for routes whose implementation belongs
    to a future group. Returns 501 per START_HERE §10."""

    code = "not_implemented"
    http_status = 501


def _trace_id() -> str:
    return uuid4().hex


def _build_problem(
    *,
    code: str,
    message: str,
    http_status: int,
    details: Any | None = None,
    retryable: bool = False,
    retry_after: int | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[ProblemDetail, str]:
    """Returns the ProblemDetail and the resolved trace_id."""
    tid = trace_id or _trace_id()
    rid = request_id or tid  # if no request id yet, reuse trace
    problem = ProblemDetail(
        status=http_status,
        code=code,
        message=message,
        details=details,
        retryable=retryable,
        retry_after=retry_after,
        trace_id=tid,
        request_id=rid,
    )
    return problem, tid


def _render(problem: ProblemDetail, request_id: str) -> dict[str, Any]:
    """Render a ProblemDetail as the standard envelope."""
    env = Envelope.failure(
        error=ErrorPayload(
            code=problem.code,
            message=problem.message,
            details=problem.details,
            retryable=problem.retryable,
            retry_after=problem.retry_after,
            trace_id=problem.trace_id,
        ),
        request_id=request_id,
    )
    return jsonable_encoder(env)


async def _request_id(request: Request) -> str:
    rid = request.headers.get("X-Request-ID")
    if not rid:
        rid = uuid4().hex
    return rid


def register_error_handlers(app: FastAPI) -> None:
    """Wire all exception handlers into the FastAPI app."""

    @app.exception_handler(DomainError)
    async def _domain_error_handler(request: Request, exc: DomainError):
        rid = await _request_id(request)
        problem, _ = _build_problem(
            code=exc.code,
            message=str(exc),
            http_status=exc.http_status,
            retryable=exc.retryable,
            request_id=rid,
        )
        _log.warning("domain_error code=%s msg=%s", exc.code, str(exc))
        return JSONResponse(
            status_code=problem.status,
            content=_render(problem, rid),
            media_type="application/problem+json",
        )

    @app.exception_handler(AuthError)
    async def _auth_error_handler(request: Request, exc: AuthError):
        rid = await _request_id(request)
        problem, _ = _build_problem(
            code=exc.code,
            message=exc.message,
            http_status=exc.http_status,
            retryable=exc.retryable,
            request_id=rid,
        )
        _log.warning("auth_error code=%s", exc.code)
        return JSONResponse(
            status_code=problem.status,
            content=_render(problem, rid),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request: Request, exc: RequestValidationError):
        rid = await _request_id(request)
        problem, _ = _build_problem(
            code="validation_error",
            message="Request payload failed validation.",
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=exc.errors(),
            request_id=rid,
        )
        return JSONResponse(
            status_code=problem.status,
            content=_render(problem, rid),
            media_type="application/problem+json",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        rid = await _request_id(request)
        code_map = {
            400: "bad_request",
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            409: "conflict",
            422: "validation_error",
            429: "rate_limited",
            500: "internal_error",
            501: "not_implemented",
            503: "service_unavailable",
        }
        problem, _ = _build_problem(
            code=code_map.get(exc.status_code, "http_error"),
            message=str(exc.detail or "HTTP error"),
            http_status=exc.status_code,
            request_id=rid,
        )
        return JSONResponse(
            status_code=problem.status,
            content=_render(problem, rid),
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception):
        rid = await _request_id(request)
        tid = _trace_id()
        problem, _ = _build_problem(
            code="internal_error",
            message="An unexpected error occurred.",
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            retryable=False,
            request_id=rid,
            trace_id=tid,
        )
        _log.exception("unhandled_error trace_id=%s", tid)
        return JSONResponse(
            status_code=problem.status,
            content=_render(problem, rid),
            media_type="application/problem+json",
        )
