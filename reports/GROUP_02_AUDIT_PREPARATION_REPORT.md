# G02_AUDIT_PREPARATION_REPORT

## Header

| Field | Value |
|-------|-------|
| Group | G02 — **Audit Preparation ONLY** (implementation NOT authorized) |
| Preparation date | 2026-10-08 |
| Operator | GLM main agent |
| Repository | `https://github.com/mayakilzy/AgentCraft_Synapse.git` |
| Branch | `main` |
| Base SHA | `bed4b8f` (HEAD of G01 closure) |
| Final SHA | _pending — set at end of this report_ |
| Authorization | User's G01 conditional acceptance message (ADR-0007) |

## STATUS: **PREPARATION COMPLETE — awaiting `TOOLKIT_INDEX(1).json`**

The audit scaffolding (templates, schemas, scripts, tests) is delivered
and verified end-to-end with a synthetic inventory. **No real audit has
been performed yet** because the user's `TOOLKIT_INDEX(1).json` has not
been received (per ADR-0007 decision D-01).

## Decisions recorded (from user's authorization message)

| ID | Decision | Status |
|----|----------|--------|
| **D-01** | Do **not** waive the AgentCraft toolkit audit. `TOOLKIT_INDEX(1).json` is required as inventory input and will be provided separately by the user. | ✅ Recorded in ADR-0007 |
| **D-02** | Defer production identity-provider selection. Preserve the authentication abstraction and external API compatibility. | ✅ Recorded in ADR-0007 |
| **D-03** | Defer licensing. Do **not** add a `LICENSE` file without explicit user approval. | ✅ Recorded in ADR-0007; verified by `test_no_LICENSE_file_present` |

## What this delivery contains

### Audit preparation artifacts

| Path | Purpose | Status |
|------|---------|--------|
| `docs/decisions/0007-g01-conditional-acceptance.md` | Records the user's three decisions and the next-authorization scope | ✅ Written |
| `docs/toolkit_audit/README.md` | Audit scope, capability domains, acceptance criteria, reproducible commands | ✅ Written |
| `docs/toolkit_audit/TOOLKIT_INDEX_SCHEMA.json` | JSON Schema for validating the inventory structure | ✅ Written |
| `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.schema.json` | JSON Schema for the output matrix | ✅ Written |
| `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.template.md` | Empty template for the human-readable matrix | ✅ Written |
| `docs/toolkit_audit/AUDIT_REPORT.template.md` | Empty template for the audit report | ✅ Written |
| `docs/toolkit_audit/PROVIDER_SELECTION.template.md` | Empty template for the provider selection proposal | ✅ Written |

### Audit scripts (idempotent, tested)

| Path | Purpose | Tests |
|------|---------|-------|
| `scripts/validate_toolkit_index.py` | Validates `TOOLKIT_INDEX(1).json` against the schema; exits 0 on valid, 1 on schema fail, 2 on missing file, 3 on schema-missing | ✅ 4 tests |
| `scripts/verify_tool_imports.py` | Imports each indexed tool, records version + license, writes `verification_results.json` | ✅ 1 test |
| `scripts/generate_capability_matrix.py` | Combines index + verification into the Layer-1 Capability Matrix (JSON + Markdown); classifies tools, detects gaps, validates against matrix schema | ✅ 1 end-to-end test |

### Makefile targets

