# Toolkit Audit — Preparation (G02 Audit Preparation ONLY)

> **Status**: PREPARATION — awaiting `TOOLKIT_INDEX(1).json` from the user
> per decision **D-01** in ADR-0007.
>
> **Policy amendment (ADR-0008)**: import success alone must NEVER imply
> `verified_runnable`. The matrix uses a tiered verification model with
> four cumulative stages. Licenses are flagged for review, NOT auto-rejected.

## Purpose

Per `START_HERE_GLM.md` §6 and §8 of the Master Architecture v2.0:

> *"Reuse existing toolkit capabilities by adapter; inspect actual
> code/license/tests before integration. The index is discovery evidence,
> not proof of runtime fitness."*

The audit converts `TOOLKIT_INDEX(1).json` (a discovery inventory) into
a **Layer-1 Capability Matrix** that records four verification stages
per tool, plus a derived disposition, plus license review status, plus
provenance preservation.

## Required inputs (provided by user)

| Input | Status | Notes |
|-------|--------|-------|
| `TOOLKIT_INDEX(1).json` | ⏳ PENDING | Must be pushed to the repo (e.g. under `docs/toolkit_audit/`) or supplied via a secure channel. Without it, the audit cannot start. |
| Optional: toolkit source location (path / git URL) | ⏳ OPTIONAL | If the toolkit itself (not just the index) is available locally, the agent can verify imports directly. Otherwise the agent will rely on `pip show` + import smoke tests for packages installed in the environment. |

## Required outputs (produced by agent after the file is received)

| Output | Path | Format |
|--------|------|--------|
| Layer-1 Capability Matrix | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json` | JSON (schema v2.0) |
| Layer-1 Capability Matrix (human) | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md` | Markdown table |
| Audit report | `reports/GROUP_02_AUDIT_REPORT.md` | Markdown |
| Audit evidence | `reports/GROUP_02_AUDIT_EVIDENCE.json` | JSON |
| Proposed provider selection | `docs/toolkit_audit/PROVIDER_SELECTION.md` | Markdown |

## Verification stages (per ADR-0008 §1)

Each indexed tool is verified through up to four cumulative stages. **A
higher stage requires the previous stage to have passed.**

| Stage | Field | What it tests | How it's tested |
|-------|-------|---------------|-----------------|
| 1 | `package_available` | Can we locate the package? | `importlib.metadata.distribution` (pip) OR `importlib.util.find_spec` (any importable module) |
| 2 | `import_succeeded` | Does `import <tool>` exit 0? | `python -c "import <tool>"` |
| 3 | `functional_smoke_passed` | Does a real functional test pass? | Tool-specific smoke-test command in `scripts/functional_smoke_tests.py` |
| 4 | `production_ready` | Does it meet production criteria? | Manual review: license clean, has tests, has fallback, has cost cap, has documented failure modes |

**Import success alone is NOT enough.** A tool that imports but has no
functional smoke test gets disposition `import_only` — never
`functional_smoke_passed` or `production_ready`.

## Disposition vocabulary

| Disposition | Required evidence |
|-------------|-------------------|
| `indexed_only` | In inventory only; no verification attempted |
| `package_available_only` | `package_available=true`, `import_succeeded=false` |
| `import_only` | `import_succeeded=true`, `functional_smoke_passed` is `null` or `false` |
| `functional_smoke_passed` | `functional_smoke_passed=true`, `production_ready` is `null` |
| `production_ready` | `production_ready=true` (manual review complete) |
| `flagged_for_legal_review` | License in review-required list AND no other blocker; can become `adopted` after user legal approval |
| `deferred` | Blocked on a user decision (paid API key, ToS review, environment setup) |
| `rejected` | Broken, unmaintained, security issue, or duplicate of an adopted tool |
| `adopted` | Selected for Synapse provider adapter (requires user approval via `PROVIDER_SELECTION.md`) |

The legacy `verified_runnable` classification is **removed** per
ADR-0008 §1.

## License handling (per ADR-0008 §3)

Licenses are **flagged for review**, NOT auto-rejected. The matrix
records:

- `license_review_required: boolean` — true if the license is in the
  review-required list (GPL/AGPL/LGPL/SSPL/CC-BY-NC/CC-BY-SA/CC-BY-ND/
  unspecified/unrecognized).
- `license_review_reason: string | null` — short explanation.
- `license_integration_implications: string | null` — what integrating
  the tool would mean (linking model, attribution, source-disclosure).
- `license_distribution_implications: string | null` — what distributing
  the tool to end-users would mean (shipping vs SaaS-only, attribution).

A tool with `license_review_required=true` is set to disposition
`flagged_for_legal_review` — **NOT `rejected`**. The user must explicitly
approve legal review before adoption.

## Provenance preservation (per ADR-0008 §4)

Each matrix row carries a `provenance_preservation` sub-object with five
booleans (or `null` if not assessed):

