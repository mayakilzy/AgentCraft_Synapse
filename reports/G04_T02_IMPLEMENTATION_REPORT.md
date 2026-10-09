# G04-T02 — Capability Registry & Gap Analysis Implementation Report

> **Status**: PASS WITH LIMITATIONS (production readiness NOT approved; PRB-01..07 unresolved)
> **Date**: 2026-10-09
> **Authoritative commit**: see §10 (Final commit SHA)
> **Starting checkpoint**: `535267cfc6a27e3ee8ee9275dd0844339d1b17b1` (G04-T01 closure review)
> **Policy version**: `deterministic-v2` (VERIFIED unreachable; conservative origin independence)

## 1. Executive summary

G04-T02 implements a compact Capability Registry + Gap Analysis layer as a
**logical view** over the existing G01–G04-T01 knowledge graph. It introduces
**no new database tables, no new migrations, no new pip dependencies, no
LLM provider, no vector database, no graph database, and no new crawler
providers**.

The registry enumerates `EntityRow(kind="capability")` records and surfaces
their providers (entities with PROVIDES / ENABLES / PRODUCES edges),
dependencies (REQUIRES / DEPENDS_ON), and limitations (LIMITS / CONTRADICTS
/ INVALIDATES). All evidence handling is delegated to the existing G02/G03
infrastructure (evidence fragments, source spans, `assess_claim()`).

The gap analyzer takes a list of required capability names plus optional
technical context and produces one of six classifications per requirement:
SUPPORTED, PARTIALLY_SUPPORTED, NOT_EVIDENCED, CONTESTED, CONSTRAINED,
UNKNOWN. The classification is deterministic, evidence-grounded, and
preserves contradictions rather than suppressing them.

A minimal read-only API surface exposes three endpoints under
`/api/v1/knowledge/capabilities` (deliberately namespaced to avoid
collision with the G01 system-capability endpoint at `/api/v1/capabilities`).

## 2. Starting and final commit SHAs

| Property | Value |
|----------|-------|
| Starting checkpoint | `535267cfc6a27e3ee8ee9275dd0844339d1b17b1` |
| Final commit SHA | `a4f75fd150eab1d4cc832ba9fa24bd1ca1109002` |
| Remote synchronization | PASS (verified via `git ls-remote`) |
| Working tree | CLEAN (in sync with origin/main) |

## 3. Files changed

### Files added (5)

| File | Purpose | LOC |
|------|---------|----:|
| `src/synapse/application/capability_registry.py` | Capability registry: list/get capabilities with providers, dependencies, limitations, evidence bundles | 635 |
| `src/synapse/application/gap_analyzer.py` | Gap analysis: 6-classification deterministic decision tree + evidence-chain collector | 818 |
| `src/synapse/api/v1/capability_registry.py` | Minimal read-only API: GET /knowledge/capabilities, GET /knowledge/capabilities/{id}, POST /knowledge/capabilities/analyze-gap | 187 |
| `tests/integration/test_g04_t02_capability_registry.py` | 16 mandatory acceptance tests + realistic AI-assistant demo + API integration tests (20 tests total) | 1067 |
| `scripts/demo_g04_t02_gap_analysis.py` | Standalone demonstration script for the report | 538 |
| **Total new** | | **3245** |

### Files modified (1)

| File | Change | +LOC | -LOC |
|------|--------|-----:|-----:|
| `src/synapse/api/v1/router.py` | Register the new `capability_registry.router` | 5 | 1 |

### Files NOT modified (intentional)

- All G01/G02/G03 source under `src/synapse/` (domain contracts frozen per ADR-0011)
- All G04-T01 source (`src/synapse/application/retrieval.py`, `src/synapse/api/v1/retrieval.py`) — unchanged
- All existing ADRs (0001–0111) — historical records, never retroactively modified
- All existing reports (`G04_T01_IMPLEMENTATION_REPORT.md`, `G04_T01_CLOSURE_REVIEW.md`) — preserved
- All migrations (0001–0004)
- `pyproject.toml` — no new dependencies

### Actual LOC added and removed

```
$ git diff --numstat 535267c..HEAD
538    0    scripts/demo_g04_t02_gap_analysis.py
187    0    src/synapse/api/v1/capability_registry.py
5      1    src/synapse/api/v1/router.py
635    0    src/synapse/application/capability_registry.py
818    0    src/synapse/application/gap_analyzer.py
1067   0    tests/integration/test_g04_t02_capability_registry.py
```

