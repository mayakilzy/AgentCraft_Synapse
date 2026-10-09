# G04 Minimal Implementation Plan — Hybrid Retrieval & Evidence-Grounded Reasoning

> **Status**: Preparation only — NOT authorized for production implementation.
> Awaiting explicit user approval per the user's "G03 Closure & G04 Preparation" message.

## 1. Executive summary

G04 builds a retrieval and reasoning layer on top of the G03 knowledge
graph. It answers capability, dependency, alternative, tradeoff and
contradiction questions with **inspectable evidence** — not raw-document
summaries. Every answer cites specific claims, relationships, source spans,
and original source URIs.

**Core principle**: *"Evidence before confidence; no fabricated citations;
absence of evidence is not evidence of absence."*

**Architecture**: modular monolith; PostgreSQL + RelationshipService; no
graph database; no vector database unless a measured retrieval gap
justifies it. Compact implementation reusing G01–G03 contracts.

## 2. Integration with existing G01–G03 contracts

### Reused components

| Component | From | What G04 uses it for |
|-----------|------|---------------------|
| `EntityRow`, `ClaimRow`, `RelationshipRow` | G03-T01/T02/T03 | Knowledge graph nodes + edges |
| `EvidenceFragmentRow`, `SourceSpanRow` | G02/G03-T01 | Evidence text + precise offsets |
| `SourceRow` | G01 | Source provenance (canonical_uri, publisher, author) |
| `RelationshipService` | G03-T03 | Graph traversal, capabilities, dependencies, contradictions |
| `assess_claim()` / `VerificationOutcome` | G03-T04 | Verification assessment of retrieved claims |
| `POST /api/v1/knowledge/search` | G03-T05 | Entity search (lexical baseline) |
| `POST /api/v1/reasoning/queries` (501) | G01 placeholder | Replaced by G04 reasoning endpoint |
| `JobRow` | G01 | Async reasoning jobs (202 + polling) |
| `PrincipalDep`, `DbSessionDep`, `RequestIDDep` | G01 | Auth + DB session + request tracing |
| Error envelope (`application/problem+json`) | G01 | Standard error responses |

### New components (G04 only)

| Component | Purpose |
|-----------|---------|
| `src/synapse/application/retrieval.py` | Hybrid retrieval: lexical + metadata + graph traversal + reranking |
| `src/synapse/application/reasoning.py` | Grounded reasoning: compose answers from retrieved evidence |
| `src/synapse/api/v1/reasoning.py` | `POST /api/v1/reasoning/queries` endpoint (202 + job pattern) |
| `tests/integration/test_g04_*.py` | G04 test suites |

## 3. Proposed tasks (5 tasks, each independently reviewable)

### G04-T01 — Hybrid retrieval

**Objective**: Implement lexical + metadata + relationship retrieval with
evidence-aware reranking. No vector database unless a measured gap
justifies it.

**Implementation scope**:
1. **Lexical search**: SQL `LIKE` / `ILIKE` on `EntityRow.canonical_name`,
   `ClaimRow.proposition`, `EvidenceFragmentRow.exact_excerpt`. This is the
   baseline — already partially implemented in `POST /knowledge/search`.
2. **Metadata filtering**: filter by `EntityRow.kind`, `ClaimRow.epistemic_state`,
   `RelationshipRow.predicate`, `RelationshipRow.origin`,
   `RelationshipRow.verification_state`, `SourceRow.source_type`,
   `EvidenceFragmentRow.retrieved_at` (freshness).
3. **Relationship graph expansion**: given a seed entity, traverse the
   relationships table via `RelationshipService.find_related_entities()`
   and `find_paths()` to discover connected knowledge (capabilities,
   dependencies, alternatives, limitations).
4. **Evidence bundling**: for each retrieved claim/relationship, fetch
   `EvidenceFragmentRow` + `SourceSpanRow` so the caller can cite the
   exact source text.
5. **Reranking**: rank results by (a) verification outcome
   (VERIFIED > CORROBORATED > SOURCE_SUPPORTED > HYPOTHESIZED > CONTESTED),
   (b) freshness (`retrieved_at` recency), (c) source independence
   (distinct `source_uri` count), (d) relationship proximity (shorter
   path = higher rank).

**Existing components reused**: `EntityRow`, `ClaimRow`, `RelationshipRow`,
`EvidenceFragmentRow`, `SourceSpanRow`, `RelationshipService`, `assess_claim()`.

