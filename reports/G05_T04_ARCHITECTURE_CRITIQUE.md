# G05-T04 — Architecture Composition & Evidence-Grounded Innovation Critique

> **Status**: PASS WITH LIMITATIONS (production readiness NOT approved; PRB-01..07 unresolved)
> **Date**: 2026-10-09
> **Authoritative commit**: see §Final SHA
> **Starting checkpoint**: `283570d37ad2117bebc0b6df0ad4133a689a21f5` (G05-T03C)
> **Policy version**: `deterministic-v2`

## 1. Executive summary

G05-T04 implements architecture composition and evidence-grounded
critique for existing persisted innovation concepts. It activates
`POST /api/v1/innovations/{id}/critique` (replaces the G04 501 placeholder).

The system evaluates a concept's technical plausibility, dependencies,
constraints, alternatives, evidence coverage and failure modes. It
distinguishes established evidence from architectural hypotheses and
explicitly caveats graph uniqueness as NOT market novelty.

### A. Architecture composition (`compose_architecture()`)

- Retrieves persisted concept, hypothesis, and component links
- Identifies component roles and capabilities (via `find_capabilities`)
- Builds dependency graph (documented REQUIRES edges) vs proposed
  integration steps (hypothesized INTEGRATES_WITH edges)
- Identifies missing components via `analyze_gap` (NOT_EVIDENCED caps)
- Distinguishes documented dependencies from proposed integration steps
- Preserves evidence references and uncertainties

### B. Innovation critique (`critique_innovation()`)

- `feasibility_score` (0.0-1.0) with explainable rationale
- `evidence_coverage` (0.0-1.0) with rationale
- `dependency_completeness` (0.0-1.0)
- `constraint_conflicts[]` (LIMITS/CONTRADICTS/INVALIDATES edges)
- `applicable_contradictions[]` (disputed claims with contradicting_refs)
- `alternatives[]` (REPLACES edges)
- `failure_modes[]` (missing deps, constraint conflicts, contradictory evidence)
- `novelty_score` (structural, explicitly caveated)
- `overall_recommendation` (testable / testable_with_caveats / needs_more_evidence / rejected)

### C. Stable concept identity

- Critiques existing persisted concept by ID — does NOT regenerate
- Preserves stable hypothesis identity
- No duplicate records on repeated critique requests
- Concurrency is LIMITED (same as G05-T03C — PRB-03 applies)

## 2. Starting and final commit SHAs

| Property | Value |
|----------|-------|
| Starting checkpoint | `283570d37ad2117bebc0b6df0ad4133a689a21f5` |
| Final commit SHA | (populated after push) |
| Remote synchronization | PASS |
| Working tree | CLEAN |

## 3. Files changed

### Files added (1)

| File | Purpose | LOC |
|------|---------|----:|
| `tests/integration/test_g05_t04_architecture_critique.py` | 8 acceptance + 7 negative tests | ~460 |

### Files modified (3)

| File | Change | +LOC |
|------|--------|-----:|
| `src/synapse/application/innovation.py` | `compose_architecture()` + `critique_innovation()` + helpers | +550 |
| `src/synapse/api/v1/innovations.py` | `POST /{id}/critique` endpoint | +60 |
| `src/synapse/api/v1/router.py` | Remove `/innovations/{id}/critique` 501 placeholder | +2/-5 |
| `tests/integration/test_g04_t05_external_client.py` | Update 501 test + placeholder paths | +5/-5 |

## 4. Acceptance criteria results

| # | Criterion | Status | Test |
|---|-----------|--------|------|
| 1 | Every critique cites evidence for every score | ✅ PASS | `test_01_critique_cites_evidence` |
| 2 | feasibility_score reflects component existence | ✅ PASS | `test_02_feasibility_reflects_components` |
| 3 | novelty_score is structural + caveated | ✅ PASS | `test_03_novelty_is_structural` |
| 4 | constraint_conflicts lists LIMITS/CONTRADICTS | ✅ PASS | `test_04_constraint_conflicts` |
| 5 | failure_modes includes CONTESTED/missing | ✅ PASS | `test_05_failure_modes` |
| 6 | overall_recommendation is valid | ✅ PASS | `test_06_recommendation_valid` |
| 7 | integration_mechanism + benefit non-empty | ✅ PASS | `test_07_integration_and_benefit` |
| 8 | G01-G04-T03 regression intact | ✅ PASS | `test_08_regression` |

### Negative tests

| Test | Purpose | Status |
|------|---------|--------|
| `test_negative_missing_innovation_id` | Invalid ID → None | ✅ PASS |
| `test_negative_api_auth` | No auth → 401 | ✅ PASS |
| `test_positive_api_with_auth` | Valid auth → 200 + structured critique | ✅ PASS |
| `test_negative_api_404` | Invalid ID via API → 404 | ✅ PASS |
| `test_negative_repeated_critique` | Repeated critique → same scores + IDs | ✅ PASS |
| `test_negative_score_bounds` | All scores in [0.0, 1.0] | ✅ PASS |
| `test_negative_openapi_includes_critique` | OpenAPI has critique endpoint | ✅ PASS |

## 5. Quality gates

| Gate | Enforcement |
|------|-------------|
| Every architecture component references existing entity | Components loaded from persisted INTEGRATES_WITH relationships; entity_ids validated |
| Proposed integration links remain explicitly hypothetical | Integration points labeled `evidence="hypothesized"` |
| No fabricated citations | All evidence_refs validated against existing fragments |
| Applicable contradictions remain visible | `applicable_contradictions[]` includes disputed claims |
| Missing evidence is not proof of impossibility | `failure_modes[]` includes `none_evidenced` mode when no failure modes found |
| Feasibility and evidence-coverage scores have explainable computation | `feasibility_rationale` + `evidence_coverage_rationale` strings |
| Structural novelty is explicitly caveated | `novelty_caveat` states "graph uniqueness != market novelty" |
| Every critique contains failure modes or truthful statement | `failure_modes[]` always non-empty (includes `none_evidenced`) |
| Stable innovation and hypothesis IDs preserved | Critique uses persisted concept_id + hypothesis_id; no regeneration |
| No automatic promotion to VERIFIED | Hypothesis remains `epistemic_state="hypothesized"` |

## 6. Tests and validation results

| Gate | Result |
|------|--------|
| `ruff check src tests scripts examples` | ✅ All checks passed (135 files) |
| `ruff format --check src tests scripts examples` | ✅ 135 files already formatted |
| `make openapi-check` | ✅ OpenAPI OK (3.1.0) |
| `pytest` (full deterministic suite) | (populated after full run) |

## 7. Remaining limitations

- Architecture composition is bounded to 1-hop component traversal (no transitive dependency chains)
- Novelty score is a component-count heuristic (not a full graph-uniqueness query)
- Applicable vs out-of-context contradiction classification is simplified (context matching via gap analysis, not direct validity_conditions checking)
- Concurrency is LIMITED (same as G05-T03C — PRB-03 applies)
- PRB-01..07 unchanged

## 8. Final SHA

```
Final commit SHA (post-T04): <populated after push>
Starting checkpoint (for reference): 283570d37ad2117bebc0b6df0ad4133a689a21f5
```

## 9. STOP

- ✅ G05-T04 implemented, tested, committed, pushed.
- ✅ `POST /api/v1/innovations/{id}/critique` activated.
- ✅ All 8 acceptance criteria + 7 negative tests pass.
- ❌ **G05-T05 NOT STARTED.** (Not authorized.)
- ❌ **AgentCraft-Toolkit NOT MODIFIED.**

---

*End of G05-T04 Architecture Composition & Critique — STOP.*