- `source_structure_preserved` — headings, paragraphs, code, tables, lists
- `metadata_preserved` — source URI, timestamp, author, publisher
- `links_preserved` — hyperlinks (anchor text + href)
- `citations_preserved` — DOI, arXiv ID, BibTeX references
- `version_preserved` — source version (commit SHA, page revision)

Tools that score higher are **preferred** when multiple candidates exist
for the same capability domain. The preference is recorded in
`PROVIDER_SELECTION.md` with rationale.

## Capability categories to cover (per Master Spec §7, §8)

| Domain | Required behavior |
|--------|-------------------|
| **Discovery** | Search engines, RSS, arXiv, OpenAlex, Crossref, Semantic Scholar, GitHub repos/releases |
| **Acquisition** | HTTP fetch, raw download, content-type sniffing, partial-fetch handling |
| **Crawling** | Multi-page traversal, robots.txt respect, rate limiting, depth budgets |
| **Browser automation** | Headless browser for JS-rendered pages, login flows, interactive elements |
| **Extraction** | HTML → structured text (headings, paragraphs, code, tables, citations, URLs) |
| **Scientific research** | arXiv full text, Crossref metadata, Semantic Scholar citations, OpenAlex graph |
| **Social ingestion** | RSS/Atom feeds, X/Twitter (compliant APIs only — no scraping if ToS forbids) |
| **Provenance** | Source URI preservation, content hashing, locator extraction, version tracking |

## Audit acceptance criteria

The audit is PASS only if ALL of the following are true:

1. ✅ Every tool in `TOOLKIT_INDEX(1).json` is classified into exactly
   one of the nine dispositions.
2. ✅ Every tool with `import_succeeded=true` has either a passing
   functional smoke test OR a documented `functional_smoke_skipped_reason`.
3. ✅ Every `production_ready` tool has documented evidence: license
   clean (or legally approved), has tests, has fallback, has cost cap,
   has documented failure modes.
4. ✅ Every capability domain has at least one tool with disposition in
   `{functional_smoke_passed, production_ready, flagged_for_legal_review,
   adopted}`, OR the gap is explicitly logged.
5. ✅ Each `adopted` tool is mapped to a Synapse `Provider` adapter name
   (real adapter code is G02 implementation, not audit).
6. ✅ Every `flagged_for_legal_review` tool has documented
   `license_integration_implications` and `license_distribution_implications`.
7. ✅ Every tool has a `provenance_preservation` sub-object filled in
   (booleans or `null` with reason).
8. ✅ The matrix is published in both JSON (schema v2.0) and Markdown.
9. ✅ The audit report and evidence JSON follow the G01 template structure.
10. ✅ The agent explicitly STOPs after submitting the audit; the user
    must approve before G02 implementation begins.

## STOP policy

Per `START_HERE_GLM.md` §1 and the user's G02 audit-prep authorization
(ADR-0007) and policy amendment (ADR-0008):

> *"Do not begin G02 implementation until the audit and provider
> selection are approved."*

After submitting the audit, the agent will:

- NOT write any provider adapter implementation code.
- NOT modify the `/api/v1/*` routes beyond what G01 already shipped.
- NOT register any tool with the runtime `Provider` registry.
- NOT modify any file under `src/synapse/` (per ADR-0008 §5).
- Wait for explicit user approval.

## Files in this directory (preparation)

| File | Purpose | Status |
|------|---------|--------|
| `README.md` (this file) | Audit scope, policy, acceptance gates | ✅ Prepared |
| `TOOLKIT_INDEX_SCHEMA.json` | JSON schema for validating the inventory | ✅ Prepared |
| `LAYER_1_CAPABILITY_MATRIX.schema.json` | JSON schema for the output matrix (v2.0) | ✅ Prepared |
| `LAYER_1_CAPABILITY_MATRIX.template.md` | Empty template for the human-readable matrix | ✅ Prepared |
| `AUDIT_REPORT.template.md` | Empty template for the audit report | ✅ Prepared |
| `PROVIDER_SELECTION.template.md` | Empty template for provider selection proposal | ✅ Prepared |
| `TOOLKIT_INDEX(1).json` | The actual inventory | ⏳ Pending from user |
| `verification_results.json` | Output of `verify_tool_imports.py` | ⏳ Pending audit |
| `smoke_results.json` | Output of `functional_smoke_tests.py` | ⏳ Pending audit |
| `LAYER_1_CAPABILITY_MATRIX.json` | The produced matrix | ⏳ Pending audit |
| `LAYER_1_CAPABILITY_MATRIX.md` | The produced matrix (human) | ⏳ Pending audit |

## Reproducible audit commands (will be run after the file arrives)

```bash
# 1. Validate the inventory against the schema
make audit-validate

# 2. For each indexed tool, attempt import + version check
make audit-verify-imports

# 3. Run functional smoke tests for shortlisted tools
python scripts/functional_smoke_tests.py docs/toolkit_audit/verification_results.json

# 4. Generate the matrix from verified evidence
make audit-generate-matrix

# 5. Lint, test, commit
ruff check scripts tests
pytest -q
git commit -m "G02 audit: capability matrix + provider selection"
```