**Expected files**: `src/synapse/application/retrieval.py` (~250 LOC),
`tests/integration/test_g04_t01_retrieval.py` (~200 LOC).

**Acceptance tests**:
1. Golden queries recover expected claims and constraints.
2. Stable pagination (cursor-based, deterministic ordering).
3. Empty results are truthful (no fabricated hits).
4. Reranking respects verification outcomes (VERIFIED > SOURCE_SUPPORTED).
5. Reranking respects freshness (recent > stale).
6. Evidence bundle includes source spans with correct offsets.
7. G01/G02/G03 regression.

**Dependencies**: G03-T01–T05 (all complete).

**Exclusions**: No vector embeddings, no pgvector, no semantic search.
Deferred to a later group if a measured retrieval gap justifies it.

---

### G04-T02 — Capability registry and gap analysis

**Objective**: Provide structured queries over capabilities, requirements,
alternatives, tradeoffs, and missing capabilities — building on the
RelationshipService from G03-T03.

**Implementation scope**:
1. **Capability registry**: use the existing `EntityRow(kind="capability")`
   + `RelationshipRow(predicate="PROVIDES"/"ENABLES")` to build a
   capability view. No new table — it's a query view over existing data.
2. **Alternative finder**: given an entity, find entities connected via
   `REPLACES` or `INTEGRATES_WITH` (NOT treating INTEGRATES_WITH as
   ALTERNATIVE_TO — per the carry-forward correction).
3. **Tradeoff analysis**: find `LIMITS` + `IMPROVES` relationships for an
   entity; present as tradeoff pairs.
4. **Missing-capability detection**: use `find_missing_capabilities()`
   from G03-T03; report as `NOT_EVIDENCED` (not as negative evidence).
5. **Contradiction surfacing**: use `find_contradictions()` from G03-T03.

**Existing components reused**: `RelationshipService.find_capabilities()`,
`find_dependencies()`, `find_alternatives()`, `find_limitations()`,
`find_missing_capabilities()`, `find_contradictions()`.

**Expected files**: `src/synapse/application/capability_registry.py` (~150 LOC),
`tests/integration/test_g04_t02_capability_registry.py` (~150 LOC).

**Acceptance tests**:
1. Find alternative tool by capability.
2. Identify missing project capability with evidence (NOT_EVIDENCED).
3. INTEGRATES_WITH is NOT reported as ALTERNATIVE_TO.
4. Contradictions are surfaced with both sides visible.
5. G01/G02/G03 regression.

**Dependencies**: G04-T01 (retrieval).

**Exclusions**: No CRUD endpoints for capability registration (read-only
in G04). No LLM-based capability inference.

---

### G04-T03 — Grounded reasoning

**Objective**: Compose answers from retrieved claims, paths, conflicts,
and conditions. Cite exact evidence IDs. Isolate prompt injection in
untrusted source text.

**Implementation scope**:
1. **Query understanding**: classify the query intent
   (CAPABILITY_QUERY, DEPENDENCY_QUERY, ALTERNATIVE_QUERY,
   TRADEOFF_QUERY, CONTRADICTION_QUERY, FACTUAL_QUERY, UNKNOWN).
   Deterministic classification via keyword matching + predicate
   vocabulary — no LLM in the minimal slice.
2. **Reasoning workflow**: parse objective → retrieve claims + paths →
   identify capabilities and gaps → compose answer → surface unknowns.
3. **Answer composition**: structured answer with:
   - `answer_text`: human-readable summary (deterministic, not LLM-generated)
   - `cited_claims`: list of ClaimRow IDs
   - `cited_relationships`: list of RelationshipRow IDs
   - `evidence_chain`: list of (claim → evidence_fragment → source_span → source_uri)
   - `verification_outcomes`: assessment per cited claim
   - `unknowns`: what the system could NOT find (knowledge gaps)
   - `contradictions`: any CONTESTED claims in the answer
4. **Prompt-injection isolation**: evidence text is always treated as
   untrusted content (never as instructions). Answers cite evidence; they
   do not execute it.
5. **No fabricated citations**: every cited claim/relationship MUST exist
   in the DB. If a claim cannot be found, the answer says "no evidence
   found" — never fabricates.

**Existing components reused**: `retrieval.py` (G04-T01),
`RelationshipService` (G03-T03), `assess_claim()` (G03-T04),
`POST /api/v1/reasoning/queries` (G01 501 placeholder, replaced).

