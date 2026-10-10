"""PRB-03 Final Audit — Migration 0005 safety tests.

Tests against a real PostgreSQL database containing representative
legacy records, including duplicates and conflicts.

Verifies:
1. Existing entities are correctly backfilled (clean legacy data).
2. Duplicate fingerprints in legacy data are detected and explicitly reported.
3. Conflicting fingerprints cannot silently produce incorrect entity mappings.
4. Existing entity references and relationships remain intact.
5. Re-running the migration does not introduce duplicates.
6. Failure produces a safe rollback without partial migration state.

Per the audit mission: 'Prefer a fail-closed migration with a clear
diagnostic over silently discarding conflicts.'
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

PG_TEST_URL = os.environ.get("SYNAPSE_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_TEST_URL, reason="Set SYNAPSE_PG_TEST_URL to run migration safety tests"
)

REPO_ROOT = "/home/z/my-project/repos/AgentCraft_Synapse"


def _reset_db():
    """Drop + recreate synapse_test on PG. Forces termination of any
    lingering connections first."""
    import psycopg

    admin = psycopg.connect(
        "host=127.0.0.1 port=5433 user=synapse dbname=postgres", autocommit=True
    )
    try:
        # Terminate any lingering connections to synapse_test
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname='synapse_test' AND pid <> pg_backend_pid()"
        )
        admin.execute("DROP DATABASE IF EXISTS synapse_test")
        admin.execute("CREATE DATABASE synapse_test")
    finally:
        admin.close()


def _alembic_env() -> dict:
    env = dict(os.environ)
    env["SYNAPSE_DB_URL"] = PG_TEST_URL
    env["SYNAPSE_ENV"] = "development"
    env["SYNAPSE_AUTH_MODE"] = "development"
    env["SYNAPSE_CORS_ORIGINS"] = "http://localhost:3000"
    env["SYNAPSE_CORS_ALLOW_CREDENTIALS"] = "true"
    env["SYNAPSE_ADMIN_API_KEYS"] = "test-key-admin"
    env["SYNAPSE_DEV_API_KEYS"] = "test-key-reader"
    env["SYNAPSE_LOG_LEVEL"] = "WARNING"
    env["PYTHONPATH"] = "src"
    return env


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=_alembic_env(),
        capture_output=True,
        text=True,
        timeout=90,
    )


def _seed_legacy_clean(sync_url: str):
    """Seed legacy data with NO duplicates — clean backfill scenario."""
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sources (id, canonical_uri, source_type, status, version) "
                "VALUES ('src-clean', 'https://example.com/clean', 'paper', 'extracted', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO evidence_fragments "
                "(id, acquisition_id, source_id, source_uri, exact_excerpt, "
                "excerpt_hash, retrieved_at, extraction_method, content_fingerprint, "
                "toolkit_commit_sha) "
                "VALUES ('frag-clean', 'acq-clean', 'src-clean', "
                "'https://example.com/clean', 'excerpt', 'hash-clean', "
                "'2026-10-01T00:00:00Z', 'fixture', 'fp-clean', 'fixture')"
            )
        )
        # 2 project entities with distinct fingerprints
        for eid, fp in [("ent-clean-A", "clean-fp-A"), ("ent-clean-B", "clean-fp-B")]:
            conn.execute(
                text(
                    "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                    "VALUES (:id, 'project', :name, '[]', :attrs, 1)"
                ),
                {
                    "id": eid,
                    "name": f"Clean {eid}",
                    "attrs": json.dumps({"concept_fingerprint": fp}),
                },
            )
        # 1 experiment entity
        conn.execute(
            text(
                "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                "VALUES ('ent-clean-E', 'experiment', 'Clean Exp', '[]', "
                "'{\"experiment_fingerprint\": \"clean-fp-E\"}', 1)"
            )
        )
        # 1 entity with malformed JSON (should be skipped silently)
        conn.execute(
            text(
                "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                "VALUES ('ent-clean-malformed', 'project', 'Bad JSON', '[]', "
                "'not-valid-json{', 1)"
            )
        )
        # Claim + relationship referencing ent-clean-A
        conn.execute(
            text(
                "INSERT INTO claims (id, proposition, subject_ref, evidence_refs, "
                "epistemic_state, extraction_method, version) "
                "VALUES ('claim-clean-A', 'Clean claim', 'ent-clean-A', '[]', "
                "'hypothesized', 'fixture', 1)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO relationships (id, from_entity_id, to_entity_id, predicate, "
                "direction, origin, verification_state, version) "
                "VALUES ('rel-clean-A', 'ent-clean-A', 'ent-clean-A', 'SELF', "
                "'directed', 'explicit', 'unverified', 1)"
            )
        )
    eng.dispose()


def _seed_legacy_with_duplicates(sync_url: str):
    """Seed legacy data WITH duplicate fingerprints — fail-closed scenario."""
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        shared_fp = "aaaa1111bbbb2222cccc3333dddd4444eeee5555"
        # 2 entities share the same fingerprint (duplicate)
        for eid in ["ent-dup-A", "ent-dup-B"]:
            conn.execute(
                text(
                    "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                    "VALUES (:id, 'project', :name, '[]', :attrs, 1)"
                ),
                {
                    "id": eid,
                    "name": f"Dup {eid}",
                    "attrs": json.dumps({"concept_fingerprint": shared_fp}),
                },
            )
        # 1 entity with a unique fingerprint
        conn.execute(
            text(
                "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                "VALUES ('ent-dup-C', 'project', 'Dup C', '[]', "
                "'{\"concept_fingerprint\": \"unique-dup-fp-C\"}', 1)"
            )
        )
    eng.dispose()


@pytest.fixture()
def pg_clean_legacy():
    """Fresh PG DB + migrations 0001-0004 + clean legacy data (no duplicates)."""
    _reset_db()
    r = _run_alembic("upgrade", "0004_relationships")
    assert r.returncode == 0, f"alembic upgrade 0004 failed: {r.stderr}"
    _seed_legacy_clean(
        "postgresql+psycopg://synapse@127.0.0.1:5433/synapse_test"
    )
    yield "postgresql+psycopg://synapse@127.0.0.1:5433/synapse_test"


@pytest.fixture()
def pg_duplicate_legacy():
    """Fresh PG DB + migrations 0001-0004 + legacy data WITH duplicates."""
    _reset_db()
    r = _run_alembic("upgrade", "0004_relationships")
    assert r.returncode == 0, f"alembic upgrade 0004 failed: {r.stderr}"
    _seed_legacy_with_duplicates(
        "postgresql+psycopg://synapse@127.0.0.1:5433/synapse_test"
    )
    yield "postgresql+psycopg://synapse@127.0.0.1:5433/synapse_test"


class TestMigrationBackfill:
    """Verify existing entities are correctly backfilled (clean data)."""

    def test_backfill_creates_fingerprint_rows(self, pg_clean_legacy):
        r = _run_alembic("upgrade", "head")
        assert r.returncode == 0, f"migration failed: {r.stderr}"

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            # 2 project + 1 experiment = 3 fingerprint rows
            # (malformed-JSON entity is skipped)
            count = conn.execute(
                text("SELECT count(*) FROM entity_fingerprints")
            ).scalar()
            assert count == 3, (
                f"Expected 3 fingerprint rows (2 project + 1 experiment), got {count}"
            )

            # Verify each fingerprint maps to the correct entity
            for fp, expected_eid, expected_kind in [
                ("clean-fp-A", "ent-clean-A", "project"),
                ("clean-fp-B", "ent-clean-B", "project"),
                ("clean-fp-E", "ent-clean-E", "experiment"),
            ]:
                row = conn.execute(
                    text(
                        "SELECT entity_id, kind FROM entity_fingerprints "
                        "WHERE fingerprint = :fp"
                    ),
                    {"fp": fp},
                ).fetchone()
                assert row is not None, f"fingerprint {fp} not backfilled"
                assert row[0] == expected_eid, (
                    f"fingerprint {fp} mapped to {row[0]}, expected {expected_eid}"
                )
                assert row[1] == expected_kind

            # The malformed-JSON entity must NOT have a fingerprint row
            malformed = conn.execute(
                text(
                    "SELECT count(*) FROM entity_fingerprints "
                    "WHERE entity_id = 'ent-clean-malformed'"
                )
            ).scalar()
            assert malformed == 0, "malformed-JSON entity was backfilled (should be skipped)"

        eng.dispose()


class TestLegacyDuplicateHandling:
    """Verify duplicate fingerprints are detected and explicitly reported.

    Per the audit mission: 'Prefer a fail-closed migration with a clear
    diagnostic over silently discarding conflicts.'

    The corrected migration is FAIL-CLOSED: it detects duplicate
    fingerprints in legacy data, raises a RuntimeError listing the
    duplicates, and does NOT backfill any rows.
    """

    def test_duplicate_fingerprints_cause_migration_failure(self, pg_duplicate_legacy):
        """The migration MUST fail with a diagnostic when duplicates exist."""
        r = _run_alembic("upgrade", "head")

        # The migration MUST fail
        assert r.returncode != 0, (
            "Migration should FAIL when duplicate fingerprints are detected, "
            "but it returned 0. Output: " + r.stdout + r.stderr
        )
        combined = r.stdout + r.stderr
        assert "duplicate" in combined.lower(), (
            f"Migration failed but did not mention 'duplicate': {combined}"
        )
        # The diagnostic must mention the conflicting entity IDs
        assert "ent-dup-A" in combined or "ent-dup-B" in combined, (
            f"Diagnostic must mention the conflicting entity IDs: {combined}"
        )

    def test_fail_closed_no_partial_backfill(self, pg_duplicate_legacy):
        """When the migration fails on duplicates, the migration is
        rolled back fully by Alembic (transactional DDL). No
        entity_fingerprints table exists, and no legacy data is lost."""
        r = _run_alembic("upgrade", "head")
        assert r.returncode != 0

        eng = create_engine(pg_duplicate_legacy)
        with eng.begin() as conn:
            # Alembic rolls back the entire migration (transactional DDL),
            # so the entity_fingerprints table does NOT exist.
            table_exists = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'entity_fingerprints')"
                )
            ).scalar()
            assert not table_exists, (
                "entity_fingerprints table exists after migration failure — "
                "Alembic should have rolled back the entire migration"
            )

            # Legacy entities are intact (not deleted)
            ent_count = conn.execute(
                text("SELECT count(*) FROM entities WHERE id LIKE 'ent-dup-%'")
            ).scalar()
            assert ent_count == 3, f"Legacy entities lost: {ent_count}"

            # Alembic version is still 0004 (the migration did not advance)
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            assert version == "0004_relationships", (
                f"Alembic version should be 0004_relationships after failed "
                f"migration, got {version}"
            )

        eng.dispose()


class TestMigrationRollback:
    """Verify failure produces a safe rollback + idempotency."""

    def test_migration_is_idempotent(self, pg_clean_legacy):
        """Re-running the migration does not introduce duplicates."""
        r1 = _run_alembic("upgrade", "head")
        assert r1.returncode == 0, f"first migration failed: {r1.stderr}"

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            count_after_first = conn.execute(
                text("SELECT count(*) FROM entity_fingerprints")
            ).scalar()
        eng.dispose()

        # Run again (alembic sees we're at head, does nothing)
        r2 = _run_alembic("upgrade", "head")
        assert r2.returncode == 0

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            count_after_second = conn.execute(
                text("SELECT count(*) FROM entity_fingerprints")
            ).scalar()
        eng.dispose()

        assert count_after_first == count_after_second, (
            f"Re-running changed row count: {count_after_first} → {count_after_second}"
        )

    def test_downgrade_drops_table_cleanly(self, pg_clean_legacy):
        """Downgrade drops entity_fingerprints without affecting other tables."""
        r = _run_alembic("upgrade", "head")
        assert r.returncode == 0, f"upgrade failed: {r.stderr}"

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            tables_before = {
                r[0]
                for r in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).fetchall()
            }
            assert "entity_fingerprints" in tables_before
        eng.dispose()

        r_down = _run_alembic("downgrade", "0004_relationships")
        assert r_down.returncode == 0, f"downgrade failed: {r_down.stderr}"

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            tables_after = {
                r[0]
                for r in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).fetchall()
            }
            assert "entity_fingerprints" not in tables_after

            expected = {
                "alembic_version", "sources", "jobs", "idempotency_keys",
                "audit_events", "capabilities", "providers", "evidence_fragments",
                "entities", "claims", "source_spans", "relationships",
            }
            missing = expected - tables_after
            assert not missing, f"Downgrade dropped unexpected tables: {missing}"

            # Legacy data intact
            ent_count = conn.execute(
                text("SELECT count(*) FROM entities WHERE id LIKE 'ent-clean-%'")
            ).scalar()
            assert ent_count == 4, f"Legacy entities lost: {ent_count}"

            claim_count = conn.execute(
                text("SELECT count(*) FROM claims WHERE id = 'claim-clean-A'")
            ).scalar()
            assert claim_count == 1

            rel_count = conn.execute(
                text("SELECT count(*) FROM relationships WHERE id = 'rel-clean-A'")
            ).scalar()
            assert rel_count == 1

        eng.dispose()


class TestEntityReferenceIntegrity:
    """Verify existing entity references and relationships remain intact."""

    def test_legacy_claims_and_relationships_survive_migration(self, pg_clean_legacy):
        r = _run_alembic("upgrade", "head")
        assert r.returncode == 0, f"migration failed: {r.stderr}"

        eng = create_engine(pg_clean_legacy)
        with eng.begin() as conn:
            claim = conn.execute(
                text(
                    "SELECT subject_ref FROM claims WHERE id = 'claim-clean-A'"
                )
            ).fetchone()
            assert claim is not None
            assert claim[0] == "ent-clean-A"

            rel = conn.execute(
                text(
                    "SELECT from_entity_id, to_entity_id FROM relationships "
                    "WHERE id = 'rel-clean-A'"
                )
            ).fetchone()
            assert rel is not None
            assert rel[0] == "ent-clean-A"
            assert rel[1] == "ent-clean-A"

            ent_count = conn.execute(
                text("SELECT count(*) FROM entities WHERE id LIKE 'ent-clean-%'")
            ).scalar()
            assert ent_count == 4

        eng.dispose()
