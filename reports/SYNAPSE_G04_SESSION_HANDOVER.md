# SYNAPSE G04 SESSION HANDOVER

> **Purpose**: Authoritative checkpoint for a fresh GLM conversation to
> continue G04 development without relying on chat history.
>
> **Created**: 2026-10-09
> **Checkpoint SHA**: `9449fa7` (pending handover commit)

## 1. Project identity

| Field | Value |
|-------|-------|
| Project | AgentCraft Synapse |
| Repository | `https://github.com/mayakilzy/AgentCraft_Synapse.git` |
| Branch | `main` |
| Checkpoint SHA | `9449fa7` (will update after this commit) |
| Remote sync | ✅ Pushed to `origin/main` |
| Working tree | Clean — no uncommitted changes |

## 2. Objectives and architecture

**AgentCraft Synapse** is a relationship-aware knowledge, hypothesis &
future-intelligence engine. It discovers evidence, extracts structured
knowledge, models relationships and capabilities, reasons across them,
proposes innovations, critiques hypotheses, designs experiments, updates
beliefs from results, and explores evidence-grounded future scenarios.

**Architecture**: Modular monolith (FastAPI + SQLAlchemy 2.0 + Pydantic v2).
PostgreSQL in production, SQLite in tests. No graph database, no Redis,
no message broker. API-first (`/api/v1`). 11 ADRs (0001–0011) document
binding decisions and reversible defaults.

## 3. Group status summary

### G01 — Foundation (PASS)

| Item | Status |
|------|--------|
| 12 typed domain records with invariants | ✅ |
| 6 ORM tables (sources, jobs, idempotency_keys, audit_events, capabilities, providers) | ✅ |
| FastAPI app with OpenAPI 3.1, auth, SSRF guard, error envelope | ✅ |
| 4 migrations (0001–0004), ruff/pytest CI | ✅ |
| Acceptance: 140 tests pass | ✅ |

### G02 — Acquisition (FUNCTIONAL PASS WITH LIMITATIONS)

| Item | Status |
|------|--------|
| 3 providers: arxiv_search (vendored), trafilatura_extractor (pip), content_delta_hash (vendored) | ✅ |
| EvidenceFragment table + provenance fields | ✅ |
| SSRF guard with IP-pinning transport (DNS-rebinding defense) | ✅ |
| POST /sources/discover, POST /sources/ingest | ✅ |
| PostgreSQL migration validation | ⛔ BLOCKED (PRB-01) |

### G03 — Knowledge & Verification (FUNCTIONAL PASS WITH LIMITATIONS)

| Task | Title | Status |
|------|-------|--------|
| G03-T01 | Typed knowledge extraction (deterministic regex + extensible contract) | ✅ PASS |
| G03-T02 | Canonicalization + idempotent persistence (JobRow-based, reliability-closed) | ✅ PASS |
| G03-T03 | RelationshipService (typed, directed, bounded traversal, contradictions) | ✅ PASS |
| G03-T04 | Verification engine (deterministic-v2, conservative, VERIFIED unreachable) | ✅ PASS (corrected) |
| G03-T05 | Final integration (knowledge API + end-to-end demo) | ✅ PASS WITH LIMITATIONS |

**Implemented capabilities**:
- 9 typed knowledge units (Identifier, Capability, Technique, Constraint, Tradeoff, FailureMode, Applicability, Opportunity, Claim)
- SourceSpan with precise char offsets + context
- 16-predicate relationship vocabulary (G01, unchanged)
- RelationshipService: create, find_related, find_paths (BFS), find_capabilities, find_dependencies, find_alternatives, find_limitations, find_missing_capabilities, find_contradictions
- Verification engine: assess_claim, SOURCE_SUPPORTED / CORROBORATED / CONTESTED / INSUFFICIENT_EVIDENCE / STALE / NOT_EVIDENCED
- EvidenceDelta: immutable audit of assessment changes
- Knowledge API: 6 endpoints (entities, relationships, claims/evidence, search)
- 338 deterministic tests pass + 3 live tests

### G04 — Retrieval & Reasoning (PLAN APPROVED, NOT IMPLEMENTED)

**Architecture plan**: `reports/G04_MINIMAL_IMPLEMENTATION_PLAN.md`

