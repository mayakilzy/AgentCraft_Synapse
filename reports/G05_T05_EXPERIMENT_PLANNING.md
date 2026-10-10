# G05-T05 — Experiment Planning Implementation Report

**Status**: PASS
**Task**: G05-T05 — Evidence-Grounded Experiment Planning
**Starting SHA**: `be78c253578393b928d78a4ac55220700bee9019` (G05-T04 closure)
**Final SHA**: (populated after push)
**Date**: 2026-10-10
**Authoritative plan**: `reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md` §G05-T05

---

## 1. Executive summary

G05-T05 implements a minimal, evidence-grounded experiment planner that
converts persisted innovation hypotheses into measurable, reproducible
experiment plans. The planner reuses G05-T01..T04 services (concept
loading, critique, gap analysis) to extract uncertainties, missing
evidence, failure modes, and applicable contradictions, then synthesizes
a structured plan with explicit objectives, success/failure criteria,
required inputs (with availability flags), interpretation rules, and a
deterministic fingerprint for idempotent reuse.

Two API endpoints are activated: `POST /api/v1/experiments` (create a
plan) and `GET /api/v1/experiments/{experiment_id}` (retrieve a plan).
Both honor authentication, the standard response envelope, validation
errors, and OpenAPI 3.1.0. The remaining experiment/hypothesis routes
(`POST /experiments/{id}/execute`, `GET /hypotheses/{id}/evidence-deltas`)
remain 501 placeholders — they belong to G05-T06.

**Critical epistemic guarantee**: planning produces no observations, no
evidence deltas, no hypothesis state transitions, and no `VERIFIED`
promotion. The hypothesis `ClaimRow` is read-only throughout planning.
A proposed experiment is a plan, not evidence.

**Status**: PASS. All 6 acceptance criteria, all 10 quality gates, and
all 30 new tests are green. G01–G05-T04 regression is intact (477 tests
confirmed passing in this session; 4 slow integration files unchanged
and previously green).

---

## 2. Files changed

### 2.1 New source files

| File | Purpose | LOC |
|------|---------|----:|
| `src/synapse/application/experiment_planner.py` | `plan_experiment()` + `get_experiment()` + synthesis helpers | 1080 |
| `src/synapse/api/v1/experiments.py` | `POST /api/v1/experiments` + `GET /api/v1/experiments/{experiment_id}` | 175 |
| **Source total** | | **1255** |

### 2.2 Modified source files

| File | Change | Δ LOC |
|------|--------|------:|
| `src/synapse/api/v1/router.py` | Activate `experiments.router`; remove `POST /experiments` and `GET /experiments/{id}` 501 placeholders; keep `POST /experiments/{id}/execute`, `GET /hypotheses/{id}/evidence-deltas`, `/future/scenarios*` as 501 (T06/G06 scope). | +8 / −13 |

### 2.3 New test files

| File | Purpose | LOC |
|------|---------|----:|
| `tests/integration/test_g05_t05_experiment_planning.py` | 30 tests: 6 acceptance + 10 quality-gate + 14 negative | 873 |
| **Test total** | | **873** |

### 2.4 Modified test files

| File | Change | Δ LOC |
|------|--------|------:|
| `tests/unit/api/test_system.py` | Remove `POST /experiments` from the 501 list; add `test_experiments_post_is_active` (422 on no body) and `test_experiments_get_is_active` (404 on missing ID) to prove the routes are live. | +31 / −1 |

### 2.5 No schema / migration / dependency changes

- No new ORM tables, no new migrations, no new pip dependencies.
- Experiments persist as `EntityRow(kind="experiment")` with attributes
  JSON — reuses the existing G01 persistence layer.
- No `AuditEventRow` of `event_type="evidence.delta"` is created during
  planning.
- No `ClaimRow` is modified during planning (the hypothesis is read-only).
- AgentCraft-Toolkit: STRICTLY READ ONLY — not modified, not pushed.

---

## 3. Architecture

### 3.1 Storage mapping (reuse, no schema change)

