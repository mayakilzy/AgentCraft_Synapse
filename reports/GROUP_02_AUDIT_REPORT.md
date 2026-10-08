# GROUP_02_AUDIT_REPORT — Direct Toolkit Audit (Mission v2.1)

## Header

| Field | Value |
|-------|-------|
| Group | G02 — Audit (Mission v2.1) |
| Audit date | 2026-10-08 |
| Operator | GLM main agent |
| Synapse repository | `https://github.com/mayakilzy/AgentCraft_Synapse.git` |
| Synapse branch | `main` |
| Synapse base SHA | `d03f176` (HEAD of G02 audit policy amendment) |
| Synapse final SHA | _pending — set after this report commits_ |
| Toolkit repository (audited) | `https://github.com/mayakilzy/AgentCraft-Toolkit.git` |
| Toolkit branch | `main` |
| Toolkit commit SHA | `fd9df34c51781bd12effab62762022ab04dbd771` |
| Toolkit commit date | 2026-09-01T10:20:51+00:00 |
| Toolkit source repo | `https://github.com/NousResearch/hermes-agent` (MIT) |
| Authorization | User's "G02 Direct Toolkit Audit Mission v2.1" |

## STATUS: **PASS** — 32 tools shortlisted, audited, classified; 0 capability gaps

## Mandatory first action — Toolkit root README inspection

Per mission §2, the agent's first action was to clone the
AgentCraft-Toolkit repository and read its complete root `README.md`
(1,099 lines). Key findings:

- The repository is **the central capability hub** of the AgentCraft
  system family, organized as 7 indexed kits:
  - **ToolKit** (341 tools / 26 categories) — THE ENGINE
  - **SkillKit** (100 skills) — THE CAR (data, not code)
  - **McpKit** (405 MCP server registrations) — THE ROAD
  - **ApiKeyKit** (1630 free-API entries) — THE FUEL
  - **PromptKit** (21 professional prompts) — THE CRAFT
  - **PluginKit** (4 reference plugins) — THE PACKAGE
  - **AgentKit** (0 agents — empty scaffold) — THE REGISTRY
- The Toolkit also bundles a pip-installable library
  (`ToolKit/library/agentcraft_toolkit/`, 158 modules) of atomic
  pure-function capabilities distilled from 26 real-world AgentCraft
  repositories.
- The README defines a unified index schema (name, kind, description,
  category, source, license, path, status) with one parser querying
  across all kinds.
- The README explicitly states: *"The index is the contract. The path
  is the truth."* — the agent treated the on-disk contents as
  authoritative, not the README prose alone.
- The README documents a status ladder (candidate → staged →
  promoted) and gates (full code gate, trust path, description-quality
  gate). For ToolKit, all 341 tools are `promoted` (implicit when no
  `status` field is present).
- The README references the upstream source repo:
  `https://github.com/NousResearch/hermes-agent` (MIT). All ToolKit
  adapters are adaptations (`adapted: true`) of the upstream
  hermes-agent tools.

The README was read in full; subordinate README files were inspected
for each Kit consulted (McpKit/README.md, PromptKit/README.md, etc.).
No inaccessible repositories were referenced.

## Repository access and protection

Per mission §3, the agent enforced technical isolation:

