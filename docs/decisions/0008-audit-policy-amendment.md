# ADR-0008 — G02 Audit Policy Amendment

- **Status**: Accepted (binding for the G02 audit)
- **Date**: 2026-10-08
- **Group**: G02 Audit Preparation
- **Supersedes**: Partial — amends the classification vocabulary defined
  in `docs/toolkit_audit/README.md` and
  `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.schema.json` (v1.0).

## Context

The user provisionally accepted the G02 audit preparation (commit
`9542cd1`) but added five clarifications before authorizing the actual
audit execution. These clarifications tighten the verification standard:
**import success alone must never imply `verified_runnable`**, and
licenses must not be auto-rejected by name.

The original matrix schema had a single `verified_runnable` boolean
classification that conflated four distinct verification stages. This
ADR separates them and re-frames the license check as a "flag for legal
review" rather than an automatic rejection.

## Decision

The G02 audit MUST apply the following five clarifications:

### 1. Tiered verification — distinguish four stages

The matrix schema's `classification` field is replaced by a
**disposition** field plus four cumulative boolean verification flags:

| Stage | Field | Meaning |
|-------|-------|---------|
| 1 | `package_available` | The package can be located (via `pip show`, `importlib.metadata.distribution`, or by file-system inspection of the toolkit source). |
| 2 | `import_succeeded` | `import <tool_name>` exited 0 in the audit environment. |
| 3 | `functional_smoke_passed` | A real functional smoke test (not just an import) was run and passed. `null` means "not tested" (with reason). |
| 4 | `production_ready` | Production-readiness checklist met (license clean or approved, has tests, has fallback, has cost cap, has documented failure modes). `null` means "not assessed". |

**The final `disposition` is derived from these flags**, never the other
way around. Possible dispositions:

| Disposition | Required evidence |
|-------------|-------------------|
| `indexed_only` | In inventory only; no verification attempted |
| `package_available_only` | `package_available=true`, `import_succeeded=false` |
| `import_only` | `import_succeeded=true`, `functional_smoke_passed` is `null` or `false` |
| `functional_smoke_passed` | `functional_smoke_passed=true`, `production_ready` is `null` |
| `production_ready` | `production_ready=true` |
| `flagged_for_legal_review` | License is in the review-required list AND no other blocker; can still become `adopted` after user legal approval |
| `deferred` | Blocked on a user decision (paid API key, ToS review, environment setup) |
| `rejected` | Broken, unmaintained, security issue, or duplicate of an adopted tool |
| `adopted` | Selected for Synapse provider adapter (requires user approval via `PROVIDER_SELECTION.md`) |

`verified_runnable` is **removed** from the vocabulary. Anywhere it
appeared in templates or scripts it is replaced by the four flags above.

### 2. Functional smoke tests — perform where safe; record where not

A new script `scripts/functional_smoke_tests.py` runs real functional
smoke tests for shortlisted providers (those with
`import_succeeded=true` and no license blocker). Each smoke test:

- Runs an actual invocation of the tool against a fixture input or a
  public test endpoint.
- Records the command, exit code, stdout/stderr (truncated and
  redacted), and a `tested_at` timestamp.
- Skips with an explicit reason when:
  - Credentials are unavailable (LLM providers, paid search APIs).
  - Network access is restricted (CI sandboxes).
  - The test would have side effects (file system writes outside a
  sandbox, network calls to non-public endpoints).
  - The tool requires a runtime not configured (Playwright without
  browser binaries, etc.).

The reason is recorded as `functional_smoke_skipped_reason` so the
audit trail is complete: every shortlisted tool has either a passing
smoke test or a documented reason for not having one.

### 3. License handling — flag, don't auto-reject

The matrix schema's `license_conflict: boolean` field is replaced by:

- `license_review_required: boolean` — true if the license is in the
  review-required list.
- `license_review_reason: string | null` — short explanation when
  review is required (e.g. "GPL-3.0 — copyleft; linking may require
  releasing Synapse source under a compatible license").
- `license_integration_implications: string | null` — what it would
  mean to integrate (link statically vs dynamically, distribute vs SaaS,
  attribute vs not).
- `license_distribution_implications: string | null` — what it would
  mean to distribute (shipping the tool to users vs running it server-side).

The review-required list (conservative, expands as new licenses are
identified):

- GPL-2.0, GPL-3.0
- AGPL-3.0
- LGPL-2.1, LGPL-3.0
- SSPL-1.0
- CC-BY-NC-*
- CC-BY-SA-*
- CC-BY-ND-*
- "Commercial" / "Proprietary" / "Unlicensed"
- Any license string the agent cannot parse as a known SPDX identifier

A tool with `license_review_required=true` is set to disposition
`flagged_for_legal_review` — NOT `rejected`. The user must explicitly
approve legal review before adoption.

### 4. Provenance preservation — prefer tools that retain source structure

Each matrix row gains a `provenance_preservation` sub-object with five
booleans (or `null` if not assessed):

- `source_structure_preserved` — does the tool preserve headings,
  paragraphs, code blocks, tables, lists?
- `metadata_preserved` — does it preserve source URI, timestamp,
  author, publisher?
- `links_preserved` — does it preserve hyperlinks (anchor text + href)?
- `citations_preserved` — does it preserve citation references (DOI,
  arXiv ID, BibTeX)?
- `version_preserved` — does it preserve source version (commit SHA,
  page revision, document version)?

Tools that score higher on these five booleans are **preferred** when
multiple candidates exist for the same capability domain. The
preference is recorded in `PROVIDER_SELECTION.md` with rationale.

### 5. Read-only audit — no production code changes

The audit MUST NOT modify any file under `src/synapse/`. Specifically:

- No provider adapter implementation code is added.
- No new entries are registered with the runtime `Provider` registry.
- No `/api/v1/*` route is added, removed, or changed.
- No domain record is added or its invariants changed.
- No migration is added.
- `tests/` may be added to (audit-evidence tests) but existing tests
  are not weakened.

The audit may add files only under:

- `docs/toolkit_audit/` (matrix, smoke results, etc.)
- `reports/` (audit report, evidence JSON)
- `scripts/` (audit scripts)
- `tests/integration/` (audit-evidence tests)

After submitting the audit report and provider selection proposal, the
agent MUST STOP and await explicit user approval before writing any
G02 implementation code.

## Consequences

- ✅ Audit trail is complete: every shortlisted tool has either a
  passing functional smoke test or a documented reason for not having
  one.
- ✅ License decisions are explicit, not silent — no GPL component is
  adopted without legal review, but none is rejected without review
  either.
- ✅ Provenance is a first-class criterion — tools that lose source
  structure are deprioritized even if they pass smoke tests.
- ✅ Production code is protected — the audit cannot accidentally
  break G01 by writing provider code that bypasses the SSRF guard or
  the auth adapter.
- ⚠️ The matrix schema is now more complex (more fields per row). The
  trade-off is explicit evidence; the alternative (single boolean)
  was rejected by the user.
- ⚠️ Functional smoke tests may take longer to run than import tests.
  Acceptable — accuracy > speed for this audit.
