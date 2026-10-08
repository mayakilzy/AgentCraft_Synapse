"""OpenAPI schema validation tests."""

from __future__ import annotations


def test_openapi_published_at_v1_path(client):
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["openapi"].startswith("3.")


def test_openapi_lists_required_endpoints(client):
    """Verify the minimum contract routes are exposed."""
    r = client.get("/api/v1/openapi.json")
    paths = r.json()["paths"]
    required = [
        "/health/live",
        "/health/ready",
        "/api/v1/capabilities",
        "/api/v1/providers",
        "/api/v1/system/activity-mode",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/cancel",
        # placeholder future routes
        "/api/v1/sources/discover",
        "/api/v1/sources/ingest",
        "/api/v1/sources",
        "/api/v1/entities",
        "/api/v1/relationships",
        "/api/v1/knowledge/search",
        "/api/v1/reasoning/queries",
        "/api/v1/innovations/generate",
        "/api/v1/experiments",
        "/api/v1/future/scenarios",
    ]
    for path in required:
        assert path in paths, f"OpenAPI missing path: {path}"


def test_openapi_routes_use_v1_prefix(client):
    """Every non-health route must be under /api/v1."""
    r = client.get("/api/v1/openapi.json")
    paths = r.json()["paths"]
    for path in paths:
        assert path.startswith("/api/v1") or path.startswith("/health"), (
            f"Route outside /api/v1: {path}"
        )


def test_openapi_version_field_present(client):
    schema = client.get("/api/v1/openapi.json").json()
    assert "info" in schema
    assert "version" in schema["info"]


def test_swagger_ui_available_in_dev(client):
    """In dev mode, /api/v1/docs serves the Swagger UI."""
    r = client.get("/api/v1/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
