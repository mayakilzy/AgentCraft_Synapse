# G06 — Future Intelligence Implementation Plan

**Status**: PLANNING ONLY — awaiting architecture review
**Task**: G06-P00 — Architecture & Implementation Planning
**Starting SHA**: `3d343b84e70aeea3f65c67ff947c61d94b12c785` (G05-T07 closure)
**Date**: 2026-10-11
**Developer**: GLM
**Architecture Reviewer**: ChatGPT

> **Execution restriction**: This document is a plan. No production code,
> no migrations, no domain-contract changes, no activated API endpoints
> are produced by G06-P00. Implementation begins only after ChatGPT
> approves the plan and authorizes G06-T01.

---

## 1. Executive summary and intended user value

### 1.1 What G06 is

G06 (Future Intelligence) is the stage where Synapse stops being only a
record of what is known and becomes able to reason about **what could
plausibly come next** in the technical domains it has ingested.

It produces **structured, explainable future scenarios** — not
predictions, not forecasts, and never certainties. Each scenario is
traceable to the evidence, assumptions, dependencies, and uncertainties
that went into it, and is explicitly labeled with one of five horizon
classes (F0–F4) drawn from the existing `HorizonClass` enum.

### 1.2 Intended user value

A practitioner using Synapse today can ask "what is known about X?" and
"what innovations could combine X with Y?". After G06 they will also be
able to ask:

> "Given what Synapse knows, which emerging capabilities look like they
> are about to open a real opportunity, which evidence supports that,
> which assumptions must hold, which dependencies are missing, and which
> experiment should we run **now** to find out whether the opportunity
> is real?"

Concretely, G06 delivers:

1. **Future-signal identification** — a deterministic scan over the
   existing knowledge base that surfaces capabilities, entities, and
   relationships whose evidence footprint has changed recently or whose
   gap classification suggests an emerging opening. No fabricated
   trends; no growth rates invented from a single snapshot.
2. **Scenario construction** — a deterministic composer that turns a
   future signal + supporting evidence + assumptions + missing
   dependencies into a persisted `Scenario` record with an F0–F4
   horizon class. The existing `Scenario` domain record and its
   validators (F4 requires critique; non-F0 requires missing
   dependencies) are reused unchanged.
3. **Scenario evaluation** — a deterministic assessor that scores a
   scenario's evidence coverage, dependency readiness, contradiction
   exposure, and assumption count. The result is a structured report,
   not a single opaque number.
4. **Scenario revision** — an explicit, traceable reassessment flow:
   when new evidence arrives (new fragment, new experiment result, new
   relationship), the scenario is re-evaluated and a `ScenarioRevision`
   audit event is emitted. Old revisions are preserved.
5. **G05 linkage** — scenarios can propose experiments that would
   reduce the scenario's uncertainty, using the existing
   `plan_experiment()` flow. Scenarios can also annotate G05 innovation
   concepts with future-context risks and timing notes.

### 1.3 What G06 is NOT

- **Not a predictor.** G06 never assigns numerical probabilities to
  future outcomes. Horizon classes (F0–F4) are categorical labels of
  distance-from-established-evidence, not probability buckets.
- **Not a trend forecaster.** Synapse has no longitudinal acquisition
  history (sources are ingested at one point in time). G06 will not
  fabricate growth rates or adoption curves from a single snapshot.
- **Not autonomous.** G06 does not schedule background scans, does not
  auto-revise scenarios without an explicit caller, and does not take
  external actions.
- **Not a substitute for G04/G05.** G06 consumes the capability graph,
  the gap analyzer, the hypothesis lifecycle, and the experiment
  pipeline. It does not duplicate their reasoning.
- **Not LLM-dependent.** G06's core is deterministic. An optional
  model-assisted narrative adapter may be added behind a bounded,
  auditable interface, but it is never the source of evidence and its
  output is always labeled as model-generated.

---

## 2. Verified existing components and contracts

The following were verified by direct source inspection during G06-P00.
None are assumed; all are present in the codebase at the G05-T07
closure SHA.

### 2.1 Domain contracts (frozen — must not be modified by G06)

| Contract | File | G06 reuse |
|----------|------|-----------|
| `HorizonClass` enum (F0–F4) | `src/synapse/domain/_base.py:98-105` | The authoritative horizon classification. G06 uses it as-is. |
| `Scenario` record | `src/synapse/domain/scenario.py` | Reused unchanged. Already carries `current_evidence_refs`, `capabilities`, `assumptions`, `missing_dependencies`, `horizon_class`, `critique`, `prototype_plan`, `status`, `risks`, `uncertainty`. Validators: F4 requires `critique`; non-F0 requires `missing_dependencies`. |
| `ScenarioStatus` enum | `src/synapse/domain/scenario.py:18-24` | Reused: DRAFT → UNDER_CRITIQUE → TESTABLE → PROTOTYPED → RETIRED. |
| `Hypothesis` record + `HypothesisStatus` | `src/synapse/domain/hypothesis.py` | Reused: G06 scenarios may reference hypotheses (especially TESTABLE / SUPPORTED) as evidence anchors. |
| `EvidenceDelta` record | `src/synapse/domain/evidence_delta.py` | Reused: scenario revision history will emit `AuditEventRow(event_type="scenario.revision")` modeled on the same append-only audit pattern. |
| `EpistemicState` enum | `src/synapse/domain/_base.py:40-47` | Reused: `supported`, `inferred`, `hypothesized`, `disputed`, `rejected`. No `verified` — G06 never promotes a scenario to "verified future". |
| `Experiment`, `ExperimentExecutionMode` | `src/synapse/domain/experiment.py` | Reused: G06 can propose experiments via the existing `plan_experiment()` flow. |
| `EntityType` enum | `src/synapse/domain/_base.py:108-120` | Already includes `SCENARIO = "scenario"`. G06 persists scenarios as `EntityRow(kind="scenario")`. |

### 2.2 Application services (G06 consumes, does not duplicate)

| Service | File | G06 consumption |
|---------|------|-----------------|
| `hybrid_retrieve()` | `src/synapse/application/retrieval.py` | G06 signal identification uses retrieval to gather the evidence footprint of a candidate signal. |
| `classify_intent()` | `src/synapse/application/retrieval.py` | G06 does not reimplement intent classification. |
| `analyze_gap()` | `src/synapse/application/gap_analyzer.py` | G06 signal identification consumes gap classifications (`NOT_EVIDENCED`, `CONTESTED`, `CONSTRAINED`) as primary future-signal inputs. |
| `list_capabilities()`, `get_capability()` | `src/synapse/application/capability_registry.py` | G06 enumerates the capability graph to find emerging-capability candidates. |
| `combine_knowledge()` | `src/synapse/application/innovation.py` | G06 may reuse combination candidates as scenario component sets. |
| `discover_opportunities()` | `src/synapse/application/innovation.py` | G06 opportunities feed scenario construction (an opportunity is a candidate future signal). |
| `generate_innovations()` | `src/synapse/application/innovation.py` | G06 does NOT call this directly; G06 scenarios annotate existing innovations rather than regenerate them. |
| `compose_architecture()`, `critique_innovation()` | `src/synapse/application/innovation.py` | G06 scenario evaluation reuses the critique pattern (feasibility, evidence coverage, failure modes). |
| `plan_experiment()` | `src/synapse/application/experiment_planner.py` | G06 "propose experiment" delegates to this. |
| `assess_claim()` | `src/synapse/application/verification.py` | G06 uses claim verification states to weight scenario evidence. |
| `create_relationship()` | `src/synapse/application/relationship_service.py` | G06 may create `SCENARIO_DERIVES_FROM` / `SCENARIO_DEPENDS_ON` relationships (origin="derived"). |

