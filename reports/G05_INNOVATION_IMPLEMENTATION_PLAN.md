# G05 — Innovation & Experiments Implementation Plan

> **Status**: PLANNING ONLY — NOT YET AUTHORIZED FOR IMPLEMENTATION
> **Date**: 2026-10-09
> **Approved starting SHA**: `d67880a1cee0bb3020f1e2753a5b9d3b360378df` (G04 closure)
> **Authoritative for**: G05 mission authorization decisions

Per the user's G05-P00 mission briefing:

> "Prepare a lean, executable architecture and staged mission plan for
> G05 — Innovation & Experiments. The primary product objective is to
> transform Synapse's evidence-backed knowledge into useful, original,
> technically plausible application concepts and testable experiments."

> "Important: The system must not merely produce generic application
> ideas. It must explain why a proposed combination is useful, how its
> components work together, what evidence supports it, and what still
> needs experimental validation."

This document is a **planning document only**. No code, schemas,
migrations, or tests are changed by G05-P00. The plan is submitted for
architectural approval before any G05-T01 implementation begins.

---

## 1. G05 functional scope and exclusions

### 1.1 In scope

G05 transforms Synapse from a knowledge-answering system (G04) into a
knowledge-driven **innovation-proposing and experiment-designing**
system. The 7 required capabilities from the mission briefing:

| # | Capability | What it produces |
|---|------------|------------------|
| 1 | Knowledge Combination | A set of compatible tools/capabilities/techniques that could be combined to solve a concrete problem, with traceable evidence per combination. |
| 2 | Opportunity Discovery | A list of underserved needs, technical gaps, and promising combinations — explicitly NOT treating missing evidence as proof of absence. |
| 3 | Innovation Generation | Distinct application concepts: purpose, target users, components, integration logic, expected value, uncertainties. |
| 4 | Architecture Composition | A proposed solution architecture for a selected concept: dependencies, implementation strategy, technical risks. |
| 5 | Innovation Critique | A feasibility/novelty/evidence-coverage/constraints/alternatives/failure-modes evaluation of a generated concept. |
| 6 | Experiment Planning | Small, measurable experiments: hypotheses, success criteria, resource estimates, evidence requirements. |
| 7 | Evidence Feedback | A mechanism by which experiment results update knowledge and confidence WITHOUT auto-converting hypotheses to verified facts. |

### 1.2 Out of scope (deferred or excluded)

| Item | Status | Rationale |
|------|--------|-----------|
| Future scenarios (F0–F4) | Deferred to G06 | Per `G04_MINIMAL_IMPLEMENTATION_PLAN.md` §8; future intelligence is a separate group. |
| Multi-agent swarm / autonomous agent platform | Excluded | Mission constraint; modular monolith only. |
| Separate graph database | Excluded | Relational tables + RelationshipService BFS suffice. |
| Vector database / semantic embeddings | Excluded | Lexical + structured + graph retrieval is sufficient; revisit only on measured gap. |
| LLM as mandatory for every operation | Excluded | Mission constraint; deterministic functions preferred. |
| LLM as forbidden | Excluded | LLM may be used as an OPTIONAL model-assisted function with explicit cost routing. |
| Production deployment of experiments | Excluded | Experiment execution is dry-run / sandbox only; production-mode experiments require explicit operator approval (already enforced by `Experiment.execution_mode=PRODUCTION`). |
| Frontend / UI | Deferred to G07 | Mission constraint; G05 defines public API contracts that G07 will consume. |
| New database engine | Excluded | Reuse existing PostgreSQL (production) / SQLite (tests). |
| AgentCraft-Toolkit modification | Excluded | READ-ONLY boundary preserved (ADR-0009 §7). |
| New crawler / acquisition providers | Excluded | G02 provider scope frozen (3 providers). |
| Auto-promotion of hypotheses to VERIFIED | Excluded | deterministic-v2 policy preserved; experiments produce SUPPORTED / WEAKENED / REJECTED, never VERIFIED. |

### 1.3 Relationship to existing G04 501 placeholders

The G04 router (`src/synapse/api/v1/router.py`) declares 6 placeholder
routes that G05 will replace with real implementations:

| Placeholder route | G05 task that activates it |
|-------------------|---------------------------|
| `POST /api/v1/innovations/generate` | G05-T03 (Innovation Generation) |
| `POST /api/v1/innovations/{id}/critique` | G05-T04 (Innovation Critique) |
| `POST /api/v1/experiments` | G05-T05 (Experiment Planning) |
| `POST /api/v1/experiments/{id}/execute` | G05-T06 (Evidence Feedback — dry-run only) |
| `GET /api/v1/experiments/{id}` | G05-T05 (Experiment Planning — read view) |
| `GET /api/v1/hypotheses/{id}/evidence-deltas` | G05-T06 (Evidence Feedback — audit view) |

G05 does NOT activate `POST /api/v1/future/scenarios*` — those remain
501 placeholders for G06.

---

## 2. Existing components to reuse

G05 is a **composition layer** over G01–G04. It introduces no new
infrastructure; it reuses the existing modular monolith.

### 2.1 Domain contracts (G01, frozen — DO NOT modify)

| Contract | File | G05 reuse |
|----------|------|-----------|
| `Hypothesis` (8 status states, transition matrix) | `domain/hypothesis.py` | G05-T03 creates hypotheses; G05-T06 transitions them based on experiment results. |
| `Experiment` (5 execution modes, `ExperimentResult`) | `domain/experiment.py` | G05-T05 creates experiments; G05-T06 records results. |
| `EvidenceDelta` (immutable belief-change audit) | `domain/evidence_delta.py` | G05-T06 emits deltas when experiment results change a hypothesis's state. |
| `Scenario` (F0–F4 horizon, status) | `domain/scenario.py` | **NOT reused in G05** — reserved for G06. |
| `EpistemicState` (5 states: supported/inferred/hypothesized/disputed/rejected) | `domain/_base.py` | G05-T06 maps experiment outcomes to epistemic state transitions. |
| `EntityType` (12 kinds, includes `EXPERIMENT` and `SCENARIO`) | `domain/_base.py` | G05-T05 may create `EntityRow(kind="experiment")` records to track experiments as first-class knowledge. |
| `Origin` (explicit / derived / hypothesized) | `domain/_base.py` | G05-T03 marks generated relationships as `origin="hypothesized"` so they are never mistaken for documented facts. |
| `Relationship` (16 predicates, direction, verification_state) | `domain/relationship.py` | G05-T03 may create `RelationshipRow(origin="hypothesized")` to model proposed combinations; existing `find_related_entities` traversal works unchanged. |

