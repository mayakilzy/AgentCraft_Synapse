# AgentCraft Synapse — Master Handoff for G07 Review (G01–G06)

**Document type**: Authoritative project handoff for initializing a new ChatGPT conversation dedicated to reviewing the proposed G07 user interface and interaction experience.

**Authoritative baseline SHA**: `435fda1332a3fdc5f531f34d03553df3054c0fae` (origin/main, post-G06-T04A).

**Date**: 2026-10-11.

**Developer (G01–G06)**: GLM.

**Reviewer (G01–G06)**: ChatGPT.

**G07 developer**: TBD (not authorized — STOP gate below).

**G07 reviewer**: ChatGPT (the new conversation that consumes this handoff).

---

## 0. How to read this document

This document is the **primary context document** for a new ChatGPT
conversation that will review the proposed G07 user experience. It is
not a tutorial; it is a factual reference.

### 0.1 Source-disclaimer conventions

Throughout this document, claims are tagged with one of five
traceability labels in square brackets:

| Label | Meaning |
|-------|---------|
| `[VERIFIED_FROM_CODE]` | The claim was verified by reading the actual source code (file path and line range cited). |
| `[REPORTED_BY_TESTS]` | The claim was reported by a passing test (test file and test name cited). These are historical results, not newly executed tests. |
| `[APPROVED_DESIGN]` | The claim is recorded in an Approved ADR (ADR number cited). |
| `[PROPOSED_FOR_G07]` | The claim is informational only — a candidate G07 area proposed by GLM, NOT a commitment. |
| `[UNRESOLVED]` | The claim is an open issue, blocker, or limitation. |

**No proposal in this document is silently converted into a completed
capability.** If documentation and implementation disagree, the
discrepancy is recorded explicitly under §I "Tests, Acceptance, and
Production Limitations".

### 0.2 Hard STOP gate

```
G07 IS NOT AUTHORIZED.
G07's scope is NOT defined in this document.
The new ChatGPT conversation must REVIEW and PRODUCE a reviewed G07
implementation plan. GLM must NOT implement G07 until the user
explicitly approves the plan.
```

This mirrors the gating pattern established by ADRs 0007, 0009, 0010,
0011 and the G05/G06 STOP gates. [APPROVED_DESIGN — ADR-0011 §"G03
scope is frozen"]

---

## Section A — Product Identity and Vision

### A.1 Product name and purpose

**Product name**: AgentCraft Synapse (sometimes shortened to "Synapse").

**Tagline** (from `pyproject.toml` description): *"Relationship-aware
knowledge, hypothesis & future intelligence engine."*
[VERIFIED_FROM_CODE — `pyproject.toml` line 6]

**Purpose** (one sentence): Synapse ingests technical sources,
extracts evidence-grounded knowledge units, organizes them as a typed
knowledge graph, and reasons over that graph to answer technical
questions, generate evidence-backed innovation concepts, plan and
record experiments, and construct explainable future-intelligence
scenarios.

**What Synapse is NOT**:

- It is not a search engine. It is a knowledge graph builder + reasoner.
- It is not a forecasting system. Scenarios are categorized
  (F0–F4 horizon classes) but never assigned time predictions.
- It is not an autonomous agent. The user is always in the loop:
  every escalation (hypothesis → experiment, scenario → prototype,
  evaluation → revision) is explicit. There is no background
  scheduler, no swarm, no auto-experiment.
- It is not a forecast oracle. `EpistemicState` has no `VERIFIED`
  value (impossible by design [APPROVED_DESIGN — ADR-0011; G06 plan §7]).

### A.2 Intended users and principal use cases

**Primary intended users**:

1. **Technical researchers and engineers** who want to investigate the
   state of the art in a domain without manually reading every paper.
2. **Innovation / R&D teams** who want evidence-grounded idea
   generation, not brainstorming in a vacuum.
3. **Experiment planners** who want to convert a hypothesis into a
   reproducible plan with success / failure criteria.
4. **Strategists / horizon scanners** who want to ask "given what we
   know today, which future scenarios are testable, which need more
   evidence, and which are speculative?"

[APPROVED_DESIGN — `docs/baseline.md` §"Architectural baseline"; G05
plan §1.1; G06 plan §1.2]

**Principal use cases** (currently supported by backend services):

1. Discover and ingest a technical source (arXiv paper, web page).
2. Investigate a technical claim with evidence-grounded retrieval.
3. Find related tools and capabilities in the knowledge graph.
4. Combine capabilities into a new product idea (innovation generation).
5. Critique an innovation concept (feasibility, evidence coverage,
   novelty, failure modes).
6. Plan an experiment to test a hypothesis (measurable criteria,
   reproducible plan).
7. Execute an experiment, record observations, finalize assessment,
   emit evidence deltas.
8. Construct a future-intelligence scenario (F0–F4 horizon).
9. Evaluate a scenario on 7 axes (evidence coverage, source
   provenance, dependency readiness, contradiction exposure,
   assumption assessment, horizon alignment, overall).
10. Plan a prototype (Mode A: link to a G05 innovation + experiment;
    Mode B: structured proposal without an experiment).
11. Reassess a scenario after new evidence arrives (atomic version
    bump + append-only revision history).

[VERIFIED_FROM_CODE — `src/synapse/api/v1/router.py`; `src/synapse/application/*`]

### A.3 Core product philosophy

The product is built on five non-negotiable principles.
[APPROVED_DESIGN — ADR-0001, ADR-0005, ADR-0006, ADR-0011; G06 plan §7]

1. **Evidence-grounded by default.** Every claim must trace to a
   persisted `EvidenceFragmentRow` with a source URI and excerpt.
   Natural-language similarity alone is NOT proof. Absent evidence
   is NOT contradiction.

2. **Epistemic honesty.** `EpistemicState` is one of
   `supported` / `inferred` / `hypothesized` / `disputed` / `rejected`.
   There is NO `VERIFIED` value — verification is an unreachable
   target by design. An experiment that supports a hypothesis moves
   `HYPOTHESIZED → SUPPORTED`, never to `VERIFIED`.

3. **Deterministic over LLM.** The core knowledge graph, retrieval,
   reasoning, evaluation, and revision logic is deterministic. There
   is no LLM call in the deterministic core (no `import openai`, no
   `import anthropic`). LLM providers are registered but disabled by
   default. `[VERIFIED_FROM_CODE — `src/synapse/api/v1/providers.py`
   _seed_providers()]`

4. **API-first.** Every capability is exposed via `/api/v1/*` with
   the standard `{data, meta, error}` envelope, `application/problem+
   json` errors (RFC 9457), and OpenAPI 3.1 schema at
   `/api/v1/openapi.json`. The UI (when built) consumes these
   contracts directly — no BFF shim, no hidden endpoints.
   `[APPROVED_DESIGN — ADR-0006]`

5. **Explicit over autonomous.** Every material change (hypothesis
   state transition, scenario revision, evidence delta emission)
   requires an explicit API call. No background scheduler, no
   queue worker (jobs table exists but workers are not yet
   implemented as automated services), no autonomous experiment
   execution. `[VERIFIED_FROM_CODE — `src/synapse/application/
   scenario_revision.py`; `src/synapse/workers/__init__.py` (empty
   placeholder)]

### A.4 Vision breakdown

The vision decomposes into five sub-goals, each mapped to a group:

| Sub-goal | Group | Status |
|----------|-------|--------|
| Knowledge discovery and acquisition | G01 (Foundation) + G02 (Acquisition) | Functionally closed with limitations |
| Knowledge extraction and structured storage | G03 (Knowledge, Relationships, Verification) | Functionally closed with limitations |
| Reasoning and innovation generation | G04 (Hybrid Retrieval & Grounded Reasoning) + G05 (Innovation & Experiments) | Functionally closed with limitations |
| Future Intelligence and scenario evaluation | G06 (Future Intelligence) | Functionally closed (T01–T04A all PASS) |
| Relationship between human judgment and automated recommendations | Cross-cutting | Implemented as the "explicit-over-autonomous" principle (A.3.5 above); G07 will expose this to users via review/approve UI affordances |

[APPROVED_DESIGN — ADR-0007 (G01 conditional acceptance), ADR-0010
(G02 conditional closure), ADR-0011 (G03 functional closure + PRB
register); G04/G05/G06 closure reports]

### A.5 Implemented vs. intended future capabilities

| Capability | Status | Source |
|------------|--------|--------|
| Source discovery (arXiv) | IMPLEMENTED (live network call to `export.arxiv.org`) | `[VERIFIED_FROM_CODE — src/synapse/application/acquisition.py:discover_sources]` |
| Source ingestion (HTTP fetch + trafilatura extract + fingerprint) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/acquisition.py:acquire_and_extract]` |
| Deterministic knowledge extraction (regex-based, 7 patterns) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/extraction.py]` |
| Typed knowledge graph (entities, claims, relationships, source spans) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/storage/models.py]` |
| Verification engine (deterministic-v2 policy, no VERIFIED) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/verification.py]` |
| Hybrid retrieval (lexical + structured + graph + evidence rerank) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/retrieval.py]` |
| Capability registry + gap analysis (6-class classification) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/capability_registry.py; gap_analyzer.py]` |
| Evidence-grounded reasoning (6 intents) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/reasoning.py]` |
| Innovation generation (6 templates) + critique | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/innovation.py]` |
| Experiment planning + execution + observation + finalize | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/experiment_planner.py; execution_record.py]` |
| Future signal identification (snapshot-only) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/future_signal.py]` |
| Scenario construction (F0–F4, deterministic) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/scenario_builder.py]` |
| Scenario evaluation (7 dimensions) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py]` |
| Prototype planning (Mode A + Mode B) | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/prototype_planner.py]` |
| Scenario reassessment + append-only revision history | IMPLEMENTED | `[VERIFIED_FROM_CODE — src/synapse/application/scenario_revision.py]` |
| LLM-assisted extraction / reasoning | NOT IMPLEMENTED (placeholder provider, disabled by default) | `[VERIFIED_FROM_CODE — src/synapse/api/v1/providers.py _seed_providers() — openai/anthropic rows with enabled=False]` |
| Background job scheduler / queue workers | NOT IMPLEMENTED (jobs table exists, workers package is empty placeholder) | `[VERIFIED_FROM_CODE — src/synapse/workers/__init__.py is empty]` |
| External provider integrations (GitHub, OpenAlex, Instagram, etc.) | NOT IMPLEMENTED (only arxiv_search + trafilatura + http-fetch are wired) | `[VERIFIED_FROM_CODE — src/synapse/providers/__init__.py; ADR-0010 §1]` |
| Production authentication (IdP / OAuth) | NOT IMPLEMENTED (ProductionAuthAdapter fails closed unless `SYNAPSE_AUTH_PROVIDER` configured) | `[APPROVED_DESIGN — ADR-0003]` |
| UI / frontend | NOT IMPLEMENTED (no `frontend/` directory, no `web/` directory) | `[VERIFIED_FROM_CODE — repo glob `**/frontend*` returns 0 matches]` |

---

## Section B — Architectural Overview

### B.1 Modular monolith

AgentCraft Synapse is a **single-process FastAPI application**. There
are no microservices, no swarm, no message broker, no separate worker
process. The runtime is one `uvicorn` process serving one FastAPI app.
`[APPROVED_DESIGN — ADR-0001]`

The code is organized into **8 packages** under `src/synapse/`:
`[VERIFIED_FROM_CODE — `src/synapse/__init__.py` + filesystem layout]`

| Package | Responsibility |
|---------|----------------|
| `synapse.api` | HTTP layer — FastAPI routers, request/response models, error handlers, middleware, deps. |
| `synapse.domain` | Pure Pydantic v2 domain records (no ORM, no I/O). The frozen contract. |
| `synapse.application` | Application services — the business logic. Orchestrates domain + storage + providers. |
| `synapse.storage` | SQLAlchemy ORM models, DB engine, session factory, fingerprint helpers. |
| `synapse.providers` | External provider adapters (arxiv_search, trafilatura, content_delta_hash). Read-only with respect to the upstream Toolkit. |
| `synapse.security` | Auth adapters, SSRF validator, HTTP transport. |
| `synapse.observability` | Logging. |
| `synapse.workers` | **Empty placeholder** — no background workers are implemented. |
| `synapse.evaluation` | Golden-dataset evaluation runner (G04-T04). |

### B.2 FastAPI application

The app is created by `create_app()` in `src/synapse/main.py`.
`[VERIFIED_FROM_CODE — `src/synapse/main.py`]`

- All API routes mount under `/api/v1` via the aggregator
  `src/synapse/api/v1/router.py`.
- Health routes mount at root (`/health/live`, `/health/ready`).
- OpenAPI 3.1 schema is auto-generated at `/api/v1/openapi.json`.
- CORS fail-closed on `*` + credentials. `[APPROVED_DESIGN — ADR-0006]`
- Standard response envelope: `{data, meta: {request_id, api_version, pagination}, error}`.
- Standard error envelope: `application/problem+json` (RFC 9457).

### B.3 SQLAlchemy and persistence

- **ORM**: SQLAlchemy 2.0 with `Mapped[]` typed columns.
- **Async sessions**: `AsyncSession`, `async_sessionmaker`, async engine.
- **10 ORM tables** (see §G.4 for full schema): `sources`, `jobs`,
  `idempotency_keys`, `audit_events`, `capabilities`, `providers`,
  `evidence_fragments`, `entities`, `claims`, `source_spans`,
  `relationships`, `entity_fingerprints`.
- **Migrations**: Alembic, 5 migration files (`alembic/versions/
  0001_initial.py` through `0005_entity_fingerprints.py`).
- **Idempotent identity**: `EntityFingerprintRow` enforces UNIQUE
  `(kind, fingerprint)` — DB-level protection against duplicate
  inserts. Used by G05 (innovation concepts, experiments), G06
  (scenarios), G05-T06 (executions).
- **Append-only audit log**: `AuditEventRow` (table `audit_events`)
  is the central provenance / belief-change trail. Used by every
  group for different `event_type`s.

### B.4 PostgreSQL and SQLite usage

- **Production target**: PostgreSQL 16+ via `postgresql+psycopg`.
  `[APPROVED_DESIGN — ADR-0002]`
- **Tests**: SQLite in-memory (`sqlite+aiosqlite:///:memory:`).
  Migrations are dialect-agnostic (ANSI SQL types only).
  `[APPROVED_DESIGN — ADR-0002]`
- **PG-only tests**: Tests that exercise PG-specific features (e.g.,
  `pg_advisory_xact_lock`, `SELECT ... FOR UPDATE` row-level locking)
  are skipped without `SYNAPSE_PG_TEST_URL`. Each PG test module
  gets its own isolated database via `tests/integration/conftest.py::pg_engine`.
  `[VERIFIED_FROM_CODE — tests/integration/conftest.py]`
- **PRB-03 resolved**: Database-level concurrency/idempotency was
  the original PRB-03 blocker. It was permanently resolved by the
  `EntityFingerprintRow` UNIQUE constraint + `pg_advisory_xact_lock`
  pattern in G06-T02 and earlier PRB-03 closure work.
  `[REPORTED_BY_TESTS — tests/integration/test_prb_03_*]`

### B.5 Domain records and contracts

The `src/synapse/domain/` package contains 15 modules defining
**pure Pydantic v2** domain records. These are the FROZEN contract:
no domain record may be modified without explicit user approval.
`[APPROVED_DESIGN — ADR-0005, ADR-0011]`

Each domain record:
- Inherits from `DomainRecord` (id, created_at, updated_at, version).
- Uses `extra="forbid"` (reject unknown fields).
- Uses string-backed enums (`StrEnum`).
- Enforces invariants via `@model_validator(mode="after")`.

Key records: `Source`, `Acquisition`, `EvidenceFragment`, `Entity`,
`Claim`, `Relationship` (+ 16-predicate vocabulary), `Capability`,
`Hypothesis`, `Experiment`, `EvidenceDelta`, `Scenario` (F0–F4 horizon),
`Job`, `ExtractionResult`.

`[VERIFIED_FROM_CODE — src/synapse/domain/*.py]`

### B.6 Application services

The `src/synapse/application/` package contains 17 service modules
(~16,217 LOC). Each service is async, takes an `AsyncSession` as
the first argument, and is the primary business-logic unit. Services
are NOT themselves HTTP handlers — they're called by the API layer.

See §D for the full capability inventory per service.

### B.7 API layer

The `src/synapse/api/v1/` package contains 14 router modules.
Every route:
- Depends on `PrincipalDep` (auth — see §B.8).
- Depends on `DbSessionDep` (async session).
- Depends on `RequestIDDep` (per-request tracing).
- Returns `Envelope.success(...)` or raises a `DomainError` subclass
  (`NotFoundError` → 404, `ValidationError` → 422, etc.).