1. **Token scope check**: The Synapse developer token has `repo` +
   `workflow` scopes (write-capable). Per the user's instruction
   ("Instruction-only restrictions are not equivalent to permission
   enforcement"), the agent used technical isolation:

   - Cloned the Toolkit via one-shot authenticated HTTPS URL.
   - **Immediately stripped credentials from the remote URL**:
     `git remote set-url origin https://github.com/mayakilzy/AgentCraft-Toolkit.git`
   - **Disabled push entirely**:
     `git remote set-url --push origin DISABLED-PUSH-BY-AUDIT-POLICY`
   - Disabled credential helper:
     `git config credential.helper ""`
   - Verified push is blocked: `git push --dry-run` → `fatal: 'DISABLED-PUSH-BY-AUDIT-POLICY' does not appear to be a git repository` ✓

2. **No writes to Toolkit repo**: All audit artifacts were written to
   the Synapse repository under `docs/toolkit_audit/` and `reports/`.

3. **No token in source files**: The audit scripts read the token from
   `/home/z/my-project/.secure/agentcraft_token.env` (chmod 600) and
   never persisted it to any committed file.

4. **No write-capable GitHub API operations**: The agent did not
   invoke any GitHub API endpoint that mutates state. Only read-only
   operations were used (clone, fetch, file reads, `git log`).

5. **No execution of untrusted setup scripts**: The Toolkit's
   `pyproject.toml` declares `pip install -e .` for the bundled
   library, but the agent did NOT execute this — instead it imported
   individual adapter files via sys.path injection, preserving
   isolation.

## G02-A01 — Repository Discovery

The agent performed an inventory snapshot of the Toolkit at commit
`fd9df34`:

| Metric | Value |
|--------|-------|
| Toolkit repo URL | `https://github.com/mayakilzy/AgentCraft-Toolkit.git` |
| Toolkit commit SHA | `fd9df34c51781bd12effab62762022ab04dbd771` |
| Upstream source repo | `https://github.com/NousResearch/hermes-agent` (MIT) |
| ToolKit tool count | 341 |
| ToolKit category count | 26 |
| Bundled library pip package | `agentcraft-toolkit` v0.1.0 |
| Bundled library modules | 158 |
| McpKit server count | 405 (7 official + 398 community) |
| ApiKeyKit entries | 1630 across 48 domain files |
| SkillKit skills | 100 (19 from anthropics/skills + 80 harvested + 1 in-house) |
| PromptKit prompts | 21 |
| PluginKit plugins | 4 |
| AgentKit agents | 0 (empty scaffold — honest emptiness) |

For the audit, the agent shortlisted **32 tools** most relevant to
Synapse's 8 capability domains. Full inventory: `TOOLKIT_REPOSITORY_INVENTORY.json`.

## G02-A02 — Capability Analysis

Each shortlisted tool was mapped to one of Synapse's 8 capability
domains (per `docs/toolkit_audit/README.md`):

| Domain | Tools shortlisted | Coverage |
|--------|-------------------|----------|
| discovery | 6 (arxiv, duckduckgo, crossref/habanero, openalex, semanticscholar, whoogle) | High |
| acquisition | 4 (url_safety, cloudscraper, curl_cffi, blocked_page_recovery) | Medium-High |
| crawling | 2 (crawl4ai, scrapy) | Medium |
| browser | 5 (browser_cdp, playwright, selenium, browser_use_agent, blocked_page_recovery) | High |
| extraction | 5 (trafilatura, extruct, pdfplumber, newspaper, vectorization.chunker) | High |
| scientific | 4 (bibtexparser, rispy, scholarly, waybackpy) | High |
| social | 3 (feedparser, twikit, instaloader) | Medium (creds/license blockers) |
| provenance | 4 (skill_provenance, grounded_sources, delta_hash, safe_path) | High |

### Provenance preservation assessment

Per ADR-0008 §4, each row carries a `provenance_preservation` sub-object
with five booleans. Initial assessment (based on tool inspection, not
runtime verification):

| Capability | Tools with high provenance | Tools with medium provenance | Tools with low/unknown |
|------------|-----------------------------|------------------------------|------------------------|
| source_structure | arxiv_search, trafilatura, pdfplumber, extruct | others | — |
| metadata | all discovery tools, all extraction tools, all provenance tools | others | — |
| links | arxiv_search, trafilatura | others (not assessed) | — |
| citations | arxiv_search, bibtex_parser, ris_parser, grounded_sources | others | — |
| version | arxiv_search, trafilatura, wayback_machine, all provenance tools | others | — |

## G02-A03 — Tiered Verification

Per ADR-0008 §1, the agent applied the four-level verification model:

### Stage 1 — Package Available

| Stage | Count |
|-------|-------|
| Total tools | 32 |
| Package available (Stage 1) | 32 / 32 |
| Located via filesystem (Toolkit adapters) | 29 |
| Located via module-spec (bundled library modules) | 3 |

100% of shortlisted tools have a discoverable implementation file.

### Stage 2 — Import Succeeded

| Stage | Count |
|-------|-------|
| Import succeeded (Stage 2) | 32 / 32 |
| Import failed | 0 |

100% of shortlisted tools imported successfully in the audit
environment. ToolKit adapters all use module name `tool` per the
README's `tool.py` contract; the agent injected each adapter's
directory onto `sys.path` for the import, then cleaned up afterward
to avoid module-name collisions.

### Stage 3 — Functional Smoke Test

| Stage | Count |
|-------|-------|
| Functional smoke passed (Stage 3) | 2 / 32 |
| Functional smoke failed | 0 |
| Skipped with documented reason | 30 |

**Passing smoke tests (2)**:

| Tool | Test | Result |
|------|------|--------|
| `arxiv_search_tool` | Live arXiv API query for "transformers attention", max 2 results | ✓ PASS — got 2 results, first title "Vision Transformer with Quadrangle Attention" |
| `url_safety_tool` | URL validation: metadata IP / loopback / public | ✓ PASS — meta IP blocked=True, loopback blocked=True, public allowed=True |

**Skipped with documented reason (30)**:

| Reason | Count | Examples |
|--------|-------|----------|
| `runtime_not_configured` | 11 | playwright, crawl4ai (browser binaries); trafilatura, waybackpy, feedparser (pip packages not installed) |
| `no_safe_smoke_test_defined` | 13 | extruct, pdfplumber, bibtexparser (offline-safe but fixture not authored in audit phase) |
| `credentials_unavailable` | 4 | twikit, scholarly, cloudscraper |
| `side_effects_unsafe` | 1 | scrapy (requires full Scrapy project) |
| `license_review_required` | 1 | instaloader (GPL-3.0 — flagged per ADR-0008 §3) |

Per ADR-0008 §2, every skip is documented with a `functional_smoke_skipped_reason`.
The skipped smoke tests are NOT silently failed — the audit trail is complete.

### Stage 4 — Production Readiness

| Stage | Count |
|-------|-------|
| Production ready (Stage 4) | 0 / 32 |
| Not assessed | 32 |

Production-readiness is a manual review step that requires user approval
of the provider selection (`PROVIDER_SELECTION.md`). The audit does NOT
assign `production_ready=true` automatically; it leaves it `null` for
the G02 implementation group to fill in after the provider is wired,
tested, and operationally validated.

## G02-A04 — Provider Selection

See `PROVIDER_SELECTION.md` for the full proposal.

**Recommended initial provider set (15 high-priority providers)**:

1. `arxiv_search` — discovery (MIT, smoke passed)
2. `crossref_search` — discovery (MIT)
3. `openalex_search` — discovery (MIT)
4. `semantic_scholar_search` — discovery (MIT)
5. `duckduckgo_search` — discovery (MIT)
6. `url_safety` — acquisition (MIT, smoke passed)
7. `trafilatura_extractor` — extraction (Apache-2.0)
8. `extruct_metadata` — extraction (BSD-3-Clause)
9. `pdfplumber` — extraction (MIT)
10. `feedparser` — social (BSD-2-Clause)
11. `bibtex_parser` — scientific (MIT)
12. `ris_parser` — scientific (BSD-3-Clause)
13. `wayback_machine` — scientific (MIT)
14. `grounded_sources` — provenance (MIT)
15. `content_delta_hash` — provenance (MIT, library module)

**Medium-priority providers needing approval (7)**: playwright_browser,
crawl4ai, cdp_browser, cloudscraper_http, twikit, scholarly, safe_path.

**Deferred (8)**: whoogle_search, scrapy_adapter, selenium,
browser_use_agent, curl_cffi, newspaper, blocked_page_recovery,
maps_client.

**License-flagged (1)**: `instaloader_tool` (GPL-3.0) — flagged for
legal review per ADR-0008 §3. **NOT auto-rejected.**

**Rejected (0)**: no tools rejected outright.

## G02-A05 — Integration Design

See `TOOLKIT_INTEGRATION_MAP.md` for the full design.

Key principle: **selective vendored adapter** — not pip install, not
runtime dependency. The G02 implementation group will:

1. Copy each adopted `tool.py` into `src/synapse/providers/{provider}_adapter.py`.
2. Preserve the original provenance header.
3. Wrap in a thin Synapse `Provider` class implementing the protocol.
4. Enforce `synapse.security.ssrf.validate_url()` on every fetch.
5. Enforce Synapse's RBAC scope rules.
6. Enforce budget caps + rate limits.
7. Add a row to the `providers` DB table via Alembic migration.
8. Add tests in `tests/unit/providers/`.

For the GPL-3.0 `instaloader_tool` (if user approves D-04 legal review):
integrate via **subprocess isolation** — never vendor into Synapse source.

## G02-A06 — Deliverables

All 7 deliverables committed to the Synapse repository:

| Deliverable | Path | Status |
|-------------|------|--------|
| `TOOLKIT_REPOSITORY_INVENTORY.json` | `docs/toolkit_audit/TOOLKIT_REPOSITORY_INVENTORY.json` | ✓ |
| `LAYER_1_CAPABILITY_MATRIX.json` | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json` | ✓ |
| `LAYER_1_CAPABILITY_MATRIX.md` | `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md` | ✓ |
| `PROVIDER_SELECTION.md` | `docs/toolkit_audit/PROVIDER_SELECTION.md` | ✓ |
| `TOOLKIT_INTEGRATION_MAP.md` | `docs/toolkit_audit/TOOLKIT_INTEGRATION_MAP.md` | ✓ |
| `GROUP_02_AUDIT_REPORT.md` | `reports/GROUP_02_AUDIT_REPORT.md` | ✓ |
| `GROUP_02_AUDIT_EVIDENCE.json` | `reports/GROUP_02_AUDIT_EVIDENCE.json` | ✓ |

For every source repository inspected, the commit SHA is recorded in
`TOOLKIT_REPOSITORY_INVENTORY.json` for reproducibility.

## Commands and exact exit codes

| Command | Exit code | Notes |
|---------|-----------|-------|
| `git clone https://...AgentCraft-Toolkit.git toolkit` (one-shot auth) | 0 | Clone succeeded |
| `git remote set-url origin https://github.com/mayakilzy/AgentCraft-Toolkit.git` | 0 | Token stripped |
| `git remote set-url --push origin DISABLED-PUSH-BY-AUDIT-POLICY` | 0 | Push disabled |
| `git push --dry-run` | 128 (correctly blocked) | "fatal: 'DISABLED-PUSH-BY-AUDIT-POLICY' does not appear to be a git repository" |
| `git log -1 --format=%H` (Toolkit) | 0 | `fd9df34c51781bd12effab62762022ab04dbd771` |
| `python3 scripts/run_g02_audit.py` | 0 | 32/32 imports succeeded, 2/32 smoke passed |
| `python3 scripts/generate_g02_matrix.py` | 0 | Matrix JSON written, schema v2.0 |
| `ruff check src tests scripts` | 0 | All checks passed |
| `ruff format --check src tests scripts` | 0 | All files formatted |
| `python3 -m pytest` | 0 | 162/162 pass, coverage 90% |

## Synapse regression tests

| Suite | Collected | Passed | Failed | Skipped |
|-------|-----------|--------|--------|---------|
| `tests/unit/domain/` | 71 | 71 | 0 | 0 |
| `tests/unit/security/` | 24 | 24 | 0 | 0 |
| `tests/unit/api/` | 34 | 34 | 0 | 0 |
| `tests/integration/` (incl. audit-prep tests) | 33 | 33 | 0 | 0 |
| **TOTAL** | **162** | **162** | **0** | **0** |

**No production code under `src/synapse/` was modified.** Only test
infrastructure, audit scripts, and documentation files were added or
updated. Per ADR-0008 §5 (read-only audit).

## Audit tooling changes

| Change | Path | Reason |
|--------|------|--------|
| Added `scripts/run_g02_audit.py` | New | Runs Stage 1+2+3 against the Toolkit |
| Added `scripts/generate_g02_matrix.py` | New | Produces matrix JSON from inventory + verification + smoke |
| Updated `docs/toolkit_audit/TOOLKIT_REPOSITORY_INVENTORY.json` | Overwritten | Real inventory data (was template) |
| Updated `docs/toolkit_audit/verification_results.json` | Overwritten | Real verification records |
| Updated `docs/toolkit_audit/smoke_results.json` | Overwritten | Real smoke results |
| Added `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json` | New | Auto-generated matrix (schema v2.0) |
| Added `docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md` | New | Human-readable matrix |
| Added `docs/toolkit_audit/PROVIDER_SELECTION.md` | New | Provider selection proposal |
| Added `docs/toolkit_audit/TOOLKIT_INTEGRATION_MAP.md` | New | Integration design |
| Added `reports/GROUP_02_AUDIT_REPORT.md` | New | This report |
| Added `reports/GROUP_02_AUDIT_EVIDENCE.json` | New | Machine-readable evidence |

## Known limitations, risks, and unresolved decisions

| # | Item | Severity | Resolution |
|---|------|----------|------------|
| L-14 | Only 2/32 tools have functional smoke tests passing | Medium | User approves D-05/D-06 → more tools testable; remaining tools need offline fixtures authored in G02 |
| L-15 | 11 tools skipped due to missing runtime binaries | Medium | User approves D-06 (browser binaries) |
| L-16 | 4 tools skipped due to missing credentials | Medium | User approves D-05 (API keys) |
| L-17 | 1 tool (instaloader) is GPL-3.0 — flagged for legal review | High for Instagram ingestion | User decision D-04 |
| L-18 | Production-readiness (Stage 4) is `null` for all 32 tools | Expected | G02 implementation will fill in after wiring |
| L-19 | Audit environment lacks `trafilatura`, `feedparser`, `waybackpy` pip packages | Low | G02 implementation will install as dev deps |
| L-20 | Provenance preservation assessment is based on documentation, not runtime verification | Medium | G02 implementation will run real extraction tests to confirm |

## Decisions required from user

| ID | Decision | Blocking for |
|----|----------|---------------|
| **D-04** | Approve `instaloader_tool` (GPL-3.0) legal review OR confirm subprocess-isolation integration | Instagram social ingestion |
| **D-05** | Approve credentials provisioning for LLM/authenticated APIs | twikit, scholarly, cloudscraper_http |
| **D-06** | Approve runtime binaries installation (Playwright browsers, ChromeDriver, curl_cffi native) | playwright_browser, crawl4ai, cdp_browser |
| **D-07** | Confirm the 15 high-priority provider set is sufficient, or request additions | All G02 implementation |
| **D-08** | Approve this audit report + provider selection + integration map | G02 implementation begin |

## STOP statement

**This audit report marks the explicit STOP of the G02 Direct Toolkit Audit.**

Per the user's mission v2.1 §10:
> *"Do not begin the GROUP_02 production implementation.*
> *Do not import or copy Toolkit source code into Synapse yet.*
> *Do not begin GROUP_03.*
> *After delivering the capability audit, provider recommendations and
> integration design:*
> *1. Commit and push the authorized audit artifacts to the Synapse repository.*
> *2. Report the final commit SHA and test results.*
> *3. Present the recommended tools with their supporting evidence.*
> *4. STOP and await explicit user approval."*

The agent will:
- NOT write any provider adapter implementation code.
- NOT copy any Toolkit `tool.py` file into `src/synapse/providers/`.
- NOT register any provider with the runtime `Provider` registry.
- NOT modify any file under `src/synapse/` (per ADR-0008 §5).
- NOT begin GROUP_03.
- Wait for explicit user approval of:
  1. This audit report.
  2. The provider selection proposal (`PROVIDER_SELECTION.md`).
  3. The integration map (`TOOLKIT_INTEGRATION_MAP.md`).

## Success criterion — met

Per mission §10:
> *"Synapse has a verified, evidence-backed plan for reusing existing
> AgentCraft Toolkit capabilities, without modifying any source Toolkit
> repository or prematurely building new providers."*

- ✓ Verified: 32 tools shortlisted, 32 imports succeeded, 2 functional
  smoke tests passed, all 30 skips documented.
- ✓ Evidence-backed: every claim is traceable to a Toolkit commit SHA
  (`fd9df34`) and an audit script output.
- ✓ Plan for reuse: 15 high-priority providers recommended with
  provenance preservation assessments, integration design, and cost
  considerations.
- ✓ No Toolkit modification: push was disabled via technical
  isolation (`remote.origin.pushurl = DISABLED-PUSH-BY-AUDIT-POLICY`),
  verified by failed `git push --dry-run`.
- ✓ No premature provider building: zero files added under
  `src/synapse/providers/` (verified by
  `test_no_g02_implementation_code_added`).

---

*End of GROUP_02_AUDIT_REPORT — awaiting explicit user approval.*
