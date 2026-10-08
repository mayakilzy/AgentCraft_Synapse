"""Top-level pytest fixtures for the Synapse test suite.

Goals:
- Each test runs against a fresh, isolated in-memory SQLite database.
- Settings are forced to ``test`` profile so production-only checks never fire.
- No external network calls. SSRF validator is mocked in unit tests.
- A TestClient with a real async app + engine is provided for API tests.

Resource handling:
- Tests use a dependency override for ``get_db`` so the per-test engine is
  shared between the TestClient and direct DB access in tests.
- The engine is disposed at fixture teardown — no ResourceWarning leak.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make src/ importable for tests.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Force test profile BEFORE any settings are imported.
os.environ["SYNAPSE_ENV"] = "test"
os.environ["SYNAPSE_DB_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SYNAPSE_AUTH_MODE"] = "development"
os.environ["SYNAPSE_DEV_API_KEYS"] = "test-key-reader"
os.environ["SYNAPSE_ADMIN_API_KEYS"] = "test-key-admin"
os.environ["SYNAPSE_CORS_ORIGINS"] = "http://localhost:3000"
os.environ["SYNAPSE_CORS_ALLOW_CREDENTIALS"] = "true"
os.environ["SYNAPSE_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["SYNAPSE_RATE_LIMIT_BURST"] = "1000"
os.environ["SYNAPSE_LOG_LEVEL"] = "WARNING"


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Each test gets fresh settings (because we may mutate env)."""
    from synapse.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def test_settings():
    from synapse.config import get_settings

    return get_settings()


@pytest_asyncio.fixture()
async def engine(tmp_path):
    """Per-test async engine bound to an isolated SQLite file.

    The engine is created with a fresh DB file (in tmp_path) and the
    full schema is created via ``Base.metadata.create_all``. The engine
    is disposed at teardown so no resources leak.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from synapse.storage import models  # noqa: F401 — register tables
    from synapse.storage.base import Base

    db_file = tmp_path / "synapse_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    eng = create_async_engine(db_url, future=True, pool_pre_ping=True)

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture()
async def db_session(engine):
    """Per-test async session bound to the engine fixture."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture()
def app(engine, monkeypatch):
    """A fresh FastAPI app per test, with the DB dependency overridden to
    use the per-test engine."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from synapse.api import deps as deps_module
    from synapse.config import get_settings
    from synapse.main import create_app

    # Reset cached engine before each test
    deps_module._engine = None
    deps_module._session_factory = None
    get_settings.cache_clear()

    # Point the test at the per-test engine
    monkeypatch.setenv("SYNAPSE_DB_URL", str(engine.url))
    get_settings.cache_clear()

    application = create_app()

    # Override get_db to use the test engine's session factory
    test_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with test_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    application.dependency_overrides[deps_module.get_db] = _override_get_db

    yield application

    # Cleanup: dispose engine & reset overrides
    application.dependency_overrides.clear()
    deps_module._engine = None
    deps_module._session_factory = None
    get_settings.cache_clear()


@pytest.fixture()
def client(app):
    """FastAPI TestClient (sync wrapper around the async app).

    Uses ``with`` so the lifespan runs and any startup/shutdown hooks fire.
    """
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers_reader():
    return {"Authorization": "Bearer test-key-reader"}


@pytest.fixture()
def auth_headers_admin():
    return {"Authorization": "Bearer test-key-admin"}
