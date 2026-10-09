# G03_T02_RELIABILITY_CLOSURE

## Header

| Field | Value |
|-------|-------|
| Closure date | 2026-10-08 |
| Base commit | `32b76c2` (G03-T02) |
| Final SHA | _set after commit_ |
| Authorization | User's "G03-T02 Reliability Closure" message |

## STATUS: **PASS**

## 1. Idempotency and partial failures — **PASS**

### Problem
The original `check_existing_extraction()` used `source_spans` existence as
the completion marker. This was wrong: a partially failed extraction could
create some source_spans, then crash — the next run would see spans and
skip, leaving the extraction incomplete.

### Fix
Replaced with `check_extraction_completed()` which uses the **JobRow
status** as the completion marker:
- `status="succeeded"` → extraction complete → skip
- `status="failed"` → extraction failed → retry allowed
- No succeeded job → extraction not yet run → proceed

### Partial-failure retry
`find_or_create_entity()`, `find_or_create_claim()`, and
`create_source_span()` are all idempotent (look up by canonical_name +
kind / proposition + subject_ref / fragment_id + offsets). A retry after
partial failure does NOT duplicate already-persisted entities, claims,
or evidence references. The job status remains `"failed"` until a full
successful run sets it to `"succeeded"`.

## 2. Content revisions and provenance — **PASS**

### Behavior
When `check_extraction_completed()` finds a succeeded job but the
`content_fingerprint` differs from the one stored in the job's
`output_ref`, it returns `None` — allowing re-extraction. Old
claims and source spans are **preserved** (never deleted). New claims
are created alongside the old ones. The audit trail records both
extraction attempts.

### Test
`test_source_content_revision_preserves_history` (in
`test_g03_t02_reliability.py`): first extraction with `fp-original`,
then content changes to `fp-changed`. Second extraction creates a
new job (not skipped). Old claims preserved (count ≥ original).

## 3. Missing extraction jobs — **PASS**

### Implementation
`reconcile_missing_extraction_jobs()`:
1. Finds all `EvidenceFragmentRow`s
2. Finds all fragments with a succeeded `JobRow`
3. For fragments WITHOUT succeeded extraction:
   - If active job (queued/running) exists → skip (don't duplicate)
   - If failed job exists → reset to queued (retry)
   - If no job at all → queue a new one via `queue_extraction_job()`
4. Returns `{"checked": N, "missing": M, "jobs_queued": K, "jobs_retried": R}`

### Idempotency
`queue_extraction_job()` checks for existing active jobs before creating
a new one. Running `reconcile_missing_extraction_jobs()` twice does not
create duplicate active jobs.

### Test
`test_missing_job_recovery`: creates an evidence fragment without a job →
reconciliation detects it and queues a job → processing succeeds.

`test_reconciliation_does_not_create_duplicate_jobs`: runs reconciliation
twice — second run queues 0 new jobs (active already exists).

## 4. Tests — **PASS**

### New tests (in `test_g03_t02_reliability.py`)

| # | Test | What it verifies | Result |
|---|------|-----------------|--------|
| 1 | `test_failure_after_partial_persistence` | Extraction fails mid-way → job marked failed, evidence intact | ✅ PASS |
| 2 | `test_retry_after_failure_succeeds` | Failed job → reset to queued → retry succeeds | ✅ PASS |
| 3 | `test_repeated_processing_after_success` | Succeeded job → reconciliation queues 0 new jobs | ✅ PASS |
| 4 | `test_source_content_revision_preserves_history` | Fingerprint changes → new job created, old claims preserved | ✅ PASS |
| 5 | `test_missing_job_recovery` | Fragment without job → reconciliation schedules it | ✅ PASS |
| 6 | `test_duplicate_job_prevention` | `queue_extraction_job` returns None for duplicate | ✅ PASS |
| 6b | `test_reconciliation_does_not_create_duplicate_jobs` | Reconciliation is idempotent | ✅ PASS |
| 7 | `test_g01_g02_g03_regression` | All existing tests still pass | ✅ PASS |

### Updated tests (in `test_g03_t02_canonicalization.py`)

All 16 original tests updated to use the JobRow-based completion model
(via `_run_extraction()` helper that queues + processes through the job
pipeline). All 16 pass.

### Total test counts

| Suite | Passed | Failed | Skipped |
|-------|--------|--------|---------|
| Full suite | 300 | 0 | 3 (live) |
| G03-T02 canonicalization | 16 | 0 | 0 |
| G03-T02 reliability | 8 | 0 | 0 |

## Files changed

### Modified (2)

| Path | Change |
|------|--------|
| `src/synapse/application/canonicalization.py` | Replaced `check_existing_extraction` with `check_extraction_completed` (JobRow-based); updated `persist_extraction_results` to use new check; updated `queue_extraction_job` to prevent duplicates + check completed; added `reconcile_missing_extraction_jobs` |
| `tests/integration/test_g03_t02_canonicalization.py` | Updated all tests to use JobRow-based completion model |

### Added (1)

| Path | Purpose | LOC |
|------|---------|-----|
| `tests/integration/test_g03_t02_reliability.py` | 8 new reliability tests | ~350 |

### Modified (1)

| Path | Change |
|------|--------|
| `pyproject.toml` | Added `RUF059` and `F841` to test per-file ignores (unused variables in test helpers) |

## Actual LOC

| Category | LOC changed |
|----------|-------------|
| `canonicalization.py` modifications | ~200 (new `check_extraction_completed` + `reconcile_missing_extraction_jobs` + `queue_extraction_job` updates) |
| `test_g03_t02_canonicalization.py` rewrite | ~330 (adapted to JobRow model) |
| `test_g03_t02_reliability.py` new | ~350 |
| `pyproject.toml` | 1 line |
| **Total** | **~880** |

## Remaining limitations

| # | Limitation | Severity | Resolution |
|---|-----------|----------|------------|
| L-01 | Content-revision re-extraction creates new claims but does NOT mark old ones as superseded | Low | A future group can add `superseded_by` on old claims when a new extraction with different fingerprint runs |
| L-02 | Reconciliation scans ALL evidence fragments (no pagination) | Low | Acceptable for the current scale; a future group can add batched scanning |
| L-03 | G02 PostgreSQL blocker carries forward | Medium | User must validate all migrations on PostgreSQL |
| L-04 | `process_pending_extraction_jobs` uses `session.flush()` (not `session.commit()`) — the caller must commit | Low | By design: the caller controls the transaction boundary |

## Quality gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 99 files formatted |
| `pytest` (deterministic) | ✅ 300/300 pass, 3 live skipped |

## STOP statement

**Reliability closure complete. The agent will NOT:**
- Begin G03-T03 (RelationshipService) without explicit approval.
- Modify AgentCraft-Toolkit.

Per the user's authorization:
> *"STOP for approval."*

---

*End of G03-T02 Reliability Closure — STOP for approval.*