### 2.3 Persistence structures (verified)

| Structure | File | G06 use |
|-----------|------|---------|
| `EntityRow` (kind="scenario") | `src/synapse/storage/models.py:202` | Scenarios persisted as entity rows with `attributes` JSON carrying the full scenario dict. Same pattern as G05 innovations (kind="project") and experiments (kind="experiment"). |
| `ClaimRow` | `src/synapse/storage/models.py:225` | A scenario may carry a backing hypothesis claim (epistemic_state="hypothesized"). |
| `RelationshipRow` | `src/synapse/storage/models.py:278` | Scenario → evidence / scenario → capability / scenario → experiment relationships. |
| `AuditEventRow` | `src/synapse/storage/models.py:102` | `event_type="scenario.revision"` for the append-only revision history. Same pattern as `evidence.delta`. |
| `EntityFingerprintRow` | `src/synapse/storage/models.py:312` | Scenario idempotency via PRB-03 `claim_fingerprint()` — same `SHA-256(kind + fingerprint)` mechanism used by innovations and experiments. |
| `EvidenceFragmentRow`, `SourceSpanRow` | `src/synapse/storage/models.py:159, 253` | Scenario `current_evidence_refs` point to these. |

### 2.4 API placeholders (already declared, G06 activates)

`src/synapse/api/v1/router.py:101-117` declares three 501 placeholders:

| Route | Method | G06 activation |
|-------|--------|----------------|
| `/api/v1/future/scenarios` | POST | G06-T02: create scenario |
| `/api/v1/future/scenarios/{scenario_id}` | GET | G06-T02: retrieve scenario |
| `/api/v1/future/scenarios/{scenario_id}/prototype-plan` | POST | G06-T03: propose prototype experiment |

G06 will also add (not yet declared as placeholders, but within the
`/future/` prefix already established):

| Route | Method | G06 task |
|-------|--------|----------|
| `/api/v1/future/signals` | POST | G06-T01: identify future signals |
| `/api/v1/future/scenarios/{scenario_id}/evaluate` | POST | G06-T03: evaluate scenario |
| `/api/v1/future/scenarios/{scenario_id}/revisions` | GET | G06-T04: revision history |

### 2.5 Test infrastructure (verified reusable)

- `tests/integration/conftest.py` (added in G05-T07) provides
  per-module isolated PostgreSQL databases. G06 tests will use the same
  pattern with no additional infrastructure.
- `tests/conftest.py` provides the per-test SQLite engine + FastAPI
  `app` + `client` fixtures.
- The PRB-03 `claim_fingerprint()` infrastructure is reused for
  scenario idempotency.

---

## 3. Exact F0–F4 horizon definitions from the repository

The authoritative definitions live in
`src/synapse/domain/_base.py:98-105`:

```python
class HorizonClass(StrEnum):
    """Future Intelligence horizon per Master Spec §13."""

    F0 = "F0"  # existing
    F1 = "F1"  # possible but uncommon
    F2 = "F2"  # near-term engineering
    F3 = "F3"  # research-dependent
    F4 = "F4"  # speculative
```

And in `src/synapse/domain/scenario.py:1-7`:

> Per Master Spec §13: Future Intelligence reuses capability networks
> and relationship services; produces F0..F4 scenarios. Each scenario
> includes evidence, assumptions, constraints, missing capabilities,
> risks, uncertainty, time-horizon class and minimum testable prototype.

### 3.1 Horizon semantics (binding interpretation for G06)

| Horizon | Meaning | Evidence requirement | Assumption load | Typical use |
|---------|---------|---------------------|-----------------|-------------|
| **F0** | **Existing** — the capability/configuration exists today in the knowledge base. | Supported by ≥1 verified claim. | None required. | "Today's state of the art." A scenario at F0 is really a structured snapshot, not a future. |
| **F1** | **Possible but uncommon** — the components exist and are documented, but the combination is not evidenced in the KB. | Each component must have ≥1 supporting claim. The *combination* has no evidence. | ≥1 assumption explaining why the combination is plausible. | "We could do this today, but nobody in our KB has." |
| **F2** | **Near-term engineering** — the components exist; the missing piece is engineering effort (no new research required). | Components supported. Missing dependencies must be **engineering** items (tooling, integration, packaging), not research questions. | ≥1 assumption about effort/timeline; ≥1 missing_dependency. | "Six months of engineering away." |
| **F3** | **Research-dependent** — at least one missing dependency is an open research question. | Components supported. ≥1 missing_dependency must be a research question (no known solution in KB). | ≥1 assumption about research outcome. | "Requires a breakthrough in X." |
| **F4** | **Speculative** — the scenario rests on assumptions that are not grounded in current evidence; the `critique` field is mandatory. | Evidence may be thin or indirect. | Multiple assumptions; the `critique` must enumerate risks and what would invalidate the scenario. | "What if …" — explicitly labeled as speculation. |

### 3.2 Horizon assignment rules (deterministic)

The horizon class of a constructed scenario is **derived from its
inputs**, not chosen freely by the caller:

1. If every `capability` has a SUPPORTED claim and every
   `missing_dependency` is empty → **F0**.
2. If every `capability` has a SUPPORTED claim, `missing_dependencies`
   is non-empty, and every missing dependency is classified as
   "engineering" (deterministic heuristic: the dependency entity exists
   in the KB with `kind` in {tool, technology, technique} and has a
   SUPPORTED claim, but the *integration* relationship is missing) →
   **F2**.
3. If any `missing_dependency` is classified as "research" (the
   dependency entity does not exist in the KB, or exists only with
   HYPOTHESIZED/INFERRED claims) → **F3**.
4. If the scenario's capabilities are each SUPPORTED but the
   combination has no evidence and no missing dependencies → **F1**.
5. If any capability lacks a SUPPORTED claim, or the caller explicitly
   requests speculation → **F4** (and the mandatory `critique` is
   populated by the caller or by the evaluation step).

The caller may **downgrade** a horizon (make it more speculative: F0 →
F1 → F2 → F3 → F4) but never **upgrade** it. The
`Scenario._non_f0_must_list_missing_deps` and
`Scenario._f4_requires_critique` validators enforce the constraints.

### 3.3 No time ranges

The horizons are **categorical**, not temporal. G06 does NOT assign
"3 months", "1 year", "5 years" to F0–F4. Time estimates require
longitudinal data Synapse does not have (§4.1).

---

## 4. Proposed G06 capabilities

Five capabilities, each mapped to a planning question (§3 of the
mission brief).

### 4.1 Capability A — Future Signal Identification (answers Q-A)

**Purpose**: scan the knowledge base for signals of emerging
technological change.

**Deterministic inputs** (no invented trends):

- **Gap classification changes**: `analyze_gap()` returns
  `NOT_EVIDENCED`, `CONTESTED`, `CONSTRAINED` entries. A capability
  that was `NOT_EVIDENCED` in one scan and later becomes
  `CONSTRAINED` (a dependency was added) is a positive signal.
  **However**, Synapse has no longitudinal acquisition history, so
  G06 does NOT compute "rate of change". It reports the *current* gap
  classification and lets the caller decide whether the signal is
  emerging.