- Uses Pydantic v2 request models for validation.

Health routes (`/health/live`, `/health/ready`) are the only routes
without `PrincipalDep`.

`[VERIFIED_FROM_CODE — src/synapse/api/v1/router.py + individual router files]`

### B.8 Authentication

- **`AuthAdapter` protocol** with two implementations:
  - `DevelopmentAuthAdapter`: env-based API keys
    (`SYNAPSE_ADMIN_API_KEYS`, `SYNAPSE_DEV_API_KEYS`). Selected
    when `SYNAPSE_AUTH_MODE=development`.
  - `ProductionAuthAdapter`: **always fails closed** unless
    `SYNAPSE_AUTH_PROVIDER` is configured. `[APPROVED_DESIGN — ADR-0003]`
- **Scopes** (hierarchy: `ADMIN ⊇ OPERATOR ⊇ EDITOR ⊇ RESEARCHER ⊇ READER`).
  Most routes accept any authenticated principal (no scope-tier
  restriction). The `/system/activity-mode PUT` route is the
  exception (returns 501).
- **Test fixtures**: `test-key-admin` (ADMIN scope) and
  `test-key-reader` (RESEARCHER scope) per `tests/conftest.py`.
- **No production IdP**: ADR-0007 D-02 deferred production IdP
  selection. This remains a PRB-blocker (see §I).

### B.9 Audit and provenance

The `audit_events` table is the **single source of truth for
provenance and belief-change history**. Every material action emits
an `AuditEventRow`:

| `event_type` | Emitted by | Purpose |
|--------------|-----------|---------|
| `acquisition.started`, `acquisition.completed` | `acquisition.acquire_and_extract` | Source fetch + extract audit. |
| `extraction.*` | `canonicalization.persist_extraction_results` | Knowledge extraction audit. |
| `relationship.created` | `relationship_service.create_relationship` | Edge creation audit. |
| `verification.assessed` | `verification.assess_claim` | Verification outcome (the ONLY trail `_is_fragment_verified` consults). |
| `evidence.delta` | `execution_record.finalize_execution` | Hypothesis epistemic-state transition (HYPOTHESIZED → SUPPORTED / DISPUTED). |
| `innovation.generated`, `innovation.critiqued` | `innovation.generate_innovations`, `critique_innovation` | Innovation lifecycle. |
| `experiment.planned` | `experiment_planner.plan_experiment` | Experiment plan creation. |
| `execution.created`, `execution.observed`, `execution.finalized` | `execution_record.*` | Execution lifecycle. |
| `scenario.created`, `scenario.evaluated`, `scenario.prototype_linked`, `scenario.prototype_proposed` | `scenario_builder`, `scenario_evaluator`, `prototype_planner` | Scenario lifecycle. |
| `scenario.revision` | `scenario_revision.reassess_scenario` | Append-only revision history (G06-T04/T04A). |

`[VERIFIED_FROM_CODE — each application service's `_record_*_audit` function]`

### B.10 Background or external integrations

- **No background scheduler / queue worker**. The `synapse.workers`
  package is an empty placeholder. Jobs are tracked in the `jobs`
  table but no automated worker consumes them.
  `[VERIFIED_FROM_CODE — src/synapse/workers/__init__.py is empty]`
- **External providers** (3 wired):
  - `arxiv_search_provider` (vendored from AgentCraft-Toolkit
    commit `fd9df34` — `[VERIFIED_FROM_CODE — NOTICE.md]`). Live
    network calls to `export.arxiv.org`.
  - `trafilatura_extractor_provider` (a thin wrapper around the
    pip-installed `trafilatura` package).
  - `content_delta_hash` (vendored from the Toolkit).
- **No LLM provider** wired (openai / anthropic rows exist in the
  static provider catalog with `enabled=False`).
- **No graph database, no vector database, no message broker**.
  `[APPROVED_DESIGN — ADR-0001]`

### B.11 Architecture diagram (Mermaid)

```mermaid
flowchart TB
    subgraph CLIENT[Client Layer]
        EXT[External Client<br/>example/client_g04.py]
        UI[G07 UI<br/>NOT IMPLEMENTED]
    end

    subgraph API[API Layer — synapse.api]
        HEALTH[health.router<br/>/health/*]
        V1[router.py aggregator<br/>/api/v1/*]
        SOURCES[sources.router]
        KNOWLEDGE[knowledge.router<br/>+ entities/relationships/claims sub-routers]
        RETRIEVAL[retrieval.router]
        CAPREG[capability_registry.router]
        REASONING[reasoning.router]
        INNOV[innovations.router]
        EXPERIMENTS[experiments.router]
        EXECUTIONS[executions.router]
        HYPOTHESES[hypotheses.router]
        FUTURE[future.router]
        CAPS_STATIC[capabilities.router<br/>static catalog]
        PROVIDERS[providers.router<br/>static catalog]
        SYSTEM[system.router<br/>+ jobs_router]
    end

    subgraph APP[Application Layer — synapse.application]
        ACQ[acquisition.py]
        EXTRACT[extraction.py]
        CANON[canonicalization.py]
        RELSVC[relationship_service.py]
        VERIFY[verification.py]
        RETRIEVALSVC[retrieval.py]
        CAPREGSVC[capability_registry.py<br/>+ gap_analyzer.py]
        REASONSVC[reasoning.py]
        INNOVSVC[innovation.py]
        EXPLANSVC[experiment_planner.py]
        EXECRECSVC[execution_record.py]
        FUTSIG[future_signal.py]
        SCNBUILD[scenario_builder.py]
        SCNEVAL[scenario_evaluator.py]
        PROTOPLANNER[prototype_planner.py]
        SCNREV[scenario_revision.py]
    end

    subgraph DOMAIN[Domain Layer — synapse.domain<br/>FROZEN Pydantic v2 contracts]
        REC[15 domain records<br/>+ 16-predicate vocabulary]
    end

    subgraph STORE[Storage Layer — synapse.storage]
        ORM[10 ORM tables<br/>+ Alembic migrations]
        FP[fingerprint.py<br/>claim_fingerprint]
    end

    subgraph PROV[Providers — synapse.providers]
        ARXIV[arxiv_search_provider]
        TRAFA[trafilatura_extractor_provider]
        CDH[content_delta_hash]
    end

    subgraph SEC[Security — synapse.security]
        AUTH[AuthAdapter<br/>Dev / Production]
        SSRF[SSRF validator]
        HTTPT[HTTP transport<br/>IP-pinned]
    end

    subgraph DB[(Database)]
        PG[(PostgreSQL 16+<br/>production)]
        SQLITE[(SQLite in-memory<br/>tests)]
    end

    EXT --> V1
    UI -.-> V1

    HEALTH --> DB
    V1 --> SOURCES & KNOWLEDGE & RETRIEVAL & CAPREG & REASONING & INNOV & EXPERIMENTS & EXECUTIONS & HYPOTHESES & FUTURE & CAPS_STATIC & PROVIDERS & SYSTEM

    SOURCES --> ACQ
    KNOWLEDGE --> RELSVC
    RETRIEVAL --> RETRIEVALSVC
    CAPREG --> CAPREGSVC
    REASONING --> REASONSVC
    INNOV --> INNOVSVC
    EXPERIMENTS --> EXPLANSVC
    EXECUTIONS --> EXECRECSVC
    HYPOTHESES --> EXECRECSVC
    FUTURE --> FUTSIG & SCNBUILD & SCNEVAL & PROTOPLANNER & SCNREV

    ACQ --> EXTRACT --> CANON
    ACQ --> PROV
    CANON --> RELSVC
    RELSVC --> VERIFY

    APP --> DOMAIN
    APP --> STORE
    STORE --> DB
    ACQ --> SEC
```

`[VERIFIED_FROM_CODE — src/synapse/api/v1/router.py, src/synapse/application/__init__.py, src/synapse/storage/models.py]`

---

## Section C — G01–G06 Implementation History

This section documents each group's objective, completed tasks,
implemented modules, capabilities, APIs, tests, corrections,
limitations, and acceptance status. The repository reports
(`reports/*.md`) are the authoritative source.

### C.1 G01 — Foundation

**Original objective**: Build the modular-monolith skeleton with
domain contracts, API/security baseline, and a quality gate.

**Completed tasks** (5):
- G01-T01: Repository baseline reconnaissance.
- G01-T02: Pydantic v2 domain records (15 records, `extra="forbid"`).
- G01-T03: Modular-monolith skeleton (8 packages, FastAPI app,
  SQLAlchemy 2.0 ORM, Alembic migrations 0001).
- G01-T04: API + security baseline (`/api/v1`, `PrincipalDep`,
  `AuthAdapter`, SSRF denylist, problem+json errors).
- G01-T05: Quality gate (140 tests, 90% coverage).

**Implemented modules**: All of `src/synapse/` skeleton, ADRs
0001–0006, 5 migrations (0001 only at this stage), 140 passing tests.

**Major capabilities**: FastAPI app with health endpoints, static
capability + provider catalogs, jobs table contract (404 stub),
idempotency-key table.

**Relevant APIs**: `/health/*`, `/api/v1/capabilities`, `/api/v1/providers`,
`/api/v1/system/activity-mode` (GET only; PUT returns 501),
`/api/v1/jobs/{id}` (404 stub), `/api/v1/jobs/{id}/cancel` (501).

**Tests and acceptance evidence**: 140 tests passing, 90% coverage,
0 failures, 0 skipped. `[REPORTED_BY_TESTS — reports/GROUP_01_CLOSURE.md]`

**Corrections and important decisions**:
- ADR-0007: G01 conditionally accepted. Three user decisions: D-01
  toolkit audit not waived, D-02 production IdP selection deferred,
  D-03 licensing deferred (no `LICENSE` file).

**Remaining limitations** (carried forward):
- No `LICENSE` file (proprietary, internal use only).
- No production IdP.
- No `TOOLKIT_INDEX(1).json` (audit deferred to G02).

**Final acceptance status**: **CONDITIONALLY ACCEPTED**.
`[APPROVED_DESIGN — ADR-0007]`

### C.2 G02 — Acquisition

**Original objective**: Wire the first vertical slice (arxiv_search +
trafilatura + content_delta_hash) for source discovery + ingestion,
with full SSRF protection and provenance preservation.

**Completed tasks** (6):
- G02-T01: Audit preparation (templates, schemas, scripts, tests).
- G02-T02: Direct Toolkit Audit (amended per ADR-0009 to
  "CONDITIONALLY ACCEPTED AS INVENTORY AUDIT").
- G02-T03: Minimal vertical slice (arxiv_search + trafilatura +
  content_delta_hash).
- G02-T04: Security hardening (DNS-rebinding TOCTOU fix via
  IP-pinned transport, SSRF redirect defense).
- G02-T05: PostgreSQL migration validation (BLOCKED → later
  resolved in PRB-03 permanent closure).
- G02-T06: G02 final qualification.

**Implemented modules**:
- `src/synapse/providers/arxiv_search_provider.py` (vendored from
  Toolkit commit `fd9df34`).
- `src/synapse/providers/trafilatura_extractor_provider.py` (thin
  wrapper around pip `trafilatura`).
- `src/synapse/providers/content_delta_hash.py` (vendored from Toolkit).
- `src/synapse/security/http_transport.py` (IP-pinned HTTP transport).
- `src/synapse/application/acquisition.py` (discover_sources,
  acquire_and_extract).
- `src/synapse/api/v1/sources.py` (5 routes).

**Major capabilities**: Source discovery (arXiv), source ingestion
(HTTP fetch + trafilatura extract + content-delta fingerprint),
acquisition audit trail.

**Relevant APIs**: `POST /api/v1/sources/discover`, `POST /api/v1/sources/ingest`,
`GET /api/v1/sources`, `GET /api/v1/sources/{id}`, `GET /api/v1/sources/{id}/acquisitions`.

**Tests and acceptance evidence**: All six requirements PASS,
SSRF redirect-defense gap found/repaired/verified.
`[REPORTED_BY_TESTS — reports/GROUP_02_FINAL_QUALIFICATION.md]`

**Corrections and important decisions**:
- ADR-0008: Audit Policy Amendment (5 clarifications: tiered
  verification flags, real functional smoke tests, license
  flag-not-reject, provenance-preservation sub-object, read-only audit).
- ADR-0009: Audit Review Clarifications (8 clarifications including
  freeze audit evidence at Toolkit commit `fd9df34`, separate
  inventory vs. functional coverage, correct credential statement).
- ADR-0010: G02 Conditional Closure (freeze 3 providers, PostgreSQL
  migration validation unresolved blocker, 4 IP-pinned transport
  security items verified, G02 NOT production-certified).

**Remaining limitations** (carried forward):
- Real PostgreSQL migration validation was BLOCKED at sandbox time
  (later resolved by PRB-03 permanent closure).
- External providers limited to arxiv + trafilatura + http-fetch.
- G02 NOT production-certified.

**Final acceptance status**: **CONDITIONALLY CLOSED**.
`[APPROVED_DESIGN — ADR-0010]`

### C.3 G03 — Knowledge, Relationships, Verification

**Original objective**: Transform G02 evidence fragments into typed
knowledge units, evidence-backed relationships, and a deterministic
verification engine — without new infrastructure (no graph DB, no
broker).

**Completed tasks** (5):
- G03-T01: Typed knowledge extraction (deterministic regex, 7
  patterns, `ExtractionResult` contract, migration 0003).
- G03-T02: Canonicalization + idempotent persistence (reliability
  closure replaced `source_spans`-based completion with `JobRow.status`).
- G03-T03: RelationshipService (typed directed edges, 11 functions,
  migration 0004, `RelationshipRow`).
- G03-T04: Verification engine (`assess_claim`, independence,
  contradiction, staleness, `EvidenceDelta`).
- G03-T05: Final integration + Knowledge API endpoints.

**Implemented modules**: `extraction.py`, `canonicalization.py`,
`relationship_service.py`, `verification.py`, `api/v1/knowledge.py`,
migration 0003 + 0004.

**Major capabilities**: Deterministic knowledge extraction,
canonicalization (arXiv ID, DOI, GitHub repo, URL), idempotent entity/
claim/source_span persistence, typed relationships (16-predicate
vocabulary), verification engine (deterministic-v2 policy, never
auto-promotes to VERIFIED), Knowledge API (entities, relationships,
claims, evidence, search).

**Relevant APIs**: `GET /api/v1/entities`, `GET /api/v1/entities/{id}`,
`GET /api/v1/relationships`, `GET /api/v1/relationships/{id}`,
`GET /api/v1/claims/{id}/evidence`, `POST /api/v1/knowledge/search`.

**Tests and acceptance evidence**: All five tasks PASS,
T01–T04 + T05 PASS WITH LIMITATIONS.
`[REPORTED_BY_TESTS — reports/G03_T05_FINAL_INTEGRATION_REPORT.md]`

**Corrections and important decisions**:
- G03-T04 VERIFICATION_POLICY_CORRECTION: Replaced unsafe
  `deterministic-v1` (auto-promote on 2 distinct URLs) with
  `deterministic-v2` decision table. VERIFIED is unreachable by
  design.
- ADR-0011: G03 Functional Closure. Established the
  **production-readiness blocker register (PRB-01..07)** that
  carries forward to all later groups.

**Remaining limitations** (PRB register, see §I):
- PRB-01: Real PostgreSQL migration validation (HIGH).
- PRB-02: Pinned-IP HTTPS, proxy, IPv6, connection-pooling review (MEDIUM).
- PRB-03: Database-level concurrency/idempotency guarantees (MEDIUM)
  — **resolved in PRB-03 permanent closure** (EntityFingerprintRow
  UNIQUE + advisory locks).
- PRB-04: Evidence-origin independence limitations (MEDIUM).
- PRB-05: Stronger VERIFIED review policy (EXPECTED — VERIFIED
  unreachable by design).
- PRB-06: Toolkit read-only credential enforcement (MEDIUM).
- PRB-07: Extractor-version-aware reprocessing (LOW).

**Final acceptance status**: **FUNCTIONALLY CLOSED WITH LIMITATIONS**.
Production readiness NOT approved. `[APPROVED_DESIGN — ADR-0011]`

### C.4 G04 — Hybrid Retrieval & Grounded Reasoning

**Original objective**: Build a compact evidence-grounded retrieval
+ reasoning layer over the G01–G03 knowledge graph — no vector DB,
no graph DB, no LLM.

**Completed tasks** (5):
- G04-T01: Hybrid retrieval (lexical + structured + graph-assisted +
  evidence-aware reranking).
- G04-T02: Capability registry + gap analysis (6 classifications).
- G04-T03: Evidence-grounded technical reasoning (6 intents,
  DOCUMENTED_FACT/DERIVED_FINDING/HYPOTHESIS/UNKNOWN).