**Expected files**: `src/synapse/application/reasoning.py` (~300 LOC),
`src/synapse/api/v1/reasoning.py` (~120 LOC),
`tests/integration/test_g04_t03_reasoning.py` (~250 LOC).

**Acceptance tests**:
1. Missing evidence leads to uncertainty (answer says "no evidence found").
2. No fabricated citations (every cited ID exists in the DB).
3. Injection fixture (source text containing "ignore previous instructions")
   cannot alter the answer or system behavior.
4. Answer includes evidence chain: claim → fragment → span → source URI.
5. Contradictions are surfaced (both sides visible).
6. G01/G02/G03 regression.

**Dependencies**: G04-T01 (retrieval), G04-T02 (capability registry).

**Exclusions**: No LLM-based reasoning. No agent orchestration. No
multi-step planning. Deterministic answer composition only.

---

### G04-T04 — Evaluation and cost routing

**Objective**: Compare lexical-only vs hybrid retrieval; record latency,
tokens/cost, and trace failures. Apply cheap-first model selection.

**Implementation scope**:
1. **Evaluation harness**: a fixed set of golden queries with expected
   results. Run retrieval (lexical-only vs hybrid) and measure:
   - Precision@K (did the expected claim appear in top-K?)
   - Recall (did ALL expected claims appear?)
   - Latency (p50/p95)
   - Evidence grounding (are all cited claims real?)
   - Unsupported-claim rate (any fabricated citations?)
2. **Cost routing**: since G04-T01–T03 are deterministic (no LLM),
   cost is zero. The cost-routing framework is a stub that records
   `cost_estimate=0.0` and `provider="deterministic-v1"`. When a
   future group adds LLM-based reasoning, the framework routes to
   the cheapest sufficient provider.
3. **Baseline report**: a Markdown report with the eval results.

**Expected files**: `tests/eval/g04_eval_harness.py` (~200 LOC),
`reports/G04_EVAL_BASELINE.md` (~auto-generated).

**Acceptance tests**:
1. Fixed query set metrics reported (precision, recall, latency).
2. No unsupported claims (zero fabricated citations across all queries).
3. Budget enforced (cost_estimate = 0.0 for deterministic; future LLM
   calls would be capped).
4. Provider timeout yields structured failure (not a crash).
5. G01/G02/G03 regression.

**Dependencies**: G04-T01–T03.

**Exclusions**: No LLM provider integration. No real API key usage.
No external network calls in CI.

---

### G04-T05 — External client compatibility

**Objective**: Publish sample external-client calls; verify UI-agnostic API;
verify CORS.

**Implementation scope**:
1. **OpenAPI verification**: ensure the OpenAPI 3.1 schema at
   `/api/v1/openapi.json` is complete and self-consistent for all G04
   endpoints.
2. **Sample client**: a standalone Python script (`examples/client_g04.py`)
   that uses `httpx` to call every G04 endpoint — proving the API is
   usable by an external client that imports nothing from `synapse`.
3. **CORS test**: verify allowed origins receive CORS headers; disallowed
   origins do not.
4. **Contract tests**: verify request/response shapes match the OpenAPI
   schema.

**Expected files**: `examples/client_g04.py` (~100 LOC),
`tests/integration/test_g04_t05_external_client.py` (~100 LOC).

**Acceptance tests**:
1. External client uses only the API (no internal DB/Python imports).
2. All G04 endpoints return 200 with valid auth.
3. OpenAPI schema validates.
4. CORS allowlist works.
5. G01/G02/G03 regression.

**Dependencies**: G04-T01–T04.

**Exclusions**: No SDK generation. No GraphQL. No WebSocket.

## 4. Evidence-aware ranking

The retrieval reranking respects:

| Factor | How measured | Weight |
|--------|-------------|--------|
| Verification outcome | `assess_claim()` result | VERIFIED (unreachable in v2) > CORROBORATED > SOURCE_SUPPORTED > HYPOTHESIZED > CONTESTED |
| Freshness | `EvidenceFragmentRow.retrieved_at` | Recent > stale (>365 days = STALE flag) |
| Source independence | distinct `source_uri` count | More distinct sources → higher rank (but does NOT imply independence) |
| Relationship proximity | path length from seed entity | Shorter path → higher rank |
| Applicability match | `ClaimRow.validity_conditions` vs query context | Matching conditions → higher rank |

## 5. Separation of knowledge categories

