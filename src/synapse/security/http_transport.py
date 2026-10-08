"""SSRF-protected HTTP transport for httpx — with IP pinning.

Per the user's G02 Final Security & DB Closure §1: implement the smallest
robust mitigation against DNS rebinding (TOCTOU between URL validation
and TCP connection).

Approach: **validated-IP connection pinning**.

1. ``validate_url_with_pin()`` resolves the hostname via ``socket.getaddrinfo``,
   checks every returned IP against the SSRF denylist, and returns the
   first safe IP (the "pinned" IP).
2. ``SSRFGuardedAsyncTransport`` rewrites the request URL to use the
   pinned IP as the host. When httpcore sees an IP literal as the host,
   it connects directly — **no second DNS lookup**. This closes the
   TOCTOU window: even if DNS changes between validation and connection,
   the TCP connection goes to the originally-validated safe IP.
3. For HTTPS, the transport sets ``extensions["sni_hostname"]`` to the
   original hostname so TLS SNI and certificate verification use the
   original hostname (not the IP). The ``Host`` header is also set to
   the original hostname.
4. Redirect targets are re-validated and re-pinned on each redirect
   (httpx re-invokes the transport for each redirect URL).

This is NOT a custom networking stack — it uses httpx's built-in
``AsyncHTTPTransport`` for the actual HTTP/TLS I/O. Only the URL is
rewritten to pin the IP.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from synapse.observability.logging import get_logger
from synapse.security.ssrf import SSRFError, _is_ipv4_mapped_ipv6, _is_private_ip

_log = get_logger("synapse.security.http_transport")

# Re-export the non-pinning transport for backward compatibility.
from synapse.security.ssrf import validate_url  # noqa: F401, E402


def validate_url_with_pin(
    url: str,
    *,
    allow_private: bool | None = None,
) -> tuple[str, str | None]:
    """Validate a URL and return (validated_url, pinned_ip).

    The pinned_ip is the first safe resolved IP for the URL's hostname.
    Callers should use the pinned_ip as the connection target to prevent
    DNS rebinding (TOCTOU between validation and connection).

    Returns:
        (validated_url, pinned_ip) — pinned_ip is None if the host is
        already an IP literal (no DNS resolution needed) or if DNS
        resolution returned no results (which would have raised SSRFError).

    Raises:
        SSRFError: if the URL is unsafe (bad scheme, blocked hostname,
        private/loopback/metadata IP, etc.).
    """
    from synapse.config import get_settings

    settings = get_settings()
    if allow_private is None:
        allow_private = settings.ssrf_allow_private

    if not url:
        raise SSRFError("URL is empty")

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SSRFError(f"Scheme {scheme!r} not allowed (only http/https)")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no hostname")

    # Check if the host is already an IP literal — no DNS resolution needed.
    try:
        ip = ipaddress.ip_address(host)
        # It's an IP literal — validate it directly
        if _is_ipv4_mapped_ipv6(ip) and not allow_private:
            raise SSRFError(f"IPv4-mapped IPv6 address {ip} blocked (potential SSRF bypass)")
        if not allow_private and _is_private_ip(ip):
            raise SSRFError(
                f"IP {ip} is in a forbidden range (private/loopback/link-local/metadata)"
            )
        # Already an IP — no pinning needed (httpcore connects directly)
        return url, None
    except ValueError:
        pass  # Not an IP literal — proceed with DNS resolution

    # Check blocked hostnames
    from synapse.security.ssrf import _BLOCKED_HOSTNAMES

    if host.lower() in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Hostname {host!r} is blocked")

    # Resolve DNS and validate every returned IP
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve hostname {host!r}: {exc}") from exc

    if not infos:
        raise SSRFError(f"No addresses returned for hostname {host!r}")

    pinned_ip: str | None = None
    for _, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        # Strip IPv6 scope-id if present (e.g. "fe80::1%eth0")
        ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SSRFError(f"Unparseable IP {ip_str!r}: {exc}") from exc

        if _is_ipv4_mapped_ipv6(ip) and not allow_private:
            raise SSRFError(f"IPv4-mapped IPv6 address {ip} blocked (potential SSRF bypass)")

        if not allow_private and _is_private_ip(ip):
            raise SSRFError(
                f"Resolved IP {ip} for {host!r} is in a forbidden range "
                "(private/loopback/link-local/metadata)"
            )

        # Pin to the first safe IP
        if pinned_ip is None:
            pinned_ip = ip_str

    return url, pinned_ip


def _format_ip_for_url(ip_str: str) -> str:
    """Format an IP string for use as a URL host.

    IPv6 addresses need brackets in URLs (e.g., [::1]).
    IPv4 addresses are used as-is.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        if isinstance(ip, ipaddress.IPv6Address):
            return f"[{ip_str}]"
        return ip_str
    except ValueError:
        # Not an IP — return as-is (shouldn't happen)
        return ip_str