**Totals**: +3250 insertions, -1 deletion (net +3249 LOC across 6 files).

## 4. Capability Registry design

### Core principle

The Capability Registry is **a logical view, not a new persistence layer**.
A "capability" is an `EntityRow(kind="capability")` — the existing G03-T01
entity contract. A "provider" is any entity with an outgoing PROVIDES /
ENABLES / PRODUCES edge to that capability. Dependencies and limitations
are derived from existing RelationshipService predicates.

No new tables. No new migrations. No canonicalization duplication.

### Predicate semantics (preserved directionality)

| Predicate | Direction | Meaning |
|-----------|-----------|---------|
| `PROVIDES` | directed (provider → capability) | The from-entity is a documented provider |
| `ENABLES` | directed (provider → capability) | The from-entity enables the capability (weaker) |
| `PRODUCES` | directed (provider → capability) | The from-entity produces the capability as output |
| `REQUIRES` | directed (provider → dependency) | The from-entity requires the to-entity |
| `DEPENDS_ON` | directed (provider → dependency) | Alias for REQUIRES |
| `LIMITS` | directed (constraint → capability) | A documented limitation applies |
| `CONTRADICTS` | undirected | Contradicts another claim/relationship (preserved) |
| `INVALIDATES` | directed | Invalidates the target |
| `INTEGRATES_WITH` | undirected | NOT collapsed into ALTERNATIVE_TO (per §3 correction) |
| `REPLACES` | directed | The from-entity replaces the to-entity (alternative) |

### Public functions (`src/synapse/application/capability_registry.py`)

```python
async def list_capabilities(
    session, *, name_contains=None, limit=50
) -> CapabilityRegistryResult

async def get_capability(
    session, capability_id
) -> CapabilityDetail | None

async def find_capability_by_name(
    session, name
) -> CapabilityDetail | None

async def find_capability_by_alias_or_fuzzy(
    session, name
) -> CapabilityDetail | None  # sets match_quality: exact|alias|fuzzy
```

### Data classes (dict subclasses for JSON output)

- `CapabilitySummary` — lightweight list-view item (id, name, provider_count, limitation_count)
- `CapabilityDetail` — full detail (providers, dependencies, limitations, claims, evidence_refs)
- `ProviderInfo` — provider entity + predicate + relationship_id + evidence_refs
- `DependencyInfo` — dependency entity + predicate + relationship_id + evidence_refs
- `LimitationInfo` — limiting entity + predicate + relationship_id + evidence_refs
- `EvidenceRefInfo` — fragment + source_uri + spans
- `ClaimAssessmentInfo` — claim + latest verification assessment

## 5. Gap classification rules

Per the G04-T02 mission briefing §4, the gap analyzer produces one of six
classifications per (capability, candidate) pair. The decision tree is
deterministic and evaluated in priority order:

### Decision tree (priority order — first matching rule wins)

```
┌── Step 1: Resolve the capability entity (exact | alias | fuzzy | not_found)
│   └─ If not_found → NOT_EVIDENCED
│
├── Step 2: Find candidate providers (PROVIDES / ENABLES / PRODUCES edges)
│   └─ If no provider → NOT_EVIDENCED
│
├── Step 3: Collect claims + assessments + limitations
│
├── Step 4: Collect unsatisfied prerequisites (REQUIRES edges to
│   capabilities that have no provider themselves)
│
├── Step 5: Evaluate applicability of claims against the context
│   (using phrase-substring matching, NOT token-set overlap)
│
├── Step 6: Classification decision tree (priority order):
│
│   6a. CONTESTED — any assessed claim has outcome CONTESTED, OR
│       any claim has explicit contradicting_refs. Both sides preserved.
│
│   6b. CONSTRAINED — a documented LIMITS / INVALIDATES edge exists AND
│       no positive evidence overrides it.
│
│   6c. SUPPORTED — at least one assessed claim has outcome
│       SOURCE_SUPPORTED or CORROBORATED, AND no contradicting evidence,
│       AND validity_conditions match the requested context.
│
│   6d. PARTIALLY_SUPPORTED — provider exists with evidence-backed
│       PROVIDES edge but no assessed claim, OR assessed claim has
│       INSUFFICIENT_EVIDENCE outcome, OR validity_conditions don't
│       match the requested context but no contradiction.
│
│   6e. NOT_EVIDENCED — provider has a PROVIDES edge but no evidence-
│       backed claim at all (no evidence refs, no claims).
│
│   6f. UNKNOWN — any state not covered above (e.g., STALE evidence,
│       ambiguous assessment state).
```

