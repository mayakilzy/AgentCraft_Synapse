# ADR-0001 — Modular monolith, single backend, single store

- **Status**: Accepted (binding per Master Spec v2.0 §3)
- **Date**: 2026-10-08
- **Group**: G01-T01

## Context

Master Spec v2.0 §3 mandates a modular monolith with background workers and
one relational store; modules separated by contracts, not by repositories
or network services. The START_HERE contract (§3) explicitly forbids
"premature microservices, swarm, graph database, or vendor lock-in" for G01.

## Decision

Synapse is deployed as a single Python process (FastAPI + uvicorn) with
background workers running in the same process group. There is one
relational database (PostgreSQL in production, SQLite in tests). Package
boundaries are:

- `synapse.api` — HTTP layer (FastAPI routers, middleware, error envelope)
- `synapse.domain` — typed Pydantic v2 records (binding contracts)
- `synapse.application` — service layer (orchestration, use-cases)
- `synapse.storage` — SQLAlchemy ORM, session, migrations
- `synapse.providers` — provider adapters (LLM, search, fetch)
- `synapse.security` — auth, SSRF, RBAC scopes
- `synapse.observability` — logging, tracing
- `synapse.workers` — background job runner (G01 stub)

Empty packages are **not** created. The `application` and `workers` packages
exist as namespaces for later groups; G01 does not populate them with
implementation code beyond `__init__.py` markers.

## Consequences

- ✅ No distributed-systems complexity in G01
- ✅ Same code path serves external UI and internal UI (API-first)
- ✅ Easy to extract services later when measured workload justifies it
- ⚠️ Future service splits require a documented ADR + measured evidence
