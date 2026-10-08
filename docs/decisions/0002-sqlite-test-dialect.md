# ADR-0002 — SQLite test dialect (PostgreSQL in production)

- **Status**: Accepted (reversible engineering default)
- **Date**: 2026-10-08
- **Group**: G01-T01, G01-T03

## Context

`DECISIONS_AND_ASSUMPTIONS.md` lists the reversible default
"Python 3.12 backend; FastAPI + Pydantic v2; PostgreSQL + pgvector if
available; Alembic migrations; pytest". The Master Spec further mandates
that required CI gates "must not depend on external services"
(START_HERE §8).

The development sandbox has **no PostgreSQL server** installed, and
mandating a Postgres container for every CI run would violate §8.

## Decision

- **Production**: `postgresql+psycopg://…` (per `SYNAPSE_DB_URL`).
- **Tests**: `sqlite+aiosqlite:///:memory:` (overridden in
  `tests/conftest.py`).
- The ORM models use only ANSI SQL types that exist on both dialects
  (`String`, `Text`, `Integer`, `BigInteger`, `DateTime`, `Boolean`,
  `JSON`, `UUID`). No Postgres-only types (e.g. `JSONB`, `ARRAY`,
  `TSVECTOR`) appear in G01 models.
- Alembic migrations are dialect-agnostic in G01. A future group may
  introduce Postgres-specific optimization migrations guarded by
  `dialect_name == 'postgresql'` checks.
- pgvector adoption is **deferred** until G03/G04 produce measured
  evidence that semantic search materially improves retrieval
  (binding decision per `DECISIONS_AND_ASSUMPTIONS.md`).

## Consequences

- ✅ CI runs without Docker / external services
- ✅ Migrations are tested in CI
- ⚠️ Subtle dialect differences (e.g. case-sensitivity on `LIKE`) must
  be guarded in integration tests; G01 tests are written to avoid
  dialect-specific SQL.
- ⚠️ pgvector-dependent features (semantic search, embeddings store)
  are out of scope for G01.
