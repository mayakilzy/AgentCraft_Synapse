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

The authoritative definitions (`src/synapse/domain/_base.py:98-105`) are
preserved unchanged. The table below is G06's binding interpretation of
what each horizon **requires** for a scenario to be classified into it.
The key distinction between F0 and F1 is whether the **integration**
(the specific combination/configuration the scenario proposes) is itself
documented in the KB, not merely whether the components exist.

| Horizon | Meaning | Components | Integration evidence | Missing dependencies | Critique |
|---------|---------|------------|---------------------|---------------------|----------|
| **F0** | **Existing** — the specific configuration the scenario describes is documented in the KB today. | Each component has ≥1 SUPPORTED claim. | ≥1 SUPPORTED claim or explicit relationship documenting *this* integration. | None (empty). | Not required. |
| **F1** | **Possible but uncommon** — the components exist and are documented, but the *integration* the scenario proposes is not evidenced in the KB. | Each component has ≥1 SUPPORTED claim. | None — the combination is unevidenced. | Must contain **at least one explicitly unresolved integration dependency** (see §3.2.1). | Not required. |
| **F2** | **Near-term engineering** — the components exist; the missing piece is engineering effort (no new research). | Each component has ≥1 SUPPORTED claim. | None (or partial). | ≥1 "engineering" dependency (entity exists in KB with SUPPORTED claim, but the integration relationship is missing); may also contain integration dependencies. | Not required. |
| **F3** | **Research-dependent** — at least one missing dependency is an open research question. | Components may be SUPPORTED or partially supported. | None (or partial). | ≥1 "research" dependency (entity does not exist in KB, or exists only with HYPOTHESIZED/INFERRED claims). | Not required. |
| **F4** | **Speculative** — the scenario rests on assumptions not grounded in current evidence. | Any. | Any. | Any (may be empty only if critique explains why). | **Mandatory** (`Scenario._f4_requires_critique` validator). |

**F0 vs F1 — the critical distinction**: an existing set of components
does NOT prove that their proposed integration exists. F0 requires
evidence of the *integration itself* (a SUPPORTED claim or an explicit
relationship documenting the combination). F1 is when the components
are each SUPPORTED but the integration is unevidenced. This resolves
the overlap: F0 and F1 are mutually exclusive on the integration-
evidence axis.

### 3.2 Horizon assignment rules (deterministic, with precedence)

The horizon class of a constructed scenario is **derived from its
inputs** using the following precedence-ordered rules. The first rule
that matches determines the horizon. Rules are **mutually exclusive**:
once a rule matches, lower-precedence rules are not evaluated.

**Precedence order** (highest first):

1. **F4** (highest precedence) — the caller explicitly requests
   speculation, OR any capability lacks a SUPPORTED claim (gap
   classification is `NOT_EVIDENCED`, `CONTESTED`, `UNKNOWN`, or the
   capability entity does not exist in the KB). The mandatory
   `critique` must be populated (by the caller or the evaluator).

2. **F3** — every capability has a SUPPORTED claim, AND at least one
   `missing_dependency` is classified as "research" (the dependency
   entity does not exist in the KB, or exists only with
   HYPOTHESIZED/INFERRED claims — i.e., gap classification
   `NOT_EVIDENCED` or `UNKNOWN` for that dependency).

