# AgentCraft Synapse

> Relationship-aware **knowledge, hypothesis & future-intelligence engine**.
> Backend implementation of the Master Architecture v2.0 specification.

[![CI](https://img.shields.io/badge/CI-local%20pytest-green)]()
[![Python](https://img.shields.io/badge/python-3.12-blue)]()
[![License](https://img.shields.io/badge/license-proprietary-lightgrey)]()

## What this is

Synapse is a continuously evolving technical-intelligence system that:
discovers evidence → extracts structured knowledge → models relationships &
capabilities → reasons across them → proposes innovations → critiques
hypotheses → designs experiments → updates beliefs from results → explores
evidence-grounded future scenarios.

The system is **API-first**, modular-monolith, provider-independent, and built
around the principle *evidence before confidence*.

## Status

| Group | Title                                | Status                  |
|-------|--------------------------------------|-------------------------|
| G01   | Foundation, Relationship Model & API  | ✅ Delivered (this PR) |
| G02   | Acquisition                          | ⏳ Awaiting user approval |
| G03   | Knowledge & Verification             | ⏳ Pending |
| G04   | Retrieval & Reasoning                | ⏳ Pending |
| G05   | Innovation & Experiments             | ⏳ Pending |
| G06   | Future Intelligence, Integration     | ⏳ Pending |

See `reports/GROUP_01_REPORT.md` for the closure report.

## Stack (reversible engineering defaults, validated in G01)

- **Python 3.12**, **FastAPI**, **Pydantic v2** (typed domain & DTOs)
- **SQLAlchemy 2.0** (async), **Alembic** migrations
- **PostgreSQL** for production; **SQLite** (aiosqlite) for tests / local dev
- **pytest**, **ruff**, **httpx** (TestClient)
- Modular monolith with packages `api`, `domain`, `storage`, `security`,
  `providers`, `observability`, `application`, `workers`

No Redis / Kafka / NATS / Temporal / graph database in G01 (per binding
decisions in `DECISIONS_AND_ASSUMPTIONS.md`).

## Quickstart

```bash
# 1. Create venv & install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,postgres]"

# 2. Configure (no secrets in repo)
cp .env.example .env

# 3. Apply migrations
alembic upgrade head

# 4. Run service
uvicorn synapse.main:app --reload --port 8000

# 5. Inspect OpenAPI
open http://127.0.0.1:8000/api/v1/openapi.json
open http://127.0.0.1:8000/docs
```

## Development commands

```bash
make help          # list available targets
make install       # editable install with dev deps
make lint          # ruff check
make format        # ruff format
make test          # pytest with coverage
make test-fast     # pytest without coverage
make migrate-up    # alembic upgrade head
make migrate-down  # alembic downgrade base
make openapi-check # validate OpenAPI schema is well-formed
make smoke         # quick smoke tests against running app
```

## Project layout

```
src/synapse/
├── main.py             # FastAPI entrypoint, app factory
├── config.py           # pydantic-settings based Settings
├── domain/             # Pydantic v2 domain records (binding contracts)
├── storage/            # SQLAlchemy models, session, base
├── api/
│   ├── v1/             # /api/v1 routes
│   ├── errors.py       # problem+json envelope
│   ├── middleware.py   # request-id, CORS, error handler
│   └── deps.py         # auth dependency, db session
├── security/           # auth adapter (fail-closed prod), SSRF guard
├── providers/          # provider registry (stub for G01)
├── observability/      # structured logging
└── workers/            # job runner (stub for G01)
alembic/                # migrations
tests/                  # unit + integration
docs/                   # baseline, architecture, ADRs
reports/                # group reports + JSON evidence
```

## Security baseline (G01)

- **Auth**: pluggable adapter; `development` mode accepts static API keys from
  env; `production` mode **fails closed** (401) unless a real provider is
  configured. RBAC scopes: `reader`, `researcher`, `editor`, `operator`, `admin`.
- **CORS**: explicit origin allowlist; rejects `*` with credentials.
- **SSRF**: URL validation rejects private IP ranges, loopback, link-local,
  cloud metadata endpoints (`169.254.169.254`); per-fetch timeout & size cap.
- **Secrets**: nothing logged; `pydantic-settings` enforces `.env` parsing;
  `.env` is git-ignored; sample env contains no real values.

## License

Proprietary — internal use only. See `LICENSE` (to be added) for terms.