- G04-T04: Evaluation system + cost-aware routing (3 paths A/B/C).
- G04-T05: External-client compatibility + OpenAPI/contract validation.

**Implemented modules**: `retrieval.py`, `capability_registry.py`,
`gap_analyzer.py`, `reasoning.py`, `evaluation/` (golden dataset
runner), `api/v1/retrieval.py`, `api/v1/capability_registry.py`,
`api/v1/reasoning.py`, `examples/client_g04.py`.

**Major capabilities**: Hybrid retrieval with ranking breakdowns,
capability registry with provider/dependency/limitation metadata,
gap analysis (SUPPORTED / PARTIALLY_SUPPORTED / NOT_EVIDENCED /
CONTESTED / CONSTRAINED / UNKNOWN), evidence-grounded reasoning
(6 intents), golden-dataset evaluation.

**Relevant APIs**: `POST /api/v1/knowledge/retrieve`,
`GET /api/v1/knowledge/capabilities`, `GET /api/v1/knowledge/capabilities/{id}`,
`POST /api/v1/knowledge/capabilities/analyze-gap`,
`POST /api/v1/reasoning/queries`, `GET /api/v1/reasoning/intents`.

**Tests and acceptance evidence**: All 5 tasks PASS WITH LIMITATIONS.
G04-T04C independent audit identified 3 defects (D1/D2/D3) and
minimally corrected them.

**Corrections and important decisions**:
- G04-T02C: Provider Attribution Closure — a capability must never
  be classified SUPPORTED for a candidate solely because evidence
  establishes it for another provider.
- G04-T03C: Context Contradiction Closure — was checking ALL
  attributed claims instead of context-matching `applicable_claims`.
- G04-T04C: Evaluation Integrity Review — 3 concrete defects
  minimally corrected.

**Remaining limitations**:
- All tasks PASS WITH LIMITATIONS (limitations documented per task).
- Carries forward the PRB-01..07 register.

**Final acceptance status**: **FUNCTIONALLY CLOSED WITH LIMITATIONS**.
`[REPORTED_BY_TESTS — reports/G04_T05_EXTERNAL_CLIENT_COMPATIBILITY.md]`

### C.5 G05 — Innovation & Experiments

**Original objective**: Transform evidence-backed knowledge into
plausible application concepts and testable experiments
(planning-only, revised at G05-P00C).

**Completed tasks** (7):
- G05-T01: Knowledge combination (evidence-grounded candidate
  discovery).
- G05-T02: Opportunity discovery (composing G04 `analyze_gap` with
  T01 combinations).
- G05-T03: Innovation generation (6 deterministic templates,
  `POST /api/v1/innovations/generate`).
- G05-T03C: Identity idempotency closure (deterministic concept
  fingerprint — fixed random UUID duplication).
- G05-T04: Architecture composition + critique (`POST /api/v1/innovations/{id}/critique`).
- G05-T05: Experiment planning (measurable, reproducible plans,
  `POST /api/v1/experiments`).
- G05-T05C: Validation closure (experiment fingerprint, calibration_status).
- G05-T06: Execution records + evidence feedback (run_id, EvidenceDelta).
- G05-T07: End-to-end innovation-lifecycle acceptance.

**Implemented modules**: `innovation.py` (3,188 LOC — T01/T02/T03/T04),
`experiment_planner.py`, `execution_record.py`, `api/v1/innovations.py`,
`api/v1/experiments.py`, `api/v1/executions.py`, `api/v1/hypotheses.py`,
migration 0005 (EntityFingerprintRow for PRB-03 permanent closure).

**Major capabilities**: Knowledge combination, opportunity discovery,
innovation generation (6 templates), architecture critique,
experiment planning with measurable criteria, execution records
(idempotent via run_id), observation recording, finalize assessment,
evidence delta emission (HYPOTHESIZED → SUPPORTED / DISPUTED,
never VERIFIED).

**Relevant APIs**: `POST /api/v1/innovations/generate`,
`POST /api/v1/innovations/{id}/critique`, `POST /api/v1/experiments`,
`GET /api/v1/experiments/{id}`, `POST /api/v1/experiments/{id}/execute`,
`POST /api/v1/executions/{id}/observations`,
`POST /api/v1/executions/{id}/finalize`, `GET /api/v1/executions/{id}`,
`GET /api/v1/hypotheses/{id}/evidence-deltas`.

**Tests and acceptance evidence**: All tasks PASS,
G05-T07 end-to-end lifecycle test closes G05.
`[REPORTED_BY_TESTS — reports/G05_T07_END_TO_END_ACCEPTANCE.md]`

**Corrections and important decisions**:
- G05-T01: epistemic-isolation defect (`find_related_entities` not
  filtering `origin='hypothesized'`) corrected.
- G05-T02: second epistemic-isolation correction applied.
- G05-T03C: deterministic concept fingerprint replaced random UUIDs.
- G05-T05C: experiment fingerprint correctness, measurable-criteria
  integrity (calibration_status), identity fixes.
- **G05 plan §5.3 "Stability contract for G07"**: lists stable
  field names G07 may rely on (`data.concepts[].components[].entity_id`,
  `evidence_refs[]`, `feasibility_score`, `overall_recommendation`,
  `evidence_deltas[]`). Field additions forward-compatible; removals
  require `/api/v2`. `[PROPOSED_FOR_G07 — reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md §5.3]`

**Remaining limitations**:
- Innovation generation is deterministic (no LLM brainstorming).
- Experiment execution is synchronous (no background jobs).
- Production-mode experiments require `safety_limits.approved_by`;
  networked-mode experiments require `cost_limits.approved_by` —
  but no UI exists to set these. `[VERIFIED_FROM_CODE — src/synapse/domain/experiment.py]`

**Final acceptance status**: **FUNCTIONALLY CLOSED** (closes G05).
`[REPORTED_BY_TESTS — reports/G05_T07_END_TO_END_ACCEPTANCE.md]`

### C.6 G06 — Future Intelligence

**Original objective**: Build future-intelligence capabilities —
signal identification, scenario construction (F0–F4 horizon),
scenario evaluation, prototype planning, and atomic scenario
reassessment — without inventing evidence, rewriting history, or
introducing autonomous background execution.

**Completed tasks** (4 + 4 corrections):
- G06-P00 + G06-P00C: Architecture planning + corrections.
- G06-T01: Future signal identification (snapshot-only, 5 signal
  kinds, deterministic IDs, conservative source independence).
- G06-T01A: Signal identity + source independence corrections.
- G06-T02: Scenario construction + API (F0–F4 derivation, F1
  fail-closed, fingerprint idempotency, `pg_advisory_xact_lock`).
- G06-T02A: F4 dependency integrity + concurrent idempotency.
- G06-T03: Scenario evaluation (7 dimensions) + prototype planning
  (Mode A delegates to `plan_experiment`, Mode B returns proposal).
- G06-T03A: Assumption evidence integrity + explicit hypothesis
  selection.
- G06-T04: Scenario revision + end-to-end acceptance (atomic
  revision integrity, append-only history, full lifecycle test).
- G06-T04A: Atomic reassessment correction + evaluation snapshot
  safety + 9 deterministic PG concurrency tests.

**Implemented modules**: `future_signal.py` (833 LOC),
`scenario_builder.py` (1,081 LOC), `scenario_evaluator.py` (795 LOC),
`prototype_planner.py` (384 LOC), `scenario_revision.py` (569 LOC),
`api/v1/future.py` (7 routes).

**Major capabilities**: Future signal identification (5 snapshot
signal kinds), scenario construction (F0–F4 deterministic horizon
classification), scenario evaluation (7 dimensions: evidence coverage,
source provenance, dependency readiness, contradiction exposure,
assumption assessment, horizon alignment, overall), prototype
planning (Mode A linked experiment + Mode B proposal), scenario
reassessment (atomic version bump + append-only `scenario.revision`
audit row), revision history (newest first, queryable via
`GET /scenarios/{id}/revisions`).

**Relevant APIs**: `POST /api/v1/future/signals`,
`POST /api/v1/future/scenarios`, `GET /api/v1/future/scenarios/{id}`,
`POST /api/v1/future/scenarios/{id}/evaluate`,
`POST /api/v1/future/scenarios/{id}/prototype-plan`,
`POST /api/v1/future/scenarios/{id}/reassess`,
`GET /api/v1/future/scenarios/{id}/revisions`.

**Tests and acceptance evidence**: All 4 tasks + 4 corrections PASS.
37 T04+T04A tests (27 SQLite + 10 PG-only).
`[REPORTED_BY_TESTS — reports/G06_T04_REVISION_E2E_ACCEPTANCE.md,
reports/G06_FINAL_CLOSURE_REPORT.md]`

**Corrections and important decisions** (per the user's task brief,
particular attention to G06 corrections):

#### G06-T01A — Signal integrity
- Signal identity is deterministic: `signal_id = "sig-" + SHA-256(kind + capability_id)[:16]`.
- `content_version = "cv-" + SHA-256(all material content fields)[:16]`.
- Conservative source independence per PRB-04 (always returns 1 for
  ≥1 fragment, 0 for zero fragments — never invents independence).
- No `gap_transition` or `dependency_shift` signal kinds exist
  (these would imply forecasting, which is out of scope).
  `[VERIFIED_FROM_CODE — src/synapse/application/future_signal.py]`

#### G06-T02A — Scenario dependency integrity and concurrent idempotency
- F4 scenarios require a critique (frozen validator).
- F1 fail-closed: if no honest integration dependency can be named,
  the builder raises `ValueError` rather than fabricating one.
- Scenario fingerprint includes `run_id` for idempotent retry;
  same `run_id` + same payload → same `scenario_id` (idempotent
  reuse); same `run_id` + conflicting payload → `ValueError`
  (explicit rejection).
- `pg_advisory_xact_lock(run_id_hash)` serializes concurrent
  `build_scenario` calls with the same `run_id`.
  `[VERIFIED_FROM_CODE — src/synapse/application/scenario_builder.py:_acquire_run_id_lock]`

#### G06-T03A — Assumption evidence integrity and hypothesis selection
- Assumption classification uses **explicit** assumption-to-claim
  associations stored in `scenario.risks["assumption_associations"]`.
- No shared-fragment-only inference, no substring matching.
- Per-assumption classification: `supported` / `contested` / `not_evidenced` / `unknown`.
- Mode A prototype planning: explicit hypothesis selection — if
  multiple hypotheses exist for a concept, the caller must select
  one; if exactly one exists, auto-selected.
  `[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py:_assess_assumptions,
  src/synapse/application/prototype_planner.py:_select_hypothesis]`

#### G06-T04A — Atomic revision integrity
- `reassess_scenario()` acquires `SELECT ... FOR UPDATE` row lock
  BEFORE reading the authoritative baseline.
- The new evaluation is computed before the lock (expensive,
  read-only) but the material-change comparison is revalidated
  against the LOCKED baseline.
- If a concurrent reassessment committed first, the second returns
  `revised=False` without writing an audit row.
- Version bump + snapshot persistence + audit-row append happen
  atomically in the same transaction — no partial state on rollback.
- `persist_evaluation_snapshot()` (called by `/evaluate`) acquires
  the same lock and skips the write if `last_revised_at` is set
  (a `/reassess` has established an authoritative baseline). A
  stale `/evaluate` result never clobbers a newer revision.
  `[VERIFIED_FROM_CODE — src/synapse/application/scenario_revision.py]`

**Remaining limitations**:
- The full evaluation snapshot is persisted in `attributes.last_evaluation`
  JSON (additive), not in a dedicated audit-row field. A future
  group could promote this to a first-class audit event type.
  `[PROPOSED_FOR_G07 — reports/G06_FINAL_CLOSURE_REPORT.md §12]`
- Reassessment relies on `verification.assessed` audit rows, NOT
  `evidence.delta`. An experiment lifecycle alone (without
  `assess_claim(hypothesis)`) does NOT change `evidence_coverage`.
- `MATERIAL_FIELDS` excludes `uncertainty` (it's a pure function
  of `evidence_coverage` per Correction C: `uncertainty = 1.0 − evidence_coverage`).
- `generate_innovations` is non-deterministic in WHICH combination
  of components it picks first. T04's e2e fixture handles this by
  iterating concepts.
