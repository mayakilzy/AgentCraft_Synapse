# G03_T04_IMPLEMENTATION_REPORT

## Header

| Field | Value |
|-------|-------|
| Task | G03-T04 — Evidence Verification Engine |
| Implementation date | 2026-10-08 |
| Base commit | `0c84f31` (G03-T03) |
| Final SHA | _set after commit_ |
| Authorization | User's "G03-T04 Authorization" message |

## STATUS: **PASS**

## Files changed

### Files added (2)

| Path | Purpose | LOC |
|------|---------|-----|
| `src/synapse/application/verification.py` | Verification engine: assess, independence, contradiction, stale, EvidenceDelta | 560 |
| `tests/integration/test_g03_t04_verification.py` | 11 tests covering all acceptance criteria | 480 |

### Files modified (0)

No existing files were modified. The verification engine is purely
additive — it reads existing ClaimRow and EvidenceFragmentRow records
and produces assessments stored in the existing `audit_events` table.

### NOT modified

- G01 domain contracts (EpistemicState, Claim, EvidenceFragment) — preserved
- AgentCraft-Toolkit — not accessed

## Actual LOC

| Category | LOC |
|----------|-----|
| `verification.py` (source) | 560 |
| `test_g03_t04_verification.py` (tests) | 480 |
| **Total new** | **~1,040** |

## Verification policy and decision table

| has_evidence | independent_sources | has_opposing | stale_count | outcome | reason_code |
|---|---|---|---|---|---|
| False | 0 | False | 0 | INSUFFICIENT_EVIDENCE | no_evidence_for_hypothesis |
| False | 0 | False | 0 | NOT_EVIDENCED | no_evidence_found |
| True | 1 | False | 0 | SOURCE_SUPPORTED | single_source; independent_sources=1 |
| True | ≥2 | False | 0 | VERIFIED | policy_met; independent_sources=N; policy=deterministic-v1 |
| True | any | True | 0 | CONTESTED | contradicting_evidence_present |
| True | ≤1 | False | >0 | STALE_OR_CONTEXT_MISMATCH | evidence_stale |

### Policy: VERIFIED

Per requirement #9: automated VERIFIED requires:
- ≥2 independent sources (different `source_uri`)
- No contradictions (no `contradicting_refs`)
- No stale evidence (when only 1 source)

If the policy is NOT met, the outcome is downgraded to SOURCE_SUPPORTED.

## Evidence independence rules

Per requirement #3: *"Never equate the number of citations with
independent confirmation. Multiple copies of the same original source
must not count as independent evidence."*

**Rule**: evidence fragments are grouped by `source_uri`. Each unique
`source_uri` counts as ONE independent source. Two fragments from the
same `source_uri` count as ONE source (even if they're different excerpts
from the same paper).

## Examples of supporting and contradictory evidence

### Supporting evidence (1 source → SOURCE_SUPPORTED)
```
Claim: "Transformers use self-attention."
  evidence_refs: ["ef-1"]  → source_uri: "https://arxiv.org/abs/1706.03762"
  → outcome: SOURCE_SUPPORTED (1 independent source, no contradictions)
```

### Corroborated / Verified (2 independent sources → VERIFIED)
```
Claim: "Self-attention outperforms RNNs."
  evidence_refs: ["ef-a", "ef-b"]
    ef-a → source_uri: "https://arxiv.org/abs/1706.03762"
    ef-b → source_uri: "https://example.com/other-paper"
  → outcome: VERIFIED (2 independent sources, no contradictions, policy met)
```

### Contradictory (CONTESTED)
```
Claim: "Transformers are fast."
  evidence_refs: ["ef-sup"]      → "Transformers are fast."
  contradicting_refs: ["ef-opp"]  → "Transformers are slow."
  → outcome: CONTESTED (both preserved, neither deleted)
```

### Same source (copies → still 1 independent)
```
Claim: "X is true."
  evidence_refs: ["ef-1", "ef-2"]  → both from source_uri: "https://same.com"
  → outcome: SOURCE_SUPPORTED (1 independent source, not 2)
```

