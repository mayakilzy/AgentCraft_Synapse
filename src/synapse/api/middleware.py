"""HTTP middleware — request-id propagation, structured logging, CORS, simple
rate limiter.

The middleware stack is wired in ``synapse.main.create_app``.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from synapse.config import get_settings
from synapse.observability.logging import get_logger

_log = get_logger("synapse.api.middleware")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Add ``X-Request-ID`` to every response. Read from request if present."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        settings = get_settings()
        request_id = request.headers.get(settings.request_id_header)
        if not request_id:
            from uuid import uuid4

            request_id = uuid4().hex
        # Stash for downstream code (error handlers etc.)
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0
        response.headers[settings.request_id_header] = request_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        _log.info(
            "%s %s %d %.2fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    """In-process per-IP token bucket. NOT for production multi-process use.

    G01 ships a simple limiter so the 429 contract is testable. A real
    limiter (Redis-backed) is a future group task.
    """

    def __init__(self, app, per_minute: int, burst: int):
        super().__init__(app)
        self.per_minute = per_minute
        self.burst = burst
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        # Skip health checks so the limiter doesn't block monitoring.
        if request.url.path.startswith("/health"):
            return await call_next(request)
        client = request.client.host if request.client else "anonymous"
        now = time.monotonic()
        window = 60.0  # seconds
        bucket = self._buckets[client]
        # Evict entries outside the window
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= self.burst:
            return StarletteResponse(
                status_code=429,
                content=(
                    '{"data":null,"meta":{"request_id":"rate-limited"},'
                    '"error":{"code":"rate_limited","message":"Too many requests",'
                    '"retryable":true,"retry_after":1}}'
                ),
                media_type="application/problem+json",
                headers={"Retry-After": "1"},
            )
        bucket.append(now)
        return await call_next(request)


def add_cors(app: FastAPI) -> None:
    """Wire CORSMiddleware from settings. Refuses wildcard + credentials."""
    settings = get_settings()
    origins = settings.cors_origin_list
    if "*" in origins and settings.cors_allow_credentials:
        raise RuntimeError(
            "CORS configuration is unsafe: '*' origins + credentials "
            "is forbidden. Fix SYNAPSE_CORS_ORIGINS."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )


def install_middleware(app: FastAPI) -> None:
    """Add all middleware in the correct order."""
    settings = get_settings()
    # Order matters: outermost middleware is added LAST in FastAPI.
    # We want:  request-id → rate-limit → CORS → routing
    add_cors(app)
    app.add_middleware(
        SimpleRateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
    )
    app.add_middleware(RequestIdMiddleware)
