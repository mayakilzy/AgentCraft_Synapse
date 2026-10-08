"""SSRF protection — validate URLs before any HTTP fetch.

Per ADR-0004 and Master Spec §17: protects against SSRF, unsafe URLs,
metadata-endpoint leaks.

This module is unit-tested in G01. It is **not yet called** by any fetcher
(G02 Acquisition will wire it in).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from pydantic import HttpUrl

from synapse.config import get_settings


class SSRFError(ValueError):
    """Raised when a URL fails the SSRF guard."""

    code = "ssrf_blocked"


# Schemes we allow fetching.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that always resolve to metadata endpoints.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.aws",
    }
)


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if the IP is loopback / private / link-local / reserved."""
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_ipv4_mapped_ipv6(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Detect IPv4-mapped IPv6 addresses (::ffff:a.b.c.d) — used to bypass
    IPv4-only checks. Only IPv6 addresses can be IPv4-mapped."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return False
    return ip.ipv4_mapped is not None


def validate_url(
    url: str | HttpUrl,
    *,
    allow_private: bool | None = None,
) -> str:
    """Validate a URL for safe fetching.

    Returns the canonical URL string if safe, raises ``SSRFError`` otherwise.

    Args:
        url: URL to validate.
        allow_private: Override the ``SYNAPSE_SSRF_ALLOW_PRIVATE`` setting.
            Default ``None`` uses the setting (which defaults to ``False``).

    Raises:
        SSRFError: scheme not allowed, hostname blocked, or resolved IP
        falls in a forbidden range.
    """
    settings = get_settings()
    if allow_private is None:
        allow_private = settings.ssrf_allow_private

    # Pydantic HttpUrl already validated the URL; for plain strings we strip.
    url_str = str(url) if isinstance(url, HttpUrl) else str(url).strip()

    if not url_str:
        raise SSRFError("URL is empty")

    parsed = urlparse(url_str)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"Scheme {scheme!r} not allowed (only http/https)")

    host = parsed.hostname
    if not host:
        raise SSRFError("URL has no hostname")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise SSRFError(f"Hostname {host!r} is blocked")

    # Resolve the host (DNS lookup) and check every returned IP.
    # This catches DNS-rebinding where a public hostname points at a private IP.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve hostname {host!r}: {exc}") from exc

    if not infos:
        raise SSRFError(f"No addresses returned for hostname {host!r}")

    for _, _, _, _, sockaddr in infos:
        ip_str = sockaddr[0]
        # Strip IPv6 scope-id if present (e.g. "fe80::1%eth0")
        ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise SSRFError(f"Unparseable IP {ip_str!r}: {exc}") from exc

        if _is_ipv4_mapped_ipv6(ip):
            # An attacker can bypass IPv4-only checks by using an
            # IPv4-mapped IPv6 address. Reject unless explicitly allowed.
            if not allow_private:
                raise SSRFError(f"IPv4-mapped IPv6 address {ip} blocked (potential SSRF bypass)")

        if not allow_private and _is_private_ip(ip):
            raise SSRFError(
                f"Resolved IP {ip} for {host!r} is in a forbidden range "
                "(private/loopback/link-local/metadata). "
                "Set SYNAPSE_SSRF_ALLOW_PRIVATE=true only in trusted sandboxes."
            )

    return url_str


def assert_safe_url(url: str | HttpUrl, *, allow_private: bool | None = None) -> None:
    """Like ``validate_url`` but raises nothing on success (side-effect only)."""
    validate_url(url, allow_private=allow_private)