- **New entity footprint**: entities whose `evidence_refs` count
  crossed a threshold (e.g., ≥3 fragments from ≥2 independent sources)
  since the last scan. Without longitudinal data, this is reported as
  a snapshot: "this capability now has N fragments from M sources".
- **Combination density**: capabilities that appear in many
  `combine_knowledge()` candidates are "centrally connected" — a
  structural signal of importance.
- **Contradiction exposure**: capabilities with `CONTESTED` gap
  classifications are signals of *active disagreement* (which is
  itself a signal that the field is moving).
- **Dependency shifts**: relationships added recently
  (`RelationshipRow.created_at` within a caller-supplied window) that
  introduce new PROVIDES / DEPENDS_ON / LIMITS edges.

**Output**: a `FutureSignalReport` dict per signal:

```python
{
    "signal_id": "sig-...",
    "kind": "new_capability" | "combination_density" |
            "contradiction_exposure" | "dependency_shift" |
            "gap_transition",
    "entity_id": "ent-..." | None,
    "capability_id": "cap-..." | None,
    "evidence_refs": ["frag-...", ...],
    "gap_classification": "not_evidenced" | "contested" | "constrained" | None,
    "combination_count": int,            # how many combine_knowledge candidates reference this
    "source_independence": int,          # distinct source_uri count
    "first_seen_at": "ISO8601",          # earliest evidence_refs.retrieved_at
    "last_seen_at": "ISO8601",           # latest evidence_refs.retrieved_at
    "snapshot_only": True,               # explicitly flag: no trend inferred
    "notes": "...",                      # deterministic human-readable summary
}
```

**Critical invariant**: `snapshot_only=True` on every signal. G06
never claims a trend. The `first_seen_at` / `last_seen_at` pair is
a factual observation of the evidence footprint, not a growth curve.

### 4.2 Capability B — Scenario Construction (answers Q-B)

**Purpose**: build a persisted `Scenario` record from a future signal
+ caller-supplied assumptions + automatically gathered evidence +
automatically identified missing dependencies.

**Inputs**:
- A `signal_id` from Capability A (or an explicit `capability_id` /
  `entity_id` from the caller).
- Caller-supplied `assumptions` (a list of strings; each must be
  non-empty).
- Caller-supplied `prototype_plan` (optional; if absent, the
  evaluator will propose one).
- Caller-supplied `horizon_class_override` (optional; may only
  downgrade per §3.2).

**Deterministic construction steps**:
1. Resolve the signal to its entity/capability.
2. Gather `current_evidence_refs` = all fragments cited by SUPPORTED
   claims about the entity/capability.
3. Gather `capabilities` = the entity's capability graph neighbors
   (via `RelationshipService`).
4. Gather `missing_dependencies` = `analyze_gap()` results for the
   capability, filtered to `NOT_EVIDENCED` and `CONSTRAINED` entries.
5. Derive `horizon_class` per §3.2 rules (unless downgraded by caller).
6. Construct the `Scenario` record; the Pydantic validators enforce
   F4-requires-critique and non-F0-requires-missing-deps.
7. Persist as `EntityRow(kind="scenario")` with attributes JSON.
8. Emit `AuditEventRow(event_type="scenario.created")`.

**Output**: the persisted scenario dict (same shape as
`_attributes_to_scenario`).

### 4.3 Capability C — Alternative Futures (answers Q-C)

**Purpose**: compare alternative futures for the same signal.

**Design**: **not a new enum**. Alternative futures are **multiple
Scenario records** that share a `signal_id` (or share
`current_evidence_refs`) but differ in `assumptions`,
`missing_dependencies`, or `horizon_class`. The caller constructs 2–N
scenarios via Capability B, then requests a comparison.

**Comparison dimensions** (deterministic):
- `evidence_overlap`: Jaccard similarity of `current_evidence_refs`.
- `assumption_divergence`: assumptions in A but not B, and vice versa.
- `dependency_divergence`: missing_dependencies in A but not B, and
  vice versa.
- `horizon_distance`: |horizon_index(A) - horizon_index(B)| where
  F0=0, F1=1, F2=2, F3=3, F4=4.
- `shared_capabilities`: intersection of `capabilities`.

**Output**: a `ScenarioComparison` dict. No artificial probabilities;
no "most likely" ranking. The caller sees the structural differences
and decides.

**Candidate planning patterns** (templates, not mandatory): the caller
may use these as seeds for assumption sets:
- "Expected continuation" — assume current capabilities remain
  available; no new research.
- "Accelerated adoption" — assume one missing_dependency becomes
  engineering-tractable (downgrade F3 → F2).
- "Delayed adoption" — assume one currently-SUPPORTED capability
  becomes CONSTRAINED.
- "Disruptive technical change" — assume a new capability appears
  (forces F4).
- "Constraint-driven alternative" — assume a LIMITS relationship
  tightens.

These are **documentation patterns**, not enum values. G06 does not
encode them in the domain.

### 4.4 Capability D — Scenario Evaluation (answers Q-D)

**Purpose**: produce a structured, deterministic evaluation of a
persisted scenario.

**Evaluation axes** (each a deterministic sub-score in [0.0, 1.0] or
a categorical label):

| Axis | Deterministic computation |
|------|---------------------------|
| **Evidence coverage** | `|current_evidence_refs ∩ KB_verified_fragments| / |current_evidence_refs|`. If 0 evidence refs → 0.0. |
| **Source independence** | `|distinct(source_uri) for ref in current_evidence_refs|`. Reported as a count, not normalized. |
| **Dependency readiness** | `|missing_dependencies that are "engineering" (F2-tractable)| / |missing_dependencies|`. 1.0 = all engineering; 0.0 = all research. |
| **Contradiction exposure** | `count(CONTESTED claims among current_evidence_refs)`. Reported as a count. |
| **Assumption load** | `|assumptions|`. Reported as a count; higher = more speculative. |
| **Assumption sensitivity** | For each assumption, check whether the KB contains any contradicting evidence. If yes, the assumption is "contested". Report `contested_assumptions` count. |
| **Scenario consistency** | Cross-check: does any `current_evidence_refs` fragment directly contradict any `assumption`? If yes → `inconsistent`. Binary flag. |
| **Horizon alignment** | Does the derived `horizon_class` match the caller's override (if any)? If the caller tried to upgrade, report `horizon_violation`. |

**Output**: a `ScenarioEvaluation` dict:

```python
{
    "scenario_id": "scn-...",
    "evaluated_at": "ISO8601",
    "evidence_coverage": 0.75,
    "source_independence": 2,
    "dependency_readiness": 0.5,
    "contradiction_exposure": 0,
    "assumption_load": 3,
    "contested_assumptions": 1,
    "consistency": "consistent",  # or "inconsistent"
    "horizon_alignment": "aligned",  # or "horizon_violation"
    "overall": "testable" | "needs_more_evidence" | "speculative" | "inconsistent",
    "reasoning": "...",  # deterministic human-readable summary
    "criteria_evaluated": [...],  # per-criterion breakdown
}
```

**No single "score"**. The `overall` field is a categorical label
derived from the axes (e.g., `inconsistent` if any contradiction;
`speculative` if horizon=F4; `needs_more_evidence` if
evidence_coverage < 0.5; `testable` otherwise).

### 4.5 Capability E — Scenario Monitoring & Revision (answers Q-E)

**Purpose**: when the KB changes (new evidence, new experiment result,
new relationship), re-evaluate existing scenarios and record revisions.

**Design**: **explicit reassessment, no background scheduler**.

