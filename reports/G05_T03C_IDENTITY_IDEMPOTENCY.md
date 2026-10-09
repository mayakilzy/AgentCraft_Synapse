# G05-T03C — Innovation Identity & Idempotency Closure

> **Status**: FOCUSED CORRECTION — PASS
> **Date**: 2026-10-09
> **Reviewer**: GLM Dev Agent
> **Starting checkpoint**: `4d512da647ed166608683f6c89965cd3c5e6c412` (G05-T03)
> **Final commit SHA**: see §Final SHA

## 1. Problem

G05-T03's `_persist_concept` function generated random UUID-based IDs for
every concept, hypothesis, and relationship on each call. Repeated
equivalent requests created **duplicate persisted records** — multiple
innovation entities, multiple hypothesis claims, and multiple
INTEGRATES_WITH relationships for the same logical concept.

## 2. Correction

### 2.1 Deterministic concept fingerprint

Added `_compute_concept_fingerprint()` which computes a SHA-256-based
fingerprint from normalized input:
- `problem_domain` (lowercased, stripped)
- `context` (lowercased, stripped, or empty)
- `combination_basis` (the capabilities involved)
- `template_name` (which of the 6 templates produced this concept)
- `component_entity_ids` (sorted for determinism)

Two requests with the same fingerprint produce the same persisted
concept identity. Requests with different fingerprints produce
distinct concepts.

### 2.2 Idempotent reuse

Modified `_persist_concept()` to:
1. Compute the fingerprint before creating any records.
2. Search for an existing `EntityRow(kind="project")` whose `attributes`
   column contains the fingerprint (stored as `concept_fingerprint`).
3. If found, reuse the existing concept and hypothesis IDs — no new
   records created.
4. If not found, create new records with **deterministic IDs** derived
   from the fingerprint (`innov-{fingerprint[:16]}`, `hyp-{fingerprint[:16]}`).

### 2.3 Duplicate relationship prevention

Each INTEGRATES_WITH relationship now uses a deterministic ID:
`rel-{sha256(fingerprint:component_id)[:16]}`. Before creating a
relationship, the code checks if one with the same ID already exists
and skips it if so.

### 2.4 Backward compatibility

- The API response contract is unchanged — `concepts[]` still contains
  the same fields with the same types.
- The `concept_fingerprint` and `template_name` fields are added to
  `EntityRow.attributes` (JSON text column) — no schema change.
- Existing G05-T03 tests pass unchanged.

## 3. Concurrency

**LIMITED**. The idempotency check uses a read-then-write pattern
(`SELECT ... then INSERT if not found`). Under true concurrent
multi-session access, two sessions could both read "not found" and
both attempt to INSERT, causing a primary-key conflict (since the IDs
are now deterministic, not random UUIDs).

This is a known limitation documented in PRB-03 (database-level
concurrency/idempotency guarantees). The deterministic IDs make
conflicts detectable (the second INSERT fails with a unique
constraint violation on the primary key), but the application-level
fallback is not implemented in G05-T03C.

**What IS safe**: sequential requests within a single session (tested
and verified). **What is NOT safe**: truly concurrent multi-session
requests for the same concept (documented as LIMITED).

## 4. Tests

### New tests (8 + 1 regression)

| Test | Purpose | Status |
|------|---------|--------|
| `test_01_same_request_repeated_sequentially` | Same request → same concept IDs | ✅ PASS |
| `test_02_equivalent_normalized_requests` | Different casing/whitespace → same IDs | ✅ PASS |
| `test_03_different_requests_remain_distinct` | Different problem domains → distinct IDs | ✅ PASS |
| `test_04_same_concept_under_repeated_api` | API called twice → same IDs | ✅ PASS |
| `test_05_duplicate_relationship_prevention` | No duplicate INTEGRATES_WITH edges | ✅ PASS |
| `test_06_no_duplicate_entities_or_claims` | No duplicate EntityRow or ClaimRow | ✅ PASS |
| `test_07_idempotency_key_conflict` | Same key, different payload → different IDs | ✅ PASS |
| `test_08_concurrent_requests` | Sequential calls in one session → no duplicates | ✅ PASS |
| `test_09_regression` | G01–G05-T03 regression intact | ✅ PASS |

## 5. Files changed

### Files added (1)

| File | Purpose | LOC |
|------|---------|----:|
| `tests/integration/test_g05_t03c_idempotency.py` | 8 idempotency tests + 1 regression | ~430 |

### Files modified (1)

| File | Change | +LOC |
|------|--------|-----:|
| `src/synapse/application/innovation.py` | Added `_compute_concept_fingerprint()` + `_find_existing_concept()` + modified `_persist_concept()` for idempotent reuse + deterministic IDs + duplicate relationship prevention | +120 |

### Files NOT modified

- No frozen domain contracts changed
- No migrations, no schema changes
- No API endpoint changes (response contract backward-compatible)
- No new dependencies
- AgentCraft-Toolkit NOT MODIFIED

## 6. Final SHA

```
Final commit SHA (post-T03C): <populated after push>
Starting checkpoint (for reference): 4d512da647ed166608683f6c89965cd3c5e6c412
```

## 7. STOP

- ✅ G05-T03C correction applied and tested.
- ✅ All 8 idempotency tests + 1 regression pass.
- ❌ **G05-T04 NOT STARTED.** (Not authorized.)
- ❌ **AgentCraft-Toolkit NOT MODIFIED.**

---

*End of G05-T03C Identity & Idempotency Closure — STOP.*