- No background reassessment scheduler (per G06 plan §4.5: "A future
  group, with explicit authorization, may add a worker that calls
  the same reassess endpoint on a schedule").
  `[PROPOSED_FOR_G07 — reports/G06_FINAL_CLOSURE_REPORT.md §12]`

**Final acceptance status**: **FUNCTIONALLY COMPLETE**.
`[REPORTED_BY_TESTS — reports/G06_FINAL_CLOSURE_REPORT.md]`

---

## Section D — Current Functional Capabilities

This section is a practical capability inventory. For each capability,
it records the service, API endpoint, inputs, outputs, persistence
behavior, evidence/provenance behavior, auth requirements, and
implementation status.

### D.1 Source acquisition

| Capability | Discover candidate sources |
|---|---|
| Service | `synapse.application.acquisition.discover_sources` |
| API | `POST /api/v1/sources/discover` |
| Inputs | `query` (str), `max_results` (int, default 5), `author` (str\|None), `category` (str\|None) |
| Outputs | `{sources: [{arxiv_id, title, authors, abstract, ...}]}` |
| Persistence | None (read-only against `export.arxiv.org`) |
| Evidence/provenance | Live network call to arXiv; no DB write |
| Auth | `PrincipalDep` (any authenticated scope) |
| Status | IMPLEMENTED |

| Capability | Ingest a source (fetch + extract + persist) |
|---|---|
| Service | `synapse.application.acquisition.acquire_and_extract` |
| API | `POST /api/v1/sources/ingest` |
| Inputs | `canonical_uri`, `source_type`, `provider_name`, `source_metadata`, `requester` |
| Outputs | `{source, evidence_fragment, audit_events}` |
| Persistence | Creates `SourceRow`, `EvidenceFragmentRow`, `AuditEventRow` (event_type=`acquisition.*`) |
| Evidence/provenance | SSRF-validated fetch → trafilatura extract → content-delta fingerprint → persist. Provenance preserved (publisher/author/license/retrieved_at/toolkit_commit_sha). |
| Auth | `PrincipalDep` |
| Status | IMPLEMENTED (synchronous; future group may convert to 202+job) |

| Capability | List sources |
|---|---|
| Service | inline query in `api/v1/sources.py` |
| API | `GET /api/v1/sources` |
| Inputs | none (max 50, most recent first) |
| Outputs | `{sources: [SourceRow...]}` |
| Persistence | Read-only |
| Auth | `PrincipalDep` |
| Status | IMPLEMENTED |

| Capability | Get a single source |
|---|---|
| API | `GET /api/v1/sources/{source_id}` |
| Status | IMPLEMENTED |

| Capability | List acquisitions + evidence fragments for a source |
|---|---|
| API | `GET /api/v1/sources/{source_id}/acquisitions` |
| Persistence | Reads `audit_events` WHERE `event_type='acquisition.*'` + `EvidenceFragmentRow` |
| Status | IMPLEMENTED |

### D.2 Extraction and knowledge organization

| Capability | Deterministic knowledge extraction (regex) |
|---|---|
| Service | `synapse.application.extraction.extract` |
| API | Indirect — called via `acquire_and_extract` |
| Inputs | `evidence_fragment_id`, `text` |
| Outputs | `ExtractionResult` (7 pattern types: arXiv IDs, DOIs, GitHub repos, URLs, introduces/proposes, requires/needs, outperforms/improves) |
| Persistence | None (pure function) — persistence is in `canonicalization.persist_extraction_results` |
| Evidence/provenance | Deterministic; no LLM. Never produces `verified`. |
| Status | IMPLEMENTED |

| Capability | Canonicalization + idempotent persistence |
|---|---|
| Service | `synapse.application.canonicalization.find_or_create_entity`, `find_or_create_claim`, `create_source_span`, `persist_extraction_results` |
| API | Indirect — called by `acquisition` pipeline |
| Persistence | Idempotent creates `EntityRow`, `ClaimRow`, `SourceSpanRow`. Completion check via `JobRow.status`. |
| Status | IMPLEMENTED |

### D.3 Claims and evidence

| Capability | List entities (paginated, filter by kind) |
|---|---|
| API | `GET /api/v1/entities` |
| Status | IMPLEMENTED |

| Capability | Get entity with claims, relationships, source spans |
|---|---|
| API | `GET /api/v1/entities/{entity_id}` |
| Status | IMPLEMENTED |

| Capability | List relationships (filterable) |
|---|---|
| Service | `synapse.application.relationship_service.find_relationships` |
| API | `GET /api/v1/relationships` |
| Status | IMPLEMENTED |

| Capability | Get relationship evidence bundle |
|---|---|
| Service | `synapse.application.relationship_service.get_relationship_evidence` |
| API | `GET /api/v1/relationships/{relationship_id}` |
| Status | IMPLEMENTED |

| Capability | Get evidence for a claim (fragments + source spans + verification assessment) |
|---|---|
| API | `GET /api/v1/claims/{claim_id}/evidence` |
| Status | IMPLEMENTED |

| Capability | Search entities by name/alias (ILIKE substring) |
|---|---|
| API | `POST /api/v1/knowledge/search` |
| Status | IMPLEMENTED |

### D.4 Capability registry (G04-T02)

| Capability | List documented capabilities |
|---|---|
| Service | `synapse.application.capability_registry.list_capabilities` |
| API | `GET /api/v1/knowledge/capabilities` |
| Outputs | `CapabilitySummary[]` (id, name, provider_count, has_limitations flag) |
| Status | IMPLEMENTED |

| Capability | Get capability detail (providers, deps, limitations, claims, evidence) |
|---|---|
| Service | `synapse.application.capability_registry.get_capability` |
| API | `GET /api/v1/knowledge/capabilities/{capability_id}` |
| Status | IMPLEMENTED |

| Capability | Analyze capability gap (6-class classification) |
|---|---|
| Service | `synapse.application.gap_analyzer.analyze_gap` |
| API | `POST /api/v1/knowledge/capabilities/analyze-gap` |
| Inputs | `required_capabilities: [str]`, `context`, `candidate_entity_ids` |
| Outputs | `GapAnalysisResult` (each required capability classified as `SUPPORTED` / `PARTIALLY_SUPPORTED` / `NOT_EVIDENCED` / `CONTESTED` / `CONSTRAINED` / `UNKNOWN`) |
| Status | IMPLEMENTED |

### D.5 Retrieval and reasoning

| Capability | Hybrid retrieval (lexical + structured + graph + evidence rerank) |
|---|---|
| Service | `synapse.application.retrieval.hybrid_retrieve` |
| API | `POST /api/v1/knowledge/retrieve` |
| Inputs | `query`, `entity_kind`, `predicate`, `epistemic_state`, `max_depth`, `limit` |
| Outputs | `{claims, relationships, entities, evidence_bundles, ranking_breakdowns}` |
| Status | IMPLEMENTED (PASS WITH LIMITATIONS) |

| Capability | Evidence-grounded reasoning (6 intents) |
|---|---|
| Service | `synapse.application.reasoning.answer_query` |
| API | `POST /api/v1/reasoning/queries` |
| Inputs | `query`, `candidate_entity_ids`, `context`, `limit` |
| Outputs | `{intent, findings: [{type: DOCUMENTED_FACT|DERIVED_FINDING|HYPOTHESIS|UNKNOWN, ...}], ...}` |
| Status | IMPLEMENTED (PASS WITH LIMITATIONS) |

| Capability | List reasoning intents (informational) |
|---|---|
| API | `GET /api/v1/reasoning/intents` |
| Status | IMPLEMENTED |

### D.6 Innovation and hypothesis generation

| Capability | Generate innovation concepts (6 templates) |
|---|---|
| Service | `synapse.application.innovation.generate_innovations` |
| API | `POST /api/v1/innovations/generate` |
| Inputs | `problem_domain`, `context`, `candidate_entity_ids`, `max_concepts` |
| Outputs | `{concepts: [{id, hypothesis_id, components, evidence_refs, integration_mechanism, potential_benefit, ...}]}` |
| Persistence | Creates `EntityRow(kind="project")` (innovation concept) + `ClaimRow` (hypothesis, epistemic_state="hypothesized") + `EntityFingerprintRow` (deterministic fingerprint) + `AuditEventRow` (event_type="innovation.generated") |
| Status | IMPLEMENTED |

| Capability | Critique an innovation concept |
|---|---|
| Service | `synapse.application.innovation.critique_innovation` |
| API | `POST /api/v1/innovations/{innovation_id}/critique` |
| Outputs | `{feasibility, evidence_coverage, novelty_caveat, failure_modes, alternatives, overall_recommendation}` |
| Status | IMPLEMENTED |

### D.7 Experiment planning and execution

| Capability | Plan an experiment |
|---|---|
| Service | `synapse.application.experiment_planner.plan_experiment` |
| API | `POST /api/v1/experiments` |
| Inputs | `hypothesis_id`, `protocol`, `baseline`, `metrics: {name: dtype}`, `execution_mode`, `safety_limits`, `cost_limits` |
| Outputs | `{experiment: {experiment_id, objectives, success_criteria, failure_criteria, required_inputs, interpretation_rules, estimated_effort, fingerprint}}` |
| Persistence | Creates `EntityRow(kind="experiment")` + `EntityFingerprintRow` + `AuditEventRow` (event_type="experiment.planned") |
| Status | IMPLEMENTED |

| Capability | Get an experiment plan |
|---|---|
| Service | `synapse.application.experiment_planner.get_experiment` |
| API | `GET /api/v1/experiments/{experiment_id}` |
| Status | IMPLEMENTED |

| Capability | Create an execution record (idempotent via run_id) |
|---|---|
| Service | `synapse.application.execution_record.create_execution` |
| API | `POST /api/v1/experiments/{experiment_id}/execute` |
| Outputs | `{execution_id, experiment_id, hypothesis_id, status: "created", observations: [], ...}` |
| Persistence | Creates `EntityRow(kind="execution")` + `EntityFingerprintRow` + `AuditEventRow` (event_type="execution.created") |
| Status | IMPLEMENTED |

### D.8 Observation and verification

| Capability | Record an observation (append-only) |
|---|---|
| Service | `synapse.application.execution_record.record_observation` |
| API | `POST /api/v1/executions/{execution_id}/observations` |
| Inputs | `metric`, `observed_value`, `expected_value`, `unit`, `measurement_method`, `source_ref`, `uncertainty` |
| Outputs | `{observation_id, ...}` |
| Persistence | Appends to execution attributes JSON; transitions execution `CREATED → RUNNING`; emits `AuditEventRow` (event_type="execution.observed") |
| Status | IMPLEMENTED |

| Capability | Finalize execution (assess + emit EvidenceDelta) |
|---|---|
| Service | `synapse.application.execution_record.finalize_execution` |
| API | `POST /api/v1/executions/{execution_id}/finalize` |
| Inputs | `status: "completed" | "failed" | "cancelled"`, `error_details` |
| Outputs | `{status, assessment: {result: "supporting"|"contradicting"|"inconclusive"|"insufficient_data", reasoning, criteria_evaluated}, evidence_delta_id}` |
| Persistence | Sets terminal status; if `completed`, runs `assess_evidence()`; if assessment changes hypothesis state, emits `AuditEventRow` (event_type="evidence.delta") + updates `ClaimRow.epistemic_state` + `confidence_value`. Never promotes to VERIFIED. |
| Status | IMPLEMENTED |

| Capability | Get execution record |
|---|---|
| API | `GET /api/v1/executions/{execution_id}` |
| Status | IMPLEMENTED |

| Capability | Get evidence deltas (audit trail) |
|---|---|
| Service | `synapse.application.execution_record.get_evidence_deltas` |
| API | `GET /api/v1/hypotheses/{hypothesis_id}/evidence-deltas` |
| Outputs | `[{delta_id, hypothesis_id, prior_state, updated_state, observation, evidence_refs, assessment_result, ...}]` (newest first) |
| Status | IMPLEMENTED |

| Capability | Assess a claim's evidence (verification engine) |
|---|---|
| Service | `synapse.application.verification.assess_claim` |
| API | Indirect (called by other services; no direct `/api/v1/claims/{id}/assess` endpoint) |
| Outputs | `{outcome: "insufficient_evidence"|"source_supported"|"corroborated"|"contested"|"verified"|"stale_or_context_mismatch"|"not_evidenced", reason_code, ...}` + emits `AuditEventRow` (event_type="verification.assessed") |
| Persistence | Updates `ClaimRow.epistemic_state` (mapped from outcome: VERIFIED/CORROBORATED/SOURCE_SUPPORTED → supported; CONTESTED → stays as-is). |
| Status | IMPLEMENTED (VERIFIED is unreachable by design — PRB-05) |

### D.9 Future signals (G06-T01)

| Capability | Identify future signals (snapshot-only) |
|---|---|
| Service | `synapse.application.future_signal.identify_future_signals` |
| API | `POST /api/v1/future/signals` |
| Inputs | `focus_capability_id`, `focus_entity_id`, `combination_problem_domain`, `combination_context`, `limit` |
| Outputs | `{signals: [{signal_id, content_version, kind, ...}]}` (5 signal kinds: `gap_classification_snapshot`, `evidence_footprint_snapshot`, `combination_density_snapshot`, `contradiction_exposure_snapshot`, `dependency_graph_snapshot`) |
| Persistence | Read-only (no DB writes) |
| Status | IMPLEMENTED |

### D.10 Scenario construction (G06-T02)

| Capability | Construct + persist a scenario |
|---|---|
| Service | `synapse.application.scenario_builder.build_scenario` |
| API | `POST /api/v1/future/scenarios` |
| Inputs | `capability_ids: [str]`, `assumptions: [str]`, `signal_id`, `evidence_refs`, `missing_dependencies`, `horizon_class_override`, `critique`, `prototype_plan`, `linked_innovation_concept_id`, `run_id` |
| Outputs | `{scenario_id, horizon_class: "F0"|"F1"|"F2"|"F3"|"F4", current_evidence_refs, capabilities, assumptions, missing_dependencies, risks, critique, prototype_plan, linked_innovation_concept_id, scenario_fingerprint, provenance, was_reused}` |
| Persistence | Creates `EntityRow(kind="scenario")` with attributes JSON + `EntityFingerprintRow` (deterministic fingerprint) + `AuditEventRow` (event_type="scenario.created"). Uses `pg_advisory_xact_lock(run_id_hash)` on PG. |
| Evidence/provenance | Every `evidence_ref` must resolve to a persisted `EvidenceFragmentRow`. Every `capability_id` must resolve to a persisted `EntityRow(kind="capability")`. F1 fail-closed. F0 requires evidenced integration + empty missing_deps. |
| Status | IMPLEMENTED |

| Capability | Get a scenario |
|---|---|
| Service | `synapse.application.scenario_builder.get_scenario` |
| API | `GET /api/v1/future/scenarios/{scenario_id}` |
| Status | IMPLEMENTED |

### D.11 Scenario evaluation (G06-T03)

| Capability | Evaluate a scenario (7 dimensions) |
|---|---|
| Service | `synapse.application.scenario_evaluator.evaluate_scenario` |
| API | `POST /api/v1/future/scenarios/{scenario_id}/evaluate` |
| Outputs | `{scenario_id, evaluated_at, evidence_coverage: float, distinct_source_uri_count, independent_origin_count, dependency_readiness, contradiction_exposure, assumption_assessment, horizon_class, horizon_alignment, overall: "testable"|"needs_more_evidence"|"speculative"|"inconsistent", uncertainty, uncertainty_method: "1.0 - evidence_coverage", reasoning}` |
| Persistence | Read-only on scenario attributes. Emits `AuditEventRow` (event_type="scenario.evaluated") with summary payload. Persists full snapshot to `attributes.last_evaluation` via `persist_evaluation_snapshot` (additive, only before any /reassess). |
| Evidence/provenance | A fragment is "verified" only if a `verification.assessed` audit row exists with positive outcome (source_supported / corroborated / verified). Fragment existence alone does NOT count. |
| Status | IMPLEMENTED |

### D.12 Prototype planning (G06-T03)

| Capability | Plan a prototype (Mode A or Mode B) |
|---|---|
| Service | `synapse.application.prototype_planner.plan_prototype` |
| API | `POST /api/v1/future/scenarios/{scenario_id}/prototype-plan` |
| Inputs | `protocol_override`, `metrics: {name: dtype}` (required for Mode A), `baseline`, `execution_mode`, `safety_limits`, `cost_limits`, `hypothesis_id` (explicit selection if multiple), `run_id` |
| Outputs (Mode A) | `{mode: "linked_experiment", experiment: {...}, experiment_id, innovation_concept_id, hypothesis_id, audit_event_id}` — delegates to `plan_experiment` |
| Outputs (Mode B) | `{mode: "prototype_proposal", scenario_id, prototype_plan, missing_dependencies, investigation_targets, assumptions_to_test, prerequisites_missing, ...}` — no experiment entity created |
| Persistence | Mode A: creates `EntityRow(kind="experiment")` + emits `AuditEventRow` (event_type="scenario.prototype_linked"). Mode B: emits `AuditEventRow` (event_type="scenario.prototype_proposed"). |
| Status | IMPLEMENTED |

### D.13 Scenario reassessment (G06-T04 + T04A)

| Capability | Reassess a scenario (atomic, explicit-only) |
|---|---|
| Service | `synapse.application.scenario_revision.reassess_scenario` |
| API | `POST /api/v1/future/scenarios/{scenario_id}/reassess` |
| Inputs | `trigger: "explicit_reassess" | "new_evidence" | "new_experiment_result"`, `trigger_ref: str | null` |
| Outputs | `{scenario_id, revised: bool, prior_evaluation, new_evaluation, changes: [str], revision_id, prior_version, new_version, trigger, trigger_ref, reassessed_at, reassessed_by}` |
| Persistence | Acquires `SELECT ... FOR UPDATE` row lock BEFORE reading baseline. Compares new evaluation against LOCKED baseline on 8 material fields. If material change: atomically bumps version, persists `last_evaluation`, sets `last_revised_at`, appends `AuditEventRow` (event_type="scenario.revision"). If no change: returns `revised=false`, no audit row. |
| Evidence/provenance | The new evaluation is recomputed via `evaluate_scenario` against the current KB. The material-change comparison uses: `evidence_coverage`, `distinct_source_uri_count`, `independent_origin_count`, `dependency_readiness`, `contradiction_exposure`, `assumption_assessment`, `horizon_alignment`, `overall`. Volatile metadata (`evaluated_at`, etc.) excluded. |
| Auth | `PrincipalDep` |
| Status | IMPLEMENTED (atomic, T04A-corrected) |

### D.14 Revision history (G06-T04)

| Capability | Get scenario revision history (newest first, append-only) |
|---|---|
| Service | `synapse.application.scenario_revision.get_scenario_revisions` |
| API | `GET /api/v1/future/scenarios/{scenario_id}/revisions` |
| Outputs | `[{revision_id, scenario_id, prior_version, new_version, prior_evaluation, new_evaluation, changes, trigger, trigger_ref, reassessed_at, reassessed_by, actor, created_at}]` (newest first) |
| Persistence | Reads `AuditEventRow` WHERE `event_type='scenario.revision' AND target_id=scenario_id` ORDER BY `created_at DESC`. |
| Status | IMPLEMENTED |

### D.15 System / utility

| Capability | Liveness / readiness probes |
|---|---|
| API | `GET /health/live`, `GET /health/ready` |
| Auth | None |
| Status | IMPLEMENTED |

| Capability | Activity mode (read-only) |
|---|---|
| API | `GET /api/v1/system/activity-mode` (returns `passive` / `assisted` / `autonomous`) |
| API | `PUT /api/v1/system/activity-mode` → 501 Not Implemented |
| Status | GET IMPLEMENTED; PUT NOT IMPLEMENTED (501) |

| Capability | Static capability catalog (G01 seed) |
|---|---|
| API | `GET /api/v1/capabilities` (returns 4 static capabilities: `web.fetch`, `web.discover`, `text.extract`, `reasoning.query`) |
| Status | IMPLEMENTED (static) |

| Capability | Static provider catalog |
|---|---|
| API | `GET /api/v1/providers` (returns 6 providers: openai, anthropic, http-fetch [enabled], arxiv, openalex, github) |
| Status | IMPLEMENTED (static — only arxiv + http-fetch are actually wired) |

---

## Section E — User Workflows

This section documents complete user-facing workflows supported by
the current backend. For each workflow, it distinguishes implemented
backend steps, required user inputs, available outputs, missing UI
capabilities, and unsupported/incomplete steps.

**Important**: Backend availability is NOT proof that a working UI
exists. The UI does not exist. `[VERIFIED_FROM_CODE — repo glob
`**/frontend*` returns 0 matches]`

### E.1 Discover a technical source and extract useful knowledge

**Implemented backend steps**:
1. `POST /api/v1/sources/discover` with a search query → returns arXiv
   candidate sources.
2. User selects a source URI.
3. `POST /api/v1/sources/ingest` with the canonical URI → fetches +
   extracts + persists `SourceRow`, `EvidenceFragmentRow`,
   `AuditEventRow`.
4. The ingestion pipeline automatically runs `extract()` (deterministic
   regex) + `persist_extraction_results()` (canonicalization), creating
   `EntityRow`s, `ClaimRow`s, `SourceSpanRow`s.

**Required user inputs**: search query; source selection; (optional)
source metadata.

**Available outputs**: list of arXiv candidates; ingested source
record with evidence fragments; the underlying entities/claims
discoverable via `/api/v1/entities` and `/api/v1/claims/{id}/evidence`.

**Missing UI capabilities**: source-discovery search box; source
preview (title/authors/abstract); source selection UX; ingestion
progress indicator (currently synchronous); evidence-fragment viewer
with source-span highlighting.

**Unsupported / incomplete steps**: ingestion is synchronous (no
background job); only arXiv is wired for discovery (no OpenAlex,
no GitHub, no browser runtime per ADR-0009).

### E.2 Investigate a technical claim

**Implemented backend steps**:
1. `POST /api/v1/knowledge/search` by entity name/alias → returns
   matching entities.
2. `GET /api/v1/entities/{id}` → returns entity with its claims,
   relationships, source spans.
3. `GET /api/v1/claims/{id}/evidence` → returns evidence fragments
   + source spans + latest verification assessment for a specific
   claim.
4. (Optional) `POST /api/v1/reasoning/queries` with a natural-language
   query → returns evidence-grounded findings (DOCUMENTED_FACT /
   DERIVED_FINDING / HYPOTHESIS / UNKNOWN).

**Required user inputs**: entity name or claim ID; (optional)
reasoning query.

**Available outputs**: entity detail; claim evidence bundle; reasoning
findings.

**Missing UI capabilities**: entity-detail view; claim-evidence
timeline / provenance graph; reasoning-query box with findings
presentation; source-span excerpt viewer with context.

**Unsupported / incomplete steps**: no claim annotation / notes;
no claim comparison view; reasoning is deterministic (no LLM).

### E.3 Find related tools and capabilities

**Implemented backend steps**:
1. `GET /api/v1/knowledge/capabilities` → list documented capabilities.
2. `GET /api/v1/knowledge/capabilities/{id}` → full detail
   (providers, dependencies, limitations, claims, evidence).
3. `POST /api/v1/knowledge/capabilities/analyze-gap` with required
   capabilities → 6-class classification.
4. `GET /api/v1/relationships?entity_id=X` → typed edges
   (PROVIDES / REQUIRES / INTEGRATES_WITH / LIMITS / SUPPORTS /
   CONTRADICTS / etc.).

**Required user inputs**: capability name or entity ID; (optional)
list of required capabilities for gap analysis.

**Available outputs**: capability catalog; capability detail with
providers/dependencies/limitations; gap analysis with 6-class
classifications; relationship list.

**Missing UI capabilities**: capability catalog browse; capability
detail view with provider cards; gap analysis matrix view;
relationship graph visualization.

**Unsupported / incomplete steps**: no interactive graph traversal
UI (the backend supports `find_paths` but no API endpoint exposes it
directly — only via reasoning); no bookmark/favorite.

### E.4 Combine capabilities into a new product idea

**Implemented backend steps**:
1. `POST /api/v1/innovations/generate` with `problem_domain` and
   (optional) `candidate_entity_ids` → returns innovation concepts
   with components, evidence_refs, integration_mechanism,
   potential_benefit.
2. (Optional) `POST /api/v1/innovations/{id}/critique` → returns
   feasibility, evidence_coverage, novelty_caveat, failure_modes,
   alternatives, overall_recommendation.

**Required user inputs**: problem domain (free text); (optional)
list of candidate entity IDs to focus on; (optional) context.

**Available outputs**: innovation concepts (each with hypothesis_id
+ components + evidence_refs + integration mechanism + potential
benefit); critique (feasibility + evidence coverage + novelty
caveat + failure modes + alternatives + overall recommendation).

**Missing UI capabilities**: problem-domain input; concept list view
with component cards; concept-detail view with evidence provenance;
critique view with feasibility/recommendation badges; "save concept
for later" / "send to experiment planning" workflow.

**Unsupported / incomplete steps**: no LLM-assisted brainstorming
(deterministic templates only); no collaborative concept editing.

### E.5 Evaluate an innovation hypothesis

**Implemented backend steps**:
1. (Prerequisite) An innovation concept must exist (from
   `/innovations/generate`).
2. `POST /api/v1/innovations/{id}/critique` → critique output.

**Required user inputs**: innovation concept ID.

**Available outputs**: feasibility score; evidence coverage;
novelty caveat; failure modes; alternatives; overall recommendation
(which G07 may rely on per G05 §5.3 stability contract).

**Missing UI capabilities**: critique dashboard; evidence-coverage
breakdown; failure-mode cards; alternative-comparison view.

**Unsupported / incomplete steps**: critique is deterministic (no
LLM); no collaborative critique.

### E.6 Plan an experiment

**Implemented backend steps**:
1. (Prerequisite) A hypothesis must exist (from `/innovations/generate`).
2. `POST /api/v1/experiments` with `hypothesis_id`, `protocol`,
   `metrics: {name: dtype}`, `baseline`, `execution_mode`,
   `safety_limits`, `cost_limits` → returns experiment plan with
   objectives, success_criteria, failure_criteria, required_inputs,
   interpretation_rules, estimated_effort, fingerprint.
3. `GET /api/v1/experiments/{id}` → retrieve the plan.

**Required user inputs**: hypothesis ID; protocol; metric schema
(name → dtype); (optional) baseline; (optional) execution mode
(default `dry_run`); (optional) safety_limits (required for
production mode); (optional) cost_limits (required for networked mode).

**Available outputs**: experiment plan with measurable criteria.

**Missing UI capabilities**: hypothesis selector; protocol editor;
metric-schema builder; safety/cost-limits forms (with approval
workflow for production/networked modes); experiment-plan preview
with success/failure thresholds.

**Unsupported / incomplete steps**: production-mode experiments
require `safety_limits.approved_by` but there's no UI to set/approve;
networked-mode experiments require `cost_limits.approved_by` but
there's no UI to set/approve. `[VERIFIED_FROM_CODE — src/synapse/domain/experiment.py]`

### E.7 Recording and reviewing experimental results

**Implemented backend steps**:
1. `POST /api/v1/experiments/{experiment_id}/execute` → creates
   execution record (status="created", idempotent via `run_id`).
2. `POST /api/v1/executions/{execution_id}/observations` with
   `metric`, `observed_value`, `expected_value`, `unit`,
   `measurement_method`, `source_ref`, `uncertainty` → appends
   observation, transitions execution `CREATED → RUNNING`.
3. (Repeat step 2 for additional observations.)
4. `POST /api/v1/executions/{execution_id}/finalize` with
   `status: "completed"|"failed"|"cancelled"` → assesses evidence
   (if completed), possibly emits `EvidenceDelta` (if hypothesis
   state changes). Never promotes to VERIFIED.
5. `GET /api/v1/executions/{id}` → execution record with all
   observations + assessment.
6. `GET /api/v1/hypotheses/{hypothesis_id}/evidence-deltas` →
   ordered belief-change audit trail.

**Required user inputs**: experiment ID; observation values; finalize
status; (optional) observation source_ref (experimenter ID).

**Available outputs**: execution record; assessment result
(`supporting` / `contradicting` / `inconclusive` / `insufficient_data`);
evidence deltas (prior_state, updated_state, evidence_refs).

**Missing UI capabilities**: execution-record list view; observation
entry form with metric validation; finalize-action button with
confirmation; assessment-result visualization; evidence-delta
timeline.

**Unsupported / incomplete steps**: no automated experiment
execution (observations are manually entered); no calibration_status
UI (operational vs. requires_calibration — currently requires direct
DB manipulation in tests).

### E.8 Construct and evaluate a future scenario

**Implemented backend steps**:
1. (Prerequisite) Capabilities and evidence must exist in the KB.
2. `POST /api/v1/future/signals` (optional) → identify current
   signals (5 snapshot kinds) to ground the scenario.
3. `POST /api/v1/future/scenarios` with `capability_ids`,
   `assumptions`, (optional) `signal_id`, `evidence_refs`,
   `missing_dependencies`, `horizon_class_override`, `critique`,
   `linked_innovation_concept_id` → constructs + persists the
   scenario with deterministic F0–F4 horizon classification.
4. `POST /api/v1/future/scenarios/{id}/evaluate` → 7-dimension
   evaluation.
5. `GET /api/v1/future/scenarios/{id}` → retrieve the scenario
   with all attributes.

**Required user inputs**: capability IDs; assumptions (non-empty
strings); (optional) signal ID for grounding; (optional) horizon
override (may only downgrade); (optional) critique (required for
F4); (optional) linked innovation concept ID.

**Available outputs**: scenario with F0–F4 horizon; 7-dimension
evaluation (evidence_coverage, distinct_source_uri_count,
independent_origin_count, dependency_readiness,
contradiction_exposure, assumption_assessment, horizon_alignment,
overall, uncertainty).

**Missing UI capabilities**: signal-list view; scenario builder
with capability picker; assumption editor; horizon classifier
explanation; evaluation dashboard with 7-axis breakdown;
uncertainty visualization.

**Unsupported / incomplete steps**: F1 fail-closed (the builder
raises ValueError if no honest integration dependency can be named
— the UI must surface this error clearly).

### E.9 Reassess a scenario after new evidence

**Implemented backend steps**:
1. (Prerequisite) A scenario must exist with an established baseline
   (either `/evaluate` was called OR a prior `/reassess` ran).
2. KB change: new evidence is ingested, a claim is retracted, an
   experiment finalizes, etc.
3. `POST /api/v1/future/scenarios/{id}/reassess` with `trigger`
   (`explicit_reassess` / `new_evidence` / `new_experiment_result`)
   + (optional) `trigger_ref` → recomputes evaluation, compares
   against the LOCKED baseline, bumps version + writes
   `scenario.revision` audit row if material change; otherwise
   returns `revised=false`.
4. `GET /api/v1/future/scenarios/{id}/revisions` → append-only
   revision history (newest first).

**Required user inputs**: scenario ID; trigger; (optional)
trigger_ref (e.g., execution_id, fragment_id, claim_id).

**Available outputs**: reassessment result (`revised`, `prior_evaluation`,
`new_evaluation`, `changes`, `revision_id`, `prior_version`,
`new_version`); revision history.

**Missing UI capabilities**: "Reassess" button with trigger selector;
trigger-ref picker (browse executions/fragments/claims); changes-diff
view (which material fields changed); revision timeline with prior/new
snapshot comparison; rollback visualization (note: rollback is via DB
transaction, NOT a UI affordance — there's no "undo revision" endpoint).

**Unsupported / incomplete steps**: no background reassessment
scheduler (explicit-only per G06 plan §4.5; a future group with
explicit authorization may add a worker); no "revert revision"
endpoint (revisions are append-only by design).

---

## Section F — Data and Epistemic Integrity

This section documents the project's rules governing evidence,
provenance, epistemic states, hypothesis vs. verified fact, experiment
plans vs. results, scenario horizon classifications, missing
dependencies, revision history, idempotency, and concurrent writes.
**These rules MUST be preserved by the G07 UI** (user-facing labels
and warnings).

### F.1 Evidence verification

**Rule**: A fragment is "verified" if and only if a `verification.assessed`
audit row exists for a claim citing that fragment, with a positive
outcome (`source_supported` / `corroborated` / `verified`).
`[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py:_is_fragment_verified]`

**User-facing implication**: The UI must distinguish "evidence
exists" from "evidence is verified". A fragment can exist (and be
shown in a list) without being verified. The verification badge
must reflect the latest `verification.assessed` outcome.

### F.2 Provenance

**Rule**: Every `EvidenceFragmentRow` carries `source_uri`,
`source_id`, `acquisition_id`, `retrieved_at`, `extraction_method`,
`content_fingerprint`, `toolkit_commit_sha`, `provider`. Every
`SourceRow` carries `publisher`, `author`, `license`, `canonical_uri`.
`[VERIFIED_FROM_CODE — src/synapse/storage/models.py:EvidenceFragmentRow, SourceRow]`

**User-facing implication**: The UI must always show the source
URI and retrieved_at for any evidence. The toolkit_commit_sha
links to the exact Toolkit version used for extraction.

### F.3 Source independence

**Rule**: `distinct_source_uri_count` is the factual count of
distinct fragment source URIs. `independent_origin_count` is
**conservative**: 0 if no fragments, 1 if ≥1 fragment (per PRB-04
— never invents independence without explicit publisher/author
metadata).
`[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py:_compute_source_provenance]`

**User-facing implication**: The UI must NOT claim "independent
sources" unless the conservative count is > 1 (which currently
requires ≥2 fragments from distinct source URIs). The label
should be "single source" or "multiple sources" — never
"verified independent" without explicit policy.