## EvidenceDelta behavior

Per requirement #8: EvidenceDelta records meaningful assessment changes.

When `assess_claim()` detects that the outcome has changed from the
previous assessment (e.g., SOURCE_SUPPORTED → VERIFIED after new
independent evidence was added), it:
1. Records an `evidence_delta.recorded` audit event with the prior
   outcome, new outcome, and reason
2. The delta is immutable — never overwritten
3. The full audit trail is queryable via `audit_events`

Example delta:
```json
{
  "prior_outcome": "source_supported",
  "new_outcome": "verified",
  "reason": "supporting: 1 → 2; opposing: 0 → 0; independent_sources: 1 → 2",
  "update_method": "deterministic-v1"
}
```

## Test results

| # | Test | Result |
|---|------|--------|
| 1 | Source-supported claim is not automatically verified | ✅ PASS |
| 2 | Independent corroboration is distinguished from repeated copies | ✅ PASS |
| 3 | Contradictory evidence is preserved | ✅ PASS |
| 4 | Context-specific apparent contradictions are not falsely merged | ✅ PASS |
| 5 | Stale evidence is identified | ✅ PASS |
| 6 | Unsupported hypotheses are not promoted | ✅ PASS |
| 7 | Verification outcomes include clear reason codes | ✅ PASS |
| 8 | EvidenceDelta records meaningful assessment changes | ✅ PASS |
| 9 | Repeated assessment is idempotent | ✅ PASS |
| 10 | New evidence triggers appropriate reassessment | ✅ PASS |
| 11 | G01/G02/G03 regression suite remains green | ✅ PASS |

### Quality gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 103 files formatted |
| `pytest` (deterministic) | ✅ 322/322 pass, 3 live skipped |

## Carry-forward corrections addressed

| Correction | How addressed |
|-----------|---------------|
| Missing capabilities without negative evidence → UNKNOWN/NOT_EVIDENCED | `assess_missing_capability()` returns `NOT_EVIDENCED` with reason "absence_of_evidence_is_not_evidence_of_absence" |
| INTEGRATES_WITH must not be treated as ALTERNATIVE_TO | `find_alternatives()` in T03 uses REPLACES + INTEGRATES_WITH; the verification engine does not treat INTEGRATES_WITH as a contradiction predicate |
| Contradiction detection considers claim identity, conditions, context | `assess_claim()` checks `contradicting_refs` on the specific claim; context-specific claims with different `validity_conditions` are NOT falsely merged (Test 4) |
| Extraction completion must account for extractor version | Documented limitation — not implemented in T04 |
| Concurrent job safety | Documented limitation — application-level checks, not DB-level constraints |
| PostgreSQL production validation | ⛔ Unresolved — user must validate all migrations on PostgreSQL |

## Limitations

| # | Limitation | Severity | Resolution |
|---|-----------|----------|------------|
| L-01 | Assessments stored in `audit_events` (not a dedicated table) — harder to query | Low | A future group can add a `verification_assessments` table |
| L-02 | VERIFIED is automatic when ≥2 independent sources + no contradictions — no human review required | Medium | Acceptable for the minimal slice; a future policy can require human review for VERIFIED |
| L-03 | Staleness check is time-based only (no content-version comparison) | Low | A future group can compare content_fingerprint versions |
| L-04 | No concurrent-assessment safety (SELECT + INSERT race) | Medium | A future group can add advisory locks |
| L-05 | G02 PostgreSQL blocker carries forward | Medium | User must validate on PostgreSQL |

## STOP statement

**G03-T04 is complete. The agent will NOT:**

- Begin G03-T05 (knowledge API + demo) without explicit approval.
- Expand the API.
- Modify AgentCraft-Toolkit.

Per the user's authorization:
> *"STOP after G03-T04. Do not begin G03-T05 without explicit approval."*

---

*End of G03-T04 Implementation Report — STOP for approval.*