3. **F2** — every capability has a SUPPORTED claim, AND at least one
   `missing_dependency` is classified as "engineering" (the dependency
   entity exists in the KB with a SUPPORTED claim, but the integration
   relationship — e.g., `INTEGRATES_WITH`, `PROVIDES`, `ENABLES` —
   between the scenario's components and this dependency is missing),
   AND no missing dependency is classified as "research".

4. **F1** — every capability has a SUPPORTED claim, AND the
   integration the scenario proposes is **not** evidenced in the KB
   (no SUPPORTED claim and no explicit relationship documents *this*
   combination), AND the `missing_dependencies` list contains at least
   one **explicitly unresolved integration dependency** (§3.2.1). If
   the integration IS evidenced → F0. If the integration is
   unevidenced but no integration dependency can be honestly named →
   **fail closed** (§3.2.2).

5. **F0** (lowest precedence) — every capability has a SUPPORTED
   claim, AND the integration the scenario describes is documented
   (≥1 SUPPORTED claim or explicit relationship evidencing *this*
   combination), AND `missing_dependencies` is empty.

**Negative tests** (mutual exclusivity verification):
- A scenario with an evidenced integration AND a non-empty
  `missing_dependencies` → cannot be F0 (rule 5 requires empty
  missing_deps); must be F2 or F3 depending on the dependency type.
- A scenario with an unevidenced integration AND no honest
  integration dependency → cannot be F1 (rule 4 requires ≥1
  integration dependency); must fail closed or be F4 if the caller
  explicitly speculates.
- A scenario where any capability is NOT_EVIDENCED → cannot be F0/F1/
  F2/F3 (rules 2–5 all require SUPPORTED capabilities); must be F4.

The caller may **downgrade** a horizon (make it more speculative:
F0 → F1 → F2 → F3 → F4) but never **upgrade** it. The
`Scenario._non_f0_must_list_missing_deps` and
`Scenario._f4_requires_critique` validators enforce the frozen
contract constraints (§3.2.1 explains how F1 satisfies the
non-F0-requires-missing-deps validator honestly).

#### 3.2.1 F1 and the mandatory `missing_dependencies` validator

The frozen `Scenario` domain record (`src/synapse/domain/scenario.py`)
requires every non-F0 scenario to have ≥1 `missing_dependency`:

```python
@model_validator(mode="after")
def _non_f0_must_list_missing_deps(self) -> Scenario:
    if self.horizon_class != HorizonClass.F0 and not self.missing_dependencies:
        raise ValueError(...)
```

G06 does **not** modify this validator (frozen contract). Instead, G06
satisfies it **honestly** for F1 scenarios by populating
`missing_dependencies` with **explicitly unresolved integration
dependencies**. An integration dependency is a real entity/capability
ID from the KB that:

(a) exists in the KB (so the ID is traceable, not fabricated), AND
(b) has a SUPPORTED claim (so it is a real component), AND
(c) lacks an integration relationship (`INTEGRATES_WITH`, `PROVIDES`,
    `ENABLES`) to at least one of the scenario's other components
    (so the integration is genuinely unresolved).

This is the **smallest compatible representation**: it uses an existing
entity ID (not a new domain field, not a new predicate) and it
truthfully represents what is missing — the integration itself. The
`missing_dependencies` list for an F1 scenario is therefore a list of
real KB entities whose integration into the scenario is unevidenced.

#### 3.2.2 Fail-closed for F1

If the scenario builder cannot honestly name at least one real KB
entity as an unresolved integration dependency (for example, the
scenario proposes a combination of components but every pair already
has an `INTEGRATES_WITH` relationship), the scenario **cannot** be F1
— it must be F0 (if the integration is fully evidenced) or F4 (if the
caller insists on speculation despite the evidence). The builder
**fails closed**: it raises `ValueError("F1 scenario requires at least
one honestly unresolved integration dependency")` rather than
fabricating a dependency.

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

#### 4.1.1 Three-layer separation (Correction D)

G06 strictly separates three epistemically distinct layers. No signal
may cross layers, and each layer is labeled explicitly in the output:

| Layer | What it represents | What it requires | `signal.temporal_type` |
|-------|-------------------|------------------|------------------------|
| **1. Current evidence snapshot** | A factual observation of the KB's *current* state. | Only the current KB. | `"snapshot"` |
| **2. Verified historical change** | A confirmed change between two *persisted, comparable* KB snapshots. | Two persisted snapshot records with matching scope, compared deterministically. | `"verified_change"` |
| **3. Speculative interpretation** | A caller's interpretation of what *might* be changing. | Caller-supplied hypothesis; labeled as speculation. | `"speculative_interpretation"` |

**Layer 2 is NOT available in G06-T01.** Synapse does not persist
comparable KB snapshots over time. `analyze_gap()` is a stateless
snapshot classifier; it does not record its outputs. Therefore G06
**cannot** report "gap transitions", "growth", or "threshold crossings"
— these are Layer 2 claims that require persisted comparable
observations. Any signal that would imply a trend is **rejected** and
the builder fails closed.

**G06-T01 implements only Layer 1** (snapshot signals). Layer 2 is a
documented future capability (requires a snapshot-persistence
infrastructure that does not exist). Layer 3 is available only via
explicit caller annotation (`POST /future/scenarios` with caller
assumptions), never via the signal endpoint.

#### 4.1.2 Snapshot signal kinds (Layer 1 only)

Each signal is a factual observation of the current KB. No signal
implies change over time.

- **Gap classification snapshot**: `analyze_gap()` returns one of six
  classifications per (capability, candidate) pair: `SUPPORTED`,
  `PARTIALLY_SUPPORTED`, `NOT_EVIDENCED`, `CONTESTED`,
  `CONSTRAINED`, `UNKNOWN`. G06 reports the *current* classification.
  It does NOT claim the classification "changed" — that would be a
  Layer 2 claim.
- **Evidence footprint snapshot**: the count of fragments and the
  count of distinct `source_uri` values currently citing the
  capability. Reported as a factual count. No threshold-crossing
  claim.
- **Combination density snapshot**: the number of
  `combine_knowledge()` candidates that currently reference the
  capability. A structural observation of graph centrality, not a
  trend.
- **Contradiction exposure snapshot**: capabilities with `CONTESTED`
  gap classifications. A factual observation of current disagreement,
  not a claim that disagreement is "increasing".
- **Dependency graph snapshot**: the current set of `PROVIDES` /
  `DEPENDS_ON` / `LIMITS` edges incident to the capability. No
  "dependency shift" signal — that would be Layer 2.

**Removed signal kinds** (versus G06-P00): `gap_transition` and
`dependency_shift` are **removed**. They required Layer 2 (verified
historical change) which G06-T01 cannot provide. The
`new_capability` kind is renamed to `evidence_footprint_snapshot` to
avoid implying novelty.

#### 4.1.3 Source independence — conservative handling (Correction D)

**Distinct source URLs are NOT independent origins.** Mirrors,
reposts, and syndicated copies may have different URLs but share the
same primary origin. The existing `verification.py`
`_count_independent_primary_origins()` function
(`src/synapse/application/verification.py:432-465`) **always returns
1** (conservative default, per PRB-04) because explicit provenance
metadata (publisher, author) is not reliably available.

G06 follows the same conservative policy:

- The signal reports `distinct_source_uri_count` (a factual count of
  distinct `EvidenceFragmentRow.source_uri` values). This is labeled
  **"distinct URL count"**, not "source independence".
- The signal reports `independent_origin_count = 1` (conservative,
  per PRB-04) unless explicit provenance metadata (distinct
  `SourceRow.publisher` or `SourceRow.author`) is available and
  differs. If provenance metadata is absent or identical,
  `independent_origin_count = 1`.
- The signal **never** claims "N independent sources" based on URL
  count alone.

#### 4.1.4 Signal output schema

```python
{
    "signal_id": "sig-...",
    "temporal_type": "snapshot",          # always "snapshot" in G06-T01
    "kind": "gap_classification_snapshot" |
            "evidence_footprint_snapshot" |
            "combination_density_snapshot" |
            "contradiction_exposure_snapshot" |
            "dependency_graph_snapshot",
    "entity_id": "ent-..." | None,
    "capability_id": "cap-..." | None,
    "evidence_refs": ["frag-...", ...],  # fragments currently citing this
    "gap_classification": "supported" | "partially_supported" |
                          "not_evidenced" | "contested" |
                          "constrained" | "unknown" | None,
    "combination_count": int,            # current combine_knowledge candidate count
    "distinct_source_uri_count": int,    # factual count of distinct URLs (NOT independence)
    "independent_origin_count": 1,       # conservative (PRB-04); 1 unless proven otherwise
    "first_seen_at": "ISO8601",          # earliest evidence_refs.retrieved_at (factual)
    "last_seen_at": "ISO8601",           # latest evidence_refs.retrieved_at (factual)
    "snapshot_only": True,               # always True in G06-T01
    "notes": "...",                      # deterministic human-readable summary
}
```

**Critical invariants**:
- `snapshot_only == True` on every signal in G06-T01.
- `temporal_type == "snapshot"` on every signal in G06-T01.
- No `growth_rate`, `trend`, `delta`, or `change` field exists.
- `first_seen_at` / `last_seen_at` are factual observations of the
  evidence footprint's timestamp range, not a growth curve.
- `independent_origin_count` is conservative (1) unless explicit
  provenance metadata proves otherwise.

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

**Evaluation axes** (each a deterministic, transparent dimension —
**not** a calibrated probability. Each axis is reported with its raw
deterministic computation. No axis is combined into a single
"score"):

| Axis | Deterministic computation | Type |
|------|---------------------------|------|
| **Evidence coverage** | `|current_evidence_refs ∩ KB_verified_fragments| / |current_evidence_refs|`. If 0 evidence refs → 0.0. | ratio in [0.0, 1.0] |
| **Distinct source URL count** | `|distinct(source_uri) for ref in current_evidence_refs|`. | integer count |
| **Independent origin count** | Conservative: `1` (per PRB-04) unless explicit `SourceRow.publisher`/`author` metadata differs. | integer count (≤ distinct URL count) |
| **Dependency readiness** | `|missing_dependencies classified "engineering"| / |missing_dependencies|`. `1.0` = all engineering; `0.0` = all research; `null` if no missing deps. | ratio in [0.0, 1.0] or null |
| **Contradiction exposure** | `count(CONTESTED claims among current_evidence_refs)`. | integer count |
| **Assumption load** | `|assumptions|`. | integer count |
| **Contested assumptions** | For each assumption, check whether the KB contains contradicting evidence. Count of contested assumptions. | integer count |
| **Scenario consistency** | Does any `current_evidence_refs` fragment directly contradict any `assumption`? | binary: `"consistent"` / `"inconsistent"` |
| **Horizon alignment** | Does the derived `horizon_class` match the caller's override? If the caller tried to upgrade → `"horizon_violation"`. | binary: `"aligned"` / `"horizon_violation"` |
| **Horizon class** | The scenario's `horizon_class` (F0–F4). | categorical (F0–F4) |

**No single "score"**. The `overall` field is a categorical label
derived from the axes (e.g., `inconsistent` if any contradiction;
`speculative` if horizon=F4; `needs_more_evidence` if
`evidence_coverage < 0.5; `testable` otherwise).

**Output**: a `ScenarioEvaluation` dict:

```python
{
    "scenario_id": "scn-...",
    "evaluated_at": "ISO8601",
    "evidence_coverage": 0.75,            # ratio
    "distinct_source_uri_count": 3,       # integer (NOT "independence")
    "independent_origin_count": 1,        # conservative (PRB-04)
    "dependency_readiness": 0.5,          # ratio or null
    "contradiction_exposure": 0,          # integer
    "assumption_load": 3,                 # integer
    "contested_assumptions": 1,           # integer
    "consistency": "consistent",          # or "inconsistent"
    "horizon_alignment": "aligned",       # or "horizon_violation"
    "horizon_class": "F2",                # categorical
    "overall": "testable" | "needs_more_evidence" | "speculative" | "inconsistent",
    "reasoning": "...",                   # deterministic human-readable summary
    "criteria_evaluated": [...],          # per-criterion breakdown
}
```

**The `uncertainty` field** on the `Scenario` domain record is
populated from `evidence_coverage` only (see §7.2 for the corrected
semantics). It is **not** a probability of future occurrence.

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

#### 4.6.1 Verified G05 contract constraints (Correction E)

Source-code inspection of `plan_experiment()`
(`src/synapse/application/experiment_planner.py:779-868`) revealed
**three binding constraints** that G06 must respect:

1. **`plan_experiment(session, hypothesis_id, ...)` requires a
   `hypothesis_id`** that resolves to a persisted `ClaimRow`. The
   hypothesis's `subject_ref` must point at a **persisted innovation
   concept entity** (kind="project"), and `plan_experiment` calls
   `critique_innovation(session, concept_id)` to load critique
   context. A scenario entity (kind="scenario") does **not** satisfy
   this contract — it lacks the innovation attributes
   (`problem_domain`, `uncertainties`, `components`) that
   `plan_experiment` reads.

2. **`metrics` is `dict[str, Any]` — a `{name: dtype}` schema**, not
   a list of entity IDs. Missing dependencies are entity/capability
   references; they are **not** measurement protocols. The previous
   G06-P00 statement "the scenario's `missing_dependencies` become
   the metrics to measure" was **incorrect** and is retracted. G06
   must not treat missing dependencies as numerical metrics without a
   defined measurement protocol.

3. **Only 16 relationship predicates are supported**
   (`src/synapse/domain/relationship.py:21-37`): PROVIDES, REQUIRES,
   ENABLES, IMPROVES, LIMITS, INTEGRATES_WITH, REPLACES, DEPENDS_ON,
   SUPPORTS, CONTRADICTS, VALIDATES, INVALIDATES, INSPIRED_BY,
   USEFUL_FOR, TESTS, PRODUCES. The previous G06-P00 plan invented
   `SCENARIO_DERIVES_FROM`, `SCENARIO_PROTOTYPED_BY`, and
   `SCENARIO_DEPENDS_ON` — these **do not exist** and G06 must not
   invent them.

#### 4.6.2 Corrected linkages (all preserve G05 contracts)

1. **Future-capability-gap annotation**: when a scenario identifies a
   `missing_dependency` that is also a G05 `combine_knowledge()`
   candidate component, the scenario annotates the innovation concept
   with a `future_risk` note via a new
   `AuditEventRow(event_type="scenario.innovation_annotation")` with
   `target_id = <innovation_concept_id>`. This does NOT modify the
   innovation's persisted attributes — it is an append-only audit
   annotation. (The previous `event_type="innovation.future_annotation"`
   is renamed to `scenario.innovation_annotation` to clarify that the
   scenario is the actor, not the innovation.)

2. **Emerging-opportunity flag**: when a scenario's
   `dependency_readiness` is `1.0` (all missing dependencies are
   engineering-tractable) AND `evidence_coverage >= 0.5`, the scenario
   is flagged `"emerging_opportunity": true` in its `risks` dict. This
   is a deterministic boolean, not a threshold-crossing claim (no
   Layer 2 temporal signal).

3. **Prototype plan — two-mode design** (corrected): the
   `POST /future/scenarios/{id}/prototype-plan` endpoint operates in
   one of two modes, determined by the scenario's linkage to a G05
   innovation:

   - **Mode A — linked innovation exists**: if the scenario is linked
     to a G05 innovation concept via an existing `INSPIRED_BY` or
     `SUPPORTS` relationship (created by the caller or by the
     scenario builder), and that innovation has a persisted
     hypothesis (`ClaimRow` with `subject_ref = <concept_id>`), the
     endpoint delegates to `plan_experiment(session, hypothesis_id,
     protocol=scenario.prototype_plan, metrics=<caller-supplied>)`.
     The `metrics` MUST be supplied by the caller as a
     `{name: dtype}` dict — G06 does NOT fabricate metrics from
     `missing_dependencies`. The linkage is recorded via an existing
     predicate (`TESTS` — the scenario tests the innovation) or via
     an `AuditEventRow(event_type="scenario.prototype_linked")`
     referencing both the scenario and the experiment.

   - **Mode B — no linked innovation**: if the scenario has no linked
     G05 innovation concept, the endpoint **cannot** call
     `plan_experiment` (it would fail the hypothesis resolution). In
     this mode, the endpoint returns a **prototype plan proposal** —
     a structured dict containing the scenario's `prototype_plan`,
     the `missing_dependencies` (as entity references to investigate,
     NOT as metrics), and a note that the caller must create a G05
     innovation + hypothesis first if they want a persisted
     experiment. The proposal is recorded as
     `AuditEventRow(event_type="scenario.prototype_proposed")`. No
     `Experiment` entity is created in Mode B.

   This two-mode design preserves `plan_experiment`'s contract
   (hypothesis_id + innovation concept resolution) while still
   delivering value for scenarios that are not yet linked to an
   innovation.

4. **Innovation timing note**: a scenario at F2 (near-term
   engineering) that shares capabilities with a G05 innovation
   concept suggests the innovation is "timely". A scenario at F4
   (speculative) suggests the innovation is "ahead of its time".
   These are notes in the `risks` dict, not gates — G05's existing
   contracts are unchanged. The timing note is recorded as an
   `AuditEventRow(event_type="scenario.innovation_annotation")` with
   `target_id = <innovation_concept_id>`.

#### 4.6.3 Scenario-to-experiment linkage options (Correction E)

G06 uses **existing relationships or audit references** for
scenario-to-experiment linkage — no new predicates:

| Linkage | Mechanism | Existing predicate / audit |
|---------|-----------|---------------------------|
| Scenario → innovation concept | `INSPIRED_BY` (scenario is INSPIRED_BY innovation) or `SUPPORTS` (scenario SUPPORTS the innovation's hypothesis) | existing predicates |
| Scenario → experiment | `AuditEventRow(event_type="scenario.prototype_linked", target_id=<experiment_id>)` + `payload.scenario_id` | audit reference |
| Scenario → evidence fragment | `current_evidence_refs` list in scenario attributes | existing field |
| Scenario → capability | `capabilities` list in scenario attributes | existing field |

No `SCENARIO_*` predicate is invented. All linkages use either
existing predicates or audit-event references.

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

### 7.2 Uncertainty policy (corrected — Correction C)

#### 7.2.1 What `uncertainty` measures

The `uncertainty` field on the `Scenario` domain record (range
[0.0, 1.0], already defined in `src/synapse/domain/scenario.py:36`)
is populated **deterministically** as:

```
uncertainty = 1.0 - evidence_coverage
```

where `evidence_coverage` is the ratio computed in §4.4 (the fraction
of `current_evidence_refs` that resolve to verified fragments in the
KB). This is the **only** input to `uncertainty`.

#### 7.2.2 What `uncertainty` does NOT measure

`uncertainty` is **not**:

- **A probability of future occurrence.** It does not estimate how
  likely the scenario is to come true. It measures only the current
  evidence-coverage gap.
- **A function of the horizon class.** The horizon class (F0–F4) is a
  **separate** categorical label (§3.1) that describes the *type* of
  evidence gap (integration unevidenced, engineering missing,
  research missing, speculative). The horizon does NOT adjust
  `uncertainty` numerically. The previous G06-P00 formula
  `+0.1 * (5 - horizon_index)` was **inverted and incorrect** — it
  would have made F4 (speculative) scenarios appear *more certain*
  merely because of their class, which is epistemically wrong. More
  speculative horizons must not appear more certain.
- **A function of contested assumptions.** Contested assumptions are
  reported as a separate integer count (`contested_assumptions` in
  §4.4). They do NOT adjust `uncertainty` numerically. The previous
  G06-P00 formula `+0.1 * contested_assumptions` introduced arbitrary
  coefficients presented as calibrated uncertainty; it is removed.
- **A calibrated or normalized score.** There are no arbitrary
  coefficients, no weighting, no Bayesian prior. `uncertainty` is a
  transparent deterministic ratio.

#### 7.2.3 Why a numeric value is retained

The `uncertainty` field is retained (rather than dropped) because:

1. The frozen `Scenario` domain contract already declares it
   (`uncertainty: float | None = Field(default=None, ge=0.0, le=1.0)`).
   G06 does not modify frozen contracts.
2. `1.0 - evidence_coverage` is a transparent, deterministic function
   of a single evaluation axis. It is easy to explain, audit, and
   reproduce.
3. It is labeled explicitly in the API output as
   `"uncertainty"` with a `"uncertainty_method": "1.0 - evidence_coverage"`
   field so callers cannot misinterpret it as a probability.

#### 7.2.4 Transparent deterministic dimensions preferred

Where the frozen contract does NOT require a numeric value, G06
prefers transparent categorical dimensions over arbitrary numeric
scores. The evaluation axes in §4.4 are reported as:
- ratios (`evidence_coverage`, `dependency_readiness`)
- integer counts (`distinct_source_uri_count`,
  `independent_origin_count`, `contradiction_exposure`,
  `assumption_load`, `contested_assumptions`)
- categorical labels (`consistency`, `horizon_alignment`,
  `horizon_class`, `overall`)

No axis uses arbitrary coefficients. No axis is combined into a
single "score". The caller sees the raw dimensions and decides.

#### 7.2.5 Assumption sensitivity

Each assumption is checked against the KB for contradicting evidence.
Contested assumptions are reported by name in the evaluation output
(`contested_assumptions` count + a `contested_assumption_names` list).
The caller decides whether to revise them. This is a separate
dimension from `uncertainty`.

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
  "metrics": {"metric_name": "float", ...}   # caller-supplied {name: dtype} schema
}
Response 200 (Mode A — linked innovation exists): {
  "data": {
    "mode": "linked_experiment",
    "experiment": Experiment,               # from plan_experiment()
    "innovation_concept_id": "innov-...",
    "hypothesis_id": "hyp-...",
    "audit_event_id": "aev-..."             # scenario.prototype_linked
  }
}
Response 200 (Mode B — no linked innovation): {
  "data": {
    "mode": "prototype_proposal",
    "prototype_plan": "...",                # the scenario's prototype_plan
    "missing_dependencies": ["ent-...", ...],  # entity refs to investigate (NOT metrics)
    "note": "No linked G05 innovation. Create an innovation + hypothesis to persist an experiment.",
    "audit_event_id": "aev-..."             # scenario.prototype_proposed
  }
}
Response 422: scenario is F4 without critique; or Mode A requested
              but no linked innovation; or metrics missing in Mode A
```

**Mode selection**: the endpoint checks whether the scenario has an
existing `INSPIRED_BY` or `SUPPORTS` relationship to a G05 innovation
concept with a persisted hypothesis. If yes → Mode A (delegates to
`plan_experiment`). If no → Mode B (returns a proposal, no experiment
created). The caller cannot force Mode A without a linked innovation.

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
| `plan_experiment()` | **Called by** `POST /future/scenarios/{id}/prototype-plan` **only in Mode A** (when the scenario is linked to a G05 innovation with a persisted hypothesis). The scenario's `prototype_plan` becomes the experiment's `protocol`. The `metrics` MUST be caller-supplied as a `{name: dtype}` dict — G06 does NOT fabricate metrics from `missing_dependencies` (Correction E). In Mode B (no linked innovation), the endpoint returns a proposal without calling `plan_experiment`. |
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
| QG-6 | `uncertainty` is in [0.0, 1.0] and equals `1.0 - evidence_coverage` | Pydantic field constraint + deterministic formula |
| QG-7 | Horizon override may only downgrade | `scenario_builder` checks `horizon_index(override) >= horizon_index(derived)` |
| QG-8 | No fabricated trends — every signal is `temporal_type="snapshot"` | `FutureSignalReport.temporal_type == "snapshot"` always in G06-T01 |
| QG-9 | Every revision emits an audit event | `scenario_revision` always inserts `AuditEventRow` |
| QG-10 | No LLM call in the deterministic core | G06 source has no `import openai` / `import anthropic` / provider calls |
| QG-11 | F0 and F1 are mutually exclusive on integration evidence | Negative tests: evidenced integration + empty deps → F0; unevidenced integration + integration dep → F1; evidenced integration + non-empty deps → not F0 |
| QG-12 | F1 requires ≥1 honestly unresolved integration dependency; fail-closed if none | `scenario_builder` raises `ValueError` if F1 derived but no integration dependency can be named |
| QG-13 | No `gap_transition` or `dependency_shift` signal kinds exist | Source code does not contain these identifiers |
| QG-14 | `independent_origin_count` is conservative (1) unless provenance metadata differs | `future_signal` checks `SourceRow.publisher`/`author`; defaults to 1 |
| QG-15 | No `SCENARIO_*` relationship predicate is invented | G06 source uses only the 16 existing predicates from `PREDICATES` |
| QG-16 | `plan_experiment` is called only in Mode A (linked innovation + hypothesis) | `prototype_plan` endpoint checks for `INSPIRED_BY`/`SUPPORTS` relationship before delegating |
| QG-17 | `uncertainty` does not depend on `horizon_class` or `contested_assumptions` | `uncertainty == 1.0 - evidence_coverage`; no horizon/assumption coefficients |

### 11.3 Test coverage

| Capability | Mandatory tests |
|------------|-----------------|
| A — Signal identification | ≥10 tests: each snapshot signal kind, `temporal_type="snapshot"` invariant, `snapshot_only=True` invariant, evidence footprint, gap classification propagation (all 6 types), empty KB, deterministic ordering, no fabricated trends, conservative `independent_origin_count`, `distinct_source_uri_count` ≠ `independent_origin_count`. |
| B — Scenario construction | ≥12 tests: each horizon derivation rule (F0–F4 with precedence), F4-requires-critique, non-F0-requires-missing-deps, F0-vs-F1 mutual exclusivity on integration evidence, F1 fail-closed (no honest integration dep), horizon override downgrade-only, idempotent retry (same run_id), alternative futures (different assumptions → different fingerprint), cross-session persistence, invalid signal, invalid capability, provenance preservation. |
| C — Alternative futures | ≥5 tests: comparison dimensions, shared signal, shared evidence, horizon distance, no artificial probabilities. |
| D — Evaluation | ≥10 tests: each axis, overall label derivation, inconsistent flag, horizon violation, no fabricated evidence, contested assumptions, deterministic output, empty evidence, `uncertainty == 1.0 - evidence_coverage` (no horizon/assumption adjustment), `independent_origin_count` conservative. |
| E — Revision | ≥6 tests: explicit reassess, new evidence trigger, new experiment result trigger, no-change (revised=false), revision history ordering, prior version preserved. |
| F — G05 linkage | ≥7 tests: Mode A prototype plan delegates to `plan_experiment` (with caller-supplied metrics), Mode B returns proposal (no experiment), `INSPIRED_BY`/`SUPPORTS` linkage, `scenario.innovation_annotation` audit event, `emerging_opportunity` flag (deterministic boolean), innovation timing note, no `SCENARIO_*` predicate used. |
| API + OpenAPI | ≥6 tests: each endpoint auth, 404, 422, 200, OpenAPI schema includes all 7 routes, Mode A vs Mode B response shapes. |
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
1. Each snapshot signal kind produces a well-formed `FutureSignalReport`
   with `temporal_type="snapshot"`.
2. `snapshot_only == True` AND `temporal_type == "snapshot"` on every
   signal.
3. Evidence footprint snapshot reports factual fragment count and
   `distinct_source_uri_count`.
4. `independent_origin_count` is conservative (1) unless explicit
   `SourceRow.publisher`/`author` metadata differs; never equals
   `distinct_source_uri_count` unless provenance is verified.
5. Gap classification propagated from `analyze_gap()` (all 6 types:
   supported, partially_supported, not_evidenced, contested,
   constrained, unknown).
6. Combination density computed from `combine_knowledge()`.
7. Empty KB → empty signals list (no fabricated signals).
8. Deterministic ordering (same KB → same signal order).
9. No fabricated trends: no `growth_rate`, `trend`, `delta`, or
   `change` field exists; `first_seen_at` / `last_seen_at` are
   factual timestamp ranges, not growth curves.
10. No `gap_transition` or `dependency_shift` signal kinds exist
    (removed — they required Layer 2 temporal data).
11. Provenance: every signal references real entity/capability IDs.

**Acceptance criteria**:
- All 11 tests pass.
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
1. F0 scenario: all capabilities SUPPORTED, integration evidenced
   (SUPPORTED claim or explicit relationship), no missing deps.
2. F1 scenario: all capabilities SUPPORTED, integration unevidenced,
   ≥1 honest integration dependency (real KB entity lacking
   `INTEGRATES_WITH`/`PROVIDES`/`ENABLES` to other components).
3. F1 fail-closed: integration unevidenced but no honest integration
   dependency can be named → `ValueError` (no fabricated dependency).
4. F0-vs-F1 mutual exclusivity: evidenced integration + empty deps →
   F0; unevidenced integration + integration dep → F1; evidenced
   integration + non-empty deps → not F0 (must be F2/F3).
5. F2 scenario: missing deps are all "engineering" (entity exists with
   SUPPORTED claim, integration relationship missing).
6. F3 scenario: ≥1 missing dep is "research" (entity absent or
   HYPOTHESIZED/INFERRED only).
7. F4 scenario: critique mandatory; caller-requested speculation → F4.
8. Horizon override downgrade OK; upgrade → 422.
9. Idempotent retry (same `run_id` → same `scenario_id`).
10. Alternative futures: different assumptions → different fingerprint.
11. Cross-session persistence (POST then GET from separate session).
12. Invalid signal → 404; invalid capability → 404.
13. Empty assumptions → 422.
14. Provenance: `created_by`, `created_at`, `request_id` preserved.
15. API auth: no token → 401.
16. OpenAPI includes all new routes.

**Acceptance criteria**:
- All 16 tests pass.
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
- `plan_experiment()` — called **only in Mode A** (linked innovation +
  hypothesis). G06 does NOT bypass its hypothesis/concept resolution.
- `assess_claim()` — for evidence verification.
- `analyze_gap()` — for dependency readiness.
- `RelationshipService` — for `INSPIRED_BY`/`SUPPORTS` linkage checks
  (existing predicates only; no `SCENARIO_*` predicate invented).
- `AuditEventRow` (event_type="scenario.evaluated",
  "scenario.prototype_linked", "scenario.prototype_proposed") — audit.
- `claim_fingerprint()` (PRB-03) — for prototype-plan idempotency.

**Implementation boundary**:
- New file: `src/synapse/application/scenario_evaluator.py`
- Modify: `src/synapse/api/v1/future.py` — add `/evaluate` and
  `/prototype-plan` endpoints.
- No new domain records.
- No new ORM tables.
- No new migrations.
- No new relationship predicates (use only the 16 existing ones).

**Expected files**:
- `src/synapse/application/scenario_evaluator.py` (~350 LOC)
- `tests/integration/test_g06_t03_evaluation_prototype.py` (~450 LOC)

**Mandatory tests**:
1. Each evaluation axis computed correctly (ratio/count/categorical).
2. `overall` label derivation (testable / needs_more_evidence /
   speculative / inconsistent).
3. `inconsistent` flag when evidence contradicts an assumption.
4. `horizon_violation` when caller tried to upgrade.
5. No fabricated evidence (empty refs → coverage 0.0).
6. Contested assumptions reported by name.
7. Deterministic output (same scenario → same evaluation).
8. `uncertainty == 1.0 - evidence_coverage` — no horizon/assumption
   coefficients (Correction C).
9. `independent_origin_count` conservative (1) unless provenance
   metadata differs; `distinct_source_uri_count` reported separately.
10. Mode A prototype plan: linked innovation exists → delegates to
    `plan_experiment()` with caller-supplied `metrics` (NOT
    missing_dependencies); `scenario.prototype_linked` audit event
    emitted; no `SCENARIO_*` predicate used.
11. Mode B prototype plan: no linked innovation → returns proposal
    (no experiment created); `scenario.prototype_proposed` audit
    event emitted; `missing_dependencies` listed as entity refs to
    investigate (NOT metrics).
12. Mode A without caller-supplied metrics → 422 (G06 does not
    fabricate metrics from missing_dependencies).
13. F4 scenario without critique → 422 on prototype-plan.
14. API auth + 404 + 422.
15. OpenAPI includes new routes; Mode A vs Mode B response shapes
    documented.

**Acceptance criteria**:
- All 15 tests pass.
- `ruff check` clean.
- `make openapi-check` passes.
- Full deterministic regression still passes.
- No `SCENARIO_*` predicate in G06 source (grep-verified).
- `plan_experiment` is called only when a linked innovation + hypothesis
  resolves (Mode A); never called in Mode B.

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
| G06-T01 | Future Signal Identification | ~650 | 11 | Yes |
| G06-T02 | Scenario Construction + API | ~1100 | 16 | Yes |
| G06-T03 | Scenario Evaluation + Prototype Plan | ~800 | 15 | Yes |
| G06-T04 | Scenario Revision + E2E Acceptance | ~800 | 12 | Yes (G06 closure) |
| **Total** | | **~3350** | **54** | |

This is a **compact** implementation. For comparison, G05 was ~5000
LOC across 6 tasks. G06 is smaller because it **reuses** G04/G05
heavily and adds no new domain contracts, no new ORM tables, no new
migrations, and no new relationship predicates.

The four-task structure is **preserved** (Correction F). No task was
added or removed. The corrections only refined acceptance criteria
within existing tasks.

---

## 14. Mandatory planning evaluation

| Question | Answer |
|----------|--------|
| Does G06 provide genuine future intelligence rather than generic speculative text? | **Yes.** Every scenario is grounded in `current_evidence_refs` and `missing_dependencies` derived from the KB. F4 scenarios require a critique. `temporal_type="snapshot"` on every signal prevents fabricated trends. |
| Are evidence, inference, and speculation clearly separated? | **Yes.** Evidence = `current_evidence_refs` (fragments). Inference = horizon derivation from gap classifications. Speculation = F4 with mandatory critique. The three are never conflated. |
| Can scenarios be constructed without inventing unavailable historical data? | **Yes.** G06 uses only the current KB snapshot (Layer 1). Layer 2 (verified historical change) is explicitly unavailable — no `gap_transition` or `dependency_shift` signals exist. `first_seen_at`/`last_seen_at` are factual timestamp ranges, not growth curves. |
| Are assumptions explicit and testable? | **Yes.** Assumptions are a mandatory list of strings. Each assumption is checked against the KB for contradicting evidence (`contested_assumptions`). The caller can revise assumptions and re-evaluate. |
| Can scenarios be revised when evidence changes? | **Yes.** G06-T04 implements explicit reassessment with append-only revision history. Prior versions are preserved. |
| Does G06 reuse G04/G05 rather than duplicate their reasoning? | **Yes.** §10 documents the consumption. G06 does not call `critique_innovation()` or `answer_query()`; it reuses `analyze_gap()`, `hybrid_retrieve()`, `plan_experiment()` (Mode A only, with caller-supplied metrics), and `get_evidence_deltas()`. |
| Is the proposed implementation proportionate to the product's needs? | **Yes.** ~3350 LOC across 4 tasks; no new tables; no new migrations; no new domain contracts; no new predicates; no new infrastructure dependencies. |
| Can the first useful scenario be demonstrated early? | **Yes.** After G06-T02 (the second task), a caller can build and retrieve a scenario. The first useful scenario is demonstrated before evaluation (T03) or revision (T04). |
| Are F0 and F1 mutually exclusive and deterministic? | **Yes (Correction A).** F0 requires evidenced integration + empty deps; F1 requires unevidenced integration + ≥1 honest integration dependency. Precedence-ordered rules with negative tests enforce mutual exclusivity. F1 fails closed if no honest integration dependency can be named. |
| Is `uncertainty` transparent and non-probabilistic? | **Yes (Correction C).** `uncertainty = 1.0 - evidence_coverage` — no horizon/assumption coefficients. It is labeled `uncertainty_method` in the API output. It is not a probability of future occurrence. |
| Are source URLs conflated with independent origins? | **No (Correction D).** `distinct_source_uri_count` is a factual URL count; `independent_origin_count` is conservative (1, per PRB-04) unless provenance metadata differs. The two are reported separately. |
| Does G06 invent relationship predicates? | **No (Correction E).** G06 uses only the 16 existing predicates from `PREDICATES`. Scenario-to-experiment linkage uses `INSPIRED_BY`/`SUPPORTS`/`TESTS` or audit-event references. |

**All twelve questions answered affirmatively.** No feature was
identified that should be removed or deferred beyond what the
corrections already removed (`gap_transition`, `dependency_shift`,
`SCENARIO_*` predicates, horizon/assumption uncertainty coefficients).

---

## 15. Deliverable summary (G06-P00C — corrected)

```
G06_P00C_STATUS = PASS
AUTHORITATIVE_START_SHA = 5a0582154fb85af6a85fea20e0f214ca5880380b
F0_F1_CLASSIFICATION = RESOLVED
SCENARIO_CONTRACT_COMPATIBILITY = PASS
UNCERTAINTY_SEMANTICS = PASS
TEMPORAL_SIGNAL_INTEGRITY = PASS
SOURCE_INDEPENDENCE = PASS
EXISTING_API_AND_RELATIONSHIP_CONTRACTS = PASS
FOUR_TASK_PLAN_PRESERVED = YES
PRODUCTION_CODE_MODIFIED = NO
PLAN_FILE = reports/G06_FUTURE_INTELLIGENCE_IMPLEMENTATION_PLAN.md
FINAL_SHA = <populated after commit>
READY_FOR_G06_T01_REVIEW = YES
```

---

## 16. Correction summary (G06-P00C)

This section documents the five focused corrections applied to the
G06-P00 plan, their final decisions, and their implications for
G06-T01–T04.

### 16.1 Correction A — F0/F1 classification

**Problem**: F0 (existing configuration) and F1 (possible but uncommon
combination) overlapped. An existing set of components does not prove
that their proposed integration exists.

**Decision**: F0 and F1 are now **mutually exclusive on the
integration-evidence axis**. F0 requires evidence of the *integration
itself* (a SUPPORTED claim or an explicit relationship documenting the
combination) AND empty `missing_dependencies`. F1 requires that the
components are each SUPPORTED but the integration is unevidenced, AND
that `missing_dependencies` contains ≥1 honestly unresolved
integration dependency (§3.2.1). Horizon assignment uses precedence-
ordered rules (F4 > F3 > F2 > F1 > F0) with negative tests (§3.2).

**Implications**:
- G06-T02 gains 2 new mandatory tests: F0-vs-F1 mutual exclusivity,
  and F1 fail-closed (no honest integration dependency → ValueError).
- The `scenario_builder` must check for integration evidence
  (SUPPORTED claim or `INTEGRATES_WITH`/`PROVIDES`/`ENABLES`
  relationship) before assigning F0.
- QG-11 and QG-12 enforce the mutual exclusivity and fail-closed
  behavior.

### 16.2 Correction B — Scenario contract compatibility

**Problem**: The frozen `Scenario._non_f0_must_list_missing_deps`
validator requires every non-F0 scenario to have ≥1
`missing_dependency`. F1 scenarios (possible but uncommon combination)
may not have a missing dependency, only an unevidenced integration.

**Decision**: G06 does **not** modify the frozen validator. Instead,
F1 scenarios satisfy the validator **honestly** by populating
`missing_dependencies` with **explicitly unresolved integration
dependencies** — real KB entity IDs that (a) exist in the KB, (b)
have a SUPPORTED claim, and (c) lack an integration relationship to
the scenario's other components (§3.2.1). If no honest integration
dependency can be named, the builder **fails closed** (§3.2.2) rather
than fabricating a dependency.

**Implications**:
- The `Scenario` domain record is unchanged (frozen contract
  preserved).
- F1 scenarios always have ≥1 real, traceable `missing_dependency`.
- The fail-closed behavior prevents hand-waving futures.
- This is the smallest compatible representation: no new domain
  field, no new predicate, no validator modification.

### 16.3 Correction C — Uncertainty semantics

**Problem**: The G06-P00 formula
`uncertainty = 1.0 - evidence_coverage + 0.1 * contested_assumptions + 0.1 * (5 - horizon_index)`
was **inverted** — the `+0.1 * (5 - horizon_index)` term would make
F4 (speculative) scenarios appear *more certain* (lower uncertainty)
merely because of their class. It also introduced arbitrary
coefficients presented as calibrated uncertainty.

**Decision**: `uncertainty = 1.0 - evidence_coverage` — the **only**
input is evidence coverage. No horizon coefficient. No assumption
coefficient. The field is retained (frozen contract) but is now a
transparent deterministic ratio. The API output includes
`uncertainty_method: "1.0 - evidence_coverage"` so callers cannot
misinterpret it as a probability. §7.2 documents exactly what
`uncertainty` measures (the evidence-coverage gap) and what it does
not (probability of occurrence, horizon class, assumption load).

**Implications**:
- QG-6 and QG-17 enforce the corrected formula.
- G06-T03 gains a mandatory test: `uncertainty == 1.0 -
  evidence_coverage` with no horizon/assumption adjustment.
- The evaluation axes in §4.4 are now all transparent dimensions
  (ratios, counts, categorical labels) — no arbitrary coefficients.

### 16.4 Correction D — Temporal signals and source independence

**Problem**: G06-P00 claimed `gap_transition` and `dependency_shift`
signals, which require verified historical change between comparable
KB snapshots. Synapse does not persist comparable snapshots. G06-P00
also conflated distinct source URLs with independent origins.

**Decision**:
- **Three-layer separation** (§4.1.1): Layer 1 (current snapshot),
  Layer 2 (verified historical change), Layer 3 (speculative
  interpretation). G06-T01 implements **only Layer 1**. Layer 2 is
  explicitly unavailable. `gap_transition` and `dependency_shift`
  signal kinds are **removed**. Every signal has
  `temporal_type="snapshot"` and `snapshot_only=True`.
- **Conservative source independence** (§4.1.3): `distinct_source_uri_count`
  is a factual URL count; `independent_origin_count` is conservative
  (1, per PRB-04) unless explicit `SourceRow.publisher`/`author`
  metadata differs. The two are reported separately. No signal claims
  "N independent sources" based on URL count.

**Implications**:
- G06-T01 gains a mandatory test: no `gap_transition` or
  `dependency_shift` signal kinds exist.
- QG-8, QG-13, QG-14 enforce the temporal-layering and source-
  independence rules.
- The signal output schema (§4.1.4) is corrected: `source_independence`
  is replaced by `distinct_source_uri_count` + `independent_origin_count`.

### 16.5 Correction E — Existing contract compatibility

**Problem**: G06-P00 invented three relationship predicates
(`SCENARIO_DERIVES_FROM`, `SCENARIO_PROTOTYPED_BY`,
`SCENARIO_DEPENDS_ON`) that do not exist in the 16-predicate
vocabulary. G06-P00 also stated that `plan_experiment()` would be
called with the scenario's `missing_dependencies` as metrics — but
`plan_experiment` requires a `hypothesis_id` resolving to an
innovation concept, and `metrics` is a `{name: dtype}` schema, not a
list of entity IDs.

**Decision**:
- **No new predicates**. G06 uses only the 16 existing predicates
  (§4.6.3). Scenario-to-experiment linkage uses `INSPIRED_BY`,
  `SUPPORTS`, `TESTS`, or audit-event references.
- **Two-mode prototype-plan endpoint** (§4.6.2): Mode A (linked
  innovation + hypothesis exists) delegates to `plan_experiment()`
  with **caller-supplied** `metrics` (G06 does NOT fabricate metrics
  from `missing_dependencies`). Mode B (no linked innovation) returns
  a proposal without calling `plan_experiment()`.
- The `metrics` parameter is explicitly caller-supplied in Mode A;
  Mode A without metrics → 422.

**Implications**:
- QG-15 and QG-16 enforce no-new-predicates and Mode-A-only
  `plan_experiment` delegation.
- G06-T03 mandatory tests are expanded from 13 to 15 to cover Mode A,
  Mode B, and the no-fabricated-metrics rule.
- The API contract (§9.3) documents both Mode A and Mode B response
  shapes.
- §10.2 G05 integration table is corrected: `plan_experiment` is
  called only in Mode A with caller-supplied metrics.

### 16.6 Correction F — Plan update scope

**Decision**: Only affected sections of the plan were revised (§3.1,
§3.2, §4.1, §4.4, §4.6, §7.2, §9.3, §10.2, §11.2, §11.3, §13.1–13.5,
§14, §15). The four-task structure (G06-T01–T04) is **preserved** —
no task was added or removed. Acceptance criteria within tasks were
refined to reflect the corrections. This new §16 documents the final
decisions and implications.

---

## 17. Stop condition

G06-P00C is **planning correction only**. No production code, no
migrations, no domain-contract changes, no activated API endpoints
are produced by this task.

**Do not begin G06-T01 until ChatGPT reviews and approves the
corrected plan.**

The corrected plan is committed and pushed for review.
