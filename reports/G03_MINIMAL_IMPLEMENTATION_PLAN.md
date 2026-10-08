# G03 Minimal Implementation Plan — Knowledge Store, Relationships & Verification

> **Status**: Preparation only — NOT authorized for production implementation.
> Awaiting explicit user approval per the user's "G03 Preparation and
> Architecture Review" authorization.

## 1. Executive summary

G03 transforms the evidence fragments produced by G02's acquisition
pipeline into structured, traceable knowledge: Entities, Claims,
Relationships — with epistemic states, provenance, and verification.
The existing G01 domain contracts (`Entity`, `Claim`, `Relationship`,
`EvidenceFragment`, `EvidenceDelta`) already implement the binding
invariants. G03 adds the storage tables, the `RelationshipService`,
the verification engine, and the API endpoints that wire these
together.

**Core principle preserved**: *"Graph Thinking from Day One — Dedicated
Graph Database Only When Justified."* The existing G01 domain models
treat Relationships as first-class records with typed predicates,
origin tracking (`explicit | derived | hypothesized`), and verification
state — this IS graph thinking. Storage uses relational tables in
PostgreSQL (via SQLAlchemy + Alembic). No graph database, no Neo4j,
no new infrastructure.

## 2. Existing contracts — what G01/G02 already provide

### Domain records (G01, `src/synapse/domain/`)

| Record | Key fields | Invariants enforced |
|--------|-----------|---------------------|
| `Entity` | id, kind, canonical_name, aliases, attributes, canonical_uri | No duplicate aliases; canonical_name not in aliases |
| `Claim` | id, proposition, subject_ref, object_ref, evidence_refs, epistemic_state, confidence_value, confidence_method, validity_conditions | Supported claim must have evidence_refs; hypothesized must not; confidence requires method |
| `Relationship` | id, from_entity_id, to_entity_id, predicate (16 typed predicates), direction, origin, verification_state, evidence_refs, derivation_chain, confidence, conditions, valid_from/to | Derived/hypothesized never auto-promoted to verified; explicit+verified requires evidence; derived requires derivation_chain; no self-loops; predicate must be in vocabulary |
| `EvidenceFragment` | id, acquisition_id, exact_excerpt, excerpt_hash, extraction_method, source_id, source_uri | Must have excerpt or hash; must preserve source linkage |
| `EvidenceDelta` | id, hypothesis_id, prior_state, observation, update_method, updated_state, evidence_refs | Must change state; rejected requires evidence |

### Storage (G01+G02, `src/synapse/storage/models.py`)

Existing tables: `sources`, `jobs`, `idempotency_keys`, `audit_events`,
`capabilities`, `providers`, `evidence_fragments` (added in G02).

**Missing tables** (G03 will add via new migration):
- `entities` — canonical entity registry
- `claims` — propositions with epistemic state
- `relationships` — typed, directed edges between entities
- `evidence_deltas` — immutable belief-change audit log

### API (G01, `src/synapse/api/v1/router.py`)

501 placeholders for:
- `GET /api/v1/entities`, `GET /api/v1/entities/{entity_id}`
- `GET /api/v1/relationships`, `GET /api/v1/relationships/{relationship_id}`
- `GET /api/v1/claims/{claim_id}/evidence`
- `POST /api/v1/knowledge/search`

### G02 integration point

`src/synapse/application/acquisition.py` → `acquire_and_extract()`
currently produces: SourceRow → EvidenceFragmentRow (with extracted
text + metadata + content_fingerprint). G03 extends this pipeline:
after extraction, an entity/claim/relationship extraction step
transforms the evidence into structured knowledge.

## 3. Proposed implementation — 5 tasks

### G03-T01 — Entity/claim extraction contracts

**Goal**: Transform an `EvidenceFragment` (extracted text) into
`Entity` and `Claim` records, preserving quote/locator and uncertainty.

**Approach**: Deterministic rule-based extraction (not LLM) for the
minimal slice. The G02 pipeline already extracts article text via
trafilatura. G03 adds a thin extraction layer that:

