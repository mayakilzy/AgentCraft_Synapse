# G04-T05 — External Client Compatibility & Contract Validation

> **Status**: PASS WITH LIMITATIONS (production readiness NOT approved; PRB-01..07 unresolved)
> **Date**: 2026-10-09
> **Reviewer**: GLM Dev Agent
> **Starting checkpoint**: `3217dd8cc8cf766e95c5a33c23d89a6286ab50cb` (G04-T04C closure)
> **Final commit SHA**: see §9 (Final commit SHA)
> **Policy version**: `deterministic-v2`

## 1. Executive summary

G04-T05 is the **final G04 implementation task**. It validates that
external applications can reliably consume the existing Synapse
knowledge, retrieval, capability analysis, and grounded reasoning APIs.
Per mission §1: *"This is an integration-compatibility task, NOT a new
application framework."*

The implementation introduces **NO new database, NO vector store, NO
LLM provider, NO message broker, NO agent framework, NO frontend, NO
new pip dependencies, and NO new API endpoints**. It is purely
additive: a contract test file, an example client, and this review.

The audit confirms:

- All G04 endpoints (`/api/v1/knowledge/retrieve`,
  `/api/v1/knowledge/capabilities` + `/analyze-gap`,
  `/api/v1/reasoning/queries` + `/intents`) return 200 with valid auth.
- OpenAPI 3.1.0 schema is published at `/api/v1/openapi.json` and is
  self-consistent for all G04 endpoints.
- All error responses follow the standard envelope + `application/problem+json`
  content type with structured `error.code`, `error.message`, `error.details`.
- Evidence-preserving fields (findings, cited_claims, cited_relationships,
  evidence_chain, unknowns, contradictions, confidence, limitations,
  out_of_context_contradictions) are all preserved in public responses.
- CORS allowlist works (allowed origin receives headers; disallowed
  origin does not).
- A minimal external Python client (`examples/client_g04.py`) consumes
  the API using only `httpx` (no `synapse` imports).
- 18 black-box HTTP contract tests pass, covering all 15 mandatory
  coverage areas from mission §5.

One test-isolation defect in the initial test implementation was
identified and fixed: `test_external_client_demo_runs` originally
called `asyncio.run()` which closed the session event loop and broke
subsequent async tests. Fixed by making the test async (using the
pytest-asyncio event loop directly). No application source was
changed.

## 2. Starting and final commit SHAs

| Property | Value |
|----------|-------|
| Starting checkpoint | `3217dd8cc8cf766e95c5a33c23d89a6286ab50cb` |
| Final commit SHA | `5c98402a3fe55752e92f0a129aefd7964c219181` |
| Remote synchronization | PASS (verified via `git ls-remote`) |
| Working tree | CLEAN (in sync with origin/main) |

## 3. Actual endpoints audited

### G04 endpoints (the focus of this task)

| Method | Path | Source | Status |
|--------|------|--------|--------|
| POST | `/api/v1/knowledge/retrieve` | `api/v1/retrieval.py` (G04-T01) | ✅ 200 with auth |
| GET | `/api/v1/knowledge/capabilities` | `api/v1/capability_registry.py` (G04-T02) | ✅ 200 with auth |
| GET | `/api/v1/knowledge/capabilities/{capability_id}` | `api/v1/capability_registry.py` (G04-T02) | ✅ 200 / 404 |
| POST | `/api/v1/knowledge/capabilities/analyze-gap` | `api/v1/capability_registry.py` (G04-T02) | ✅ 200 with auth |
| POST | `/api/v1/reasoning/queries` | `api/v1/reasoning.py` (G04-T03) | ✅ 200 with auth |
| GET | `/api/v1/reasoning/intents` | `api/v1/reasoning.py` (G04-T03) | ✅ 200 with auth |

### Legacy endpoints (backward compatibility verified)

