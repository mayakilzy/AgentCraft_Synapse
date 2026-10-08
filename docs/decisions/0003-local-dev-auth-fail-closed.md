# ADR-0003 — Local dev auth adapter, fail-closed production

- **Status**: Accepted (reversible, but production behavior is binding)
- **Date**: 2026-10-08
- **Group**: G01-T04

## Context

`DECISIONS_AND_ASSUMPTIONS.md` lists, under "Decisions requiring evidence
or approval": *External auth identity provider, exact authorization policy
for multi-tenancy …* and instructs: *"If missing, implement local development
auth adapter and fail-closed production settings, never anonymous public
production access."*

The user has not yet chosen a production identity provider (OIDC / OAuth2 /
internal SSO). Production access must therefore be denied by default.

## Decision

A `synapse.security.auth.AuthAdapter` protocol with two implementations:

1. **`DevelopmentAuthAdapter`** — reads `SYNAPSE_DEV_API_KEYS` and
   `SYNAPSE_ADMIN_API_KEYS` from env. Used only when
   `SYNAPSE_AUTH_MODE == "development"`. Returns `Principal(kind="dev",
   scopes=[...])` for known keys, `401` for unknown.
2. **`ProductionAuthAdapter`** — placeholder that **always rejects** unless
   `SYNAPSE_AUTH_PROVIDER` is configured. Selected when
   `SYNAPSE_ENV == "production"`. If no provider is configured, every
   protected endpoint returns `401 unauthenticated` (fail-closed).

Scopes follow Master Spec §15: `reader`, `researcher`, `editor`,
`operator`, `admin`. The dependency `get_principal(required_scopes=...)`
enforces scope checks at route level.

## Consequences

- ✅ G01 ships a usable auth story without forcing a production IdP choice
- ✅ Production cannot accidentally start in anonymous mode
- ⚠️ Real OIDC integration is a future group's task; documented as a
  `decisions_required` entry in the G01 report
- ⚠️ Multi-tenant isolation is **out of scope for G01** — the
  `Principal` carries an optional `tenant_id`, but no tenant scoping is
  enforced on queries yet.
