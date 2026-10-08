"""FastAPI dependencies — auth, db session, request-id."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.security.auth import (
    AuthAdapter,
    ForbiddenError,
    Principal,
    Scope,
    UnauthenticatedError,
    anonymous_principal,
    get_auth_adapter,
)
from synapse.storage.db import create_engine, create_session_factory

# ── Auth ──────────────────────────────────────────────────────────────────────


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID", "")


RequestIDDep = Annotated[str, Depends(get_request_id)]


def _default_auth_adapter() -> AuthAdapter:
    return get_auth_adapter()


def get_principal_dep(
    authorization: Annotated[str | None, Header()] = None,
    adapter: Annotated[AuthAdapter, Depends(_default_auth_adapter)] = None,
) -> Principal:
    """Resolve the current principal from the Authorization header.

    Routes that require auth should depend on this. Public endpoints can
    depend on ``get_optional_principal`` instead.
    """
    ad = adapter or get_auth_adapter()
    return ad.authenticate(authorization)


PrincipalDep = Annotated[Principal, Depends(get_principal_dep)]


def get_optional_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Like ``get_principal_dep`` but returns anonymous if no header."""
    if not authorization:
        return anonymous_principal()
    try:
        return get_auth_adapter().authenticate(authorization)
    except UnauthenticatedError:
        return anonymous_principal()


OptionalPrincipalDep = Annotated[Principal, Depends(get_optional_principal)]


def require_scopes(*required: Scope):
    """Build a dependency that enforces the given scopes on a route.

    Usage::

        @router.get("/admin", dependencies=[Depends(require_scopes(Scope.ADMIN))])
        def admin_endpoint(...): ...
    """

    def _checker(principal: PrincipalDep) -> Principal:
        for scope in required:
            if not principal.has_scope(scope):
                raise ForbiddenError(f"Principal lacks required scope: {scope.value}")
        return principal

    return _checker


# ── DB session ────────────────────────────────────────────────────────────────

# The engine is created lazily so tests can swap in their own via dependency
# overrides. In production this is a singleton.
_engine = None
_session_factory = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine()
        _session_factory = create_session_factory(_engine)
    return _engine, _session_factory


async def get_db() -> AsyncSession:
    """Yield a session for the request lifecycle."""
    _, factory = get_engine()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