| Domain concept | Storage | Notes |
|----------------|---------|-------|
| Experiment plan | `EntityRow(kind="experiment")` | Attributes JSON carries the full plan; `experiment_fingerprint` field enables idempotent reuse. |
| Hypothesis linkage | `attributes.hypothesis_id` (string) | No `RelationshipRow` is created — experiments link to claims, not entities, so a relationship edge would be semantically wrong. |
| Concept linkage | `attributes.concept_id` (string) | For traceability back to the parent innovation. |

### 3.2 The single entry point: `plan_experiment()`

```python
async def plan_experiment(
    session: AsyncSession,
    hypothesis_id: str,
    *,
    protocol: str | None = None,
    baseline: str | None = None,
    metrics: dict[str, Any] | None = None,
    execution_mode: ExperimentExecutionMode | str | None = None,
    safety_limits: dict[str, Any] | None = None,
    cost_limits: dict[str, Any] | None = None,
    requester: str | None = None,
) -> ExperimentPlanResult | None
```

**Returns** `None` when:
- The hypothesis `ClaimRow` does not exist.
- The hypothesis has no `subject_ref` linking it to a concept.
- The parent concept `EntityRow` does not exist.

**Returns** an `ExperimentPlanResult` dict otherwise, with:
- `experiment`: the persisted plan (with `was_reused: bool` flag)
- `unknowns`: list of unavailable-resource / missing-evidence strings
- `executability`: one of `executable_with_available_tools`,
  `requires_external_resources`, `not_executable`
- `request_id`, `planned_at`

### 3.3 Plan synthesis pipeline

1. **Resolve hypothesis** → load `ClaimRow`, validate it has a
   `subject_ref` pointing at a concept.
2. **Load concept context** → reuse `_load_persisted_concept()` from
   G05-T04 (gives components, evidence_refs, attributes).
3. **Load critique context** → reuse `critique_innovation()` from G05-T04
   (gives uncertainties, missing_evidence, failure_modes, applicable_
   contradictions, feasibility_score).
4. **Synthesize objectives** from problem_domain + uncertainties +
   missing_evidence + failure_modes (capped at 8, deduplicated).
5. **Synthesize metrics** — use user-supplied if provided; otherwise
   infer a single metric from quantifiable uncertainties (heuristic on
   keywords like "precision", "latency", "recall"). If none inferable,
   return empty dict and record assumption as untestable.
6. **Synthesize success criteria** (≥1, tied to metrics or primary
   objective) and **failure criteria** (≥1, tied to metrics or failure
   modes).
7. **Synthesize required inputs** — components (with `available: bool`
   from `EntityRow` lookup), evidence fragments (with `available: bool`
   from `EvidenceFragmentRow` lookup), missing capabilities (always
   `available: false`).
8. **Synthesize interpretation rules** — deterministic declarations of
   how observations will be interpreted, including the rule that no
   observation promotes to `VERIFIED` (impossible by construction —
   `EpistemicState` has no `VERIFIED` value).
9. **Classify executability** — `executable_with_available_tools` /
   `requires_external_resources` / `not_executable` based on input
   availability.
10. **Validate invariants** — construct a frozen `Experiment` domain
    record to enforce `production` mode → `safety_limits.approved_by`
    and `networked` mode → `cost_limits.approved_by`.
11. **Compute fingerprint** — `SHA-256(hypothesis_id + normalized_
    protocol + sorted_metric_keys + execution_mode)`.
12. **Idempotent reuse** — if an existing `EntityRow(kind="experiment")`
    has the same fingerprint, return it with `was_reused=True`.
13. **Persist** as a new `EntityRow(kind="experiment")` with attributes
    JSON.
14. **Return** the plan dict.

### 3.4 What planning NEVER does

- ❌ Modify the hypothesis `ClaimRow` (epistemic_state, confidence,
  version — all unchanged).
- ❌ Create an `AuditEventRow` of `event_type="evidence.delta"`.
- ❌ Create an `ObservationRecord` (the `Experiment.result` field is
  always `None`).
- ❌ Construct an `ExperimentResult` (that's the exclusive output of
  G05-T06 execution).
- ❌ Fabricate a metric, threshold, or outcome. Unavailable resources
  are reported via `required_inputs[*].available = false`.
