# Toolkit Audit — Preparation (G02 Audit Preparation ONLY)

> **Status**: PREPARATION — awaiting `TOOLKIT_INDEX(1).json` from the user
> per decision **D-01** in ADR-0007.
>
> **Authorization**: This directory contains templates, schemas, and
> scope definitions only. **No tool is marked "verified runnable"**
> until the real inventory is inspected.

## Purpose

Per `START_HERE_GLM.md` §6 and §8 of the Master Architecture v2.0:

> *"Reuse existing toolkit capabilities by adapter; inspect actual
> code/license/tests before integration. The index is discovery evidence,
> not proof of runtime fitness."*

The audit's job is to convert `TOOLKIT_INDEX(1).json` (a discovery
inventory) into a **Layer-1 Capability Matrix** that distinguishes:

1. **Indexed** — appears in the inventory (declared only).
2. **Verified Runnable** — agent has inspected source, license, tests,
   interface; the tool actually imports and runs in this environment.
3. **Adopted** — wired into a Synapse provider adapter.
4. **Deferred** — needs more evidence or a future group.
5. **Rejected** — license conflict, unmaintained, broken, or
   redundant with an already-adopted tool.

## Required inputs (provided by user)

| Input | Status | Notes |
|-------|--------|-------|
| `TOOLKIT_INDEX(1).json` | ⏳ PENDING | Must be pushed to the repo (e.g. under `docs/toolkit_audit/`) or supplied via a secure channel. Without it, the audit cannot start. |
| Optional: toolkit source location (path / git URL) | ⏳ OPTIONAL | If the toolkit itself (not just the index) is available locally, the agent can verify imports directly. Otherwise the agent will rely on `pip show` + import smoke tests for packages installed in the environment. |

## Required outputs (produced by agent after the file is received)

| Output | Path | Format |
|--------|------|--------|
| Layer-1 Capability Matrix | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json` | JSON (schema below) |
| Layer-1 Capability Matrix (human) | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md` | Markdown table |
| Audit report | `reports/GROUP_02_AUDIT_REPORT.md` | Markdown |
| Audit evidence | `reports/GROUP_02_AUDIT_EVIDENCE.json` | JSON |
| Proposed provider selection | `docs/toolkit_audit/PROVIDER_SELECTION.md` | Markdown |

## Capability categories to cover (per Master Spec §7, §8)

The audit MUST classify each indexed tool against these capability
domains. Tools that fall outside all domains are recorded as
"out-of-scope" and not pursued.

| Domain | Required behavior |
|--------|-------------------|
| **Discovery** | Search engines, RSS, arXiv, OpenAlex, Crossref, Semantic Scholar, GitHub repos/releases |
| **Acquisition** | HTTP fetch, raw download, content-type sniffing, partial-fetch handling |
| **Crawling** | Multi-page traversal, robots.txt respect, rate limiting, depth budgets |
| **Browser automation** | Headless browser for JS-rendered pages, login flows, interactive elements |
| **Extraction** | HTML → structured text (headings, paragraphs, code, tables, citations, URLs) |
| **Scientific research** | arXiv full text, Crossref metadata, Semantic Scholar citations, OpenAlex graph |
| **Social ingestion** | RSS/Atom feeds, X/Twitter (compliant APIs only — no scraping if ToS forbids) |
| **Provenance** | Source URI preservation, content hashing, locator extraction (page/line/span), version tracking |

## Audit acceptance criteria

The audit is PASS only if ALL of the following are true:

1. ✅ Every tool in `TOOLKIT_INDEX(1).json` is classified into exactly
   one of: indexed-only / verified-runnable / adopted / deferred / rejected.
2. ✅ Every "verified-runnable" tool has documented evidence: import
   command, exit code, license text, test count (if any), known
   failure modes.
3. ✅ Every capability domain above has at least one tool classified,
   OR the gap is explicitly logged with a recommended remediation
   (build new, find alternative, or defer the capability).
4. ✅ Each adopted tool is mapped to a Synapse `Provider` adapter
   stub (real adapter code is G02 implementation, not audit).
5. ✅ No tool is marked "verified-runnable" without an actual import
   test in the agent's environment.
6. ✅ License conflicts are flagged (e.g. GPL-3.0 tools cannot be
   linked into a proprietary codebase without legal review).
7. ✅ The Layer-1 Capability Matrix is published in both JSON (machine)
   and Markdown (human) formats.
8. ✅ The audit report and evidence JSON follow the G01 template
   structure (status, tasks, commands, test counts, decisions_required).
9. ✅ The agent explicitly STOPs after submitting the audit; the user
   must approve before G02 implementation begins.

## STOP policy

Per `START_HERE_GLM.md` §1 and the user's G02 audit-prep authorization:

> *"Do not begin G02 implementation until the audit and provider
> selection are approved."*

The agent will, after submitting the audit:

- NOT write any provider adapter implementation code.
- NOT modify the `/api/v1/*` routes beyond what G01 already shipped.
- NOT register any tool with the runtime `Provider` registry.
- Wait for explicit user approval.

## Files in this directory (preparation)

| File | Purpose | Status |
|------|---------|--------|
| `README.md` (this file) | Audit scope & acceptance gates | ✅ Prepared |
| `TOOLKIT_INDEX_SCHEMA.json` | JSON schema for validating the inventory | ✅ Prepared |
| `LAYER_1_CAPABILITY_MATRIX.schema.json` | JSON schema for the output matrix | ✅ Prepared |
| `LAYER_1_CAPABILITY_MATRIX.template.md` | Empty template for the human-readable matrix | ✅ Prepared |
| `AUDIT_REPORT.template.md` | Empty template for the audit report | ✅ Prepared |
| `PROVIDER_SELECTION.template.md` | Empty template for provider selection proposal | ✅ Prepared |
| `TOOLKIT_INDEX(1).json` | The actual inventory | ⏳ Pending from user |
| `LAYER_1_CAPABILITY_MATRIX.json` | The produced matrix | ⏳ Pending audit |
| `LAYER_1_CAPABILITY_MATRIX.md` | The produced matrix (human) | ⏳ Pending audit |

## Reproducible audit commands (will be run after the file arrives)

```bash
# 1. Validate the inventory against the schema
python scripts/validate_toolkit_index.py docs/toolkit_audit/TOOLKIT_INDEX\(1\).json

# 2. For each indexed tool, attempt import + version check
python scripts/verify_tool_imports.py docs/toolkit_audit/TOOLKIT_INDEX\(1\).json

# 3. Generate the matrix from verified evidence
python scripts/generate_capability_matrix.py \
    docs/toolkit_audit/TOOLKIT_INDEX\(1\).json \
    docs/toolkit_audit/verification_results.json \
    --out-json docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json \
    --out-md   docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md

# 4. Lint, test, commit
ruff check scripts tests
pytest -q
git commit -m "G02 audit: capability matrix + provider selection"
```