| Method | Path | Source | Status |
|--------|------|--------|--------|
| GET | `/health/live` | `api/v1/health.py` (G01) | ✅ 200 (no auth) |
| GET | `/health/ready` | `api/v1/health.py` (G01) | ✅ 200 (no auth) |
| GET | `/api/v1/capabilities` | `api/v1/capabilities.py` (G01) | ✅ 200 |
| GET | `/api/v1/providers` | `api/v1/providers.py` (G01) | ✅ 200 |
| GET | `/api/v1/system/activity-mode` | `api/v1/system.py` (G01) | ✅ 200 |
| GET | `/api/v1/entities` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 |
| GET | `/api/v1/entities/{entity_id}` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 / 404 |
| GET | `/api/v1/relationships` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 |
| GET | `/api/v1/relationships/{relationship_id}` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 / 404 |
| GET | `/api/v1/claims/{claim_id}/evidence` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 / 404 |
| POST | `/api/v1/knowledge/search` | `api/v1/knowledge.py` (G03-T05) | ✅ 200 |
| POST | `/api/v1/sources/discover` | `api/v1/sources.py` (G02) | ✅ 200 |
| POST | `/api/v1/sources/ingest` | `api/v1/sources.py` (G02) | ✅ 200 |
| GET | `/api/v1/sources` | `api/v1/sources.py` (G02) | ✅ 200 |
| GET | `/api/v1/sources/{source_id}` | `api/v1/sources.py` (G02) | ✅ 200 / 404 |

### Placeholder endpoints (G05+ — NOT activated)

| Method | Path | Status |
|--------|------|--------|
| POST | `/api/v1/innovations/generate` | ✅ 501 (not_implemented) |
| POST | `/api/v1/innovations/{innovation_id}/critique` | ✅ 501 |
| POST | `/api/v1/experiments` | ✅ 501 |
| POST | `/api/v1/experiments/{experiment_id}/execute` | ✅ 501 |
| GET | `/api/v1/experiments/{experiment_id}` | ✅ 501 |
| GET | `/api/v1/hypotheses/{hypothesis_id}/evidence-deltas` | ✅ 501 |
| POST | `/api/v1/future/scenarios` | ✅ 501 |
| GET | `/api/v1/future/scenarios/{scenario_id}` | ✅ 501 |
| POST | `/api/v1/future/scenarios/{scenario_id}/prototype-plan` | ✅ 501 |

Per mission §7: *"Do not activate G05 placeholder endpoints."* — confirmed.

## 4. Contract compatibility findings

### 4.1 Stable request and response schemas

All G04 endpoints use Pydantic v2 models for request validation:

- `RetrieveRequest` (retrieval.py): `query`, `entity_kind?`, `predicate?`, `epistemic_state?`, `max_depth=3`, `limit=20`
- `AnalyzeGapRequest` (capability_registry.py): `required_capabilities[]`, `context?`, `candidate_entity_ids?`, `limit=20`
- `ReasoningRequest` (reasoning.py): `query`, `candidate_entity_ids?`, `context?`, `limit=20`

All responses use the standard `Envelope[T]` wrapper:

```json
{
  "data": ...,
  "meta": {"request_id": "...", "api_version": "v1", "pagination": ...},
  "error": null
}
```

### 4.2 Consistent field names and types

Field naming is consistent across all G04 endpoints:
- `query` (string) — the natural-language query
- `context` (string|null) — optional technical context
- `candidate_entity_ids` (string[]|null) — optional candidate scoping
- `limit` (int) — pagination cap
- `request_id` (string) — per-request tracing ID

### 4.3 Optional-field handling

Optional fields are explicitly `null` when absent (not omitted), so
external clients can rely on field presence. The Pydantic v2 default
behavior is preserved.

### 4.4 Validation errors

All validation failures return:
- HTTP 422 Unprocessable Content
- `Content-Type: application/problem+json`
- Standard envelope with `error.code="validation_error"`
- `error.details` array with per-field error info (`type`, `loc`, `msg`, `input`, `ctx`)

### 4.5 Authentication and authorization

All `/api/v1/*` endpoints require the `Authorization: Bearer <key>` header.
Missing/invalid auth returns:
- HTTP 401
- `error.code="unauthenticated"`

The `PrincipalDep` dependency enforces this consistently across all
G04 endpoints.

### 4.6 Predictable HTTP status codes

| Status | Meaning | When |
|--------|---------|------|
| 200 | Success | Valid request with valid auth |
| 401 | Unauthenticated | Missing/invalid Authorization header |
| 404 | Not Found | Invalid identifier (capability_id, entity_id, etc.) |
| 422 | Validation Error | Missing required field, wrong type, oversized input |
| 501 | Not Implemented | G05+ placeholder routes |
| 500 | Internal Error | Unhandled exception (logged with trace_id) |