| Task | Objective |
|------|-----------|
| G04-T01 | Hybrid retrieval (lexical + metadata + graph + evidence-aware reranking) |
| G04-T02 | Capability registry + gap analysis (alternatives, tradeoffs, missing caps) |
| G04-T03 | Grounded reasoning (POST /reasoning/queries, citation chains, injection isolation) |
| G04-T04 | Evaluation + cost routing (golden queries, precision/recall, grounding) |
| G04-T05 | External client compatibility (OpenAPI, CORS, sample client) |

**First vertical slice**: G04-T01 — hybrid retrieval demonstrating grounded
technical answer with traceable evidence.

## 4. G04-T01 exact approved scope

**File**: `src/synapse/application/retrieval.py` (~250 LOC)

```python
async def hybrid_retrieve(
    session, query, *, entity_kind=None, predicate=None,
    epistemic_state=None, max_depth=3, limit=20
) -> RetrievalResult
```

Returns: claims + evidence bundles + relationships + reranking factors + unknowns.

**Acceptance criteria**:
1. Golden queries recover expected claims (precision ≥ 0.8)
2. Stable pagination (deterministic ordering)
3. Empty results truthful (no fabricated hits)
4. Reranking respects verification outcomes
5. Evidence bundle includes source spans with correct offsets
6. G01/G02/G03 regression (all 338 tests pass)

**Estimated**: ~450 LOC (source + tests), 0 new pip deps, 0 new migrations.

## 5. Existing API, domain contracts and migrations

### API endpoints (implemented)

| Group | Endpoints |
|-------|----------|
| G01 | `/health/live`, `/health/ready`, `/api/v1/capabilities`, `/api/v1/providers`, `/api/v1/system/activity-mode`, `/api/v1/jobs/{id}`, `/api/v1/jobs/{id}/cancel` |
| G02 | `POST /api/v1/sources/discover`, `POST /api/v1/sources/ingest`, `GET /api/v1/sources`, `GET /api/v1/sources/{id}`, `GET /api/v1/sources/{id}/acquisitions` |
| G03 | `GET /api/v1/entities`, `GET /api/v1/entities/{id}`, `GET /api/v1/relationships`, `GET /api/v1/relationships/{id}`, `GET /api/v1/claims/{id}/evidence`, `POST /api/v1/knowledge/search` |

### 501 placeholders (G04+)

`POST /api/v1/reasoning/queries`, `POST /api/v1/innovations/generate`,
`POST /api/v1/innovations/{id}/critique`, `POST /api/v1/experiments`,
`POST /api/v1/experiments/{id}/execute`, `GET /api/v1/experiments/{id}`,
`GET /api/v1/hypotheses/{id}/evidence-deltas`, `POST /api/v1/future/scenarios`,
`GET /api/v1/future/scenarios/{id}`, `POST /api/v1/future/scenarios/{id}/prototype-plan`

### Migrations

| Migration | Tables |
|-----------|--------|
| 0001_initial | sources, jobs, idempotency_keys, audit_events, capabilities, providers |
| 0002_evidence_fragments | evidence_fragments |
| 0003_knowledge_tables | entities, claims, source_spans |
| 0004_relationships | relationships |

### Domain contracts (G01, frozen)

`DomainRecord` (base), `Source`, `Acquisition`, `EvidenceFragment` +
`Locator`, `Entity` (12 EntityType kinds), `Claim` (5 EpistemicState
values), `Relationship` (16 predicates, Origin enum, VerificationState
enum), `Capability`, `Hypothesis`, `Experiment`, `EvidenceDelta`, `Scenario`,
`Job` (state machine). All invariants enforced by Pydantic v2
`model_validator`.

## 6. Seven production-readiness blockers

| # | Blocker | Severity | Origin |
|---|---------|----------|--------|
| PRB-01 | Real PostgreSQL migration/integration validation | High | G02 |
| PRB-02 | Pinned-IP HTTPS, proxy, IPv6, connection-pooling security review | Medium | G02 closure |
| PRB-03 | Database-level concurrency/idempotency guarantees (UNIQUE constraints) | Medium | G03-T02 |
| PRB-04 | Evidence-origin independence (conservative default=1) | Medium | G03-T04 |
| PRB-05 | Stronger VERIFIED review policy (VERIFIED unreachable in v2) | Expected | G03-T04 |
| PRB-06 | Toolkit read-only credential enforcement (write-capable token) | Medium | ADR-0009 §7 |
| PRB-07 | Extractor-version-aware reprocessing | Low | G03-T02 |