### Gap views (separated per mission briefing §4 "Important distinction")

The result exposes `gap_views` with five separate lists:

| View | Contents |
|------|----------|
| `missing_evidence` | NOT_EVIDENCED requirement names |
| `partially_covered_requirements` | PARTIALLY_SUPPORTED requirement names |
| `contradictory_evidence` | CONTESTED requirement names |
| `unsatisfied_prerequisites` | CONSTRAINED entries with unmet dependencies (formatted as "capability: candidate REQUIRES X but no provider documents it") |
| `explicit_incompatibilities` | CONSTRAINED entries with LIMITS / INVALIDATES edges (formatted as "capability: limited by constraint_entity") |
| `unknown` | UNKNOWN requirement names |

A gap is an **evidence or requirement gap**, NOT a proven product deficiency.
The separation above respects the briefing's instruction: "Avoid collapsing
these into one generic 'missing' category."

### Conservative applicability matching (per §11)

The `_applicability_matches` function uses **phrase-substring matching**,
not token-set overlap. A condition matches only if the entire condition
phrase appears as a substring of the context (case-insensitive).

Rationale: token-set overlap produced false-positive matches (e.g.,
"AI agent systems" vs "embedded systems" share the token "systems").
Phrase-substring is stricter and respects the conservative-evaluation
requirement.

## 6. Relationship semantics

### Directionality is preserved

PROVIDES is from provider → capability (directed). A reverse edge
(capability PROVIDES provider) is NOT a provider relationship. The
`_find_capability_providers` function queries for incoming edges to the
capability entity with the PROVIDER_PREDICATES — directionality is
encoded in the SQL query, not just in post-filtering.

### INTEGRATES_WITH is NOT collapsed into ALTERNATIVE_TO

Per the G04 plan §3 (carry-forward correction) and mission briefing §3,
`INTEGRATES_WITH` is undirected and semantically distinct from
`REPLACES` / `ALTERNATIVE_TO`. The gap analyzer treats `INTEGRATES_WITH`
separately — it is NOT in the PROVIDER_PREDICATES, DEPENDENCY_PREDICATES,
or LIMITATION_PREDICATES lists. A capability provided via
`INTEGRATES_WITH` is NOT counted as a provider relationship for gap
analysis purposes.

### A dependency is NOT an available implementation

A REQUIRES / DEPENDS_ON edge documents that the provider needs the
to-entity. It does NOT mean the to-entity is available. The gap analyzer's
`unsatisfied_prerequisites` view surfaces dependencies that point to
capabilities with no documented provider — these are evidence gaps, not
proven product deficiencies.

## 7. Evidence and provenance handling

### Evidence bundle construction

For each capability detail, the registry collects evidence fragments and
source spans from:
- The PROVIDES / ENABLES / PRODUCES relationship's `evidence_refs` field
- The ClaimRow records with `subject_ref` or `object_ref` = capability_id
- The claim's `evidence_refs` and `contradicting_refs` fields

Each fragment is bundled with its source spans (precise char offsets +
excerpt + context_before/after) — the same shape as G04-T01's evidence
bundles.

### Citation chain (fully traversable)

For each SUPPORTED / CONTESTED / PARTIALLY_SUPPORTED classification, the
result includes an `evidence_chain` list of `EvidenceChainLink` dicts:

```
Requirement → Capability entity → Provider relationship (PROVIDES edge)
           → Claim (subject_ref = capability_id)
           → EvidenceFragment (from claim.evidence_refs)
           → SourceSpan (precise offsets)
           → source_uri (original source URL)
```

The chain is traversable from the answer back to the exact source text
at the exact character offset. The gap analyzer NEVER fabricates a
fragment, span, or source_uri.

### Verification assessment lookup

