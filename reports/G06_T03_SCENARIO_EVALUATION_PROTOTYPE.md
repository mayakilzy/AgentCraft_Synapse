# G06-T03 — Scenario Evaluation + Prototype Planning

**Status**: PASS
**Task**: G06-T03 — Scenario Evaluation + Prototype Planning
**Starting SHA**: `170161c639f26297e8220627107cef523d4182eb` (G06-T02A closure)
**Date**: 2026-10-11
**Developer**: GLM
**Reviewer**: ChatGPT

---

## 1. Executive summary

G06-T03 implements deterministic scenario evaluation and activates the
prototype-plan API endpoint with the approved two-mode design.

The evaluation service (`src/synapse/application/scenario_evaluator.py`)
produces a structured, deterministic assessment of a persisted scenario
across seven dimensions: evidence coverage, source provenance,
dependency readiness, contradiction exposure, assumption assessment,
horizon alignment, and an overall categorical result. The uncertainty
field follows the corrected formula `uncertainty = 1.0 -
evidence_coverage` — it measures only the evidence-coverage gap, not a
probability of future occurrence.

The prototype planner (`src/synapse/application/prototype_planner.py`)
implements Mode A (linked G05 innovation → delegates to
`plan_experiment()` with caller-supplied metrics) and Mode B (no linked
innovation → structured proposal, no experiment created). Mode A never
fabricates metrics from missing dependencies; Mode B never calls
`plan_experiment()`.

The API (`src/synapse/api/v1/future.py`) adds the `/evaluate` endpoint
and replaces the 501 placeholder on `/prototype-plan` with the real
two-mode implementation.

**Result**: 36 focused tests pass. Full deterministic regression: 720
passed, 0 failed. Combined PostgreSQL suite: 38 passed. Ruff clean.
OpenAPI OK.

---

## 2. Authoritative starting SHA

```
HEAD:        170161c639f26297e8220627107cef523d4182eb
origin/main: 170161c639f26297e8220627107cef523d4182eb
```

---

## 3. Implemented evaluation dimensions

### A. Evidence coverage

`evidence_coverage = verified_fragments / total_evidence_refs`

A fragment "meets the verification criterion" if it is cited by a claim
that has been independently verified via `assess_claim()` with outcome
`source_supported`, `corroborated`, or `verified`. Fragment existence
alone does NOT count as verified evidence. Zero evidence refs → 0.0.

### B. Source provenance

- `distinct_source_uri_count`: factual count of distinct
  `EvidenceFragmentRow.source_uri` values.
- `independent_origin_count`: conservative (1 per PRB-04) when there is
  ≥1 fragment; 0 when there are zero fragments. Multiple URLs without
  verified independence never inflate the count beyond 1.

### C. Dependency readiness

Each `missing_dependency` is classified as:
- `research`: entity absent or only HYPOTHESIZED/INFERRED claims.
- `engineering`: entity has SUPPORTED claim + LIMITS/REQUIRES edge.
- `integration`: entity has SUPPORTED claim, no LIMITS/REQUIRES —
  integration is unresolved (caller attested).
- `caller_attested`: entity exists but no claims at all.
- `unknown`: entity does not exist in KB.

Overall readiness uses precedence: research > engineering > integration
> caller_attested > unknown. Caller-supplied deps are NOT independently
verified.

### D. Contradiction exposure

Identifies evidence-backed contradictions: claims citing the scenario's
evidence fragments with a CONTESTED verification outcome, or claims
whose `contradicting_refs` reference the scenario's fragments. Absence
of supporting evidence is NOT a contradiction.

### E. Assumption assessment

Each assumption is classified as `supported`, `contested`,
`not_evidenced`, or `unknown` based on the verification outcome of
claims citing the scenario's evidence fragments. Natural-language
similarity alone is not proof.

### F. Horizon alignment

Reassesses whether the persisted F0-F4 horizon is consistent with the
evidence and dependency classifications. Returns `aligned`,
`insufficient_evidence`, `misaligned`, or `unknown`. Never silently
upgrades a scenario's horizon.

### G. Overall evaluation

Deterministic categorical result with precedence:
1. `inconsistent` — any contradiction exists.
2. `speculative` — horizon is F4.
3. `needs_more_evidence` — evidence_coverage < 0.5.
4. `testable` — otherwise.

---

## 4. Evidence verification semantics

The evaluator distinguishes:
- **Fragment exists**: the `EvidenceFragmentRow` is in the DB.
- **Fragment supports a claim**: a `ClaimRow` cites the fragment in its
  `evidence_refs`.
- **Claim is independently verified**: the claim has a
  `verification.assessed` audit event with outcome `source_supported`,
  `corroborated`, or `verified`.

Only the third level counts toward `evidence_coverage`. This prevents
fragment existence alone from inflating the coverage.

