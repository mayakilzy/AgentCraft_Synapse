# ADR-0009 — G02 Audit Review Clarifications

- **Status**: Accepted (binding for the G02 implementation gate)
- **Date**: 2026-10-08
- **Group**: G02 — Audit review → minimal implementation proposal
- **Supersedes**: Partial — corrects ADR-0007 D-02 (auth deferment unchanged)
  and ADR-0008 §3 (license flag-not-reject unchanged); corrects the
  audit-evidence claim that local git push restrictions make the
  credential read-only (they do not).

## Context

The user reviewed the G02 Direct Toolkit Audit (commit `12e99db`,
`GROUP_02_AUDIT_REPORT.md`) and conditionally accepted it as an
**inventory and discovery audit**, not as production provider approval.
Eight clarifications were issued before any GROUP_02 implementation
may begin.

## Decision

The G02 implementation gate MUST apply the following eight
clarifications. None may be silently relaxed.

### 1. Preserve audit evidence and Toolkit commit SHA

The audit artifacts (`TOOLKIT_REPOSITORY_INVENTORY.json`,
`LAYER_1_CAPABILITY_MATRIX.json`, `verification_results.json`,
`smoke_results.json`, `GROUP_02_AUDIT_REPORT.md`,
`GROUP_02_AUDIT_EVIDENCE.json`) are **frozen at Toolkit commit
`fd9df34c51781bd12effab62762022ab04dbd771`**. Future audits reference
this commit; nothing in the Synapse repo may rewrite or backfill the
audit evidence to match a later Toolkit state.

### 2. Separate inventory coverage from verified functional coverage

The audit reported "0 capability gaps" because every shortlisted tool
imported successfully. **Import success is NOT functional readiness**
(per ADR-0008 §1). The corrected gap analysis distinguishes:

- **Inventory coverage** — tool exists in the Toolkit, has source code
  on disk, imports without error. (32/32 tools.)
- **Functional coverage** — a real functional smoke test passed with
  observed output. (2/32 tools: `arxiv_search_tool`, `url_safety_tool`.)

A capability domain counts as "functionally covered" only when at
least one tool in that domain has a **passing functional smoke test**.
By that stricter measure:

| Domain | Functionally covered? |
|--------|-----------------------|
| discovery | ✓ YES (`arxiv_search_tool`) |
| acquisition | ✓ YES (`url_safety_tool`) |
| crawling | ✗ NO (all skipped: runtime or side-effects) |
| browser | ✗ NO (all skipped: runtime binaries) |
| extraction | ✗ NO (all skipped: runtime or no fixture) |
| scientific | ✗ NO (all skipped: runtime or no fixture) |
| social | ✗ NO (all skipped: credentials or license) |
| provenance | ✗ NO (all skipped: no fixture authored) |

So 6 of 8 domains have **inventory coverage but NOT functional
coverage**. The minimal execution proposal must shrink the functional
gap by running real smoke tests on the smallest necessary set before
claiming readiness.

### 3. Propose a minimal first vertical slice

The proposal must select the **smallest necessary provider set** that
delivers a coherent vertical slice — not 15 providers at once. The
user's stated priority: scientific source discovery, extraction,
provenance preservation, and delivery to the Synapse knowledge
pipeline.

### 4. Compare integration approaches and recommend the least complex

Three approaches were on the table:

- **Direct package dependencies** — `pip install <lib>` and write Synapse
  adapters from scratch.
- **Thin adapters** — copy the ToolKit's `tool.py` adapter into Synapse
  `src/synapse/providers/`, preserve provenance header.
- **Selective vendoring** — copy only the parts needed (a function, a
  class) from a ToolKit adapter.

The proposal must compare these on maintainability, dependency
surface, license cleanliness, and upgrade path, then recommend ONE
approach with rationale.

### 5. Do not install all 15 shortlisted providers

The minimal execution proposal installs only the providers required
for the first vertical slice. The other 12+ shortlisted providers
remain **audited but not adopted** — they may be added in later groups
after explicit user approval per provider.

### 6. Defer instaloader, social credentials, and browser runtimes

The proposal MUST explicitly defer:

- `instaloader_tool` (GPL-3.0) — pending user legal review (D-04).
- All social-credential providers (`twikit`, `scholarly`,
  `cloudscraper_http`) — pending user credentials provisioning (D-05).
- All browser-runtime providers (`playwright_browser`, `crawl4ai`,
  `cdp_browser`, `selenium`, `browser_use_agent`) — pending user
  approval for browser binary installation (D-06).

These are NOT in the minimal first vertical slice.

### 7. Correct the credential statement

The Synapse developer token has `repo` + `workflow` GitHub OAuth scopes
— **it is write-capable at the credential level**. The audit's local
git push block (`remote.origin.pushurl = DISABLED-PUSH-BY-AUDIT-POLICY`)
is a **process-level guard on the local clone**, not a credential-level
read-only enforcement. The credential can still be used to push to any
repository the user owns via direct GitHub API calls.

**Correction**: The audit reports (`GROUP_02_AUDIT_REPORT.md` and
`GROUP_02_AUDIT_EVIDENCE.json`) are amended to remove any claim that
the credential is "read-only" or that the local push block makes it
so. The honest statement is:

> *"The credential is write-capable (repo + workflow scopes). The
> local clone's push URL is set to a non-existent value to prevent
> accidental pushes from this clone, but this does not change the
> credential's capabilities. Future audits MUST use repository-scoped
> read-only credentials (fine-grained PAT with `Contents: Read` only
> on the specific repo) or an independently enforced read-only access
> boundary (e.g., a mirror clone that is never given the write URL)."*

A new `CORRECTED_CREDENTIAL_STATEMENT.md` documents this correction.

### 8. Do not modify, repair, or push to AgentCraft-Toolkit

The Toolkit and all its subordinate source repositories remain
**strictly read-only**. Any defect, missing feature, or required
upstream repair discovered during the Synapse integration must be:

1. **Documented as a request** in `docs/toolkit_audit/UPSTREAM_REPAIR_REQUESTS.md`.
2. **Submitted to the authorized Toolkit agent** under Master Agent
   governance — NOT directly to the Toolkit repo by the Synapse
   implementation agent.
3. **Worked around in Synapse** if blocking — by writing a Synapse-side
   adapter that compensates for the upstream gap, with a comment
   pointing to the upstream request.

The Synapse agent never pushes commits, branches, tags, releases,
issues, pull requests, or repository settings to any Toolkit source
repository.

## Consequences

- ✅ Audit evidence is reproducible: Toolkit commit `fd9df34` is the
  frozen reference for everything in `docs/toolkit_audit/`.
- ✅ Functional readiness claims are evidence-backed: a tool is
  "functionally ready" only when a real smoke test passed.
- ✅ The minimal first slice is small, focused, and license-clean.
- ✅ The credential correction is honest — no false claim of read-only
  enforcement.
- ✅ Upstream repairs are routed through proper governance, not bypassed.
- ⚠️ The minimal first slice has a narrower scope than the original
  15-provider proposal — fewer capabilities delivered per group, but
  each one is verified.
- ⚠️ Until a repository-scoped read-only credential is provisioned,
  future Toolkit audits carry residual write-risk at the credential
  level (mitigated only by process discipline).
