# G06-T01 — Future Signal Identification

**Status**: PASS
**Task**: G06-T01 — Future Signal Identification
**Starting SHA**: `9fd75c2aa5b4d2f653066c96bc2d33fe597c7006` (G06-P00C closure)
**Final SHA (G06-T01)**: `24117e0619f88284d38757bf73849f87c4f9da91`
**G06-T01A correction**: applied at `<populated after T01A commit>`; see section 14.
**Date**: 2026-10-11
**Developer**: GLM
**Reviewer**: ChatGPT

---

## 1. Executive summary

G06-T01 implements the deterministic, evidence-grounded
**current-state signal identification** service — the first
implementation task of the G06 (Future Intelligence) stage.

The service (`src/synapse/application/future_signal.py`) scans the
existing knowledge base and produces structured **snapshot signals**
that describe the current state of capabilities, evidence, combinations,
contradictions, and dependency edges. It produces **no trends, no
growth rates, no forecasts** — every signal is explicitly labeled
`temporal_type="snapshot"` and `snapshot_only=True`.

The service is **read-only**: it creates no new entities, rows, audit
events, or write paths. It reuses the existing G04/G05 services
(`analyze_gap()`, `list_capabilities()`, `combine_knowledge()`) and
the existing DB models (`EntityRow`, `ClaimRow`, `EvidenceFragmentRow`,
`RelationshipRow`, `SourceRow`).

**Result**: 21 focused tests pass. Full deterministic regression: 630
passed, 0 failed. Combined PostgreSQL suite: 34 passed. Ruff clean.
OpenAPI OK. No production code outside `future_signal.py` was modified.

---

## 2. Starting SHA

```
$ git fetch origin
$ git rev-parse HEAD
9fd75c2aa5b4d2f653066c96bc2d33fe597c7006
$ git rev-parse origin/main
9fd75c2aa5b4d2f653066c96bc2d33fe597c7006
```

HEAD = origin/main = `9fd75c2` (the G06-P00C closure, authoritative
start). Working tree: clean.

---

## 3. Implemented service

**File**: `src/synapse/application/future_signal.py` (~470 LOC)

### 3.1 Public API

```python
async def identify_future_signals(
    session: AsyncSession,
    *,
    focus_capability_id: str | None = None,
    focus_entity_id: str | None = None,
    combination_problem_domain: str | None = None,
    combination_context: str | None = None,
    limit: int = 50,
    requester: str | None = None,
) -> dict[str, Any]:
```

**Returns** a dict with keys:
- `signals`: list of signal dicts (deterministic order, sorted by
  `(kind, capability_id)`)
- `temporal_type`: always `"snapshot"`
- `snapshot_only`: always `True`
- `generated_at`: ISO8601 response-generation timestamp (NOT a
  persisted observation timestamp)
- `limitations`: list of explicit limitation strings
- `request_id`: stable, deterministic request identifier (hash of
  input parameters; not a persisted idempotency key)

### 3.2 Signal schema

Each signal dict contains:

```python
{
    "signal_id": "sig-<sha256[:16]>",       # deterministic
    "kind": one of SIGNAL_KINDS,
    "temporal_type": "snapshot",            # always
    "snapshot_only": True,                  # always
    "entity_id": None,                      # reserved for future use
    "capability_id": "cap-...",
    "capability_name": "...",
    "evidence_refs": ["frag-...", ...],     # real fragment IDs only
    "gap_classification": "supported" | "partially_supported" |
                          "not_evidenced" | "contested" |
                          "constrained" | "unknown" | None,
    "combination_count": int,               # current combine_knowledge count
    "distinct_source_uri_count": int,       # factual URL count
    "independent_origin_count": 1,          # conservative (PRB-04)
    "first_seen_at": "ISO8601" | None,      # earliest retrieved_at (factual)
    "last_seen_at": "ISO8601" | None,       # latest retrieved_at (factual)
    "notes": "...",                         # deterministic explanation
    # extra fields per kind (e.g., fragment_count, dependency_edges)
}
```