The caller (an API endpoint, or a future worker if authorized) calls:

```
POST /api/v1/future/scenarios/{scenario_id}/reassess
```

This triggers:
1. Reload the scenario's `current_evidence_refs` from the current KB
   (some may have been retracted; some new ones may now apply).
2. Re-run `analyze_gap()` for the scenario's capabilities.
3. Re-run Capability D evaluation.
4. If any axis changed beyond a deterministic threshold (e.g.,
   evidence_coverage dropped, or a new contradiction appeared), emit
   a `ScenarioRevision` audit event (`AuditEventRow(event_type=
   "scenario.revision")`) with the prior and new evaluation snapshots.
5. The scenario's `version` is bumped; the prior version remains
   queryable via the revision history.

**Revision history** (`GET /api/v1/future/scenarios/{scenario_id}/revisions`):
returns the ordered list of `scenario.revision` audit events, each
containing the full prior and new evaluation snapshots. This is the
same append-only audit pattern used by `evidence.delta` in G05-T06.

**No autonomous monitoring**: G06 does NOT install a cron, does NOT
watch a queue, does NOT spawn a worker. Reassessment is always
explicit. (A future group, with explicit authorization, may add a
worker that calls the same reassess endpoint on a schedule.)

### 4.6 Capability F — Connection to Innovation (answers Q-F)

**Purpose**: feed G06 scenario outputs back into G05 innovation
decisions.

**Linkages** (all preserve G05 contracts):

1. **Future-capability-gap annotation**: when a scenario identifies a
   `missing_dependency` that is also a G05 `combine_knowledge()`
   candidate component, the scenario can annotate the innovation
   concept with a `future_risk` note. This is a new
   `AuditEventRow(event_type="innovation.future_annotation")` — it
   does NOT modify the innovation's persisted attributes.
2. **Emerging-opportunity flag**: when a scenario's
   `dependency_readiness` crosses a threshold (e.g., 1.0 = all
   engineering-tractable), the scenario can be flagged as
   "emerging_opportunity" in its `risks` dict. G05
   `discover_opportunities()` results can be cross-referenced.
3. **Experiment proposal**: the existing `POST /future/scenarios/{id}/
   prototype-plan` endpoint (declared as a 501 placeholder) calls
   `plan_experiment()` with the scenario's `prototype_plan` as the
   protocol and the scenario's `missing_dependencies` as the metrics
   to measure. The experiment is linked to the scenario via a
   `SCENARIO_PROTOTYPED_BY` relationship.
4. **Innovation timing**: a scenario at F2 (near-term engineering)
   that shares capabilities with a G05 innovation concept suggests
   the innovation is "timely". A scenario at F4 (speculative)
   suggests the innovation is "ahead of its time". These are notes,
   not gates — G05's existing contracts are unchanged.

---

## 5. Minimal architecture and data flow

### 5.1 Module layout

```
src/synapse/
├── application/
│   ├── future_signal.py      # NEW (G06-T01): Capability A
│   ├── scenario_builder.py   # NEW (G06-T02): Capability B + C
│   ├── scenario_evaluator.py # NEW (G06-T03): Capability D
│   └── scenario_revision.py  # NEW (G06-T04): Capability E
├── api/v1/
│   └── future.py             # NEW (G06-T02): /future/* endpoints
└── domain/
    └── scenario.py           # UNCHANGED (frozen contract)
```

**No new packages.** All G06 code lives under the existing
`application/` and `api/v1/` packages.

### 5.2 Data flow

```
                ┌─────────────────────────────────────────────┐
                │                  Knowledge Base             │
                │  (EntityRow, ClaimRow, RelationshipRow,     │
                │   EvidenceFragmentRow, SourceSpanRow)       │
                └────────────────┬────────────────────────────┘
                                 │
                                 ▼
        ┌──────────────────────────────────────────────────────┐
        │  G06-T01: Future Signal Identification              │
        │  (future_signal.py)                                  │
        │  consumes: analyze_gap(), hybrid_retrieve(),         │
        │            list_capabilities(), combination_count    │
        │  produces: FutureSignalReport[]                      │
        └────────────────┬─────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────────────────┐
        │  G06-T02: Scenario Construction                     │
        │  (scenario_builder.py)                               │
        │  consumes: FutureSignalReport, caller assumptions,   │
        │            analyze_gap() for missing_dependencies    │
        │  produces: Scenario (EntityRow kind="scenario")      │
        │  persists: EntityRow + AuditEventRow(scenario.created)│
        └────────────────┬─────────────────────────────────────┘
                         │
            ┌────────────┴───────────────┐
            ▼                            ▼
  ┌─────────────────────┐    ┌──────────────────────────────┐
  │  G06-T03: Evaluate  │    │  G06-T03: Prototype Plan     │
  │  (scenario_evaluator)│   │  (delegates to plan_experiment)│
  │  produces:          │    │  produces: Experiment plan    │
  │  ScenarioEvaluation │    │  + SCENARIO_PROTOTYPED_BY rel │
  └──────────┬──────────┘    └───────────────┬──────────────┘
             │                               │
             ▼                               ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  G06-T04: Scenario Revision (scenario_revision.py)          │
  │  trigger: explicit POST /scenarios/{id}/reassess            │
  │  produces: AuditEventRow(scenario.revision)                 │
  │  preserves: prior versions via audit trail                  │
  └─────────────────────────────────────────────────────────────┘
```

### 5.3 Persistence mapping

Scenarios are persisted as `EntityRow(kind="scenario")` with
`attributes` JSON:

```json
{
  "scenario_id": "scn-...",
  "signal_id": "sig-... | null",
  "horizon_class": "F0" | "F1" | "F2" | "F3" | "F4",
  "status": "draft" | "under_critique" | "testable" | "prototyped" | "retired",
  "current_evidence_refs": ["frag-...", ...],
  "capabilities": ["cap-...", ...],
  "assumptions": ["...", ...],
  "missing_dependencies": ["ent-..." | "cap-...", ...],
  "risks": {"emerging_opportunity": true, "future_risk_notes": [...]},
  "uncertainty": 0.6,
  "critique": "..." | null,
  "prototype_plan": "..." | null,
  "scenario_fingerprint": "sha256-...",  // PRB-03 idempotency
  "provenance": {
    "created_by": "...",
    "created_at": "ISO8601",
    "request_id": "..."
  }
}
```

Scenario revisions are `AuditEventRow(event_type="scenario.revision")`
with `payload` JSON:

```json
{
  "scenario_id": "scn-...",
  "prior_version": 3,
  "new_version": 4,
  "prior_evaluation": { ... ScenarioEvaluation snapshot ... },
  "new_evaluation": { ... ScenarioEvaluation snapshot ... },
  "changes": ["evidence_coverage: 0.75 -> 0.50", "new_contradiction: ..."],
  "trigger": "explicit_reassess" | "new_evidence" | "new_experiment_result",
  "trigger_ref": "frag-..." | "exp-..." | null,
  "reassessed_at": "ISO8601",
  "reassessed_by": "..."
}
```

**No new tables.** Scenarios reuse `EntityRow`; revisions reuse
`AuditEventRow`; idempotency reuses `EntityFingerprintRow`. The
`kind="scenario"` value is already declared in `EntityType`
(`src/synapse/domain/_base.py:120`).

---

## 6. Scenario schema and persistence mapping

(Covered in §5.3 above. The scenario attributes JSON is the
persistence mapping; the `Scenario` Pydantic record is the domain
contract. G06 does NOT add a new ORM table.)

