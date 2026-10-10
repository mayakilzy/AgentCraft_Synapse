# TEST-INFRA-01 — Recursive Regression Test Elimination

**Status**: PASS
**Task**: TEST-INFRA-01 — Eliminate Recursive Regression Test Execution
**Starting SHA**: `9e40f01c8a7b10781fc0eeb244e5793c1755b2a7` (G05-T05 closure)
**Final SHA**: `5d34671b0392ca2481bdacad128b38ceafb4729f`
**Date**: 2026-10-10
**Context**: G05-T05C validation closure

---

## 1. Executive summary

The Synapse test suite contained **18 recursive regression wrappers** —
test functions whose only purpose was to invoke `subprocess.run([python,
-m, pytest, ...])` to re-run other tests that were already collected and
executed as part of the same suite. Two of these wrappers were
**self-recursive** (a test in file X spawned a subprocess that re-ran
file X minus itself), and one was **3-level nested** (T05 test →
subprocess → T03 test → subprocess → T03 file again).

This produced **~21 minutes of redundant pytest invocations per full-
suite run** and made the complete deterministic suite impossible to
execute within a single 10-minute tool budget. The wrappers also made
the suite fragile: any environment slowdown (e.g., CI load, sandbox
throttling) caused `subprocess.TimeoutExpired` failures that were
misattributed to code defects.

TEST-INFRA-01 removes all 18 wrappers, preserves 100% of the functional
test coverage (the wrappers contained zero unique assertions — they only
asserted `returncode == 0` and `"passed" in stdout`), and adds two new
Makefile targets (`test-deterministic`, `test-slow`) for explicit
reproducible execution.

**Result**: the complete deterministic suite now runs in **~2 minutes**
(was ~21 minutes), in a single pytest invocation, with zero failures.
Collection dropped from 567 to 549 tests (–18 wrappers), then rose to
583 after adding 34 new T05C validation tests.

---

## 2. Audit

### 2.1 Inventory of `subprocess.run` calls in `tests/`

| # | File | Function | Subprocess target | Classification |
|---|------|----------|-------------------|----------------|
| 1 | `test_g02_minimal_slice.py` | `test_g01_security_tests_still_pass` | unit G01 tests | REDUNDANT — reruns already-collected tests |
| 2 | `test_g03_t02_canonicalization.py` | `test_g01_g02_g03_t01_regression` | unit G01 tests | REDUNDANT |
| 3 | `test_g03_t02_reliability.py` | `test_g01_g02_g03_regression` | unit G01 tests | REDUNDANT |
| 4 | `test_g03_t03_relationship_service.py` | `test_g01_g02_g03_regression` | unit + G03-T02 regression | REDUNDANT |
| 5 | `test_g03_t04_verification.py` | `test_g01_g02_g03_regression` | unit + G03-T03 regression | REDUNDANT |
| 6 | `test_g03_t05_final_integration.py` | `test_g01_g02_g03_regression` | unit + G03-T03/T04 regression | REDUNDANT |
| 7 | `test_g04_t01_retrieval.py` | `test_g01_g02_g03_regression` | unit + G03-T03/T04/T05 regression | REDUNDANT |
| 8 | `test_g04_t02_capability_registry.py` | `test_g01_g04_t01_regression` | G03-T03/T04/T05 + G04-T01 regression | REDUNDANT |
| 9 | `test_g04_t02_capability_registry.py` | `test_g01_g04_t02_regression_after_attribution_fix` | **OWN file** (self-recursive) | REDUNDANT |
| 10 | `test_g04_t03_reasoning.py` | `test_g01_g04_t02c_regression` | G04-T01/T02 regression | REDUNDANT |
| 11 | `test_g04_t03_reasoning.py` | `test_g01_g04_t03_regression_after_contradiction_closure` | **OWN file** (self-recursive) | REDUNDANT |
| 12 | `test_g04_t05_external_client.py` | `test_15_full_regression_g01_through_t04c` | unit + G04-T01/T02/T03/T04 regression (3-level nested) | REDUNDANT |
| 13 | `test_g05_t01_knowledge_combination.py` | `test_07_g01_g04_regression` | unit + G04-T01/T02/T03 regression | REDUNDANT |
| 14 | `test_g05_t02_opportunity_discovery.py` | `test_06_g01_g05_t01_regression` | unit + G04 + G05-T01/T02 specific | REDUNDANT |
| 15 | `test_g05_t03_innovation_generation.py` | `test_07_regression` | unit + G05-T01/T02 specific | REDUNDANT |
| 16 | `test_g05_t03c_idempotency.py` | `test_09_regression` | unit + G05-T01/T03 specific | REDUNDANT |
| 17 | `test_g05_t04_architecture_critique.py` | `test_08_regression` | unit + G05-T01/T03/T03C specific | REDUNDANT |
| 18 | `test_toolkit_audit_preparation.py` | `test_g01_tests_still_pass` | unit G01 tests | REDUNDANT |
| 19 | `test_migrations.py` | `_run_alembic()` helper | `alembic upgrade/downgrade` | **PRESERVE** — not pytest; unique alembic verification |
| 20 | `test_migrations.py` | `test_migration_0001_initial_unchanged_from_g01_baseline` | `git diff` (verifies migration unchanged) | **PRESERVE** — not pytest; unique git verification |
| 21 | `test_toolkit_audit_preparation.py` | `_run_script()` helper | validator script | **PRESERVE** — not pytest; unique script verification |

