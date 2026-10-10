# G05-T05C — Experiment Planning Validation & Identity Closure

**Status**: PASS
**Task**: G05-T05C — Experiment Planning Validation & Identity Closure
**Starting SHA**: `9e40f01c8a7b10781fc0eeb244e5793c1755b2a7` (G05-T05 closure)
**Final SHA**: (populated after push)
**Date**: 2026-10-10
**Authoritative plan**: `reports/G05_INNOVATION_IMPLEMENTATION_PLAN.md` §G05-T05 + T05C mission briefing

---

## 1. Executive summary

G05-T05C closes the remaining correctness and validation gaps in
G05-T05 without expanding the architecture or implementing experiment
execution. Four validation areas were addressed:

1. **Experiment fingerprint correctness** — the fingerprint now
   includes every input that materially changes the resulting plan:
   `hypothesis_id`, `protocol` (normalized), `baseline` (normalized),
   `metrics` (sorted by key, with values), `execution_mode`,
   `safety_limits` (sorted by key, with values), and `cost_limits`
   (sorted by key, with values). Equivalent inputs produce the same
   fingerprint; materially different inputs produce different
   fingerprints.

2. **Measurable criteria integrity** — every success/failure criterion
   now carries a `calibration_status` field: `"operational"` (threshold
   set, fully operational), `"requires_calibration"` (threshold null,
   structural only — NOT an operational pass/fail rule), or
   `"untestable"` (no metric inferable, experimenter must redefine). A
   null threshold is never presented as an operational rule. No
   thresholds or results are fabricated.

3. **No evidence from planning** — verified: no `ObservationRecord`,
   no `EvidenceDelta` audit event, no hypothesis state mutation, no
   `VERIFIED` promotion (impossible by construction).

4. **API compatibility** — `POST /api/v1/experiments` and
   `GET /api/v1/experiments/{id}` remain functional, honor auth, and
   preserve the standard envelope. The frozen `Experiment` contract is
   unchanged.

**Status**: PASS. All 34 new T05C tests pass. The complete deterministic
suite (580 tests) passes in ~2 minutes. Ruff clean. OpenAPI 3.1.0 OK.

A prerequisite **TEST-INFRA-01** correction was performed first to
eliminate 18 recursive regression wrappers that made the full suite
un-executable in a single invocation. See
`reports/TEST_INFRA_01_REGRESSION_OPTIMIZATION.md` for details.

---

## 2. Files changed

### 2.1 Source changes

| File | Change | Δ LOC |
|------|--------|------:|
| `src/synapse/application/experiment_planner.py` | (1) Added `_normalize_text()` + `_normalize_dict()` helpers. (2) Extended `_compute_experiment_fingerprint()` to include `baseline`, `metrics` (with values), `safety_limits`, `cost_limits`. (3) Added `calibration_status` field to every success/failure criterion. (4) Updated docstrings. | +85 / −20 |

### 2.2 Test changes

| File | Change | Δ LOC |
|------|--------|------:|
| `tests/integration/test_g05_t05c_validation_closure.py` | NEW. 34 tests across 4 classes: `TestFingerprintCorrectness` (12), `TestFingerprintNoFalseReuse` (3), `TestMeasurableCriteriaIntegrity` (6), `TestRegressionScope` (6), `TestNormalization` (7). | +830 |

### 2.3 Test-infrastructure changes (TEST-INFRA-01)

See `reports/TEST_INFRA_01_REGRESSION_OPTIMIZATION.md` for the full
inventory. Summary:

| File | Change |
|------|--------|
| 16 integration test files | Removed 18 recursive regression wrappers |
| `tests/integration/test_g02_minimal_slice.py` | Added missing `@pytest.mark.live` to `test_live_arxiv_discovery_and_ingest` |
| `tests/integration/test_g04_t05_external_client.py` | Updated `test_11_predictable_error_responses` for T05 contract (carried forward) |
| `tests/unit/api/test_system.py` | T05 activation tests (carried forward) |
| `Makefile` | Added `test-deterministic` + `test-slow` targets |

### 2.4 No schema / migration / dependency changes

- No new ORM tables, no new migrations, no new pip dependencies.
- No domain-contract changes (the `Experiment` Pydantic record is
  unchanged — the `calibration_status` field lives in the synthesized
  plan dict, not in the frozen domain record).
- AgentCraft-Toolkit: STRICTLY READ ONLY.

---

## 3. Task 3 — Experiment fingerprint correctness

### 3.1 Before (T05 closure)

```python
def _compute_experiment_fingerprint(*, hypothesis_id, protocol, metric_keys, execution_mode):
    payload = "\n".join([
        hypothesis_id,
        _normalize_protocol(protocol),
        "\n".join(sorted(metric_keys)),  # keys only, no values
        execution_mode,
    ])
    return hashlib.sha256(payload.encode()).hexdigest()
```

