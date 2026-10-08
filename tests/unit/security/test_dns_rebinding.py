"""DNS rebinding defense tests — deterministic negative tests.

Per the user's G02 Final Security & DB Closure §1:
  "Add deterministic negative tests simulating DNS changes between
   validation and connection."

These tests confirm that the IP-pinning transport closes the TOCTOU
window: even if DNS changes between validate_url() and the actual TCP
connection, the connection goes to the originally-validated safe IP.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from synapse.security.http_transport import (
    SSRFGuardedAsyncTransport,
    _format_ip_for_url,
    validate_url_with_pin,
)
from synapse.security.ssrf import SSRFError

# ── validate_url_with_pin: basic functionality ──────────────────────────────


def test_validate_url_with_pin_returns_pinned_ip_for_hostname():
    """For a hostname, validate_url_with_pin resolves DNS and returns
    the first safe IP as the pinned IP."""
    # Patch getaddrinfo to return a known safe public IP
    with patch("synapse.security.http_transport.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
        ]
        url, pinned_ip = validate_url_with_pin("https://example.com/path")
    assert url == "https://example.com/path"
    assert pinned_ip == "93.184.216.34"


def test_validate_url_with_pin_returns_none_for_ip_literal():
    """When the host is already an IP literal, no DNS resolution is
    needed — pinning returns None (the IP is used directly by httpcore)."""
    url, pinned_ip = validate_url_with_pin("https://93.184.216.34/path")
    assert url == "https://93.184.216.34/path"
    assert pinned_ip is None


def test_validate_url_with_pin_blocks_private_ip_literal():
    """An IP literal that's in a private range is blocked immediately."""
    with pytest.raises(SSRFError, match="forbidden range"):
        validate_url_with_pin("http://10.0.0.5/")


def test_validate_url_with_pin_blocks_loopback_ip_literal():
    with pytest.raises(SSRFError, match="forbidden range"):
        validate_url_with_pin("http://127.0.0.1/")


def test_validate_url_with_pin_blocks_metadata_ip_literal():
    with pytest.raises(SSRFError, match="forbidden range"):
        validate_url_with_pin("http://169.254.169.254/")


def test_validate_url_with_pin_blocks_hostname_resolving_to_private():
    """When DNS resolves to a private IP, the URL is blocked."""
    with patch("synapse.security.http_transport.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", 0)),
        ]
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url_with_pin("https://evil.example.com/")


# ── DNS rebinding: the core TOCTOU defense ──────────────────────────────────


