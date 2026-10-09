# G03_T04_VERIFICATION_POLICY_CORRECTION

## Header

| Field | Value |
|-------|-------|
| Correction date | 2026-10-08 |
| Base commit | `c18b9f2` (G03-T04 original) |
| Final SHA | _set after commit_ |
| Authorization | User's "G03-T04 Verification Policy Correction" |

## STATUS: **CORRECTED — PASS**

## Critical issue addressed

The original `deterministic-v1` policy automatically promoted claims to
VERIFIED when two different `source_uri` values supported a claim and no
contradictions were recorded. This was epistemically unsafe: different URLs
do not prove independent evidence origins, and absence of recorded
contradictions does not establish truth.

## Corrected decision table

| has_evidence | independent_primary_origins | has_opposing | stale_count | outcome | reason_code |
|---|---|---|---|---|---|
| False | 0 | False | 0 | INSUFFICIENT_EVIDENCE | no_evidence_for_hypothesis |
| True | 1 | False | 0 | SOURCE_SUPPORTED | single_source; policy=deterministic-v2 |
| True | ≥2 | False | 0 | CORROBORATED | independent_primary_origins=N; verified_unreachable=true |
| True | any | True | 0 | CONTESTED | contradicting_evidence_present |
| True | any | False | >0 (1 src) | STALE_OR_CONTEXT_MISMATCH | evidence_stale |
| False | 0 | False | 0 | NOT_EVIDENCED | no_evidence_found |

**VERIFIED is UNREACHABLE in deterministic-v2.**

## Evidence-origin independence rules

1. **Multiple URLs repeating one primary study** count as one evidence origin.
2. **Mirrors, reposts, and summaries** do not create independent confirmation.
3. **Unknown origin independence** must not be assumed — conservative default: count = 1.
4. **Distinct primary evidence origins** may qualify for CORROBORATED when
   their applicability is compatible — requires explicit provenance metadata
   (not implemented in the minimal slice).
5. **Default uncertain evidence-origin independence** → conservative assessment
   (SOURCE_SUPPORTED, not CORROBORATED or VERIFIED).

## Actual code changes

### Modified files (2)

| Path | Change |
|------|--------|
| `src/synapse/application/verification.py` | Policy version v1→v2; removed auto-VERIFIED; added `_count_independent_primary_origins()` (conservative default=1); updated `_determine_outcome()` signature and logic; updated assessment payload field names; fixed idempotency check to use `distinct_source_count` |
| `tests/integration/test_g03_t04_verification.py` | Rewritten with 17 tests (was 11) — 8 mandatory negative tests + 6 original acceptance tests + 3 policy-level tests |

### LOC changed

| Category | LOC |
|----------|-----|
| `verification.py` modifications | ~120 (replaced ~80, added ~40) |
| `test_g03_t04_verification.py` rewrite | ~480 (was ~480, fully rewritten with new test cases) |
| **Total** | **~600** |

## Key code changes

### 1. Policy version bumped

```python
POLICY_VERSION = "deterministic-v2"  # was "deterministic-v1"
VERIFIED_REACHABLE = False  # VERIFIED is unreachable
```

### 2. `_count_independent_primary_origins()` added

```python
def _count_independent_primary_origins(fragments) -> int:
    # Conservative default: 1.
    # Without provenance metadata, we cannot prove independence.
    return 1
```

### 3. `_determine_outcome()` corrected

- Removed the `VERIFIED` branch that auto-promoted based on source count
- `CORROBORATED` is now the highest reachable outcome
- Reason codes include `verified_unreachable=true`

### 4. Assessment payload uses `distinct_source_count`

The field `independent_source_count` was renamed to `distinct_source_count`
to avoid implying independence. The `independent_primary_origin_count`
field is added separately.

### 5. Idempotency check fixed

Uses `distinct_source_count` (matching the new payload field name).

## Mandatory negative tests — all PASS

| # | Test | Result |
|---|------|--------|
| 1 | Two URLs copying same primary study → NOT VERIFIED, NOT CORROBORATED | ✅ PASS |
| 2 | Two sources with unknown common origin → NOT independent | ✅ PASS |
| 3 | Two genuinely independent origins → CORROBORATED (with patched provenance) | ✅ PASS |
| 4 | No evidence → NOT VERIFIED | ✅ PASS |
| 5 | No contradictions recorded → does NOT imply VERIFIED | ✅ PASS |
| 6 | Contradictory evidence → CONTESTED | ✅ PASS |
| 7 | Repeated assessment → idempotent | ✅ PASS |
| 8 | EvidenceDelta records meaningful changes | ✅ PASS |

## Additional tests

| Test | Result |
|------|--------|
| Source-supported claim is not auto-verified | ✅ PASS |
| Stale evidence is identified | ✅ PASS |
| Unsupported hypotheses are not promoted | ✅ PASS |
| Verification outcomes include clear reason codes | ✅ PASS |
| VERIFIED_REACHABLE is False in v2 | ✅ PASS |
| `_count_independent_primary_origins` is conservative | ✅ PASS |
| `_determine_outcome` never returns VERIFIED | ✅ PASS |
| Context-specific contradictions not falsely merged | ✅ PASS |
| G01/G02/G03 regression | ✅ PASS |

## Quality gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 103 files formatted |
| `pytest` (deterministic) | ✅ 328/328 pass, 3 live skipped |

## Remaining limitations

| # | Limitation | Severity | Resolution |
|---|-----------|----------|------------|
| L-01 | `_count_independent_primary_origins` always returns 1 — no provenance metadata to distinguish primary origins from mirrors | Medium | Future group can check SourceRow.publisher/author for distinct values; check domain overlap; use explicit provenance from acquisition |
| L-02 | CORROBORATED is only reachable via patched provenance — in practice, all claims with ≥2 sources get SOURCE_SUPPORTED | Medium | Acceptable for the minimal slice; provenance enhancement is a future group |
| L-03 | VERIFIED is completely unreachable — no review mechanism exists | Expected | A future policy version (e.g., deterministic-v3 with human-review) can make it reachable |
| L-04 | Assessments stored in `audit_events` (not a dedicated table) | Low | A future group can add a `verification_assessments` table |
| L-05 | G02 PostgreSQL blocker carries forward | Medium | User must validate on PostgreSQL |

## STOP statement

**Policy correction complete. The agent will NOT:**

- Begin G03-T05 without explicit approval.
- Modify AgentCraft-Toolkit.

Per the user's authorization:
> *"STOP for approval."*

---

*End of G03-T04 Verification Policy Correction — STOP for approval.*