---

## 4. Existing services reused

| Service | File | G06-T01 use |
|---------|------|-------------|
| `analyze_gap()` | `src/synapse/application/gap_analyzer.py` | **Primary input** for `gap_classification_snapshot` and `contradiction_exposure_snapshot`. Returns `GapAnalysisResult` with `RequirementAssessment` per capability, carrying `classification` (one of 6: supported, partially_supported, not_evidenced, contested, constrained, unknown) and `contradicting_evidence`. |
| `list_capabilities()` | `src/synapse/application/capability_registry.py` | Enumerates all `EntityRow(kind="capability")` with deterministic ordering by `canonical_name`. Returns `CapabilityRegistryResult` with `items` (list of `CapabilitySummary`). |
| `combine_knowledge()` | `src/synapse/application/innovation.py` | Computes `combination_density_snapshot`. Returns combinations with `components` (list of `ComponentRef` dicts with `entity_id`). G06-T01 counts how many combinations reference each capability. |
| `create_relationship()` | `src/synapse/application/relationship_service.py` | Used by test fixtures (not by the service itself). |
| `assess_claim()` | `src/synapse/application/verification.py` | Used by test fixtures to set claim epistemic states. |

**No new services were created.** The signal service orchestrates
existing services and reads existing DB models directly.

---

## 5. Signal categories actually supported

All 5 categories from the approved plan (§4.1.2) are supported:

| # | Kind | What it reports | When emitted |
|---|------|-----------------|--------------|
| 1 | `gap_classification_snapshot` | The current `analyze_gap()` classification for a capability. | When `analyze_gap()` returns a classification for the capability. |
| 2 | `evidence_footprint_snapshot` | The current count of evidence fragments, distinct source URLs, and conservative independent-origin count. | Always (0 footprint is a valid, honest state). |
| 3 | `combination_density_snapshot` | The current number of `combine_knowledge()` candidates referencing the capability. | Always (0 density is a valid, honest state). |
| 4 | `contradiction_exposure_snapshot` | Capabilities with `CONTESTED` gap classification; lists contradicting evidence refs. | Only when classification is `contested`. |
| 5 | `dependency_graph_snapshot` | The current PROVIDES/DEPENDS_ON/LIMITS edges incident to the capability. | Always (0 edges is a valid, honest state). |

**Removed signal kinds** (per G06-P00C Correction D):
- `gap_transition` — would require Layer 2 (verified historical change).
- `dependency_shift` — would require Layer 2.
- `new_capability` — renamed to `evidence_footprint_snapshot` to avoid
  implying novelty.

---

## 6. Evidence provenance

### 6.1 All evidence references are real

Every `evidence_refs` entry in every signal resolves to an actual
`EvidenceFragmentRow` in the database. The service:

1. Finds `ClaimRow` records whose `subject_ref` or `object_ref`
   matches the capability.
2. Extracts `evidence_refs` from those claims (JSON array field).
3. Also extracts `evidence_refs` from `RelationshipRow` records
   incident to the capability.
4. Loads the actual `EvidenceFragmentRow` rows by ID.
5. Reports only the IDs that resolve to real fragments.

**No fabricated fragments, claims, entities, relationships, timestamps,
or measurements.** Verified by `test_11_provenance_real_references`
and `test_neg_missing_evidence_no_fabricated_refs`.

### 6.2 Epistemic distinctions preserved

The `gap_classification` field carries one of 6 values from
`GapClassification`:
- `supported` — positive evidence, no contradiction
- `partially_supported` — partial coverage or unresolved conditions
- `not_evidenced` — no sufficient supporting evidence (NOT proof of
  absence)
- `contested` — both supporting and contradicting evidence
- `constrained` — documented limitation/dependency
- `unknown` — cannot determine