class SSRFGuardedAsyncTransport(httpx.AsyncBaseTransport):
    """Wraps an httpx transport with SSRF validation + IP pinning.

    For every request URL (including redirect targets):
    1. Validates the URL via ``validate_url_with_pin()``
    2. Pins the connection to the validated safe IP
    3. Rewrites the URL to use the IP (httpcore skips DNS for IP literals)
    4. Sets TLS SNI + Host header to the original hostname

    This closes the DNS-rebinding TOCTOU window: even if DNS changes
    between validation and connection, the TCP connection goes to
    the originally-validated safe IP.
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
        # Validate and pin — raises SSRFError if blocked.
        validated_url, pinned_ip = validate_url_with_pin(url, allow_private=self._allow_private)

        if pinned_ip is None:
            # Host is already an IP literal or pinning not needed.
            # Just validate (already done above) and delegate.
            _log.debug("ssrf-guarded request (no pin needed): %s %s", request.method, url)
            return await self._wrapped.handle_async_request(request)

        # Rewrite the URL to use the pinned IP.
        # httpcore skips DNS resolution when the host is an IP literal,
        # so the TCP connection goes to pinned_ip — not to whatever
        # DNS might return at connect time.
        parsed = urlparse(validated_url)
        ip_host = _format_ip_for_url(pinned_ip)
        port = parsed.port
        # Reconstruct the URL string with the pinned IP as the host.
        # Preserve scheme, port, path, query, fragment.
        netloc = f"{ip_host}:{port}" if port else ip_host
        pinned_url_str = (
            f"{parsed.scheme}://{netloc}{parsed.path or '/'}?{parsed.query}"
            if parsed.query
            else f"{parsed.scheme}://{netloc}{parsed.path or '/'}"
        )
        pinned_url = httpx.URL(pinned_url_str)

        # Build new request with pinned URL + SNI override + Host header.
        # The extensions["sni_hostname"] tells httpcore to use the
        # original hostname for TLS SNI and certificate verification.
        new_headers = request.headers.copy()
        new_headers["host"] = parsed.hostname  # original hostname, not IP

        # Handle request content safely: for streaming requests (e.g.,
        # client.stream("GET", url)), request.content raises RequestNotRead.
        # For GET requests there is no body, so content=None is fine.
        try:
            content = request.content
        except httpx.RequestNotRead:
            content = None

        pinned_request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=new_headers,
            content=content,
            # Merge existing extensions (e.g., timeout) with our SNI override.
            extensions={**request.extensions, "sni_hostname": parsed.hostname},
        )

        _log.debug(
            "ssrf-guarded request (pinned): %s %s → IP %s (SNI: %s)",
            request.method,
            parsed.hostname,
            pinned_ip,
            parsed.hostname,
        )
        return await self._wrapped.handle_async_request(pinned_request)


def ssrf_guarded_client(
    *,
    timeout: float = 10.0,
    follow_redirects: bool = True,
    max_redirects: int = 5,
    allow_private: bool | None = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient that SSRF-validates + IP-pins every URL.

    Per Master Spec §17: bounded fetches. Default timeout is 10s.
    """
    transport = SSRFGuardedAsyncTransport(allow_private=allow_private)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        transport=transport,
        **kwargs,
    )
