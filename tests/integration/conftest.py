"""Integration-test-scoped fixtures for PostgreSQL isolation.

Problem
-------
The four PostgreSQL integration files (``test_prb_03_postgresql_concurrency``,
``test_prb_03_determinism``, ``test_prb_03_migration_safety``,
``test_g05_t06_audit_concurrency``) historically shared a single physical
database (``synapse_test``). Each file's ``_seed_fixture`` helper began by
``DELETE FROM`` every table, and ``test_prb_03_migration_safety._reset_db()``
went further: it ``DROP DATABASE synapse_test`` and re-created it from
scratch in every fixture.

When the files were run **individually**, they passed: each one re-seeded
the shared DB from zero and left it in a workable state for the next file.

When the files were run **together** (the G05-T07 acceptance gate), they
contaminated each other:

* ordering — alphabetical collection puts ``test_prb_03_migration_safety``
  before ``test_prb_03_postgresql_concurrency`` only sometimes; either
  way, one file's ``_reset_db`` / ``DELETE FROM`` runs while another
  file's module-scoped engine is still bound to the same DB;
* schema loss — ``_reset_db`` drops the whole DB, so any concurrent or
  subsequent file that expects ``entity_fingerprints`` to exist sees
  ``UndefinedTable``;
* row contamination — when ``_reset_db`` does NOT run (e.g. only the
  three non-migration files run together), shared ``DELETE FROM`` is
  not transactional across files, and a row written by file A can be
  observed by file B's assertions.

Solution
--------
Give each test module its own physical database.

``pg_engine`` (module-scoped) creates a fresh database named
``synapse_t_<module_label>_<random>``, applies all Alembic migrations,
returns an async engine bound to it, and drops the database on teardown.

``pg_sync_url`` (module-scoped) returns a sync psycopg URL to the same
isolated database, for tests that use ``sqlalchemy.create_engine``
directly (e.g. migration-safety seeding helpers).

``alembic_env_for(db_url)`` returns an environment dict pointing
``SYNAPSE_DB_URL`` at the given URL, so ``_run_alembic`` invocations
target the isolated DB instead of the shared ``synapse_test``.

No production application code is changed. Only test infrastructure
is touched.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PG_TEST_URL = os.environ.get("SYNAPSE_PG_TEST_URL", "")

# Skip the entire module's PG fixtures if PG is not configured.
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL,
    reason="Set SYNAPSE_PG_TEST_URL to run PostgreSQL integration tests",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_pg_url(url: str) -> dict[str, str]:
    """Parse a SQLAlchemy PostgreSQL URL into its components.

    Returns a dict with keys: driver, user, password, host, port, dbname.
    """
    m = re.match(
        r"^(?P<driver>postgresql\+psycopg)://"
        r"(?P<user>[^:@/]+)(?::(?P<password>[^@/]*))?@"
        r"(?P<host>[^:/]+):(?P<port>\d+)"
        r"/(?P<dbname>.+)$",
        url,
    )
    if not m:
        raise ValueError(f"Cannot parse PG_TEST_URL: {url!r}")
    return m.groupdict()


def _admin_sync_url() -> str:
    """A sync psycopg URL pointing at the ``postgres`` maintenance DB."""
    parts = _parse_pg_url(PG_TEST_URL)
    return f"postgresql+psycopg://{parts['user']}@{parts['host']}:{parts['port']}/postgres"


def _async_url_for(dbname: str) -> str:
    """An async SQLAlchemy URL pointing at the given database name."""
    parts = _parse_pg_url(PG_TEST_URL)
    return f"postgresql+psycopg://{parts['user']}@{parts['host']}:{parts['port']}/{dbname}"


def _sync_url_for(dbname: str) -> str:
    """A sync SQLAlchemy URL pointing at the given database name."""
    parts = _parse_pg_url(PG_TEST_URL)
    return f"postgresql+psycopg://{parts['user']}@{parts['host']}:{parts['port']}/{dbname}"


def _create_isolated_db(dbname: str) -> None:
    """Create a fresh PostgreSQL database with the given name.

    Terminates any lingering connections to a same-named DB, drops it if
    it exists, then creates it clean. Uses psycopg directly with
    autocommit=True because DROP/CREATE DATABASE cannot run inside a
    transaction.
    """
    import psycopg

    parts = _parse_pg_url(PG_TEST_URL)
    admin = psycopg.connect(
        f"host={parts['host']} port={parts['port']} user={parts['user']} dbname=postgres",
        autocommit=True,
    )
    try:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        admin.close()


def _drop_isolated_db(dbname: str) -> None:
    """Drop a PostgreSQL database by name (terminate connections first)."""
    import psycopg

    parts = _parse_pg_url(PG_TEST_URL)
    admin = psycopg.connect(
        f"host={parts['host']} port={parts['port']} user={parts['user']} dbname=postgres",
        autocommit=True,
    )
    try:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    finally:
        admin.close()


def _apply_migrations(db_url: str) -> None:
    """Apply all Alembic migrations to the database at db_url.

    Uses the project's alembic.ini and env.py. The SYNAPSE_DB_URL
    environment variable is set to db_url for the subprocess.
    """
    env = alembic_env_for(db_url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed for {db_url}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )


def alembic_env_for(db_url: str) -> dict[str, str]:
    """Return an env dict for ``alembic`` subprocesses targeting db_url.

    Use this to run ``alembic upgrade`` / ``alembic downgrade`` against
    an isolated DB instead of the shared ``synapse_test``.

    Forces SYNAPSE_ENV=development (the top-level tests/conftest.py sets
    SYNAPSE_ENV=test, which would cause Settings to reject PostgreSQL).
    """
    env = dict(os.environ)
    env["SYNAPSE_DB_URL"] = db_url
    env["SYNAPSE_ENV"] = "development"
    env["SYNAPSE_AUTH_MODE"] = "development"
    env["SYNAPSE_CORS_ORIGINS"] = "http://localhost:3000"
    env["SYNAPSE_CORS_ALLOW_CREDENTIALS"] = "true"
    env["SYNAPSE_ADMIN_API_KEYS"] = "test-key-admin"
    env["SYNAPSE_DEV_API_KEYS"] = "test-key-reader"
    env["SYNAPSE_LOG_LEVEL"] = "WARNING"
    env["PYTHONPATH"] = "src"
    return env


def run_alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    """Run an alembic command against the given database URL."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=alembic_env_for(db_url),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _module_label(request: pytest.FixtureRequest) -> str:
    """Derive a short, stable label for the requesting test module."""
    mod = request.module
    if mod is not None and mod.__name__:
        # e.g. "tests.integration.test_prb_03_postgresql_concurrency" →
        # "prb_03_postgresql_concurrency"
        name = mod.__name__.rsplit(".", 1)[-1]
        # Strip the leading "test_" prefix for readability.
        if name.startswith("test_"):
            name = name[5:]
        return name
    return "pg"