1. Identifies entities in the extracted text using simple patterns:
   - arXiv IDs (`\d{4}\.\d{4,5}`)
   - DOIs (`10\.\d{4,}/\S+`)
   - GitHub repos (`github.com/\S+/\S+`)
   - URLs (already preserved by trafilatura)
2. Creates `Entity` records for each identified entity (kind=paper,
   kind=repository, etc.)
3. Creates `Claim` records for each entity-to-evidence link:
   - `proposition`: "Source X mentions entity Y"
   - `epistemic_state`: `supported` (has evidence_refs)
   - `evidence_refs`: [evidence_fragment_id]
   - `subject_ref`: entity_id
   - `extraction_method`: "regex-pattern-v1"

**Deliverables**:
- `src/synapse/application/extraction.py` — entity/claim extraction
  from EvidenceFragment text (~150 LOC)
- `tests/unit/application/test_extraction.py` — deterministic tests
  with fixture text containing known arXiv IDs, DOIs, GitHub repos
- `alembic/versions/0003_entities_claims.py` — migration for
  `entities` + `claims` tables (~100 LOC)

**Acceptance criteria**:
- Every `Claim` with `epistemic_state=supported` has ≥1 evidence_ref
- Unsupported model assertions are labeled `hypothesized` (not
  silently promoted to `supported`)
- Re-extraction of the same evidence fragment is idempotent (same
  entities + claims, no duplicates)

### G03-T02 — Canonicalization and versioning

**Goal**: Normalize entity aliases, versions, and identity; handle
conflicting values without destructive overwrite.

**Approach**:
1. **Canonical entity registry**: the `entities` table has a unique
   constraint on `(canonical_name, kind)`. Aliases stored in a
   separate `entity_aliases` table (or JSON column) with a
   `source_id` reference — never silently merged.
2. **Idempotent re-ingest**: if the same canonical_uri + content_hash
   is re-ingested, the existing entities/claims are returned without
   creating duplicates. (G02 already handles idempotency at the
   Source level; G03 extends it to entities/claims.)
3. **Versioning**: when entity attributes change (e.g., a paper gets a
   new version), a new `Entity` row is created with `version += 1`;
   the old row is marked `superseded` but not deleted. The
   `evidence_deltas` table records the belief change.

**Deliverables**:
- `src/synapse/application/canonicalization.py` — merge/normalize
  logic (~100 LOC)
- `tests/unit/application/test_canonicalization.py` — tests for
  idempotent re-ingest, alias conflicts, version supersession

**Acceptance criteria**:
- Re-ingest of identical data is idempotent (no duplicate entities/claims)
- Changed version retained (new row, old row superseded)
- Ambiguous alias not silently merged (conflict recorded, not
  auto-resolved)

### G03-T03 — RelationshipService

**Goal**: Implement first-class directed, typed, conditional, and
temporal relationships with `explicit/derived/hypothesized` origin;
path lookup and missing-capability queries.

**Approach**: The `Relationship` domain record (G01) already enforces
all invariants. G03 adds:

1. **`RelationshipService`** (`src/synapse/application/relationship_service.py`):
   - `upsert_entity(entity)` — create or update an entity
   - `add_relationship(from_id, to_id, predicate, origin, evidence_refs)`
   - `find_related_entities(entity_id, predicate=None, max_depth=3)`
   - `find_paths(from_id, to_id, max_depth=5)` — multi-hop traversal
   - `find_missing_capabilities(entity_id)` — what capabilities does
     this entity lack?
   - `find_contradictions()` — pairs of relationships with
     `SUPPORTS` + `CONTRADICTS` on the same entity pair
   - `find_unverified_relationships()` — relationships with
     `origin in {derived, hypothesized}` and
     `verification_state = unverified`
   - `explain_path(path)` — human-readable explanation of a path

2. **Storage**: `relationships` table with indexes on
   `(from_entity_id, predicate)`, `(to_entity_id, predicate)`,
   `(origin, verification_state)`.