### F.4 Claim confidence and uncertainty

**Rule**: `ClaimRow.confidence_value` (float [0.0, 1.0]) requires
`confidence_method`. `assess_claim` does not set confidence_value
directly — it's set by `finalize_execution` (±0.2 clamped to
[0.0, 1.0] on a SUPPORTING/CONTRADICTING assessment).
`[VERIFIED_FROM_CODE — src/synapse/application/execution_record.py:finalize_execution]`

**User-facing implication**: The UI must show confidence_value
alongside the confidence_method. A claim with no confidence_value
must display "no confidence assessed".

### F.5 Unsupported assumptions

**Rule**: An assumption is classified `supported` / `contested` /
`not_evidenced` / `unknown`. The default is `unknown` — natural-
language similarity alone is NOT proof. Shared evidence fragments
alone are NOT sufficient. The only structured association is
`scenario.risks["assumption_associations"]` (assumption text →
claim ID).
`[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py:_assess_assumptions]`

**User-facing implication**: The UI must NOT auto-classify
assumptions. The user must explicitly associate an assumption
with a claim (via the `risks.assumption_associations` field).
The UI should encourage this association.

### F.6 Hypothesis versus verified fact

**Rule**: `EpistemicState` is `supported` / `inferred` /
`hypothesized` / `disputed` / `rejected`. **There is NO `VERIFIED`
value.** An experiment that supports a hypothesis moves it
`HYPOTHESIZED → SUPPORTED`, never to `VERIFIED`. VERIFIED is
unreachable in the deterministic-v2 verification policy by design.
`[APPROVED_DESIGN — ADR-0011 PRB-05; VERIFIED_FROM_CODE — src/synapse/domain/_base.py:EpistemicState]`

**User-facing implication**: The UI must NEVER show a "Verified"
label. The strongest epistemic label is "Supported". A hypothesis
that has supporting experimental evidence is "Supported" — not
"Verified". The UI must explain this distinction.

### F.7 Experiment plans versus empirical results

**Rule**: An experiment plan (`EntityRow(kind="experiment")`) is
NOT evidence that the experiment succeeded. An execution record
(`EntityRow(kind="execution")`) is NOT a verified claim. An
observation (appended to execution attributes) is NOT automatically
a verified claim. A finalized experiment (`finalize_execution`)
emits a `evidence.delta` audit row updating `hypothesis.epistemic_state`
— but only `assess_claim(hypothesis)` emits the `verification.assessed`
audit row that the evaluator consults.
`[VERIFIED_FROM_CODE — src/synapse/application/scenario_revision.py docstring §5]`

**User-facing implication**: The UI must clearly distinguish
"experiment planned" / "experiment executing" / "observation
recorded" / "experiment finalized" / "hypothesis verified". A
finalized experiment is not the same as a verified hypothesis.

### F.8 Scenario horizon classifications F0–F4

**Rule**: The horizon class is **deterministically derived from
the KB state**, not chosen freely by the caller. The caller may
only **downgrade** (make more speculative). Precedence: F4 > F3 >
F2 > F1 > F0 (first matching rule wins, rules mutually exclusive).
`[VERIFIED_FROM_CODE — src/synapse/application/scenario_builder.py:_derive_horizon]`

| Horizon | Meaning | Components | Integration evidence | Missing dependencies | Critique |
|---------|---------|------------|---------------------|---------------------|----------|
| **F0** | Existing — the configuration is documented in the KB today. | Each component has ≥1 SUPPORTED claim. | ≥1 SUPPORTED claim or explicit relationship evidencing *this* integration. | None (empty). | Not required. |
| **F1** | Possible but uncommon — components exist; integration unevidenced. | Each component has ≥1 SUPPORTED claim. | None — the combination is unevidenced. | ≥1 honestly unresolved integration dependency (real KB entity lacking INTEGRATES_WITH/PROVIDES/ENABLES to other components). | Not required. |
| **F2** | Near-term engineering — components exist; missing piece is engineering effort. | Each component has ≥1 SUPPORTED claim. | None (or partial). | ≥1 "engineering" dependency (entity exists with SUPPORTED claim but missing integration relationship). | Not required. |
| **F3** | Research-dependent — at least one missing dependency is an open research question. | Components may be SUPPORTED or partially supported. | None (or partial). | ≥1 "research" dependency (entity does not exist in KB, or exists only with HYPOTHESIZED/INFERRED claims). | Not required. |
| **F4** | Speculative — rests on assumptions not grounded in current evidence. | Any. | Any. | Any (may be empty only if critique explains why). | **Mandatory** (`Scenario._f4_requires_critique`). |

