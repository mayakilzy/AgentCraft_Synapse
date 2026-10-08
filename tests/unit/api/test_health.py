"""Health endpoint tests."""

from __future__ import annotations


def test_live_returns_ok(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["status"] == "ok"
    assert body["meta"]["api_version"] == "v1"
    assert body["error"] is None


def test_live_returns_request_id_header(client):
    r = client.get("/health/live")
    assert "X-Request-ID" in r.headers


def test_live_echoes_request_id_if_provided(client):
    r = client.get("/health/live", headers={"X-Request-ID": "test-rid-123"})
    assert r.headers["X-Request-ID"] == "test-rid-123"


def test_ready_returns_ok(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["status"] in ("ok", "degraded")


def test_live_does_not_require_auth(client):
    """Health endpoints are public — no Authorization header needed."""
    r = client.get("/health/live")
    assert r.status_code == 200
