# Toolkit Integration Map — G02 Audit

> Per the user's mission v2.1 §8: describe how each candidate integrates
> behind Synapse's existing abstractions WITHOUT tightly coupling the
> Synapse Core to the Toolkit repository structure.
>
> **The Toolkit must NOT become a runtime dependency unless explicitly
> justified and approved.**

## Architectural principle

Per ADR-0001 (modular monolith) and ADR-0006 (API-first contract):

- Synapse Core (`src/synapse/`) exposes stable abstractions: `Provider` protocol, `Source`, `Acquisition`, `EvidenceFragment`, `Job`, etc.
- External capabilities (LLMs, fetchers, crawlers, extractors) live behind `Provider` adapters under `src/synapse/providers/`.
- The Toolkit is a **catalog** of well-structured adapters — but Synapse does NOT import the Toolkit as a runtime dependency.

## Integration pattern — SELECTIVE VENDORED ADAPTER (not pip install)

For each adopted ToolKit adapter, the G02 implementation group will:

1. **Copy the `tool.py` adapter** from `ToolKit/{category}/{tool}/tool.py` into `src/synapse/providers/{provider_name}_adapter.py`.
   - Preserve the original provenance header: `Origin: <external_repo> (<url>, <license>)`.
   - Preserve `__source_repo__`, `__license__`, `RUNTIME_DEPS` attributes.
2. **Add a thin Synapse Provider wrapper** that:
   - Implements the Synapse `Provider` protocol (`name`, `kind`, `enabled`, `health_check()`).
   - Translates Synapse domain types (`Source`, `Acquisition`, `EvidenceFragment`) to/from the tool's `dict` return shape.
   - Wires the Synapse SSRF guard around every URL the tool fetches.
   - Enforces Synapse's auth + RBAC scope rules (e.g., operator-level for `crawl4ai`).
3. **Register the provider** in the Synapse provider registry (`src/synapse/providers/__init__.py`).
4. **Add a row to the `providers` table** in the database (via Alembic migration in G02 implementation).
5. **Add tests** in `tests/unit/providers/` covering:
   - Adapter instantiation + happy-path functional smoke (using a fixture input).
   - SSRF guard: blocked URL rejected.
   - Provenance preservation: source URI + content hash + locator extracted.

**Critical**: the copied `tool.py` adapter files live in Synapse's
`src/synapse/providers/` directory as Synapse code — NOT as a runtime
dependency on the Toolkit repo. The Toolkit commit SHA (`fd9df34`) is
recorded in the provider's `__source_repo__` attribute for reproducibility,
but Synapse does NOT clone or import the Toolkit at runtime.

## Vendor-vs-subprocess decision matrix

| Provider | Vendor in Synapse? | Subprocess? | Reason |
|----------|--------------------|-------------|--------|
| `arxiv_search` | ✓ Vendor | — | Pure stdlib; tiny file; no runtime deps |
| `crossref_search` (habanero) | ✓ Vendor adapter | pip install `habanero` | Adapter is thin; library is pip-installed separately |
| `openalex_search` (pyalex) | ✓ Vendor adapter | pip install `pyalex` | Same |
| `semantic_scholar_search` | ✓ Vendor adapter | pip install `semanticscholar` | Same |
| `url_safety` | ✓ Vendor (already similar to `synapse.security.ssrf`) | — | Synapse has its own SSRF guard — compare and merge the best of both |
| `trafilatura_extractor` | ✓ Vendor adapter | pip install `trafilatura` | Adapter is thin |
| `extruct_metadata` | ✓ Vendor adapter | pip install `extruct` | Adapter is thin |
| `pdfplumber` | ✓ Vendor adapter | pip install `pdfplumber` | Adapter is thin |
| `feedparser` | ✓ Vendor adapter | pip install `feedparser` | Adapter is thin |
| `bibtex_parser` | ✓ Vendor adapter | pip install `bibtexparser` | Adapter is thin |
| `ris_parser` | ✓ Vendor adapter | pip install `rispy` | Adapter is thin |
| `wayback_machine` | ✓ Vendor adapter | pip install `waybackpy` | Adapter is thin |
| `grounded_sources` | ✓ Vendor (pure stdlib) | — | Pure stdlib; tiny file |
| `content_delta_hash` | ✓ Vendor (library module) | — | Pure stdlib; tiny file |
| `safe_path` | ✓ Vendor (library module) | — | Pure stdlib |
| `playwright_browser` | ✓ Vendor adapter | pip install `playwright` + `playwright install chromium` | Adapter is thin; browser binary required |
| `crawl4ai` | ✓ Vendor adapter | pip install `crawl4ai` + browser binaries | Adapter is thin |
| `cdp_browser` | ✓ Vendor adapter | — (assumes external Chrome) | Adapter is thin |
| `duckduckgo_search` | ✓ Vendor adapter | pip install `duckduckgo_search` | Adapter is thin |
| `cloudscraper_http` | ✓ Vendor adapter | pip install `cloudscraper` | Adapter is thin |
| `twikit` | ✓ Vendor adapter | pip install `twikit` | Adapter is thin; cookie-based auth |
| `scholarly` | ✓ Vendor adapter | pip install `scholarly` + proxy/captcha service | Adapter is thin; ToS risk |
| `instaloader` (GPL-3.0) | ✗ DO NOT vendor | ✓ Subprocess isolation | GPL license — vendoring into proprietary codebase triggers copyleft. Run as subprocess to maintain separation. |

## Integration flow — end-to-end data path

