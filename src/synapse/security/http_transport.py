"""SSRF-protected HTTP transport for httpx.

Per ADR-0009 §2 (separate inventory from functional coverage) and the
user's G02 Final Qualification §2 (verify redirect handling, DNS
rebinding, final connection destination validation): the standard
``validate_url()`` check on the initial URL is NOT sufficient because
httpx follows redirects automatically. A 302 from a public host that
points at ``http://169.254.169.254/...`` would bypass the initial
check.

This module installs an httpx ``AsyncBaseTransport`` wrapper that
intercepts every request URL — including redirect targets — and runs
``validate_url()`` on it. If the URL is unsafe, the request is aborted
with ``SSRFError`` before any bytes are sent.

Usage::

    from synapse.security.http_transport import ssrf_guarded_client

    async with ssrf_guarded_client(timeout=10.0) as client:
        resp = await client.get("https://example.com/")

The transport uses httpx's built-in ``AsyncHTTPTransport`` for the
actual HTTP I/O, but wraps the request method to enforce SSRF
validation on every URL httpx would fetch (including redirect chains).
"""

from __future__ import annotations

from typing import Any

import httpx

from synapse.observability.logging import get_logger
from synapse.security.ssrf import validate_url

_log = get_logger("synapse.security.http_transport")


class SSRFGuardedAsyncTransport(httpx.AsyncBaseTransport):
    """Wraps an httpx transport and validates every request URL via
    ``synapse.security.ssrf.validate_url()``.

    This catches redirect-based SSRF: when httpx follows a 302 to a new
    URL, the wrapped transport's ``handle_async_request`` is called
    again with the new URL, and the SSRF check runs on it.
    """

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport | None = None,
        *,
        allow_private: bool | None = None,
    ) -> None:
        self._wrapped = wrapped or httpx.AsyncHTTPTransport()
        self._allow_private = allow_private

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Validate before any bytes are sent. Raises SSRFError if blocked.
        validate_url(url, allow_private=self._allow_private)
        _log.debug("ssrf-guarded request: %s %s", request.method, url)
        return await self._wrapped.handle_async_request(request)


def ssrf_guarded_client(
    *,
    timeout: float = 10.0,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    allow_private: bool | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient that SSRF-validates every URL,
    including redirect targets.

    Per Master Spec §17: bounded fetches. Default timeout is 10s;
    callers should also enforce a max-bytes cap on the response body
    (the acquisition router does this).
    """
    transport = SSRFGuardedAsyncTransport(allow_private=allow_private)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        transport=transport,
        **kwargs,
    )