@pytest.fixture(scope="module")
def pg_engine(request: pytest.FixtureRequest):
    """Module-scoped async engine bound to an ISOLATED PostgreSQL database.

    A fresh database is created for each test module (named
    ``synapse_t_<label>_<random>``), all migrations are applied, and the
    engine is yielded. On teardown the engine is disposed and the
    database is dropped.

    This replaces the per-file ``pg_engine`` fixtures that previously
    shared the ``synapse_test`` database and contaminated each other
    when run in a combined suite.
    """
    if not PG_TEST_URL:
        pytest.skip("Set SYNAPSE_PG_TEST_URL to run PostgreSQL integration tests")

    label = _module_label(request)
    # Sanitize label: keep alphanumerics + underscore only
    safe_label = re.sub(r"[^A-Za-z0-9_]", "_", label)[:40]
    dbname = f"synapse_t_{safe_label}_{secrets.token_hex(4)}"

    _create_isolated_db(dbname)
    db_async_url = _async_url_for(dbname)
    db_sync_url = _sync_url_for(dbname)
    _apply_migrations(db_sync_url)

    eng = create_async_engine(db_async_url, future=True, pool_pre_ping=True)
    yield eng

    # Teardown: dispose engine, drop the isolated database
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(eng.dispose())
        loop.close()
    except Exception:
        pass
    _drop_isolated_db(dbname)


@pytest_asyncio.fixture()
async def pg_session(pg_engine):
    """Per-test async session bound to the isolated PG engine."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest.fixture(scope="module")
def pg_db_url(pg_engine) -> str:
    """The isolated database URL (sync form) for the current module.

    Use this in tests that need to open a separate sync engine (e.g.
    migration-safety seeding helpers, or subprocess alembic invocations).
    """
    # pg_engine.url is an Async URL; convert to sync by replacing the driver
    async_url = str(pg_engine.url)
    return async_url.replace("postgresql+psycopg://", "postgresql+psycopg://")


@pytest.fixture(scope="module")
def pg_db_async_url(pg_engine) -> str:
    """The isolated database URL (async form) for the current module."""
    return str(pg_engine.url)
