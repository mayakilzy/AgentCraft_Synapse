# G03_T03_IMPLEMENTATION_REPORT

## Header

| Field | Value |
|-------|-------|
| Task | G03-T03 — RelationshipService |
| Implementation date | 2026-10-08 |
| Base commit | `24d59ae` (G03-T02 reliability closure) |
| Final SHA | _set after commit_ |
| Authorization | User's "G03-T03 Authorization" message |

## STATUS: **PASS**

## Files changed

### Files added (3)

| Path | Purpose | LOC |
|------|---------|-----|
| `alembic/versions/0004_relationships.py` | Migration: `relationships` table | 55 |
| `src/synapse/application/relationship_service.py` | RelationshipService: create, query, traverse, contradictions | 380 |
| `tests/integration/test_g03_t03_relationship_service.py` | 11 tests covering all 10 acceptance criteria | 340 |

### Files modified (1)

| Path | Change |
|------|--------|
| `src/synapse/storage/models.py` | Added `RelationshipRow` ORM model (35 LOC) |

### NOT modified

- `src/synapse/domain/relationship.py` — G01 Relationship domain contract preserved unchanged
- `src/synapse/domain/_base.py` — G01 enums (Origin, VerificationState, etc.) preserved
- AgentCraft-Toolkit — not accessed

## Actual LOC

| Category | LOC |
|----------|-----|
| Migration (`0004_relationships.py`) | 55 |
| ORM model (`RelationshipRow` in `models.py`) | 35 |
| RelationshipService (`relationship_service.py`) | 380 |
| Tests (`test_g03_t03_relationship_service.py`) | 340 |
| **Total new** | **~810** |

## Schema decisions

### Migration 0004_relationships — additive

- Creates `relationships` table only (no ALTER on existing tables)
- 4 indexes: `(from_entity_id, predicate)`, `(to_entity_id, predicate)`, `(origin, verification_state)`, `(from_entity_id, to_entity_id)`
- Downgrade drops the table cleanly
- Verified on SQLite: upgrade creates, downgrade drops

### G01 Relationship domain contract — reused unchanged

The G01 `Relationship` Pydantic model enforces all invariants:
- Predicate must be in the 16-predicate vocabulary
- No self-loops (from == to is rejected)
- Derived/hypothesized → never auto-promoted to verified
- Explicit + verified → requires evidence_refs
- Derived → requires derivation_chain

The RelationshipService uses the G01 contract's semantics (origin, verification_state, predicate vocabulary) via the ORM layer. No domain model duplication.

## Acceptance test evidence

| # | Test | Result |
|---|------|--------|
| 1 | Typed relationship creation and retrieval | ✅ PASS |
| 2 | Directionality and predicate correctness | ✅ PASS |
| 3 | Provenance and source-span preservation | ✅ PASS |
| 4 | Multiple supporting evidence references (merge) | ✅ PASS |
| 5 | Contradictory relationships coexist (SUPPORTS + CONTRADICTS) | ✅ PASS |
| 6 | Idempotent repeated creation (merge, no duplicate) | ✅ PASS |
| 7 | Bounded multi-hop traversal (A→B→C, max_depth=5) + cyclic safety | ✅ PASS |
| 8 | Missing-capability and dependency queries | ✅ PASS |
| 9 | Atomicity and retry after failure | ✅ PASS |
| 10 | G01/G02/G03 regression | ✅ PASS |

### Quality gates

| Gate | Result |
|------|--------|
| `ruff check src tests scripts` | ✅ All checks passed |
| `ruff format --check src tests scripts` | ✅ 101 files formatted |
| `pytest` (deterministic) | ✅ 311/311 pass, 3 live skipped |
| Migration upgrade (SQLite) | ✅ Creates relationships table |
| Migration downgrade (SQLite) | ✅ Drops table cleanly |

## Real relationship query examples

### Example 1: "What capabilities does this tool enable?"

```python
caps = await find_capabilities(session, "ent-tool")
# Returns entities connected via PROVIDES, ENABLES, PRODUCES
# [{"entity": {"canonical_name": "WebFetch", ...}, "relationship": {"predicate": "PROVIDES", ...}}]
```

### Example 2: "What dependencies does this tool require?"

```python
deps = await find_dependencies(session, "ent-tool")
# Returns entities connected via REQUIRES, DEPENDS_ON
# [{"entity": {"canonical_name": "Python3.12", ...}}]
```

### Example 3: "Which capabilities are missing?"

```python
missing = await find_missing_capabilities(session, "ent-tool")
# Returns capability entities NOT connected to the tool
# [{"entity": {"canonical_name": "UnusedCap", ...}, "reason": "not_connected"}]
```

### Example 4: "Find multi-hop path A → B → C"

```python
paths = await find_paths(session, "ent-a", "ent-c", max_depth=5)
# Returns paths as lists of relationship dicts
# [[{"predicate": "ENABLES", ...}, {"predicate": "PRODUCES", ...}]]
```

### Example 5: "Find contradictions"

```python
contradictions = await find_contradictions(session)
# Returns pairs where SUPPORTS + CONTRADICTS both exist
# [{"entity_a": "...", "entity_b": "...", "both_preserved": True}]
```

## Reliability requirements carried forward

| Requirement | Status |
|-------------|--------|
| Extraction completion is fingerprint-based; may need extractor-version awareness | Documented limitation (L-01 from G03-T02 reliability closure) |
| Relationship persistence is atomic and retry-safe | ✅ `create_relationship` uses `session.flush()` (not commit); caller controls transaction boundary. Failed creation does not corrupt existing data (Test 9) |
| Application-level duplicate-job checks may not be sufficient under concurrency | Documented: `queue_extraction_job` checks for active jobs via SELECT, which is not concurrency-safe under high contention. Acceptable for the minimal slice; a future group can add a UNIQUE constraint or advisory lock |
| PostgreSQL production validation | ⛔ Unresolved — user must validate all migrations (0001–0004) on PostgreSQL |

## Limitations

| # | Limitation | Severity | Resolution |
|---|-----------|----------|------------|
| L-01 | Multi-hop traversal uses iterative BFS (not recursive CTE) — O(depth × edges) queries | Low | Acceptable for the current scale; recursive CTE can be added for PostgreSQL-only deployments |
| L-02 | `find_contradictions` only checks SUPPORTS vs CONTRADICTS — not other predicate pairs | Low | Can be extended in T04 (verification engine) |
| L-03 | No concurrency control on `create_relationship` — concurrent calls could create duplicates before the SELECT+INSERT completes | Medium | A future group can add a UNIQUE constraint on `(from_entity_id, to_entity_id, predicate, origin)` |
| L-04 | G02 PostgreSQL blocker carries forward | Medium | User must validate on PostgreSQL |

## STOP statement

**G03-T03 is complete. The agent will NOT:**

- Begin G03-T04 (verification engine) without explicit approval.
- Expand the API.
- Modify AgentCraft-Toolkit.

Per the user's authorization:
> *"STOP after G03-T03. Do not begin G03-T04 or later tasks without explicit approval."*

---

*End of G03-T03 Implementation Report — STOP for approval.*