### 6.1 Idempotency

Scenario creation uses the PRB-03 `claim_fingerprint()` infrastructure:
- Fingerprint = `SHA-256(kind="scenario" + signal_id + horizon_class +
  sorted(capabilities) + sorted(assumptions) + sorted(missing_dependencies))`.
- Same fingerprint → same `scenario_id` (idempotent retry).
- Different assumptions or different missing_dependencies → different
  fingerprint → different scenario (legitimate alternative futures).
- `run_id` parameter (same as G05-T06 executions) for caller-driven
  idempotent retry.

### 6.2 Cross-session visibility

Same pattern as G05: API POST commits; separate API GET sees the
scenario. The `_override_get_db` in `tests/conftest.py` already
commits on success (fixed in G05-T06).

---

## 7. Evidence and uncertainty policies

### 7.1 Evidence policy

- **An experiment plan is not evidence.** (Inherited from G05-T06.)
- **An observation is not verified evidence.** (Inherited.)
- **A scenario is not evidence.** A scenario is a structured
  hypothesis about the future. Its `current_evidence_refs` are the
  evidence that *supports* the scenario; the scenario itself is not
  evidence for anything else.
- **No fabricated evidence.** The evaluator only uses
  `current_evidence_refs` that were explicitly cited. Missing refs →
  `evidence_coverage` drops, not fabricated.
- **No auto-VERIFIED.** `EpistemicState` has no VERIFIED value. A
  scenario's backing hypothesis can move HYPOTHESIZED → SUPPORTED via
  G05-T06 evidence feedback, but never to VERIFIED.

### 7.2 Uncertainty policy

- **`uncertainty` field** (already on `Scenario`, range [0.0, 1.0]):
  G06 populates this **deterministically** from the evaluation:
  `uncertainty = 1.0 - evidence_coverage`, adjusted by
  `+0.1 * contested_assumptions` and `+0.1 * (5 - horizon_index)`
  (more speculative horizons → more uncertain), bounded to [0.0, 1.0].
- **No probabilistic forecast.** `uncertainty` is a deterministic
  summary of evidence coverage and assumption load, not a probability
  of the scenario coming true.
- **Assumption sensitivity**: each assumption is checked against the
  KB for contradicting evidence. Contested assumptions are reported
  by name; the caller decides whether to revise them.

### 7.3 Provenance policy

- Every scenario carries `provenance` (created_by, created_at,
  request_id).
- Every revision carries `trigger` (what caused the reassessment) and
  `trigger_ref` (the fragment/experiment that triggered it).
- `current_evidence_refs` are fragment IDs; the caller can always
  trace back to the source via `EvidenceFragmentRow.source_id` →
  `SourceRow.canonical_uri`.

---

## 8. Deterministic versus optional model-assisted responsibilities

### 8.1 Deterministic core (mandatory, no LLM)

All of Capability A–E is deterministic. The signal identification,
scenario construction, evaluation, and revision logic use only the
existing KB and the existing services. No LLM call is required for
G06 to function.

### 8.2 Optional model-assisted adapter (NOT implemented in G06-P00; future task with explicit authorization)

A future `ModelAssistedAdapter` may be added behind a bounded,
auditable interface to:
- Draft a human-readable narrative summary of a scenario (the
  deterministic core produces the structured fields; the adapter
  writes the prose).
- Suggest assumptions the caller might consider (the caller still
  approves/rejects each).
- Draft a `critique` for F4 scenarios (the caller still approves).

**Binding rules for any future adapter** (carried forward from G05
§3.5):
- A model-generated narrative is **never evidence**. It is labeled
  `model_generated: true` in the audit trail.
- A model-generated assumption is **never persisted** without caller
  approval.
- A model-generated critique is **never the only critique**; the
  deterministic evaluation axes remain the source of truth.
- The adapter is **optional**: if the adapter is unavailable, G06
  returns the structured fields without prose.
- The adapter is **auditable**: every call is logged in
  `AuditEventRow(event_type="model.assisted")` with the input, output,
  model identifier, and caller.

**G06-P00 does NOT implement the adapter.** It is listed here only to
bound the design space. Any adapter requires explicit reviewer
authorization in a future task.

---

## 9. API contract proposal

All routes under `/api/v1/future/`. Auth required (same `PrincipalDep`
as all other v1 routes). Standard error envelope. Standard response
envelope (`{"data": ..., "request_id": ...}`).

### 9.1 G06-T01 — Future signals

```
POST /api/v1/future/signals
Body: {
  "focus_capability_id": "cap-..." | null,
  "focus_entity_id": "ent-..." | null,
  "evidence_threshold": 3,           // min fragments for "new entity footprint"
  "source_independence_threshold": 2,
  "limit": 20
}
Response 200: {
  "data": {
    "signals": [ FutureSignalReport, ... ],
    "snapshot_only": true,
    "generated_at": "ISO8601",
    "request_id": "..."
  }
}
```

### 9.2 G06-T02 — Scenario construction + retrieval

```
POST /api/v1/future/scenarios
Body: {
  "signal_id": "sig-..." | null,
  "capability_id": "cap-..." | null,   // alternative to signal_id
  "assumptions": ["...", ...],          // mandatory, non-empty
  "prototype_plan": "..." | null,
  "horizon_class_override": "F2" | null,  // may only downgrade
  "run_id": "..." | null                  // idempotent retry
}
Response 200: { "data": Scenario, "request_id": "..." }
Response 422: invalid assumptions / horizon upgrade attempt
Response 404: signal/capability not found

GET /api/v1/future/scenarios/{scenario_id}
Response 200: { "data": Scenario, "request_id": "..." }
Response 404: scenario not found
```

### 9.3 G06-T03 — Evaluation + prototype plan

```
POST /api/v1/future/scenarios/{scenario_id}/evaluate
Body: {}
Response 200: { "data": ScenarioEvaluation, "request_id": "..." }

POST /api/v1/future/scenarios/{scenario_id}/prototype-plan
Body: {
  "protocol_override": "..." | null,
  "metrics": {"metric_name": "float", ...}
}
Response 200: {
  "data": {
    "experiment": Experiment,          // from plan_experiment()
    "relationship_id": "rel-..."       // SCENARIO_PROTOTYPED_BY
  }
}
Response 422: scenario is F4 without critique; or no missing_dependencies
```

### 9.4 G06-T04 — Reassessment + revision history

```
POST /api/v1/future/scenarios/{scenario_id}/reassess
Body: {
  "trigger": "explicit_reassess" | "new_evidence" | "new_experiment_result",
  "trigger_ref": "frag-..." | "exp-..." | null
}
Response 200: {
  "data": {
    "prior_evaluation": ScenarioEvaluation,
    "new_evaluation": ScenarioEvaluation,
    "changes": ["...", ...],
    "revision_id": "rev-...",
    "revised": true | false             // false if no changes
  }
}

GET /api/v1/future/scenarios/{scenario_id}/revisions
Response 200: {
  "data": [ ScenarioRevision, ... ]    // newest first
}
```

### 9.5 OpenAPI stability

G06 activates the three existing 501 placeholders
(`/future/scenarios` POST, `/future/scenarios/{id}` GET,
`/future/scenarios/{id}/prototype-plan` POST) and adds four new
routes. The OpenAPI schema is checked with `make openapi-check` at
every G06 task.

---

## 10. Integration with G04 and G05

### 10.1 G04 integration

