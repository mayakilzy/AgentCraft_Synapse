# G05 — Innovation & Experiments Implementation Plan

> **Status**: PLANNING ONLY — NOT YET AUTHORIZED FOR IMPLEMENTATION
> **Date**: 2026-10-09 (revised G05-P00C)
> **Approved starting SHA**: `d67880a1cee0bb3020f1e2753a5b9d3b360378df` (G04 closure)
> **Revised at**: `211268a5b0fd89f274d73577329a4b55e0d99b64` (G05-P00)
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
migrations, or tests are changed by G05-P00 or G05-P00C. The plan is
submitted for architectural approval before any G05-T01
implementation begins.

### Revision history

- **G05-P00** (commit `211268a`): initial plan.
- **G05-P00C** (this revision): focused architectural correction
  per the G05-P00C mission. Three correction areas applied:
  - **Correction A — Genuine Innovation**: distinguish candidate
    discovery, exploratory concept generation, evidence-grounded
    evaluation, novelty assessment, and experiment design as
    separate concerns; allow optional model-assisted creative
    exploration behind a bounded interface; require every proposed
    combination to explain its integration mechanism and potential
    benefit; graph uniqueness is NOT market/technical novelty.
  - **Correction B — Persistence and Epistemic Isolation**: verify
    the storage mapping against actual frozen domain contracts and
    ORM schema; ensure distinct stable identities and lifecycle
    semantics for innovations, hypotheses, experiments, and
    observations; one hypothesis supports many experiments;
    hypothesized relationships must not contaminate established
    knowledge or ordinary retrieval; prefer minimal reuse, no new
    table unless an invariant demonstrably cannot be preserved.
  - **Correction C — Experiments and Evidence Feedback**: separate
    dry-run validation, sandbox/actual execution, observation
    capture, evidence assessment, and hypothesis-state transition;
    a successful dry-run is NOT evidence; no fixed-count
    auto-promotion; use evidence quality, independence,
    applicability, and contradiction handling; verify all
    transitions against the frozen `Hypothesis` and `EvidenceDelta`
    contracts; never auto-promote to VERIFIED.
  - **Additional consistency checks**: remove unsupported numerical
    benefit claims from API examples; verify endpoint paths match
    the actual placeholder route definitions; ensure G05 evaluation
    cases test meaningful innovation quality, not merely non-empty
    template output; preserve G06/G07 dependencies; keep scope lean.
  See §14 (Corrections summary and implications for T01–T06) for
  the full list of changes and their per-task impact.

---

## 1. G05 functional scope and exclusions

### 1.1 In scope

G05 transforms Synapse from a knowledge-answering system (G04) into a
knowledge-driven **innovation-proposing and experiment-designing**
system. Per Correction A (Genuine Innovation), the 7 required
capabilities are organized as 5 distinct concerns that must not be
conflated:

#### Concern 1 — Candidate discovery and combination (capabilities 1)

Enumerate compatible tools, capabilities, techniques, and architectural
patterns from the knowledge graph. This is a **retrieval + graph
traversal** concern — it produces *candidates*, not *concepts*. Every
candidate cites traceable evidence. (→ G05-T01)

#### Concern 2 — Exploratory concept generation (capability 3)

Produce distinct application concepts from candidates. This is a
**generative** concern. Six deterministic innovation templates
(composition, substitution, constraint-relaxation, gap-filling,
recombination, analogy) are useful **baselines**, not the complete
innovation capability. An optional model-assisted creative
exploration path may propose non-template concepts behind a bounded
interface (§3.5), but every concept — deterministic or
model-assisted — is parsed into the same structured shape and must
explain its **integration mechanism** (how the components work
together) and **potential benefit** (why the combination is useful).
(→ G05-T03)

#### Concern 3 — Evidence-grounded evaluation (capabilities 4, 5)

Evaluate a generated concept for feasibility, evidence coverage,
constraint conflicts, alternatives, and failure modes. This is an
**assessment** concern — deterministic scoring grounded in the
knowledge graph. (→ G05-T04)

#### Concern 4 — Novelty assessment and uncertainty (part of capability 5)

Assess whether a proposed combination is novel. **Knowledge-graph
uniqueness is necessary but NOT sufficient** — a combination absent
from the graph is not proof of market or technical novelty.
`novelty_score` reflects graph uniqueness; a separate
`novelty_caveat` field explicitly states that graph uniqueness is
structural, not market validation. Every concept carries ≥1
uncertainty (a concept with no uncertainties is overclaiming).
(→ G05-T04)

#### Concern 5 — Experiment design (capabilities 2, 6, 7)

Identify underserved needs and gaps (Opportunity Discovery, T02),
define small measurable experiments (Experiment Planning, T05), and
design how experiment results update knowledge and confidence
without auto-converting hypotheses to verified facts (Evidence
Feedback, T06). (→ G05-T02, T05, T06)

### 1.1.1 Capability-to-concern mapping

| # | Required capability | Concern |
|---|---------------------|---------|
| 1 | Knowledge Combination | 1 (Candidate discovery) |
| 2 | Opportunity Discovery | 5 (Experiment design) |
| 3 | Innovation Generation | 2 (Exploratory concept generation) |
| 4 | Architecture Composition | 3 (Evidence-grounded evaluation) |
| 5 | Innovation Critique | 3 + 4 (Evaluation + Novelty) |
| 6 | Experiment Planning | 5 (Experiment design) |
| 7 | Evidence Feedback | 5 (Experiment design) |

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
2. **Deterministic by default; model-assisted as opt-in behind a bounded interface.** Every G05 function has a deterministic fallback. LLM calls (when configured) are routed through a single `ModelAdapter` interface (§3.5) with explicit cost accounting; without an LLM, the deterministic path runs. The model-assisted path NEVER bypasses evidence grounding — its output is parsed into the same structured shape and validated against the knowledge graph.
3. **Five distinct concerns, not one pipeline.** Candidate discovery, exploratory concept generation, evidence-grounded evaluation, novelty assessment, and experiment design are separate concerns (§1.1). They may be composed but must not be conflated — a candidate is not a concept; a concept is not an evaluation; graph uniqueness is not market novelty.
4. **Every proposed combination explains its integration mechanism and potential benefit.** A concept that lists components without explaining how they work together, or why the combination is useful, is rejected by quality gate 10 (§6.1).
5. **Hypotheses are first-class, never facts.** Every generated innovation is stored with `origin="hypothesized"`. The verification engine (G03-T04) is the sole arbiter of promotion; experiments can move a hypothesis's `HypothesisStatus` (PROPOSED → TESTABLE → SUPPORTED/WEAKENED/REJECTED) but NEVER to a VERIFIED-equivalent state. See §6.2 for the corrected state-machine semantics.
6. **Hypothesized relationships are epistemically isolated.** `RelationshipRow(origin="hypothesized")` rows are never returned by ordinary retrieval (`hybrid_retrieve` filters them out) — they are only visible via the innovation endpoints. This prevents hypothesized combinations from contaminating established knowledge. (§4.4)
7. **Evidence feedback is append-only.** Every belief change produces an immutable `EvidenceDelta` audit row. The prior state is never mutated in place.
8. **Bounded execution.** Every G05 operation respects the `ResourceBudget` from G04-T04 (max_results ≤ 100, max_graph_depth ≤ 5, max_graph_expansion ≤ 50). No unbounded traversal.
9. **API-first for G07.** All G05 endpoints return the standard `Envelope` with stable field names so the G07 UI can consume them without BFF shims.

