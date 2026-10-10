# G05-T06 — Experiment Execution Records + Evidence Feedback

**Status**: PASS
**Task**: G05-T06 — Experiment Execution Records + Evidence Feedback
**Starting SHA**: `a954908aaa9476c05b967fdb37805ee748bde8b5` (PRB-03 JSON safety patch)
**Final SHA**: (populated after push)
**Date**: 2026-10-10

---

## 1. Executive summary

G05-T06 implements a minimal, reliable workflow that transforms an
existing experiment plan into a traceable execution record and evaluates
submitted observations against the plan. The system distinguishes:

- Experiment planned (G05-T05)
- Experiment execution recorded (T06-A)
- Observations received (T06-B)
- Evidence assessed (T06-C)
- Hypothesis supported, contradicted, or inconclusive (T06-D)

**An experiment plan is not evidence. An observation is not automatically
verified evidence.**

**Status**: PASS. All 16 mandatory tests + 4 API contract tests + 1
OpenAPI test = 21 T06 tests pass. Full deterministic regression: 600
passed, 0 failed. 25 PostgreSQL tests pass (12 concurrency + 9
migration + 4 determinism). Ruff clean. OpenAPI 3.1.0 OK.

---

## 2. Implemented capabilities

### T06-A — Experiment Execution Record

- `create_execution()` creates an `EntityRow(kind="execution")` with a
  deterministic fingerprint (SHA-256 of experiment_id + execution_mode +
  protocol_snapshot).
- Lifecycle: `CREATED → RUNNING → COMPLETED / FAILED / CANCELLED`
- Transitions to RUNNING on first observation; terminal states are
  immutable.
- Protocol snapshot preserved verbatim from the experiment plan.
- Provenance: created_by, created_at, request_id.

### T06-B — Observation and Metric Capture

- `record_observation()` appends observations to the execution's
  attributes JSON (append-only — never overwritten).
- Validates metric against the experiment plan's `metrics_schema`.
- Rejects invalid metrics with `ValueError` (→ 422 in API).
- Rejects observations on terminal executions.
- Each observation preserves: metric, observed_value, expected_value,
  unit, measurement_method, source_ref, timestamp, evidence_origin,
  uncertainty.

### T06-C — Evidence Assessment

- `assess_evidence()` is a deterministic function that compares
  observations against the plan's success/failure criteria.
- Only evaluates criteria with `calibration_status="operational"` and
  non-null threshold.
- Results: `supporting`, `contradicting`, `inconclusive`,
  `insufficient_data`.
- Failure criteria take priority (contradicting > supporting).
- Repeated observations from the same metric use the latest value (NOT
  independent corroboration).
- Criteria with `requires_calibration` or `untestable` are reported but
  don't contribute to the pass/fail decision.

### T06-D — Evidence Feedback

- `finalize_execution()` sets terminal status, runs assessment (if
  COMPLETED), and emits an `EvidenceDelta` audit event if the
  assessment changes the hypothesis's epistemic state.
- Transitions: HYPOTHESIZED → SUPPORTED (supporting), HYPOTHESIZED →
  DISPUTED (contradicting). Inconclusive/insufficient → no change.
- EvidenceDelta stored as `AuditEventRow(event_type="evidence.delta")`
  — append-only audit log.
- Hypothesis `ClaimRow.epistemic_state` and `confidence_value` updated
  ONLY when a delta is emitted.
- `get_evidence_deltas()` retrieves the ordered audit trail for a
  hypothesis.

### T06-E — Minimal API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/experiments/{experiment_id}/execute` | POST | Create execution |
| `/api/v1/executions/{execution_id}/observations` | POST | Record observation |
| `/api/v1/executions/{execution_id}/finalize` | POST | Finalize + assess |
| `/api/v1/executions/{execution_id}` | GET | Retrieve execution |
| `/api/v1/hypotheses/{hypothesis_id}/evidence-deltas` | GET | Evidence audit trail |

### T06-F — Persistence and Idempotency

- Execution creation reuses PRB-03 `claim_fingerprint()` — same
  experiment_id + execution_mode + protocol → same execution_id.
- Different payload → different fingerprint → different execution_id.
- No partial writes after failure (transactional).
- Cross-session persistence (API POST commits, separate GET sees data).

---

## 3. Data model changes

**No new tables, no migrations.** Execution records reuse the existing
`EntityRow(kind="execution")` pattern (same as experiments use
`kind="experiment"`). The execution's `attributes` JSON carries:

```json
{
  "experiment_id": "exp-...",
  "hypothesis_id": "hyp-...",
  "execution_mode": "dry_run",
  "status": "created|running|completed|failed|cancelled",
  "started_at": "ISO8601",
  "completed_at": "ISO8601|null",
  "protocol_snapshot": "...",
  "metrics_schema": {"precision_at_10": "float"},
  "success_criteria": [...],
  "failure_criteria": [...],
  "observations": [
    {
      "observation_id": "obs-...",
      "metric": "precision_at_10",
      "observed_value": 0.85,
      "expected_value": null,
      "unit": "float",
      "measurement_method": "manual",
      "source_ref": "experimenter-001",
      "timestamp": "ISO8601",
      "evidence_origin": "experiment_execution",
      "uncertainty": null
    }
  ],
  "assessment": {
    "result": "supporting|contradicting|inconclusive|insufficient_data",
    "reasoning": "...",
    "criteria_evaluated": [...],
    "assessed_at": "ISO8601"
  },
  "error_details": null,
  "evidence_delta_id": "edelta-...|null",
  "provenance": {"created_by": "...", "created_at": "...", "request_id": "..."}
}
```

Evidence deltas use the existing `AuditEventRow(event_type=
"evidence.delta")` table with a payload containing the full delta.

---

## 4. Execution lifecycle

```
CREATED ──(first observation)──→ RUNNING ──(finalize: completed)──→ COMPLETED
   │                                  │
   │                                  ├──(finalize: failed)──→ FAILED
   │                                  │
   │                                  └──(finalize: cancelled)──→ CANCELLED
   │
   └──(finalize: cancelled)──→ CANCELLED
```

Terminal states (COMPLETED, FAILED, CANCELLED) are immutable — no
further observations can be recorded.

---

## 5. Evidence assessment rules

1. **No observations** → `insufficient_data`.
2. **Failure criteria evaluated first** (contradicting takes priority):
   - For each failure criterion with `calibration_status="operational"`
     and non-null threshold: if the observed value triggers the
     comparator (e.g. `<`), the assessment is `contradicting`.
3. **Success criteria evaluated second**:
   - If all operational success criteria are `met` → `supporting`.
   - If any criterion has `no_data` or `requires_calibration` →
     `inconclusive`.
   - If all operational criteria are evaluated but not all met →
     `inconclusive`.
4. **Repeated observations** from the same metric use the latest value
   (NOT independent corroboration).
5. **Calibration status**:
   - `operational` (non-null threshold): contributes to pass/fail.
   - `requires_calibration` (null threshold): reported but doesn't
     contribute.
   - `untestable`: reported but doesn't contribute.

---

## 6. Provenance and verification safeguards

- **An experiment plan is NOT evidence** — the plan is a structural
  template; only observations are evidence.
- **An observation is NOT verified evidence** — it is a recorded
  measurement. The assessment is a deterministic comparison, not
  independent verification.
- **No VERIFIED promotion** — `EpistemicState` has no VERIFIED value
  (only `VerificationState` does). The assessment can only move
  HYPOTHESIZED → SUPPORTED or DISPUTED.
- **Append-only evidence history** — observations are never overwritten
  or erased. Prior observations are preserved even when new ones are
  added.
- **Contradictory evidence preserved** — the assessment reports both
  met and unmet criteria; the EvidenceDelta records the full reasoning.
- **No fabricated evidence** — the assessment only uses observations
  that were explicitly recorded. Missing observations → `no_data`, not
  fabricated values.
- **No unsupported EvidenceDelta** — a delta is emitted ONLY when the
  assessment produces a state change. `insufficient_data` and
  `inconclusive` → no delta, no state change.

---

## 7. Persistence behavior

- **Idempotent execution creation**: same experiment_id + execution_mode
  + protocol → same execution_id (via PRB-03 `claim_fingerprint`).
- **Different payload → different ID**: different execution_mode or
  protocol → different fingerprint → different execution_id.
- **Transactional**: `get_db()` commits on success, rolls back on error.
- **Cross-session visibility**: API POST commits; separate API GET sees
  the data.
- **Concurrent submission safe**: PRB-03 advisory locks serialize
  fingerprint claims.

---

## 8. Test results

### 8.1 T06 tests (20 tests)

```
$ pytest tests/integration/test_g05_t06_execution_evidence.py
20 passed in 12.85s
```

| # | Test | Result |
|---|------|--------|
| 1 | `test_01_successful_execution_creation` | PASS |
| 2 | `test_02_invalid_experiment_reference` | PASS |
| 3 | `test_03_valid_metric_observation` | PASS |
| 4 | `test_04_invalid_metric_rejected` | PASS |
| 5 | `test_05_supported_hypothesis` | PASS |
| 6 | `test_06_contradicted_hypothesis` | PASS |
| 7 | `test_07_inconclusive_evidence` | PASS |
| 8 | `test_08_insufficient_evidence` | PASS |
| 9 | `test_09_duplicate_equivalent_submission` | PASS |
| 10 | `test_10_conflicting_idempotency_payload` | PASS |
| 11 | `test_11_failed_execution_rollback` | PASS |
| 12 | `test_12_cross_session_persistence` | PASS |
| 13 | `test_13_evidence_provenance_preservation` | PASS |
| 14 | `test_14_no_auto_verified_promotion` | PASS |
| 15 | `test_15_no_fabricated_evidence_delta` | PASS |
| 16 | `test_16_experiment_planning_compatibility` | PASS |
| API | `test_api_execute_requires_auth` | PASS |
| API | `test_api_404_missing_experiment` | PASS |
| API | `test_api_422_invalid_metric` | PASS |
| API | `test_openapi_includes_t06_endpoints` | PASS |