| G04 component | G06 use |
|---------------|---------|
| `hybrid_retrieve()` | Signal identification: gather evidence footprint. |
| `classify_intent()` | Not used by G06 (G06 has its own signal classification). |
| `analyze_gap()` | **Primary input** to signal identification and scenario construction. The `NOT_EVIDENCED` / `CONTESTED` / `CONSTRAINED` classifications drive horizon assignment. |
| `list_capabilities()` | Enumerate the capability graph for signal candidates. |
| `answer_query()` (reasoning) | Not used by G06 directly; a caller may use reasoning to explain a scenario, but G06 does not depend on it. |

### 10.2 G05 integration

| G05 component | G06 use |
|---------------|---------|
| `combine_knowledge()` | Signal identification: `combination_count` per capability. |
| `discover_opportunities()` | Opportunities are candidate future signals; G06 may consume them. |
| `generate_innovations()` | G06 does NOT call this. G06 annotates existing innovations; it does not regenerate them. |
| `compose_architecture()` | Not used by G06 (scenarios are not architectures). |
| `critique_innovation()` | G06 scenario evaluation reuses the critique *pattern* (feasibility, evidence coverage, failure modes) but does NOT call `critique_innovation()` — scenarios and innovations have different evaluation axes. |
| `plan_experiment()` | **Called by** `POST /future/scenarios/{id}/prototype-plan`. The scenario's `prototype_plan` becomes the experiment's `protocol`; the scenario's `missing_dependencies` become the metrics to measure. |
| `create_execution()`, `record_observation()`, `finalize_execution()` | Not called by G06. A scenario-proposed experiment follows the normal G05-T06 execution flow. |
| `get_evidence_deltas()` | G06 scenario revision may consult the hypothesis's evidence deltas to detect new supporting/contradicting evidence. |

### 10.3 What G06 does NOT change in G04/G05

- No G04 service signature is modified.
- No G05 service signature is modified.
- No G04/G05 domain contract is modified.
- No G04/G05 API endpoint is modified.
- No G04/G05 test is modified (G06 tests are additive).
- No G04/G05 migration is modified.
- G06 only **reads** from G04/G05 and **delegates** to
  `plan_experiment()`.

---

## 11. Evaluation strategy and acceptance gates

### 11.1 Per-task acceptance gates

Each G06-T0X task has its own acceptance gate (defined in §13). The
overall G06 acceptance gate requires:

1. **End-to-end scenario lifecycle**: a single integration test that
   builds a scenario from a signal, evaluates it, proposes a prototype
   experiment, runs the experiment (via G05-T06), and reassesses the
   scenario with the new evidence.
2. **Epistemic integrity**: no scenario is promoted to "verified
   future"; no fabricated evidence; no artificial probabilities; F4
   scenarios always carry a critique.
3. **Provenance traceability**: every scenario traces back to its
   evidence fragments, its signal, its assumptions, and its revision
   history.
4. **G04/G05 regression**: the full deterministic suite still passes;
   no G04/G05 test is modified.
5. **PostgreSQL isolation**: G06 tests use the per-module isolated DB
   pattern from G05-T07; the combined PG suite still passes.
6. **Ruff + OpenAPI**: clean.

### 11.2 Quality gates (deterministic)

| # | Gate | Enforcement |
|---|------|-------------|
| QG-1 | Every scenario has `horizon_class` in F0–F4 | Pydantic enum |
| QG-2 | F4 scenarios have non-empty `critique` | `Scenario._f4_requires_critique` validator |
| QG-3 | Non-F0 scenarios have ≥1 `missing_dependency` | `Scenario._non_f0_must_list_missing_deps` validator |
| QG-4 | No scenario has `epistemic_state="verified"` | `EpistemicState` has no VERIFIED value (type system) |
| QG-5 | Every `current_evidence_refs` entry is a real fragment ID | Foreign-key-style check in scenario_builder |
| QG-6 | `uncertainty` is in [0.0, 1.0] | Pydantic field constraint |
| QG-7 | Horizon override may only downgrade | `scenario_builder` checks `horizon_index(override) >= horizon_index(derived)` |
| QG-8 | No fabricated trends | `FutureSignalReport.snapshot_only == True` always |
| QG-9 | Every revision emits an audit event | `scenario_revision` always inserts `AuditEventRow` |
| QG-10 | No LLM call in the deterministic core | G06 source has no `import openai` / `import anthropic` / provider calls |

### 11.3 Test coverage

| Capability | Mandatory tests |
|------------|-----------------|
| A — Signal identification | ≥8 tests: each signal kind, snapshot_only invariant, evidence threshold, source independence, gap classification propagation, empty KB, deterministic ordering, no fabricated trends. |
| B — Scenario construction | ≥10 tests: each horizon derivation rule, F4-requires-critique, non-F0-requires-missing-deps, horizon override downgrade-only, idempotent retry (same run_id), alternative futures (different assumptions → different fingerprint), cross-session persistence, invalid signal, invalid capability, provenance preservation. |
| C — Alternative futures | ≥5 tests: comparison dimensions, shared signal, shared evidence, horizon distance, no artificial probabilities. |
| D — Evaluation | ≥8 tests: each axis, overall label derivation, inconsistent flag, horizon violation, no fabricated evidence, contested assumptions, deterministic output, empty evidence. |
| E — Revision | ≥6 tests: explicit reassess, new evidence trigger, new experiment result trigger, no-change (revised=false), revision history ordering, prior version preserved. |
| F — G05 linkage | ≥5 tests: prototype plan delegates to plan_experiment, SCENARIO_PROTOTYPED_BY relationship, future_risk annotation, emerging_opportunity flag, innovation timing note. |
| API + OpenAPI | ≥6 tests: each endpoint auth, 404, 422, 200, OpenAPI schema includes all 7 routes. |
| Regression | Full deterministic suite + combined PG suite pass. |

---

## 12. Risks, limitations, and explicit exclusions

### 12.1 Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R-01 | Scenarios degenerate into generic speculation | Medium | High | QG-2, QG-3, QG-8 enforce grounding; F4 mandatory critique; `snapshot_only=True` on every signal. |
| R-02 | Callers misuse `horizon_class_override` to upgrade horizons | Low | High | QG-7: override may only downgrade; `scenario_builder` rejects upgrades with 422. |
| R-03 | Scenario evaluation produces a false sense of precision | Medium | Medium | No single "score"; `overall` is categorical; `uncertainty` is deterministic and labeled as such. |
| R-04 | Revision history grows unbounded | Medium | Low | Audit rows are append-only; a future group can add archival. Not a G06 blocker. |
| R-05 | G06 scope creep into autonomous monitoring | Low | High | §4.5: no background scheduler, no cron, no worker. Reassessment is always explicit. |
| R-06 | Model-assisted adapter introduced without authorization | Low | High | §8.2: adapter is planning boundary only; not implemented in G06; requires explicit authorization. |
| R-07 | G06 duplicates G04/G05 reasoning | Low | Medium | §10: G06 consumes, does not duplicate. `critique_innovation()` is NOT called; only the pattern is reused. |
| R-08 | Signal identification pretends to detect trends without longitudinal data | Medium | High | `snapshot_only=True` on every signal; `first_seen_at`/`last_seen_at` are factual observations, not growth curves. |

### 12.2 Limitations (explicit)

1. **No longitudinal trends.** Synapse ingests sources at a point in
   time; G06 cannot compute "rate of adoption" or "growth curve". This
   is a factual limitation of the data, not a G06 defect.
