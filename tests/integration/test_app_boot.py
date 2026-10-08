"""App boot tests — cold-start, fail-closed production, secret redaction."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture()
def _isolate_env(monkeypatch) -> Iterator[None]:
    """Save & restore env so production-mode tests don't bleed."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_app_boot_in_test_env(app):
    """The fixture-built app boots cleanly."""
    assert app.title == "AgentCraft Synapse"


def test_app_routes_registered(app):
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/health/live" in paths
    assert "/api/v1/capabilities" in paths
    assert "/api/v1/providers" in paths


def test_app_lifespan_starts_and_stops():
    """Verify the lifespan runs without raising."""
    from fastapi.testclient import TestClient

    from synapse.main import create_app

    app = create_app()
    with TestClient(app) as _client:
        pass  # lifespan entered and exited cleanly


def test_production_env_fails_fast_without_auth(monkeypatch):
    """Per ADR-0003 — production mode without auth_provider must refuse
    to construct Settings."""
    from synapse.config import Settings

    monkeypatch.setenv("SYNAPSE_ENV", "production")
    monkeypatch.setenv("SYNAPSE_AUTH_MODE", "development")
    monkeypatch.delenv("SYNAPSE_AUTH_PROVIDER", raising=False)

    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert "production" in str(exc_info.value).lower()


def test_production_env_with_wildcard_cors_credentials_blocked(monkeypatch):
    """Per ADR-0006 — '*' + credentials is forbidden."""
    from synapse.config import Settings

    monkeypatch.setenv("SYNAPSE_ENV", "production")
    monkeypatch.setenv("SYNAPSE_AUTH_MODE", "production")
    monkeypatch.setenv("SYNAPSE_AUTH_PROVIDER", "oidc://stub")
    monkeypatch.setenv("SYNAPSE_CORS_ORIGINS", "*")
    monkeypatch.setenv("SYNAPSE_CORS_ALLOW_CREDENTIALS", "true")

    # pydantic-settings raises ValidationError when model_validator fails.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings()


def test_test_env_rejects_postgres_url(monkeypatch):
    """Tests must not hit a real Postgres — fail fast."""
    from synapse.config import Settings

    monkeypatch.setenv("SYNAPSE_ENV", "test")
    monkeypatch.setenv("SYNAPSE_DB_URL", "postgresql+psycopg://u:p@host/db")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings()


def test_secret_redaction_in_logger(capsys):
    """Per ADR-0006 — secrets must never appear in logs.

    Two checks:
      1. With the human formatter, extra fields are not printed at all —
         so the secret doesn't leak even if not explicitly masked.
      2. With the JSON formatter, the field IS printed but masked.
    """
    from synapse.observability.logging import configure_logging, get_logger

    # ── Check 1: human formatter ─────────────────────────────────────────
    configure_logging(level="INFO", json_output=False)
    log = get_logger("test.redact.human")
    log.info(
        "request received",
        extra={
            "authorization": "Bearer ghp_supersecret",
            "synapse_dev_api_keys": "key1,key2",
        },
    )
    captured = capsys.readouterr().err
    assert "ghp_supersecret" not in captured
    assert "key1,key2" not in captured

    # ── Check 2: JSON formatter ──────────────────────────────────────────
    configure_logging(level="INFO", json_output=True)
    log2 = get_logger("test.redact.json")
    log2.info(
        "request received",
        extra={
            "authorization": "Bearer ghp_supersecret",
            "synapse_dev_api_keys": "key1,key2",
        },
    )
    captured = capsys.readouterr().err
    assert "ghp_supersecret" not in captured
    assert "key1,key2" not in captured
    # In JSON output, the masked field IS present
    assert "REDACTED" in captured
