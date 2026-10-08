"""SSRF guard tests — scheme allowlist + private-IP denylist."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from synapse.security.ssrf import SSRFError, validate_url


def _patch_resolve(host_to_ips: dict[str, list[str]]):
    """Patch socket.getaddrinfo to return canned IPs for hosts."""

    def fake_getaddrinfo(host, *args, **kwargs):
        ips = host_to_ips.get(host)
        if ips is None:
            raise socket.gaierror("mocked: unknown host")
        return [
            (
                socket.AF_INET if ":" not in ip else socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (ip, 0),
            )
            for ip in ips
        ]

    return patch("synapse.security.ssrf.socket.getaddrinfo", side_effect=fake_getaddrinfo)


def test_https_url_ok():
    with _patch_resolve({"example.com": ["93.184.216.34"]}):
        result = validate_url("https://example.com/page")
    assert result.startswith("https://")


def test_http_url_ok():
    with _patch_resolve({"example.com": ["93.184.216.34"]}):
        validate_url("http://example.com")


def test_file_scheme_rejected():
    with pytest.raises(SSRFError, match="Scheme"):
        validate_url("file:///etc/passwd")


def test_ftp_scheme_rejected():
    with pytest.raises(SSRFError, match="Scheme"):
        validate_url("ftp://example.com/x")


def test_data_uri_rejected():
    with pytest.raises(SSRFError, match="Scheme"):
        validate_url("data:text/plain;base64,aGVsbG8=")


def test_loopback_ipv4_rejected():
    with _patch_resolve({"localhost": ["127.0.0.1"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://localhost/x")


def test_loopback_ipv6_rejected():
    with _patch_resolve({"ipv6-loopback": ["::1"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://ipv6-loopback/x")


def test_rfc1918_10_rejected():
    with _patch_resolve({"internal.test": ["10.0.0.5"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://internal.test/")


def test_rfc1918_172_rejected():
    with _patch_resolve({"internal.test": ["172.16.0.1"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://internal.test/")


def test_rfc1918_192_rejected():
    with _patch_resolve({"internal.test": ["192.168.1.1"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://internal.test/")


def test_cloud_metadata_endpoint_rejected():
    """The 169.254.169.254 metadata endpoint is in 169.254/16 (link-local)."""
    with _patch_resolve({"169.254.169.254": ["169.254.169.254"]}):
        with pytest.raises(SSRFError, match="forbidden range"):
            validate_url("http://169.254.169.254/latest/meta-data/")


def test_metadata_hostname_rejected():
    """AWS / GCP metadata hostnames are blocked by name."""
    with pytest.raises(SSRFError, match="blocked"):
        validate_url("http://metadata.google.internal/")


def test_ipv4_mapped_ipv6_bypass_rejected():
    """Attacker tries ::ffff:127.0.0.1 to bypass IPv4-only checks."""
    with _patch_resolve({"bypass.test": ["::ffff:127.0.0.1"]}), pytest.raises(SSRFError):
        validate_url("http://bypass.test/")


def test_unresolvable_hostname_rejected():
    with pytest.raises(SSRFError, match="Could not resolve"):
        validate_url("https://this-host-does-not-exist.invalid/")


def test_allow_private_true_lets_loopback_through():
    with _patch_resolve({"localhost": ["127.0.0.1"]}):
        result = validate_url("http://localhost/x", allow_private=True)
    assert result.startswith("http://")


def test_empty_url_rejected():
    with pytest.raises(SSRFError, match="empty"):
        validate_url("")