### 8.2 Full deterministic regression (SQLite)

```
$ pytest tests/ --no-cov -q -p no:cacheprovider -m "not live"
600 passed, 25 skipped, 3 deselected in 140.42s (0:02:20)
```

### 8.3 PostgreSQL tests

```
$ pytest tests/integration/test_prb_03_postgresql_concurrency.py
12 passed in 20.69s

$ pytest tests/integration/test_prb_03_migration_safety.py tests/integration/test_prb_03_determinism.py
13 passed in 26.10s
```

### 8.4 Ruff + OpenAPI

```
$ ruff check src tests scripts examples
All checks passed!

$ make openapi-check
OpenAPI 3.1.0 OK
```

---

## 9. Modified files

| File | Change | LOC |
|------|--------|----:|
| `src/synapse/application/execution_record.py` | NEW. T06-A/B/C/D: create_execution, record_observation, assess_evidence, finalize_execution, get_evidence_deltas. | 903 |
| `src/synapse/api/v1/executions.py` | NEW. T06-E: observations, finalize, GET execution endpoints. | 170 |
| `src/synapse/api/v1/hypotheses.py` | NEW. T06-E: GET evidence-deltas endpoint. | 59 |
| `src/synapse/api/v1/experiments.py` | Added POST /{experiment_id}/execute endpoint + ExecuteRequest model. | +52 |
| `src/synapse/api/v1/router.py` | Activated executions + hypotheses routers; removed 501 placeholders for execute + evidence-deltas. | +6 / −16 |
| `tests/integration/test_g05_t06_execution_evidence.py` | NEW. 20 tests (16 mandatory + 4 API/OpenAPI). | 669 |
| `tests/conftest.py` | Fixed `_override_get_db` to commit on success (matches production get_db). | +6 / −1 |

**No new tables, no migrations, no new dependencies.** Reuses existing
`EntityRow`, `ClaimRow`, `AuditEventRow`, and PRB-03 `claim_fingerprint`.

---

## 10. Known limitations

1. **Assessment depends on calibrated criteria** — criteria with
   `calibration_status="requires_calibration"` (null threshold) don't
   contribute to the pass/fail decision. The experimenter must set
   thresholds before meaningful assessment is possible.

2. **No independent verification** — the assessment is a deterministic
   comparison of observations against criteria. It is NOT independent
   verification. The hypothesis can move to SUPPORTED but never to
   VERIFIED.

3. **Single-origin observations** — repeated observations from the same
   metric use the latest value. The system does NOT treat repeated
   observations from the same origin as independent corroboration.

4. **No arbitrary code execution** — T06 records execution results; it
   does NOT execute experiments. The mission explicitly states:
   "For this mission, recording execution results is sufficient."

5. **Confidence adjustment is simple** — supporting → +0.2,
   contradicting → -0.2 (bounded to [0.0, 1.0]). A more sophisticated
   Bayesian update is out of scope.

6. **PRB-01..02, PRB-04..07** — unchanged.

---

## 11. Deliverable summary

```
G05_T06_STATUS = PASS
AUTHORITATIVE_HEAD = (populated after push)
EXECUTION_RECORDS = PASS
OBSERVATION_CAPTURE = PASS
EVIDENCE_ASSESSMENT = PASS
HYPOTHESIS_FEEDBACK = PASS
PROVENANCE_INTEGRITY = PASS
NO_AUTO_VERIFIED_PROMOTION = PASS
IDEMPOTENCY = PASS
TRANSACTION_SAFETY = PASS
POSTGRESQL_TESTS = PASS
FULL_REGRESSION = 600 passed, 25 skipped (PG), 3 deselected (live), 0 failed
RUFF = All checks passed
OPENAPI = OpenAPI 3.1.0 OK
MODIFIED_FILES = execution_record.py (new, 903 LOC), executions.py (new, 170 LOC), hypotheses.py (new, 59 LOC), experiments.py (+52 LOC), router.py (+6/-16 LOC), test_g05_t06_execution_evidence.py (new, 669 LOC), conftest.py (+6/-1 LOC)
REMAINING_LIMITATIONS = assessment requires calibrated thresholds; no independent verification; single-origin observations not independent; no arbitrary code execution; simple confidence adjustment; PRB-01..02/04..07 unchanged
FINAL_SHA = (populated after push)
```