3. **Graph thinking without a graph database**: the `relationships`
   table IS an edge table. Multi-hop queries use recursive CTEs
   (Common Table Expressions) in PostgreSQL — the standard SQL
   approach for graph traversal. SQLite supports recursive CTEs
   too (since 3.8.3), so tests work on both.

**Deliverables**:
- `src/synapse/application/relationship_service.py` (~200 LOC)
- `alembic/versions/0004_relationships.py` — migration for
  `relationships` table (~80 LOC)
- `tests/unit/application/test_relationship_service.py` — multi-hop
  path, cyclic graph safety, inferred links stay unverified,
  evidence lookup

**Acceptance criteria**:
- Multi-hop paths correct (tested with a 3-node graph: A→B→C)
- Cyclic graph safe (A→B→A does not infinite-loop; max_depth enforced)
- Inferred links remain `unverified` (derived/hypothesized never
  auto-promoted)
- Evidence lookup succeeds (every verified relationship has
  traceable evidence_refs)

### G03-T04 — Verification and contradiction engine

**Goal**: Assign epistemic states from evidence rules; track
independent sources; record contradictions; never equate model
confidence with empirical probability.

**Approach**:
1. **Verification policies** (deterministic rules, not LLM):
   - A `Claim` can move to `epistemic_state=supported` only if it
     has ≥1 `evidence_ref` (already enforced by G01 domain model)
   - A `Relationship` with `origin=explicit` can move to
     `verification_state=verified` only if it has ≥1 `evidence_ref`
     (already enforced)
   - Source independence: two evidence fragments from the same
     `canonical_uri` count as one source; fragments from different
     URIs count as independent
   - Contradiction detection: if two claims about the same entity
     pair have `SUPPORTS` and `CONTRADICTS` predicates, both
     coexist — neither is deleted; both are marked `disputed`
2. **EvidenceDelta** records: every belief change (e.g., a claim
   moving from `hypothesized` to `supported`) creates an immutable
   `EvidenceDelta` row — the prior state, observation, update
   method, and new state are all recorded.
3. **Confidence**: never a decorative probability. If a confidence
   value is set, a `confidence_method` must be specified (already
   enforced by G01). For the minimal slice, confidence values are
   NOT assigned — only categorical epistemic states are used.

**Deliverables**:
- `src/synapse/application/verification.py` — verification engine
  (~150 LOC)
- `alembic/versions/0005_evidence_deltas.py` — migration for
  `evidence_deltas` table (~60 LOC)
- `tests/unit/application/test_verification.py` — contradiction
  coexistence, verification requires source, changes recorded
  and reversible

**Acceptance criteria**:
- Contradictions coexist (both SUPPORTS and CONTRADICTS on the same
  pair are preserved; neither deleted)
- Verification requires source (no `verified` without evidence_ref)
- Changes recorded and reversible (EvidenceDelta audit trail)
- Model confidence never equated with empirical probability (no
  LLM-generated probability scores; only categorical states)

### G03-T05 — Knowledge API and vertical data demo

**Goal**: Expose entities, claims/evidence, relationships, and source
histories via versioned API endpoints with scoped access.

**Approach**: Replace the G01 501 placeholders for:
- `GET /api/v1/entities` — list entities (paginated, filterable by kind)
- `GET /api/v1/entities/{entity_id}` — single entity with aliases + attributes
- `GET /api/v1/relationships` — list relationships (filterable by predicate, origin, verification_state)
- `GET /api/v1/relationships/{relationship_id}` — single relationship
- `GET /api/v1/claims/{claim_id}/evidence` — evidence fragments for a claim
- `POST /api/v1/knowledge/search` — search entities by name/alias/kind

All endpoints require `RESEARCHER` scope (existing G01 auth).

**Deliverables**:
- `src/synapse/api/v1/knowledge.py` — replaces 501 placeholders (~200 LOC)
- `tests/unit/api/test_knowledge.py` — endpoint smoke tests
- `tests/integration/test_g03_vertical_demo.py` — 10-source demo:
  ingest 10 real arXiv papers → extract entities/claims → create
  relationships → verify via API

**Acceptance criteria**:
- API contract + auth tests pass (401 without auth, 200 with)
- 10-source demo: can trace source → fragment → claim → relationship
  for each of the 10 sources