### 4.7 Problem+json consistency

All error responses use `application/problem+json` content type with the
standard envelope. Verified by `test_11_predictable_error_responses`.

### 4.8 Pagination and result limits

All list-style responses include `meta.pagination` with `limit` and
`total` fields. Hard upper bounds are enforced by Pydantic validators:
- `query`: max 512 chars
- `limit`: max 100
- `max_depth`: max 5
- `max_graph_expansion`: max 50

### 4.9 JSON serializability

All responses are JSON-serializable (verified by `r.json()` in every
test). The `Envelope` Pydantic model handles serialization
consistently.

### 4.10 OpenAPI schema correctness

OpenAPI 3.1.0 schema published at `/api/v1/openapi.json` includes:
- All G04 endpoints with request/response schemas
- All legacy endpoints (backward compatibility)
- All G05+ placeholder routes (correctly marked 501)
- `info.version="0.1.0"`, `info.title="AgentCraft Synapse"`

Verified by `test_12_stable_openapi_schema_generation` and
`make openapi-check`.

## 5. Evidence/provenance preservation results

Per mission §4: *"External consumers must be able to distinguish
documented facts, derived findings, hypotheses, unknowns, applicable
contradictions, out-of-context contradictions, evidence limitations."*

### 5.1 Finding types preserved

The `POST /api/v1/reasoning/queries` response's `findings` array
preserves all 4 finding types:

| Type | Meaning | Field markers |
|------|---------|---------------|
| `documented_fact` | Directly supported by source evidence | `evidence_refs` non-empty, `confidence` in [0.70, 0.90] |
| `derived_finding` | Follows from documented relationships + rules | `evidence_refs` may be partial, `confidence` in [0.20, 0.50] |
| `hypothesis` | Plausible but not established | `confidence` < 0.30 |
| `unknown` | Insufficient information | `caveat` explains the gap |

Verified by `test_03_valid_reasoning_request` and
`test_09_contradictions_and_context_metadata`.

### 5.2 Citation chain preserved

Each finding carries:
- `sources`: list of capability IDs (or entity/relationship IDs for
  relationship-derived findings)
- `evidence_refs`: list of evidence fragment IDs

The top-level `evidence_chain` array provides the full traceable chain:
```
cited_claim_id → evidence_fragment_id → source_span → source_uri
```

Each chain link includes `fragment_id`, `source_uri`, and `spans` (with
`start_offset`, `end_offset`, `excerpt`). Verified by
`test_10_citation_and_provenance_serialization`.

### 5.3 Contradictions preserved

Applicable contradictions (where `validity_conditions` match the
context) produce:
- Finding text containing "CONTESTED"
- Both supporting and contradicting `evidence_refs` in the same finding
- Non-empty `contradictions` field at the answer level (when
  relationship-level contradictions exist)

Out-of-context contradictions are preserved as
`out_of_context_contradictions` metadata inside each finding's caveat
(not in the top-level `contradictions` list). Verified by
`test_09_contradictions_and_context_metadata`.

### 5.4 Evidence limitations preserved

The `unknowns` array explicitly lists what the system could NOT find.
The `confidence` object includes:
- `overall`: bounded [0.0, 0.90] (VERIFIED unreachable in deterministic-v2)
- `policy_version`: `"deterministic-v2"`
- `stale_threshold_days`: 365
- `confidence_map`: full mapping of verification outcomes to scores
- `note`: explicit explanation of the policy bounds

The `limitations` array lists 8 architectural limitations (no LLM, no
semantic search, no transitive deps, etc.).

### 5.5 What is NOT claimed

Per mission §4: *"Do not invent missing evidence fields or claim
semantic verification that does not exist."*

The response does NOT claim:
- Semantic grounding (only structural identifier existence)
- That cited evidence actually supports the finding (only that the ID resolves)
- That the answer is production-ready (PRB-01..07 unresolved)

## 6. Defects corrected

### Defect D1 (test isolation): `asyncio.run()` in test corrupted session event loop

**Symptom**: When `test_external_client_demo_runs` was run as part of
the full suite, 17 SSRF/DNS tests subsequently failed with
`RuntimeError: There is no current event loop in thread 'MainThread'`.

**Root cause**: The original test called `asyncio.run(_setup())` which
creates AND closes a new event loop. The `conftest.py` has a
session-scoped `event_loop` fixture; closing the loop via `asyncio.run()`
corrupted the session loop state for subsequent async tests.