### 2.2 Application services (G04 — composed, NOT modified)

| Service | File | G05 reuse |
|---------|------|-----------|
| `hybrid_retrieve()` | `application/retrieval.py` | G05-T01 (Knowledge Combination) uses it to fetch candidate capabilities/tools/techniques for a problem domain. |
| `list_capabilities()` / `get_capability()` / `find_capability_by_name()` | `application/capability_registry.py` | G05-T01 enumerates the capability graph; G05-T02 identifies gaps. |
| `analyze_gap()` | `application/gap_analyzer.py` | G05-T02 (Opportunity Discovery) calls it to classify coverage; G05-T04 (Critique) reuses its CONTESTED / NOT_EVIDENCED / CONSTRAINED classifications. |
| `answer_query()` (6 intents + FindingType + citation chains) | `application/reasoning.py` | G05-T04 (Critique) reuses its capability_explanation / dependency_analysis / documented_alternatives / constraint_analysis / gap_explanation intents to ground critique in evidence. |
| `find_capabilities()` / `find_dependencies()` / `find_alternatives()` / `find_limitations()` / `find_contradictions()` / `find_missing_capabilities()` | `application/relationship_service.py` | G05-T01 (Knowledge Combination) uses these to traverse the capability graph and build compatible combinations. |
| `assess_claim()` / `VerificationOutcome` | `application/verification.py` | G05-T06 (Evidence Feedback) consults existing assessments to compute the prior epistemic state before emitting an `EvidenceDelta`. |
| `classify_reasoning_intent()` | `application/reasoning.py` | G05-T03 (Innovation Generation) may classify user intent to choose the right innovation template. |

### 2.3 Evaluation + routing (G04-T04 — reused as-is)

| Component | File | G05 reuse |
|-----------|------|-----------|
| `ResourceBudget` + `route_query()` | `evaluation/router.py` | G05-T03/T05 use the same 3-path router (A direct lookup / B hybrid retrieval / C grounded reasoning) to choose execution paths for innovation-generation and experiment-planning requests. |
| `CostMeasurement` enum (MEASURED / ESTIMATED / NOT_MEASURED) | `evaluation/runner.py` | G05-T03/T05 cost-aware execution reports use the same envelope. |
| 6 mandatory quality gates | `evaluation/runner.py` | G05-T03 extends the same gate pattern (zero fabricated citations, zero unsupported hypothesis promotion, etc.) to innovation concepts. |

### 2.4 API infrastructure (G01 — reused as-is)

| Component | File | G05 reuse |
|-----------|------|-----------|
| `Envelope` / `PaginationMeta` / `ProblemDetail` | `api/responses.py` | All G05 endpoints return the standard envelope. |
| `DomainError` / `NotFoundError` / `ValidationError` / `UnsupportedFeatureError` | `api/errors.py` | G05 endpoints raise the same domain errors → `application/problem+json`. |
| `PrincipalDep` / `DbSessionDep` / `RequestIDDep` | `api/deps.py` | G05 endpoints use the same auth + DB + request-id dependencies. |
| `register_error_handlers()` | `api/errors.py` | No new error handlers needed. |
| `install_middleware()` (request-id, CORS, rate-limit) | `api/middleware.py` | No new middleware needed. |

### 2.5 Storage (G01/G02/G03 — minimal extension)