**Missing**: `baseline`, metric **values** (dtype), `safety_limits`,
`cost_limits`. Two requests differing only in baseline would produce
the same fingerprint → incorrect reuse.

### 3.2 After (T05C)

```python
def _compute_experiment_fingerprint(
    *,
    hypothesis_id: str,
    protocol: str,
    baseline: str,
    metrics: dict[str, Any],
    execution_mode: str,
    safety_limits: dict[str, Any],
    cost_limits: dict[str, Any],
) -> str:
    metrics_items = ";".join(f"{k}={metrics[k]!r}" for k in sorted(metrics.keys()))
    payload = "\n".join([
        hypothesis_id,
        _normalize_protocol(protocol),
        _normalize_text(baseline),
        metrics_items,
        execution_mode,
        _normalize_dict(safety_limits),
        _normalize_dict(cost_limits),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()
```

**Now includes**: every material input. Normalization:
- Text fields (`protocol`, `baseline`): whitespace-collapsed + lowercased.
- Dicts (`metrics`, `safety_limits`, `cost_limits`): key-sorted, values
  stringified, None values dropped.
- Metric values (dtype) included, not just keys.

### 3.3 Verification (12 unit tests + 3 integration tests)

`TestFingerprintCorrectness` (12 tests):
- `test_fingerprint_includes_hypothesis_id` — different hypothesis → different fingerprint
- `test_fingerprint_includes_protocol` — different protocol → different fingerprint
- `test_fingerprint_includes_baseline` — different baseline → different fingerprint
- `test_fingerprint_includes_metric_keys` — different metric keys → different fingerprint
- `test_fingerprint_includes_metric_values` — different dtype → different fingerprint
- `test_fingerprint_includes_execution_mode` — different mode → different fingerprint
- `test_fingerprint_includes_safety_limits` — different safety → different fingerprint
- `test_fingerprint_includes_cost_limits` — different cost → different fingerprint
- `test_fingerprint_normalizes_equivalent_protocol_whitespace` — equivalent protocol → same fingerprint
- `test_fingerprint_normalizes_equivalent_baseline_whitespace` — equivalent baseline → same fingerprint
- `test_fingerprint_normalizes_equivalent_dict_key_order` — equivalent dict → same fingerprint
- `test_fingerprint_normalizes_equivalent_metric_key_order` — equivalent metrics → same fingerprint

`TestFingerprintNoFalseReuse` (3 integration tests):
- `test_different_baseline_creates_new_experiment`
- `test_different_safety_limits_creates_new_experiment`
- `test_different_metric_dtype_creates_new_experiment`

`TestNormalization` (7 unit tests on pure helpers):
- `_normalize_protocol`, `_normalize_text`, `_normalize_dict` behavior
  verified in isolation.

---

## 4. Task 4 — Measurable criteria integrity

### 4.1 The three calibration states

Every success/failure criterion now carries a `calibration_status` field:

| Status | Meaning | Threshold | Operational? |
|--------|---------|-----------|--------------|
| `"operational"` | Threshold is set; the rule is a fully operational pass/fail test. | non-null | YES |
| `"requires_calibration"` | Threshold is null; experimenter must set a value before execution. Structural only. | null | NO |
| `"untestable"` | No metric inferable; experimenter must redefine. Never operational. | null | NO |

### 4.2 Mapping to synthesis paths

| Synthesis path | `calibration_status` | `threshold` |
|----------------|----------------------|-------------|
| User-supplied metric → criterion | `requires_calibration` | `None` |
| No metric, but objective exists → qualitative criterion | `operational` | `True` (boolean) |
| No metric, no objective → last-resort placeholder | `untestable` | `None` |
| Failure-mode-based criterion | `operational` | `True` (boolean) |
| No-evidence-collected fallback | `operational` | `True` (boolean) |

### 4.3 No fabrication

The planner **never** fabricates a numeric threshold. Allowed threshold
values are:
- `None` (requires_calibration / untestable)
- `True` (operational boolean — qualitative criteria)
- `"unspecified"` (only when `dtype == "unspecified"`)

Numeric `int`/`float` thresholds are NEVER fabricated. The experimenter
sets them before execution (T06 scope).

### 4.4 Verification (6 tests)

`TestMeasurableCriteriaIntegrity`:
- `test_metric_criteria_are_requires_calibration` — user-metric criteria are `requires_calibration`, not `operational`
- `test_no_null_threshold_is_operational` — **the core invariant**: any criterion with `threshold is None` must have `calibration_status in ("requires_calibration", "untestable")`, never `"operational"`
- `test_operational_criteria_have_non_null_threshold` — converse: `operational` criteria must have non-null threshold
- `test_no_fabricated_thresholds` — thresholds are only None, True, or "unspecified" (with matching dtype); never int/float
- `test_calibration_status_field_always_present` — every criterion has the field, with a valid value
- `test_untestable_assumptions_are_separate_field` — untestable assumptions go in `untestable_assumptions[]`, not coerced into a metric

