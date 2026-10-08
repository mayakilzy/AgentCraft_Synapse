# ADR-0006 — API-first contract, /api/v1, problem+json errors

- **Status**: Accepted (binding per `DOMAIN_AND_API_CONTRACTS.md` and
  Master Spec §15)
- **Date**: 2026-10-08
- **Group**: G01-T04

## Context

The Master Spec mandates that the API layer is independent of any UI and
that internal and external clients share the same public service
contracts. `DOMAIN_AND_API_CONTRACTS.md` specifies the base path
(`/api/v1`), the response envelope (`data` / `meta` / `error`), the
async job pattern (`202 Accepted` with `job_id` + `status_url`), CORS
allowlist semantics, and the standard error codes.

## Decision

### Base path & versioning

- All routes mount under `/api/v1`.
- OpenAPI 3.1 schema is published at `/api/v1/openapi.json` and
  `/api/v1/docs` (Swagger UI) — visible in non-production; in production
  the docs endpoint returns `404` (controlled exposure).
- Backwards-compatible additive changes only within v1.

### Response envelope

```json
{
  "data": { /* payload or null */ },
  "meta": {
    "request_id": "uuid",
    "api_version": "v1",
    "pagination": { "next_cursor": "...", "prev_cursor": "...", "limit": 50 }
  },
  "error": null
}
```

On error: `data` is `null`, `error` is the problem object.

### Error format — `application/problem+json` (RFC 9457)

```json
{
  "data": null,
  "meta": { "request_id": "...", "api_version": "v1" },
  "error": {
    "code": "validation_error",
    "message": "…",
    "details": [ /* field-level errors */ ],
    "retryable": false,
    "retry_after": null,
    "trace_id": "..."
  }
}
```

HTTP codes: 400 / 401 / 403 / 404 / 409 / 422 / 429 / 500 / 503, exactly
as the contracts file specifies.

### Async job pattern

Mutating endpoints (POST) that perform long work return `202 Accepted`
with `{"job_id": "...", "status_url": "/api/v1/jobs/{job_id}"}`. Polling
is always supported. SSE / webhooks are deferred to a later group.

### Idempotency

`Idempotency-Key` header is read on mutating endpoints and stored in a
dedicated table (`idempotency_keys`) so that a retried request returns the
original response. (Table schema is created in G01; full enforcement is
deferred to G02 when the first real mutation endpoint goes live.)

### CORS

Origins come from `SYNAPSE_CORS_ORIGINS` (comma-separated). If
`SYNAPSE_CORS_ALLOW_CREDENTIALS == true` and `*` appears in the origins,
the app **refuses to start** (fail-fast at boot).

### Security

- `synapse.security.auth.get_principal(required_scopes=[...])` dependency.
- Per-route RBAC scope check.
- Request-id middleware adds `X-Request-ID` to every response.
- Error middleware translates `DomainError`, `ValidationError`,
  `PermissionError`, etc. to the problem envelope.

## Consequences

- ✅ UI and external clients have a single, documented contract
- ✅ Errors are machine-readable (`error.code` is stable, `retryable`
  is explicit)
- ✅ Schema tests in `tests/unit/api/test_openapi.py` assert the OpenAPI
  version is `3.x`, all routes start with `/api/v1`, and the standard
  health endpoints exist
- ⚠️ A future group may add GraphQL/BFF, but only for demonstrated client
  need (binding per Master Spec §15)