Full details: `docs/decisions/0011-g03-functional-closure.md`

## 7. Important architectural decisions and frozen boundaries

| Boundary | Rule |
|----------|------|
| G01 domain contracts | **FROZEN** — no changes to Pydantic models, enums, or invariants |
| G02 provider scope | **FROZEN** — 3 providers only (arxiv_search, trafilatura, delta_hash) |
| G03 scope | **FROZEN** — no expansion without explicit approval |
| Verification policy | **deterministic-v2** — VERIFIED unreachable; conservative origin independence |
| Graph database | **NOT introduced** — relational tables + BFS traversal suffice |
| AgentCraft-Toolkit | **READ ONLY** — never modify, push, or write to Toolkit repos |

## 8. Existing reports and ADRs to read

### ADRs (`docs/decisions/`)

| ADR | Title |
|-----|-------|
| 0001 | Modular monolith, single backend, single store |
| 0002 | SQLite test dialect (PostgreSQL in production) |
| 0003 | Local dev auth adapter, fail-closed production |
| 0004 | SSRF denylist by default |
| 0005 | Pydantic v2 typed domain contracts |
| 0006 | API-first contract, /api/v1, problem+json errors |
| 0007 | G01 conditional acceptance (D-01/D-02/D-03) |
| 0008 | G02 audit policy amendment (tiered verification) |
| 0009 | G02 audit review clarifications (8 corrections) |
| 0010 | G02 conditional closure |
| 0011 | G03 functional closure + blocker register |

### Key reports

- `reports/G04_MINIMAL_IMPLEMENTATION_PLAN.md` — **READ FIRST for G04**
- `reports/G03_T05_FINAL_INTEGRATION_REPORT.md` — G03 end-to-end demo
- `reports/G03_T04_VERIFICATION_POLICY_CORRECTION.md` — deterministic-v2 policy
- `reports/GROUP_02_SECURITY_DB_CLOSURE.md` — SSRF IP-pinning + DNS rebinding
- `docs/baseline.md` — pre-G01 repository baseline
- `docs/toolkit_audit/README.md` — G02 toolkit audit scope
- `docs/toolkit_audit/CORRECTED_CREDENTIAL_STATEMENT.md` — credential correction

## 9. Exact next execution step

1. Read this handover document.
2. Read `reports/G04_MINIMAL_IMPLEMENTATION_PLAN.md` (the approved plan).
3. Read ADR-0011 (G03 closure + blocker register).
4. Implement **G04-T01 only** (hybrid retrieval) after receiving explicit
   user authorization.
5. Run all 338 existing tests to confirm no regression.
6. Deliver `G04_T01_IMPLEMENTATION_REPORT.md` and STOP.

## 10. STOP conditions

- Do NOT implement G04-T02 through G04-T05 without per-task approval.
- Do NOT begin GROUP_05 (Innovation) or GROUP_06 (Future Intelligence).
- Do NOT modify AgentCraft-Toolkit or its subrepositories.
- Do NOT introduce a graph database, vector database, or message broker.
- Do NOT use the write-capable Synapse token to access the Toolkit.
- Do NOT claim PostgreSQL production readiness (PRB-01 unresolved).
- Do NOT auto-promote hypotheses to VERIFIED (deterministic-v2).

## 11. Toolkit protection policy

**AgentCraft-Toolkit** (`https://github.com/mayakilzy/AgentCraft-Toolkit`)
is **PRIVATE STRATEGIC INTELLECTUAL PROPERTY**.

- The Toolkit and all its subrepositories are **STRICTLY READ ONLY**.
- Never write, commit, push, tag, or modify Toolkit originals.
- Only the authorized Toolkit modification agent, under Master Agent
  governance, may modify them.
- Any approved integration must be implemented **inside the Synapse
  repository only**.
- The write-capable Synapse developer token must NOT be used for Toolkit
  access (per ADR-0009 §7). Future Toolkit audits must use a
  repository-scoped read-only PAT.

## 12. Test results at checkpoint

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 105 files formatted |
| `pytest` (deterministic) | ✅ 338 passed, 0 failed, 3 skipped (live) |

---

*End of Synapse G04 Session Handover — STOP.*