---

## 5. Uncertainty interpretation

```
uncertainty = 1.0 - evidence_coverage
uncertainty_method = "1.0 - evidence_coverage"
```

**What it measures**: the evidence-coverage gap.

**What it does NOT measure**:
- Probability of future occurrence.
- Probability of experimental success.
- Overall epistemic certainty.
- Technical feasibility.

No horizon or assumption coefficients are added (Correction C,
G06-P00C). The `uncertainty_limitation` field in the evaluation output
documents this explicitly.

---

## 6. Mode A / Mode B behavior

### Mode A — Linked G05 Innovation

When the scenario has a `linked_innovation_concept_id` that resolves to
a persisted `EntityRow(kind="project")` with a hypothesis (`ClaimRow`
with `subject_ref=concept_id`):

1. Validate the innovation and hypothesis references.
2. Require caller-supplied `metrics` (`{name: dtype}` dict). If missing
   → `ValueError`.
3. Call `plan_experiment(session, hypothesis_id, protocol=...,
   metrics=...)`.
4. Return the real experiment plan + scenario linkage.
5. Record `AuditEventRow(event_type="scenario.prototype_linked")`.

Never fabricates metrics from missing dependencies. Never bypasses
hypothesis resolution. Never creates unsupported relationship predicates.

### Mode B — No Linked Innovation

When no valid linked innovation exists:

1. Return a structured prototype proposal.
2. Include genuine unresolved dependencies as investigation targets.
3. Identify assumptions that should be tested.
4. Explain which G05 prerequisites are missing.
5. Do NOT call `plan_experiment()`.
6. Do NOT create an Experiment entity.
7. Record `AuditEventRow(event_type="scenario.prototype_proposed")`.

A proposal is clearly distinguished from a persisted experiment plan
by the `mode` field (`"prototype_proposal"` vs `"linked_experiment"`).

---

## 7. G05 contract compatibility

- `plan_experiment()` is called only in Mode A, with caller-supplied
  metrics. Its contract (hypothesis_id + innovation concept resolution)
  is preserved.
- No G05 service signature is modified.
- No G05 domain contract is modified.
- No G05 API endpoint is modified.
- No G05 test is modified (except the T02 test that expected 501 on
  prototype-plan, now updated to expect 200).
- The scenario-to-experiment linkage uses audit-event references, not
  new relationship predicates.

---

## 8. API changes

| Route | Method | Status | Behavior |
|-------|--------|--------|----------|
| `/api/v1/future/scenarios/{id}/evaluate` | POST | 200 | Deterministic evaluation. 404 if not found. |
| `/api/v1/future/scenarios/{id}/prototype-plan` | POST | 200 | Two-mode: Mode A (linked experiment) or Mode B (proposal). 404 if not found. 422 on invalid input. |

All routes require authentication. Standard error envelope.

---

## 9. Audit and idempotency behavior

- `evaluate_scenario()` emits `AuditEventRow(event_type=
  "scenario.evaluated")` — one per evaluation call (each evaluation is
  a distinct observation).
- Mode A emits `scenario.prototype_linked`; Mode B emits
  `scenario.prototype_proposed`.
- Mode A retry: `plan_experiment()` uses its own PRB-03 fingerprint
  idempotency, so the experiment is reused (no duplicate). The audit
  event is per-call (distinct observation).
- No duplicate audit records for idempotent scenario retrievals.

---

## 10. Limitations

1. **Scenario revision**: not implemented (G06-T04 scope).
2. **End-to-end G06 acceptance**: not implemented (G06-T04 scope).
3. **Assumption assessment heuristic**: maps assumptions to claims via
   evidence-ref overlap (defensible), not natural-language similarity.
   A more sophisticated mapping is out of scope.
4. **`_find_hypothesis_for_concept`**: selects the most recent
   `ClaimRow` with `subject_ref=concept_id`. If multiple hypotheses
   exist, the most recent is used. This is sufficient for G06-T03.
5. **Evaluation does not persist `uncertainty` on the scenario**: the
   `uncertainty` field is computed per-evaluation and returned in the
   response. It is NOT written back to the scenario's attributes (the
   approved service contract does not require it).

---

## 11. Exact test commands and results

### 11.1 G06-T03 focused tests

```
$ pytest tests/integration/test_g06_t03_evaluation_prototype.py --no-cov -q -p no:cacheprovider
36 passed in 10.17s
```

### 11.2 G06-T01 regression

```
$ pytest tests/integration/test_g06_t01_future_signals.py --no-cov -q -p no:cacheprovider
29 passed in 5.75s
```

### 11.3 G06-T02 regression

```
$ pytest tests/integration/test_g06_t02_scenario_construction.py --no-cov -q -p no:cacheprovider
46 passed in 10.60s
```

### 11.4 Full deterministic regression (SQLite)