### 3.3 Deterministic vs model-assisted split

Per Correction A, the six deterministic innovation templates are
**baselines**, not the complete innovation capability. The
model-assisted path is an opt-in creative exploration behind a
bounded interface — it never bypasses evidence grounding.

| Function | Deterministic implementation (baseline) | Model-assisted extension (optional, behind `ModelAdapter`) |
|----------|----------------------------------------|----------------------------------------------------------|
| Knowledge Combination (T01) | Graph traversal: BFS over `find_capabilities` + `find_dependencies` to enumerate compatible capability sets; ranking by evidence count + independence. | LLM may propose additional combinations by reading capability descriptions; output is still validated against the graph (every cited capability must exist). |
| Opportunity Discovery (T02) | Gap analysis: `analyze_gap` classification (NOT_EVIDENCED, CONSTRAINED, CONTESTED) over the capability graph; missing-capability enumeration via `find_missing_capabilities`. | LLM may summarize gap patterns in natural language; the structured gap list is produced deterministically. |
| Innovation Generation (T03) | Template-based baselines: 6 innovation templates (composition, substitution, constraint-relaxation, gap-filling, recombination, analogy) applied to the gap+combination data; each template produces a structured concept with components, **integration_mechanism** (how components work together), **potential_benefit** (why the combination is useful), uncertainties. | LLM may propose non-template concepts via creative exploration; output is parsed into the same structured shape and must still carry `integration_mechanism` and `potential_benefit`. The LLM cannot fabricate component IDs or evidence_refs — these are validated against the graph. |
| Architecture Composition (T04-pre) | Template-based: maps a concept's components to existing `EntityRow(kind="tool"/"technology")` records + `RelationshipRow(predicate="REQUIRES"/"PROVIDES")`; produces a dependency graph + risk list. | LLM may propose alternative architectures; the structured graph is validated against the knowledge base. |
| Innovation Critique (T04) | Deterministic evaluation: feasibility (does every component exist?), evidence coverage (how many SUPPORTED claims?), constraint conflicts (any LIMITS edges?), alternative count, failure-mode enumeration (CONTESTED claims, missing dependencies). | LLM may produce a narrative critique; the structured scores are computed deterministically. |
| Novelty Assessment (T04) | `novelty_score` = graph-uniqueness fraction (1 − existing_combinations / total_combinations). **Explicitly labeled structural, not market novelty.** `novelty_caveat` field states: "graph uniqueness is necessary but not sufficient; market/technical novelty requires external validation." | LLM may suggest market-comparison analogies; these are appended as `novelty_notes[]`, never used to inflate `novelty_score`. |
| Experiment Planning (T05) | Template-based: each concept → 1–3 experiments with hypothesis, success criteria, metrics, resource estimate, evidence requirements. | LLM may refine the protocol text; the structured experiment record is produced deterministically. |
| Evidence Feedback (T06) | Rule-based: experiment outcome → `HypothesisStatus` transition per `HYPOTHESIS_TRANSITIONS` (the frozen 8-state lifecycle); emits `EvidenceDelta` audit row with `prior_state`/`updated_state` typed `EpistemicState` (the frozen 5-axis claim state). No fixed-count auto-promotion. No automatic VERIFIED. | None — feedback is always deterministic per the deterministic-v2 policy. |

**Key correction (G05-P00C)**: the original plan conflated `HypothesisStatus`
(8-state lifecycle) with `EpistemicState` (5-axis claim state).
`EvidenceDelta.prior_state` and `updated_state` are typed
`EpistemicState` (supported/inferred/hypothesized/disputed/rejected),
NOT `HypothesisStatus`. The two state machines are distinct:
- `HypothesisStatus` governs the hypothesis **lifecycle** (PROPOSED →
  TESTABLE → EXPERIMENT_RUNNING → SUPPORTED/WEAKENED/REJECTED).
- `EpistemicState` governs the **epistemic weight** of a claim
  (hypothesized → supported/disputed/rejected).
G05-T06 transitions `HypothesisStatus` and emits `EvidenceDelta`
records whose `prior_state`/`updated_state` reflect the **claim's**
epistemic state, not the hypothesis's lifecycle state. See §6.2 for
the corrected transition semantics.

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

### 4.1 Storage mapping verified against actual frozen domain contracts and ORM schema

Per Correction B, the proposed storage mapping is verified against
the **actual** frozen domain contracts (`src/synapse/domain/*.py`)
and ORM schema (`src/synapse/storage/models.py`). No new migrations
are proposed; the existing tables suffice to preserve the required
invariants.

#### 4.1.1 Verified table inventory (from `storage/models.py`)

| Table | ORM class | Relevant columns |
|-------|-----------|------------------|
| `entities` | `EntityRow` | `id`, `kind` (String(32)), `canonical_name`, `aliases` (JSON), `attributes` (JSON), `description`, `version`, timestamps. `kind` accepts any string (no DB-level enum constraint). |
| `claims` | `ClaimRow` | `id`, `proposition`, `subject_ref`, `object_ref`, `evidence_refs` (JSON array), `epistemic_state` (String(32), default `"hypothesized"`), `confidence_value`, `validity_conditions` (JSON), `contradicting_refs` (JSON), `superseded_by`, `extraction_method`, `version`, timestamps. |
| `relationships` | `RelationshipRow` | `id`, `from_entity_id`, `to_entity_id`, `predicate`, `direction`, `origin` (String(32), default `"explicit"`), `verification_state`, `evidence_refs` (JSON), `confidence_value`, `conditions`, `superseded_by`, `derivation_chain` (JSON), `version`, timestamps. **Indexed on `(origin, verification_state)`** — supports efficient filtering of `origin='hypothesized'`. |
| `evidence_fragments` | `EvidenceFragmentRow` | `id`, `acquisition_id`, `source_id`, `source_uri`, `exact_excerpt`, `excerpt_hash`, `retrieved_at`, `extraction_method`, `content_fingerprint`, etc. |
| `source_spans` | `SourceSpanRow` | `id`, `evidence_fragment_id`, `claim_id`, `start_offset`, `end_offset`, `excerpt`. |
| `audit_events` | `AuditEventRow` | `id`, `event_type` (String(64)), `actor`, `target_id`, `target_type`, `payload` (JSON), `request_id`, `created_at`. **Indexed on `(event_type)` and `(target_type, target_id)`.** |
| `jobs` | `JobRow` | `id`, `kind`, `status`, `progress`, `input_ref`, `output_ref`, `idempotency_key`, etc. |

