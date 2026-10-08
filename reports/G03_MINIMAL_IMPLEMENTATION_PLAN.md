# G03 Minimal Implementation Plan (Revised) — Knowledge Store, Relationships & Verification

> **Status**: Preparation only — NOT authorized for production implementation.
> Revised per the user's "G03 Plan Refinement" message (11 requirements).
> Awaiting explicit user approval.

## 1. Executive summary

G03 transforms the evidence fragments produced by G02's acquisition
pipeline into structured, traceable knowledge: **typed knowledge
units** (Entity, Capability, Technique, Constraint, Tradeoff,
FailureMode, Applicability, Opportunity, Claim), **evidence-backed
relationships** with a compact typed vocabulary, and **verification
states** that distinguish observed source statements from verified
claims, derived relationships, hypotheses, and contradictions.

The existing G01 domain contracts (`Entity`, `Claim`, `Relationship`,
`EvidenceFragment`, `EvidenceDelta`) already implement the binding
invariants. G03 adds storage tables, an extensible extraction
contract, the `RelationshipService`, the verification engine, and
the API endpoints — without introducing a graph database, a message
broker, or new infrastructure.

**Core principle preserved**: *"Graph Thinking from Day One —
Dedicated Graph Database Only When Justified."* The existing G01
domain models treat Relationships as first-class records with typed
predicates, origin tracking (`explicit | derived | hypothesized`),
and verification state. Storage uses relational tables in PostgreSQL
(via SQLAlchemy + Alembic) with recursive CTEs for graph traversal.

**Key revision from v1**: G03 now extends beyond identifier
extraction to **typed technical knowledge units**. Extraction is
**decoupled** from the G02 acquisition transaction — successful
acquisition commits even if knowledge extraction fails. An
**extensible extraction contract** is defined for future
semantic/LLM-based extraction without implementing a large LLM
framework now.

## 2. Preserved G01 contracts and PostgreSQL-based graph architecture

### Domain records (G01 — UNCHANGED by G03)

G03 does NOT modify any existing G01 domain record. All invariants
remain enforced by Pydantic v2 `model_validator`:

| Record | Key fields | Invariants (enforced by G01) |
|--------|-----------|------------------------------|
| `Entity` | id, kind (EntityType), canonical_name, aliases, attributes, canonical_uri | No duplicate aliases; canonical_name not in aliases |
| `Claim` | id, proposition, subject_ref, object_ref, evidence_refs, epistemic_state, confidence_value, confidence_method, validity_conditions | Supported → must have evidence_refs; hypothesized → must not; confidence requires method |
| `Relationship` | id, from_entity_id, to_entity_id, predicate (16 typed), direction, origin, verification_state, evidence_refs, derivation_chain, confidence, conditions, valid_from/to | Derived/hypothesized never auto-promoted to verified; explicit+verified requires evidence; derived requires derivation_chain; no self-loops |
| `EvidenceFragment` | id, acquisition_id, exact_excerpt, excerpt_hash, locator, source_id, source_uri | Must have excerpt or hash; must preserve source linkage |
| `EvidenceDelta` | id, hypothesis_id, prior_state, observation, update_method, updated_state, evidence_refs | Must change state; rejected requires evidence |

### Existing predicate vocabulary (G01 — 16 predicates, UNCHANGED)

```
PROVIDES, REQUIRES, ENABLES, IMPROVES, LIMITS,
INTEGRATES_WITH, REPLACES, DEPENDS_ON, SUPPORTS,
CONTRADICTS, VALIDATES, INVALIDATES, INSPIRED_BY,
USEFUL_FOR, TESTS, PRODUCES
```

Each predicate has a declared direction (`directed` or `undirected`).
G03 does NOT add predicates — the G01 vocabulary is sufficient for
the minimal slice.

### PostgreSQL-based graph architecture

