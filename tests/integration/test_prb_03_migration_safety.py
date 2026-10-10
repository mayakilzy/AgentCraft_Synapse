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
        # 1 entity with valid JSON but NO fingerprint field (should be skipped
        # by the IS NOT NULL check, not by malformed-JSON detection)
        conn.execute(
            text(
                "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                "VALUES ('ent-clean-no-fp', 'project', 'No FP', '[]', "
                "'{\"problem_domain\": \"test\"}', 1)"
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


def _seed_legacy_with_malformed_json(sync_url: str):
    r"""Seed legacy data with malformed JSON that passes the old regex
    but fails genuine JSON validation.

    The old regex '^\s*\{.*\}\s*$' passes all of these, but they are
    NOT valid JSON:
    - '{"name": }' — missing value after colon
    - '{not json at all}' — no quotes, no colons
    - '{"key": "val",}' — trailing comma
    """
    eng = create_engine(sync_url)
    with eng.begin() as conn:
        # Valid entity (should backfill)
        conn.execute(
            text(
                "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                "VALUES ('ent-valid-1', 'project', 'Valid 1', '[]', "
                "'{\"concept_fingerprint\": \"valid-fp-1\"}', 1)"
            )
        )
        # Malformed JSON that passes the OLD regex but is NOT valid JSON
        for eid, bad_attrs in [
            ("ent-bad-regex-pass-1", '{"name": }'),              # missing value
            ("ent-bad-regex-pass-2", '{not json at all}'),       # no quotes
            ("ent-bad-regex-pass-3", '{"key": "val",}'),         # trailing comma
        ]:
            conn.execute(
                text(
                    "INSERT INTO entities (id, kind, canonical_name, aliases, attributes, version) "
                    "VALUES (:id, 'project', :name, '[]', :attrs, 1)"
                ),
                {"id": eid, "name": f"Bad {eid}", "attrs": bad_attrs},
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


@pytest.fixture()
def pg_malformed_json_legacy():
    """Fresh PG DB + migrations 0001-0004 + legacy data with malformed JSON
    that passes the old regex but fails genuine JSON validation."""
    _reset_db()
    r = _run_alembic("upgrade", "0004_relationships")
    assert r.returncode == 0, f"alembic upgrade 0004 failed: {r.stderr}"
    _seed_legacy_with_malformed_json(
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

            # The no-fingerprint entity must NOT have a fingerprint row
            malformed = conn.execute(
                text(
                    "SELECT count(*) FROM entity_fingerprints "
                    "WHERE entity_id = 'ent-clean-no-fp'"
                )
            ).scalar()
            assert malformed == 0, "no-fingerprint entity was backfilled (should be skipped)"

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


class TestMalformedJSONHandling:
    """Verify the migration GENUINELY validates JSON — not just a regex.

    Per the JSON safety patch mission: the old regex
    '^\\s*\\{.*\\}\\s*$' is NOT a JSON validator. Strings like
    '{"name": }' pass the regex but crash the PostgreSQL ::json cast.

    The corrected migration uses a PL/pgSQL DO block with exception
    handling (PostgreSQL) or json_valid() (SQLite) to genuinely
    validate. Malformed JSON entities are FAIL-CLOSED: the migration
    raises a diagnostic listing the entity IDs.
    """

    def test_regex_passing_malformed_json_detected(self, pg_malformed_json_legacy):
        """Malformed JSON that passes the OLD regex (e.g. '{"name": }')
        MUST be detected by the GENUINE JSON validation and cause the
        migration to fail with a diagnostic."""
        r = _run_alembic("upgrade", "head")

        # The migration MUST fail
        assert r.returncode != 0, (
            "Migration should FAIL when malformed JSON is detected, "
            "but it returned 0. Output: " + r.stdout + r.stderr
        )
        combined = r.stdout + r.stderr
        # The diagnostic must mention malformed JSON or the entity IDs
        assert (
            "malformed" in combined.lower()
            or "MALFORMED" in combined
            or "ent-bad-regex-pass" in combined
        ), (
            f"Migration failed but did not mention malformed JSON or the "
            f"bad entity IDs. Output: {combined[:500]}"
        )
        # The diagnostic must mention at least one of the malformed entity IDs
        assert any(
            eid in combined for eid in ["ent-bad-regex-pass-1", "ent-bad-regex-pass-2", "ent-bad-regex-pass-3"]
        ), (
            f"Diagnostic must mention the malformed entity IDs. Output: {combined[:500]}"
        )

    def test_malformed_json_fail_closed_no_partial_backfill(self, pg_malformed_json_legacy):
        """When the migration fails on malformed JSON, NO fingerprint rows
        are backfilled (fail-closed, not partial). The valid entity is
        NOT backfilled either — the operator must fix the malformed JSON
        first."""
        r = _run_alembic("upgrade", "head")
        assert r.returncode != 0

        eng = create_engine(pg_malformed_json_legacy)
        with eng.begin() as conn:
            # Alembic rolls back the entire migration (transactional DDL)
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

            # The valid entity (ent-valid-1) is still in entities (not deleted)
            valid_count = conn.execute(
                text("SELECT count(*) FROM entities WHERE id = 'ent-valid-1'")
            ).scalar()
            assert valid_count == 1, "Valid entity was deleted by failed migration"

            # All malformed entities are still present (not deleted)
            for eid in ["ent-bad-regex-pass-1", "ent-bad-regex-pass-2", "ent-bad-regex-pass-3"]:
                count = conn.execute(
                    text("SELECT count(*) FROM entities WHERE id = :id"),
                    {"id": eid},
                ).scalar()
                assert count == 1, f"Malformed entity {eid} was deleted by failed migration"

            # Alembic version is still 0004
            version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()
            assert version == "0004_relationships", (
                f"Alembic version should be 0004_relationships after failed "
                f"migration, got {version}"
            )

        eng.dispose()

    def test_valid_json_still_backfills_after_malformed_fixed(self, pg_malformed_json_legacy):
        """After fixing the malformed JSON (replacing with valid JSON),
        the migration succeeds and backfills all entities."""
        # First attempt fails (malformed JSON present)
        r1 = _run_alembic("upgrade", "head")
        assert r1.returncode != 0

        # Fix the malformed JSON entities — replace their attributes with valid JSON
        eng = create_engine(pg_malformed_json_legacy)
        with eng.begin() as conn:
            for eid in ["ent-bad-regex-pass-1", "ent-bad-regex-pass-2", "ent-bad-regex-pass-3"]:
                conn.execute(
                    text(
                        "UPDATE entities SET attributes = :attrs WHERE id = :id"
                    ),
                    {"attrs": json.dumps({"concept_fingerprint": f"fixed-fp-{eid}"}), "id": eid},
                )
        eng.dispose()

        # Second attempt should succeed
        r2 = _run_alembic("upgrade", "head")
        assert r2.returncode == 0, (
            f"Migration should succeed after fixing malformed JSON. "
            f"Output: {r2.stderr}"
        )

        # Verify all 4 entities are backfilled (1 originally valid + 3 fixed)
        eng = create_engine(pg_malformed_json_legacy)
        with eng.begin() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM entity_fingerprints")
            ).scalar()
            assert count == 4, (
                f"Expected 4 fingerprint rows after fixing malformed JSON, got {count}"
            )
        eng.dispose()