2. **No numerical probabilities.** Horizon classes are categorical;
   `uncertainty` is a deterministic coverage summary, not a
   probability.
3. **No autonomous monitoring.** Reassessment is explicit.
4. **No verified futures.** A scenario can never be promoted to
   "verified" — `EpistemicState` has no VERIFIED value.
5. **No external actions.** G06 does not call external APIs, does not
   send emails, does not deploy code.
6. **No new database engine.** G06 uses PostgreSQL/SQLite only.
7. **No vector database.** G06 retrieval uses the existing
   `hybrid_retrieve()` (lexical + metadata + graph).
8. **No graph database.** G06 graph queries use the existing
   `RelationshipService`.
9. **No multi-agent swarm.** G06 is a single-process modular monolith.
10. **No mandatory LLM.** G06's core is deterministic.
11. **Production-readiness blockers (PRB-01..07) remain unchanged.**
    G06 does not resolve them.

### 12.3 Explicit exclusions (out of G06 scope)

- **G07 frontend** — out of scope. G06 exposes API endpoints; the UI
  is a future group.
- **Background scheduler / cron** — out of scope. Reassessment is
  explicit.
- **Model-assisted adapter** — out of scope for G06-P00 and G06-T0X.
  Requires explicit authorization.
- **Scenario archival** — out of scope. Append-only audit log grows;
  archival is a future operational concern.
- **Cross-domain scenario aggregation** — out of scope. G06 scenarios
  are per problem_domain; cross-domain rollup is a future analytics
  concern.
- **Numerical forecasting** — permanently excluded. Synapse does not
  have the data for it.

---

## 13. Staged implementation plan

Four implementation tasks, each independently reviewable. Prefer a
small number of coherent tasks over many fragmented missions.

### 13.1 G06-T01 — Future Signal Identification

**Objective**: implement Capability A — deterministic scan of the KB
for signals of emerging technological change.

**Existing components reused**:
- `analyze_gap()` — primary input.
- `hybrid_retrieve()` — evidence footprint gathering.
- `list_capabilities()` — capability enumeration.
- `combine_knowledge()` — combination density (cached or recomputed).
- `assess_claim()` — verification state of evidence.
- `EntityFingerprintRow` — signal idempotency.

**Implementation boundary**:
- New file: `src/synapse/application/future_signal.py`
- New file: `tests/integration/test_g06_t01_future_signals.py`
- No new domain records (signal report is a plain dict).
- No new ORM tables.
- No API endpoint (signals are returned by a service function; the API
  is added in G06-T02 to keep T01 focused).

**Expected files**:
- `src/synapse/application/future_signal.py` (~300 LOC)
- `tests/integration/test_g06_t01_future_signals.py` (~350 LOC)

**Mandatory tests**:
1. Each signal kind produces a well-formed `FutureSignalReport`.
2. `snapshot_only == True` on every signal.
3. Evidence threshold respected.
4. Source independence computed correctly.
5. Gap classification propagated from `analyze_gap()`.
6. Combination density computed from `combine_knowledge()`.
7. Empty KB → empty signals list (no fabricated signals).
8. Deterministic ordering (same KB → same signal order).
9. No fabricated trends: `first_seen_at` / `last_seen_at` are
   factual; no growth rate field exists.
10. Provenance: every signal references real entity/capability IDs.

**Acceptance criteria**:
- All 10 tests pass.
- `ruff check` clean.
- Full deterministic regression still passes.
- No production code outside `src/synapse/application/future_signal.py`
  is modified.

**Dependencies**: None (G06-T01 is the foundation).

**STOP gate**: After G06-T01, STOP for reviewer confirmation before
G06-T02.

---

### 13.2 G06-T02 — Scenario Construction + API

**Objective**: implement Capability B (scenario construction) + the
API layer for signals and scenarios.

**Existing components reused**:
- `Scenario` domain record (frozen — unchanged).
- `ScenarioStatus` enum (frozen).
- `HorizonClass` enum (frozen).
- `analyze_gap()` — for `missing_dependencies`.
- `assess_claim()` — for evidence verification states.
- `RelationshipService` — for capability graph neighbors.
- `claim_fingerprint()` (PRB-03) — for scenario idempotency.
- `EntityRow` (kind="scenario") — persistence.
- `AuditEventRow` (event_type="scenario.created") — audit.
- `tests/integration/conftest.py` — PG isolation.

**Implementation boundary**:
- New file: `src/synapse/application/scenario_builder.py`
- New file: `src/synapse/api/v1/future.py` (signals POST + scenarios
  POST + GET)
- Modify: `src/synapse/api/v1/router.py` — activate the 3 existing
  501 placeholders for `/future/scenarios`, add the `/future/signals`
  route.
- No new domain records.
- No new ORM tables.
- No new migrations.

**Expected files**:
- `src/synapse/application/scenario_builder.py` (~400 LOC)
- `src/synapse/api/v1/future.py` (~250 LOC)
- `tests/integration/test_g06_t02_scenario_construction.py` (~450 LOC)

**Mandatory tests**:
1. F0 scenario: all capabilities SUPPORTED, no missing deps.
2. F1 scenario: all capabilities SUPPORTED, no missing deps, but
   combination unevidenced.
3. F2 scenario: missing deps are all "engineering".
4. F3 scenario: ≥1 missing dep is "research".
5. F4 scenario: critique mandatory.
6. Horizon override downgrade OK; upgrade → 422.
7. Idempotent retry (same `run_id` → same `scenario_id`).
8. Alternative futures: different assumptions → different fingerprint.
9. Cross-session persistence (POST then GET from separate session).
10. Invalid signal → 404; invalid capability → 404.
11. Empty assumptions → 422.
12. Provenance: `created_by`, `created_at`, `request_id` preserved.
13. API auth: no token → 401.
14. OpenAPI includes all new routes.

**Acceptance criteria**:
- All 14 tests pass.
- `ruff check` clean.
- `make openapi-check` passes.
- Full deterministic regression still passes.
- The 3 existing 501 placeholders are replaced (no longer in
  `_placeholder` router).

**Dependencies**: G06-T01 (signals are an input to scenario
construction).

**STOP gate**: After G06-T02, STOP for reviewer confirmation before
G06-T03.

---

### 13.3 G06-T03 — Scenario Evaluation + Prototype Plan

**Objective**: implement Capability D (evaluation) + the prototype-plan
endpoint (Capability F linkage).

**Existing components reused**:
- `plan_experiment()` — the prototype-plan endpoint delegates to this.
- `assess_claim()` — for evidence verification.
- `analyze_gap()` — for dependency readiness.
- `RelationshipService` — for `SCENARIO_PROTOTYPED_BY` relationship.
- `AuditEventRow` (event_type="scenario.evaluated") — audit.

**Implementation boundary**:
- New file: `src/synapse/application/scenario_evaluator.py`
- Modify: `src/synapse/api/v1/future.py` — add `/evaluate` and
  `/prototype-plan` endpoints.
- No new domain records.
- No new ORM tables.
- No new migrations.

**Expected files**:
- `src/synapse/application/scenario_evaluator.py` (~350 LOC)
- `tests/integration/test_g06_t03_evaluation_prototype.py` (~400 LOC)

**Mandatory tests**:
1. Each evaluation axis computed correctly.
2. `overall` label derivation (testable / needs_more_evidence /
   speculative / inconsistent).
