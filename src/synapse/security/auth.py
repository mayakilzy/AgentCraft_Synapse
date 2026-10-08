"""Authentication & authorization.

Two adapters as decided in ADR-0003:

- ``DevelopmentAuthAdapter``  — static API keys from env (dev only)
- ``ProductionAuthAdapter``   — placeholder that fails closed unless a
  real provider is configured

Both produce a ``Principal`` (the authenticated actor) or raise
``UnauthenticatedError`` / ``ForbiddenError`` (caught by the API error
middleware and turned into 401/403 responses).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from synapse.config import Settings, get_settings
from synapse.observability.logging import get_logger

_log = get_logger("synapse.security.auth")


class Scope(str, Enum):
    """RBAC scopes per Master Spec §15."""

    READER = "reader"
    RESEARCHER = "researcher"
    EDITOR = "editor"
    OPERATOR = "operator"
    ADMIN = "admin"


# Hierarchy: each scope includes the privileges of all lower scopes.
SCOPE_HIERARCHY: dict[Scope, set[Scope]] = {
    Scope.READER: {Scope.READER},
    Scope.RESEARCHER: {Scope.READER, Scope.RESEARCHER},
    Scope.EDITOR: {Scope.READER, Scope.RESEARCHER, Scope.EDITOR},
    Scope.OPERATOR: {Scope.READER, Scope.RESEARCHER, Scope.EDITOR, Scope.OPERATOR},
    Scope.ADMIN: {
        Scope.READER,
        Scope.RESEARCHER,
        Scope.EDITOR,
        Scope.OPERATOR,
        Scope.ADMIN,
    },
}


@dataclass(frozen=True)
class Principal:
    """The authenticated actor — passed through the request lifecycle."""

    kind: str  # "dev" | "service-account" | "user" | "anonymous"
    subject: str  # key id, user id, etc.
    scopes: frozenset[Scope] = field(default_factory=frozenset)
    tenant_id: str | None = None  # multi-tenant is a future group
    is_anonymous: bool = False

    def has_scope(self, required: Scope) -> bool:
        return required in self.scopes or Scope.ADMIN in self.scopes


class AuthError(Exception):
    """Base class for auth-related errors."""

    code: str = "auth_error"
    http_status: int = 401

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class UnauthenticatedError(AuthError):
    code = "unauthenticated"
    http_status = 401


class ForbiddenError(AuthError):
    code = "forbidden"
    http_status = 403


class AuthAdapter(Protocol):
    """Auth adapters implement this."""

    def authenticate(self, credentials: str | None) -> Principal:  # pragma: no cover
        ...


class DevelopmentAuthAdapter:
    """Reads static API keys from env. Dev only — never use in production.

    Keys are comma-separated in ``SYNAPSE_DEV_API_KEYS`` (reader/researcher)
    and ``SYNAPSE_ADMIN_API_KEYS`` (admin).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def authenticate(self, credentials: str | None) -> Principal:
        if not credentials:
            raise UnauthenticatedError("Missing API key")
        # Accept "Bearer xxx" or raw key
        token = credentials.removeprefix("Bearer ").strip()
        if token in self.settings.admin_api_key_set:
            return Principal(
                kind="dev",
                subject=token[:8] + "…",  # never log full key
                scopes=frozenset(SCOPE_HIERARCHY[Scope.ADMIN]),
            )
        if token in self.settings.dev_api_key_set:
            return Principal(
                kind="dev",
                subject=token[:8] + "…",
                scopes=frozenset(SCOPE_HIERARCHY[Scope.RESEARCHER]),
            )
        _log.warning("auth_failed dev adapter: unknown api key prefix=%s", token[:4])
        raise UnauthenticatedError("Invalid API key")


class ProductionAuthAdapter:
    """Placeholder for production. Fails closed unless a real provider is
    configured via ``SYNAPSE_AUTH_PROVIDER`` (left for a future group)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def authenticate(self, credentials: str | None) -> Principal:
        # When a real provider is integrated (OIDC, internal SSO, etc.),
        # this method dispatches to it. Until then: fail closed.
        if not self.settings.auth_provider:
            raise UnauthenticatedError(
                "Production auth provider not configured (set SYNAPSE_AUTH_PROVIDER)"
            )
        # Hook for future providers — never silently allow.
        raise UnauthenticatedError(
            f"Auth provider {self.settings.auth_provider!r} not implemented yet"
        )


def get_auth_adapter() -> AuthAdapter:
    """Pick the right adapter based on ``SYNAPSE_AUTH_MODE``."""
    settings = get_settings()
    if settings.auth_mode == "production":
        return ProductionAuthAdapter(settings)
    return DevelopmentAuthAdapter(settings)


_ANONYMOUS = Principal(
    kind="anonymous",
    subject="anonymous",
    scopes=frozenset(),
    is_anonymous=True,
)


def anonymous_principal() -> Principal:
    """A principal with no scopes — used for public endpoints (health)."""
    return _ANONYMOUS
