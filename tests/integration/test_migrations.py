"""Migration tests — upgrade + downgrade on SQLite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess:
    """Run alembic with the given args and DB URL."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "SYNAPSE_ENV": "test",
        "SYNAPSE_DB_URL": db_url,
        "SYNAPSE_AUTH_MODE": "development",
        "SYNAPSE_CORS_ORIGINS": "http://localhost:3000",
        "SYNAPSE_CORS_ALLOW_CREDENTIALS": "true",
    }
    cmd = [sys.executable, "-m", "alembic", *args]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_migration_upgrade_creates_tables(tmp_path):
    db_file = tmp_path / "mig_up.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    result = _run_alembic("upgrade", "head", db_url=db_url)
    assert result.returncode == 0, result.stderr + "\n" + result.stdout

    # Verify tables exist by running a SELECT via SQLAlchemy
    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check():
        eng = create_async_engine(db_url)
        async with eng.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        await eng.dispose()
        return tables

    tables = asyncio.run(_check())
    expected = {"sources", "jobs", "idempotency_keys", "audit_events", "capabilities", "providers"}
    assert expected.issubset(set(tables)), f"missing tables: {expected - set(tables)}"


def test_migration_downgrade_drops_tables(tmp_path):
    db_file = tmp_path / "mig_down.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    up = _run_alembic("upgrade", "head", db_url=db_url)
    assert up.returncode == 0, up.stderr

    down = _run_alembic("downgrade", "base", db_url=db_url)
    assert down.returncode == 0, down.stderr + "\n" + down.stdout

    import asyncio

    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _check():
        eng = create_async_engine(db_url)
        async with eng.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        await eng.dispose()
        return tables

    tables = asyncio.run(_check())
    # alembic_version is left behind by downgrade — that's fine, it's the
    # migration bookkeeping table, not an application table.
    app_tables = [t for t in tables if t != "alembic_version"]
    assert app_tables == [], f"expected empty application DB after downgrade, got {app_tables}"


def test_migration_upgrade_then_upgrade_again_is_idempotent(tmp_path):
    db_file = tmp_path / "mig_idem.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    first = _run_alembic("upgrade", "head", db_url=db_url)
    assert first.returncode == 0, first.stderr
    second = _run_alembic("upgrade", "head", db_url=db_url)
    assert second.returncode == 0, second.stderr
    # Second run should be a no-op
    assert "Running upgrade" not in second.stdout


def test_migration_round_trip_preserves_clean_db(tmp_path):
    """upgrade head → downgrade base → upgrade head again should work."""
    db_file = tmp_path / "mig_roundtrip.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    up1 = _run_alembic("upgrade", "head", db_url=db_url)
    assert up1.returncode == 0, up1.stderr
    down = _run_alembic("downgrade", "base", db_url=db_url)
    assert down.returncode == 0, down.stderr
    up2 = _run_alembic("upgrade", "head", db_url=db_url)
    assert up2.returncode == 0, up2.stderr