```
              ┌──────────────────────────────────────────────┐
              │  AgentCraft-Toolkit @ fd9df34 (READ ONLY)    │
              │  ───────────────────────────────────────     │
              │  ToolKit/research/arxiv_search_tool/tool.py  │
              │  ToolKit/web/trafilatura_extractor_tool/      │
              │  ToolKit/security/url_safety_tool/            │
              │  ... (32 shortlisted adapters)               │
              └─────────────────┬────────────────────────────┘
                                │
                                │  copy tool.py adapter
                                │  + write Synapse Provider wrapper
                                │  + record provenance header
                                ▼
              ┌──────────────────────────────────────────────┐
              │  Synapse Core: src/synapse/providers/         │
              │  ───────────────────────────────────────      │
              │  arxiv_search_provider.py                    │
              │    class ArxivSearchProvider:                 │
              │      name = "arxiv"                          │
              │      kind = "search"                         │
              │      def discover(query):                    │
              │        # wraps ArxivSearchTool.run(query)     │
              │        # enforces SYNAPSE_SSRF + rate-limit     │
              │        # returns Synapse Source candidates     │
              │                                              │
              │  trafilatura_extractor_provider.py            │
              │    class TrafilaturaExtractorProvider:        │
              │      def extract(html_or_url):                │
              │        # wraps trafilatura.extract()           │
              │        # returns Synapse EvidenceFragment       │
              │                                              │
              │  ... (one file per adopted adapter)           │
              └─────────────────┬────────────────────────────┘
                                │
                                │  register in __init__.py
                                │  + Alembic migration: insert row in `providers`
                                ▼
              ┌──────────────────────────────────────────────┐
              │  Synapse Acquisition Router                  │
              │  (src/synapse/application/acquisition.py)    │
              │  ───────────────────────────────────────     │
              │  Pick provider by source_type + capability    │
              │  Enforce budget caps (per Master Spec §14)     │
              │  Emit Acquisition + Job records               │
              └─────────────────┬────────────────────────────┘
                                │
                                ▼
              ┌──────────────────────────────────────────────┐
              │  Synapse Extraction Pipeline                 │
              │  (src/synapse/application/extraction.py)     │
              │  ───────────────────────────────────────     │
              │  Extract → normalize → verify → store          │
              │  Preserve source URI + content_hash + locator  │
              │  Emit EvidenceFragment records                 │
              └─────────────────┬────────────────────────────┘
                                │
                                ▼
              ┌──────────────────────────────────────────────┐
              │  Synapse Persistence Layer                   │
              │  (src/synapse/storage/models.py)             │
              │  ───────────────────────────────────────     │
              │  sources, acquisitions, evidence_fragments, │
              │  audit_events (immutable evidence deltas)     │
              └──────────────────────────────────────────────┘
```

## What Synapse does NOT do

1. **NO runtime dependency on the Toolkit repo.** Synapse does not clone, import, or pip-install `agentcraft-toolkit` at runtime.
2. **NO writes to the Toolkit repo.** Per the user's mission v2.1 §3, all writes are blocked via `git remote set-url --push origin DISABLED-PUSH-BY-AUDIT-POLICY`.
3. **NO bypassing of Synapse's existing security.** Every ToolKit adapter is wrapped by a Synapse Provider that:
   - Enforces `synapse.security.ssrf.validate_url()` on every fetched URL.
   - Enforces Synapse's RBAC scope (e.g., `OPERATOR` scope for `crawl4ai`).
   - Enforces Synapse's rate limits + budget caps.
4. **NO modification of ToolKit source files.** All copies are made into Synapse's own `src/synapse/providers/` directory.
5. **NO GPL components vendored into Synapse source.** `instaloader_tool` (GPL-3.0) is integrated via subprocess isolation only if user approves legal review (D-04).

## Reproducibility — every adapter is traceable

For every copied adapter, Synapse records:

```python
# In src/synapse/providers/arxiv_search_provider.py

# Origin: ToolKit/research/arxiv_search_tool/tool.py
# Toolkit repo: https://github.com/mayakilzy/AgentCraft-Toolkit.git
# Toolkit commit: fd9df34c51781bd12effab62762022ab04dbd771 (2026-09-01)
# Upstream source: https://github.com/NousResearch/hermes-agent (MIT)
# Adapted: yes (per ToolKit index `adapted: true`)

__source_repo__ = "https://github.com/NousResearch/hermes-agent"
__license__ = "MIT"
__toolkit_commit__ = "fd9df34c51781bd12effab62762022ab04dbd771"
__toolkit_path__ = "ToolKit/research/arxiv_search_tool/tool.py"

class ArxivSearchProvider:
    name = "arxiv"
    kind = "search"
    ...
```

This header makes the audit trail reproducible: any reviewer can verify
the adapter's exact origin by inspecting the Toolkit at the recorded commit.

## Verification — Synapse tests still pass

The G02 audit did NOT modify any file under `src/synapse/`. All 162 G01 +
audit-prep tests still pass. The audit scripts added:

- `scripts/run_g02_audit.py` — runs Stage 1 + 2 + 3 against the Toolkit
- `scripts/generate_g02_matrix.py` — produces the matrix JSON

These scripts are themselves tested by `tests/integration/test_toolkit_audit_preparation.py`.

## STOP statement

**This integration map is part of G02 Audit Preparation.** The agent
will NOT implement any provider adapter until the user explicitly
approves:

1. The audit report (`GROUP_02_AUDIT_REPORT.md`).
2. The provider selection proposal (`PROVIDER_SELECTION.md`).
3. This integration map (`TOOLKIT_INTEGRATION_MAP.md`).

After approval, the G02 implementation group will follow this map
exactly: copy adapters, wrap in Synapse Providers, register, test,
migrate, ship — without ever making the Toolkit a runtime dependency.