**Horizons are categorical, NOT temporal.** G06 does NOT assign
"3 months" / "1 year" / "5 years" to F0–F4. Time estimates require
longitudinal data Synapse does not have. `[APPROVED_DESIGN — G06 plan §3.3, §4.1]`

**User-facing implication**: The UI must show the horizon label
prominently. The UI must NOT add time estimates. The UI must explain
each horizon's meaning (the table above). F1 scenarios may need
the user to add an honest integration dependency (F1 fail-closed
raises `ValueError` otherwise).

### F.9 Missing dependencies

**Rule**: `missing_dependencies` is a list of entity IDs. Each is
classified as `research` / `engineering` / `integration` /
`caller_attested` / `unknown`. Caller-supplied deps are NOT
independently verified. `[VERIFIED_FROM_CODE — src/synapse/application/scenario_evaluator.py:_compute_dependency_readiness]`

**User-facing implication**: The UI must show each dependency's
classification. Caller-attested dependencies must be labeled as
such (not "verified").

### F.10 Revision history

**Rule**: The revision history is **append-only**. Prior snapshots
are never overwritten. Each revision is a new `AuditEventRow`
with `event_type="scenario.revision"`. The history is ordered
newest-first. Version numbers are strictly monotonic (1 → 2 →
3 → ...). `[VERIFIED_FROM_CODE — src/synapse/application/scenario_revision.py:get_scenario_revisions]`

**User-facing implication**: The UI must show the full revision
history as a timeline. Each entry must show prior_version,
new_version, prior_evaluation snapshot, new_evaluation snapshot,
changes list, trigger, trigger_ref, reassessed_at, reassessed_by.
The UI must NOT offer a "delete revision" affordance (revisions
are immutable).

### F.11 Idempotency

**Rule**: Idempotent identity is DB-enforced via
`EntityFingerprintRow` UNIQUE on `(kind, fingerprint)`. Scenarios,
innovation concepts, experiments, executions all use this pattern.
Same `run_id` + same payload → same entity ID (idempotent reuse).
Same `run_id` + conflicting payload → `ValueError` (explicit
rejection). No `run_id` → each call is independent.
`[VERIFIED_FROM_CODE — src/synapse/storage/fingerprint.py:claim_fingerprint; src/synapse/application/scenario_builder.py:_compute_scenario_fingerprint]`

**User-facing implication**: The UI may safely retry create
operations with the same `run_id`. Conflicting retries should be
surfaced as errors (not silent duplicates).

### F.12 Concurrent writes

**Rule**: Concurrent reassessments of the same scenario are
serialized via `SELECT ... FOR UPDATE` row-level locking on
PostgreSQL. Two concurrent reassessments of the same material
change produce exactly ONE revision (the second sees the locked
baseline has shifted and returns `revised=false`). SQLite
serializes writes at the DB level. `[VERIFIED_FROM_CODE — src/synapse/application/scenario_revision.py:_load_scenario_entity_for_update]`

**User-facing implication**: The UI may issue concurrent reassess
calls safely (no duplicate revisions). The UI should show a
loading indicator during reassess (the operation may block on
the row lock).

---

## Section G — Frozen Architectural Decisions

This section summarizes the approved ADRs and important cross-stage
design decisions. It explicitly identifies frozen contracts,
prohibited substitutions, components that must be reused, integration
boundaries, security/audit requirements, and decisions G07 must not
reopen without explicit approval.

### G.1 Approved ADRs (0001–0011)

`[APPROVED_DESIGN — docs/decisions/0001-*.md through 0011-*.md]`

| ADR | Title | Status | Key decision |
|-----|-------|--------|--------------|
| 0001 | Modular monolith | Accepted | Single FastAPI process, single store (PG prod / SQLite test), 8 packages. No microservices/swarm/graph DB. |
| 0002 | SQLite test dialect | Accepted | Tests use SQLite in-memory; production uses PostgreSQL. ORM uses ANSI SQL types. pgvector deferred. |
| 0003 | Local dev auth, fail-closed prod | Accepted | `DevelopmentAuthAdapter` (env keys) + `ProductionAuthAdapter` (fails closed unless `SYNAPSE_AUTH_PROVIDER` configured). Scopes: reader/researcher/editor/operator/admin. |
| 0004 | SSRF denylist by default | Accepted | `validate_url` enforces scheme allowlist + hostname resolution + IP denylist (loopback/RFC1918/link-local/CGNAT/IPv6 ULA/link-local/IPv4-mapped IPv6). |
| 0005 | Pydantic v2 typed domain contracts | Accepted | All 15 domain records are `pydantic.BaseModel` with `extra="forbid"`. Common `DomainRecord` base. String-backed enums. Invariants via `@model_validator`. DTOs separate from ORM. |
| 0006 | API-first contract | Accepted | All routes under `/api/v1`; OpenAPI 3.1 at `/api/v1/openapi.json`; standard `{data, meta, error}` envelope; `application/problem+json` errors; `202 Accepted` async job pattern; `Idempotency-Key` table; CORS fail-fast on `*` + credentials. |
| 0007 | G01 conditional acceptance | Accepted (user-conditional) | G01 conditionally accepted. D-01 toolkit audit not waived, D-02 production IdP deferred, D-03 licensing deferred. |
| 0008 | G02 Audit Policy Amendment | Accepted | 5 clarifications: tiered verification flags, real functional smoke tests, license flag-not-reject, provenance preservation sub-object, read-only audit. |
| 0009 | G02 Audit Review Clarifications | Accepted | 8 clarifications: freeze audit at Toolkit commit `fd9df34`, separate inventory vs. functional coverage, correct credential statement, do not modify Toolkit. |
| 0010 | G02 Conditional Closure | Accepted | Freeze 3 providers; PostgreSQL migration validation unresolved; 4 IP-pinned transport items verified; G02 NOT production-certified. |
| 0011 | G03 Functional Closure | Accepted (functional; production NOT approved) | G03 closed at commit `046443a`. Established PRB-01..07 production-readiness blocker register. |

### G.2 Frozen contracts (must not be modified without explicit approval)

1. **Domain records** (`src/synapse/domain/*.py`): All 15 records
   are frozen. Adding fields is forward-compatible; removing/renaming
   requires `/api/v2` and explicit user approval.
   `[APPROVED_DESIGN — ADR-0005, ADR-0011 "G03 scope is frozen"]`

2. **`Scenario` domain record**: FROZEN per G06-T02. The F0–F4
   validator (`_f4_requires_critique`, `_non_f0_must_list_missing_deps`)
   is unchanged. The `assumption_associations` policy (T03A) is
   unchanged. `[APPROVED_DESIGN — G06 plan §16.2 Correction B]`

3. **`Relationship` 16-predicate vocabulary** (`PREDICATES`):
   No new relationship predicates. G06-T04 uses only existing
   predicates (no `SCENARIO_*` predicate invented).
   `[APPROVED_DESIGN — G06 plan §16.5 Correction E]`

4. **`EpistemicState` enum**: No `VERIFIED` value. Adding `VERIFIED`
   requires a new verification policy version (e.g.,
   `deterministic-v3` with human-review integration or
   `experimental-validation-v1`). `[APPROVED_DESIGN — ADR-0011 PRB-05]`

5. **`uncertainty = 1.0 − evidence_coverage`**: This formula is
   fixed. No horizon coefficient, no assumption coefficient, no
   Bayesian prior. The API output must include
   `uncertainty_method: "1.0 - evidence_coverage"`.
   `[APPROVED_DESIGN — G06 plan §16.3 Correction C]`

6. **Standard response/error envelopes**: `{data, meta: {request_id,
   api_version, pagination}, error}` and `application/problem+json`
   (RFC 9457). `[APPROVED_DESIGN — ADR-0006]`

7. **OpenAPI schema at `/api/v1/openapi.json`**: The schema is
   auto-generated and verified by `make openapi-check`. Adding
   routes is forward-compatible; removing/renaming requires
   `/api/v2`.

### G.3 Prohibited architectural substitutions

G07 must NOT introduce:

- ❌ A second backend service (microservices, swarm, separate worker
  process). The modular monolith is binding. `[APPROVED_DESIGN — ADR-0001]`
- ❌ A graph database (Neo4j, etc.). The knowledge graph is
  SQLAlchemy ORM. `[APPROVED_DESIGN — ADR-0001]`
- ❌ A vector database (pgvector / Pinecone / Weaviate). Deferred
  until measured workload justifies. `[APPROVED_DESIGN — ADR-0002]`
- ❌ A message broker (Kafka, RabbitMQ, Redis pub/sub). Jobs table
  + workers package are sufficient for current scope.
  `[APPROVED_DESIGN — ADR-0001]`
- ❌ A BFF (backend-for-frontend) shim. G07 consumes the existing
  `/api/v1/*` contracts directly. `[PROPOSED_FOR_G07 — G05 plan §12.2]`
- ❌ A new ORM table or migration. G07 is documentation/UX only —
  no DB schema changes. `[APPROVED_DESIGN — G06 plan §10 "Scope and
  architecture restrictions"]`
- ❌ An LLM call in the deterministic core. No `import openai` /
  `import anthropic` in the core services. (LLM providers may be
  wired as opt-in adapters for non-deterministic features, but the
  deterministic knowledge graph, retrieval, reasoning, evaluation,
  and revision logic must remain LLM-free.)
  `[APPROVED_DESIGN — G06 plan §11 QG-10]`
- ❌ A second experiment engine. G06-T03 Mode A delegates to the
  existing G05 `plan_experiment`. `[APPROVED_DESIGN — G06 plan §10]`
- ❌ A background scheduler for reassessment. Reassessment is
  explicit-only. A future group with explicit authorization may
  add a worker that calls the same `/reassess` endpoint on a
  schedule. `[APPROVED_DESIGN — G06 plan §4.5]`
- ❌ Auto-promotion to `VERIFIED`. `EpistemicState` has no `VERIFIED`
  value. `[APPROVED_DESIGN — ADR-0011 PRB-05]`
- ❌ Time estimates for F0–F4 horizon classes. Horizons are
  categorical, not temporal. `[APPROVED_DESIGN — G06 plan §3.3]`

### G.4 Existing components that must be reused

| Component | Source path | Reuse reason |
|-----------|-------------|--------------|
| `EntityRow(kind="scenario")` persistence | `src/synapse/storage/models.py` | G06 persists scenarios as EntityRow with attributes JSON. No new table. |
| `AuditEventRow` append-only audit log | `src/synapse/storage/models.py` | The single source of truth for provenance + belief-change history. Every group uses it. |
| `EntityFingerprintRow` UNIQUE constraint | `src/synapse/storage/models.py` | DB-enforced idempotent identity. Used by G05 + G06. |
| `claim_fingerprint()` helper | `src/synapse/storage/fingerprint.py` | Atomically claims a `(kind, fingerprint)` pair. |
| `pg_advisory_xact_lock` pattern | `src/synapse/application/scenario_builder.py:_acquire_run_id_lock` | Concurrent-retry serialization on PostgreSQL. |
| `SELECT ... FOR UPDATE` pattern | `src/synapse/application/scenario_revision.py:_load_scenario_entity_for_update` | Atomic revision integrity. |
| G05-T06 experiment lifecycle | `src/synapse/application/experiment_planner.py`, `execution_record.py` | G06-T03 Mode A delegates here. |
| G04 `analyze_gap` | `src/synapse/application/gap_analyzer.py` | G06-T01 signals + G06-T02 scenario construction consult this. |
| G04 hybrid retrieval | `src/synapse/application/retrieval.py` | G06 may use this for evidence-aware scenario enrichment. |
| G03 `assess_claim` verification engine | `src/synapse/application/verification.py` | The ONLY way to emit `verification.assessed` audit rows. |
| G03 `RelationshipService` | `src/synapse/application/relationship_service.py` | Typed-edge CRUD + traversal. |
| Standard envelope + problem+json | `src/synapse/api/responses.py`, `src/synapse/api/errors.py` | All G07 UI calls must consume these. |

### G.5 Integration boundaries

- **AgentCraft-Toolkit**: **READ ONLY**. The Toolkit is the upstream
  open-source project; Synapse vendors specific providers from
  Toolkit commit `fd9df34` but MUST NOT modify the Toolkit upstream.
  `[APPROVED_DESIGN — ADR-0009 §4]`
- **External providers**: Only `arxiv_search`, `trafilatura_extractor`,
  `content_delta_hash` are wired. Adding new providers requires
  explicit authorization (not in G07 scope).
  `[APPROVED_DESIGN — ADR-0010 §1]`
- **G07 UI** must consume the existing `/api/v1/*` contracts. No
  hidden endpoints, no BFF shim. `[PROPOSED_FOR_G07 — G05 plan §12.2]`

### G.6 Security and audit requirements

- **SSRF protection** (`src/synapse/security/ssrf.py`): All external
  HTTP calls go through `validate_url`. IP-pinned HTTP transport
  (`src/synapse/security/http_transport.py`) prevents DNS-rebinding
  TOCTOU. `[APPROVED_DESIGN — ADR-0004]`
- **Authentication**: `PrincipalDep` on all `/api/v1/*` routes
  except `/health/*`. Production auth fails closed unless
  `SYNAPSE_AUTH_PROVIDER` is configured. `[APPROVED_DESIGN — ADR-0003]`
- **Audit trail**: Every material action emits an `AuditEventRow`.
  The audit log is append-only. `[VERIFIED_FROM_CODE — src/synapse/storage/models.py:AuditEventRow]`
- **Provenance preservation**: Every evidence fragment carries
  `toolkit_commit_sha`, `extraction_method`, `content_fingerprint`.
  `[APPROVED_DESIGN — ADR-0008 §4]`

### G.7 Decisions G07 must not reopen without explicit approval

- The F0–F4 horizon classification rules (§F.8).
- The 7-dimension scenario evaluation axes (§D.11).
- The 8 material-change fields for revision detection (§F.10 + G06-T04 §3.1).
- The `uncertainty = 1.0 − evidence_coverage` formula.
- The "explicit-over-autonomous" principle (no background scheduler).
- The 16-predicate relationship vocabulary.
- The `EpistemicState` enum (no `VERIFIED`).
- The standard response/error envelope shapes.
- The `/api/v1/*` route prefix.
- The modular-monolith architecture.

---

## Section H — API and Integration Reference

This section is an accurate API inventory derived from the actual
routers and the generated OpenAPI schema (`/api/v1/openapi.json`).
`[VERIFIED_FROM_CODE — src/synapse/api/v1/*.py + openapi schema]`

### H.1 Health (no auth)

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| GET | `/health/live` | Liveness probe (process up). | inline (no service) |
| GET | `/health/ready` | Readiness probe (DB `SELECT 1`). | inline |

### H.2 Sources

| Method | Route | Purpose | Service | Validation |
|--------|-------|---------|---------|------------|
| POST | `/api/v1/sources/discover` | Discover arXiv candidates. | `acquisition.discover_sources` | `query` required; `max_results` 1-100. |
| POST | `/api/v1/sources/ingest` | Fetch + extract + persist a source. | `acquisition.acquire_and_extract` | `canonical_uri` required (SSRF-validated). |
| GET | `/api/v1/sources` | List sources (max 50, newest first). | inline query | none |
| GET | `/api/v1/sources/{source_id}` | Get a single source. | inline query | 404 if missing. |
| GET | `/api/v1/sources/{source_id}/acquisitions` | List acquisitions + fragments. | inline query | 404 if source missing. |

### H.3 Knowledge (entities, relationships, claims)

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| GET | `/api/v1/entities` | List entities (paginated, filter by kind). | inline |
| GET | `/api/v1/entities/{entity_id}` | Get entity with claims, relationships, source spans. | inline |
| GET | `/api/v1/relationships` | List relationships (filterable). | `relationship_service.find_relationships` |
| GET | `/api/v1/relationships/{relationship_id}` | Get relationship with evidence. | `relationship_service.get_relationship_evidence` |
| GET | `/api/v1/claims/{claim_id}/evidence` | Get evidence fragments + source spans + verification assessment. | inline |
| POST | `/api/v1/knowledge/search` | Search entities by name/alias (ILIKE substring). | inline |