| Category | What it is | How G04 represents it |
|----------|-----------|---------------------|
| **Retrieved facts** | Claims with evidence from sources | `ClaimRow(epistemic_state=supported)` + evidence bundle |
| **Derived conclusions** | Relationships inferred from explicit knowledge | `RelationshipRow(origin=derived, verification_state=unverified)` — clearly labeled |
| **Hypotheses** | Proposed explanations without evidence | `ClaimRow(epistemic_state=hypothesized)` — never cited as fact |
| **Unknowns** | What the system could NOT find | `unknowns` field in the answer — knowledge gaps |
| **Contradictions** | Conflicting claims/relationships | `contradictions` field — both sides visible |

## 6. Citation chains

Every answer includes a citation chain:

```
answer → cited_claim_id → evidence_fragment_id → source_span → source_uri
```

The chain is traversable: a reviewer can follow from the answer text back
to the exact source text at the exact character offset.

## 7. Missing-information and knowledge-gap detection

- If no claims match the query → `unknowns: ["no evidence found for query X"]`
- If a capability is missing → `NOT_EVIDENCED` (not negative evidence)
- If a relationship path is incomplete → `unknowns: ["no path from A to B within depth N"]`
- If a claim is contested → both sides visible; no auto-resolution

**Absence of evidence is NOT evidence of absence.** Unknowns are reported
explicitly — never silently filled with fabricated claims.

## 8. Deferred features (G05/G06)

| Feature | Deferred to | Reason |
|---------|------------|--------|
| LLM-based reasoning | G05 (Innovation) | G04 is deterministic; LLM reasoning requires cost/cost-routing infrastructure |
| Innovation generation | G05 | New idea synthesis is out of G04 scope |
| Experiment design | G05 | Experiment proposals require G04's reasoning output |
| Future scenarios (F0–F4) | G06 | Future intelligence requires G04's knowledge base |
| Vector embeddings / semantic search | Later group (if measured gap) | Lexical + metadata + graph retrieval is sufficient for the minimal slice |
| Multi-agent orchestration | Future group | No agent swarm in G04 |
| Real-time streaming (SSE) | Future group | Polling is sufficient for G04's job pattern |

## 9. Proposed G04-T01 scope and acceptance criteria

### Scope

Implement `src/synapse/application/retrieval.py` with:

```python
async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    *,
    entity_kind: str | None = None,
    predicate: str | None = None,
    epistemic_state: str | None = None,
    max_depth: int = 3,
    limit: int = 20,
) -> RetrievalResult:
    """Hybrid retrieval: lexical + metadata + graph + reranking."""
```

Returns:
```python
class RetrievalResult:
    claims: list[ClaimWithEvidence]    # claims + evidence fragments + spans
    relationships: list[RelationshipWithEvidence]
    entities: list[EntitySummary]
    reranking_factors: dict[str, Any]  # verification, freshness, proximity
    unknowns: list[str]                # what was NOT found
```

### Acceptance criteria

1. Golden queries recover expected claims (precision ≥ 0.8 on fixture set)
2. Stable pagination (deterministic ordering by verification + freshness)
3. Empty results are truthful (no fabricated hits)
4. Reranking respects verification outcomes
5. Evidence bundle includes source spans with correct offsets
6. G01/G02/G03 regression (all 338 tests pass)

### Estimated footprint

| Dimension | Estimate |
|-----------|----------|
| New source files | 1 (`retrieval.py`) |
| New test files | 1 (`test_g04_t01_retrieval.py`) |
| New LOC | ~450 (source + tests) |
| New pip deps | 0 |
| New migrations | 0 |
| New API endpoints | 0 (retrieval is internal; exposed via T03 reasoning endpoint) |
| Implementation time | 1 session |

## 10. Architecture constraints (confirmed)

- ✅ Modular monolith
- ✅ Reuse PostgreSQL and RelationshipService
- ✅ No dedicated graph database
- ✅ No vector database (unless measured gap — not present in minimal slice)
- ✅ No new crawler providers
- ✅ No large agent swarm
- ✅ No elaborate LLM orchestration framework
- ✅ No modification to AgentCraft-Toolkit
- ✅ Compact implementation

## STOP — awaiting explicit approval

Per the user's "G03 Closure & G04 Preparation" message:
> *"Do not implement G04-T01 or any later G04 task without explicit approval."*

The agent will NOT:
- Write any code under `src/synapse/` for G04.
- Replace any 501 placeholder.
- Begin G04-T01 implementation.
- Modify AgentCraft-Toolkit.

The agent awaits explicit user approval of this plan before beginning
G04-T01 implementation.

---

*End of G04 Minimal Implementation Plan — STOP for approval.*