#### 4.1.2 G05 entity → storage mapping (verified)

| G05 entity | Storage mapping (verified) | Identity | Lifecycle |
|------------|---------------------------|----------|-----------|
| **Innovation (concept)** | `EntityRow(kind="project")` for the concept root + `ClaimRow(epistemic_state="hypothesized", proposition=<purpose>)` for the concept's central claim + `RelationshipRow(origin="hypothesized", predicate="PROVIDES")` linking the concept to its components. | `EntityRow.id` (stable) | Created in T03; may be SUPERSEDED by a newer concept (via `superseded_by` on the ClaimRow). |
| **Hypothesis** | `EntityRow(kind="experiment")` for the hypothesis entity + `ClaimRow(epistemic_state="hypothesized", proposition=<hypothesis description>)` linking the hypothesis to the innovation concept via `subject_ref`. The hypothesis's `HypothesisStatus` is stored in `ClaimRow.confidence_method` (a free-text String(256) column) as a JSON-encoded `{"hypothesis_status": "proposed"}`. **No new column needed.** | `EntityRow.id` (stable) | Transitions per `HYPOTHESIS_TRANSITIONS` (the frozen 8-state matrix in `domain/hypothesis.py`). |
| **Experiment** | `EntityRow(kind="experiment")` (distinct from the hypothesis entity) + `ClaimRow` for the protocol + `RelationshipRow(predicate="REQUIRES")` for required capabilities. The `Experiment.execution_mode` is stored in `ClaimRow.confidence_method` as `{"execution_mode": "dry_run"}`. | `EntityRow.id` (stable) | Created in T05; one hypothesis → many experiments (1:N via `ClaimRow.subject_ref` pointing to the hypothesis entity). |
| **Observation** (experiment result) | `ClaimRow(epistemic_state=<inferred from outcome>)` with `subject_ref=<experiment entity>` + `evidence_refs[]` pointing to fragments produced by the experiment (sandbox/execution mode). For dry-run, no observation ClaimRow is created (dry-run produces NO evidence). | `ClaimRow.id` (stable) | Immutable once recorded. |
| **EvidenceDelta** | `AuditEventRow(event_type="evidence.delta", target_id=<hypothesis entity id>, target_type="hypothesis", payload=<EvidenceDelta dict>)`. The payload includes `prior_state`, `updated_state` (both `EpistemicState` values), `observation`, `evidence_refs[]`, `experiment_id`. | `AuditEventRow.id` (stable, append-only) | Immutable audit row. |

#### 4.1.3 Why no new table is needed

Per Correction B: *"Prefer minimal reuse. Introduce a new table only
if the existing storage model demonstrably cannot preserve the required
invariants."*

The required invariants and how the existing schema preserves them:

1. **Distinct stable identities**: innovations, hypotheses, experiments,
   and observations each get a distinct `EntityRow.id` or `ClaimRow.id`.
   ✅ Existing `id` columns (String(64) primary keys) suffice.
2. **Distinct lifecycle semantics**: innovations SUPERSEDE; hypotheses
   transition per `HYPOTHESIS_TRANSITIONS`; experiments are immutable
   once executed; observations are immutable. The `version` and
   `superseded_by` columns on `EntityRow`/`ClaimRow`/`RelationshipRow`
   support these lifecycles. ✅ Existing columns suffice.
3. **One hypothesis → many experiments**: `ClaimRow.subject_ref`
   (the hypothesis entity ID) can be referenced by multiple experiment
   `ClaimRow`s. ✅ Existing FK-by-convention (no DB-level FK, but
   `ix_claims_subject_ref` index supports the query).
4. **Hypothesized relationships don't contaminate retrieval**:
   `RelationshipRow.origin="hypothesized"` is filterable via the
   existing `ix_relationships_origin_verification` index. The G04-T01
   `hybrid_retrieve` function already filters by `origin` in some
   paths; G05-T01 will verify and document this isolation. ✅ Existing
   index suffices.
5. **Evidence feedback is append-only**: `AuditEventRow` is append-only
   by design (no `UPDATE` or `DELETE` in the existing codebase).
   ✅ Existing table suffices.
6. **`EvidenceDelta` invariants** (from `domain/evidence_delta.py`):
   `_must_change_state` (prior ≠ updated) and
   `_rejected_requires_contradicting_evidence` (REJECTED requires
   evidence_refs) are enforced at the Pydantic layer before the
   audit row is written. ✅ No DB-level constraint needed.