- OpenAPI 3.1.0 schema updated with the new endpoints

## 4. Integration with G02 acquisition pipeline

The existing `acquire_and_extract()` in
`src/synapse/application/acquisition.py` currently produces:

```
SourceRow → EvidenceFragmentRow (extracted text + metadata + fingerprint)
```

G03 extends this to:

```
SourceRow → EvidenceFragmentRow → Entity[] → Claim[] → Relationship[]
                                    ↓            ↓            ↓
                              entities table  claims table  relationships table
                                    ↓            ↓            ↓
                              audit_events (full provenance trail)
```

**Minimal change to existing code**: `acquire_and_extract()` gains a
new call after the evidence fragment is persisted:

```python
# After evidence_row is persisted (existing G02 code):
from synapse.application.extraction import extract_knowledge
entities, claims = await extract_knowledge(session, evidence_row)
# entities and claims are now in the DB; relationships are created
# by the RelationshipService when explicit links are found.
```

No G02 code is modified beyond this one new call. The existing
tests continue to pass because the extraction step is additive — if
no entities/claims are found in the text, the pipeline simply
completes without creating any.

## 5. Storage contracts — new migrations

| Migration | Tables created | Indexes | Downgrade |
|-----------|---------------|---------|-----------|
| `0003_entities_claims` | `entities`, `claims` | `ix_entities_canonical_name`, `ix_entities_kind`, `ix_claims_epistemic_state`, `ix_claims_subject_ref` | drops both tables |
| `0004_relationships` | `relationships` | `ix_relationships_from_predicate`, `ix_relationships_to_predicate`, `ix_relationships_origin_verification`, `ix_relationships_evidence_refs` | drops table |
| `0005_evidence_deltas` | `evidence_deltas` | `ix_evidence_deltas_hypothesis_id`, `ix_evidence_deltas_updated_state` | drops table |

All migrations are additive (CREATE TABLE + CREATE INDEX only).
No ALTER on existing tables. Downgrade drops only the new tables.

## 6. API boundaries — what changes, what doesn't

### New endpoints (replacing 501 placeholders)

| Method | Path | Status before G03 | Status after G03 |
|--------|------|-------------------|------------------|
| GET | `/api/v1/entities` | 501 | 200 (paginated list) |
| GET | `/api/v1/entities/{entity_id}` | 501 | 200 (single entity) |
| GET | `/api/v1/relationships` | 501 | 200 (paginated list) |
| GET | `/api/v1/relationships/{relationship_id}` | 501 | 200 (single relationship) |
| GET | `/api/v1/claims/{claim_id}/evidence` | 501 | 200 (evidence fragments for a claim) |
| POST | `/api/v1/knowledge/search` | 501 | 200 (search results) |

### Unchanged endpoints

All G01 + G02 endpoints remain unchanged:
- `/health/*`, `/api/v1/capabilities`, `/api/v1/providers`
- `/api/v1/system/*`, `/api/v1/jobs/*`
- `/api/v1/sources/*` (G02 — now real, not 501)

### Remaining 501 placeholders (NOT in G03 scope)

- `POST /api/v1/reasoning/queries` (GROUP_04)
- `POST /api/v1/innovations/generate` (GROUP_05)
- `POST /api/v1/experiments` (GROUP_05)
- `POST /api/v1/future/scenarios` (GROUP_06)

## 7. Dependencies and new packages

| Package | Version | License | Why |
|---------|---------|---------|-----|
| (none) | — | — | G03 uses only existing deps: SQLAlchemy, Pydantic, httpx, stdlib `re` for pattern extraction |

**No new pip dependencies.** The deterministic extraction approach
(regex patterns for arXiv IDs, DOIs, GitHub repos) uses only Python
stdlib `re`. If LLM-based extraction is added in a later group, the
LLM provider dependency will be introduced then.

## 8. Confidence and provenance policies