The registry and analyzer reuse the G03-T04 verification engine's
`assess_claim()` audit events. The `_get_latest_assessment` helper
queries `AuditEventRow` for the most recent
`event_type="verification.assessed"` event for a given claim_id. The
outcome (SOURCE_SUPPORTED, CORROBORATED, CONTESTED, INSUFFICIENT_EVIDENCE,
STALE_OR_CONTEXT_MISMATCH, NOT_EVIDENCED) drives the classification.

The deterministic-v2 policy is preserved unchanged:
- VERIFIED remains unreachable
- Multiple URLs do NOT imply independent origins
- CONTESTED is preserved, not suppressed
- No auto-promotion of HYPOTHESIZED to VERIFIED

## 8. API contracts

### New endpoints (3, all read-only)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/knowledge/capabilities` | List documented capabilities (filter by `name_contains`) |
| GET | `/api/v1/knowledge/capabilities/{capability_id}` | Full detail: providers, dependencies, limitations, claims, evidence |
| POST | `/api/v1/knowledge/capabilities/analyze-gap` | Analyze coverage against required capabilities |

### Why `/knowledge/capabilities` and NOT `/capabilities`

The G01 endpoint `GET /api/v1/capabilities` lists Synapse SYSTEM
capabilities (from the `CapabilityRow` table — the platform's own
function/capability registry, e.g., arxiv_search, trafilatura_extractor,
content_delta_hash). That is a DIFFERENT concept from the knowledge-graph
capabilities (EntityRow kind=capability).

Namespacing the new endpoints under `/knowledge` makes the distinction
explicit and avoids collision. The existing G01 endpoint is preserved
unchanged.

### Request / response shapes

#### POST /api/v1/knowledge/capabilities/analyze-gap

Request:
```json
{
  "required_capabilities": ["source discovery", "content extraction", "..."],
  "context": "AI agent systems arxiv papers",
  "candidate_entity_ids": ["ent-synapse"],
  "limit": 20
}
```

Response (Envelope-wrapped):
```json
{
  "data": {
    "requirements": [
      {
        "required_capability": "source discovery",
        "capability_id": "cap-source-discovery",
        "match_quality": "exact",
        "classification": "supported",
        "reason": "1 assessed claim(s) with positive outcome...",
        "candidates": [{"provider_canonical_name": "synapse", "predicate": "PROVIDES", ...}],
        "evidence_chain": [{"fragment_id": "frag-a", "source_uri": "...", "spans": [...]}],
        "contradicting_evidence": [],
        "limitations": [],
        "unsatisfied_prerequisites": [],
        "applicability_match": true
      }
    ],
    "summary": {"supported": 2, "contested": 1, "not_evidenced": 1, "partially_supported": 1},
    "gap_views": {
      "missing_evidence": ["structured knowledge extraction"],
      "partially_covered_requirements": ["dependency analysis"],
      "contradictory_evidence": ["evidence verification"],
      "unsatisfied_prerequisites": [],
      "explicit_incompatibilities": ["evidence verification: limited by no vector database"]
    },
    "unknowns": [],
    "limits": {"limit": 20, "max_required_capabilities": 50, "max_candidates": 20},
    "policy_version": "deterministic-v2",
    "context": "AI agent systems arxiv papers",
    "candidates": ["ent-synapse"]
  },
  "meta": {"request_id": "...", "api_version": "v1", "pagination": {...}}
}
```

## 9. Realistic demonstration

### Scenario (mission briefing §6)

> "Build an AI research assistant that retrieves technical sources,
>  extracts useful knowledge, verifies evidence, and identifies
>  technical dependencies."

### Required capabilities (5)

1. source discovery
2. content extraction
3. structured knowledge extraction
4. evidence verification
5. dependency analysis

### Candidate: `ent-synapse` (the AgentCraft Synapse tool entity)

### Setup

The fixture models:
- 1 tool entity (synapse)
- 3 technology entities (arxiv, trafilatura, RelationshipService)
- 1 constraint entity (no vector database)
- 6 capability entities (the 5 required + 1 for lexical-similarity testing)
- 4 evidence fragments (1 stale, 1 opposing)
- 4 claims (1 disputed with contradicting_refs)
- 3 source spans with precise offsets
- 9 typed relationships (PROVIDES, REQUIRES, LIMITS, CONTRADICTS-via-claim)

Each claim is run through `assess_claim()` so the verification engine
produces assessment records that drive the classification.

### Output (realistic demonstration script: `scripts/demo_g04_t02_gap_analysis.py`)