3. `inconsistent` flag when evidence contradicts an assumption.
4. `horizon_violation` when caller tried to upgrade.
5. No fabricated evidence (empty refs → coverage 0.0).
6. Contested assumptions reported by name.
7. Deterministic output (same scenario → same evaluation).
8. Prototype plan delegates to `plan_experiment()`.
9. `SCENARIO_PROTOTYPED_BY` relationship created.
10. F4 scenario without critique → 422 on prototype-plan.
11. Scenario with no missing_dependencies → 422 on prototype-plan
    (nothing to test).
12. API auth + 404 + 422.
13. OpenAPI includes new routes.

**Acceptance criteria**:
- All 13 tests pass.
- `ruff check` clean.
- `make openapi-check` passes.
- Full deterministic regression still passes.

**Dependencies**: G06-T02 (scenarios must exist to be evaluated).

**STOP gate**: After G06-T03, STOP for reviewer confirmation before
G06-T04.

---

### 13.4 G06-T04 — Scenario Revision + End-to-End Acceptance

**Objective**: implement Capability E (revision) + the end-to-end
acceptance test that closes G06.

**Existing components reused**:
- `AuditEventRow` (event_type="scenario.revision") — append-only
  revision history (same pattern as `evidence.delta`).
- `get_evidence_deltas()` — to detect new evidence for the scenario's
  backing hypothesis.
- `scenario_evaluator` (from G06-T03) — re-evaluation.
- `tests/integration/conftest.py` — PG isolation.

**Implementation boundary**:
- New file: `src/synapse/application/scenario_revision.py`
- Modify: `src/synapse/api/v1/future.py` — add `/reassess` and
  `/revisions` endpoints.
- New file: `tests/integration/test_g06_t04_revision_e2e.py`
- No new domain records.
- No new ORM tables.
- No new migrations.

**Expected files**:
- `src/synapse/application/scenario_revision.py` (~300 LOC)
- `tests/integration/test_g06_t04_revision_e2e.py` (~500 LOC)

**Mandatory tests**:
1. Explicit reassess with no changes → `revised=false`.
2. Explicit reassess with new evidence → `revised=true`, audit event
   emitted.
3. New experiment result trigger → reassess picks up the evidence
   delta.
4. Prior version preserved in revision history.
5. Revision history ordering (newest first).
6. End-to-end: build scenario → evaluate → propose prototype → run
   experiment (G05-T06) → record observation → finalize → reassess
   scenario → new evaluation reflects the experiment result.
7. Provenance: every revision carries `trigger` and `trigger_ref`.
8. No background scheduler (reassessment is explicit).
9. API auth + 404.
10. OpenAPI includes new routes.
11. Combined PG suite (G06 + G05 + PRB-03) passes in a single run.
12. Full deterministic regression passes.

**Acceptance criteria**:
- All 12 tests pass.
- End-to-end lifecycle test passes.
- Combined PostgreSQL suite passes (G06 + G05-T06 audit + PRB-03
  concurrency + PRB-03 determinism + PRB-03 migration safety).
- `ruff check` clean.
- `make openapi-check` passes.
- No production code outside `src/synapse/application/scenario_revision.py`,
  `src/synapse/api/v1/future.py`, and `src/synapse/api/v1/router.py`
  is modified.
- No G04/G05 test is modified.

**Dependencies**: G06-T03 (evaluation must exist to reassess).

**STOP gate**: After G06-T04, STOP for G06 closure review. Do not
begin G07 without explicit reviewer authorization.

---

### 13.5 Task summary

| Task | Title | LOC estimate | Tests | STOP gate |
|------|-------|-------------|-------|-----------|
| G06-T01 | Future Signal Identification | ~650 | 10 | Yes |
| G06-T02 | Scenario Construction + API | ~1100 | 14 | Yes |
| G06-T03 | Scenario Evaluation + Prototype Plan | ~750 | 13 | Yes |
| G06-T04 | Scenario Revision + E2E Acceptance | ~800 | 12 | Yes (G06 closure) |
| **Total** | | **~3300** | **49** | |

This is a **compact** implementation. For comparison, G05 was ~5000
LOC across 6 tasks. G06 is smaller because it **reuses** G04/G05
heavily and adds no new domain contracts, no new ORM tables, and no
new migrations.

---

## 14. Mandatory planning evaluation

| Question | Answer |
|----------|--------|
| Does G06 provide genuine future intelligence rather than generic speculative text? | **Yes.** Every scenario is grounded in `current_evidence_refs` and `missing_dependencies` derived from the KB. F4 scenarios require a critique. `snapshot_only=True` on every signal prevents fabricated trends. |
| Are evidence, inference, and speculation clearly separated? | **Yes.** Evidence = `current_evidence_refs` (fragments). Inference = horizon derivation from gap classifications. Speculation = F4 with mandatory critique. The three are never conflated. |
| Can scenarios be constructed without inventing unavailable historical data? | **Yes.** G06 uses only the current KB snapshot. `first_seen_at`/`last_seen_at` are factual observations of evidence fragments, not growth curves. No longitudinal data is fabricated. |
| Are assumptions explicit and testable? | **Yes.** Assumptions are a mandatory list of strings. Each assumption is checked against the KB for contradicting evidence (`contested_assumptions`). The caller can revise assumptions and re-evaluate. |
| Can scenarios be revised when evidence changes? | **Yes.** G06-T04 implements explicit reassessment with append-only revision history. Prior versions are preserved. |
| Does G06 reuse G04/G05 rather than duplicate their reasoning? | **Yes.** §10 documents the consumption. G06 does not call `critique_innovation()` or `answer_query()`; it reuses `analyze_gap()`, `hybrid_retrieve()`, `plan_experiment()`, and `get_evidence_deltas()`. |
| Is the proposed implementation proportionate to the product's needs? | **Yes.** ~3300 LOC across 4 tasks; no new tables; no new migrations; no new domain contracts; no new infrastructure dependencies. |
| Can the first useful scenario be demonstrated early? | **Yes.** After G06-T02 (the second task), a caller can build and retrieve a scenario. The first useful scenario is demonstrated before evaluation (T03) or revision (T04). |

**All eight questions answered affirmatively.** No feature was
identified that should be removed or deferred.

---

## 15. Deliverable summary

```
G06_P00_STATUS = PASS
AUTHORITATIVE_START_SHA = 3d343b84e70aeea3f65c67ff947c61d94b12c785
EXISTING_CONTRACTS_VERIFIED = YES
F0_F4_HORIZONS_VERIFIED = YES
G06_SCOPE_DEFINED = YES
EPISTEMIC_POLICY_DEFINED = YES
ARCHITECTURE_PROPOSED = YES
IMPLEMENTATION_TASKS_DEFINED = G06-T01 (Future Signal Identification), G06-T02 (Scenario Construction + API), G06-T03 (Scenario Evaluation + Prototype Plan), G06-T04 (Scenario Revision + E2E Acceptance)
PRODUCTION_CODE_MODIFIED = NO
MIGRATIONS_CREATED = NO
PLAN_FILE = reports/G06_FUTURE_INTELLIGENCE_IMPLEMENTATION_PLAN.md
FINAL_SHA = dd3a74ddbb7c0b0678e734e6ce6a6ee4d55b7a1c
READY_FOR_ARCHITECTURE_REVIEW = YES
```

---

## 16. Stop condition

G06-P00 is **planning only**. No production code, no migrations, no
domain-contract changes, no activated API endpoints are produced by
this task.

**Do not begin G06-T01 until ChatGPT reviews and approves this plan.**

The plan is committed and pushed for review.
