"""System & jobs endpoint tests."""

from __future__ import annotations


def test_get_activity_mode_ok(client, auth_headers_reader):
    r = client.get("/api/v1/system/activity-mode", headers=auth_headers_reader)
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["mode"] == "passive"
    assert "transitionable_to" in body["data"]


def test_put_activity_mode_returns_501(client, auth_headers_admin):
    """Per START_HERE §10: unsupported future routes return 501, not 200."""
    r = client.put(
        "/api/v1/system/activity-mode",
        headers=auth_headers_admin,
        json={"mode": "autonomous"},
    )
    assert r.status_code == 501
    body = r.json()
    assert body["error"]["code"] == "not_implemented"


def test_get_activity_mode_requires_auth(client):
    r = client.get("/api/v1/system/activity-mode")
    assert r.status_code == 401


def test_get_job_unknown_returns_404(client, auth_headers_reader):
    r = client.get(
        "/api/v1/jobs/does-not-exist",
        headers=auth_headers_reader,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"


def test_cancel_job_returns_501(client, auth_headers_admin):
    r = client.post(
        "/api/v1/jobs/some-id/cancel",
        headers=auth_headers_admin,
    )
    assert r.status_code == 501


def test_future_routes_return_501(client, auth_headers_reader):
    """All routes declared in DOMAIN_AND_API_CONTRACTS.md but NOT YET
    implemented must return 501, not 200, per START_HERE §10.

    The G02 minimal slice implemented the /sources/* routes, so they
    are excluded from this list (they return 200/422, not 501).
    """
    paths = [
        ("GET", "/api/v1/entities"),
        ("GET", "/api/v1/relationships"),
        ("POST", "/api/v1/knowledge/search"),
        ("POST", "/api/v1/reasoning/queries"),
        ("POST", "/api/v1/innovations/generate"),
        ("POST", "/api/v1/experiments"),
        ("POST", "/api/v1/future/scenarios"),
    ]
    for method, path in paths:
        r = client.request(method, path, headers=auth_headers_reader)
        assert r.status_code == 501, f"{method} {path} → {r.status_code}"