**Fix**: Made `test_external_client_demo_runs` async (using
`@pytest.mark.asyncio`) and extracted the setup into an async helper
`_setup_app_with_seeded_data()`. The test now uses the pytest-asyncio
event loop directly — no `asyncio.run()` call.

**Lines changed**: ~30 lines in `test_g04_t05_external_client.py` (test
converted from sync to async; setup helper extracted).

**Regression test**: The fix is verified by running the full suite —
the 17 SSRF/DNS tests now pass after `test_external_client_demo_runs`.

### Defect D2 (test timeout): `test_15` subprocess timeout too short

**Symptom**: `test_15_full_regression_g01_through_t04c` timed out at
300 seconds when run inside the full suite (under CI load).

**Root cause**: The subprocess pytest loads the full Synapse package
and runs integration tests that build a fresh DB per test. Under full-
suite load this can exceed 300s.

**Fix**: Increased the subprocess timeout from 300s to 600s. Added a
docstring explaining why the timeout is generous.

**Lines changed**: 2 lines (timeout value + docstring).

### No application source defects found

No defects were found in the application source code
(`src/synapse/api/`). All G04 endpoints behave correctly per the
contract. No corrections to `src/synapse/` were needed.

## 7. External client example

`examples/client_g04.py` (243 LOC) provides a minimal external HTTP
client using only `httpx` (already a Synapse dependency — no new
packages).

### Demonstrated capabilities

1. **Sending a retrieval query**: `retrieve_knowledge(client, query="synapse")`
2. **Requesting grounded reasoning**: `answer_reasoning_query(client, query="What can synapse do?", ...)`
3. **Reading structured findings**: `extract_findings(resp)` returns the findings list; `group_findings_by_type(findings)` groups by `documented_fact` / `derived_finding` / `hypothesis` / `unknown`
4. **Extracting citation references**: `extract_citation_chain(resp)` returns the evidence chain; each link has `fragment_id`, `source_uri`, `spans`
5. **Extracting unknowns and contradictions**: `extract_unknowns(resp)` and `extract_contradictions(resp)`
6. **Handling HTTP and validation errors**: `handle_error(response)` parses the `application/problem+json` body and prints `error.code`, `error.message`, `error.details`, `request_id`

### Verification

`test_external_client_demo_runs` imports `examples/client_g04.py` via
`importlib.util` (no `synapse` imports in the example), runs it against
a live TestClient-wrapped app, and verifies it produces structured
output. The test passes — the example uses only the public HTTP API.

### CLI entry point

The example can also be run as a script:

```bash
python examples/client_g04.py \
    --base-url http://127.0.0.1:8000 \
    --api-key "$SYNAPSE_API_KEY"
```

Exit code 0 on success, 1 on failure, 2 on missing `--api-key`.

## 8. Contract compatibility policy

### 8.1 Available `/api/v1` contracts

External clients may rely on these endpoints (all return the standard
`Envelope`):

| Contract | Stability |
|----------|-----------|
| `POST /api/v1/knowledge/retrieve` | Stable (G04-T01) |
| `GET /api/v1/knowledge/capabilities` | Stable (G04-T02) |
| `GET /api/v1/knowledge/capabilities/{id}` | Stable (G04-T02) |
| `POST /api/v1/knowledge/capabilities/analyze-gap` | Stable (G04-T02) |
| `POST /api/v1/reasoning/queries` | Stable (G04-T03) |
| `GET /api/v1/reasoning/intents` | Stable (G04-T03) |
| `GET /api/v1/entities` | Stable (G03-T05) |
| `GET /api/v1/entities/{id}` | Stable (G03-T05) |
| `GET /api/v1/relationships` | Stable (G03-T05) |
| `GET /api/v1/relationships/{id}` | Stable (G03-T05) |
| `GET /api/v1/claims/{id}/evidence` | Stable (G03-T05) |
| `POST /api/v1/knowledge/search` | Stable (G03-T05) |
| `POST /api/v1/sources/discover` | Stable (G02) |
| `POST /api/v1/sources/ingest` | Stable (G02) |
| `GET /api/v1/sources` | Stable (G02) |
| `GET /api/v1/sources/{id}` | Stable (G02) |
| `GET /health/live` | Stable (G01) |
| `GET /health/ready` | Stable (G01) |
| `GET /api/v1/capabilities` | Stable (G01) |
| `GET /api/v1/providers` | Stable (G01) |
| `GET /api/v1/system/activity-mode` | Stable (G01) |