```
=== Gap analysis summary ===
{
  "supported": 2,
  "partially_supported": 1,
  "not_evidenced": 1,
  "contested": 1,
  "constrained": 0,
  "unknown": 0
}

=== Per-requirement assessments ===

  Requirement: source discovery
    match_quality: exact
    classification: supported
    reason: 1 assessed claim(s) with positive outcome (SOURCE_SUPPORTED
            or CORROBORATED), no contradicting evidence, applicability
            matches context
    candidates: 1 (synapse PROVIDES, has_evidence=True)
    evidence_chain: 1 link (fragment=frag-a, source_uri=https://example.com/papers/synapse)
      spans: [0:33] "Synapse retrieves technical sources"
             [80:99] "extracts knowledge"

  Requirement: content extraction
    match_quality: exact
    classification: supported
    reason: 1 assessed claim(s) with positive outcome...
    candidates: 1 (synapse PROVIDES, has_evidence=True)
    evidence_chain: 1 link (frag-a)

  Requirement: structured knowledge extraction
    match_quality: exact
    classification: not_evidenced
    reason: provider has a PROVIDES edge but no evidence-backed claim;
            no sufficient supporting evidence was found
    candidates: 1 (synapse PROVIDES, has_evidence=False)
    evidence_chain: 0 links

  Requirement: evidence verification
    match_quality: exact
    classification: contested
    reason: supporting and contradicting evidence present; both sides
            preserved, neither suppressed
    candidates: 1 (synapse PROVIDES, has_evidence=True)
    limitations: 1 (no vector database LIMITS)
    contradicting_evidence: ['frag-opp']
    evidence_chain: 2 links (frag-a + frag-opp)

  Requirement: dependency analysis
    match_quality: exact
    classification: partially_supported
    reason: provider has evidence-backed PROVIDES edge but no assessed
            claim (the only claim cites stale fragment frag-c, retrieved
            500 days ago)
    candidates: 1 (synapse PROVIDES, has_evidence=True)
    evidence_chain: 2 links (frag-a + frag-c with stale span [20:50])

=== CITATION CHAIN EXAMPLE (source discovery = SUPPORTED) ===

  Required capability: source discovery
    capability_id: cap-source-discovery
    classification: supported
    reason: 1 assessed claim(s) with positive outcome...

  Citation chain:
    Link 1:
      fragment_id: frag-a
      source_uri: https://example.com/papers/synapse
      retrieved_at: 2026-09-29T02:30:57+00:00
      span: id=span-sd  claim_id=claim-source-discovery
             offsets=[0, 33)
             excerpt: "Synapse retrieves technical sources"
      span: id=span-ce  claim_id=claim-content-extraction
             offsets=[80, 99)
             excerpt: "extracts knowledge"

  Provider relationship:
    relationship_id: f3cf05e644164d69849aa8c5fd2f8d76
    predicate: PROVIDES
    provider: synapse (kind=tool)
    provider_id: ent-synapse
```

### Demonstration summary

The demonstration shows:
- ✅ Relevant capabilities are retrieved (5 of 5 required)
- ✅ SUPPORTED classification requires applicable evidence (source discovery, content extraction)
- ✅ NOT_EVIDENCED when no evidence exists (structured knowledge extraction)
- ✅ CONTESTED preserves contradictions (evidence verification, with contradicting_evidence=['frag-opp'])
- ✅ PARTIALLY_SUPPORTED when evidence is incomplete (dependency analysis with stale fragment)
- ✅ Documented limitations visible (LIMITS edge from "no vector database")
- ✅ Citation chain fully traversable: Result → Claim → Fragment → Span → source_uri
- ✅ Directionality preserved (PROVIDES is provider → capability)
- ✅ INTEGRATES_WITH not collapsed into ALTERNATIVE_TO
- ✅ Context-dependent support is conservative (phrase-substring matching)

## 10. Test results

### Gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed (113 files) |
| `ruff format --check src tests scripts` | ✅ 113 files formatted |
| `pytest` (deterministic) | ✅ 379 passed, 0 failed, 3 skipped (live) |

### Baseline vs. after-G04-T02