---

## 5. Task 5 — Regression and scope

### 5.1 Frozen Experiment contract preserved

`test_frozen_experiment_contract_preserved` verifies that `production`
mode still raises `ValueError` without `safety_limits.approved_by`. The
`Experiment` Pydantic record is unchanged.

### 5.2 Multiple experiments per hypothesis preserved

`test_multiple_experiments_per_hypothesis_preserved` verifies that two
different protocols/metrics for the same hypothesis produce two
different `experiment_id`s.

### 5.3 No observation or evidence delta during planning

`test_no_observation_or_evidence_delta` counts
`AuditEventRow.event_type == "evidence.delta"` before and after
planning; the count is unchanged. The plan's `result` and `artifact_refs`
are both empty/null.

### 5.4 No hypothesis-state mutation

`test_no_hypothesis_state_mutation` snapshots the hypothesis
`ClaimRow`'s `epistemic_state`, `confidence_value`, and `version`
before planning, then verifies they are unchanged after planning.

### 5.5 No automatic VERIFIED promotion

`test_no_verified_promotion` verifies `EpistemicState` has no `VERIFIED`
value (only `VerificationState` does), and the hypothesis's epistemic
state remains in the allowed set after planning.

### 5.6 API compatibility

`test_api_compatibility` verifies:
- `GET /experiments/{id}` without auth → 401
- `GET /experiments/{id}` with auth → 200 (returns the persisted plan)
- `GET /experiments/exp-does-not-exist` with auth → 404
- `POST /experiments` without auth → 401
- `POST /experiments` with auth → 200 (creates a new experiment with a
  different fingerprint when metrics differ)

---

## 6. Complete deterministic regression

### 6.1 Final suite run

```
$ make test-deterministic
580 passed, 3 deselected in 125.96s (0:02:05)
```

- **580 passed** (583 collected − 3 live deselected)
- **3 deselected** (`@pytest.mark.live`: 2 arxiv provider + 1 arxiv discovery+ingest)
- **0 failed**
- **Duration**: 125.96s

### 6.2 T05C tests

```
$ pytest tests/integration/test_g05_t05c_validation_closure.py
34 passed in 10.63s
```

### 6.3 T05 tests (unchanged, still pass)

```
$ pytest tests/integration/test_g05_t05_experiment_planning.py
30 passed in 19.24s
```

### 6.4 Ruff + OpenAPI

```
$ ruff check src tests scripts examples
All checks passed!

$ make openapi-check
OpenAPI 3.1.0 OK
```

---

## 7. Open limitations

### 7.1 PRB-03 (unchanged)

No DB-level UNIQUE constraint on `experiment_fingerprint`. Idempotent
reuse relies on Python-level scan. Documented; not addressed by T05C.

### 7.2 Heuristic metric synthesis (unchanged)

When no metrics are supplied, the planner infers a metric from
quantifiable keywords in uncertainties. The fallback is
`primary_objective_met` (operational, boolean) or
`experimenter_to_define` (untestable). No fabrication.

### 7.3 Estimated effort is a placeholder (unchanged)

`estimated_effort` is a deterministic function of `execution_mode` and
unavailable-input count. Labeled via `caveat` subfield.

### 7.4 No execution / evidence feedback (T06 scope)

`POST /experiments/{id}/execute` and `GET /hypotheses/{id}/evidence-deltas`
remain 501 placeholders. T05C does not implement them.

### 7.5 Test-infrastructure slow files (TEST-INFRA-01 §6.5)

The four "slow" integration files remain 30-60s each due to per-test DB
creation. The recursion elimination removed the ~3× subprocess
multiplier, but the base cost remains. Future optimization
(session-scoped engine, `pytest-xdist`) is out of scope.

### 7.6 Live-test marker audit (recommendation)

One missing `@pytest.mark.live` was fixed during TEST-INFRA-01. A
future audit should verify no other real-network test is missing the
marker.

---

## 8. Deliverable summary

```
FINAL_SHA = (populated after push)
FULL_SUITE = 580 passed, 3 deselected (live), 0 failed, 125.96s
RUFF = All checks passed (src tests scripts examples)
OPENAPI = OpenAPI 3.1.0 OK
FINGERPRINT_CORRECTNESS = PASS
MEASURABLE_CRITERIA = PASS
NO_EVIDENCE_FROM_PLANNING = PASS
API_REGRESSION = PASS
T05C_STATUS = PASS
OPEN_LIMITATIONS = PRB-03 (no DB-level UNIQUE on fingerprint); heuristic metric synthesis (keyword-based, no fabrication); estimated_effort is placeholder; no execution/evidence feedback (T06 scope); slow integration files remain (base cost, not recursive); recommend live-test marker audit
READY_FOR_T06_REVIEW = YES
```
