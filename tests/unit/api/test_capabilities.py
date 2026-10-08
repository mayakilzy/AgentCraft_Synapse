"""Capabilities endpoint tests — auth + envelope shape."""

from __future__ import annotations


def test_capabilities_require_auth(client):
    r = client.get("/api/v1/capabilities")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "unauthenticated"


def test_capabilities_with_reader_key_ok(client, auth_headers_reader):
    r = client.get("/api/v1/capabilities", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["count"] >= 1
    assert body["meta"]["api_version"] == "v1"


def test_capabilities_envelope_shape(client, auth_headers_reader):
    r = client.get("/api/v1/capabilities", headers=auth_headers_reader)
    body = r.json()
    assert set(body.keys()) == {"data", "meta", "error"}
    assert body["error"] is None
    assert "items" in body["data"]
    assert "count" in body["data"]
    assert body["meta"]["pagination"]["limit"] == 50


def test_capabilities_have_stable_catalog(client, auth_headers_reader):
    """G01 ships a static catalog — verify it includes the seed capabilities."""
    r = client.get("/api/v1/capabilities", headers=auth_headers_reader)
    items = r.json()["data"]["items"]
    names = [i["name"] for i in items]
    assert "web.fetch" in names
    assert "reasoning.query" in names


def test_capabilities_invalid_key_rejected(client):
    r = client.get(
        "/api/v1/capabilities",
        headers={"Authorization": "Bearer bogus"},
    )
    assert r.status_code == 401