| Metric | Baseline (535267c) | After G04-T02 | Delta |
|--------|------------------:|--------------:|------:|
| Deterministic tests passed | 359 | 379 | +20 |
| Tests failed | 0 | 0 | 0 |
| Tests skipped (live) | 3 | 3 | 0 |
| Files (ruff check) | 109 | 113 | +4 |
| Files (ruff format) | 109 | 113 | +4 |

### G04-T02 acceptance criteria (16 mandatory + 4 API integration)

Per mission briefing §8 (MANDATORY ACCEPTANCE TESTS):

| # | Acceptance criterion | Status | Test |
|---|----------------------|--------|------|
| 1 | Capabilities enumerated without duplicate database | ✅ PASS | `test_capabilities_enumerated_without_duplicate_db` |
| 2 | One capability, multiple providers | ✅ PASS | `test_one_capability_multiple_providers` |
| 3 | One provider, multiple capabilities | ✅ PASS | `test_one_provider_multiple_capabilities` |
| 4 | SUPPORTED requires applicable evidence | ✅ PASS | `test_supported_requires_applicable_evidence` |
| 5 | Lexical similarity alone ≠ SUPPORTED | ✅ PASS | `test_lexical_similarity_alone_not_supported` |
| 6 | Unknown capabilities remain NOT_EVIDENCED | ✅ PASS | `test_unknown_capability_remains_not_evidenced` |
| 7 | Partial coverage ≠ complete | ✅ PASS | `test_partial_coverage_not_reported_complete` |
| 8 | Explicit dependencies preserved | ✅ PASS | `test_explicit_dependencies_preserved` |
| 9 | Incompatibilities/constraints visible | ✅ PASS | `test_incompatibilities_constraints_visible` |
| 10 | Contradictory evidence not suppressed | ✅ PASS | `test_contradictory_evidence_not_suppressed` |
| 11 | Context-dependent support conservative | ✅ PASS | `test_context_dependent_conservative` |
| 12 | Directionality respected | ✅ PASS | `test_relationship_directionality_respected` |
| 13 | Evidence refs resolve to valid provenance | ✅ PASS | `test_evidence_refs_resolve_to_valid_provenance` |
| 14 | Graph traversal + output bounded | ✅ PASS | `test_graph_traversal_and_output_bounded` |
| 15 | Repeated analyses deterministic | ✅ PASS | `test_repeated_analyses_deterministic` |
| 16 | G01-G04-T01 regression | ✅ PASS | `test_g01_g04_t01_regression` (subprocess runs 6 selected tests) |

### API integration tests

| Test | Result |
|------|--------|
| `test_api_capability_endpoints_require_auth` (401 without auth) | ✅ PASS |
| `test_api_capability_endpoints_with_auth` (200 with auth, structured response) | ✅ PASS |
| `test_api_openapi_includes_capability_registry` (OpenAPI schema) | ✅ PASS |

### Realistic demonstration test

| Test | Result |
|------|--------|
| `test_realistic_demonstration_ai_research_assistant` (5-capability scenario) | ✅ PASS |

## 11. Known limitations

### Architectural limitations (carried forward)

1. **No semantic search** — only lexical (ILIKE) + structured + graph.
   Vector database is deferred to a later group.
2. **No LLM-based reasoning** — the gap analyzer is a deterministic
   decision tree. LLM reasoning is deferred to G05.
3. **No autonomous architecture generation** — the gap analyzer does NOT
   propose solutions; it only classifies what is supported. Innovation
   generation is deferred to G05.

### Gap-analysis-specific limitations

4. **Conservative applicability matching** — phrase-substring matching
   is intentionally strict. A condition matches only if the entire
   condition phrase appears as a substring of the context. This may
   miss legitimate matches when the user's context uses synonyms or
   paraphrases. The trade-off favors conservative classification over
   false-positive SUPPORTED.

5. **Unsatisfied-prerequisites detection is shallow** — only one level
   of dependency is checked. If candidate A requires B, and B requires C,
   and C has no provider, the gap analyzer does NOT surface C as an
   unsatisfied prerequisite of A. This is a deliberate scope decision;
   deeper transitive-dependency analysis is deferred to G04-T03 reasoning.

