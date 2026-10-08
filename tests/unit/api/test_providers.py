"""Providers endpoint tests."""

from __future__ import annotations


def test_providers_require_auth(client):
    r = client.get("/api/v1/providers")
    assert r.status_code == 401


def test_providers_with_reader_key_ok(client, auth_headers_reader):
    r = client.get("/api/v1/providers", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] >= 1
    assert "enabled_count" in body["data"]


def test_providers_includes_http_fetch(client, auth_headers_reader):
    r = client.get("/api/v1/providers", headers=auth_headers_reader)
    items = r.json()["data"]["items"]
    names = [p["name"] for p in items]
    assert "http-fetch" in names
    assert "openai" in names


def test_providers_default_disabled(client, auth_headers_reader):
    """Per ADR-0001: providers are disabled by default in G01 — explicit
    enable is required."""
    r = client.get("/api/v1/providers", headers=auth_headers_reader)
    items = r.json()["data"]["items"]
    openai = next(p for p in items if p["name"] == "openai")
    assert openai["enabled"] is False