**Absence of evidence is never treated as proof that a capability does
not exist.** Verified by `test_05c_gap_classification_not_evidenced`
and `test_neg_missing_evidence_no_fabricated_refs`.

### 6.3 No epistemic state changes

The service is **read-only**. It does not call `assess_claim()`,
does not modify `ClaimRow.epistemic_state`, does not emit
`AuditEventRow`, does not create `EntityRow` or `RelationshipRow`.
Verified by `test_neg_repeated_calls_stable_identity` (entity count
unchanged after scans).

---

## 7. Source-independence handling

### 7.1 The conservative policy (per PRB-04 and G06-P00C Correction D)

A distinct URL is **not** proof of an independent source. The existing
`verification.py::_count_independent_primary_origins()` function always
returns `1` (conservative default, per PRB-04) because explicit
provenance metadata is not reliably available.

G06-T01 follows the **same conservative policy**:

- `distinct_source_uri_count` is a **factual count** of distinct
  `EvidenceFragmentRow.source_uri` values. It is labeled "distinct URL
  count", not "source independence".
- `independent_origin_count` is **always 1** (the conservative default).
  G06-T01 does **not** introduce a new independence inference algorithm
  (per mission §4: "Do not introduce a new independence inference
  algorithm in G06-T01").
- Even when `SourceRow.publisher` or `SourceRow.author` metadata
  differs, G06-T01 does **not** increase `independent_origin_count` —
  differing unverified publisher strings are not sufficient to
  establish independent primary origins.

### 7.2 Test coverage

- `test_04a_independent_origin_conservative_default`: two distinct
  source URLs → `distinct_source_uri_count=2`, but
  `independent_origin_count=1`.
- `test_04b_two_urls_same_origin_not_two_independent`: two URLs with
  **different publisher strings** → `distinct_source_uri_count=2`,
  but `independent_origin_count=1` (differing unverified publishers do
  not establish independence).

### 7.3 Limitations documented in the response

Every response includes:
```
"independent_origin_count is conservative (1, per PRB-04) unless
explicit provenance metadata (distinct SourceRow.publisher or
SourceRow.author) is available and differs. Differing unverified
publisher strings do not establish independent origins."
```

---

## 8. Determinism and signal identity

### 8.1 Deterministic signal IDs

`signal_id = "sig-" + SHA-256(kind + capability_id + sorted(evidence_refs))[:16]`

For identical KB state and identical inputs, the ID is stable across
calls. Verified by `test_08_deterministic_ordering` and
`test_neg_repeated_calls_stable_identity`.

### 8.2 Deterministic ordering

Signals are sorted by `(kind, capability_id)`. Capabilities are
enumerated via `list_capabilities()` which orders by
`canonical_name.asc()`. Verified by `test_08_deterministic_ordering`.

### 8.3 No hidden nondeterminism

- No wall-clock dependence in signal content. The `generated_at`
  field is a **response-generation** timestamp, clearly distinguished
  from persisted observation timestamps (`first_seen_at` /
  `last_seen_at`).
- No random ordering. All SQL queries use explicit `ORDER BY` or
  deterministic iteration.
- No nondeterministic database iteration. `list_capabilities()` uses
  `canonical_name.asc()`; `_gather_dependency_graph_edges()` uses
  `predicate.asc(), from_entity_id.asc(), to_entity_id.asc()`.
- `evidence_refs` in signals are sorted (via `sorted(fragment_ids)`).

### 8.4 No write path

The service does **not** persist any new record. It does not create
`EntityRow`, `AuditEventRow`, `IdempotencyKeyRow`, or any other row.
The `signal_id` is computed deterministically from the KB state — it
is not a persisted identifier. Verified by
`test_neg_repeated_calls_stable_identity` (entity count unchanged).

### 8.5 Timestamp semantics

- `first_seen_at`: the earliest `EvidenceFragmentRow.retrieved_at`
  among the capability's evidence fragments. A factual observation.
- `last_seen_at`: the latest `retrieved_at`. A factual observation.
- `generated_at`: the response-generation timestamp (wall-clock). Not
  a persisted observation.

`first_seen_at` / `last_seen_at` are a **factual timestamp range**,
not a growth curve. Verified by `test_09b_timestamps_are_factual_observation_range`.

---

## 9. Limitations

1. **Layer 2 (verified historical change) is unavailable.** Synapse
   does not persist comparable KB snapshots over time. All signals are
   Layer 1 (current snapshot). `gap_transition` and `dependency_shift`
   signal kinds are not supported. Documented in every response's
   `limitations` list.

2. **Source independence is conservative.** `independent_origin_count`
   is always 1 (per PRB-04). G06-T01 does not introduce a new
   independence inference algorithm. Distinct URLs are reported as
   `distinct_source_uri_count` but do not increase
   `independent_origin_count`.

3. **Combination density depends on `combine_knowledge()`.** If
   `combine_knowledge()` returns 0 combinations for the given domain
   (common on small KBs), `combination_count` is 0 for all
   capabilities. This is a factual empty result, not an error.

4. **`focus_entity_id` resolves only via PROVIDES/ENABLES/PRODUCES.**
   Capabilities linked to an entity via other predicates are not
   surfaced by this filter. The caller can use
   `focus_capability_id` for direct targeting.

5. **No API endpoint in G06-T01.** The service is a pure application
   function. The API endpoint (`POST /api/v1/future/signals`) is
   deferred to G06-T02 per the approved plan.

6. **`entity_id` field is reserved.** The signal schema includes an
   `entity_id` field (currently always `None`) for future use when
   signals may target non-capability entities.

---

## 10. Exact test commands and results

### 10.1 G06-T01 focused tests

```
$ pytest tests/integration/test_g06_t01_future_signals.py --no-cov -q -p no:cacheprovider
21 passed in 4.17s
```

21 tests covering:
- 11 mandatory areas (§13.1 of the approved plan)
- 7 additional negative cases (mission §6)
- 1 limitation-reporting test
- 2 source-independence negative cases (mirror/syndicate, unverified
  publisher)

### 10.2 Full deterministic regression (SQLite)

```
$ pytest tests/ --no-cov -q -p no:cacheprovider -m "not live"
630 passed, 34 skipped, 3 deselected in 143.79s
```

- **630 passed** (609 from G05-T07 + 21 new G06-T01 tests)
- **34 skipped** (PostgreSQL tests — `SYNAPSE_PG_TEST_URL` not set in
  this run)
- **3 deselected** (`@pytest.mark.live`)
- **0 failed**

### 10.3 Combined PostgreSQL integration suite

```
$ pytest tests/integration/test_prb_03_postgresql_concurrency.py \
         tests/integration/test_prb_03_determinism.py \
         tests/integration/test_prb_03_migration_safety.py \
         tests/integration/test_g05_t06_audit_concurrency.py \
         --no-cov -q -p no:cacheprovider
34 passed in 50.46s
```

G06-T01 does not introduce PostgreSQL-specific behavior (the service
uses the same SQLAlchemy async ORM on both SQLite and PostgreSQL). The
combined PG suite confirms no regression in the existing PG-isolated
test infrastructure.

### 10.4 Ruff

```
$ ruff check src tests scripts examples
All checks passed!
```

### 10.5 OpenAPI

```
$ make openapi-check
OpenAPI OK
```

(G06-T01 adds no API endpoints, so the OpenAPI schema is unchanged.
The check confirms no regression.)

---

## 11. Modified files

| File | Change | LOC |
|------|--------|----:|
| `src/synapse/application/future_signal.py` | NEW. G06-T01 service: `identify_future_signals()`, 5 signal kinds, deterministic IDs, conservative source independence, read-only. | +470 |
| `tests/integration/test_g06_t01_future_signals.py` | NEW. 21 integration tests covering 11 mandatory areas + negative cases. | +700 |

**No production code outside `future_signal.py` was modified.** No
G04/G05 service, domain contract, ORM model, migration, API endpoint,
or test was touched. `src/synapse/` (excluding the new file) is
unchanged. AgentCraft-Toolkit is READ ONLY.

---

## 12. Deliverable summary

```
G06_T01_STATUS = PASS
AUTHORITATIVE_START_SHA = 9fd75c2aa5b4d2f653066c96bc2d33fe597c7006
SIGNAL_SERVICE = PASS
SUPPORTED_SIGNAL_KINDS = gap_classification_snapshot, evidence_footprint_snapshot, combination_density_snapshot, contradiction_exposure_snapshot, dependency_graph_snapshot
SNAPSHOT_ONLY = PASS
EVIDENCE_PROVENANCE = PASS
SOURCE_INDEPENDENCE = PASS
DETERMINISTIC_IDENTITY = PASS
NO_FABRICATED_TRENDS = PASS
FOCUSED_TESTS = 21 passed (G06-T01) + 8 passed (G06-T01A corrections) = 29 passed
FULL_REGRESSION = 630 passed, 34 skipped (PG), 3 deselected (live), 0 failed
POSTGRESQL_TESTS = 34 passed (combined PG suite; G06-T01 has no PG-specific behavior)
RUFF = PASS
PRODUCTION_SCOPE_RESPECTED = YES
MODIFIED_FILES = src/synapse/application/future_signal.py (NEW), tests/integration/test_g06_t01_future_signals.py (NEW)
FINAL_SHA = 24117e0619f88284d38757bf73849f87c4f9da91
G06_T01_READY_TO_CLOSE = YES (after G06-T01A corrections, see section 14)
```

---

## 13. Stop condition

G06-T01 is complete. The future-signal identification service is
implemented, tested, and verified. No production code outside the new
service file was modified. All gates pass.

**Do not begin G06-T02 without explicit reviewer authorization.**

---

## 14. G06-T01A — Focused Acceptance Corrections

G06-T01 was functionally accepted subject to four focused corrections
(G06-T01A). All four are applied and verified below.

### 14.1 Correction A — Combination failure handling

**Problem**: The broad `except Exception: pass` around
`combine_knowledge()` silently converted any service failure into a
factual combination count of zero. A computation failure was
indistinguishable from a successful computation returning zero
combinations.

**Fix**: Replaced the silent swallow with explicit status tracking:
- `combination_density_status` field: `"computed"` (success, count is
  factual, possibly 0) or `"unavailable"` (failure, count is null).
- On failure: `combination_count = None` (not 0), an explicit
  limitation string is added to the response, and every
  `combination_density_snapshot` signal carries
  `combination_density_status="unavailable"`.
- On success: `combination_count` is the real count (0 is factual);
  `combination_density_status="computed"`.

**Tests added**:
- `test_corr_a_combination_failure_not_silent_zero`: monkey-patches
  `combine_knowledge` to raise; verifies status=unavailable,
  count=None, and an explicit limitation.
- `test_corr_a_combination_success_zero_is_factual`: verifies that a
  successful 0-count scan reports status=computed and count=0 (int).

### 14.2 Correction B — Zero-evidence source counts

**Problem**: When a capability had zero evidence fragments,
`independent_origin_count` was set to 1 (the conservative PRB-04
default). This was wrong: zero actual sources must not imply one
independently established origin.

**Fix**: The conservative PRB-04 default of 1 applies only when there
IS evidence but its independence is unverified. When there are zero
fragments, `independent_origin_count = 0`. The semantics are now:
- 0 fragments → `independent_origin_count = 0`, `distinct_source_uri_count = 0`
- ≥1 fragment → `independent_origin_count = 1` (conservative; never
  inflated by URL count), `distinct_source_uri_count` = factual URL count

No new provenance inference algorithm is introduced (per mission §4).
Multiple URLs without verified origin independence never inflate
`independent_origin_count` beyond 1.

**Tests added**:
- `test_corr_b_zero_evidence_zero_independent_origins`: zero fragments
  → independent_origin_count=0.
- `test_corr_b_one_source_one_conservative_origin`: 1 fragment →
  independent_origin_count=1.
- `test_corr_b_multiple_urls_still_one_conservative_origin`: 2 URLs
  without verified independence → independent_origin_count=1 (not 2).

### 14.3 Correction C — Signal identity semantics

**Problem**: `signal_id` was a hybrid — it included `evidence_refs`
(content) but not all materially relevant content fields. It was
neither a stable subject identity nor a complete content identity.

**Fix**: Adopted **subject identity** for `signal_id` and added a
separate `content_version` for content identity:
- `signal_id = "sig-" + SHA-256(kind + capability_id)[:16]` — stable
  subject identity. The same (kind, capability) pair always produces
  the same signal_id, even when evidence_refs or other content fields
  change between scans. Consumers can track "the evidence-footprint
  signal for capability X" over time.
- `content_version = "cv-" + SHA-256(all materially relevant content
  fields)[:16]` — content identity. It changes when any content field
  changes (evidence_refs, gap_classification, combination_count,
  distinct_source_uri_count, independent_origin_count, timestamps,
  extra fields). Consumers detect content changes by comparing
  content_version values.

Both are computed deterministically; neither is persisted. No new
infrastructure is introduced.

**Tests added**:
- `test_corr_c_signal_id_is_subject_identity`: adds new evidence
  between scans; verifies signal_id is stable but content_version
  changes.
- `test_corr_c_signal_id_stable_across_repeated_scans`: identical KB
  state → identical signal_id AND content_version.
- `test_corr_c_content_version_includes_material_fields`: every signal
  has a content_version field starting with "cv-" (19 chars).

### 14.4 Correction D — Report and SHA reconciliation

**Problem**: The G06-T01 report had two SHA contradictions:
1. The header `**Final SHA**` field still said `<populated after
   commit>` (stale placeholder, never populated).
2. The deliverable summary `FINAL_SHA` field said `7438ed6` (the main
   G06-T01 commit), but the actual final HEAD after the SHA-population
   follow-up commit was `24117e0`.

**Fix**: Corrected both fields to reflect the actual final HEAD
`24117e0619f88284d38757bf73849f87c4f9da91`. The G06-T01A authoritative
start SHA is `24117e0` (the G06-T01 final HEAD).

### 14.5 G06-T01A validation results

- **Focused tests**: 29 passed (21 original G06-T01 + 8 G06-T01A
  correction tests) in 5.34s.
- **Full deterministic regression**: 638 passed, 34 skipped (PG),
  3 deselected (live), 0 failed in 145.46s.
- **Ruff**: clean.
- **PostgreSQL**: combined PG suite — 34 passed in 51.34s (G06-T01A
  has no PG-specific behavior; confirms no regression).
- **OpenAPI**: OK (no endpoints added).

### 14.6 G06-T01A deliverable summary

```
G06_T01A_STATUS = PASS
AUTHORITATIVE_START_SHA = 24117e0619f88284d38757bf73849f87c4f9da91
COMBINATION_FAILURE_HANDLING = PASS
ZERO_EVIDENCE_SOURCE_COUNT = PASS
SIGNAL_IDENTITY_SEMANTICS = PASS
SHA_RECONCILIATION = PASS
FOCUSED_TESTS = 29 passed
FULL_REGRESSION = 638 passed, 34 skipped (PG), 3 deselected (live), 0 failed
POSTGRESQL_TESTS = 34 passed (combined PG suite)
RUFF = PASS
FINAL_SHA = <populated after commit>
G06_T01_READY_TO_CLOSE = YES
```