6. **Fuzzy match quality does not affect classification** — a fuzzy
   ILIKE match resolves the capability entity but does NOT promote the
   classification. The `match_quality="fuzzy"` field is informational;
   the classification still requires evidence. This is by design per
   mission briefing §5 ("Lexical retrieval may suggest candidates, but
   must not by itself establish SUPPORTED status").

7. **No claimant inference** — if a claim's `subject_ref` is missing
   or doesn't match the capability's provider entity, the claim is still
   collected for the capability (via `subject_ref` OR `object_ref` match).
   The gap analyzer does NOT verify that the claim's subject IS the
   candidate provider. This is a deliberate scope decision — deeper
   claim-provenance analysis is deferred to G04-T03.

### API limitations

8. **Single new endpoint cluster** — only the `/knowledge/capabilities`
   cluster is added. The existing `/api/v1/capabilities` (G01 system
   capabilities) is preserved unchanged. `POST /api/v1/reasoning/queries`
   remains a 501 placeholder (will be replaced by G04-T03 reasoning, not
   authorized).

9. **No streaming** — endpoints return a single JSON response. SSE /
   streaming is deferred to a later group.

## 12. Existing and newly identified blockers

The full blocker register from ADR-0011 carries forward UNCHANGED.
G04-T02 does NOT resolve any of them and does NOT introduce new ones.

### Carried-forward blockers (unchanged)

| # | Blocker | Origin | Severity | Status after G04-T02 |
|---|---------|--------|----------|----------------------|
| PRB-01 | Real PostgreSQL migration/integration validation | G02 | High | Unchanged — G04-T02 uses SQLite in tests, no new migrations |
| PRB-02 | Pinned-IP HTTPS, proxy, IPv6, connection-pooling security review | G02 closure | Medium | Unchanged — G04-T02 has no new HTTP/transport code |
| PRB-03 | Database-level concurrency/idempotency guarantees | G03-T02 | Medium | Unchanged — G04-T02 is read-only |
| PRB-04 | Evidence-origin independence limitations | G03-T04 | Medium | Unchanged — gap analyzer inherits the conservative `assess_claim()` default |
| PRB-05 | Stronger VERIFIED review policy | G03-T04 | Expected | Unchanged — VERIFIED remains unreachable; gap analyzer never auto-promotes |
| PRB-06 | Toolkit read-only credential enforcement | ADR-0009 §7 | Medium | Unchanged — G04-T02 does not touch the Toolkit |
| PRB-07 | Extractor-version-aware reprocessing | G03-T02 | Low | Unchanged — G04-T02 does not modify the extraction pipeline |

### New blockers introduced by G04-T02

**None.** G04-T02 introduces no new production-readiness blockers.

### Observations from the closure review (carried forward, not new)

The G04-T01 closure review identified that the active Synapse developer
token has `repo` + `workflow` scopes (write-capable for AgentCraft-Toolkit).
This is PRB-06, already documented. G04-T02 did not access the Toolkit
and did not introduce any new credential concerns.

## 13. Frozen boundaries respected (verified in this implementation)

| Boundary | Rule | Status |
|----------|------|--------|
| G01 domain contracts | Frozen — no Pydantic / enum / invariant changes | ✅ No changes to `domain/*.py` |
| G02 provider scope | Frozen — 3 providers only | ✅ No new providers |
| G03 scope | Frozen — no expansion | ✅ No G03 file modified |
| Verification policy | deterministic-v2 — VERIFIED unreachable | ✅ Policy version unchanged |
| G04-T01 retrieval | No broad changes | ✅ retrieval.py and api/v1/retrieval.py unchanged |
| Graph database | NOT introduced | ✅ Existing RelationshipService + bounded SQL |
| Vector database | NOT introduced | ✅ Lexical + structured + graph only |
| AgentCraft-Toolkit | READ ONLY | ✅ No Toolkit access in this session |
| No new migrations | Confirmed | ✅ migrations 0001–0004 unchanged |
| No new pip deps | Confirmed | ✅ `pyproject.toml` unchanged |
| No LLM integration | Confirmed | ✅ Deterministic decision tree only |
| No agent swarm | Confirmed | ✅ Single function `analyze_gap()` |
| No frontend | Confirmed | ✅ No frontend code, only API |
| No 501 placeholder replaced | Confirmed | ✅ `/reasoning/queries` remains 501 (G04-T03 not authorized) |

## 14. Final report format

```
STATUS: PASS WITH LIMITATIONS

Repository: https://github.com/mayakilzy/AgentCraft_Synapse.git (verified)
Branch: main
Starting checkpoint SHA: 535267cfc6a27e3ee8ee9275dd0844339d1b17b1
Final commit SHA: a4f75fd150eab1d4cc832ba9fa24bd1ca1109002

Remote synchronization: PASS (verified via `git ls-remote` after push)

Files changed: 6
  - 5 new files (3250 insertions)
    - src/synapse/application/capability_registry.py (635 LOC)
    - src/synapse/application/gap_analyzer.py (818 LOC)
    - src/synapse/api/v1/capability_registry.py (187 LOC)
    - tests/integration/test_g04_t02_capability_registry.py (1067 LOC)
    - scripts/demo_g04_t02_gap_analysis.py (538 LOC)
  - 1 modified file
    - src/synapse/api/v1/router.py (+5/-1)

LOC: +3250 / -1 (net +3249 across 6 files)

Tests:
  - passed: 379 (359 baseline + 20 new G04-T02)
  - failed: 0
  - skipped: 3 (live tests; unchanged from baseline)

G04-T02 acceptance criteria (16 mandatory + 1 demo + 3 API):
  1.  Capabilities enumerated without duplicate database — PASS
  2.  One capability, multiple providers — PASS
  3.  One provider, multiple capabilities — PASS
  4.  SUPPORTED requires applicable evidence — PASS
  5.  Lexical similarity alone ≠ SUPPORTED — PASS
  6.  Unknown capabilities remain NOT_EVIDENCED — PASS
  7.  Partial coverage ≠ complete — PASS
  8.  Explicit dependencies preserved — PASS
  9.  Incompatibilities/constraints visible — PASS
  10. Contradictory evidence not suppressed — PASS
  11. Context-dependent support conservative — PASS
  12. Directionality respected — PASS
  13. Evidence refs resolve to valid provenance — PASS
  14. Graph traversal + output bounded — PASS
  15. Repeated analyses deterministic — PASS
  16. G01-G04-T01 regression — PASS
  Demo. Realistic AI-research-assistant demonstration — PASS
  API1. Auth required without credentials — PASS
  API2. Structured response with auth — PASS
  API3. OpenAPI schema includes new endpoints — PASS

Demonstration summary:
  Scenario: "Build an AI research assistant that retrieves technical
            sources, extracts useful knowledge, verifies evidence, and
            identifies technical dependencies."
  Required capabilities (5): source discovery, content extraction,
    structured knowledge extraction, evidence verification, dependency analysis
  Candidate: ent-synapse
  Results:
    - source discovery: SUPPORTED (positive evidence + applicability match)
    - content extraction: SUPPORTED (positive evidence + arxiv papers context)
    - structured knowledge extraction: NOT_EVIDENCED (PROVIDES but no evidence)
    - evidence verification: CONTESTED (positive + contradicting evidence)
    - dependency analysis: PARTIALLY_SUPPORTED (stale evidence)
  Citation chain: Result → Claim(claim-source-discovery) →
                  EvidenceFragment(frag-a) → SourceSpan(2 spans: [0,33), [80,99))
                  → Source URI: https://example.com/papers/synapse
  Limitations visible: LIMITS edge from "no vector database" to evidence verification
  Contradictions preserved: contradicting_evidence=['frag-opp'] (not suppressed)

Remaining limitations:
  - No semantic search (vector DB deferred)
  - No LLM reasoning (deferred to G05)
  - Conservative applicability matching (phrase-substring; may miss synonyms)
  - Shallow unsatisfied-prerequisites detection (no transitive deps)
  - Fuzzy match does not affect classification (informational only)
  - No claimant inference (claim subject vs candidate provider not verified)
  - PRB-01..07 unchanged from ADR-0011; no new blockers introduced

Next task: G04-T03 — Grounded reasoning — NOT AUTHORIZED.
```

## 15. STOP

Per mission briefing §13 and the G04 plan §10:

- ✅ G04-T02 implemented, tested, committed, pushed.
- ❌ G04-T03 NOT STARTED. (Not authorized.)
- ❌ G04-T04 NOT STARTED. (Not authorized.)
- ❌ G04-T05 NOT STARTED. (Not authorized.)
- ❌ AgentCraft-Toolkit NOT MODIFIED. (READ-ONLY boundary preserved.)

The Synapse implementation continues efficiently, safely, and without
losing any previously completed work.

---

*End of G04-T02 Implementation Report — STOP.*