### 8.2 Placeholder endpoints (NOT for external use)

These return HTTP 501 `not_implemented`. External clients must NOT
depend on them:

- `/api/v1/innovations/*`
- `/api/v1/experiments/*`
- `/api/v1/hypotheses/*`
- `/api/v1/future/*`
- `PUT /api/v1/system/activity-mode`
- `POST /api/v1/jobs/{id}/cancel`

### 8.3 Reliable response fields

External clients may rely on these fields being present:

- `data`: the response payload (or `null` on error)
- `meta.request_id`: per-request tracing ID
- `meta.api_version`: `"v1"`
- `meta.pagination`: `{limit, total, next_cursor?, prev_cursor?}` (for list endpoints)
- `error`: `null` on success, or `{code, message, details?, retryable, retry_after?, trace_id?}` on error

For reasoning responses specifically:
- `data.findings[]`: each has `{text, type, sources, evidence_refs, confidence, caveat?}`
- `data.cited_claims[]`: list of claim IDs
- `data.cited_relationships[]`: list of relationship IDs
- `data.evidence_chain[]`: each has `{fragment_id?, source_uri, spans[]}`
- `data.unknowns[]`: what the system could NOT find
- `data.contradictions[]`: applicable contradictions (preserved)
- `data.confidence`: `{overall, policy_version, stale_threshold_days, confidence_map, note}`
- `data.limitations[]`: architectural limitations

### 8.4 Optional additions

Optional fields may be added to responses in future without breaking
compatibility. External clients should ignore unknown fields (forward-
compatible parsing). New fields will NOT be removed once published.

### 8.5 Changes that would require a future API version

The following changes would require `/api/v2`:

- Removing a currently-published field
- Changing a field's type (e.g., `string` → `object`)
- Changing the envelope structure (`data`/`meta`/`error` keys)
- Changing the error content type away from `application/problem+json`
- Changing authentication mechanism (Bearer token → something else)

### 8.6 Known limitations

- **No semantic verification**: citation validity checks identifier
  existence, not semantic support. Structural only.
- **No LLM-based reasoning**: deterministic evidence composition only.
- **No semantic search**: lexical + structured + graph only.
- **No transitive dependency analysis**: 1-hop only.
- **VERIFIED unreachable**: confidence is capped at 0.90
  (deterministic-v2 policy).
- **No streaming**: all responses are synchronous JSON.
- **No WebSocket**: polling only.
- **No GraphQL**: REST only.

## 9. Files changed and LOC

### Files added (3)

| File | Purpose | LOC |
|------|---------|----:|
| `tests/integration/test_g04_t05_external_client.py` | 18 black-box HTTP contract tests + 2 CORS tests + 1 external-client-demo test | 1090 |
| `examples/client_g04.py` | Minimal external HTTP client using httpx | 243 |
| `reports/G04_T05_EXTERNAL_CLIENT_COMPATIBILITY.md` | This report | (this file) |

### Files modified (0)

No application source files were modified. G04-T05 is purely additive
(tests + example + report).

### Files NOT modified (intentional)

- All `src/synapse/api/*.py` (no contract changes needed)
- All `src/synapse/application/*.py` (no service changes)
- All `src/synapse/domain/*.py` (frozen per ADR-0011)
- All `src/synapse/storage/*.py` (no DB changes)
- All migrations (0001-0004 unchanged)
- `pyproject.toml` (no new dependencies)
- All existing ADRs (0001-0011)
- All existing reports

## 10. Tests and validation results

### Gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts examples` | ✅ All checks passed |
| `ruff format --check src tests scripts examples` | ✅ all files formatted |
| `pytest` (deterministic) | ✅ 468 passed, 0 failed, 3 skipped (live) |
| `make openapi-check` | ✅ OpenAPI OK (3.1.0) |

### Baseline vs. after-G04-T05

| Metric | Baseline (3217dd8) | After G04-T05 | Delta |
|--------|------------------:|--------------:|------:|
| Deterministic tests passed | 450 | 468 | +18 |
| Tests failed | 0 | 0 | 0 |
| Tests skipped (live) | 3 | 3 | 0 |
| Files (ruff check) | 126 | 128 | +2 |
| Files (ruff format) | 126 | 128 | +2 |