| Policy | Enforcement |
|--------|-------------|
| No decorative confidence | G01 domain model: `confidence_value` requires `confidence_method`. G03 does not assign confidence values in the minimal slice. |
| Categorical epistemic states only | `supported | inferred | hypothesized | disputed | rejected` — no numeric probability scores. |
| Source independence | Two evidence fragments from the same `canonical_uri` count as one source. Fragments from different URIs count as independent. Tracked via `evidence_fragments.source_id`. |
| Provenance preserved | Every Entity, Claim, and Relationship records its `source_id` (or `evidence_refs`). The `toolkit_commit_sha` from G02 is preserved on evidence fragments. |
| Immutable belief changes | Every transition of a Claim's `epistemic_state` creates an `EvidenceDelta` row. The prior state, observation, update method, and new state are all recorded — never overwritten. |

## 9. Unresolved G02 prerequisites

| Prerequisite | Status | Impact on G03 |
|--------------|--------|---------------|
| PostgreSQL migration validation | ⛔ BLOCKED (no PG in env) | G03 migrations must also be validated on PostgreSQL. The user should run all migrations (0001–0005) against a real PG instance before G03 production certification. |
| HTTPS hostname verification with IP pinning | Unverified (design-correct) | Does not block G03 preparation. If G03 needs live HTTPS acquisition (it doesn't in the minimal slice), this must be verified first. |
| Proxy behavior with IP-pinned transport | Unverified | Does not block G03. G03's extraction is from already-fetched evidence fragments, not from new network calls. |
| Write-capable credential | Unresolved (ADR-0009 §7) | Does not block G03. G03 does not access the Toolkit. |

## 10. Proposed task sequence

```
G03-T01 (extraction + entities/claims migration)
    ↓
G03-T02 (canonicalization + idempotent re-ingest)
    ↓
G03-T03 (RelationshipService + relationships migration)
    ↓
G03-T04 (verification engine + evidence_deltas migration)
    ↓
G03-T05 (knowledge API + 10-source demo)
    ↓
GROUP_03_REPORT + EVIDENCE → STOP for approval
```

Each task depends on the previous one:
- T02 needs T01's entities/claims tables
- T03 needs T02's canonicalized entities
- T04 needs T03's relationships
- T05 needs T04's verification states

## 11. Estimated complexity

| Dimension | Estimate |
|-----------|----------|
| New source files | ~8 (extraction, canonicalization, relationship_service, verification, knowledge API, 3+ test files) |
| New LOC | ~1,000 (source) + ~600 (tests) |
| New pip deps | 0 |
| New Alembic migrations | 3 (0003, 0004, 0005) |
| New API endpoints | 6 (replacing 501 placeholders) |
| Implementation time | 2-3 working sessions |
| Risk of breaking G01/G02 | Low — all changes are additive (new tables, new endpoints, one new call in `acquire_and_extract()`) |

## 12. Acceptance tests — summary

| Test group | What it verifies |
|------------|-----------------|
| Entity extraction | arXiv IDs, DOIs, GitHub repos extracted from fixture text → Entity records created |
| Claim evidence linkage | Every supported Claim has ≥1 evidence_ref; unsupported → hypothesized |
| Canonicalization | Idempotent re-ingest; alias conflict not silently merged; version supersession |
| RelationshipService | Multi-hop path (A→B→C); cyclic graph safe; inferred links unverified; evidence lookup |
| Verification | Contradictions coexist; verification requires source; EvidenceDelta audit trail |
| Knowledge API | 401 without auth; 200 with auth; OpenAPI schema valid |
| 10-source demo | Source → fragment → claim → relationship traceable for 10 real arXiv papers |
| G01/G02 regression | All 237 existing tests still pass |

## STOP — awaiting explicit approval

Per the user's "G03 Preparation and Architecture Review" authorization:
> *"Do not begin G03 production implementation yet."*
> *"STOP for approval."*

The agent will NOT:
- Write any code under `src/synapse/` for G03.
- Add any Alembic migration.
- Replace any 501 placeholder for knowledge endpoints.
- Begin GROUP_04.

The agent awaits explicit user approval of this plan before beginning
G03-T01 implementation.

---

*End of G03 Minimal Implementation Plan — STOP for approval.*
