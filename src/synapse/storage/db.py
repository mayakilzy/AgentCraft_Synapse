"""Async engine, session factory, FastAPI dependency, and test isolation.

This module is the only place that creates ``AsyncEngine`` instances. Everything
else receives a session via dependency injection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from synapse.config import get_settings
from synapse.observability.logging import get_logger


def create_engine(db_url: str | None = None, *, echo: bool | None = None) -> AsyncEngine:
    """Build an async engine. Defaults come from Settings."""
    settings = get_settings()
    url = db_url or settings.db_url
    is_sqlite = url.startswith("sqlite")
    return create_async_engine(
        url,
        echo=echo if echo is not None else settings.db_echo,
        # SQLite does not support pool_size / max_overflow
        pool_size=1 if is_sqlite else settings.db_pool_size,
        max_overflow=0 if is_sqlite else settings.db_max_overflow,
        connect_args={"check_same_thread": False} if is_sqlite else {},
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
    )


@asynccontextmanager
async def session_scope(
    engine: AsyncEngine | None = None,
) -> AsyncIterator[AsyncSession]:
    """Context-managed session that commits on success, rolls back on error.

    Usage::

        async with session_scope() as session:
            ...do work...
        # commits automatically on exit
    """
    if engine is None:
        engine = create_engine()
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session(
    engine: AsyncEngine | None = None,
) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields a session per request."""
    if engine is None:
        engine = create_engine()
    factory = create_session_factory(engine)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


_log = get_logger("synapse.storage")


async def dispose_engine(engine: AsyncEngine) -> None:
    """Dispose of engine connections — used in test fixtures and shutdown."""
    await engine.dispose()
    _log.debug("engine disposed")