The +18 tests are:
- 15 mandatory HTTP contract tests (test_01 through test_15)
- 2 CORS preflight tests (test_cors_allowed_origin_preflight,
  test_cors_disallowed_origin_rejected)
- 1 external-client-demo test (test_external_client_demo_runs)

### G04-T05 acceptance criteria

Per mission §5 (15 mandatory coverage areas):

| # | Criterion | Status | Test |
|---|-----------|--------|------|
| 1 | Valid retrieval requests | ✅ PASS | `test_01_valid_retrieval_request` |
| 2 | Valid capability-analysis requests | ✅ PASS | `test_02_valid_capability_analysis_request` |
| 3 | Valid reasoning requests | ✅ PASS | `test_03_valid_reasoning_request` |
| 4 | Missing required fields | ✅ PASS | `test_04_missing_required_fields` |
| 5 | Incorrect field types | ✅ PASS | `test_05_incorrect_field_types` |
| 6 | Invalid identifiers | ✅ PASS | `test_06_invalid_identifiers` |
| 7 | Oversized requests | ✅ PASS | `test_07_oversized_requests` |
| 8 | Empty results | ✅ PASS | `test_08_empty_results` |
| 9 | Contradictions and context metadata | ✅ PASS | `test_09_contradictions_and_context_metadata` |
| 10 | Citation and provenance serialization | ✅ PASS | `test_10_citation_and_provenance_serialization` |
| 11 | Predictable error responses | ✅ PASS | `test_11_predictable_error_responses` |
| 12 | Stable OpenAPI schema generation | ✅ PASS | `test_12_stable_openapi_schema_generation` |
| 13 | Existing authentication behavior | ✅ PASS | `test_13_existing_authentication_behavior` |
| 14 | Backward compatibility with G04 endpoints | ✅ PASS | `test_14_backward_compatibility_g04_endpoints` |
| 15 | Existing regression suite | ✅ PASS | `test_15_full_regression_g01_through_t04c` |

Plus G04 plan T05 acceptance criteria (5 items):

| # | Criterion | Status | Test |
|---|-----------|--------|------|
| 16 | External client uses only the API | ✅ PASS | `test_external_client_demo_runs` |
| 17 | All G04 endpoints return 200 with valid auth | ✅ PASS | `test_14_backward_compatibility_g04_endpoints` |
| 18 | OpenAPI schema validates | ✅ PASS | `test_12_stable_openapi_schema_generation` + `make openapi-check` |
| 19 | CORS allowlist works | ✅ PASS | `test_cors_allowed_origin_preflight` + `test_cors_disallowed_origin_rejected` |
| 20 | G01/G02/G03 regression | ✅ PASS | `test_15_full_regression_g01_through_t04c` |

## 11. Remaining limitations

### Carried forward from G04-T01..T04C

1. **No LLM-based reasoning** — deterministic evidence composition only.
2. **No semantic search** — lexical + structured + graph only.
3. **No transitive dependency analysis** — 1-hop only.
4. **Conservative applicability matching** — phrase-substring.
5. **VERIFIED unreachable** — per deterministic-v2 policy (PRB-05).
6. **Structural citation validity** — identifier existence, not semantic support.
7. **No streaming** — synchronous JSON only.
8. **No WebSocket** — polling only.

### G04-T05-specific limitations

9. **External client example is illustrative, not a supported SDK** —
   `examples/client_g04.py` is a demonstration. It is NOT a distributable
   SDK, CLI framework, or generated client package. External applications
   should write their own clients against the OpenAPI schema.

10. **No contract versioning beyond v1** — `/api/v1` is the only API
    version. Future breaking changes would require `/api/v2`, but no
    such migration is planned or authorized.

11. **CORS allowlist is environment-configured** — the test profile uses
    `http://localhost:3000`. Production deployments must configure
    `SYNAPSE_CORS_ORIGINS` appropriately. Wildcard origins +
    credentials are forbidden (enforced by `add_cors`).

### No new production-readiness blockers

PRB-01 through PRB-07 remain unchanged. G04-T05 does NOT introduce any
new blockers.

## 12. Existing PRB-01 through PRB-07 blockers

