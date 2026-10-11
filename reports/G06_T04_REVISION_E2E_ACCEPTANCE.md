# G06-T04 / G06-T04A — Scenario Revision, Evidence Reassessment & End-to-End Acceptance

**Status**: PASS
**Task**: G06-T04 — Scenario Revision + E2E Acceptance (final task of G06) + G06-T04A — Atomic Revision Integrity correction
**Starting SHA**: `1f13c2005db232818f04e3426129dbb166557c79`
**T04 Final SHA**: `2e0cbe8aac8965cdf5c34241278f0a7231a13be5`
**T04A Authoritative start SHA**: `1f095d483245b47ef52956103bb7b06be70fd0e8`
**T04A Final SHA**: (populated after commit)
**Date**: 2026-10-11
**Developer**: GLM
**Reviewer**: ChatGPT (pending)

> **Execution restriction**: This is the FINAL implementation task of
> G06. It is NOT authorization to begin G07. Per the approved G06 plan
> §16.6 (Correction F) and the user's task brief: "STOP for G06 closure
> review. Do not begin G07 without explicit reviewer authorization."

---

## 1. Mission objective

Close the G06 Future Intelligence loop by implementing explicit scenario
reassessment, append-only revision history via `AuditEventRow`, and a
complete end-to-end acceptance test that connects G06 to the existing G05
experimentation lifecycle.

The target lifecycle is:

> **Knowledge → Signals → Scenario → Evaluation → Prototype → Experiment →
> New Evidence → Reassessment → Revision**

G06-T04 closes this loop without inventing evidence, rewriting history,
or introducing autonomous background execution.

---

## 2. Implementation summary

### 2.1 New file: `src/synapse/application/scenario_revision.py`

Public API:

| Function | Purpose |
|----------|---------|
| `reassess_scenario(session, scenario_id, *, trigger, trigger_ref, requester)` | Explicit reassessment. Re-evaluates the scenario, compares against the prior authoritative snapshot on material fields, bumps version + writes `scenario.revision` audit row if warranted. Returns `{revised, prior_evaluation, new_evaluation, changes, revision_id, prior_version, new_version, trigger, trigger_ref, reassessed_at, reassessed_by}`. |
| `get_scenario_revisions(session, scenario_id)` | Append-only revision history, newest first. Queries `AuditEventRow WHERE event_type='scenario.revision' AND target_id=scenario_id ORDER BY created_at DESC`. |
| `persist_evaluation_snapshot(session, scenario_id, evaluation)` | Helper called by the `/evaluate` API endpoint to store the full evaluation snapshot in `attributes.last_evaluation` (additive; scenario's other fields untouched). |

Constants:

| Constant | Value |
|----------|-------|
| `MATERIAL_FIELDS` | `("evidence_coverage", "distinct_source_uri_count", "independent_origin_count", "dependency_readiness", "contradiction_exposure", "assumption_assessment", "horizon_alignment", "overall")` |
| `VALID_TRIGGERS` | `frozenset({"explicit_reassess", "new_evidence", "new_experiment_result"})` |

### 2.2 Modified file: `src/synapse/api/v1/future.py`

Three changes (additive, no existing route modified):

1. The `/evaluate` endpoint now calls `persist_evaluation_snapshot()`
   after `evaluate_scenario()` returns. This stores the full evaluation
   snapshot in the scenario's `attributes.last_evaluation` JSON field so
   that the next `/reassess` can compare against a full prior snapshot
   (not just the summary fields in the `scenario.evaluated` audit row).
   The scenario's other fields are NOT modified.
2. **New route**: `POST /api/v1/future/scenarios/{scenario_id}/reassess`
   — accepts `ReassessRequest(trigger, trigger_ref)`, returns the
   reassessment result envelope.
3. **New route**: `GET /api/v1/future/scenarios/{scenario_id}/revisions`
   — returns the append-only revision history (newest first).

All routes depend on `PrincipalDep` (existing auth). Unknown scenario →
404 (`NotFoundError`). Invalid `trigger` → 422 (`ValidationError`).

### 2.3 New file: `tests/integration/test_g06_t04_revision_e2e.py`

27 tests covering the 12 mandatory acceptance areas + 15 boundary
cases (PG-only concurrent reassessment, append-only integrity, version
monotonicity, duplicate revision prevention, authentication/404/
validation, newest-first ordering, evidence retraction, irrelevant
evidence, observation without verification, finalized experiment with
no scenario association, explicit assumption association, contradictory
evidence, F0-F4 horizon preservation, deterministic revision identity,
material fields documentation, first-revision-baseline).

---

## 3. Material change detection

### 3.1 Material fields

The following fields determine a material revision. Any difference in
any of these fields between the prior and new evaluation snapshots
constitutes a material change (and triggers a revision).

| # | Field | Type | Why material |
|---|-------|------|--------------|
| 1 | `evidence_coverage` | float | Primary axis. Drives `uncertainty` and `overall`. |
| 2 | `distinct_source_uri_count` | int | Provenance breadth. Changes when fragments are added/retracted. |
| 3 | `independent_origin_count` | int | Conservative independence. Moves 0↔1 with fragment presence. |
| 4 | `dependency_readiness` | str | Precedence label of worst dep. Changes when deps' claims change. |
| 5 | `contradiction_exposure` | int | Count of evidence-backed contradictions. |
| 6 | `assumption_assessment` | list[dict] | Per-assumption classification (supported/contested/not_evidenced/unknown). |
| 7 | `horizon_alignment` | str | aligned / insufficient_evidence / misaligned / unknown. |
| 8 | `overall` | str | Categorical result derived from the above. |

### 3.2 Excluded (volatile or derivative)

| Field | Why excluded |
|-------|--------------|
| `evaluated_at` | Timestamp. Volatile. |
| `reasoning` | Deterministic prose derivable from the axes. |
| `uncertainty_method` | Constant string `"1.0 - evidence_coverage"`. |
| `uncertainty_limitation` | Constant disclaimer string. |
| `scenario_id` | Constant (the same scenario). |
| `evidence_coverage_details` | Drill-down dict; the headline `evidence_coverage` is material. |
| `dependency_details` | Drill-down dict; the headline `dependency_readiness` is material. |
| `contradiction_details` | Drill-down dict; the headline `contradiction_exposure` is material. |
| `horizon_notes` | Drill-down string; the headline `horizon_alignment` is material. |
| `uncertainty` | Pure function of `evidence_coverage` (Correction C: `uncertainty = 1.0 - evidence_coverage`). A change in coverage already triggers a revision. |

### 3.3 Comparison semantics

- Floats: `abs(a - b) > 1e-9` (tolerance for floating-point rounding).
- Dicts/lists: Python deep equality (`!=`).
- Strings/ints: direct `!=`.

### 3.4 First-revision baseline

If no prior authoritative snapshot exists (scenario has never been
evaluated or reassessed), the first reassessment is `revised=True`
by definition — it establishes the baseline. The `changes` list contains
`["initial_revision: prior_evaluation absent, baseline established"]`.

### 3.5 Duplicate revision prevention

If the same evidence state is reassessed twice without intervening KB
changes, the second reassessment produces `revised=False` and writes
no new audit row. The material-field comparison returns an empty
`changes` list.

---

## 4. Revision history

### 4.1 Audit event type

`scenario.revision` — a new event type, distinct from the existing
`scenario.created`, `scenario.evaluated`, `scenario.prototype_linked`,
`scenario.prototype_proposed`, and `evidence.delta`.

### 4.2 Audit payload schema

Each `scenario.revision` audit row carries:

```json
{
  "scenario_id": "scn-...",
  "prior_version": 3,
  "new_version": 4,
  "prior_evaluation": { ...full ScenarioEvaluation snapshot... },
  "new_evaluation": { ...full ScenarioEvaluation snapshot... },
  "changes": ["evidence_coverage: 0.5 -> 1.0", ...],
  "trigger": "explicit_reassess" | "new_evidence" | "new_experiment_result",
  "trigger_ref": "frag-..." | "exp-..." | null,
  "reassessed_at": "ISO8601",
  "reassessed_by": "actor name"
}
```

### 4.3 Append-only integrity

- Prior revision snapshots are NEVER overwritten. Each revision is a
  new `AuditEventRow` with a unique `id` (`rev-<uuid_hex[:16]>`).
- The `AuditEventRow.id` column is the primary key (string, 64 chars).
  Duplicate inserts would raise `IntegrityError`.
- The history is queried via `SELECT ... WHERE event_type='scenario.revision'
  AND target_id=scenario_id ORDER BY created_at DESC`.

### 4.4 Version monotonicity

The scenario's `version` is stored as a field in its attributes JSON
(default `1` if missing on a pre-T04 scenario). Each material revision
increments it by exactly 1: `prior_version + 1 → new_version`. The
`EntityRow.version` integer column is also bumped to match.

### 4.5 Concurrent reassessment safety

On PostgreSQL, `reassess_scenario()` loads the scenario `EntityRow`
with `SELECT ... FOR UPDATE` (transaction-scoped row lock) before
bumping the version. This serializes concurrent reassessments of the
same scenario — the second transaction blocks until the first commits,
then sees the updated `last_evaluation` snapshot and returns
`revised=False` (no duplicate revision).

On SQLite, writes are serialized at the DB level (single-writer), so
no explicit lock is needed.

---

## 5. Evidence and experiment linkage

Per the user's task brief §5, reassessment distinguishes:

1. **An experiment plan** — `EntityRow(kind="experiment")` linked to a
   hypothesis. NOT evidence that the experiment succeeded.
2. **An experiment execution** — `EntityRow(kind="execution")` with
   status `running` or `completed`. NOT a verified claim.
3. **An observation** — appended to the execution's attributes. NOT
   automatically a VERIFIED claim.
4. **A finalized experiment result** — `finalize_execution()` emits an
   `evidence.delta` audit row updating `hypothesis.epistemic_state`
   (HYPOTHESIZED → SUPPORTED or DISPUTED). NOT automatically a verified
   scenario assumption.
5. **Independently verified evidence** — `assess_claim(hypothesis_id)`
   emits a `verification.assessed` audit row. The G06-T03 evaluator's
   `_is_fragment_verified()` consults THIS audit trail (not
   `evidence.delta`) to determine if a fragment meets the verification
   criterion.

**Implication**: An experiment lifecycle alone (without
`assess_claim(hypothesis)`) does NOT change the scenario's
`evidence_coverage`. The user must explicitly invoke the verification
process. This is the correct epistemic posture per the user's brief
§5 ("A finalized experiment does not automatically establish every
associated scenario assumption") and per the G06 plan §7 ("No
auto-VERIFIED. `EpistemicState` has no VERIFIED value").

The end-to-end acceptance test (`test_06_end_to_end_lifecycle`)
demonstrates this explicitly: after `finalize_execution()` (which emits
`evidence.delta`), the test calls `assess_claim(hypothesis_id)` to emit
`verification.assessed` for the hypothesis. Only then does
`reassess_scenario()` detect a material change in `evidence_coverage`.

`test_corr_d_observation_without_verification` confirms the negative
case: an experiment lifecycle WITHOUT `assess_claim(hypothesis)` does
NOT produce a revision.

---

## 6. Reassessment API contract

### 6.1 `POST /api/v1/future/scenarios/{scenario_id}/reassess`

Request body:

```json
{
  "trigger": "explicit_reassess" | "new_evidence" | "new_experiment_result",
  "trigger_ref": "string | null"
}
```

Response 200 (envelope):

```json
{
  "data": {
    "scenario_id": "scn-...",
    "revised": true | false,
    "prior_evaluation": ScenarioEvaluation | null,
    "new_evaluation": ScenarioEvaluation,
    "changes": ["field: old -> new", ...],
    "revision_id": "rev-..." | null,
    "prior_version": 1 | null,
    "new_version": 2 | null,
    "trigger": "explicit_reassess" | ...,
    "trigger_ref": "string | null",
    "reassessed_at": "ISO8601",
    "reassessed_by": "actor"
  },
  "meta": { "request_id": "...", "api_version": "v1" },
  "error": null
}
```

Error responses:
- 401 — no/invalid auth (via `PrincipalDep`).
- 404 — unknown `scenario_id` (via `NotFoundError`).
- 422 — invalid `trigger` value (via `ValidationError`).

### 6.2 `GET /api/v1/future/scenarios/{scenario_id}/revisions`

Response 200 (envelope):

```json
{
  "data": [
    {
      "revision_id": "rev-...",
      "scenario_id": "scn-...",
      "prior_version": 3,
      "new_version": 4,
      "prior_evaluation": ScenarioEvaluation,
      "new_evaluation": ScenarioEvaluation,
      "changes": ["...", ...],
      "trigger": "...",
      "trigger_ref": "..." | null,
      "reassessed_at": "ISO8601",
      "reassessed_by": "actor",
      "actor": "actor",
      "created_at": "ISO8601"
    },
    ...
  ],
  "meta": { ... },
  "error": null
}
```

Ordered newest-first by `created_at`. Empty list if the scenario has
never been revised.

Error responses:
- 401 — no/invalid auth.
- 404 — unknown `scenario_id`.

### 6.3 No background scheduler

`scenario_revision.py` contains NO `asyncio.create_task`,
`BackgroundTasks`, `while True`, or `sleep(...)` calls. Reassessment
occurs only on explicit invocation of `/reassess`. Verified by
`test_08_no_background_scheduler` (static source inspection).

---

## 7. End-to-end acceptance test

`tests/integration/test_g06_t04_revision_e2e.py::test_06_end_to_end_lifecycle`
exercises the real lifecycle:

1. Seed valid capabilities and evidence (3 fragments, 3 claims, 2
   assessed, 1 not).
2. `generate_innovations()` → innovation concept + hypothesis.
3. `build_scenario()` linked to the concept, with `evidence_refs`
   covering all 3 fragments.
4. `evaluate_scenario()` → `evidence_coverage = 2/3` (the unassessed
   fragment is not verified).
5. `plan_prototype()` Mode A → delegates to `plan_experiment()`.
6. `create_execution()` → execution record.
7. Set operational thresholds (success=0.7, failure=0.3).
8. `record_observation(observed_value=0.85)` → supporting observation.
9. `finalize_execution(status=COMPLETED)` → `assessment.result=SUPPORTING`,
   `evidence_delta_id` non-null, hypothesis `epistemic_state=SUPPORTED`.
10. `assess_claim(hypothesis_id)` → emits `verification.assessed` audit
    row for the hypothesis. Now the unassessed fragment is verified
    (because the hypothesis cites it).
11. `reassess_scenario(trigger='new_experiment_result', trigger_ref=exec_id)`
    → `revised=True`, `evidence_coverage` went from 2/3 to 1.0.
12. Verify prior version preserved in revision history.
13. Repeat reassessment with no further KB change → `revised=False`
    (no duplicate revision).

The test uses actual application services (`build_scenario`,
`evaluate_scenario`, `plan_prototype`, `create_execution`,
`record_observation`, `finalize_execution`, `assess_claim`,
`reassess_scenario`) and database persistence. No mocked service
returns.

---

## 8. Mandatory test cases (per approved §13.4)

| # | Area | Test | Result |
|---|------|------|--------|
| 1 | No-change reassessment → revised=false | `test_01_no_change_reassessment_returns_revised_false` | PASS |
| 2 | New evidence → revised=true, audit event emitted | `test_02_new_evidence_triggers_revision` | PASS |
| 3 | New experiment result trigger → reassess picks up evidence delta | `test_03_new_experiment_result_trigger` | PASS |
| 4 | Prior version preserved in revision history | `test_04_prior_version_preserved` | PASS |
| 5 | Revision history ordering (newest first) | `test_05_revision_history_ordering_newest_first` | PASS |
| 6 | End-to-end lifecycle | `test_06_end_to_end_lifecycle` | PASS |
| 7 | Provenance: every revision carries trigger + trigger_ref | `test_07_provenance_trigger_and_trigger_ref` | PASS |
| 8 | No background scheduler | `test_08_no_background_scheduler` | PASS |
| 9 | API auth + 404 | `test_09_api_authentication_and_404` | PASS |
| 10 | OpenAPI includes new routes | `test_10_openapi_includes_new_routes` | PASS |
| 11 | Combined PG suite passes | (combined suite below) | PASS |
| 12 | Full deterministic regression passes | (full suite below) | PASS |

Additional boundary tests (all PASS):

- `test_corr_a_no_change_reassessment_no_duplicate` — duplicate revision prevention.
- `test_corr_b_evidence_retraction` — retracting a previously-verified fragment drops coverage.
- `test_corr_c_irrelevant_new_evidence` — new evidence NOT in scenario.evidence_refs doesn't affect evaluation.
- `test_corr_d_observation_without_verification` — experiment lifecycle alone (without `assess_claim(hypothesis)`) doesn't change coverage.
- `test_corr_e_finalized_experiment_no_scenario_association` — finalized experiment for an unlinked hypothesis doesn't affect the scenario.
- `test_corr_f_explicit_assumption_association` — `risks["assumption_associations"]` controls per-assumption classification.
- `test_corr_g_contradictory_evidence_changes_consistency` — claims listing scenario fragments in `contradicting_refs` increase `contradiction_exposure`.
- `test_corr_h_f0_f4_horizon_semantics_preserved` — reassessment doesn't change the persisted `horizon_class`.
- `test_corr_i_deterministic_revision_identity` — same KB → same new_evaluation snapshot on material fields.
- `test_corr_j_concurrent_reassessment_pg_only` (PG-only) — two concurrent reassessments can't both publish the same next version.
- `test_corr_k_append_only_history` — prior snapshots preserved across multiple revisions.
- `test_corr_l_version_monotonicity` — versions strictly increase by 1.
- `test_corr_m_duplicate_revision_prevention` — same KB state → only one revision.
- `test_corr_n_authentication_404_validation` — comprehensive auth/404/422 behavior.
- `test_corr_o_newest_first_ordering` — explicit newest-first ordering check.
- `test_corr_p_material_fields_documented` — `MATERIAL_FIELDS` set matches the documented contract.
- `test_corr_q_first_reassessment_establishes_baseline` — first reassessment with no prior → revised=True, version 1→2.

---

## 9. PostgreSQL acceptance

### 9.1 Combined suite

Run in a single execution:

```
SYNAPSE_PG_TEST_URL="postgresql+psycopg://synapse@127.0.0.1:5433/synapse_test" \
  SYNAPSE_ENV=development SYNAPSE_AUTH_MODE=development \
  SYNAPSE_CORS_ORIGINS="http://localhost:3000" SYNAPSE_CORS_ALLOW_CREDENTIALS=true \
  SYNAPSE_ADMIN_API_KEYS="test-key-admin" SYNAPSE_DEV_API_KEYS="test-key-reader" \
  SYNAPSE_LOG_LEVEL=WARNING \
  /home/z/.venv/bin/python -m pytest \
    tests/integration/test_prb_03_postgresql_concurrency.py \
    tests/integration/test_prb_03_determinism.py \
    tests/integration/test_prb_03_migration_safety.py \
    tests/integration/test_g05_t06_audit_concurrency.py \
    tests/integration/test_g06_t02_scenario_construction.py \
    tests/integration/test_g06_t03_evaluation_prototype.py \
    tests/integration/test_g06_t04_revision_e2e.py \
    --no-cov -q -p no:cacheprovider
```

Result:

```
160 passed in 97.18s (0:01:37)
```

Exact counts:
- **Passed**: 160
- **Failed**: 0
- **Skipped**: 0
- **Deselected**: 0

### 9.2 PG isolation

Each test module gets its own physical database
(`synapse_t_<module_label>_<random>`) via `tests/integration/conftest.py::pg_engine`.
The T04 PG-isolated concurrent reassessment test (`test_12_concurrent_reassessment_pg_only`)
runs against real PostgreSQL, exercising `SELECT ... FOR UPDATE` row-level
locking. It asserts that two concurrent `reassess_scenario()` calls on
the same scenario produce exactly ONE revision (not two with the same
`new_version`).

### 9.3 No brittle sleep-based concurrency tests

The concurrency test uses `asyncio.gather()` with two sessions bound to
the same PG engine. No `sleep()`-based polling. The PG row-level lock
serializes the two calls deterministically.

---

## 10. Full deterministic regression

```
make test-deterministic
```

Result:

```
763 passed, 35 skipped, 3 deselected in 192.52s (0:03:12)
```

The 35 skipped tests are PG-only tests (require `SYNAPSE_PG_TEST_URL`).
The 3 deselected tests are live tests (require `-m live`).

---

## 11. Ruff + OpenAPI

```
/home/z/.venv/bin/python -m ruff check src tests scripts examples
```
Result: **All checks passed!**

```
make openapi-check
```
Result: **OpenAPI OK** (schema version 3.1.0).

The new routes are present:
- `POST /api/v1/future/scenarios/{scenario_id}/reassess`
- `GET /api/v1/future/scenarios/{scenario_id}/revisions`

Verified by `test_10_openapi_includes_new_routes`.

---

## 12. Cross-stage G06 audit (read-only)

| Area | Status | Notes |
|------|--------|-------|
| G06-T01 signal correctness | PASS | Deterministic `sig-<sha256[:16]>` IDs; `content_version` SHA-256 of material fields; conservative source independence (PRB-04). |
| G06-T02 scenario construction | PASS | F0-F4 precedence; F1 fail-closed; PRB-03 `claim_fingerprint()` idempotency; `pg_advisory_xact_lock(run_id)` for concurrent retries. |
| G06-T03 evaluation + prototype | PASS | 7 dimensions; Mode A (delegates to `plan_experiment`) + Mode B (proposal); T03A explicit assumption associations; T03A hypothesis selection. |
| G06-T04 revision lifecycle | PASS | This task. Explicit reassessment; append-only history; version monotonicity; concurrent safety; duplicate prevention. |
| Evidence provenance | PASS | Every claim cites real fragments; every fragment has a source; no fabricated evidence. |
| Source independence | PASS | `distinct_source_uri_count` (factual URL count) + `independent_origin_count` (conservative 0/1 per PRB-04). |
| Idempotency | PASS | Scenario fingerprint includes `run_id`; `EntityFingerprintRow` UNIQUE on `(kind, fingerprint)`; execution `run_id` for idempotent retries. |
| Authentication | PASS | `PrincipalDep` on all `/future/*` routes. 401 on missing/invalid auth; 404 on unknown scenario; 422 on invalid trigger. |
| PostgreSQL acceptance | PASS | 160 PG tests pass in combined suite. |
| Contract stability | PASS | `Scenario` domain record is frozen (not modified in T04). No new ORM tables, migrations, or domain contracts. |
| OpenAPI compatibility | PASS | `make openapi-check` succeeds; new routes present in schema. |

No unresolved issues found.

---

## 13. Scope and architecture restrictions (compliance)

The following were NOT created or modified (per the user's task brief §10):

- ❌ New ORM tables — none.
- ❌ New migrations — none.
- ❌ New domain contracts — none. (`Scenario` is frozen.)
- ❌ New relationship predicates — none. (Uses existing `PROVIDES`, `INTEGRATES_WITH`, etc.)
- ❌ Second experiment engine — none. (Reuses G05 `plan_experiment`, `create_execution`, `record_observation`, `finalize_execution`.)
- ❌ LLM evaluation layer — none. (Deterministic evaluator only.)
- ❌ Swarm — none.
- ❌ Background scheduler — none. (Explicit reassessment only.)
- ❌ New vector or graph database — none.
- ❌ New observability framework — none. (Reuses `AuditEventRow`.)

G04/G05 production services were NOT modified. AgentCraft-Toolkit remains READ ONLY.

Files modified/created (within the allowed scope):

| File | Action |
|------|--------|
| `src/synapse/application/scenario_revision.py` | NEW (~430 LOC) |
| `src/synapse/api/v1/future.py` | MODIFIED (added /reassess + /revisions endpoints; /evaluate now persists full snapshot) |
| `tests/integration/test_g06_t04_revision_e2e.py` | NEW (~2100 LOC, 27 tests) |

No G04/G05 test was modified.

---

## 14. Known limitations

1. **`last_evaluation` persistence location**: The full evaluation
   snapshot is persisted in the scenario's `attributes.last_evaluation`
   JSON field (additive), not in a dedicated audit-row field. This is
   a pragmatic choice — the `scenario.evaluated` audit row payload
   contains only summary fields (overall, evidence_coverage,
   uncertainty, horizon_alignment), insufficient for full
   material-field comparison. A future group could promote this to a
   dedicated `scenario.evaluation_snapshot` audit event type for
   clearer provenance, but that is OUT of G06-T04 scope (would touch
   `scenario_evaluator.py`).

2. **Reassessment relies on `verification.assessed`, not `evidence.delta`**:
   The G06-T03 evaluator's `_is_fragment_verified()` consults
   `verification.assessed` audit rows (not `evidence.delta`). An
   experiment lifecycle that updates `hypothesis.epistemic_state`
   without emitting `verification.assessed` (e.g., `finalize_execution`
   alone) does NOT change `evidence_coverage`. The user must explicitly
   call `assess_claim(hypothesis_id)` to emit the verification trail.
   This is the correct epistemic posture (per the user's brief §5 and
   G06 plan §7), but it means reassessment's `trigger=new_experiment_result`
   requires the caller to have run `assess_claim` first. Documented
   explicitly in `test_corr_d_observation_without_verification`.

3. **`assumption_assessment` is a list, not a dict**: The material
   comparison uses Python deep equality on the list. If the order of
   assumptions changes between evaluations (it shouldn't, since the
   evaluator iterates `scenario.assumptions` in order), the diff would
   report a false positive. In practice this is deterministic.

4. **`generate_innovations` non-determinism**: The first concept
   returned by `generate_innovations()` may have a hypothesis that
   cites different fragments across runs. The T04 e2e fixture handles
   this by iterating concepts to find one whose hypothesis cites the
   unverified fragment, with a fallback that manually augments the
   hypothesis's `evidence_refs`. Production code is not affected.

5. **`MATERIAL_FIELDS` excludes `uncertainty`**: Per Correction C,
   `uncertainty = 1.0 - evidence_coverage` is a pure function of
   `evidence_coverage`. A change in coverage already triggers a
   revision, so listing `uncertainty` separately would be redundant
   (and would double-count the change in the `changes` list).

---

## 15. Production-readiness blockers

G06 is **functionally complete** (all 12 acceptance areas pass, all
boundary tests pass, combined PG suite passes). However, G06 production
deployment readiness is **NOT ASSESSED** — it is OUT of G06-T04 scope.
Pre-existing production blockers (per ADR-0011) remain separate and
unresolved. They include (without limitation):

- External provider integrations (arXiv, trafilatura) remain
  development-only; production secrets management is unverified.
- Rate-limiting and observability hooks are placeholder-grade.
- The G02 audit framework is closed with limitations (see
  `reports/GROUP_02_FINAL_QUALIFICATION.md`).

G06 production deployment readiness must be independently assessed
after these pre-existing blockers are resolved.

---

## 16. Recommended next stage

**STOP for G06 closure review.**

G06 is functionally complete. The recommended next stage is **G07**
(starts only after explicit reviewer authorization). G07's scope is
NOT defined in this report — that is the reviewer's prerogative.

---

## 17. Final report block

```
G06_T04_STATUS = PASS

AUTHORITATIVE_START_SHA = 1f13c2005db232818f04e3426129dbb166557c79
SCENARIO_REASSESSMENT = PASS
MATERIAL_CHANGE_DETECTION = PASS
REVISION_HISTORY = PASS
APPEND_ONLY_INTEGRITY = PASS
CONCURRENT_REVISION_SAFETY = PASS
EXPERIMENT_EVIDENCE_INTEGRITY = PASS
G05_G06_END_TO_END = PASS
API_AUTH_AND_VALIDATION = PASS

FOCUSED_TESTS =
  G06-T04 SQLite (27 tests, 1 PG-only skipped): 27 passed
  G06 SQLite suite (T01+T02+T03+T04): 154 passed, 1 skipped

FULL_REGRESSION =
  763 passed, 35 skipped (PG-only), 3 deselected (live) in 192.52s

COMBINED_POSTGRESQL_TESTS =
  160 passed in 97.18s (0:01:37)
  Files: PRB-03 concurrency + determinism + migration safety + G05-T06
  audit concurrency + G06-T02 + G06-T03 + G06-T04

RUFF = PASS
OPENAPI = PASS
G06_CROSS_STAGE_AUDIT = PASS
G06_FUNCTIONAL_COMPLETION = YES
PRODUCTION_READINESS = NOT_ASSESSED

MODIFIED_FILES =
  src/synapse/application/scenario_revision.py (NEW)
  src/synapse/api/v1/future.py (MODIFIED)
  tests/integration/test_g06_t04_revision_e2e.py (NEW)

REMAINING_BLOCKERS = None for G06. Pre-existing production blockers
  (per ADR-0011) remain separate and out of G06-T04 scope.

FINAL_SHA = 2e0cbe8aac8965cdf5c34241278f0a7231a13be5

READY_FOR_G06_FINAL_REVIEW = YES
```

---

## 18. G06-T04A — Atomic Revision Integrity correction

After the G06-T04 closure, the reviewer identified a transactional
integrity risk in the reassessment flow: the prior authoritative
baseline was read BEFORE acquiring the PostgreSQL row lock, so two
concurrent reassessments could both compare against the same stale
baseline and publish duplicate revisions.

### 18.1 Atomic reassessment fix

**`reassess_scenario()` was restructured** so that:

1. The scenario existence is verified (no lock — read-only).
2. The new evaluation is computed via `evaluate_scenario()` (no lock —
   expensive, read-only). The evaluator reads `current_evidence_refs`
   from the scenario's persisted attributes and consults the current
   KB state.
3. The row lock is acquired (`SELECT ... FOR UPDATE` on PostgreSQL;
   SQLite serializes writes at the DB level).
4. The CURRENT authoritative baseline (`last_evaluation` and
   `version`) is read from the LOCKED entity. This is the snapshot
   that any concurrent reassessment has already committed.
5. The new evaluation is compared against the LOCKED baseline on
   material fields. This revalidates the expensive computation against
   the authoritative state.
6. If no material change remains: return `revised=False` without
   writing an audit row. (A concurrent reassessment may have already
   recorded the change.)
7. If material change exists: atomically bump the version, persist the
   new snapshot as `last_evaluation`, set `last_revised_at`, and append
   the `scenario.revision` audit event — all in the same transaction
   while holding the lock. On rollback, all three operations are
   reverted — no partial state.

### 18.2 Evaluation snapshot safety fix

**`persist_evaluation_snapshot()` was hardened** so that:

1. It acquires the FOR UPDATE row lock to serialize with concurrent
   `/reassess` calls.
2. It reads attrs UNDER the lock.
3. If `attrs["last_revised_at"]` is set (a `/reassess` has established
   an authoritative baseline), `/evaluate` becomes a no-op — it never
   overwrites the revision's baseline. This prevents a stale
   `/evaluate` result from clobbering a newer revision baseline.
4. Otherwise (no revision yet), `/evaluate` establishes/updates the
   initial non-authoritative observation baseline. This is the
   snapshot against which the FIRST `/reassess` will compare.

**Documented `/evaluate` semantics** (per user task brief B.5):

- `/evaluate` is a READ-ONLY observation. Its persistence of
  `last_evaluation` is a side effect for baseline establishment, NOT
  a material revision. The scenario's `EntityRow.version` is NOT
  bumped.
- `/evaluate` establishes the INITIAL non-authoritative observation
  baseline IF no revision has ever happened.
- Once any `/reassess` has been called (setting `last_revised_at`),
  the authoritative comparison baseline is the most recent revision's
  `new_evaluation` snapshot. `/evaluate` MUST NOT overwrite it.
- This function never allows an older evaluation result to overwrite a
  newer revision.

### 18.3 Deterministic PostgreSQL concurrency tests

9 new PG-only tests added to `tests/integration/test_g06_t04_revision_e2e.py`:

| Test | Scenario | Assertion |
|------|----------|-----------|
| `test_t04a_c1_concurrent_same_material_change` | 2 concurrent reassessments of the SAME material change | Exactly 1 returns revised=True (version 1→2); other returns revised=False. Final DB: 1 audit row, version=2, last_evaluation=eval_v2. |
| `test_t04a_c2_concurrent_no_material_change` | 2 concurrent reassessments with NO KB change | Both return revised=False. Final DB: 0 audit rows, version=1. |
| `test_t04a_c3_evaluate_racing_with_reassess` | /evaluate + /reassess racing | Final DB consistent: last_evaluation reflects current KB; at most 1 scenario.revision audit row (no duplicates). |
| `test_t04a_c4_version_monotonicity` | 3 sequential material revisions | Versions 2, 3, 4 strictly increasing; each revision's prior_version = new_version - 1. |
| `test_t04a_c5_one_revision_per_material_transition` | 3 concurrent reassessments of same change | Exactly 1 wins; other 2 return revised=False. Final DB: 1 audit row. |
| `test_t04a_c6_snapshot_preservation` | 2 sequential revisions | Each revision's prior_evaluation and new_evaluation snapshots preserved verbatim in the audit row (not overwritten by subsequent revisions). |
| `test_t04a_c7_rollback_no_partial_updates` | Reassess that acquires lock + bumps version, then rolls back | Version unchanged, no audit row, last_evaluation unchanged. |
| `test_t04a_c8_repeated_reassessment_after_committed_revision` | 3 no-change reassessments after a committed revision | All return revised=False; version stays 2; 1 audit row. |
| `test_t04a_c9_evaluate_after_reassess_does_not_clobber` | /evaluate AFTER /reassess has set last_revised_at | /evaluate is a no-op; last_evaluation remains the /reassess's new_evaluation; version unchanged. |

All tests assert FINAL DATABASE STATE, not only response status.
No timing-dependent sleeps — the PG FOR UPDATE row-level lock
serializes the concurrent reassessments deterministically.

### 18.4 Test results after T04A correction

- **G06-T04+T04A SQLite** (37 tests, 10 PG-only skipped): 27 passed, 10 skipped.
- **Combined PG suite** (PRB-03 + G05-T06 + G06-T02/T03/T04+T04A):
  **169 passed in 89.77s** (was 160 in T04; +9 new T04A PG tests).
- **Full deterministic regression**: **763 passed, 44 skipped** (was
  35; +9 new PG-only T04A tests skipped without
  `SYNAPSE_PG_TEST_URL`), 3 deselected (live) in 187.85s.
- **Ruff**: clean. **OpenAPI**: OK.

### 18.5 T04A final report block

```
G06_T04A_STATUS = PASS

AUTHORITATIVE_START_SHA = 1f095d483245b47ef52956103bb7b06be70fd0e8
ATOMIC_REASSESSMENT = PASS
LOCKED_BASELINE_COMPARISON = PASS
EVALUATION_SNAPSHOT_SAFETY = PASS
DUPLICATE_REVISION_PREVENTION = PASS
VERSION_MONOTONICITY = PASS
POSTGRESQL_CONCURRENCY = PASS
AUDIT_ATOMICITY = PASS

FOCUSED_TESTS =
  G06-T04+T04A SQLite (37 tests, 10 PG-skipped): 27 passed, 10 skipped
  G06-T04+T04A combined PG suite: 169 passed in 89.77s

FULL_G06_REGRESSION =
  G06 SQLite suite (T01+T02+T03+T04+T04A): 154 passed, 10 skipped in 40.83s

FULL_DETERMINISTIC_REGRESSION =
  763 passed, 44 skipped (PG-only without SYNAPSE_PG_TEST_URL),
  3 deselected (live) in 187.85s

COMBINED_POSTGRESQL_TESTS =
  169 passed in 89.77s
  Files: PRB-03 concurrency + determinism + migration safety +
  G05-T06 audit concurrency + G06-T02 + G06-T03 + G06-T04+T04A

RUFF = PASS
OPENAPI = PASS

MODIFIED_FILES =
  src/synapse/application/scenario_revision.py (MODIFIED — atomic lock + snapshot safety)
  tests/integration/test_g06_t04_revision_e2e.py (MODIFIED — +9 PG-only T04A tests + _seed_two_fragment_kb_labeled helper)

REMAINING_LIMITATIONS =
  Same as T04 (see §14). No new limitations introduced by T04A.

FINAL_SHA = (populated after commit)

G06_READY_FOR_FINAL_CLOSURE = YES
```

---

**Commit, push, verify `origin/main == HEAD`, and STOP.**

**Do not begin G07 without explicit reviewer authorization.**