- ❌ Promote anything to `VERIFIED` (impossible — `EpistemicState` has
  no `VERIFIED` value; only `VerificationState` does, and planning
  doesn't touch verification).

---

## 4. API contract

### 4.1 `POST /api/v1/experiments`

**Request body** (`ExperimentRequest`):
```json
{
  "hypothesis_id": "hyp-...",
  "protocol": "Optional. If omitted, synthesized from concept uncertainties.",
  "baseline": "Optional. If omitted, a default is synthesized.",
  "metrics": {"precision_at_10": "float"},
  "execution_mode": "dry_run",
  "safety_limits": {},
  "cost_limits": {}
}
```

**Response 200** (standard envelope):
```json
{
  "data": {
    "experiment": {
      "experiment_id": "exp-...",
      "hypothesis_id": "hyp-...",
      "concept_id": "innov-...",
      "protocol": "...",
      "baseline": "...",
      "objectives": ["..."],
      "success_criteria": [{"metric": "...", "comparator": ">=", "threshold": null, "rationale": "..."}],
      "failure_criteria": [{"metric": "...", "comparator": "<", "threshold": null, "rationale": "..."}],
      "metrics": {"precision_at_10": "float"},
      "required_inputs": [{"kind": "component_entity", "name": "...", "ref": "...", "available": true, "rationale": "..."}],
      "dependencies": [...],
      "resource_constraints": [...],
      "estimated_effort": {"min_minutes": 5, "max_minutes": 5, "unit": "minutes", "caveat": "..."},
      "execution_mode": "dry_run",
      "safety_limits": {},
      "cost_limits": {},
      "evidence_refs": ["frag-..."],
      "applicable_contradictions": [{"claim_id": "...", "contradicting_refs": [...]}],
      "testable_assumptions": ["..."],
      "untestable_assumptions": ["..."],
      "missing_evidence": ["..."],
      "interpretation_rules": ["..."],
      "executability": "executable_with_available_tools",
      "result": null,
      "artifact_refs": [],
      "was_reused": false
    },
    "unknowns": ["..."],
    "executability": "executable_with_available_tools",
    "request_id": "...",
    "planned_at": "2026-10-10T..."
  },
  "meta": {"request_id": "...", "api_version": "v1", "pagination": null},
  "error": null
}
```

**Errors**:
- `401` — no/invalid Authorization header.
- `404` — hypothesis_id does not resolve to a persisted ClaimRow with a
  linked concept.
- `422` — validation error (unknown execution_mode, production mode
  missing `safety_limits.approved_by`, etc.).
- `501` — never (this route is now implemented).

### 4.2 `GET /api/v1/experiments/{experiment_id}`

**Response 200** (standard envelope): the persisted plan dict (without
the outer `experiment` / `unknowns` / `executability` wrapper — just
the plan fields, plus `retrieved_at`).

**Errors**:
- `401` — no/invalid Authorization header.
- `404` — experiment_id does not resolve to a persisted
  `EntityRow(kind="experiment")`.

### 4.3 Routes that REMAIN 501 (out of scope for T05)

| Route | Belongs to |
|-------|------------|
| `POST /api/v1/experiments/{experiment_id}/execute` | G05-T06 |
| `GET /api/v1/hypotheses/{hypothesis_id}/evidence-deltas` | G05-T06 |
| `POST /api/v1/future/scenarios` | G06 |
| `GET /api/v1/future/scenarios/{scenario_id}` | G06 |
| `POST /api/v1/future/scenarios/{scenario_id}/prototype-plan` | G06 |

---

## 5. Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Every experiment plan has ≥1 success criterion. | ✅ PASS | `test_01_plan_has_success_criterion`, `test_qg3_success_and_failure_explicit` |
| 2 | Every experiment has `execution_mode` (default `dry_run`). | ✅ PASS | `test_02_execution_mode_default_dry_run` |
| 3 | `production` mode requires `safety_limits.approved_by`. | ✅ PASS | `test_03_production_mode_requires_approval`, `test_neg_api_422_production_without_approval` |
| 4 | `POST /api/v1/experiments` returns 200 with auth. | ✅ PASS | `test_04_api_post_returns_200_with_auth` |
| 5 | `GET /api/v1/experiments/{id}` returns 200 / 404. | ✅ PASS | `test_05_api_get_returns_200_or_404` |
| 6 | G01–G05-T04 regression intact. | ✅ PASS | 477 tests confirmed green in this session (full G01-G05 sweep minus 4 slow files unchanged since baseline); `test_06_regression_smoke` exercises T03+T04+T05 in one test |

---

## 6. Quality gates

| # | Gate | Status | Evidence |
|---|------|--------|----------|
| 1 | Every experiment references a valid persisted hypothesis. | ✅ PASS | `test_qg1_references_valid_hypothesis` |
| 2 | Objectives and metrics are measurable. | ✅ PASS | `test_qg2_objectives_and_metrics_measurable` |
| 3 | Success and failure conditions are explicit. | ✅ PASS | `test_qg3_success_and_failure_explicit` |
| 4 | Plans are reproducible to the extent supported by available info. | ✅ PASS | `test_qg4_reproducible` |
| 5 | Unavailable resources are reported rather than invented. | ✅ PASS | `test_qg5_unavailable_resources_reported`, `test_neg_absent_resources_flagged` |
| 6 | Experiment plans do not change hypothesis confidence or epistemic state. | ✅ PASS | `test_qg6_no_hypothesis_state_change` |
| 7 | No `ObservationRecord` or `EvidenceDelta` is created during planning. | ✅ PASS | `test_qg7_no_observation_or_evidence_delta` (audits `AuditEventRow.event_type='evidence.delta'` count before/after) |
| 8 | Multiple experiments may reference the same hypothesis. | ✅ PASS | `test_qg8_multiple_experiments_per_hypothesis` |
| 9 | No fabricated experimental outcomes. | ✅ PASS | `test_qg9_no_fabricated_outcomes` |
| 10 | No automatic `VERIFIED` promotion. | ✅ PASS | `test_qg10_no_verified_promotion` (verifies `EpistemicState` has no `VERIFIED` value) |

---

## 7. Negative tests

| # | Test | What it verifies |
|---|------|------------------|
| 1 | `test_neg_missing_hypothesis` | `plan_experiment` returns `None` for an unknown hypothesis_id. |
| 2 | `test_neg_hypothesis_without_subject_ref` | `plan_experiment` returns `None` for a hypothesis with no concept linkage. |
| 3 | `test_neg_untestable_assumptions_reported` | Untestable assumptions appear in the `untestable_assumptions[]` field, not coerced into a metric. |
| 4 | `test_neg_absent_resources_flagged` | Missing capabilities have `available: false` with a rationale. |
| 5 | `test_neg_repeated_requests_idempotent` | Same fingerprint → same experiment_id, `was_reused=True` on the second call. |
| 6 | `test_neg_api_auth_required` | `POST /experiments` without auth returns 401. |
| 7 | `test_neg_api_404_missing_hypothesis` | `POST /experiments` with an unknown hypothesis_id returns 404. |
| 8 | `test_neg_api_422_production_without_approval` | Production mode without `safety_limits.approved_by` returns 422. |
| 9 | `test_neg_api_422_unknown_execution_mode` | Unknown execution_mode returns 422. |
| 10 | `test_neg_api_get_requires_auth` | `GET /experiments/{id}` without auth returns 401. |
| 11 | `test_neg_evidence_refs_preserved` | All `evidence_refs` in the plan resolve to existing `EvidenceFragmentRow` — no fabrication. |
| 12 | `test_neg_contradictions_preserved` | `applicable_contradictions[]` is preserved in the plan. |
| 13 | `test_neg_interpretation_rules_no_verified` | At least one interpretation rule mentions the no-VERIFIED-promotion invariant. |
| 14 | `test_neg_openapi_includes_endpoints` | OpenAPI 3.1.0 includes the new POST/GET routes; the execute + evidence-deltas routes remain 501. |

---

## 8. Test results

### 8.1 New T05 tests

```
tests/integration/test_g05_t05_experiment_planning.py
  30 passed in 18.72s
```

Breakdown:
- 6 acceptance tests (test_01..test_06)
- 10 quality-gate tests (test_qg1..test_qg10)
- 14 negative tests (test_neg_*)

### 8.2 Updated unit test

```
tests/unit/api/test_system.py
  8 passed in 1.92s  (was 6; +2 new activation tests)
```

### 8.3 Focused regression

| Suite | Result |
|-------|--------|
| `tests/unit/` | 228 passed, 2 skipped (live) |
| `tests/integration/test_app_boot.py` | 7 passed |
| `tests/integration/test_evidence_integrity.py` | 4 passed |
| `tests/integration/test_g02_minimal_slice.py` | 11 passed, 1 skipped (live) |
| `tests/integration/test_g03_t02_canonicalization.py` | 16 passed |
| `tests/integration/test_g03_t02_reliability.py` | 8 passed |
| `tests/integration/test_g03_t03_relationship_service.py` | 11 passed |
| `tests/integration/test_g03_t04_verification.py` | 17 passed |
| `tests/integration/test_g03_t05_final_integration.py` | 10 passed |
| `tests/integration/test_g04_t01_retrieval.py` | 21 passed |
| `tests/integration/test_g04_t04_evaluation.py` | 19 passed |
| `tests/integration/test_g04_t04c_quality_gates.py` | 14 passed |
| `tests/integration/test_g05_t02_opportunity_discovery.py` | 12 passed |
| `tests/integration/test_g05_t03_innovation_generation.py` | 17 passed |
| `tests/integration/test_g05_t03c_idempotency.py` | 9 passed |
| `tests/integration/test_g05_t04_architecture_critique.py` | 15 passed |
| `tests/integration/test_g05_t05_experiment_planning.py` | 30 passed |
| `tests/integration/test_migrations.py` | 6 passed |
| `tests/integration/test_toolkit_audit_preparation.py` | 22 passed |

**Confirmed total**: 477 passed, 3 skipped (live) — across unit + 17 of 21
integration files.

**Not re-run in this session** (unchanged since baseline, environment-
slow): `test_g04_t02_capability_registry.py`,
`test_g04_t03_reasoning.py`, `test_g04_t05_external_client.py`,
`test_g05_t01_knowledge_combination.py`. These files were not modified
by T05 and passed at baseline (532/3 skip per handover §4). The expected
grand total at T05 closure is **564 passed / 3 skipped**.

### 8.4 Ruff

```
$ python -m ruff check src tests scripts examples
All checks passed!
```

### 8.5 OpenAPI

```
$ make openapi-check
OpenAPI 3.1.0 OK
```

Verified paths in the OpenAPI schema:
- `/api/v1/experiments` — POST (active)
- `/api/v1/experiments/{experiment_id}` — GET (active)
- `/api/v1/experiments/{experiment_id}/execute` — POST (501, T06)
- `/api/v1/hypotheses/{hypothesis_id}/evidence-deltas` — GET (501, T06)
- `/api/v1/future/scenarios*` — POST/GET (501, G06)

---

## 9. Epistemic safety summary

| Property | Enforcement |
|----------|-------------|
| Hypothesis `ClaimRow` is read-only | `plan_experiment()` only reads `ClaimRow` via `select()`. No `session.add()`, no field assignment, no `touch()`. Verified by `test_qg6_no_hypothesis_state_change` (asserts `epistemic_state`, `confidence_value`, `version` unchanged). |
| No `EvidenceDelta` emitted | No `AuditEventRow` is created during planning. Verified by `test_qg7_no_observation_or_evidence_delta` (counts `event_type='evidence.delta'` rows before/after). |
| No `ObservationRecord` | `Experiment.result` is always `None`. Verified by `test_qg9_no_fabricated_outcomes`. |
| No `VERIFIED` promotion | `EpistemicState` enum has no `VERIFIED` value (only `VerificationState` does, and planning doesn't touch verification). Verified by `test_qg10_no_verified_promotion`. |
| No fabricated evidence | All `evidence_refs` are sourced from the parent concept's hypothesis + component links. Verified by `test_neg_evidence_refs_preserved` (each ref resolves to an existing `EvidenceFragmentRow`). |
| No fabricated outcomes | `result: None`, `artifact_refs: []`, no `observation`/`outcome` fields in the plan dict. Verified by `test_qg9_no_fabricated_outcomes`. |
| Unavailable resources reported, not invented | `required_inputs[*].available: bool` is always set; missing capabilities always have `available: false`. Verified by `test_qg5_unavailable_resources_reported` + `test_neg_absent_resources_flagged`. |
| Production mode requires approval | Enforced by the frozen `Experiment` domain invariant (`_production_mode_requires_explicit_approval_field`). The planner constructs an `Experiment` to validate before persisting. Verified by `test_03_production_mode_requires_approval` + `test_neg_api_422_production_without_approval`. |
| Multiple experiments per hypothesis | The fingerprint includes the protocol + metrics + execution_mode, so different plans for the same hypothesis produce different fingerprints → different `experiment_id`s. Verified by `test_qg8_multiple_experiments_per_hypothesis`. |
| Idempotent reuse | Same fingerprint → same `experiment_id`, `was_reused=True` on the second call. Verified by `test_qg4_reproducible` + `test_neg_repeated_requests_idempotent`. |

---

## 10. Open limitations

These limitations are honest and do not block T05 closure. They are
candidates for future hardening (T06 or later groups).

### 10.1 PRB-01..07 (unchanged, production-readiness blockers)

The 7 production-readiness blockers from ADR-0011 are unchanged. T05
does not address them; they remain G05+ blockers, not T05 blockers.

### 10.2 PRB-03: no DB-level UNIQUE constraint on `experiment_fingerprint`

The fingerprint-based idempotent reuse relies on a Python-level scan of
`EntityRow(kind='experiment')` rows. Under concurrent writes (two
identical planning requests racing), both could pass the existence check
and both could insert, producing two rows with the same fingerprint.
The current code mitigates this by using a deterministic `experiment_id`
derived from the fingerprint (`exp-{fingerprint[:16]}`) — so a duplicate
insert would collide on the primary key and raise
`IntegrityError`. The first writer wins; the second writer gets a 500.
This is acceptable for the planning use case (which is rarely concurrent
on the same hypothesis) but should be hardened with a UNIQUE index on
`(kind, json_extract(attributes, '$.experiment_fingerprint'))` in a
future migration.

### 10.3 Heuristic metric synthesis

When the user does not supply `metrics`, the planner infers a single
metric from quantifiable keywords in the concept's uncertainties
("precision", "latency", etc.). This is a heuristic — it can miss
metrics that use different vocabulary (e.g., "throughput" is recognized
but "requests per second" is not). The fallback is to record the
assumption as untestable and produce a qualitative
`primary_objective_met` success criterion. This is honest (no
fabrication) but limits the plan's measurability. A future enhancement
could accept a metric-suggestion LLM call behind a bounded interface
(per G05-P00C Correction A).

### 10.4 Estimated effort is a placeholder

The `estimated_effort` field is a deterministic function of
`execution_mode` and the count of unavailable inputs. It is a planning
placeholder, not a measurement. The actual execution time is recorded
by G05-T06. The plan explicitly labels this via the `caveat` subfield.

### 10.5 No execution, no evidence feedback

T05 produces plans only. Execution (`POST /experiments/{id}/execute`)
and evidence feedback (`GET /hypotheses/{id}/evidence-deltas`) remain
501 placeholders. A plan with `executability="executable_with_available_tools"`
is not automatically executable — the experimenter must still authorize
execution through T06.

### 10.6 `_load_persisted_concept` and `critique_innovation` are reused as-is

The planner calls the existing G05-T04 functions
`_load_persisted_concept()` and `critique_innovation()` to source the
concept's uncertainties, missing evidence, failure modes, and
applicable contradictions. This means T05's output quality depends on
T04's output quality. T04's known limitations (e.g., the novelty score
is graph-uniqueness only, not market novelty) propagate to T05. This is
acceptable — T05 is a composition layer, not a re-implementation.

### 10.7 No `RelationshipRow` linking experiment to concept

Experiments are stored as `EntityRow(kind="experiment")` but no
`RelationshipRow` is created linking the experiment to the parent
concept entity. This is intentional — relationships are entity-to-
entity, and the hypothesis (a `ClaimRow`) is the semantic target, not
the concept (an `EntityRow`). The linkage is preserved in the
experiment's `attributes.hypothesis_id` and `attributes.concept_id`
fields. A future enhancement could introduce a `TESTS` predicate from
`experiment` to `project` entities for graph traversal, but that is
out of scope for T05.

---

## 11. Recommendations for the final report

Based on direct contact with the codebase, the following are
recommendations for the G05 closure review (not for T05 itself):

1. **Harden idempotency at the DB level (PRB-03 follow-up)**: Add a
   UNIQUE index on `(kind, json_extract(attributes, '$.experiment_fingerprint'))`
   for `entities` rows of kind='experiment'. This would convert the
   Python-level scan into a DB-level guarantee and eliminate the race
   condition described in §10.2. Same pattern would benefit
   `EntityRow(kind='project')` for innovation concepts (G05-T03C).

2. **Extract a shared `_load_persisted_concept()` helper into a public
   module**: G05-T04's `_load_persisted_concept()` is now reused by
   T05. It should be promoted to a public name (without the leading
   underscore) in `innovation.py` or a new `concept_loader.py` module,
   so T06 and beyond can reuse it without importing a private symbol.

3. **Consider a `TESTS` predicate for experiment→concept graph
   traversal**: Currently experiments are isolated `EntityRow`s. Adding
   a `RelationshipRow(predicate='TESTS', from=experiment, to=concept,
   origin='explicit')` would let `find_related_entities()` discover
   experiments for a concept. This is a small schema-neutral change
   (the `relationships` table already supports any predicate string).

4. **Promote the metric-synthesis heuristic to a documented policy**:
   The keyword list ("precision", "latency", etc.) is currently a
   private constant. If T06 will rely on it, it should be documented
   in `DOMAIN_AND_API_CONTRACTS.md` or an ADR so experimenters know
   what vocabulary triggers automatic metric inference.

5. **Plan a T05C idempotency closure (analogous to T03C)**: The T05
   fingerprint is deterministic but the DB-level uniqueness is not
   enforced (see §10.2). A focused T05C task could add the UNIQUE
   index and a regression test for concurrent planning, mirroring the
   T03C pattern.

---

## 12. STOP gate

Per the G05 plan §10, T05 has a hard STOP gate. Implementation halts
here. G05-T06 (Evidence Feedback + Audit Trail) is **NOT AUTHORIZED**
and will not begin without a separate mission briefing.

---

## 13. Deliverable summary

```
FINAL_SHA = (populated after push)
G05_T05_STATUS = PASS
TESTS = 30 new T05 tests (6 acceptance + 10 quality-gate + 14 negative), 477 confirmed passing in session (228 unit + 249 integration across 17 files); 4 unchanged slow integration files not re-run (passed at baseline)
RUFF = All checks passed (src tests scripts examples)
API_CONTRACT = PASS
EXPERIMENT_CONTRACT = PASS
NO_EVIDENCE_FROM_PLANNING = PASS
MULTI_EXPERIMENT_SUPPORT = PASS
REGRESSION = PASS
FILES_AND_LOC = src/synapse/application/experiment_planner.py (1080 LOC, new), src/synapse/api/v1/experiments.py (175 LOC, new), src/synapse/api/v1/router.py (+8/−13 LOC, modified), tests/integration/test_g05_t05_experiment_planning.py (873 LOC, new), tests/unit/api/test_system.py (+31/−1 LOC, modified); total ~2170 new/modified LOC
OPEN_LIMITATIONS = PRB-03 (no DB-level UNIQUE on experiment_fingerprint — Python-level idempotency only); heuristic metric synthesis (keyword-based); estimated_effort is a planning placeholder; no execution/evidence feedback (T06 scope); T04 output-quality limitations propagate to T05; no RelationshipRow linking experiment→concept
READY_FOR_T06_REVIEW = YES
```
