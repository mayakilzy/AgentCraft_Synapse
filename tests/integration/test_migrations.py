"""Migration tests — upgrade + downgrade on SQLite.

Per the user's G02 Final Qualification §1: verify migration integrity.
Uses synchronous SQLAlchemy to inspect tables (avoids asyncio.run()
which closes the session event loop and breaks downstream async tests).
"""

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


def _list_tables_sync(db_url: str) -> list[str]:
    """List tables in the DB using sync SQLAlchemy.

    The DB URL is async (sqlite+aiosqlite://...); we convert to a sync
    driver (sqlite://) for inspection only. This avoids asyncio.run()
    which would close the pytest session event loop and break downstream
    async tests.
    """
    from sqlalchemy import create_engine, inspect

    sync_url = db_url.replace("sqlite+aiosqlite://", "sqlite://")
    eng = create_engine(sync_url)
    try:
        return inspect(eng).get_table_names()
    finally:
        eng.dispose()


def test_migration_upgrade_creates_tables(tmp_path):
    db_file = tmp_path / "mig_up.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    result = _run_alembic("upgrade", "head", db_url=db_url)
    assert result.returncode == 0, result.stderr + "\n" + result.stdout

    tables = _list_tables_sync(db_url)
    expected = {
        "sources",
        "jobs",
        "idempotency_keys",
        "audit_events",
        "capabilities",
        "providers",
        "evidence_fragments",
    }
    assert expected.issubset(set(tables)), f"missing tables: {expected - set(tables)}"


def test_migration_downgrade_drops_tables(tmp_path):
    db_file = tmp_path / "mig_down.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    up = _run_alembic("upgrade", "head", db_url=db_url)
    assert up.returncode == 0, up.stderr

    down = _run_alembic("downgrade", "base", db_url=db_url)
    assert down.returncode == 0, down.stderr + "\n" + down.stdout

    tables = _list_tables_sync(db_url)
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

    # After round-trip, all expected tables exist.
    tables = _list_tables_sync(db_url)
    assert "evidence_fragments" in tables, "evidence_fragments missing after round-trip"
    assert "sources" in tables, "sources missing after round-trip"


def test_migration_0001_initial_unchanged_from_g01_baseline():
    """Per Final Qualification §1: confirm 0001_initial.py is unchanged
    from the G01 baseline commit (7a9088a)."""
    import subprocess

    result = subprocess.run(
        ["git", "diff", "7a9088a", "HEAD", "--", "alembic/versions/0001_initial.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"git diff failed: {result.stderr}"
    assert result.stdout == "", (
        "alembic/versions/0001_initial.py has been modified since G01 baseline "
        "(commit 7a9088a). Per Final Qualification §1, this migration must "
        "remain unchanged. Diff:\n" + result.stdout
    )


def test_migration_0002_is_separate_and_additive():
    """Per Final Qualification §1: confirm 0002 is a separate, additive
    migration with the correct down_revision chain."""
    import ast

    source = (REPO_ROOT / "alembic" / "versions" / "0002_evidence_fragments.py").read_text()

    # Extract revision + down_revision via AST.
    # Handle both `revision = "..."` (ast.Assign) and
    # `revision: str = "..."` (ast.AnnAssign).
    tree = ast.parse(source)
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    if isinstance(node.value, ast.Constant):
                        assignments[target.id] = node.value.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id in {"revision", "down_revision"}
                and isinstance(node.value, ast.Constant)
            ):
                assignments[node.target.id] = node.value.value

    assert assignments.get("revision") == "0002_evidence_fragments", (
        f"unexpected revision: {assignments.get('revision')}"
    )
    assert assignments.get("down_revision") == "0001_initial", (
        f"unexpected down_revision: {assignments.get('down_revision')} — "
        "0002 must chain after 0001_initial (additive, not parallel)"
    )

    # 0002 must NOT modify any existing table — only CREATE TABLE + CREATE INDEX.
    # `op.alter_column` is forbidden entirely.
    assert "op.alter_column" not in source, "0002 uses op.alter_column — not purely additive"