| Component | File | G05 reuse / extension |
|-----------|------|------------------------|
| `EntityRow` / `ClaimRow` / `RelationshipRow` / `EvidenceFragmentRow` / `SourceSpanRow` | `storage/models.py` | Read-only reuse. G05-T03 may INSERT `RelationshipRow(origin="hypothesized")` rows for proposed combinations; existing tables suffice. |
| `AuditEventRow` | `storage/models.py` | G05-T06 writes `EvidenceDelta` records as `event_type="evidence.delta"` audit events (extending the existing pattern from G03-T04's `verification.assessed`). |
| `JobRow` | `storage/models.py` | G05-T03/T05 long-running operations may use the existing job pattern (202 + polling). |
| `IdempotencyKeyRow` | `storage/models.py` | G05 mutating endpoints accept `Idempotency-Key` header (already wired in G01). |

**No new ORM tables required for the minimal G05 slice.** Hypotheses
and experiments are stored as `EntityRow(kind="experiment")` +
`ClaimRow` + `RelationshipRow` + `AuditEventRow`. This avoids a new
migration and keeps G05 compact. (A future group may add dedicated
`hypotheses` and `experiments` tables if query patterns justify it.)

### 2.6 Existing G05-relevant fixtures

| Fixture | File | G05 reuse |
|---------|------|-----------|
| G04-T03 reasoning fixture (AI-research-assistant scenario) | `tests/integration/test_g04_t03_reasoning.py::_seed_reasoning_fixture` | G05-T01/T03 tests reuse the same fixture shape to verify that innovation generation produces grounded concepts. |
| G04-T03C context-contradiction fixture | `tests/integration/test_g04_t03_reasoning.py::_seed_context_contradiction_fixture` | G05-T04 (Critique) tests verify that critique preserves contradictions. |
| G04-T04 golden dataset (14 cases) | `src/synapse/evaluation/golden_dataset.py` | G05-T03 may extend the dataset with innovation-generation cases (bumping `GOLDEN_DATASET_VERSION`). |

---

## 3. Proposed minimal architecture

### 3.1 Module layout

G05 adds ONE new application package and ONE new API package:

```
src/synapse/
├── application/
│   ├── innovation.py          # NEW: G05-T01..T04 core
│   ├── experiment_planner.py  # NEW: G05-T05 core
│   └── evidence_feedback.py   # NEW: G05-T06 core
├── api/v1/
│   ├── innovations.py          # NEW: G05-T03/T04 endpoints
│   ├── experiments.py          # NEW: G05-T05/T06 endpoints
│   └── hypotheses.py          # NEW: G05-T06 evidence-deltas endpoint
└── evaluation/
    └── innovation_quality.py   # NEW: G05-T03 quality gates (extends G04-T04 pattern)
```

### 3.2 Architectural principles

1. **Composition, not duplication.** G05 services call G04 services; they do NOT re-implement retrieval, capability analysis, or reasoning.
2. **Deterministic by default; model-assisted as opt-in.** Every G05 function has a deterministic fallback. LLM calls (when configured) are routed through a single `ModelAdapter` interface with explicit cost accounting; without an LLM, the deterministic path runs.
3. **Hypotheses are first-class, never facts.** Every generated innovation is stored with `origin="hypothesized"`. The verification engine (G03-T04) is the sole arbiter of promotion; experiments can move a hypothesis to SUPPORTED / WEAKENED / REJECTED, but NEVER to VERIFIED.
4. **Evidence feedback is append-only.** Every belief change produces an immutable `EvidenceDelta` audit row. The prior state is never mutated in place.
5. **Bounded execution.** Every G05 operation respects the `ResourceBudget` from G04-T04 (max_results ≤ 100, max_graph_depth ≤ 5, max_graph_expansion ≤ 50). No unbounded traversal.
6. **API-first for G07.** All G05 endpoints return the standard `Envelope` with stable field names so the G07 UI can consume them without BFF shims.

### 3.3 Deterministic vs model-assisted split

| Function | Deterministic implementation | Model-assisted extension (optional) |
|----------|------------------------------|--------------------------------------|
| Knowledge Combination (T01) | Graph traversal: BFS over `find_capabilities` + `find_dependencies` to enumerate compatible capability sets; ranking by evidence count + independence. | LLM may propose additional combinations by reading capability descriptions; output is still validated against the graph (every cited capability must exist). |
| Opportunity Discovery (T02) | Gap analysis: `analyze_gap` classification (NOT_EVIDENCED, CONSTRAINED, CONTESTED) over the capability graph; missing-capability enumeration via `find_missing_capabilities`. | LLM may summarize gap patterns in natural language; the structured gap list is produced deterministically. |
| Innovation Generation (T03) | Template-based: 6 innovation templates (composition, substitution, constraint-relaxation, gap-filling, recombination, analogy) applied to the gap+combination data; each template produces a structured concept with components, integration logic, uncertainties. | LLM may produce richer natural-language descriptions and propose non-template concepts; output is parsed into the same structured shape. |
| Architecture Composition (T04-pre) | Template-based: maps a concept's components to existing `EntityRow(kind="tool"/"technology")` records + `RelationshipRow(predicate="REQUIRES"/"PROVIDES")`; produces a dependency graph + risk list. | LLM may propose alternative architectures; the structured graph is validated against the knowledge base. |
| Innovation Critique (T04) | Deterministic evaluation: feasibility (does every component exist?), novelty (is the combination unique in the graph?), evidence coverage (how many SUPPORTED claims?), constraint conflicts (any LIMITS edges?), alternative count, failure-mode enumeration (CONTESTED claims, missing dependencies). | LLM may produce a narrative critique; the structured scores are computed deterministically. |
| Experiment Planning (T05) | Template-based: each concept → 1–3 experiments with hypothesis, success criteria, metrics, resource estimate, evidence requirements. | LLM may refine the protocol text; the structured experiment record is produced deterministically. |
| Evidence Feedback (T06) | Rule-based: experiment outcome → `EpistemicState` transition per `HYPOTHESIS_TRANSITIONS`; emits `EvidenceDelta` audit row. No automatic VERIFIED. | None — feedback is always deterministic per the deterministic-v2 policy. |

### 3.4 No new infrastructure

- ❌ No new database engine (reuse PostgreSQL/SQLite)
- ❌ No new ORM tables (reuse `EntityRow` + `ClaimRow` + `RelationshipRow` + `AuditEventRow`)
- ❌ No new migrations
- ❌ No new pip dependencies (httpx already available for any future LLM adapter)
- ❌ No agent framework
- ❌ No vector database
- ❌ No message broker
- ❌ No new middleware

### 3.5 Optional LLM adapter boundary

If/when an LLM is authorized for G05, it will be wired through a
single interface:

```python
# src/synapse/providers/llm_adapter.py (PROPOSED, not yet implemented)
class ModelAdapter(Protocol):
    async def complete(self, prompt: str, *, max_tokens: int) -> str: ...
    def estimate_cost(self, prompt: str, max_tokens: int) -> CostEstimate: ...
```

The adapter is OPTIONAL. Without a configured adapter, every G05
function uses its deterministic fallback. The adapter is the ONLY
place where token usage and monetary cost are measured (extending
the G04-T04 `CostMeasurement` envelope).

**No LLM adapter is implemented in G05-P00.** This is a planning
boundary only. A future G05 task may add the adapter if explicitly
authorized.

---

## 4. Data contracts and required persistence changes

### 4.1 No new migrations required

G05 stores innovations, experiments, and evidence deltas using
existing tables:

| G05 entity | Storage mapping | Existing table |
|------------|-----------------|----------------|
| Innovation (concept) | `EntityRow(kind="project", description=<concept>)` + `ClaimRow` for each component claim + `RelationshipRow(predicate="PROVIDES"/"REQUIRES", origin="hypothesized")` for proposed combinations | `entities`, `claims`, `relationships` |
| Hypothesis | `EntityRow(kind="experiment", description=<hypothesis>)` + `ClaimRow(epistemic_state="hypothesized")` linking the hypothesis to the innovation entity | `entities`, `claims` |
| Experiment | `EntityRow(kind="experiment")` + `ClaimRow` for the protocol + `RelationshipRow(predicate="REQUIRES")` for required capabilities | `entities`, `claims`, `relationships` |
| EvidenceDelta | `AuditEventRow(event_type="evidence.delta", target_id=<hypothesis_entity_id>, payload=<EvidenceDelta dict>)` | `audit_events` |

This avoids a new migration and keeps the G05 footprint small.
**Trade-off**: queries that need "all hypotheses for innovation X"
must filter `EntityRow` + `ClaimRow` rather than a dedicated
`hypotheses` table. This is acceptable for the minimal slice; a
future group can add a dedicated table if query performance
justifies it.

### 4.2 New domain records (in-memory, no persistence change)

G05 introduces in-memory Pydantic models for API responses (no
new `DomainRecord` subclasses, no frozen-contract violation):

```python
# src/synapse/application/innovation.py (PROPOSED)

class InnovationConcept(dict):
    """A generated application concept.
    Keys: id, purpose, target_users, components[], integration_logic,
          expected_value, uncertainties[], evidence_refs[],
          combination_basis, generation_method, hypothesis_id, request_id
    """

class InnovationCritique(dict):
    """A structured critique of an innovation concept.
    Keys: concept_id, feasibility_score, novelty_score, evidence_coverage,
          constraint_conflicts[], alternatives[], failure_modes[],
          overall_recommendation, critique_method, evidence_refs[]
    """

class ExperimentPlan(dict):
    """A planned experiment for a concept/hypothesis.
    Keys: id, hypothesis_id, protocol, baseline, metrics{},
          success_criteria[], resource_estimate, evidence_requirements[],
          execution_mode, safety_limits{}, cost_limits{}
    """

class EvidenceFeedbackResult(dict):
    """The result of applying an experiment outcome to a hypothesis.
    Keys: hypothesis_id, experiment_id, prior_state, observation,
          updated_state, evidence_delta_id, update_method,
          evidence_refs[], applicable_conditions[]
    """
```

These are `dict` subclasses (JSON-friendly) following the same
pattern as G04's `Finding`, `RetrievalResult`, etc. — NOT Pydantic
`DomainRecord` subclasses, so they do not violate the G01 frozen-
contracts boundary.

### 4.3 Audit-event type extension

G05-T06 writes audit events with `event_type="evidence.delta"`. This
extends the existing `AuditEventRow` pattern (G03-T04 already uses
`event_type="verification.assessed"`). No schema change — the
`event_type` column is `String(64)` and accepts any value.

---

## 5. Proposed API endpoints and response contracts

G05 activates 6 of the 9 G04 501 placeholders. The remaining 3
(future scenarios) stay 501 for G06.

### 5.1 Innovation endpoints (G05-T03, G05-T04)

#### POST /api/v1/innovations/generate

**Request:**
```json
{
  "problem_domain": "AI research assistants",
  "context": "academic literature mining",
  "candidate_entity_ids": ["ent-synapse"],
  "max_concepts": 3,
  "generation_method": "deterministic",
  "limit": 20
}
```

**Response (200, Envelope):**
```json
{
  "data": {
    "concepts": [
      {
        "id": "innov-...",
        "purpose": "Accelerate literature review for AI researchers",
        "target_users": ["AI researchers", "graduate students"],
        "components": [
          {"entity_id": "ent-arxiv", "role": "source_discovery"},
          {"entity_id": "ent-trafilatura", "role": "content_extraction"}
        ],
        "integration_logic": "arxiv_search discovers papers; trafilatura extracts text; ...",
        "expected_value": "Reduces literature review time by ~40%",
        "uncertainties": [
          "arxiv API rate limits under heavy load",
          "trafilatura accuracy on non-English papers"
        ],
        "evidence_refs": ["frag-...", "frag-..."],
        "combination_basis": "cap-source-discovery + cap-content-extraction",
        "generation_method": "deterministic",
        "hypothesis_id": "hyp-...",
        "request_id": "..."
      }
    ],
    "unknowns": ["no evidence found for cap-knowledge-extraction"],
    "policy_version": "deterministic-v2",
    "generated_at": "2026-10-09T..."
  },
  "meta": {"request_id": "...", "api_version": "v1", "pagination": {...}},
  "error": null
}
```

**Quality gates enforced:**
- Every `components[].entity_id` must exist in the DB.
- Every `evidence_refs[]` must resolve to an existing fragment.
- Every concept must carry at least one `uncertainty`.
- `generation_method` must be `"deterministic"` or `"model_assisted"`.

#### POST /api/v1/innovations/{innovation_id}/critique

**Request:**
```json
{
  "innovation_id": "innov-...",
  "critique_depth": "full",
  "limit": 20
}
```

**Response (200, Envelope):**
```json
{
  "data": {
    "concept_id": "innov-...",
    "feasibility_score": 0.75,
    "novelty_score": 0.60,
    "evidence_coverage": 0.80,
    "constraint_conflicts": [
      {"constraint_id": "ent-no-vector", "predicate": "LIMITS", "evidence_refs": [...]}
    ],
    "alternatives": [
      {"concept_id": "innov-...", "rationale": "AltTool REPLACES synapse"}
    ],
    "failure_modes": [
      {"mode": "rate_limit_exceeded", "evidence_refs": [...]}
    ],
    "overall_recommendation": "testable_with_caveats",
    "critique_method": "deterministic",
    "evidence_refs": [...]
  },
  ...
}
```

### 5.2 Experiment endpoints (G05-T05, G05-T06)

#### POST /api/v1/experiments

**Request:**
```json
{
  "hypothesis_id": "hyp-...",
  "protocol": "Run arxiv_search for 50 AI agent papers; measure retrieval precision.",
  "baseline": "Random sampling of 50 papers",
  "metrics": {"precision_at_10": "float", "recall_at_10": "float"},
  "execution_mode": "dry_run",
  "safety_limits": {},
  "cost_limits": {"max_api_calls": 10}
}
```

**Response (200, Envelope):** the persisted `ExperimentPlan` with `id`.

#### POST /api/v1/experiments/{experiment_id}/execute

**Request:**
```json
{
  "execution_mode": "dry_run",
  "approved_by": null
}
```

**Constraint:** `execution_mode=PRODUCTION` requires
`safety_limits.approved_by` (already enforced by `Experiment`
domain invariant). G05-T06 only executes `dry_run` and `sandbox`
modes; `local`/`networked`/`production` are rejected with 422.

**Response (200, Envelope):** the `EvidenceFeedbackResult` including
the new `EvidenceDelta` audit row ID.

#### GET /api/v1/experiments/{experiment_id}

Returns the experiment + its result (if any) + linked hypothesis.

#### GET /api/v1/hypotheses/{hypothesis_id}/evidence-deltas

Returns the ordered list of `EvidenceDelta` audit events for a
hypothesis — the full belief-change history.

### 5.3 Stability contract for G07

G07 (UI) may rely on:
- `data.concepts[].components[].entity_id` (stable)
- `data.concepts[].evidence_refs[]` (stable, traceable)
- `data.feasibility_score` (float in [0,1])
- `data.overall_recommendation` (enum: `testable`, `testable_with_caveats`, `needs_more_evidence`, `rejected`)
- `data.evidence_deltas[]` (append-only audit)

Field additions are forward-compatible. Field removals or type
changes require `/api/v2`.

---

## 6. Innovation quality and evidence policies

### 6.1 Mandatory invariants (extends G04-T04 quality gates)

| # | Invariant | Enforcement |
|---|----------|-------------|
| 1 | Zero fabricated component IDs | Every `components[].entity_id` must exist in `EntityRow`. |
| 2 | Zero fabricated evidence references | Every `evidence_refs[]` must resolve to an existing `EvidenceFragmentRow`. |
| 3 | Zero hypothesis-as-fact promotion | Every generated concept's `hypothesis_id` must point to a `ClaimRow(epistemic_state="hypothesized")`; never `"supported"` or `"verified"`. |
| 4 | Zero unsupported architecture claims | Every "component X provides capability Y" claim must trace to a `RelationshipRow(predicate="PROVIDES")` with `evidence_refs`, OR be explicitly marked `origin="hypothesized"`. |
| 5 | Every concept carries ≥1 uncertainty | Concepts without uncertainties are rejected (a concept with no uncertainties is overclaiming). |
| 6 | Every critique cites evidence | `feasibility_score`, `novelty_score`, `evidence_coverage` must cite the `evidence_refs[]` used to compute them. |
| 7 | No experiment auto-promotes to VERIFIED | `EvidenceFeedbackResult.updated_state` may be `SUPPORTED` / `WEAKENED` / `REJECTED` / `HYPOTHESIZED`, but NEVER `VERIFIED`. |
| 8 | Every belief change is audited | An `EvidenceDelta` audit row is written for every `EvidenceFeedbackResult`. Prior state is preserved. |

### 6.2 Evidence-grounding policy

Per mission §6 (Evidence Feedback): *"Design how experiment results
can update knowledge and confidence without automatically converting
hypotheses into verified facts."*

The G05 evidence-feedback policy:

1. **Experiment outcome** is one of: `supporting`, `contradicting`, `inconclusive`.
2. **Transition rules** (deterministic, extend `HYPOTHESIS_TRANSITIONS`):
   - `PROPOSED` + `supporting` → `TESTABLE` (still not SUPPORTED — needs ≥2 supporting experiments)
   - `TESTABLE` + `supporting` × 2 → `SUPPORTED`
   - `TESTABLE` + `contradicting` → `WEAKENED`
   - `EXPERIMENT_RUNNING` + `contradicting` → `REJECTED` (requires `evidence_refs`)
   - `SUPPORTED` + `contradicting` → `WEAKENED`
   - `WEAKENED` + `supporting` × 2 → `SUPPORTED`
3. **No transition to VERIFIED** — VERIFIED remains unreachable in deterministic-v2 (PRB-05).
4. **Every transition emits an `EvidenceDelta`** with `prior_state`, `updated_state`, `observation`, `evidence_refs[]`, `experiment_id`.
5. **Contradictions preserved** — if an experiment produces `contradicting` evidence, the `EvidenceDelta` includes both supporting and contradicting refs; neither is suppressed.

### 6.3 Provenance preservation

Every generated concept, critique, experiment, and feedback result
carries:
- `evidence_refs[]`: traceable to `EvidenceFragmentRow` → `SourceSpanRow` → `SourceRow.canonical_uri`
- `request_id`: per-request tracing
- `policy_version`: `"deterministic-v2"` (unchanged)
- `generation_method`: `"deterministic"` or `"model_assisted"`

External consumers (G07 UI) can follow the chain from any concept
back to the original source text.

---

## 7. Evaluation strategy using realistic innovation scenarios

### 7.1 Golden innovation dataset

G05-T03 extends the G04-T04 golden dataset (currently 14 cases) with
**6 innovation-generation cases** (bumping `GOLDEN_DATASET_VERSION`
to `2.0.0`):

| Case ID | Problem domain | Expected |
|---------|---------------|----------|
| G05-I01 | "AI research assistant" | ≥1 concept citing cap-source-discovery + cap-content-extraction |
| G05-I02 | "Lightweight retrieval tool" | ≥1 concept citing AltTool (REPLACES) |
| G05-I03 | "Vector-search-free evidence verification" | ≥1 concept that respects ent-no-vector LIMITS |
| G05-I04 | "Multi-provider capability comparison" | ≥1 concept citing both ent-synapse and ent-arxiv PROVIDES cap-source-discovery |
| G05-I05 | "Gap-filling: structured knowledge extraction" | ≥1 concept that explicitly addresses cap-knowledge-extraction's NOT_EVIDENCED status |
| G05-I06 | "Constraint-aware: no LLM" | ≥1 concept whose components are all deterministic |

### 7.2 Innovation metrics

| Metric | Implementation |
|--------|---------------|
| Component validity | Fraction of `components[].entity_id` that exist in DB. |
| Evidence coverage | Fraction of `evidence_refs[]` that resolve to existing fragments. |
| Novelty (structural) | `1 - (existing_combinations / total_combinations)` — does this combination already exist as a `RelationshipRow`? |
| Uncertainty honesty | Fraction of concepts that carry ≥1 uncertainty (target: 100%). |
| Critique completeness | Fraction of critiques that cite evidence for every score. |
| Feedback auditability | Fraction of belief changes that emit an `EvidenceDelta` (target: 100%). |

### 7.3 Quality gates (extends G04-T04)

The 6 G04-T04 quality gates apply unchanged. G05 adds 3 innovation-
specific gates:

| # | Gate | Enforcement |
|---|------|-------------|
| 7 | Zero unsupported architecture claims | `RelationshipRow(origin="hypothesized")` for proposed combinations; never `origin="explicit"` without evidence. |
| 8 | Every concept has ≥1 uncertainty | Reject concepts with empty `uncertainties[]`. |
| 9 | No VERIFIED promotion | `EvidenceFeedbackResult.updated_state != "verified"` for all results. |

### 7.4 Demonstration scenario

Per mission §"Important": *"The system must not merely produce
generic application ideas. It must explain why a proposed combination
is useful, how its components work together, what evidence supports
it, and what still needs experimental validation."*

The G05-T03 demonstration runs:

1. **Problem**: "Build an AI research assistant that summarizes arxiv papers."
2. **Knowledge Combination (T01)**: retrieves cap-source-discovery (ent-arxiv PROVIDES), cap-content-extraction (ent-trafilatura PROVIDES), cap-evidence-verification (ent-synapse PROVIDES).
3. **Opportunity Discovery (T02)**: identifies gap cap-knowledge-extraction (NOT_EVIDENCED).
4. **Innovation Generation (T03)**: produces concept "arxiv→trafilatura→synapse pipeline" with components, integration logic, evidence_refs, uncertainties (rate limits, accuracy).
5. **Critique (T04)**: feasibility=0.80 (all components exist), novelty=0.60 (combination is partially novel), evidence_coverage=0.75, constraint_conflicts=[ent-no-vector LIMITS cap-evidence-verification], failure_modes=[rate_limit_exceeded].
6. **Experiment Planning (T05)**: plans experiment "Run arxiv_search for 50 papers; measure retrieval precision."
7. **Evidence Feedback (T06, dry-run)**: simulates a `supporting` outcome → hypothesis moves `PROPOSED` → `TESTABLE`; emits `EvidenceDelta` audit row.

The demonstration prints every stage's structured output and proves
the full chain from problem → concept → critique → experiment →
feedback is traceable and evidence-grounded.

---

## 8. Cost-aware execution strategy

G05 reuses the G04-T04 cost-aware routing policy:

### 8.1 Routing per G05 operation

| G05 operation | Default path | Reason |
|---------------|-------------|--------|
| Knowledge Combination (T01) | PATH B (hybrid retrieval) | Needs lexical + graph traversal. |
| Opportunity Discovery (T02) | PATH C (grounded reasoning) | Needs `analyze_gap` classification. |
| Innovation Generation (T03) | PATH C (grounded reasoning) | Composes T01 + T02 + templates; needs evidence chains. |
| Architecture Composition (T04-pre) | PATH C | Needs `find_dependencies` + `find_limitations`. |
| Innovation Critique (T04) | PATH C | Needs `analyze_gap` + `find_contradictions`. |
| Experiment Planning (T05) | PATH A (direct lookup) if hypothesis is named; else PATH C | Direct lookup suffices when the hypothesis ID is known. |
| Evidence Feedback (T06) | PATH A (direct lookup) | Reads hypothesis + experiment by ID; no retrieval needed. |

### 8.2 Cost envelope

Every G05 operation returns a `ResourceReport` (extending G04-T04):

| Field | Label | Source |
|-------|-------|--------|
| `duration_seconds` | MEASURED | `time.perf_counter()` |
| `component_count` | MEASURED | count of entities touched |
| `evidence_refs_count` | MEASURED | count of fragments cited |
| `graph_expansion_count` | MEASURED for PATH B/C; NOT_MEASURED for PATH A | BFS expansion size |
| `concept_count` | MEASURED | innovations generated |
| `monetary_cost` | NOT_MEASURED (deterministic) / MEASURED (model-assisted) | LLM token cost if adapter configured |
| `token_usage` | NOT_MEASURED (deterministic) / MEASURED (model-assisted) | LLM tokens if adapter configured |

### 8.3 No paid LLM mandatory

Per mission constraint: every G05 function has a deterministic
fallback. The LLM adapter is OPTIONAL; without it, `monetary_cost =
NOT_MEASURED` and `token_usage = NOT_MEASURED` (same as G04-T04).

---

## 9. Sequential implementation missions

### G05-T01 — Knowledge Combination

**Objective**: Given a problem domain, enumerate compatible
tool/capability/technique combinations from the knowledge graph,
each with traceable evidence.

**Scope**:
- `src/synapse/application/innovation.py` (~250 LOC): `combine_knowledge()` function.
- `tests/integration/test_g05_t01_knowledge_combination.py` (~200 LOC).
- Reuses: `hybrid_retrieve`, `find_capabilities`, `find_dependencies`, `find_alternatives`.

**Acceptance criteria**:
1. Returns ≥1 combination for a problem domain with ≥2 documented capabilities.
2. Every cited entity_id resolves to an existing `EntityRow`.
3. Every evidence_ref resolves to an existing `EvidenceFragmentRow`.
4. Empty result when no capabilities match (truthful, no fabrication).
5. Deterministic: same input → same output.
6. G01–G04 regression intact.

**STOP gate**: STOP after T01. Await T02 authorization.

**Estimated footprint**: ~450 LOC (source + tests).

---

### G05-T02 — Opportunity Discovery

**Objective**: Identify underserved needs, technical gaps, and
promising combinations — explicitly NOT treating missing evidence
as proof of absence.

**Scope**:
- Extend `src/synapse/application/innovation.py` (~150 LOC): `discover_opportunities()` function.
- `tests/integration/test_g05_t02_opportunity_discovery.py` (~200 LOC).
- Reuses: `analyze_gap`, `find_missing_capabilities`, `find_contradictions`.

**Acceptance criteria**:
1. Returns ≥1 opportunity per gap category (NOT_EVIDENCED, CONSTRAINED, CONTESTED).
2. Missing evidence is reported as `unknowns[]`, NOT as "no solution exists".
3. Applicable contradictions are preserved (not suppressed).
4. Out-of-context contradictions are preserved as metadata.
5. Deterministic.
6. G01–G04-T01 regression intact.

**STOP gate**: STOP after T02. Await T03 authorization.

**Estimated footprint**: ~350 LOC (source + tests).

---

### G05-T03 — Innovation Generation + Quality Gates

**Objective**: Produce distinct application concepts from T01+T02
data, each with purpose, components, integration logic, expected
value, uncertainties, evidence_refs. Activate `POST /api/v1/innovations/generate`.

**Scope**:
- Extend `src/synapse/application/innovation.py` (~300 LOC): `generate_innovations()` + 6 templates.
- `src/synapse/evaluation/innovation_quality.py` (~150 LOC): 3 new quality gates.
- `src/synapse/api/v1/innovations.py` (~120 LOC): `POST /api/v1/innovations/generate`.
- Update `src/synapse/api/v1/router.py` (~5 LOC): activate the innovations router.
- `tests/integration/test_g05_t03_innovation_generation.py` (~300 LOC).
- Extend `src/synapse/evaluation/golden_dataset.py` (~80 LOC): 6 innovation cases; bump `GOLDEN_DATASET_VERSION` to `"2.0.0"`.

**Acceptance criteria**:
1. Generates ≥1 valid concept per golden innovation case.
2. Every concept has ≥1 uncertainty.
3. Every concept's `hypothesis_id` points to a `ClaimRow(epistemic_state="hypothesized")`.
4. Quality gates 7–9 (unsupported architecture claims, uncertainty honesty, no VERIFIED promotion) enforced.
5. `POST /api/v1/innovations/generate` returns 200 with auth, 401 without.
6. OpenAPI includes the new endpoint.
7. G01–G04-T02 regression intact.

**STOP gate**: STOP after T03. Await T04 authorization.

**Estimated footprint**: ~950 LOC (source + tests + golden dataset).

---

### G05-T04 — Innovation Critique + Architecture Composition

**Objective**: Evaluate a generated concept for feasibility, novelty,
evidence coverage, constraints, alternatives, failure modes.
Activate `POST /api/v1/innovations/{id}/critique`.

**Scope**:
- Extend `src/synapse/application/innovation.py` (~250 LOC): `compose_architecture()` + `critique_innovation()`.
- Extend `src/synapse/api/v1/innovations.py` (~80 LOC): `POST /api/v1/innovations/{id}/critique`.
- `tests/integration/test_g05_t04_innovation_critique.py` (~250 LOC).

**Acceptance criteria**:
1. Every critique cites evidence for every score.
2. `feasibility_score` reflects whether all components exist.
3. `novelty_score` reflects whether the combination is unique in the graph.
4. `constraint_conflicts[]` lists LIMITS/CONTRADICTS edges.
5. `failure_modes[]` includes CONTESTED claims and missing dependencies.
6. `overall_recommendation` is one of: `testable`, `testable_with_caveats`, `needs_more_evidence`, `rejected`.
7. G01–G04-T03 regression intact.

**STOP gate**: STOP after T04. Await T05 authorization.

**Estimated footprint**: ~580 LOC (source + tests).

---

### G05-T05 — Experiment Planning

**Objective**: Define small, measurable experiments for a concept/
hypothesis. Activate `POST /api/v1/experiments` + `GET /api/v1/experiments/{id}`.

**Scope**:
- `src/synapse/application/experiment_planner.py` (~200 LOC): `plan_experiment()`.
- `src/synapse/api/v1/experiments.py` (~150 LOC): `POST` + `GET` endpoints.
- Update `src/synapse/api/v1/router.py` (~5 LOC): activate experiments router.
- `tests/integration/test_g05_t05_experiment_planning.py` (~250 LOC).

**Acceptance criteria**:
1. Every experiment plan has ≥1 success criterion.
2. Every experiment has `execution_mode` (default `dry_run`).
3. `production` mode requires `safety_limits.approved_by` (already enforced by `Experiment` invariant).
4. `POST /api/v1/experiments` returns 200 with auth.
5. `GET /api/v1/experiments/{id}` returns 200 / 404.
6. G01–G04-T04 regression intact.

**STOP gate**: STOP after T05. Await T06 authorization.

**Estimated footprint**: ~600 LOC (source + tests).

---

### G05-T06 — Evidence Feedback + Audit Trail

**Objective**: Apply experiment results to hypotheses via
deterministic transition rules; emit immutable `EvidenceDelta` audit
rows. Activate `POST /api/v1/experiments/{id}/execute` +
`GET /api/v1/hypotheses/{id}/evidence-deltas`.

**Scope**:
- `src/synapse/application/evidence_feedback.py` (~250 LOC): `apply_experiment_result()` + transition rules.
- Extend `src/synapse/api/v1/experiments.py` (~80 LOC): `POST /execute`.
- `src/synapse/api/v1/hypotheses.py` (~80 LOC): `GET /evidence-deltas`.
- Update `src/synapse/api/v1/router.py` (~5 LOC): activate hypotheses router.
- `tests/integration/test_g05_t06_evidence_feedback.py` (~300 LOC).

**Acceptance criteria**:
1. `supporting` outcome moves PROPOSED → TESTABLE (not SUPPORTED — needs 2).
2. `contradicting` outcome moves TESTABLE → WEAKENED; EXPERIMENT_RUNNING → REJECTED.
3. **No transition to VERIFIED** — invariant enforced.
4. Every belief change emits an `EvidenceDelta` audit row.
5. `GET /api/v1/hypotheses/{id}/evidence-deltas` returns the ordered audit trail.
6. Contradictions preserved (both supporting + contradicting refs in the delta).
7. Only `dry_run` and `sandbox` execution modes accepted; others return 422.
8. G01–G04-T05 full regression intact.

**STOP gate**: STOP after T06. Await G05 closure review authorization.

**Estimated footprint**: ~700 LOC (source + tests).

---

### G05 closure review (separate mission, not part of T01–T06)

A G05 closure review (analogous to G04-T04C) may be authorized
after T06 to audit innovation quality, evidence-feedback integrity,
and API contract stability before G06 begins. This is NOT part of
the current plan.

---

## 10. Acceptance criteria and STOP gate for each task

| Task | Acceptance criteria count | STOP gate |
|------|--------------------------:|-----------|
| G05-T01 | 6 | STOP. Await T02 authorization. |
| G05-T02 | 6 | STOP. Await T03 authorization. |
| G05-T03 | 7 | STOP. Await T04 authorization. |
| G05-T04 | 7 | STOP. Await T05 authorization. |
| G05-T05 | 6 | STOP. Await T06 authorization. |
| G05-T06 | 8 | STOP. Await G05 closure review authorization. |

**No task begins without explicit authorization.** Each task's STOP
gate is hard: implementation halts, the report is committed, and
the next task awaits a separate mission briefing.

---

## 11. Estimated file and LOC footprint, with justification

### 11.1 Per-task estimate

| Task | New source LOC | New test LOC | New report LOC | Total LOC |
|------|---------------:|-------------:|---------------:|----------:|
| G05-T01 | 250 | 200 | ~400 | ~850 |
| G05-T02 | 150 | 200 | ~400 | ~750 |
| G05-T03 | 570 (innovation + quality + API + golden dataset) | 300 | ~500 | ~1,370 |
| G05-T04 | 330 (innovation + API) | 250 | ~450 | ~1,030 |
| G05-T05 | 350 (planner + API) | 250 | ~450 | ~1,050 |
| G05-T06 | 410 (feedback + API + hypotheses) | 300 | ~500 | ~1,210 |
| **G05 total** | **~2,060** | **~1,500** | **~2,700** | **~6,260** |

### 11.2 Justification

- **Source LOC (~2,060)**: G05 is a composition layer — most logic is "call G04 service + apply template + return structured result". The 6 innovation templates in T03 are the largest single chunk (~300 LOC). Each template is a small deterministic function; no LLM is mandatory.
- **Test LOC (~1,500)**: Tests follow the G04-T04 pattern (fixture + assertions on evidence-preservation fields + quality-gate verification). The 6 golden innovation cases add ~80 LOC to the existing dataset.
- **Report LOC (~2,700)**: Each task ships a closure report following the G04 pattern (executive summary, files changed, acceptance criteria, test results, limitations). Reports are the primary review artifact.
- **No new migrations, no new pip deps, no new ORM tables**: G05 reuses existing persistence. The only schema-adjacent change is the new `event_type="evidence.delta"` audit event value, which requires no schema change (the column is `String(64)`).

### 11.3 Comparison to G04

G04 added ~3,400 LOC (T01-T05 source + tests). G05 is estimated at
~6,260 LOC — roughly 1.8× G04. The increase is justified by:
- 6 tasks (vs G04's 5)
- Innovation generation requires 6 templates (more logic than G04's intent classifier)
- Evidence feedback requires transition-rule engine + audit trail
- 3 new quality gates + 6 golden innovation cases

If the actual LOC exceeds the estimate by >30%, the implementation
should pause and re-evaluate scope (per G04-T04 mission §9
"Implementation discipline": *"If implementation scope grows
significantly, simplify the design before proceeding."*).

---

## 12. Dependencies and risks affecting G06 and G07

### 12.1 Dependencies on G06 (Future Intelligence)

G05 produces:
- `Hypothesis` records with status transitions (PROPOSED → TESTABLE → SUPPORTED/WEAKENED/REJECTED).
- `Experiment` records with results.
- `EvidenceDelta` audit trail.

G06 (Future Scenarios) will consume:
- The hypothesis lifecycle to ground F0–F4 scenarios in tested beliefs.
- The evidence-feedback audit trail to compute scenario confidence.
- The capability graph (unchanged from G04) to identify future-capability gaps.

**Risk**: If G05-T06's transition rules are too conservative, G06 may
lack enough SUPPORTED hypotheses to build scenarios. Mitigation:
the rules allow `TESTABLE` (1 supporting experiment) as a scenario
basis, so G06 is not blocked by `SUPPORTED` (2 experiments) scarcity.

### 12.2 Dependencies on G07 (UI)

G05 produces stable API contracts that G07 will consume:
- `POST /api/v1/innovations/generate` → UI "Generate concept" button.
- `POST /api/v1/innovations/{id}/critique` → UI "Critique" button.
- `POST /api/v1/experiments` + `GET /api/v1/experiments/{id}` → UI experiment workflow.
- `POST /api/v1/experiments/{id}/execute` → UI "Run experiment" (dry-run only).
- `GET /api/v1/hypotheses/{id}/evidence-deltas` → UI belief-history timeline.

**Risk**: If G05 field names are not stable, G07 will need a BFF
shim. Mitigation: §5.3 documents the stability contract; field
additions are forward-compatible; removals require `/api/v2`.

### 12.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Innovation generation produces generic ideas (not grounded) | Medium | High | Quality gates 7–9 (§6.1) enforce evidence grounding; golden innovation cases (§7.1) verify specificity. |
| LLM adapter introduced without authorization | Low | High | The adapter is a planning boundary only (§3.5); no adapter is implemented in G05-P00. Any future adapter requires explicit authorization. |
| Hypothesis auto-promotion to VERIFIED | Low | Critical | `EvidenceFeedbackResult.updated_state` is restricted to non-VERIFIED values (§6.2); quality gate 9 enforces. |
| EvidenceDelta audit trail grows unbounded | Medium | Low | Audit rows are append-only; a future group can add archival. Not a G05 blocker. |
| G05 scope creep (LOC exceeds estimate) | Medium | Medium | §11.3 pause-and-re-evaluate rule. |
| G07 UI assumes field stability before G05 freezes contracts | Low | Medium | §5.3 stability contract; G07 should wait for G05 closure. |
| G06 blocked by insufficient SUPPORTED hypotheses | Low | Medium | §12.1: TESTABLE is a valid scenario basis. |
| Experiment execution side-effects (even in dry-run) | Low | High | T06 only allows `dry_run` and `sandbox`; `local`/`networked`/`production` return 422 (§5.2). |

### 12.4 Production-readiness blockers (unchanged)

PRB-01 through PRB-07 (from ADR-0011) remain unchanged. G05 does
NOT introduce new production-readiness blockers. G05 is functional-
pass-with-limitations, NOT production-ready.

---

## 13. Plan summary

G05 is a **composition layer** that transforms Synapse from a
knowledge-answering system into an innovation-proposing and
experiment-designing system. It:

- Reuses G01–G04 entities, services, retrieval, capability analysis, reasoning, evaluation, and routing.
- Introduces no new database, vector store, LLM provider (mandatory), agent framework, or frontend.
- Adds 6 sequential tasks (T01–T06), each with explicit acceptance criteria and a STOP gate.
- Activates 6 of the 9 G04 501 placeholder routes (the other 3 stay 501 for G06).
- Preserves all evidence/provenance/uncertainty/contradiction handling from G04.
- Never auto-promotes hypotheses to VERIFIED.
- Emits immutable `EvidenceDelta` audit rows for every belief change.
- Defines stable API contracts for G07 (UI).

**Estimated total**: ~6,260 LOC across 6 tasks (source + tests + reports).

**No G05 implementation begins without explicit per-task authorization.**

---

*End of G05 Innovation & Experiments Implementation Plan — STOP for architectural approval.*