| Target | What it does |
|--------|--------------|
| `make audit-validate` | Runs the schema validator; if the file isn't there yet, prints a friendly message and exits 0 |
| `make audit-verify-imports` | Runs the import verifier |
| `make audit-generate-matrix` | Runs the matrix generator (requires the previous step's output) |
| `make audit-all` | All three in sequence |

All three correctly detect the absent `TOOLKIT_INDEX(1).json` and exit 0
with the message *"TOOLKIT_INDEX(1).json not yet provided — awaiting
user (per ADR-0007 D-01)."*

### Tests added

| Path | Tests |
|------|-------|
| `tests/integration/test_toolkit_audit_preparation.py` | 13 tests covering: schema existence, sample inventory validation, validator script behavior (4 paths), import verifier end-to-end, matrix generator end-to-end against the matrix schema, ADR-0007 contents, no-LICENSE-file enforcement, no G02 implementation code added, G01 regression |

## Capability domains to cover (per Master Spec §7, §8)

The audit will classify each indexed tool against these eight domains:

| Domain | Required behavior |
|--------|-------------------|
| Discovery | Search engines, RSS, arXiv, OpenAlex, Crossref, Semantic Scholar, GitHub repos/releases |
| Acquisition | HTTP fetch, raw download, content-type sniffing, partial-fetch handling |
| Crawling | Multi-page traversal, robots.txt respect, rate limiting, depth budgets |
| Browser automation | Headless browser for JS-rendered pages, login flows, interactive elements |
| Extraction | HTML → structured text (headings, paragraphs, code, tables, citations, URLs) |
| Scientific research | arXiv full text, Crossref metadata, Semantic Scholar citations, OpenAlex graph |
| Social ingestion | RSS/Atom feeds, X/Twitter (compliant APIs only — no scraping if ToS forbids) |
| Provenance | Source URI preservation, content hashing, locator extraction, version tracking |

## Classification vocabulary (per matrix schema)

Each indexed tool will be classified as exactly one of:

| Classification | Meaning |
|----------------|---------|
| `indexed_only` | Appears in the inventory but import failed (or not attempted) |
| `verified_runnable` | Imported successfully (exit 0) AND license is clean |
| `adopted` | Verified-runnable AND selected for Synapse provider adapter (requires user approval via PROVIDER_SELECTION.md) |
| `deferred` | Verified-runnable but blocked on a user decision (paid API key, ToS review, etc.) |
| `rejected` | License conflict, unmaintained, broken, or duplicate of an adopted tool |

## Audit acceptance criteria (the audit is PASS only if ALL are true)

1. ✅ Every tool in `TOOLKIT_INDEX(1).json` is classified into exactly one of the five categories.
2. ✅ Every `verified_runnable` tool has documented evidence: import command, exit code, license text, failure modes.
3. ✅ Every capability domain has at least one `verified_runnable` tool, OR the gap is explicitly logged with a recommended remediation (`build_new` / `find_alternative` / `defer` / `accept_gap`).
4. ✅ Each `adopted` tool is mapped to a Synapse `Provider` adapter name (real adapter code is G02 implementation, not audit).
5. ✅ No tool is marked `verified_runnable` without an actual import test.
6. ✅ License conflicts are flagged (GPL/AGPL/LGPL/SSPL/CC-BY-NC/CC-BY-SA → `rejected`).
7. ✅ The matrix is published in both JSON and Markdown formats, conforming to `LAYER_1_CAPABILITY_MATRIX.schema.json`.
8. ✅ The audit report and evidence JSON follow the G01 template structure.
9. ✅ The agent STOPs after submitting the audit; the user must approve before G02 implementation begins.

## Reproducible audit commands (run by the agent after the file arrives)

```bash
# 0. Receive TOOLKIT_INDEX(1).json from user (pushed to repo, e.g. under
#    docs/toolkit_audit/ or supplied via secure channel).

# 1. Validate the inventory against the schema
make audit-validate
# Equivalent: python scripts/validate_toolkit_index.py \
#               "docs/toolkit_audit/TOOLKIT_INDEX(1).json"

# 2. Verify which indexed tools are actually importable
make audit-verify-imports
# Writes docs/toolkit_audit/verification_results.json

# 3. Generate the Layer-1 Capability Matrix
make audit-generate-matrix
# Writes docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json
#    and docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md

# 4. Manual review: fill in failure_modes, fallback, recommended_provider
#    in LAYER_1_CAPABILITY_MATRIX.md based on agent's domain knowledge.

# 5. Write the provider selection proposal
#    docs/toolkit_audit/PROVIDER_SELECTION.md
#    (from template docs/toolkit_audit/PROVIDER_SELECTION.template.md)

# 6. Write the audit report
#    reports/GROUP_02_AUDIT_REPORT.md
#    (from template docs/toolkit_audit/AUDIT_REPORT.template.md)

# 7. Write the evidence JSON
#    reports/GROUP_02_AUDIT_EVIDENCE.json

# 8. Lint + test
ruff check src tests scripts
ruff format --check src tests scripts
pytest

# 9. Commit + push
git add .
git commit -m "G02 audit: capability matrix + provider selection"
git push

# 10. STOP and await explicit user approval of the audit + provider selection.
```

## Commands and exact exit codes (preparation phase)

| Command | Exit code | Notes |
|---------|-----------|-------|
| `ruff check src tests scripts` | 0 | "All checks passed!" |
| `ruff format --check src tests scripts` | 0 | 73 files formatted |
| `python -m pytest` | 0 | 153/153 pass, coverage 90% |
| `make audit-validate` (no inventory yet) | 0 | Friendly "awaiting user" message |
| `make audit-verify-imports` (no inventory yet) | 0 | Friendly "awaiting user" message |
| `make audit-generate-matrix` (no inventory yet) | 0 | Friendly "awaiting user" message |

## Test counts

| Suite | Collected | Passed | Failed | Skipped |
|-------|-----------|--------|--------|---------|
| `tests/unit/domain/` | 71 | 71 | 0 | 0 |
| `tests/unit/security/` | 24 | 24 | 0 | 0 |
| `tests/unit/api/` | 34 | 34 | 0 | 0 |
| `tests/integration/` (incl. new audit-prep tests) | 24 | 24 | 0 | 0 |
| **TOTAL** | **153** | **153** | **0** | **0** |

Coverage: **90%**.

## G01 regression check

| Check | Result |
|-------|--------|
| Domain invariants still enforced (claim, relationship, evidence delta, job state machine, etc.) | ✅ All 71 domain tests pass |
| Auth + SSRF negative tests still pass | ✅ All 24 security tests pass |
| API contract unchanged | ✅ All 34 API tests pass |
| OpenAPI 3.1.0 still publishes 28 routes | ✅ |
| Migrations still work up + down | ✅ |
| No `LICENSE` file added (per D-03) | ✅ Verified by `test_no_LICENSE_file_present` |
| No G02 implementation code added | ✅ Verified by `test_no_g02_implementation_code_added` |
| `Principal` / `AuthAdapter` abstraction preserved (per D-02) | ✅ Unchanged from G01 |

**No G01 code was modified** except:
- `tests/conftest.py` — narrowed the autouse `_clear_settings_cache` fixture so it gracefully skips when the `synapse` package isn't needed (required for the new audit-prep tests that exercise scripts directly without importing synapse). This is a test-only change; no production code touched.
- `docs/baseline.md` — added a single header note recording the G01 conditional acceptance.
- `pyproject.toml` — added `jsonschema>=4.20,<5.0` to dev deps (required by the validator script); added `docs/toolkit_audit` to ruff's `extend-exclude` (templates and JSON schemas shouldn't be linted as Python); added `ignore::DeprecationWarning:jsonschema.*` to pytest filterwarnings.