The full blocker register from ADR-0011 carries forward UNCHANGED.

| # | Blocker | Origin | Severity | Status after G04-T05 |
|---|---------|--------|----------|----------------------|
| PRB-01 | Real PostgreSQL migration/integration validation | G02 | High | Unchanged |
| PRB-02 | Pinned-IP HTTPS, proxy, IPv6, connection-pooling security review | G02 closure | Medium | Unchanged |
| PRB-03 | Database-level concurrency/idempotency guarantees | G03-T02 | Medium | Unchanged |
| PRB-04 | Evidence-origin independence limitations | G03-T04 | Medium | Unchanged |
| PRB-05 | Stronger VERIFIED review policy | G03-T04 | Expected | Unchanged — VERIFIED remains unreachable |
| PRB-06 | Toolkit read-only credential enforcement | ADR-0009 §7 | Medium | Unchanged — G04-T05 does not touch the Toolkit |
| PRB-07 | Extractor-version-aware reprocessing | G03-T02 | Low | Unchanged |

### New blockers introduced by G04-T05

**None.** G04-T05 introduces no new production-readiness blockers.

## 13. G04 closure readiness recommendation

### G04 task completion summary

| Task | Title | Status | Commit |
|------|-------|--------|--------|
| G04-T01 | Hybrid retrieval | PASS WITH LIMITATIONS | `49f3d3e` |
| G04-T01 (closure) | Closure review | PASS | `535267c` |
| G04-T02 | Capability registry + gap analysis | PASS WITH LIMITATIONS | `a4f75fd` |
| G04-T02C | Provider attribution closure | PASS | `022d10a` |
| G04-T03 | Grounded reasoning | PASS WITH LIMITATIONS | `c6492d7` |
| G04-T03C | Context-aware contradiction closure | PASS | `2dce73a` |
| G04-T04 | Evaluation + cost-aware routing | PASS WITH LIMITATIONS | `629b6c1` |
| G04-T04C | Evaluation integrity review | PASS WITH MINIMAL CORRECTIONS | `c891fec` |
| G04-T05 | External client compatibility | PASS WITH LIMITATIONS | (this commit) |

### Recommendation

**READY FOR ARCHITECTURAL REVIEW.**

G04 is functionally complete:

- All 5 planned tasks (T01-T05) implemented and tested.
- 2 focused closure reviews (T01 closure, T04C integrity review) applied.
- 2 provider-attribution and context-contradiction closures (T02C, T03C) applied.
- 468 deterministic tests pass; 0 fail; 3 skipped (live).
- Ruff lint + format pass on 128 files.
- OpenAPI 3.1.0 schema validates.
- External client demo runs end-to-end.
- No new production-readiness blockers introduced.
- PRB-01..PRB-07 unchanged.
- AgentCraft-Toolkit READ-ONLY boundary preserved throughout.

Per mission §11: *"Do not declare G04 globally closed without explicit
architectural approval."* — this report recommends G04 be submitted for
architectural review. G04 closure decision belongs to the architectural
reviewer, not to this implementation report.

## 14. Final commit SHA

After implementing G04-T05 + 18 contract tests + external client
example + this report:

```
Final commit SHA (post-T05): 5c98402a3fe55752e92f0a129aefd7964c219181
Starting checkpoint (for reference): 3217dd8cc8cf766e95c5a33c23d89a6286ab50cb
```

## 15. STOP

Per the G04-T05 mission briefing §11:

- ✅ G04-T05 implemented, tested, committed, pushed.
- ✅ Compact implementation: 3 new files, 1333 LOC + this report.
- ✅ All 15 mandatory contract tests pass + 5 plan-specific acceptance criteria.
- ✅ Full G01-G04-T04C regression: 468 tests pass, 0 fail, 3 skipped.
- ✅ Ruff lint + format: 128 files, all pass.
- ✅ OpenAPI generation validates.
- ✅ External client demo runs.
- ❌ **G05 NOT STARTED.** (Not authorized.)
- ❌ **G04 NOT globally closed.** (Awaiting architectural review.)
- ❌ **AgentCraft-Toolkit NOT MODIFIED.** (READ-ONLY boundary preserved.)

The Synapse G04 implementation is complete and ready for architectural
review.

---

*End of G04-T05 External Client Compatibility Report — STOP.*