### H.4 Retrieval

| Method | Route | Purpose | Service | Inputs |
|--------|-------|---------|---------|--------|
| POST | `/api/v1/knowledge/retrieve` | Hybrid retrieval. | `retrieval.hybrid_retrieve` | `query`, `entity_kind`, `predicate`, `epistemic_state`, `max_depth`, `limit` |

### H.5 Capability registry + gap analysis

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| GET | `/api/v1/knowledge/capabilities` | List documented capabilities. | `capability_registry.list_capabilities` |
| GET | `/api/v1/knowledge/capabilities/{capability_id}` | Get capability detail. | `capability_registry.get_capability` |
| POST | `/api/v1/knowledge/capabilities/analyze-gap` | 6-class gap analysis. | `gap_analyzer.analyze_gap` |

### H.6 Reasoning

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| POST | `/api/v1/reasoning/queries` | Answer a reasoning query. | `reasoning.answer_query` |
| GET | `/api/v1/reasoning/intents` | List supported intents. | inline |

### H.7 Innovations

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| POST | `/api/v1/innovations/generate` | Generate innovation concepts. | `innovation.generate_innovations` |
| POST | `/api/v1/innovations/{innovation_id}/critique` | Critique a concept. | `innovation.critique_innovation` |

### H.8 Experiments

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| POST | `/api/v1/experiments` | Plan an experiment. | `experiment_planner.plan_experiment` |
| GET | `/api/v1/experiments/{experiment_id}` | Get an experiment plan. | `experiment_planner.get_experiment` |
| POST | `/api/v1/experiments/{experiment_id}/execute` | Create an execution record. | `execution_record.create_execution` |

### H.9 Executions

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| POST | `/api/v1/executions/{execution_id}/observations` | Record an observation. | `execution_record.record_observation` |
| POST | `/api/v1/executions/{execution_id}/finalize` | Finalize + assess + emit delta. | `execution_record.finalize_execution` |
| GET | `/api/v1/executions/{execution_id}` | Get execution record. | `execution_record.get_execution` |

### H.10 Hypotheses

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| GET | `/api/v1/hypotheses/{hypothesis_id}/evidence-deltas` | Get ordered belief-change trail. | `execution_record.get_evidence_deltas` |

### H.11 Future Intelligence (G06)

| Method | Route | Purpose | Service |
|--------|-------|---------|---------|
| POST | `/api/v1/future/signals` | Identify current signals. | `future_signal.identify_future_signals` |
| POST | `/api/v1/future/scenarios` | Construct + persist a scenario. | `scenario_builder.build_scenario` |
| GET | `/api/v1/future/scenarios/{scenario_id}` | Get a scenario. | `scenario_builder.get_scenario` |
| POST | `/api/v1/future/scenarios/{scenario_id}/evaluate` | Evaluate (7 dimensions). | `scenario_evaluator.evaluate_scenario` + `scenario_revision.persist_evaluation_snapshot` |
| POST | `/api/v1/future/scenarios/{scenario_id}/prototype-plan` | Plan a prototype (Mode A/B). | `prototype_planner.plan_prototype` |
| POST | `/api/v1/future/scenarios/{scenario_id}/reassess` | Reassess (atomic). | `scenario_revision.reassess_scenario` |
| GET | `/api/v1/future/scenarios/{scenario_id}/revisions` | Revision history (newest first). | `scenario_revision.get_scenario_revisions` |