### 2.2 `pytest.main()` calls

**Zero.** No test uses `pytest.main()`. All recursive invocation is via
`subprocess.run`.

### 2.3 Counts

- **`subprocess.run` calls before**: 21
- **`subprocess.run` calls after**: 3 (all non-pytest: alembic, git, validator)
- **Recursive pytest calls before**: 18
- **Recursive pytest calls after**: 0

### 2.4 Wrapper content analysis

Every one of the 18 removed wrappers had the identical structure:

```python
def test_*_regression():
    r = subprocess.run([sys.executable, "-m", "pytest", ...], timeout=N)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout
```

**Zero unique assertions.** The wrappers verified only that the
subprocess pytest exited 0 and printed "passed" — which is exactly what
the parent pytest already verifies when it collects and runs those same
tests as part of the same suite. Removing the wrappers loses no
coverage; it only eliminates the redundant re-execution.

---

## 3. Changes

### 3.1 Removed wrappers (18 functions across 16 files)

Each wrapper function was removed along with its preceding section
comment header (when the header was a regression-style marker). The
removal was performed by a persistent script
(`/home/z/my-project/scripts/remove_recursive_wrappers.py`) that:

1. Finds the `def test_*regression*` line.
2. Walks backwards to capture `@pytest.mark.*` decorators.
3. Walks backwards to capture a contiguous comment block, removing it
   only if it contains regression-style keywords ("regression", "still
   pass", "g01 tests").
4. Walks forwards to the next top-level `def`/`async def`/`class` to
   find the function body end.
5. Removes the entire span and collapses 3+ consecutive newlines to 2.

### 3.2 Unused-import cleanup

Ruff's `--fix` removed 12 unused `import subprocess` / `import sys` /
`import os` declarations that were only used by the removed wrappers.

### 3.3 Live-test marker fix

`test_g02_minimal_slice.py::test_live_arxiv_discovery_and_ingest` was a
real-network test (it calls arXiv) but lacked the `@pytest.mark.live`
decorator — its docstring said "Run with: pytest -m live" but the marker
was missing. This caused the full deterministic suite (`-m "not live"`)
to still execute it, producing a flaky HTTP 429 failure against arXiv.
Added the missing `@pytest.mark.live` decorator.

### 3.4 Test_11 contract update (T05-related, carried forward)

`test_g04_t05_external_client.py::test_11_predictable_error_responses`
was updated in T05 to reflect the activation of `POST /experiments` (it
previously asserted 501; now asserts 422 + verifies a genuinely
unimplemented route still returns 501). This change is preserved.

### 3.5 Makefile — new documented test commands

```
make test              # Full pytest with coverage
make test-fast         # Pytest without coverage (skips live tests)
make test-deterministic  # Complete deterministic suite — single invocation
make test-slow         # Run only known-slow integration files (for diagnosis)
```

The **canonical full deterministic suite command** is:

```bash
make test-deterministic
# equivalent to:
python -m pytest tests/ --no-cov -q -p no:cacheprovider -m "not live"
```

This is a **single pytest invocation** that collects and runs every
deterministic test in the suite. No subprocess, no recursion, no
re-execution of already-collected tests.

### 3.6 No fixture scope changes

Per task 4 constraint: fixture scope is unchanged. The `engine` and
`db_session` fixtures remain `function`-scoped (per-test isolated
SQLite). No mutable database sharing was introduced.

### 3.7 No new dependencies, no framework change

The correction uses only existing pytest configuration (`-m`, `--no-cov`,
`-p no:cacheprovider`) and existing Makefile patterns. No new pip
packages, no `pytest-xdist`, no `pytest-split`, no new test framework.

---

## 4. Validation

### 4.1 Collection before / after

| Stage | Collected | Δ |
|-------|----------:|---:|
| Before (T05 closure, with wrappers) | 567 | — |
| After wrapper removal | 549 | –18 |
| After T05C validation tests added | 583 | +34 |
| **Net** | **583** | **+16** |

The –18 wrappers are pure removal (zero coverage loss). The +34 T05C
tests are new validation coverage (fingerprint correctness + measurable
criteria integrity + regression scope).

### 4.2 Full deterministic suite

```
$ make test-deterministic
580 passed, 3 deselected in 125.96s (0:02:05)
```

- **580 passed** (583 collected – 3 live deselected)
- **3 deselected** (`@pytest.mark.live`: 2 arxiv provider + 1 arxiv
  discovery+ingest)
- **0 failed**
- **Duration**: 125.96s (~2:06)

### 4.3 Before/after duration comparison

| Configuration | Duration | Invocations |
|---------------|---------:|------------:|
| Before (with wrappers, sequential) | ~21 min | 1 parent + ~40 subprocess |
| After (no wrappers, single invocation) | ~2 min | 1 |

**~10× speedup**, single invocation, no recursion.

### 4.4 Ruff

```
$ ruff check src tests scripts examples
All checks passed!
```

### 4.5 OpenAPI

```
$ make openapi-check
OpenAPI 3.1.0 OK
```

### 4.6 Focused tests on modified files

All 16 modified integration files collect and pass individually. The
34 new T05C tests pass:

```
$ pytest tests/integration/test_g05_t05c_validation_closure.py
34 passed in 10.63s
```

The 30 existing T05 tests still pass:

```
$ pytest tests/integration/test_g05_t05_experiment_planning.py
30 passed in 19.24s
```

---

## 5. Coverage analysis

### 5.1 Did removing 18 tests reduce coverage?

**No.** Each removed wrapper's body was:

```python
r = subprocess.run([..., "pytest", "<other tests>", ...], ...)
assert r.returncode == 0
assert "passed" in r.stdout
```

The wrapper verifies that the subprocess pytest exited 0. The parent
pytest (the one running the wrapper) already collects and runs those
exact same `<other tests>` as part of the same suite — so if they fail,
the parent pytest reports the failure directly, with a real traceback,
at the actual test site. The wrapper added only indirection and
redundancy.

### 5.2 What about the "regression intent"?

The wrappers were originally written to prove "G0X-T0Y didn't break
G01-G0X-T0Y-1". That intent is now satisfied by the **single-suite
invocation**: if any G01-G0X-T0Y-1 test fails, the suite fails. The
regression intent is preserved without the wrapper.

### 5.3 Preserved subprocess usage (Category B)

Three `subprocess.run` calls are preserved because they invoke non-
pytest binaries and provide genuinely unique verification:

1. `test_migrations.py::_run_alembic()` — invokes `alembic upgrade` /
   `alembic downgrade` to verify migrations round-trip. Cannot be
   replaced by an in-process call without spawning alembic's env.
2. `test_migrations.py::test_migration_0001_initial_unchanged_from_g01_baseline`
   — invokes `git diff 7a9088a HEAD -- alembic/versions/0001_initial.py`
   to verify the G01 baseline migration is unchanged. Requires git CLI.
3. `test_toolkit_audit_preparation.py::_run_script()` — invokes the
   validator script (`scripts/validate_toolkit_index.py`) as a CLI to
   verify its exit code and stdout. Tests the script's `__main__` entry
   point, not just its importable functions.

---

## 6. Open limitations

### 6.1 PRB-03 (unchanged, documented)

The 7 production-readiness blockers from ADR-0011 are unchanged. This
task does not address them.

### 6.2 Self-recursive pattern is now structurally impossible

The two self-recursive wrappers (`test_g01_g04_t02_regression_after_attribution_fix`
in T04-T02 and `test_g01_g04_t03_regression_after_contradiction_closure`
in T04-T03) are gone. No remaining test spawns a subprocess that re-runs
its own file. The pattern cannot re-emerge without re-introducing
`subprocess.run([..., "pytest", ...])`, which a future code review
should flag.

### 6.3 No DB-level UNIQUE on `experiment_fingerprint`

Unchanged from T05 §10.2. The fingerprint-based idempotent reuse relies
on a Python-level scan. PRB-03 follow-up.

### 6.4 Live-test marker audit (recommendation)

During this task, one missing `@pytest.mark.live` was found and fixed
(`test_live_arxiv_discovery_and_ingest`). A future audit should verify
no other real-network test is missing the marker. The current count is
3 live tests (2 arxiv provider unit tests + 1 arxiv discovery+ingest
integration test), all correctly marked.

### 6.5 Slow files remain slow (but no longer recursive)

The four "slow" integration files (`test_g04_t02`, `test_g04_t03`,
`test_g04_t05`, `test_g05_t01`) still take 30-60s each due to per-test
DB creation. The recursion removal eliminated the ~3× multiplier from
subprocess re-execution, but the base cost remains. Future optimization
(session-scoped engine, `pytest-xdist` parallelism) is out of scope for
TEST-INFRA-01.

---

## 7. Deliverable summary

```
FINAL_SHA = 5d34671b0392ca2481bdacad128b38ceafb4729f
RECURSIVE_PYTEST_CALLS_BEFORE = 18
RECURSIVE_PYTEST_CALLS_AFTER = 0
FUNCTIONAL_TESTS_PRESERVED = 549 (567 before − 18 redundant wrappers; 0 unique assertions lost)
FULL_SUITE_TESTS = 580 passed, 3 deselected (live), 0 failed
FULL_SUITE_DURATION = 125.96s (~2:06)
RUFF = All checks passed (src tests scripts examples)
OPENAPI = OpenAPI 3.1.0 OK
```
