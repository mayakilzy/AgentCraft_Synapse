"""Auth adapter tests — dev adapter & fail-closed production."""

from __future__ import annotations

import pytest

from synapse.security.auth import (
    SCOPE_HIERARCHY,
    DevelopmentAuthAdapter,
    Principal,
    Scope,
    UnauthenticatedError,
    anonymous_principal,
)


def test_dev_adapter_missing_credentials_rejected():
    adapter = DevelopmentAuthAdapter()
    with pytest.raises(UnauthenticatedError):
        adapter.authenticate(None)


def test_dev_adapter_unknown_key_rejected():
    adapter = DevelopmentAuthAdapter()
    with pytest.raises(UnauthenticatedError):
        adapter.authenticate("Bearer bogus")


def test_dev_adapter_admin_key_grants_all_scopes(test_settings):
    adapter = DevelopmentAuthAdapter(test_settings)
    principal = adapter.authenticate("Bearer test-key-admin")
    assert principal.kind == "dev"
    assert Scope.ADMIN in principal.scopes
    assert Scope.READER in principal.scopes


def test_dev_adapter_reader_key_grants_researcher(test_settings):
    adapter = DevelopmentAuthAdapter(test_settings)
    principal = adapter.authenticate("Bearer test-key-reader")
    assert Scope.RESEARCHER in principal.scopes
    assert Scope.ADMIN not in principal.scopes


def test_production_adapter_fails_closed_when_no_provider(monkeypatch):
    """Per ADR-0003: production without a configured provider MUST reject
    every request."""
    from synapse.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("SYNAPSE_ENV", "production")
    monkeypatch.setenv("SYNAPSE_AUTH_MODE", "production")
    monkeypatch.setenv("SYNAPSE_AUTH_PROVIDER", "")
    monkeypatch.delenv("SYNAPSE_AUTH_PROVIDER", raising=False)

    # Re-validate settings — production mode should reject empty provider.
    # pydantic-settings raises ValidationError when model_validator fails.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        get_settings()


def test_scope_hierarchy_admin_includes_all():
    admin = SCOPE_HIERARCHY[Scope.ADMIN]
    for scope in Scope:
        assert scope in admin


def test_principal_has_scope_respects_hierarchy():
    admin = Principal(kind="dev", subject="x", scopes=frozenset(SCOPE_HIERARCHY[Scope.ADMIN]))
    assert admin.has_scope(Scope.READER)
    assert admin.has_scope(Scope.OPERATOR)

    reader = Principal(kind="dev", subject="y", scopes=frozenset(SCOPE_HIERARCHY[Scope.READER]))
    assert reader.has_scope(Scope.READER)
    assert not reader.has_scope(Scope.EDITOR)


def test_anonymous_principal_is_anonymous():
    p = anonymous_principal()
    assert p.is_anonymous is True
    assert p.scopes == frozenset()
    assert not p.has_scope(Scope.READER)
