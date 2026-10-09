# ADR-0011 — G03 Functional Closure

- **Status**: Accepted (functional closure; production readiness NOT approved)
- **Date**: 2026-10-09
- **Group**: G03 closure → G04 preparation
- **Authoritative commit**: `046443a`

## Context

The user reviewed the G03-T05 Final Integration Report and accepted G03 as
**functionally complete with limitations**. Production readiness is explicitly
NOT approved. This ADR records the closure and the production-readiness
blocker register that carries forward to G04 and beyond.

## G03 functional closure

G03 is functionally accepted at commit `046443a`. All five tasks (T01–T05)
are complete and tested:

| Task | Title | Status |
|------|-------|--------|
| G03-T01 | Typed knowledge extraction (deterministic + extensible contract) | ✅ PASS |
| G03-T02 | Canonicalization + idempotent persistence (reliability-closed) | ✅ PASS |
| G03-T03 | RelationshipService (typed, directed, bounded traversal) | ✅ PASS |
| G03-T04 | Verification engine (deterministic-v2, conservative) | ✅ PASS (corrected) |
| G03-T05 | Final integration (knowledge API + end-to-end demo) | ✅ PASS WITH LIMITATIONS |

**G03 scope is frozen.** No G03 functionality may be expanded without
explicit user approval.

## Production-readiness blocker register

These blockers carry forward from G01/G02/G03. They do NOT block G04
preparation or implementation, but MUST be resolved before any production
deployment.

| # | Blocker | Origin | Severity | Resolution path |
|---|---------|--------|----------|-----------------|
| PRB-01 | Real PostgreSQL migration/integration validation | G02 | High | User must run all migrations (0001–0004) against a real disposable PostgreSQL instance. Verify schema creation, constraints, indexes, round-trip. |
| PRB-02 | G02 pinned-IP HTTPS, proxy, IPv6, connection-pooling security review | G02 closure | Medium | Live HTTPS test against a real endpoint with valid certificate. Test proxy behavior. Test IPv6 connectivity. Verify connection-pool keying. |
| PRB-03 | Database-level concurrency/idempotency guarantees | G03-T02 | Medium | Add UNIQUE constraints on `(from_entity_id, to_entity_id, predicate, origin)` for relationships; on `(canonical_name, kind)` for entities; or use advisory locks. Application-level checks are not sufficient under concurrency. |
| PRB-04 | Evidence-origin independence limitations | G03-T04 | Medium | `_count_independent_primary_origins` always returns 1 (conservative). Future group can check SourceRow.publisher/author for distinct values, domain overlap, or explicit provenance metadata from the acquisition pipeline. |
| PRB-05 | Stronger VERIFIED review policy | G03-T04 | Expected | VERIFIED is unreachable in deterministic-v2 by design. A future policy version (e.g., deterministic-v3 with human-review integration or experimental-validation-v1 with experiment results) can make it reachable. |
| PRB-06 | Toolkit read-only credential enforcement | G02 closure (ADR-0009 §7) | Medium | The Synapse developer token is write-capable (`repo` + `workflow` OAuth scopes). Future Toolkit audits MUST use a repository-scoped fine-grained PAT with `Contents: Read` only, OR an independently enforced read-only boundary. |
| PRB-07 | Extractor-version-aware reprocessing | G03-T02 reliability closure | Low | The current idempotency check uses `content_fingerprint` + JobRow status. If the extractor version changes (e.g., regex-v1 → regex-v2), the old extraction is not automatically re-run. A future group can add `extractor_version` to the completion check. |

## Preservation requirements

- All G03 implementation, tests, reports, contracts, and provenance are
  preserved unchanged.
- No G03 file under `src/synapse/` may be modified for G04 preparation.
- All ADRs (0001–0011), all reports (GROUP_01 through GROUP_03), and all
  audit evidence are historical records — never retroactively modified.

## Consequences

- ✅ G04 can proceed with preparation and implementation.
- ✅ G03's knowledge graph, verification engine, and API are stable inputs.
- ⚠️ No production deployment until PRB-01 is resolved.
- ⚠️ No production deployment until PRB-03 is resolved.
- ⚠️ VERIFIED remains unreachable until PRB-05 is addressed.