The `relationships` table IS an edge table. Multi-hop queries use
**recursive CTEs** (Common Table Expressions) — the standard SQL
approach for graph traversal, supported by both PostgreSQL and
SQLite (≥3.8.3). No Neo4j, no Redis, no message broker.

## 3. Typed technical knowledge units

### Beyond identifiers — the 8 typed knowledge units

G03 extends extraction beyond simple identifier matching (arXiv IDs,
DOIs, GitHub repos) to **typed technical knowledge units**. Each unit
is an `Entity` with a specific `kind` from the existing G01
`EntityType` enum, plus typed attributes:

| Knowledge unit | Entity.kind | What it represents | Example |
|----------------|-------------|---------------------|---------|
| **Capability** | `capability` | What a tool/technique can do | "web article extraction" |
| **Technique** | `technique` | A method or approach | "self-attention mechanism" |
| **Constraint** | `constraint` | A limitation or requirement | "requires GPU with ≥8GB VRAM" |
| **Tradeoff** | `constraint` | A cost/benefit tension | "higher accuracy vs. slower inference" |
| **FailureMode** | `constraint` | How a system can fail | "hallucination under low-context" |
| **Applicability** | `constraint` | When something is applicable | "useful for NLP tasks with long context" |
| **Opportunity** | `capability` | A potential improvement | "combining X with Y could enable Z" |
| **Claim** | (Claim record, not Entity) | A proposition about the world | "Transformer architecture outperforms RNNs on long sequences" |

**Tradeoff, FailureMode, Applicability** use `kind=constraint` with a
`subtype` attribute to distinguish them. This avoids bloating the
`EntityType` enum while preserving type information for queries.

### Extensible extraction contract

```python
# src/synapse/domain/extraction_contract.py (new, ~80 LOC)

class KnowledgeUnitType(StrEnum):
    """Typed knowledge unit categories for extraction."""
    IDENTIFIER = "identifier"        # arXiv ID, DOI, GitHub repo, URL
    CAPABILITY = "capability"
    TECHNIQUE = "technique"
    CONSTRAINT = "constraint"
    TRADEOFF = "tradeoff"
    FAILURE_MODE = "failure_mode"
    APPLICABILITY = "applicability"
    OPPORTUNITY = "opportunity"
    CLAIM = "claim"


class ExtractionResult(DomainRecord):
    """Output of a single extraction pass on one EvidenceFragment.

    This is the contract between the extraction layer and the
    persistence layer. Future LLM-based extractors must produce
    the same shape.
    """
    evidence_fragment_id: str
    source_span: SourceSpan           # where in the text the unit was found
    unit_type: KnowledgeUnitType
    entity_kind: EntityType           # maps to Entity.kind
    canonical_name: str
    attributes: dict[str, Any]         # type-specific attributes
    proposition: str | None           # for Claim-type units
    epistemic_state: EpistemicState    # supported (has evidence) or hypothesized
    extraction_method: str             # "regex-v1" | "llm-future-v1" | ...
    extraction_confidence: float | None = None  # method-specific; NOT empirical probability


class SourceSpan(DomainRecord):
    """Precise reference to where in the evidence text a unit was found.

    Preserves the char offsets so knowledge claims can be traced
    back to their supporting content — per requirement #8.
    """
    evidence_fragment_id: str
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)
    excerpt: str                      # the exact text at [start, end)
    context_before: str | None = None # ~50 chars before (for disambiguation)
    context_after: str | None = None  # ~50 chars after
```

**Future LLM-based extraction**: a future group can implement an
`LLMExtractor` that produces `ExtractionResult` objects with
`extraction_method="llm-future-v1"`. The persistence layer and
verification engine remain unchanged — they consume `ExtractionResult`
regardless of how it was produced. No LLM framework is implemented
in G03.

## 4. Compact, typed relationship vocabulary

### Direction, conditions, evidence, confidence, provenance, epistemic status

The existing G01 `Relationship` record already carries all required
fields. G03 defines which subset is used in the minimal vertical slice:

| Field | Type | Used in minimal slice? | Notes |
|-------|------|----------------------|-------|
| `from_entity_id` | str | ✅ | Source entity of the edge |
| `to_entity_id` | str | ✅ | Target entity of the edge |
| `predicate` | str (16 vocab) | ✅ | One of the 16 G01 predicates |
| `direction` | `directed \| undirected \| bidirectional` | ✅ | Inherited from predicate metadata |
| `origin` | `explicit \| derived \| hypothesized` | ✅ | **Critical** — never auto-promoted (requirement #6) |
| `verification_state` | `unverified \| weak \| strong \| verified \| contradicted \| rejected` | ✅ | Starts at `unverified`; only moves to `verified` with evidence + review |
| `evidence_refs` | list[str] | ✅ | EvidenceFragment IDs supporting this edge |
| `confidence_value` | float \| None | ❌ (not assigned) | Per requirement: no decorative probabilities |
| `confidence_method` | str \| None | ❌ (not assigned) | If confidence were set, method is required (G01 invariant) |
| `conditions` | list[str] | ✅ (when available) | e.g., "under low-context conditions" |
| `derivation_chain` | list[str] | ✅ (for derived) | Required when `origin=derived` (G01 invariant) |
| `valid_from` / `valid_to` | str \| None | ❌ (deferred) | Temporal validity — future group |
| `superseded_by` | str \| None | ❌ (deferred) | Versioning — future group |

## 5. Epistemic distinction — 5 categories

Per requirement #5, G03 explicitly distinguishes:

| Category | What it is | Storage representation | How it enters the system |
|----------|-----------|----------------------|--------------------------|
| **Observed source statement** | Text directly quoted from a source | `EvidenceFragment.exact_excerpt` + `SourceSpan` | G02 acquisition → trafilatura extraction |
| **Verified claim** | A proposition backed by ≥1 evidence fragment, no contradiction | `Claim(epistemic_state=supported, evidence_refs=[...])` | G03 extraction → verification engine |
| **Derived relationship** | An edge inferred from explicit knowledge (not directly stated) | `Relationship(origin=derived, derivation_chain=[...], verification_state=unverified)` | G03 RelationshipService (future: reasoning engine) |
| **Hypothesis** | A proposed explanation with no evidence yet | `Claim(epistemic_state=hypothesized, evidence_refs=[])` | Future: innovation engine (G05). G03 stores but does not generate. |
| **Contradiction** | Two claims/relationships with conflicting predicates on the same pair | Both coexist; both marked `disputed` | G03 verification engine detects; never auto-resolves |

### Rule: hypotheses are NEVER auto-promoted

Per requirement #6: *"Never promote hypotheses to verified facts
automatically based solely on repetition or model confidence."*

**Enforcement** (already in G01 domain model):
- `Claim(epistemic_state=hypothesized)` MUST NOT have `evidence_refs`
  (G01 invariant — `hypothesized` means "no evidence yet")
- Moving from `hypothesized` → `supported` requires:
  1. An explicit `EvidenceDelta` record (immutable audit)
  2. At least one `evidence_ref` pointing to an `EvidenceFragment`
  3. The transition is initiated by a **human review** or a **verified
     experiment result** (G05) — NOT by repetition count or LLM
     confidence score
- The verification engine (G03-T04) enforces: no `verified` state
  without `evidence_refs`, no auto-promotion based on count

## 6. Decoupled extraction from G02 acquisition

### Requirement #7: successful acquisition commits even if extraction fails

**Current G02 behavior**: `acquire_and_extract()` is a single
transaction that:
1. Validates URL (SSRF)
2. Fetches HTML
3. Extracts text (trafilatura)
4. Fingerprints content
5. Persists SourceRow + EvidenceFragmentRow + AuditEventRow
6. Commits

**G03 revised approach**: extraction is **decoupled** into a
separate, resumable step. The G02 transaction commits **before**
extraction runs. If extraction fails, the acquisition is still
committed — the evidence fragment is in the DB and can be
re-processed later.

**Simplest resumable mechanism** (no message broker):

```
G02 acquire_and_extract():
  1-5. (unchanged) — fetch, extract text, fingerprint, persist
  6. COMMIT transaction ← acquisition is now durable
  7. Create a JobRow(kind="extract_knowledge",
       status="queued",
       input_ref=evidence_fragment_id)
  8. COMMIT job row

G03 knowledge extraction worker (same process, async):
  - Polls JobRow WHERE kind="extract_knowledge" AND status="queued"
  - Runs extract_knowledge(session, evidence_fragment)
  - On success: job.status="succeeded", output_ref=extraction_summary
  - On failure: job.status="failed", error_code=...
  - Job is retryable: set status back to "queued" to re-run
```

This uses the **existing G01 JobRow table** and the **existing Job
state machine** (queued → running → succeeded/failed). No new
infrastructure. The "worker" is an async function called from the
API layer or a future scheduler — not a separate process.

### Code change to G02 (minimal)

```python
# In acquire_and_extract(), AFTER evidence_row is committed:
# (existing G02 code unchanged through the commit)

# NEW: queue knowledge extraction as a separate job
job_row = JobRow(
    id=uuid4().hex,
    kind="extract_knowledge",
    requester=requester,
    status="queued",
    input_ref=evidence_row.id,
)
session.add(job_row)
await session.commit()  # commits the job; acquisition already committed
```

If the job fails, the evidence fragment is still in the DB. The job
can be retried by setting `status="queued"` again.

## 7. Source spans — precise evidence references

### Requirement #8: preserve source spans

Every `ExtractionResult` carries a `SourceSpan` that records:

- `evidence_fragment_id` — which evidence fragment the unit came from
- `start_offset`, `end_offset` — character offsets into
  `EvidenceFragment.exact_excerpt`
- `excerpt` — the exact text at those offsets (redundant with
  `exact_excerpt[start:end]` but preserved for query convenience)
- `context_before`, `context_after` — ~50 chars of surrounding text
  for disambiguation

**Storage**: `source_spans` table (new migration) or JSON column on
the `claims` table. For the minimal slice, a separate table is
cleaner:

```sql
CREATE TABLE source_spans (
    id VARCHAR(64) PRIMARY KEY,
    evidence_fragment_id VARCHAR(64) NOT NULL,
    claim_id VARCHAR(64),           -- nullable: spans can predate the claim
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    excerpt TEXT NOT NULL,
    context_before TEXT,
    context_after TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX ix_source_spans_evidence_fragment_id ON source_spans (evidence_fragment_id);
CREATE INDEX ix_source_spans_claim_id ON source_spans (claim_id);
```

This enables: *"show me the exact text in the source that supports
this claim"* — `SELECT excerpt FROM source_spans WHERE claim_id = ?`.

## 8. Minimal vertical slice

### Requirement #9: prove the full chain

The vertical slice proves:

```
EvidenceFragment
    ↓ (G03-T01: deterministic extraction)
Typed Knowledge Unit (Entity + Claim + SourceSpan)
    ↓ (G03-T03: RelationshipService)
Evidence-backed Relationship (origin=explicit, verification_state=unverified)
    ↓ (G03-T04: verification engine)
Verification State (unverified → verified, with EvidenceDelta audit)
    ↓ (G03-T05: knowledge API)
Queryable Result (GET /api/v1/entities/{id} → entity + claims + relationships + evidence)
```

**Concrete demo**: ingest one arXiv paper via G02 → extract the
paper as an Entity(kind=paper) → extract a Claim("this paper
introduces technique X") with a SourceSpan pointing to the exact
text → create a Relationship(paper →TECHNIQUE→ technique,
origin=explicit, evidence_refs=[fragment_id]) → verification engine
confirms the relationship has evidence → API returns the full
traceable chain.

## 9. Exact tasks, acceptance criteria, and code footprint

### G03-T01 — Typed knowledge extraction (deterministic + extensible contract)

**Goal**: Extract typed knowledge units from EvidenceFragment text
using deterministic patterns; define an extensible contract for
future LLM-based extraction.

**Deterministic extractors** (minimal, regex-based):

| Pattern | Knowledge unit | Entity.kind | Example |
|---------|---------------|-------------|---------|
| `\d{4}\.\d{4,5}` | Identifier | `paper` | `2303.15105` |
| `10\.\d{4,}/\S+` | Identifier | `paper` | `10.48550/arXiv.1706.03762` |
| `github.com/\S+/\S+` | Identifier | `repository` | `github.com/microsoft/playwright` |
| `"X introduces/allows/enables Y"` | Claim (technique) | `technique` | "This paper introduces the self-attention mechanism" |
| `"X requires/needs Y"` | Claim (constraint) | `constraint` | "This approach requires GPU acceleration" |
| `"X outperforms/improves Y"` | Claim (capability) | `capability` | "Transformers outperform RNNs on long sequences" |

**Extensible contract**: `ExtractionResult` + `SourceSpan` (defined
in §3). A future `LLMExtractor` class implementing
`extract(fragment: EvidenceFragment) -> list[ExtractionResult]`
plugs in without changing the persistence or verification layers.

**Deliverables**:
- `src/synapse/domain/extraction_contract.py` — typed contract (~80 LOC)
- `src/synapse/application/extraction.py` — deterministic extractor (~180 LOC)
- `alembic/versions/0003_knowledge_tables.py` — migration for
  `entities`, `claims`, `source_spans` tables (~120 LOC)
- `tests/unit/application/test_extraction.py` — deterministic tests (~150 LOC)

**Acceptance criteria**:
1. Every `Claim` with `epistemic_state=supported` has ≥1 `evidence_ref`
2. Unsupported assertions are labeled `hypothesized` (never silently promoted)
3. Every extraction result has a `SourceSpan` with valid `(start, end)` offsets
4. Re-extraction of the same evidence fragment is idempotent
5. `ExtractionResult` schema is extensible (future LLM extractor produces same shape)

**Estimated footprint**: ~530 LOC (contract + extractor + migration + tests)

---

### G03-T02 — Canonicalization and idempotent re-ingest

**Goal**: Normalize entity aliases and identity; handle conflicts
without destructive overwrite; ensure idempotent re-extraction.

**Deliverables**:
- `src/synapse/application/canonicalization.py` (~120 LOC)
- `tests/unit/application/test_canonicalization.py` (~100 LOC)

**Acceptance criteria**:
1. Re-extraction of identical evidence produces no duplicate entities/claims
2. Same entity found via different aliases (e.g., "Transformer" vs.
   "transformer architecture") canonicalizes to one Entity
3. Ambiguous alias (two different entities with the same alias) is
   NOT silently merged — conflict recorded for review
4. Entity version supersession: when attributes change, old version
   is retained (not deleted), new version created

**Estimated footprint**: ~220 LOC

---

### G03-T03 — RelationshipService

**Goal**: First-class directed, typed, conditional relationships
with `explicit/derived/hypothesized` origin; path lookup; missing
capability queries.

**Deliverables**:
- `src/synapse/application/relationship_service.py` (~220 LOC)
- `alembic/versions/0004_relationships.py` — migration for
  `relationships` table (~80 LOC)
- `tests/unit/application/test_relationship_service.py` (~180 LOC)

**Acceptance criteria**:
1. Multi-hop paths correct (A→B→C returns path `[A, B, C]`)
2. Cyclic graph safe (A→B→A does not infinite-loop; max_depth enforced)
3. Derived relationships remain `verification_state=unverified`
   (never auto-promoted — requirement #6)
4. `find_contradictions()` detects SUPPORTS + CONTRADICTS on same pair
5. Evidence lookup: every verified relationship has traceable `evidence_refs`

**Estimated footprint**: ~480 LOC

---

### G03-T04 — Verification and contradiction engine

**Goal**: Assign epistemic states from evidence rules; track
independent sources; record contradictions; never equate model
confidence with empirical probability.

**Deliverables**:
- `src/synapse/application/verification.py` (~160 LOC)
- `alembic/versions/0005_evidence_deltas.py` — migration for
  `evidence_deltas` table (~60 LOC)
- `tests/unit/application/test_verification.py` (~140 LOC)

**Acceptance criteria**:
1. Contradictions coexist (both SUPPORTS and CONTRADICTS preserved;
   neither deleted; both marked `disputed`)
2. Verification requires source (no `verified` without `evidence_refs`)
3. Every epistemic state change creates an immutable `EvidenceDelta`
4. **Hypotheses are NEVER auto-promoted** — no amount of repetition
   or model confidence moves `hypothesized` → `supported` without
   explicit evidence + review (requirement #6)
5. Source independence: fragments from the same `canonical_uri` count
   as one source; fragments from different URIs count as independent

**Estimated footprint**: ~360 LOC

---

### G03-T05 — Knowledge API and vertical slice demo

**Goal**: Expose entities, claims/evidence, relationships via
versioned API endpoints; prove the vertical slice end-to-end.

**Deliverables**:
- `src/synapse/api/v1/knowledge.py` — replaces 501 placeholders (~180 LOC)
- `src/synapse/api/v1/router.py` — updated to include knowledge router
- `tests/unit/api/test_knowledge.py` (~120 LOC)
- `tests/integration/test_g03_vertical_slice.py` — vertical demo (~150 LOC)

**Acceptance criteria**:
1. 401 without auth on all new endpoints
2. 200 with auth (RESEARCHER scope)
3. `GET /api/v1/entities/{id}` returns entity + claims + relationships + evidence spans
4. `POST /api/v1/knowledge/search` finds entities by name/alias/kind
5. Vertical slice demo: EvidenceFragment → Typed Knowledge Unit →
   Relationship → Verification State → Queryable Result
6. OpenAPI 3.1.0 schema valid with new endpoints

**Estimated footprint**: ~450 LOC

## 10. Code footprint summary

| Category | Files | LOC |
|----------|-------|-----|
| Domain contract (extraction_contract.py) | 1 | ~80 |
| Application (extraction, canonicalization, relationship_service, verification) | 4 | ~680 |
| API (knowledge.py) | 1 | ~180 |
| Migrations (0003, 0004, 0005) | 3 | ~260 |
| Tests (unit + integration) | 6 | ~840 |
| **Total new** | **15** | **~2,040** |
| Modified existing files | 2 | ~20 (router.py + acquisition.py) |
| New pip dependencies | 0 | — |

## 11. Storage contracts — new migrations (all additive)

| Migration | Tables | Indexes | Downgrade |
|-----------|--------|---------|-----------|
| `0003_knowledge_tables` | `entities`, `claims`, `source_spans` | 6 indexes (canonical_name, kind, epistemic_state, subject_ref, evidence_fragment_id, claim_id) | drops all 3 |
| `0004_relationships` | `relationships` | 4 indexes (from+predicate, to+predicate, origin+verification, evidence_refs) | drops table |
| `0005_evidence_deltas` | `evidence_deltas` | 2 indexes (hypothesis_id, updated_state) | drops table |

**All migrations are additive** — CREATE TABLE + CREATE INDEX only.
No ALTER on existing tables. No modification to `0001_initial` or
`0002_evidence_fragments`. Downgrade drops only the new tables.

## 12. API scope — limited to what the vertical slice requires

### New endpoints (replacing 501 placeholders)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/entities` | List entities (paginated, filter by kind) |
| GET | `/api/v1/entities/{entity_id}` | Single entity + claims + relationships + evidence spans |
| GET | `/api/v1/relationships` | List relationships (filter by predicate, origin, verification_state) |
| GET | `/api/v1/relationships/{relationship_id}` | Single relationship |
| GET | `/api/v1/claims/{claim_id}/evidence` | Evidence fragments + source spans for a claim |
| POST | `/api/v1/knowledge/search` | Search entities by name/alias/kind |

### Unchanged endpoints

All G01 + G02 endpoints remain unchanged.

### Remaining 501 placeholders (NOT in G03 scope)

`POST /api/v1/reasoning/queries` (G04), `POST /api/v1/innovations/*`
(G05), `POST /api/v1/experiments` (G05), `POST /api/v1/future/*` (G06).

## 13. Explicit deferred features

| Feature | Deferred to | Reason |
|---------|------------|--------|
| LLM-based semantic extraction | Future group | Extensible contract defined; no LLM framework implemented in G03 |
| Numeric confidence scores | Future group | Categorical epistemic states only; no decorative probabilities |
| Temporal validity (`valid_from`/`valid_to`) | Future group | Not needed for minimal slice |
| Entity version supersession workflow | G03-T02 (minimal) + future | Basic versioning; full review workflow deferred |
| Multi-source verification strengthening | Future group | G03 tracks source independence; multi-source → `strong` verification deferred |
| Graph database migration | Only if justified by measured workload | Recursive CTEs suffice for the minimal slice |
| Background job scheduler | Future group | G03 uses existing JobRow + inline async; no Celery/Redis |
| Knowledge graph visualization | Future group | API returns JSON; UI is a separate concern |

## 14. Unresolved G02 prerequisites (visible, NOT claimed resolved)

| Prerequisite | Status | Impact on G03 |
|--------------|--------|---------------|
| **PostgreSQL migration validation** | ⛔ **BLOCKED** (no PG in env) | G03 migrations (0003–0005) must also be validated on PostgreSQL. User must run all migrations against a real PG instance before any production certification. **G03 is NOT production-certified.** |
| HTTPS hostname verification with IP pinning | Unverified (design-correct) | Does not block G03. G03 extracts from already-fetched evidence, not new network calls. |
| Proxy behavior with IP-pinned transport | Unverified | Does not block G03. |
| Write-capable credential | Unresolved (ADR-0009 §7) | Does not block G03. G03 does not access the Toolkit. |

**G03 is NOT production-certified.** The PostgreSQL blocker from G02
carries forward. All G03 migrations must be validated on PostgreSQL
before claiming production readiness.

## 15. Proposed task sequence

```
G03-T01 (typed extraction + extensible contract + entities/claims/source_spans migration)
    ↓
G03-T02 (canonicalization + idempotent re-extract)
    ↓
G03-T03 (RelationshipService + relationships migration)
    ↓
G03-T04 (verification engine + evidence_deltas migration)
    ↓
G03-T05 (knowledge API + vertical slice demo)
    ↓
GROUP_03_REPORT + EVIDENCE → STOP for approval
```

**Decoupled extraction** (requirement #7): G02's `acquire_and_extract()`
gains a `JobRow(kind="extract_knowledge")` enqueue after commit.
G03-T01's extraction worker polls and processes jobs. If extraction
fails, the acquisition is already committed — the job can be retried.

## STOP — awaiting explicit approval

Per the user's "G03 Plan Refinement" message:
> *"Do not implement G03 yet.*
> *"STOP for approval."*

The agent will NOT:
- Write any code under `src/synapse/` for G03.
- Add any Alembic migration.
- Replace any 501 placeholder.
- Modify G02's `acquire_and_extract()` beyond the documented job-enqueue.
- Begin GROUP_04.

The agent awaits explicit user approval of this revised plan before
beginning G03-T01 implementation.

---

*End of G03 Minimal Implementation Plan (Revised) — STOP for approval.*