### H.12 Static catalogs

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/v1/capabilities` | Static capability catalog (4 entries). |
| GET | `/api/v1/providers` | Static provider catalog (6 entries; only arxiv + http-fetch wired). |

### H.13 System

| Method | Route | Purpose | Status |
|--------|-------|---------|--------|
| GET | `/api/v1/system/activity-mode` | Read activity mode. | IMPLEMENTED (returns `passive` / `assisted` / `autonomous`). |
| PUT | `/api/v1/system/activity-mode` | Update activity mode. | 501 Not Implemented. |
| GET | `/api/v1/jobs/{job_id}` | Get job status. | 404 stub (G01 contract only). |
| POST | `/api/v1/jobs/{job_id}/cancel` | Cancel a job. | 501 Not Implemented. |

### H.14 Validation behavior summary

- All `POST` endpoints use Pydantic v2 request models. Invalid
  payloads → 422 `ValidationError`.
- Unknown entity/relationship/claim/scenario IDs → 404
  `NotFoundError`.
- Invalid `trigger` for `/reassess` → 422.
- Invalid `horizon_class_override` (upgrading F1→F0) → 422.
- F1 fail-closed (no honest integration dependency) → 422
  `ValueError("F1 scenario requires at least one honestly unresolved
  integration dependency")`.
- Conflicting retry (same `run_id` + different payload) → 422
  `ValueError("Conflicting retry...")`.

### H.15 OpenAPI schema verification

The OpenAPI 3.1 schema is auto-generated by FastAPI and is
verified by `make openapi-check`. The schema is at
`/api/v1/openapi.json`. Total routes: 43 (2 health + 41 /api/v1).

`[VERIFIED_FROM_CODE — make openapi-check]`

---

## Section I — Tests, Acceptance, and Production Limitations

### I.1 Latest G01–G06 acceptance status

| Group | Status | Authoritative SHA | Closure report |
|-------|--------|-------------------|----------------|
| G01 | CONDITIONALLY ACCEPTED | `7a9088a` | `reports/GROUP_01_CLOSURE.md`, ADR-0007 |
| G02 | CONDITIONALLY CLOSED | `999f4a0` | `reports/GROUP_02_FINAL_QUALIFICATION.md`, ADR-0010 |
| G03 | FUNCTIONALLY CLOSED WITH LIMITATIONS | `046443a` | `reports/G03_T05_FINAL_INTEGRATION_REPORT.md`, ADR-0011 |
| G04 | FUNCTIONALLY CLOSED WITH LIMITATIONS | `9449fa7` | `reports/G04_T05_EXTERNAL_CLIENT_COMPATIBILITY.md` |
| G05 | FUNCTIONALLY CLOSED | `3d343b8` | `reports/G05_T07_END_TO_END_ACCEPTANCE.md` |
| G06 | FUNCTIONALLY COMPLETE | `435fda1` (post-T04A) | `reports/G06_FINAL_CLOSURE_REPORT.md` |

`[REPORTED_BY_TESTS — closure reports above]`

### I.2 GLM-reported closure baseline for G06

These are **historical reported results**, NOT newly executed
tests in this handoff mission:

- **Final SHA (post-T04A)**: `435fda1332a3fdc5f531f34d03553df3054c0fae`
- **Full deterministic regression**: 763 passed, 44 skipped (PG-only
  without `SYNAPSE_PG_TEST_URL`), 3 deselected (live) in 187.85s.
- **Combined PostgreSQL acceptance**: 169 passed in 89.77s.
  Files: PRB-03 concurrency + determinism + migration safety +
  G05-T06 audit concurrency + G06-T02 + G06-T03 + G06-T04+T04A.
- **Ruff**: PASS.
- **OpenAPI**: PASS.

`[REPORTED_BY_TESTS — reports/G06_T04_REVISION_E2E_ACCEPTANCE.md §18.4]`

### I.3 Test inventory summary

- **34 integration test files** under `tests/integration/` totalling
  ~26,391 LOC.
- **Test isolation**: Each PG test module gets its own database
  (`synapse_t_<module_label>_<random>`) via `pg_engine` fixture.
- **PG-only tests** are skipped without `SYNAPSE_PG_TEST_URL`.
- **Live tests** (network-dependent) are skipped without `-m live`.

`[VERIFIED_FROM_CODE — tests/integration/conftest.py]`

### I.4 Known test exclusions

- All PG-only tests (35 in G06-T04+T04A, plus PRB-03 concurrency,
  determinism, migration safety tests) are skipped without
  `SYNAPSE_PG_TEST_URL`.
- Live tests (arXiv network calls) are skipped by default.

### I.5 Production-readiness blocker register (PRB-01..07)

`[APPROVED_DESIGN — ADR-0011]`

| # | Blocker | Origin | Severity | Resolution path | Status |
|---|---------|--------|----------|-----------------|--------|
| PRB-01 | Real PostgreSQL migration/integration validation | G02 | High | User must run all migrations (0001–0005) against a real disposable PostgreSQL instance. | RESOLVED in PRB-03 permanent closure work (EntityFingerprintRow UNIQUE + advisory locks tested on PG 16.2). |
| PRB-02 | Pinned-IP HTTPS, proxy, IPv6, connection-pooling security review | G02 closure | Medium | Live HTTPS test against a real endpoint with valid certificate. | UNRESOLVED. |
| PRB-03 | Database-level concurrency/idempotency guarantees | G03-T02 | Medium | UNIQUE constraints on `(kind, fingerprint)` + advisory locks. | RESOLVED (EntityFingerprintRow + `pg_advisory_xact_lock` pattern, G06-T02A + T04A). |
| PRB-04 | Evidence-origin independence limitations | G03-T04 | Medium | `_count_independent_primary_origins` always returns 1 (conservative). | UNRESOLVED (intentionally conservative). |
| PRB-05 | Stronger VERIFIED review policy | G03-T04 | Expected | VERIFIED is unreachable in deterministic-v2 by design. Future policy version (deterministic-v3 with human-review integration or experimental-validation-v1) can make it reachable. | UNRESOLVED (intentionally unreachable). |
| PRB-06 | Toolkit read-only credential enforcement | G02 closure (ADR-0009 §7) | Medium | The Synapse developer token is write-capable (`repo` + `workflow` OAuth scopes). Future Toolkit audits MUST use a repository-scoped fine-grained PAT with `Contents: Read` only. | UNRESOLVED. |
| PRB-07 | Extractor-version-aware reprocessing | G03-T02 reliability closure | Low | Current idempotency check uses `content_fingerprint` + JobRow status. Future group can add `extractor_version` to the completion check. | UNRESOLVED. |

### I.6 ADR-0011 constraints (carried forward)

- All G01–G03 implementation, tests, reports, contracts, and
  provenance are preserved unchanged.
- No file under `src/synapse/` for G01–G03 may be modified for G04+
  preparation.
- All ADRs (0001–0011), all reports, all audit evidence are
  historical records — never retroactively modified.
- Production deployment blocked until PRB-01 + PRB-03 resolved
  (BOTH NOW RESOLVED via PRB-03 permanent closure).
- VERIFIED remains unreachable until PRB-05 addressed.

### I.7 Distinction: functional completion vs. production readiness

**G06 is functionally complete.** All 4 tasks + 4 corrections PASS.
The end-to-end lifecycle is demonstrated by real-service,
database-persisted acceptance tests.

**G06 is NOT production-ready.** Production deployment requires
resolving the unresolved PRBs (PRB-02, PRB-04, PRB-05, PRB-06,
PRB-07) — these are OUT of G06 scope. They are project-level
blockers that pre-date G06 and remain the responsibility of a
future production-readiness group.

`[APPROVED_DESIGN — ADR-0011; reports/G06_FINAL_CLOSURE_REPORT.md §10]`

### I.8 Documentation-vs-implementation discrepancies

During the preparation of this handoff, the following discrepancies
were checked and confirmed RESOLVED:

1. **PRB-03 status**: ADR-0011 lists PRB-03 as an unresolved
   blocker. The PRB-03 permanent closure work (`reports/PRB_03_PERMANENT_CLOSURE.md`,
   `reports/PRB_03_FINAL_CLOSURE_AUDIT.md`) and the G06-T02A +
   G06-T04A implementation resolve it. The ADR-0011 text is
   historical and not retroactively modified (per ADR-0011
   preservation requirements). `[APPROVED_DESIGN — ADR-0011 §"Preservation requirements"]`

2. **No other discrepancies found.** The implementation, tests,
   reports, and ADRs are consistent.

---

## Section J — G07 Readiness and UX Review Inputs

This section prepares a structured foundation for reviewing G07.
It identifies open UX questions WITHOUT deciding them. **It does
NOT design or implement G07.**

### J.1 Existing G07 plans and their approval status

**No G07 plan, design, or implementation exists in the repository.**

`[VERIFIED_FROM_CODE — repo glob `**/G07*` returns 0 matches;
repo glob `**/*g07*` returns 0 matches; no `docs/decisions/0012-*`
ADR; no `docs/g07/` directory; no `frontend/` or `web/` directory
under `src/`.]`

G07 is mentioned only as a forward-looking dependency in:
- `reports/G06_FINAL_CLOSURE_REPORT.md` (lines 8–12, 371–389, 434)
  — STOP gate + 4 informational candidate areas.
- `reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md` (§5.3, §12.2) —
  stability contract for G07 UI consumers + dependency notes.
- `reports/G06_FUTURE_INTELLIGENCE_IMPLEMENTATION_PLAN.md`
  (lines 1283, 1573) — "G07 frontend — out of scope" + STOP gate.
- `reports/G06_T04_REVISION_E2E_ACCEPTANCE.md` (lines 14–16,
  609–610, 805) — STOP gate verbatim.

**G07_EXISTING_PLAN_STATUS = NOT_FOUND.**

`[VERIFIED_FROM_CODE — see search above]`

### J.2 Informational candidate G07 areas (NOT commitments)

`[PROPOSED_FOR_G07 — reports/G06_FINAL_CLOSURE_REPORT.md §12]`

1. **Production-readiness hardening**: external provider integrations,
   rate-limiting, observability, deployment pipeline.
2. **Optional: dedicated `scenario.evaluation_snapshot` audit event
   type** (promote `last_evaluation` from attributes JSON to a
   first-class audit row).
3. **Optional: background reassessment scheduler** (per G06 plan §4.5
   note: "A future group, with explicit authorization, may add a
   worker that calls the same reassess endpoint on a schedule").
4. **Optional: production-grade verification policy** (current
   `assess_claim` policy is `POLICY_VERSION` 1 — a future group may
   refine, possibly making VERIFIED reachable via human-review
   integration or experimental-validation-v1).

**These are NOT commitments. The reviewer decides.**

### J.3 Backend capabilities that could be exposed in the UI

(see §D — Current Functional Capabilities — for the full inventory)

The UI must expose at minimum:
- Source discovery + ingestion (§D.1).
- Entity/relationship/claim browsing + evidence viewing (§D.3).
- Capability catalog + gap analysis (§D.4).
- Reasoning queries (§D.5).
- Innovation generation + critique (§D.6).
- Experiment planning + execution + observation + finalize (§D.7, D.8).
- Future signals + scenario construction + evaluation (§D.9, D.10, D.11).
- Prototype planning (§D.12).
- Scenario reassessment + revision history (§D.13, D.14).

### J.4 Candidate user workflows

(see §E — User Workflows — for the full list)

The 9 workflows documented in §E are the candidate user journeys
G07 should support. Each workflow has documented backend steps +
required user inputs + available outputs + missing UI capabilities
+ unsupported steps.

### J.5 Relevant API dependencies

(see §H — API and Integration Reference)

The UI consumes the existing `/api/v1/*` contracts. **No BFF shim**.
`[PROPOSED_FOR_G07 — G05 plan §12.2]`

Key dependencies (per G05 §5.3 stability contract):
- `data.concepts[].components[].entity_id`
- `evidence_refs[]`
- `feasibility_score`
- `overall_recommendation`
- `evidence_deltas[]`

Field additions are forward-compatible; removals require `/api/v2`.

### J.6 Evidence/provenance presentation requirements

The UI must preserve the epistemic policies documented in §F:

- Distinguish "evidence exists" from "evidence is verified" (§F.1).
- Always show source URI + retrieved_at for any evidence (§F.2).
- Use "single source" / "multiple sources" labels — never
  "verified independent" without explicit policy (§F.3).
- Show confidence_value alongside confidence_method (§F.4).
- Encourage explicit assumption-to-claim associations (§F.5).
- NEVER show a "Verified" label — strongest is "Supported" (§F.6).
- Distinguish experiment plan / execution / observation / finalized /
  hypothesis verified (§F.7).
- Show F0–F4 horizon label prominently; NO time estimates (§F.8).
- Show each dependency's classification (research / engineering /
  integration / caller_attested / unknown) (§F.9).
- Show full revision history as a timeline; no "delete revision"
  affordance (§F.10).
- Surface idempotent-retry semantics (§F.11).
- Show loading indicator during reassess (concurrent safety, §F.12).

### J.7 Scenario and experiment visualization requirements

- Scenario evaluation dashboard with 7-axis breakdown
  (evidence_coverage, distinct_source_uri_count,
  independent_origin_count, dependency_readiness,
  contradiction_exposure, assumption_assessment, horizon_alignment,
  overall).
- Scenario horizon badge (F0–F4) with explanation tooltip.
- Revision timeline with prior/new snapshot comparison.
- Experiment plan preview with success/failure thresholds.
- Execution-record timeline with observations + assessment.
- Evidence-delta timeline for hypothesis belief changes.
- Innovation concept card with components + evidence_refs +
  integration_mechanism + potential_benefit.
- Innovation critique dashboard with feasibility/evidence-coverage/
  novelty/failure-modes/alternatives/overall-recommendation.

### J.8 Human review and approval points

- F1 fail-closed: UI must surface `ValueError("F1 scenario requires
  at least one honestly unresolved integration dependency")` clearly
  and prompt the user to add a real dependency.
- F4 scenarios require a critique — UI must enforce this before
  persistence.
- Mode A prototype planning requires caller-supplied metrics — UI
  must provide a metric-schema builder.
- Production-mode experiments require `safety_limits.approved_by` —
  UI must provide an approval workflow (currently no UI exists).
- Networked-mode experiments require `cost_limits.approved_by` —
  UI must provide an approval workflow (currently no UI exists).
- Reassessment trigger selection: UI must let the user pick the
  trigger (`explicit_reassess` / `new_evidence` /
  `new_experiment_result`) and the trigger_ref.

### J.9 Error and uncertainty presentation requirements

- Standard `application/problem+json` errors must be rendered with
  code, message, details, retryable, trace_id, request_id.
- `uncertainty` must be shown alongside `uncertainty_method` and
  `uncertainty_limitation` (the disclaimer that uncertainty measures
  ONLY the evidence-coverage gap, NOT a probability of future
  occurrence or experimental success).
- Contradiction exposure must be shown as a count + drill-down to
  the contradicting claims.
- Assumption assessment must show per-assumption classification
  (supported / contested / not_evidenced / unknown) with the
  associated claim ID.

### J.10 Security and authentication considerations

- The UI must consume authenticated endpoints (`PrincipalDep` on all
  `/api/v1/*` except `/health/*`).
- The DevelopmentAuthAdapter uses env-based API keys
  (`SYNAPSE_ADMIN_API_KEYS`, `SYNAPSE_DEV_API_KEYS`). For local
  development, the UI must accept an API key (e.g., via a header or
  settings panel).
- The ProductionAuthAdapter fails closed unless
  `SYNAPSE_AUTH_PROVIDER` is configured. **No production IdP is
  wired.** G07 must NOT invent its own auth — it must defer to the
  backend's `PrincipalDep`. `[APPROVED_DESIGN — ADR-0003]`
- Scopes: `reader` / `researcher` / `editor` / `operator` / `admin`
  (hierarchy). Currently no per-route scope-tier restriction
  exists, but the UI may want to gate certain actions (e.g.,
  `/system/activity-mode PUT`) behind `operator`+ scope. This is
  a UX decision, not a backend requirement.

### J.11 Known backend limitations that could affect UX

- No LLM-assisted extraction / reasoning (deterministic only).
- No background job scheduler (synchronous ingestion only).
- No `VERIFIED` epistemic state (max is `SUPPORTED`).
- No time estimates for F0–F4 horizons.
- No "revert revision" endpoint (revisions are append-only).
- No background reassessment scheduler (explicit-only).
- Innovation generation is non-deterministic in WHICH combination
  of components it picks first (the UI must handle varying concept
  sets across calls).
- Production-mode / networked-mode experiments require approval
  fields that have no UI to set.
- Calibration status (`operational` vs. `requires_calibration`)
  currently requires direct DB manipulation (no API endpoint to
  set thresholds).

### J.12 Open UX questions (NOT decided here)

1. Should the UI be a single-page application (SPA) or a multi-page
   app? (No decision — reviewer's prerogative.)
2. Should the UI use a frontend framework (React, Vue, Svelte) or
   server-rendered templates (Jinja2, HTMX)? (No decision.)
3. Should the UI be served by the FastAPI app (static files) or a
   separate static-hosting service? (No decision.)
4. Should the UI support real-time updates (WebSocket) for
   long-running operations (ingestion, reassessment)? Currently
   the backend is synchronous, so this is moot unless a background
   scheduler is added. (No decision.)
5. Should the UI expose a "compare two scenarios" view? (Backend
   supports this via `/revisions` but no direct comparison API.)
6. Should the UI expose the `find_paths` graph-traversal function?
   (Currently no API endpoint exposes this — only reasoning does
   internally.)
7. Should the UI expose `assess_claim` directly (a "verify this
   claim" button)? (Currently no `/api/v1/claims/{id}/assess`
   endpoint exists.)
8. Should the UI expose the static capability/provider catalogs
   (`/api/v1/capabilities`, `/api/v1/providers`)? (Currently
   static, not editable.)
9. Should the UI include a "what-if" scenario builder that
   pre-validates horizon classification before persistence?
10. Should the UI include a "knowledge graph explorer" with
    interactive node/edge visualization?

**These questions are NOT answered here. The reviewer decides.**

---

## Section K — NEW_CHAT_STARTER — G07 REVIEW

Copy the text below into a fresh ChatGPT conversation to initialize
the G07 review. Do not edit this section except to update the
authoritative SHA after future commits.

```text
NEW_CHAT_STARTER — G07 REVIEW

You are the reviewer for AgentCraft Synapse's G07 (user interface
and interaction experience) stage. The developer (GLM) is NOT
authorized to implement G07 until you produce a reviewed G07
implementation plan AND the user explicitly approves it.

BEFORE ANYTHING ELSE:

1. Read the master handoff document:
   docs/handoffs/AgentCraft_Synapse_G01-G06_Master_Handoff_for_G07_Review.md
   This is your primary context. It contains:
   - Section A: Product identity and vision.
   - Section B: Architectural overview (with Mermaid diagram).
   - Section C: G01–G06 implementation history (with attention
     to G06 corrections T01A, T02A, T03A, T04A).
   - Section D: Current functional capability inventory.
   - Section E: User workflows (9 workflows, each with backend
     steps, required inputs, outputs, missing UI capabilities,
     and unsupported steps).
   - Section F: Data and epistemic integrity rules (12 policies
     the UI MUST preserve).
   - Section G: Frozen architectural decisions (ADRs 0001–0011,
     frozen contracts, prohibited substitutions, components that
     must be reused, integration boundaries, security/audit
     requirements, decisions G07 must not reopen).
   - Section H: API and integration reference (43 routes).
   - Section I: Tests, acceptance, production limitations, and
     the PRB-01..07 blocker register.
   - Section J: G07 readiness + UX review inputs + open UX
     questions (NOT decided).
   - Section K: This starter text.

2. Treat the repository and the approved reports as authoritative.
   - The repository is at: https://github.com/mayakilzy/AgentCraft_Synapse.git
   - The authoritative HEAD is: 435fda1332a3fdc5f531f34d03553df3054c0fae
   - Clone it locally if you need to inspect code:
     git clone https://github.com/mayakilzy/AgentCraft_Synapse.git
   - Approved ADRs: docs/decisions/0001-*.md through 0011-*.md.
   - Closure reports: reports/GROUP_01_*.md, reports/G02_*.md,
     reports/G03_*.md, reports/G04_*.md, reports/G05_*.md,
     reports/G06_*.md, reports/PRB_03_*.md.

3. Preserve frozen G01–G06 decisions.
   - The modular monolith (ADR-0001) — no microservices, no swarm.
   - SQLite test dialect (ADR-0002) — production uses PostgreSQL.
   - Local dev auth, fail-closed production (ADR-0003).
   - SSRF denylist by default (ADR-0004).
   - Pydantic v2 typed domain contracts (ADR-0005) — FROZEN, no
     field removal/renaming without /api/v2.
   - API-first contract (ADR-0006) — /api/v1, problem+json, OpenAPI 3.1.
   - The 16-predicate relationship vocabulary — no new predicates.
   - The EpistemicState enum — NO VERIFIED value (PRB-05).
   - The uncertainty = 1.0 − evidence_coverage formula (G06 §16.3).
   - The 7-dimension scenario evaluation axes.
   - The 8 material-change fields for revision detection.
   - The "explicit-over-autonomous" principle — no background
     scheduler.

4. Review the existing G07 proposal, if one exists.
   - As of this handoff: NO G07 plan, design, or implementation
     exists in the repository. The G06 final closure report lists
     4 INFORMATIONAL candidate areas (production-readiness
     hardening, optional dedicated audit event type, optional
     background reassessment scheduler, optional production-grade
     verification policy). These are NOT commitments.
   - If a G07 proposal has been added since this handoff was
     written, read it and assess whether it respects the frozen
     decisions in step 3.

5. Discuss the following with the user:
   - User journeys: which of the 9 workflows in §E are essential
     vs. optional for the first G07 release?
   - Information architecture: how should the UI organize the
     14 capability groups in §D?
   - Interaction patterns: how should the user move between
     source discovery → knowledge graph → reasoning → innovation
     → experiment → scenario → reassessment?
   - Visual presentation: how should the UI render the F0–F4
     horizon classifications, the 7-axis evaluation dashboard,
     the revision timeline, the evidence-delta timeline, the
     assumption_associations editor, the gap-analysis matrix?
   - Essential UI features vs. optional enhancements.
   - API/UI gaps and risks (e.g., no `/api/v1/claims/{id}/assess`
     endpoint, no `find_paths` endpoint, no calibration_status
     endpoint).
   - Avoid unnecessary infrastructure or frontend complexity
     (no BFF shim per G05 plan §12.2; the UI consumes /api/v1/*
     directly).
   - Authentication approach (defer to backend PrincipalDep;
     do not invent UI-side auth).

6. Produce a reviewed G07 implementation plan BEFORE authorizing
   development. The plan must:
   - Identify the UI technology choice (with rationale).
   - Identify the essential vs. optional user journeys.
   - Identify the API/UI gaps that must be closed (and whether
     they're in G07 scope or require a backend amendment).
   - Identify the security and audit requirements (per §G.6).
   - Identify the evidence/provenance presentation requirements
     (per §F and §J.6).
   - Identify the open UX questions from §J.12 that need user
     decisions.
   - NOT include any code implementation — that requires a
     separate G07 implementation authorization after the user
     approves the plan.

7. DO NOT authorize GLM to implement G07 until the user explicitly
   approves the plan. Mirror the gating pattern established by
   ADRs 0007, 0009, 0010, 0011 and the G05/G06 STOP gates.

8. If you identify a discrepancy between this handoff and the
   actual repository state, record it explicitly and ask the user
   to resolve it before proceeding.

Authoritative starting point: the master handoff document at
docs/handoffs/AgentCraft_Synapse_G01-G06_Master_Handoff_for_G07_Review.md
on branch `main` at commit 435fda1332a3fdc5f531f34d03553df3054c0fae.

STOP. Wait for the user to acknowledge before proceeding.
```

---

## Source Inventory

This section lists the main documents and code files consulted in
the preparation of this handoff.

### ADRs (Approved Design)

- `docs/decisions/0001-modular-monolith.md`
- `docs/decisions/0002-sqlite-test-dialect.md`
- `docs/decisions/0003-local-dev-auth-fail-closed.md`
- `docs/decisions/0004-ssrf-denylist.md`
- `docs/decisions/0005-domain-pydantic-v2.md`
- `docs/decisions/0006-api-first-contract.md`
- `docs/decisions/0007-g01-conditional-acceptance.md`
- `docs/decisions/0008-audit-policy-amendment.md`
- `docs/decisions/0009-g02-audit-review-clarifications.md`
- `docs/decisions/0010-g02-conditional-closure.md`
- `docs/decisions/0011-g03-functional-closure.md`

### Closure reports (Reported by Tests)

- `reports/GROUP_01_REPORT.md`, `reports/GROUP_01_CLOSURE.md`,
  `reports/GROUP_01_EVIDENCE.json`
- `reports/GROUP_02_AUDIT_*.md`, `reports/GROUP_02_SLICE_REPORT.md`,
  `reports/GROUP_02_FINAL_QUALIFICATION.md`,
  `reports/GROUP_02_SECURITY_DB_CLOSURE.md`
- `reports/G03_MINIMAL_IMPLEMENTATION_PLAN.md`,
  `reports/G03_T01_*_REPORT.md` through `reports/G03_T05_*.md`,
  `reports/G03_T04_VERIFICATION_POLICY_CORRECTION.md`
- `reports/G04_MINIMAL_IMPLEMENTATION_PLAN.md`,
  `reports/G04_T01_*_REPORT.md` through `reports/G04_T05_*.md`,
  `reports/G04_T02C_*.md`, `reports/G04_T03C_*.md`,
  `reports/G04_T04C_*.md`
- `reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md`,
  `reports/G05_T01_*.md` through `reports/G05_T07_*.md`,
  `reports/G05_T03C_*.md`, `reports/G05_T05C_*.md`
- `reports/G06_FUTURE_INTELLIGENCE_IMPLEMENTATION_PLAN.md`,
  `reports/G06_T01_*.md` through `reports/G06_T04_*.md`,
  `reports/G06_FINAL_CLOSURE_REPORT.md`
- `reports/PRB_03_PERMANENT_CLOSURE.md`,
  `reports/PRB_03_FINAL_CLOSURE_AUDIT.md`,
  `reports/PRB_03_FINAL_CLOSURE_AUDIT.md`
- `reports/SYNAPSE_G04_SESSION_HANDOVER.md`,
  `reports/TEST_INFRA_01_REGRESSION_OPTIMIZATION.md`

### Source code (Verified from Code)

- `src/synapse/main.py` — FastAPI app factory.
- `src/synapse/api/v1/router.py` — aggregator.
- `src/synapse/api/v1/*.py` — 14 router modules.
- `src/synapse/application/*.py` — 17 service modules.
- `src/synapse/domain/*.py` — 15 domain record modules.
- `src/synapse/storage/models.py` — 10 ORM tables.
- `src/synapse/storage/fingerprint.py` — `claim_fingerprint`.
- `src/synapse/security/*.py` — auth + SSRF + http_transport.
- `src/synapse/providers/*.py` — 3 wired providers.
- `src/synapse/workers/__init__.py` — empty placeholder.
- `tests/integration/conftest.py` — PG-isolation fixtures.
- `tests/integration/test_*.py` — 33 test files.
- `alembic/versions/0001_*.py` through `0005_*.py` — 5 migrations.
- `pyproject.toml` — project metadata + dependencies.
- `Makefile` — `make test-deterministic`, `make openapi-check`.
- `docs/baseline.md` — repository baseline reconnaissance.
- `NOTICE.md` — third-party attribution + vendored components.

### OpenAI schema (Verified from Code)

- `/api/v1/openapi.json` — auto-generated by FastAPI, 43 routes.

---

## Final Report Block

```
HANDOFF_STATUS = PASS

AUTHORITATIVE_START_SHA = 435fda1332a3fdc5f531f34d03553df3054c0fae

G01_G06_COVERAGE = COMPLETE

ARCHITECTURE_DOCUMENTED = YES

CAPABILITY_INVENTORY = COMPLETE

API_INVENTORY_VERIFIED = YES

FROZEN_DECISIONS_CAPTURED = YES

EPISTEMIC_POLICIES_CAPTURED = YES

PRODUCTION_BLOCKERS_DOCUMENTED = YES

G07_EXISTING_PLAN_STATUS = NOT_FOUND

NEW_CHAT_STARTER_INCLUDED = YES

DOCUMENT_PATH = docs/handoffs/AgentCraft_Synapse_G01-G06_Master_Handoff_for_G07_Review.md

DOCUMENT_LINE_COUNT = 2582

SOURCE_REFERENCES =
  ADRs 0001-0011 (docs/decisions/)
  Closure reports (reports/GROUP_01_*.md, reports/G02_*.md, ...,
    reports/G06_*.md, reports/PRB_03_*.md)
  Source code (src/synapse/api/v1/*.py, src/synapse/application/*.py,
    src/synapse/domain/*.py, src/synapse/storage/models.py,
    src/synapse/security/*.py, src/synapse/providers/*.py,
    src/synapse/workers/__init__.py)
  Test infrastructure (tests/integration/conftest.py,
    tests/integration/test_*.py)
  Migrations (alembic/versions/0001-0005)
  pyproject.toml, Makefile, docs/baseline.md, NOTICE.md
  OpenAPI schema (/api/v1/openapi.json, 43 routes)

MODIFIED_FILES =
  docs/handoffs/AgentCraft_Synapse_G01-G06_Master_Handoff_for_G07_Review.md (NEW)

FINAL_SHA = (populated after commit)

READY_FOR_G07_REVIEW_CHAT = YES
```

---

**Commit, push, verify `origin/main == HEAD`, and STOP.**

**Do not begin G07 development.**