**Conclusion**: no new table, no new column, no new migration is
required for the minimal G05 slice. If a future group finds that
query patterns (e.g., "all experiments for hypothesis X ordered by
execution time") become performance bottlenecks, a dedicated
`hypotheses` or `experiments` table can be added then — but that is
NOT justified by the current invariants.

### 4.2 New domain records (in-memory, no persistence change)

G05 introduces in-memory Pydantic models for API responses (no
new `DomainRecord` subclasses, no frozen-contract violation):

```python
# src/synapse/application/innovation.py (PROPOSED)

class InnovationConcept(dict):
    """A generated application concept.
    Keys: id, purpose, target_users, components[],
          integration_mechanism, potential_benefit,
          uncertainties[], evidence_refs[], combination_basis,
          generation_method, hypothesis_id, request_id
    """

class InnovationCritique(dict):
    """A structured critique of an innovation concept.
    Keys: concept_id, feasibility_score, evidence_coverage,
          constraint_conflicts[], alternatives[], failure_modes[],
          novelty_score, novelty_caveat, novelty_notes[],
          overall_recommendation, critique_method, evidence_refs[]
    """

class ExperimentPlan(dict):
    """A planned experiment for a concept/hypothesis.
    Keys: id, hypothesis_id, protocol, baseline, metrics{},
          success_criteria[], resource_estimate, evidence_requirements[],
          execution_mode, safety_limits{}, cost_limits{}
    """

class ObservationRecord(dict):
    """An immutable observation captured during experiment execution.
    Keys: id, experiment_id, outcome (supporting|contradicting|inconclusive),
          metrics{}, artifact_refs[], notes, captured_at, provenance
    """

class EvidenceFeedbackResult(dict):
    """The result of assessing an observation and transitioning a hypothesis.
    Keys: hypothesis_id, experiment_id, observation_id,
          prior_hypothesis_status, updated_hypothesis_status,
          prior_epistemic_state, updated_epistemic_state,
          evidence_delta_id, assessment_method,
          evidence_refs[], applicable_conditions[],
          transition_reason
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

### 4.4 Epistemic isolation of hypothesized relationships (NEW — Correction B)

Per Correction B: *"Hypothesized relationships must not contaminate
established knowledge or ordinary retrieval results."*

#### 4.4.1 The isolation invariant

`RelationshipRow(origin="hypothesized")` rows (created by G05-T03 to
model proposed combinations) MUST NOT appear in:
- `hybrid_retrieve()` results (G04-T01) — the public retrieval endpoint.
- `find_related_entities()` / `find_capabilities()` / `find_dependencies()`
  / `find_alternatives()` / `find_limitations()` results (G03-T03) —
  unless the caller explicitly requests `origin="hypothesized"`.
- `analyze_gap()` evidence collection (G04-T02C) — hypothesized
  relationships are not evidence.
- `answer_query()` findings (G04-T03) — hypothesized relationships
  are not documented facts.

#### 4.4.2 Enforcement strategy

The existing `RelationshipRow.origin` column (String(32), default
`"explicit"`) and the `ix_relationships_origin_verification` index
support efficient filtering. The enforcement happens at the **query**
layer, not the schema layer:

1. **G04-T01 `hybrid_retrieve`**: G05-T01 will verify that the
   retrieval SQL filters `RelationshipRow.origin != 'hypothesized'`
   (or equivalently, `origin IN ('explicit', 'derived')`). If the
   current G04-T01 code does not already filter this, G05-T01 will
   add the filter as a minimal correction (with a regression test).
2. **G03-T03 RelationshipService**: the existing `find_relationships()`
   function accepts an `origin` parameter. G05-T01 will document that
   callers who want established knowledge must pass `origin="explicit"`
   (or omit `origin` and filter post-hoc). The default behavior of
   `find_related_entities()` (which does NOT filter by origin) will
   be reviewed in G05-T01; if it returns hypothesized relationships,
   a minimal correction will be applied.
3. **G05 innovation endpoints**: the innovation generation endpoints
   (T03, T04) explicitly query `origin='hypothesized'` rows to
   surface proposed combinations. These rows are NEVER returned by
   the G04 retrieval/reasoning endpoints.

#### 4.4.3 Verification

G05-T01 acceptance criterion: "`hybrid_retrieve()` does not return
`RelationshipRow(origin='hypothesized')` rows." This is verified by
a regression test that seeds a hypothesized relationship and confirms
it is absent from retrieval results.

If the current G04-T01 code already filters by origin, no correction
is needed. If it does not, G05-T01 applies the smallest safe
correction (a single `WHERE origin != 'hypothesized'` clause) with a
regression test. **No G05-P00C code change is made** — this is a
planning decision that G05-T01 will implement and verify.

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
        "integration_mechanism": "arxiv_search discovers candidate papers; trafilatura extracts text and metadata; the extracted fragments feed into the knowledge-extraction pipeline which produces claims with source spans.",
        "potential_benefit": "Reduces manual literature triage time by surfacing relevant papers with structured evidence; the benefit is a hypothesis to be measured by experiment G05-T05, not a verified claim.",
        "uncertainties": [
          "arxiv API rate limits under heavy load",
          "trafilatura accuracy on non-English papers",
          "whether the combination produces higher precision than either component alone"
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

**Note on `potential_benefit`**: per the additional consistency checks,
numerical benefit claims (e.g., "reduces time by 40%") are NOT
included as assertions. Benefits are stated as hypotheses to be
measured by experiment, with explicit language marking them as
targets rather than verified outcomes.

**Quality gates enforced:**
- Every `components[].entity_id` must exist in the DB.
- Every `evidence_refs[]` must resolve to an existing fragment.
- Every concept must carry at least one `uncertainty`.
- `generation_method` must be `"deterministic"` or `"model_assisted"`.

#### POST /api/v1/innovations/{id}/critique

**Note**: the path parameter is `{id}` (not `{innovation_id}`), matching
the actual placeholder route definition in `src/synapse/api/v1/router.py`
line 81: `"/innovations/{id}/critique"`.

**Request:**
```json
{
  "critique_depth": "full",
  "limit": 20
}
```
(The `id` is passed in the URL path, not the body.)

**Response (200, Envelope):**
```json
{
  "data": {
    "concept_id": "innov-...",
    "feasibility_score": 0.75,
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
    "novelty_score": 0.60,
    "novelty_caveat": "graph uniqueness is necessary but not sufficient; market/technical novelty requires external validation",
    "novelty_notes": [],
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

#### POST /api/v1/experiments/{id}/execute

**Note**: the path parameter is `{id}` (not `{experiment_id}`), matching
the actual placeholder route definition in `src/synapse/api/v1/router.py`
line 95: `"/experiments/{id}/execute"`.

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

#### GET /api/v1/experiments/{id}

**Note**: the path parameter is `{id}` (not `{experiment_id}`),
matching the actual placeholder route definition in
`src/synapse/api/v1/router.py` line 101: `"/experiments/{id}"`.

Returns the experiment + its result (if any) + linked hypothesis.

#### GET /api/v1/hypotheses/{id}/evidence-deltas

**Note**: the path parameter is `{id}` (not `{hypothesis_id}`),
matching the actual placeholder route definition in
`src/synapse/api/v1/router.py` line 107: `"/hypotheses/{id}/evidence-deltas"`.

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
| 3 | Zero hypothesis-as-fact promotion | Every generated concept's `hypothesis_id` must point to a `ClaimRow(epistemic_state="hypothesized")`; never `"supported"`, `"verified"`, or any non-hypothesized state at creation time. |
| 4 | Zero unsupported architecture claims | Every "component X provides capability Y" claim must trace to a `RelationshipRow(predicate="PROVIDES")` with `evidence_refs` AND `origin!="hypothesized"`, OR be explicitly marked `origin="hypothesized"` (and filtered out of ordinary retrieval). |
| 5 | Every concept carries ≥1 uncertainty | Concepts without uncertainties are rejected (a concept with no uncertainties is overclaiming). |
| 6 | Every critique cites evidence | `feasibility_score`, `evidence_coverage`, and `novelty_score` must cite the `evidence_refs[]` used to compute them. |
| 7 | No experiment auto-promotes to VERIFIED | `EvidenceFeedbackResult.updated_epistemic_state` may be `SUPPORTED` / `DISPUTED` / `REJECTED`, but NEVER `VERIFIED` (which is not even an `EpistemicState` value — it exists only in `VerificationState`, a different enum). `updated_hypothesis_status` may be `SUPPORTED` / `WEAKENED` / `REJECTED`, never a VERIFIED-equivalent. |
| 8 | Every belief change is audited | An `EvidenceDelta` audit row is written for every `EvidenceFeedbackResult`. Prior state is preserved. |
| 9 | Dry-run is NOT evidence | A successful dry-run validation produces NO `ObservationRecord`, NO `EvidenceDelta`, and NO hypothesis-state transition. Dry-run only validates the experiment plan's structure. |
| 10 | Every concept explains integration mechanism + potential benefit | A concept with empty `integration_mechanism` or `potential_benefit` is rejected. Listing components without explaining how they work together is overclaiming. (Correction A) |
| 11 | Graph uniqueness ≠ market novelty | `novelty_score` reflects graph-uniqueness only; `novelty_caveat` explicitly states this is structural, not market validation. (Correction A) |
| 12 | Hypothesized relationships are epistemically isolated | `RelationshipRow(origin="hypothesized")` never appears in `hybrid_retrieve()`, `analyze_gap()`, or `answer_query()` results. (Correction B, §4.4) |
| 13 | No fixed-count auto-promotion | Hypothesis-state transitions use evidence quality, independence, applicability, and contradiction handling — NOT a fixed count of supporting experiments. (Correction C) |

### 6.2 Evidence-feedback policy (corrected — Correction C)

Per mission §6 (Evidence Feedback): *"Design how experiment results can
update knowledge and confidence without automatically converting
hypotheses into verified facts."* Per Correction C: *"Separate dry-run
validation, sandbox/actual execution, observation capture, evidence
assessment, and hypothesis-state transition. A successful dry-run is
NOT evidence. Do not automatically promote hypotheses based only on a
fixed number of supporting experiments. Use evidence quality,
independence, applicability, and contradiction handling. Verify all
proposed transitions against the existing frozen `Hypothesis` and
`EvidenceDelta` domain contracts. Do not invent incompatible states.
Never auto-promote to VERIFIED."*

#### 6.2.1 Five distinct stages (Correction C)

The evidence-feedback pipeline is separated into 5 stages that must
not be conflated:

| Stage | What it does | What it does NOT do |
|-------|-------------|--------------------|
| **1. Dry-run validation** | Validates the experiment plan's structure (hypothesis exists, protocol is non-empty, metrics are defined, execution_mode is `dry_run` or `sandbox`). Returns a `dry_run_validation` result with `valid: bool` and `issues: list[str]`. | Does NOT execute the experiment, does NOT capture observations, does NOT transition the hypothesis state, does NOT emit an `EvidenceDelta`. |
| **2. Sandbox / actual execution** | Executes the experiment in `sandbox` mode (isolated environment) or, if explicitly authorized, `local`/`networked`/`production` mode (each requiring `approved_by`). Produces an `ObservationRecord` with `outcome`, `metrics`, `artifact_refs`. | Does NOT assess evidence quality; does NOT transition the hypothesis state; does NOT emit an `EvidenceDelta`. The observation is raw data. |
| **3. Observation capture** | Persists the `ObservationRecord` as an immutable `ClaimRow` with `subject_ref=<experiment entity>` + `evidence_refs[]` pointing to fragments produced by the experiment. For dry-run, NO observation is captured (dry-run produces no evidence). | Does NOT assess the observation; does NOT transition the hypothesis state. |
| **4. Evidence assessment** | Evaluates the observation's quality, independence, applicability, and contradiction handling. Produces an `EvidenceAssessment` with `quality`, `independence`, `applicability_match`, `contradiction_handling`. | Does NOT transition the hypothesis state; produces only the assessment that the transition rule will consume. |
| **5. Hypothesis-state transition** | Applies the transition rule (§6.2.3) using the assessment. If the rule permits a transition, emits an immutable `EvidenceDelta` audit row and updates the hypothesis's `HypothesisStatus`. | Does NOT auto-promote to VERIFIED; does NOT use a fixed count of supporting experiments; does NOT transition if the assessment's `applicability_match` is false. |

#### 6.2.2 Two distinct state machines (verified against frozen contracts)

Per Correction C: *"Verify all proposed transitions against the existing
frozen `Hypothesis` and `EvidenceDelta` domain contracts. Do not invent
incompatible states."*

The G05 evidence-feedback policy operates on **two distinct state
machines** that must not be conflated:

**State machine A — `HypothesisStatus`** (8 states, frozen in
`domain/hypothesis.py`):
`PROPOSED`, `UNDER_REVIEW`, `TESTABLE`, `EXPERIMENT_RUNNING`,
`SUPPORTED`, `WEAKENED`, `REJECTED`, `SUPERSEDED`.

This governs the hypothesis **lifecycle** — what stage of testing the
hypothesis is in. G05-T06 transitions this state.

**State machine B — `EpistemicState`** (5 states, frozen in
`domain/_base.py`):
`SUPPORTED`, `INFERRED`, `HYPOTHESIZED`, `DISPUTED`, `REJECTED`.

This governs the **epistemic weight** of a claim — how much evidence
supports it. `EvidenceDelta.prior_state` and `updated_state` are typed
`EpistemicState`. G05-T06 emits `EvidenceDelta` records whose
`prior_state`/`updated_state` are `EpistemicState` values.

**Critical**: `VERIFIED` is NOT an `EpistemicState` value. It exists
only in `VerificationState` (a different enum for relationship
verification). Therefore, no `EvidenceDelta` can ever have
`updated_state="verified"` — the Pydantic validator on
`EvidenceDelta.updated_state: EpistemicState` would reject it.

#### 6.2.3 Transition rules (deterministic, no fixed-count promotion)

Per Correction C: *"Do not automatically promote hypotheses based only
on a fixed number of supporting experiments. Use evidence quality,
independence, applicability, and contradiction handling."*

The transition rule consumes the `EvidenceAssessment` from stage 4:

```
For a hypothesis H in HypothesisStatus S, given an EvidenceAssessment A
with quality Q, independence I, applicability_match AM, and
contradiction_handling CH:

  Rule 1 (applicability gate):
    If AM == False:
      → NO TRANSITION (the experiment's context does not match the
        hypothesis's validity_conditions; the observation is
        inapplicable).

  Rule 2 (contradiction handling — CONTESTED observations):
    If the observation's outcome is "contradicting" AND CH includes
    applicable contradictions:
      If S == EXPERIMENT_RUNNING → REJECTED  (requires evidence_refs)
      If S == TESTABLE → WEAKENED
      If S == SUPPORTED → WEAKENED
      If S == WEAKENED → REJECTED  (requires evidence_refs)

  Rule 3 (supporting observations — quality-gated, NOT count-gated):
    If the observation's outcome is "supporting" AND Q >= "sufficient"
    AND I >= 2 (≥2 independent origins per PRB-04's intent — but see
    note below on PRB-04's current conservative default):
      If S == PROPOSED → TESTABLE  (one quality supporting observation
        suffices to move from PROPOSED to TESTABLE)
      If S == TESTABLE → SUPPORTED  (requires quality Q >= "sufficient"
        AND independence I >= 2 — i.e., supporting evidence from
        ≥2 independent origins, NOT just "2 supporting experiments")
      If S == WEAKENED → SUPPORTED  (same quality + independence bar)

  Rule 4 (inconclusive observations):
    If the observation's outcome is "inconclusive":
      → NO TRANSITION (inconclusive observations do not change belief).

  Rule 5 (VERIFIED prohibition):
    No rule may produce updated_state == VERIFIED. The Pydantic
    validator on EvidenceDelta.updated_state: EpistemicState enforces
    this at the type level (VERIFIED is not a valid EpistemicState).
```

**Key correction from G05-P00**: the original plan's rule
"TESTABLE + supporting × 2 → SUPPORTED" was a fixed-count
auto-promotion, which Correction C explicitly forbids. The corrected
Rule 3 uses evidence **quality** (`Q >= "sufficient"`) AND evidence
**independence** (`I >= 2`) — not a count of experiments. A single
high-quality, multi-origin supporting observation can promote
TESTABLE → SUPPORTED; conversely, any number of low-quality or
single-origin supporting observations will NOT promote.

**Note on PRB-04**: the current `_count_independent_primary_origins`
function (G03-T04) always returns 1 (conservative default per PRB-04).
This means Rule 3's `I >= 2` condition is currently unreachable — the
TESTABLE → SUPPORTED transition is effectively blocked until PRB-04 is
resolved. **This is intentional and conservative**: G05 does NOT
weaken PRB-04. The plan documents this dependency: G05-T06's
TESTABLE → SUPPORTED transition is conditional on a future PRB-04
resolution. Until then, hypotheses can reach TESTABLE but not
SUPPORTED via experiments alone.

#### 6.2.4 Every transition emits an `EvidenceDelta`

For every Rule 2 or Rule 3 transition, G05-T06 writes an
`AuditEventRow(event_type="evidence.delta", target_id=<hypothesis
entity>, payload=<EvidenceDelta dict>)`. The `EvidenceDelta` includes:
- `prior_state`: the `EpistemicState` of the hypothesis's claim BEFORE
  the transition.
- `updated_state`: the `EpistemicState` AFTER the transition (never
  `VERIFIED`).
- `observation`: human-readable description of the observation.
- `evidence_refs[]`: supporting AND contradicting fragments.
- `experiment_id`: the experiment that produced the observation.
- `applicable_conditions[]`: the context in which the observation
  applies (matched against `validity_conditions`).

The `EvidenceDelta` Pydantic validators enforce:
- `_must_change_state`: `prior_state != updated_state` (no no-op deltas).
- `_rejected_requires_contradicting_evidence`: `updated_state == REJECTED`
  requires non-empty `evidence_refs[]`.

These are the **existing frozen validators** — G05 does NOT modify them.

### 6.3 Provenance preservation

Every generated concept, critique, experiment, observation, and
feedback result carries:
- `evidence_refs[]`: traceable to `EvidenceFragmentRow` → `SourceSpanRow` → `SourceRow.canonical_uri`
- `request_id`: per-request tracing
- `policy_version`: `"deterministic-v2"` (unchanged)
- `generation_method`: `"deterministic"` or `"model_assisted"`
- `assessment_method`: for evidence assessments — `"quality_independence_applicability"` (never `"count"`)

External consumers (G07 UI) can follow the chain from any concept,
experiment, or belief change back to the original source text.

---

## 7. Evaluation strategy using realistic innovation scenarios

### 7.1 Golden innovation dataset

G05-T03 extends the G04-T04 golden dataset (currently 14 cases) with
**6 innovation-generation cases** (bumping `GOLDEN_DATASET_VERSION`
to `2.0.0`). Per the additional consistency checks, each case tests
**meaningful innovation quality**, not merely whether a template
returns a non-empty result.

| Case ID | Problem domain | Expected quality assertions |
|---------|---------------|------------------------------|
| G05-I01 | "AI research assistant" | ≥1 concept citing cap-source-discovery + cap-content-extraction; `integration_mechanism` explains how arxiv_search feeds trafilatura; `potential_benefit` is stated as a hypothesis, not a verified claim; ≥2 uncertainties including rate-limit and accuracy. |
| G05-I02 | "Lightweight retrieval tool" | ≥1 concept citing AltTool (REPLACES); `novelty_score` reflects graph uniqueness; `novelty_caveat` explicitly states graph uniqueness ≠ market novelty. |
| G05-I03 | "Vector-search-free evidence verification" | ≥1 concept that respects ent-no-vector LIMITS cap-evidence-verification; `constraint_conflicts[]` includes the LIMITS edge; concept does NOT claim to overcome the constraint. |
| G05-I04 | "Multi-provider capability comparison" | ≥1 concept citing both ent-synapse and ent-arxiv PROVIDES cap-source-discovery; concept does NOT attribute ent-arxiv's evidence to ent-synapse (G04-T02C preserved). |
| G05-I05 | "Gap-filling: structured knowledge extraction" | ≥1 concept that explicitly addresses cap-knowledge-extraction's NOT_EVIDENCED status; concept's `uncertainties[]` includes the gap; concept does NOT claim the gap is closed. |
| G05-I06 | "Constraint-aware: no LLM" | ≥1 concept whose components are all deterministic; `generation_method="deterministic"`; concept does NOT claim LLM-assisted capabilities. |

### 7.2 Innovation metrics

| Metric | Implementation |
|--------|---------------|
| Component validity | Fraction of `components[].entity_id` that exist in DB. |
| Evidence coverage | Fraction of `evidence_refs[]` that resolve to existing fragments. |
| Integration-mechanism presence | Fraction of concepts with non-empty `integration_mechanism`. (Target: 100%.) |
| Potential-benefit-as-hypothesis | Fraction of concepts whose `potential_benefit` is phrased as a hypothesis (e.g., "reduces time by surfacing...") rather than a verified numerical claim. (Target: 100%.) |
| Novelty (structural, explicitly labeled) | `novelty_score` = graph-uniqueness fraction (1 − existing_combinations / total_combinations). **Always accompanied by `novelty_caveat` stating graph uniqueness ≠ market novelty.** |
| Uncertainty honesty | Fraction of concepts that carry ≥1 uncertainty (target: 100%). |
| Critique completeness | Fraction of critiques that cite evidence for every score. |
| Feedback auditability | Fraction of belief changes that emit an `EvidenceDelta` (target: 100%). |
| Dry-run isolation | Fraction of dry-run executions that produce NO `ObservationRecord` and NO `EvidenceDelta` (target: 100%). |

### 7.3 Quality gates (extends G04-T04)

The 6 G04-T04 quality gates apply unchanged. G05 adds innovation-
specific gates 7–13 (see §6.1 for the full table):

| # | Gate | Enforcement |
|---|------|-------------|
| 7 | Zero unsupported architecture claims | `RelationshipRow(origin="hypothesized")` for proposed combinations; never `origin="explicit"` without evidence. |
| 8 | Every concept has ≥1 uncertainty | Reject concepts with empty `uncertainties[]`. |
| 9 | No VERIFIED promotion | `EvidenceFeedbackResult.updated_epistemic_state` is never `VERIFIED` (not even an `EpistemicState` value); `updated_hypothesis_status` never reaches a VERIFIED-equivalent. |
| 10 | Every concept explains integration mechanism + potential benefit | Reject concepts with empty `integration_mechanism` or `potential_benefit`. (Correction A) |
| 11 | Graph uniqueness ≠ market novelty | `novelty_score` always accompanied by `novelty_caveat`. (Correction A) |
| 12 | Hypothesized relationships epistemically isolated | `origin="hypothesized"` rows never appear in ordinary retrieval. (Correction B) |
| 13 | No fixed-count auto-promotion | Transitions use quality + independence + applicability, not a count. (Correction C) |

### 7.4 Demonstration scenario

Per mission §"Important": *"The system must not merely produce
generic application ideas. It must explain why a proposed combination
is useful, how its components work together, what evidence supports
it, and what still needs experimental validation."*

The G05-T03 demonstration runs:

1. **Problem**: "Build an AI research assistant that summarizes arxiv papers."
2. **Knowledge Combination (T01)**: retrieves cap-source-discovery (ent-arxiv PROVIDES), cap-content-extraction (ent-trafilatura PROVIDES), cap-evidence-verification (ent-synapse PROVIDES).
3. **Opportunity Discovery (T02)**: identifies gap cap-knowledge-extraction (NOT_EVIDENCED).
4. **Innovation Generation (T03)**: produces concept "arxiv→trafilatura→synapse pipeline" with `integration_mechanism` (how the components connect), `potential_benefit` (stated as a hypothesis), `evidence_refs`, `uncertainties` (rate limits, accuracy, gap in cap-knowledge-extraction).
5. **Critique (T04)**: feasibility (all components exist), `novelty_score` (graph uniqueness, with `novelty_caveat`), `evidence_coverage`, `constraint_conflicts` (ent-no-vector LIMITS cap-evidence-verification), `failure_modes` (rate_limit_exceeded, missing cap-knowledge-extraction).
6. **Experiment Planning (T05)**: plans experiment "Run arxiv_search for 50 papers; measure retrieval precision."
7. **Evidence Feedback (T06, dry-run)**: dry-run validation only — NO observation, NO `EvidenceDelta`, NO hypothesis-state transition. The hypothesis remains PROPOSED.

The demonstration prints every stage's structured output and proves
the full chain from problem → concept → critique → experiment →
feedback is traceable and evidence-grounded. The dry-run stage
explicitly shows that NO evidence was produced.

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
6. **Hypothesized relationships are epistemically isolated**: `hybrid_retrieve()` does NOT return `RelationshipRow(origin='hypothesized')` rows. Verified by a regression test that seeds a hypothesized relationship and confirms it is absent from retrieval results. (Correction B, §4.4)
7. G01–G04 regression intact.

**STOP gate**: STOP after T01. Await T02 authorization.

**Estimated footprint**: ~500 LOC (source + tests, +50 LOC for the
isolation regression test).

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
3. `novelty_score` reflects graph-uniqueness only; `novelty_caveat` explicitly states graph uniqueness ≠ market novelty. (Correction A)
4. `constraint_conflicts[]` lists LIMITS/CONTRADICTS edges.
5. `failure_modes[]` includes CONTESTED claims and missing dependencies.
6. `overall_recommendation` is one of: `testable`, `testable_with_caveats`, `needs_more_evidence`, `rejected`.
7. Every concept's `integration_mechanism` and `potential_benefit` are non-empty (quality gate 10). (Correction A)
8. G01–G04-T03 regression intact.

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
1. Dry-run validation produces NO `ObservationRecord`, NO `EvidenceDelta`, and NO hypothesis-state transition. (Correction C, gate 9)
2. Sandbox execution produces an `ObservationRecord` but does NOT directly transition the hypothesis state — the 5-stage pipeline (dry-run → execution → observation capture → evidence assessment → transition) is enforced. (Correction C)
3. `supporting` observation with quality ≥ "sufficient" AND applicability_match=True moves PROPOSED → TESTABLE (one quality observation suffices). (Correction C, no fixed-count)
4. `contradicting` observation moves TESTABLE → WEAKENED; EXPERIMENT_RUNNING → REJECTED (requires evidence_refs).
5. **No transition to VERIFIED** — invariant enforced at the Pydantic type level (`EpistemicState` has no VERIFIED value). (Correction C)
6. **No fixed-count auto-promotion** — TESTABLE → SUPPORTED requires evidence quality + independence (≥2 independent origins), NOT a count of supporting experiments. (Correction C, gate 13)
7. Every belief change emits an `EvidenceDelta` audit row.
8. `GET /api/v1/hypotheses/{id}/evidence-deltas` returns the ordered audit trail.
9. Contradictions preserved (both supporting + contradicting refs in the delta).
10. Only `dry_run` and `sandbox` execution modes accepted; `local`/`networked`/`production` return 422.
11. G01–G04-T05 full regression intact.

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
| G05-T01 | 7 | STOP. Await T02 authorization. |
| G05-T02 | 6 | STOP. Await T03 authorization. |
| G05-T03 | 7 | STOP. Await T04 authorization. |
| G05-T04 | 8 | STOP. Await T05 authorization. |
| G05-T05 | 6 | STOP. Await T06 authorization. |
| G05-T06 | 11 | STOP. Await G05 closure review authorization. |

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
| Hypothesis auto-promotion to VERIFIED | Low | Critical | `EvidenceFeedbackResult.updated_epistemic_state` is restricted to non-VERIFIED values (§6.2); quality gate 9 enforces. `VERIFIED` is not even an `EpistemicState` value, so the Pydantic type system rejects it. |
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
- Never auto-promotes hypotheses to VERIFIED (impossible — `VERIFIED` is not an `EpistemicState` value).
- Never uses fixed-count auto-promotion (transitions use evidence quality + independence + applicability).
- Emits immutable `EvidenceDelta` audit rows for every belief change.
- Epistemically isolates hypothesized relationships from ordinary retrieval.
- Defines stable API contracts for G07 (UI).

**Estimated total**: ~6,260 LOC across 6 tasks (source + tests + reports).

**No G05 implementation begins without explicit per-task authorization.**

---

## 14. Corrections summary and implications for T01–T06 (G05-P00C)

This section summarizes the G05-P00C corrections and their per-task
impact, per the mission's requirement: *"Include a brief section
summarizing the corrections and their implications for T01–T06."*

### 14.1 Correction A — Genuine Innovation

| Change | Implication for tasks |
|--------|---------------------|
| Distinguish 5 concerns (candidate discovery, exploratory generation, evidence-grounded evaluation, novelty assessment, experiment design) | T01 = concern 1; T03 = concern 2; T04 = concerns 3+4; T02/T05/T06 = concern 5. Each task's scope is now narrower and clearer. |
| 6 templates are baselines, not the complete capability | T03 explicitly labels templates as baselines; the optional model-assisted path is documented as a bounded interface, not the primary path. |
| Every concept must explain `integration_mechanism` + `potential_benefit` | T03 adds quality gate 10; T04 verifies both fields are non-empty in critique. |
| Graph uniqueness ≠ market novelty | T04 adds `novelty_caveat` field; `novelty_score` is explicitly labeled structural. |
| Optional model-assisted creative exploration behind a bounded interface | T03 may optionally use `ModelAdapter` (not implemented in G05-P00C); the deterministic path is always available. |

### 14.2 Correction B — Persistence and Epistemic Isolation

| Change | Implication for tasks |
|--------|---------------------|
| Verified storage mapping against actual frozen domain contracts + ORM schema | T01–T06 use the verified mapping (§4.1.2); no new table, no new column, no new migration. |
| Distinct stable identities for innovations, hypotheses, experiments, observations | Each gets a distinct `EntityRow.id` or `ClaimRow.id`; T03/T05/T06 create and reference these correctly. |
| One hypothesis → many experiments | T05 verifies `ClaimRow.subject_ref` supports 1:N; a regression test confirms multiple experiments can reference one hypothesis. |
| Hypothesized relationships must not contaminate retrieval | T01 adds acceptance criterion 6 + a regression test verifying `hybrid_retrieve()` excludes `origin='hypothesized'` rows. |
| Prefer minimal reuse; no new table unless an invariant demonstrably cannot be preserved | §4.1.3 documents that all 6 invariants are preserved by the existing schema; no new table is proposed. |

### 14.3 Correction C — Experiments and Evidence Feedback

| Change | Implication for tasks |
|--------|---------------------|
| Separate 5 stages (dry-run, execution, observation capture, evidence assessment, transition) | T06 implements the 5-stage pipeline; each stage has a distinct function and produces a distinct artifact. |
| Dry-run is NOT evidence | T06 acceptance criterion 1: dry-run produces NO `ObservationRecord`, NO `EvidenceDelta`, NO transition. |
| No fixed-count auto-promotion | T06 transition rules (§6.2.3) use evidence quality + independence + applicability, not a count. The original "× 2 → SUPPORTED" rule is removed. |
| Use evidence quality, independence, applicability, contradiction handling | T06's `EvidenceAssessment` stage produces `quality`, `independence`, `applicability_match`, `contradiction_handling`; the transition rule consumes these. |
| Verify all transitions against frozen `Hypothesis` + `EvidenceDelta` contracts | §6.2.2 documents the two distinct state machines (`HypothesisStatus` 8-state vs `EpistemicState` 5-axis); T06 transitions `HypothesisStatus` and emits `EvidenceDelta` with `EpistemicState` values — never conflated. |
| Never auto-promote to VERIFIED | Enforced at the Pydantic type level: `EpistemicState` has no `VERIFIED` value; `EvidenceDelta.updated_state: EpistemicState` rejects `VERIFIED` at validation time. |

### 14.4 Additional consistency checks

| Change | Implication for tasks |
|--------|---------------------|
| Remove unsupported numerical benefit claims | T03 API example uses `potential_benefit` phrased as a hypothesis; no "~40%" assertions. |
| Verify endpoint paths match actual placeholder definitions | T03/T04/T05/T06 use `{id}` (not `{innovation_id}`/`{experiment_id}`/`{hypothesis_id}`), matching `router.py` lines 81/95/101/107. |
| G05 evaluation cases test meaningful innovation quality, not merely non-empty template output | T03 golden cases (§7.1) assert `integration_mechanism`, `potential_benefit`-as-hypothesis, `novelty_caveat`, `constraint_conflicts`, and `uncertainties` — not just "≥1 concept returned". |
| Preserve G06/G07 dependencies | §12 unchanged; G06 consumes hypothesis lifecycle + audit trail; G07 consumes stable API contracts. |
| Keep scope lean | §11 LOC estimate unchanged (~6,260); T01 +50 LOC for the isolation regression test; T06 +0 LOC (the 5-stage split is a structural clarification, not additional code). |

### 14.5 Six-task plan preserved

The six-task structure (T01–T06) is **preserved**. No task is added,
removed, merged, or split. The corrections refine each task's
acceptance criteria and clarify the architectural principles, but
do not change the task boundaries or the STOP-gate discipline.

---

*End of G05 Innovation & Experiments Implementation Plan (G05-P00C
revised) — STOP for architectural approval.*