```
$ pytest tests/ --no-cov -q -p no:cacheprovider -m "not live"
720 passed, 34 skipped, 3 deselected in 170.85s
```

### 11.5 Combined PostgreSQL integration suite

```
$ pytest tests/integration/test_prb_03_postgresql_concurrency.py \
         tests/integration/test_prb_03_determinism.py \
         tests/integration/test_prb_03_migration_safety.py \
         tests/integration/test_g05_t06_audit_concurrency.py \
         tests/integration/test_g06_t02_scenario_construction.py::test_corr_b_concurrent_same_run_id_same_payload \
         tests/integration/test_g06_t02_scenario_construction.py::test_corr_b_concurrent_same_run_id_conflicting_payload \
         tests/integration/test_g06_t03_evaluation_prototype.py::test_add_10_mode_a_calls_real_planner \
         tests/integration/test_g06_t03_evaluation_prototype.py::test_add_13_mode_a_retry_no_duplicate \
         --no-cov -q -p no:cacheprovider
38 passed in 56.26s
```

### 11.6 Ruff

```
$ ruff check src tests scripts examples
All checks passed!
```

### 11.7 OpenAPI

```
$ make openapi-check
OpenAPI OK
```

---

## 12. Modified files

| File | Change | LOC |
|------|--------|----:|
| `src/synapse/application/scenario_evaluator.py` | NEW. 7-dimension evaluation service. | +500 |
| `src/synapse/application/prototype_planner.py` | NEW. Two-mode prototype planning. | +260 |
| `src/synapse/api/v1/future.py` | Modified. Added `/evaluate` endpoint; replaced 501 on `/prototype-plan` with real two-mode implementation. | +120 / -30 |
| `tests/integration/test_g06_t03_evaluation_prototype.py` | NEW. 36 integration tests. | +1100 |
| `tests/integration/test_g06_t02_scenario_construction.py` | Modified. Updated `test_add_08` — prototype-plan is now 200 (was 501). | +12 / -8 |
| `tests/unit/api/test_system.py` | Modified. Updated comments — all future routes are now functional. | +4 / -2 |

**No production code outside the new/modified files was changed.** G04/G05
services, domain contracts, ORM models, migrations all untouched.
Toolkit READ ONLY.

---

## 13. Deliverable summary

```
G06_T03_STATUS = PASS
AUTHORITATIVE_START_SHA = 170161c639f26297e8220627107cef523d4182eb
SCENARIO_EVALUATOR = PASS
EVIDENCE_COVERAGE_INTEGRITY = PASS
SOURCE_INDEPENDENCE = PASS
DEPENDENCY_READINESS = PASS
ASSUMPTION_ASSESSMENT = PASS
CONTRADICTION_HANDLING = PASS
HORIZON_ALIGNMENT = PASS
UNCERTAINTY_SEMANTICS = PASS
MODE_A_EXPERIMENT_LINKAGE = PASS
MODE_B_PROTOTYPE_PROPOSAL = PASS
IDEMPOTENCY_AND_AUDIT = PASS
API_AUTH_AND_VALIDATION = PASS
FOCUSED_TESTS = 36 passed
FULL_REGRESSION = 720 passed, 34 skipped (PG), 3 deselected (live), 0 failed
COMBINED_POSTGRESQL_TESTS = 38 passed (34 PRB-03/G05-T06 + 2 G06-T02A concurrency + 2 G06-T03 Mode A on PG)
RUFF = PASS
OPENAPI = PASS
MODIFIED_FILES = src/synapse/application/scenario_evaluator.py (NEW), src/synapse/application/prototype_planner.py (NEW), src/synapse/api/v1/future.py (modified), tests/integration/test_g06_t03_evaluation_prototype.py (NEW), tests/integration/test_g06_t02_scenario_construction.py (modified), tests/unit/api/test_system.py (modified)
REMAINING_LIMITATIONS = scenario revision deferred to G06-T04; end-to-end acceptance deferred to G06-T04; assumption assessment uses evidence-ref overlap heuristic; evaluation does not persist uncertainty on scenario
FINAL_SHA = 801f6f8ef1e1cd935e448c9fa9058cffb86820c3
READY_FOR_G06_T04_REVIEW = YES
```

---

## 14. Stop condition

G06-T03 is complete. The scenario evaluation service and prototype-plan
endpoint are implemented, tested, and verified. No production code
outside the new/modified files was changed. All gates pass.

**Do not begin G06-T04 without explicit reviewer authorization.**

---

## 15. G06-T03A — Assumption Evidence Integrity & Hypothesis Selection

G06-T03 was functionally accepted subject to two focused integrity
corrections (G06-T03A). Both are applied and verified below.

### 15.1 Correction A — Assumption-to-evidence integrity