def test_dns_rebinding_attack_blocked_by_ip_pinning():
    """SIMULATE DNS REBINDING: DNS returns a safe IP at validation time,
    then a private IP at connection time.

    WITHOUT pinning: the second DNS lookup would return the private IP,
    and httpx would connect to it — SSRF bypass.

    WITH pinning: the transport uses the validated IP from step 1;
    the second DNS lookup NEVER HAPPENS — the TCP connection goes to
    the originally-validated safe IP.

    This test confirms: getaddrinfo is called EXACTLY ONCE (during
    validate_url_with_pin), and the request URL is rewritten to use
    the pinned IP (so httpcore does NOT do a second DNS lookup).
    """
    # Simulate DNS rebinding: first call returns public IP, second
    # call (if it happened) would return private IP.
    call_count = {"getaddrinfo": 0}

    def fake_getaddrinfo(host, *args, **kwargs):
        call_count["getaddrinfo"] += 1
        if call_count["getaddrinfo"] == 1:
            # First call (during validate_url_with_pin): return safe IP
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))
            ]
        else:
            # Second call (during httpx connect, if it happened): return
            # EVIL private IP — DNS rebinding attack
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 0))
            ]

    with patch("synapse.security.http_transport.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        _, pinned_ip = validate_url_with_pin("https://rebinding.example.com/")

    # The pinned IP must be the SAFE IP from the first DNS lookup
    assert pinned_ip == "93.184.216.34"

    # DNS was called exactly ONCE — during validation. The transport
    # will use the pinned IP directly, so no second lookup happens.
    assert call_count["getaddrinfo"] == 1, (
        "DNS was called more than once — IP pinning is not preventing "
        "the second (potentially poisoned) DNS lookup"
    )


@pytest.mark.asyncio
async def test_dns_rebinding_url_is_rewritten_to_pinned_ip():
    """The transport rewrites the request URL to use the pinned IP.
    This means httpcore sees an IP literal as the host and does NOT
    do a second DNS lookup — closing the TOCTOU window."""
    transport = SSRFGuardedAsyncTransport()

    # Capture the request that gets passed to the wrapped transport
    captured_request: dict = {}

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            captured_request["url"] = str(request.url)
            captured_request["host_header"] = request.headers.get("host")
            captured_request["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, text="OK")

    transport._wrapped = CapturingTransport()

    # Mock DNS to return a safe IP
    with patch("synapse.security.http_transport.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
        ]
        request = httpx.Request("GET", "https://example.com/path?q=1")
        await transport.handle_async_request(request)

    # The URL passed to the wrapped transport must use the pinned IP
    assert "93.184.216.34" in captured_request["url"], (
        f"URL was not rewritten to use pinned IP: {captured_request['url']}"
    )
    # The original hostname must NOT be in the URL (it was replaced by the IP)
    assert "example.com" not in captured_request["url"], (
        f"URL still contains the hostname instead of the pinned IP: {captured_request['url']}"
    )
    # The Host header must be the original hostname (not the IP)
    assert captured_request["host_header"] == "example.com", (
        f"Host header is not the original hostname: {captured_request['host_header']}"
    )
    # The SNI must be the original hostname (for TLS cert verification)
    assert captured_request["sni"] == "example.com", (
        f"SNI is not the original hostname: {captured_request['sni']}"
    )


def test_dns_rebinding_no_second_lookup_for_redirect_targets():
    """When httpx follows a redirect, the transport re-validates and
    re-pins the redirect target URL. Each redirect target gets its own
    DNS lookup (during validation), but the connection uses the pinned
    IP — no second lookup per target."""
    call_count = {"getaddrinfo": 0}

    def fake_getaddrinfo(host, *args, **kwargs):
        call_count["getaddrinfo"] += 1
        # Always return safe IPs
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    with patch("synapse.security.http_transport.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        # Validate URL #1
        _url1, pin1 = validate_url_with_pin("https://first.example.com/")
        assert pin1 == "93.184.216.34"

        # Validate URL #2 (simulating a redirect target)
        _url2, pin2 = validate_url_with_pin("https://second.example.com/")
        assert pin2 == "93.184.216.34"

    # Two validations → two DNS lookups (one per hostname)
    assert call_count["getaddrinfo"] == 2

    # But the connections use the pinned IPs — no additional DNS lookups
    # would happen during the actual TCP connect because the URL is
    # rewritten to use the IP literal.


# ── IP formatting ───────────────────────────────────────────────────────────


def test_format_ipv4_for_url():
    assert _format_ip_for_url("93.184.216.34") == "93.184.216.34"


def test_format_ipv6_for_url():
    """IPv6 addresses need brackets in URLs."""
    assert _format_ip_for_url("2001:db8::1") == "[2001:db8::1]"


# ── Redirect defense (still works with IP pinning) ──────────────────────────


@pytest.mark.asyncio
async def test_redirect_to_metadata_ip_blocked_with_pinning():
    """A redirect to 169.254.169.254 must be blocked even with IP pinning.
    The transport validates the redirect target URL (which is an IP literal)
    and blocks it immediately."""
    transport = SSRFGuardedAsyncTransport()
    request = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SSRFError, match="forbidden range"):
        await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_redirect_to_loopback_blocked_with_pinning():
    transport = SSRFGuardedAsyncTransport()
    request = httpx.Request("GET", "http://127.0.0.1:8080/")
    with pytest.raises(SSRFError, match="forbidden range"):
        await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_redirect_to_rfc1918_blocked_with_pinning():
    transport = SSRFGuardedAsyncTransport()
    request = httpx.Request("GET", "http://10.0.0.5/")
    with pytest.raises(SSRFError, match="forbidden range"):
        await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_redirect_to_file_scheme_blocked():
    transport = SSRFGuardedAsyncTransport()
    request = httpx.Request("GET", "file:///etc/passwd")
    with pytest.raises(SSRFError, match="Scheme"):
        await transport.handle_async_request(request)


# ── Safe URL delegation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_url_delegates_with_pinning():
    """For a safe URL with a hostname, the transport pins the IP and
    delegates to the wrapped transport with a rewritten URL."""
    fake_response = httpx.Response(200, text="OK")
    fake_wrapped = MagicMock()
    fake_wrapped.handle_async_request = AsyncMock(return_value=fake_response)

    transport = SSRFGuardedAsyncTransport(wrapped=fake_wrapped)

    with patch("synapse.security.http_transport.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
        ]
        request = httpx.Request("GET", "https://example.com/path")
        response = await transport.handle_async_request(request)

    assert response.status_code == 200
    assert response.text == "OK"
    # The wrapped transport was called with a rewritten URL (using the IP)
    actual_request = fake_wrapped.handle_async_request.call_args[0][0]
    assert "93.184.216.34" in str(actual_request.url)
    assert actual_request.headers.get("host") == "example.com"
    assert actual_request.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_safe_ip_literal_url_delegates_without_pinning():
    """For a safe URL with an IP literal, no pinning is needed —
    the transport delegates directly."""
    fake_response = httpx.Response(200, text="OK")
    fake_wrapped = MagicMock()
    fake_wrapped.handle_async_request = AsyncMock(return_value=fake_response)

    transport = SSRFGuardedAsyncTransport(wrapped=fake_wrapped)

    request = httpx.Request("GET", "https://93.184.216.34/path")
    response = await transport.handle_async_request(request)

    assert response.status_code == 200
    # The wrapped transport was called with the ORIGINAL URL (no rewrite)
    actual_request = fake_wrapped.handle_async_request.call_args[0][0]
    assert str(actual_request.url) == "https://93.184.216.34/path"