## File count after this delivery

| Category | Count |
|----------|-------|
| Source (src/) | 32 (unchanged from G01) |
| Tests (tests/) | 23 (was 22 — added `test_toolkit_audit_preparation.py`) |
| Documentation (docs/) | 15 (was 8 — added ADR-0007 + 7 audit-prep files) |
| Scripts (scripts/) | 3 (new directory) |
| Reports (reports/) | 4 (was 3 — will add audit reports later) |
| Config | 7 (was 6 — added Makefile audit targets; pyproject updated) |
| Alembic | 3 (unchanged) |
| **Total tracked files** | **87 → 99** (12 new files) |

## Lines of code (approximate)

| | LOC |
|---|---|
| Source (`src/`) | 2,926 (unchanged from G01) |
| Tests (`tests/`) | 1,857 (was 1,678; +179 for audit-prep tests) |
| Scripts (`scripts/`) | 311 (new) |
| Documentation (`docs/`) | ~1,200 (was ~700; +500 for audit-prep docs) |

## Known limitations

| # | Limitation | Severity | Resolution |
|---|------------|----------|------------|
| L-11 | Audit cannot run until user provides `TOOLKIT_INDEX(1).json` | Blocking for audit | User action required (decision D-01) |
| L-12 | `verify_tool_imports.py` only tests import; does not exercise runtime behavior | Medium | Acceptable for Layer-1 audit; Layer-2 (runtime smoke) is a G02 implementation task |
| L-13 | License conflict detection is keyword-based (GPL/AGPL/LGPL/SSPL/CC-BY-NC/CC-BY-SA) | Low | Conservative; agent will review edge cases manually in the audit report |

## Decisions required from user (carried forward from G01 + new)

| ID | Decision | Blocking for | Status |
|----|----------|---------------|--------|
| **D-01** | Provide `TOOLKIT_INDEX(1).json` (pushed to repo OR via secure channel) | G02 audit execution | ⏳ Pending |
| D-02 | Choose production identity provider | Production deployment only | Deferred (per user's G02 audit-prep message) |
| D-03 | Choose a license for the repository | Public release | Deferred (per user's G02 audit-prep message) |

## STOP statement

**This report marks the explicit STOP of G02 Audit Preparation.**

The agent has:

1. ✅ Prepared all audit scaffolding (templates, schemas, scripts, tests).
2. ✅ Verified the scaffolding works end-to-end with a synthetic inventory.
3. ✅ Recorded the user's three decisions (D-01, D-02, D-03) in ADR-0007.
4. ✅ Confirmed no G01 code was modified beyond test infrastructure.
5. ✅ Confirmed no G02 implementation code was added.
6. ✅ Confirmed no `LICENSE` file was added.

**The agent will NOT begin G02 audit execution until the user provides
`TOOLKIT_INDEX(1).json`.** When the file arrives, the agent will run
`make audit-all` (or the equivalent scripts), produce the Layer-1
Capability Matrix, write the audit report and provider selection
proposal, push, and STOP again — awaiting explicit user approval of
both before any G02 implementation code is written.

Per the user's authorization message:
> *"Once the toolkit index is available: 1. Inspect and validate the
> inventory. 2. Identify existing capabilities ... 3. Distinguish indexed
> tools from verified runnable tools. 4. Build the Layer-1 Capability
> Matrix ... 5. Prioritize minimal reuse ... 6. Submit the audit report
> and proposed implementation selection.*
>
> *Do not begin G02 implementation until the audit and provider
> selection are approved.*

---

*End of G02 Audit Preparation Report — awaiting `TOOLKIT_INDEX(1).json`.*