**Root cause**: `_assess_assumptions()` classified assumptions as
`supported` or `contested` based on shared evidence fragments between
the scenario and claims — without verifying that the claim actually
addresses the specific assumption. This was insufficient: shared
evidence fragments alone do not establish a semantic association
between an assumption and a claim.

**Correction**: Rewrote `_assess_assumptions()` to require an
**explicit, defensible association** between the assumption and a
claim, stored in the scenario's `risks` dict under
`assumption_associations`:
```json
{"assumption_associations": {"assumption text": "claim-id"}}
```

- If an explicit association exists and the claim has a positive
  verification outcome → `supported`.
- If an explicit association exists and the claim has a contested
  outcome → `contested`.
- If no explicit association exists → `unknown` (with an explicit
  limitation note). Absence of support is NOT contradiction.
- No substring matching. No shared-fragment-only inference. No LLM.

Also fixed `_safe_json_loads()` to handle audit event payloads that
SQLAlchemy auto-deserializes from JSON text to dicts.

**Tests added** (6):
- `test_corr_a_no_explicit_association_returns_unknown`
- `test_corr_a_similar_text_not_supported`
- `test_corr_a_shared_evidence_not_supported`
- `test_corr_a_genuine_association_with_verified_evidence`
- `test_corr_a_genuine_association_with_contradiction`
- `test_corr_a_absence_of_support_not_contradiction`

### 15.2 Correction B — Explicit hypothesis selection for Mode A

**Root cause**: `_find_hypothesis_for_concept()` silently selected the
most recently created hypothesis when multiple eligible hypotheses
existed. This was non-deterministic from the caller's perspective.

**Correction**: Replaced `_find_hypothesis_for_concept()` with three
explicit helpers:
- `_load_concept_entity()` — verifies the concept is `kind="project"`.
- `_find_eligible_hypotheses()` — returns ALL eligible hypotheses (not
  just the most recent).
- `_select_hypothesis()` — implements the selection rules:
  1. Caller supplies `hypothesis_id` → verify it belongs to the linked
     concept; reject if it belongs to another innovation.
  2. No `hypothesis_id` + exactly one hypothesis → auto-select.
  3. No `hypothesis_id` + multiple hypotheses → `ValueError` (the
     caller must specify which hypothesis to use).
  4. No hypotheses → Mode B (proposal).

Added `hypothesis_id` parameter to `plan_prototype()` and the API
`PrototypePlanRequest` model.

**Tests added** (10):
- `test_corr_b_one_eligible_hypothesis_auto_selected`
- `test_corr_b_multiple_hypotheses_without_selection_fails`
- `test_corr_b_multiple_hypotheses_with_explicit_selection`
- `test_corr_b_cross_innovation_hypothesis_rejected`
- `test_corr_b_unknown_hypothesis_id_rejected`
- `test_corr_b_mode_a_idempotent_retry`
- `test_corr_b_mode_b_unchanged`
- `test_corr_b_api_hypothesis_id_in_request`
- `test_corr_b_api_multiple_hypotheses_422`
- `test_corr_b_openapi_includes_hypothesis_id`

### 15.3 G06-T03A validation results

- **Focused tests**: 52 passed (36 original G06-T03 + 16 G06-T03A
  correction tests) in 15.30s.
- **G06-T01 regression**: 29 passed.
- **G06-T02 regression**: 46 passed (1 flaky concurrent test on SQLite;
  passes on re-run and on PostgreSQL).
- **Full deterministic regression**: 736 passed, 34 skipped (PG),
  3 deselected (live), 0 failed in 176.54s.
- **Combined PostgreSQL suite**: 36 passed in 55.29s (34 PRB-03/G05-T06
  + 2 G06-T03A Mode A on real PG).
- **Ruff**: clean.
- **OpenAPI**: OK.

### 15.4 G06-T03A deliverable summary

```
G06_T03A_STATUS = PASS
AUTHORITATIVE_START_SHA = adc186f9f20d3cd2deec472043e40f7742deafaa
ASSUMPTION_EVIDENCE_INTEGRITY = PASS
EXPLICIT_HYPOTHESIS_SELECTION = PASS
CROSS_INNOVATION_HYPOTHESIS_REJECTION = PASS
MODE_A_IDEMPOTENCY = PASS
MODE_B_REGRESSION = PASS
FOCUSED_TESTS = 52 passed
FULL_REGRESSION = 736 passed, 34 skipped (PG), 3 deselected (live), 0 failed
COMBINED_POSTGRESQL_TESTS = 36 passed (34 PRB-03/G05-T06 + 2 G06-T03A Mode A on PG)
RUFF = PASS
OPENAPI = PASS
MIGRATIONS_CREATED = NO
FINAL_SHA = bee16d5a569d30b461096189215af9c4f535bb13
G06_T03_READY_TO_CLOSE = YES
```
