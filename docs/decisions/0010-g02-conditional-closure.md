# ADR-0010 — G02 Conditional Closure Decisions

- **Status**: Accepted (binding for G03 preparation)
- **Date**: 2026-10-08
- **Group**: G02 closure → G03 preparation

## Context

The user reviewed the G02 Security & DB Closure report and accepted
G02 as a completed functional vertical slice, with production readiness
explicitly pending. Five decisions were issued to govern the transition
to G03 preparation.

## Decisions

### 1. Freeze the current G02 provider scope

The three providers shipped in the G02 minimal slice
(`arxiv_search`, `trafilatura_extractor`, `content_delta_hash`) are
frozen. No additional providers may be added to the G02 scope. The
12+ deferred providers from the audit remain deferred and may be
reconsidered in later groups after explicit per-provider approval.

### 2. PostgreSQL migration validation is an unresolved production-readiness blocker

The migrations (`0001_initial` + `0002_evidence_fragments`) have been
validated on SQLite and their SQL has been syntax-validated via
`pglast`. However, they have NOT been run against a real PostgreSQL
instance. This is recorded as a **production-readiness blocker**: G02
is not production-certified until the user (or a CI pipeline with
PostgreSQL) verifies schema creation, constraints, indexes, and
round-trip behavior on a real PostgreSQL database.

### 3. Security verification items for the IP-pinned transport

The following aspects of the `SSRFGuardedAsyncTransport` are recorded
as security verification items that must be addressed before
production certification:

| Item | Status | Notes |
|------|--------|-------|
| HTTPS hostname verification | Verified by design | `extensions["sni_hostname"]` ensures httpcore verifies the TLS certificate against the original hostname, not the pinned IP. A dedicated live test against a real HTTPS endpoint with a valid certificate should confirm this. |
| Proxy behavior | Unverified | If the Synapse deployment uses an HTTP proxy (via `HTTP_PROXY` / `HTTPS_PROXY` env vars or httpx's `proxy` parameter), the IP-pinning transport's URL rewriting may interact unexpectedly with proxy routing. This must be tested if proxy support is needed. |
| IPv6 handling | Partially verified | `_format_ip_for_url()` wraps IPv6 addresses in brackets for URL compatibility. Unit tests confirm the formatting. However, live IPv6 connectivity has not been tested. |
| Connection pooling | Verified by design | httpx's connection pool keys by `(scheme, host, port)`. Since the pinned URL has a different host (the IP), the pool creates a new connection per pinned IP. This prevents connection reuse across different IPs for the same hostname — correct behavior for security. |

### 4. G02 is NOT fully production-certified

G02 is accepted as a **completed functional vertical slice**. It is
NOT marked as fully production-certified. The blockers are:

- PostgreSQL migration validation (decision #2)
- Security verification items (decision #3)
- The credential is write-capable (per ADR-0009 §7) — a read-only
  credential must be provisioned before any production deployment
  that touches the Toolkit

### 5. Preserve all existing test evidence and source provenance

All audit evidence (`TOOLKIT_REPOSITORY_INVENTORY.json`,
`LAYER_1_CAPABILITY_MATRIX.json`, `verification_results.json`,
`smoke_results.json`, `minimal_slice_smoke_results.json`), all ADRs
(0001–0010), all reports (`GROUP_01_REPORT.md` through
`GROUP_02_SECURITY_DB_CLOSURE.md`), and all provenance headers in
vendored source files are preserved unchanged. No historical evidence
may be retroactively modified.

### 6. Do not modify AgentCraft-Toolkit

The Toolkit repository at commit `fd9df34` remains strictly
read-only. The local snapshot at
`/home/z/my-project/agentcraft/toolkit_audit_workspace/toolkit` is
the only authorized source. No pushes, no commits, no issue creation,
no PR submission. The write-capable Synapse developer token must NOT
be used to access the Toolkit.

## Consequences

- ✅ G02 scope is frozen — G03 can proceed without provider-scope drift.
- ✅ Production blockers are explicitly tracked — no false claim of
  production readiness.
- ✅ Security items are documented — a future security review has a
  clear checklist.
- ⚠️ G02 is not production-certified — deployment to production
  requires resolving blockers #2 and #3 first.
- ⚠️ The user must run PostgreSQL migration validation before any
  production deployment.
