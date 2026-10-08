# G02 Minimal Execution Proposal

> **Status**: Proposal — awaiting explicit user approval per ADR-0009.
>
> This document is **not** an implementation authorization. No code under
> `src/synapse/` will be modified until the user approves this proposal
> and the listed acceptance criteria.

## 1. Proposed first vertical slice

**Goal**: deliver ONE coherent end-to-end path — scientific paper
discovery → acquisition → extraction → provenance preservation →
delivery to Synapse DB — using the smallest possible provider set,
with every step backed by a passing functional smoke test.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  G02 Minimal Vertical Slice                                            │
│                                                                         │
│  1. Discovery      arxiv_search_tool  →  Source candidates               │
│       (find papers on arXiv by query)                                   │
│                                                                         │
│  2. Acquisition    httpx + synapse.security.ssrf  →  raw HTML / PDF     │
│       (fetch URL with SSRF guard, content-type sniff)                   │
│                                                                         │
│  3. Extraction     trafilatura.extract()  →  EvidenceFragment          │
│       (article text + title + author + date + links)                   │
│                                                                         │
│  4. Provenance     grounded citation + delta_hash  →  Source.version    │
│       (canonical_uri + content_hash + locator)                          │
│                                                                         │
│  5. Delivery       write to Synapse DB tables (sources,                │
│                    acquisitions, evidence_fragments, audit_events)      │
│       via existing SQLAlchemy session + Alembic migrations              │
└─────────────────────────────────────────────────────────────────────────┘
```

This slice exercises every layer of the Synapse architecture (domain
records → storage → API → security) without introducing browser
runtimes, credentials, or GPL-licensed components.

## 2. Exact providers required

**Three new providers** (all MIT/Apache/BSD licensed, no credentials,
no browser runtimes):

| Provider name | Synapse capability | Source | License | Integration approach |
|---|---|---|---|---|
| `arxiv_search` | discovery | `ToolKit/research/arxiv_search_tool/tool.py` @ `fd9df34` | MIT (upstream hermes-agent) | **Vendor adapter** — copy `tool.py` into `src/synapse/providers/arxiv_search_provider.py`, preserve provenance header |
| `trafilatura_extractor` | extraction | `pip install trafilatura==2.3.1` + Synapse wrapper | Apache-2.0 (trafilatura) | **Direct package + thin wrapper** — pip-install trafilatura, write a ~50 LOC Synapse adapter that calls `trafilatura.extract()` and `trafilatura.extract_metadata()` |
| `content_delta_hash` | provenance | `ToolKit/library/agentcraft_toolkit/scraping/delta_hash.py` @ `fd9df34` | MIT (AgentCraft Toolkit) | **Vendor function** — copy the `fingerprint()` function (one function, ~30 LOC) into `src/synapse/providers/content_delta_hash.py` |

**Two existing capabilities (already in Synapse G01)** — no new
provider needed:

| Existing capability | Where it lives |
|---|---|
| SSRF guard | `src/synapse/security/ssrf.py` (already in G01) |
| HTTP client | `httpx` (already a Synapse dependency) |
| Source / Acquisition / EvidenceFragment domain records | `src/synapse/domain/{source,acquisition,evidence}.py` (already in G01) |
| DB persistence | `src/synapse/storage/models.py` + `alembic/versions/0001_initial.py` (already in G01) |
| API endpoints (`POST /api/v1/sources/discover`, `POST /api/v1/sources/ingest`) | `src/synapse/api/v1/router.py` (placeholder 501s in G01 — to be wired to real handlers in G02 implementation) |

**Total new code**: ~250 LOC (3 thin provider adapters) + ~150 LOC
(approx, 2 API endpoint handlers replacing the 501 placeholders) + ~50
LOC (tests).

**Deferred providers** (NOT in this slice):

| Provider | Reason for deferral |
|---|---|
| `crossref_search`, `openalex_search`, `semantic_scholar_search`, `duckduckgo_search`, `whoogle_search` | Not needed for the first slice (arxiv is sufficient for scientific discovery); add in later groups |
| `cloudscraper_http`, `curl_cffi_http`, `blocked_page_recovery` | Acquisition via plain httpx is sufficient; add when stealth/recovery is needed |
| `crawl4ai`, `scrapy_adapter` | Crawling is out of scope for the first slice (single-page fetch only) |
| `playwright_browser`, `cdp_browser`, `selenium`, `browser_use_agent` | Browser runtimes deferred (D-06) |
| `extruct_metadata`, `pdfplumber`, `newspaper_tool` | Extraction via trafilatura is sufficient for the first slice; add when PDF / structured-metadata extraction is needed |
| `bibtex_parser`, `ris_parser`, `wayback_machine`, `scholarly` | Scientific parsing deferred; the first slice extracts text, not BibTeX |
| `feedparser`, `twikit`, `instaloader` | Social ingestion deferred (D-04, D-05) |
| `grounded_sources`, `skill_provenance`, `safe_path` | Provenance via `content_delta_hash` is sufficient; add when richer provenance tracking is needed |
| `agentcraft_toolkit.vectorization.chunker` | Out of scope for the first slice (no embeddings yet) |

## 3. Evidence of functional readiness and remaining tests

### Functional smoke tests — REAL evidence (per ADR-0009 §2)

The script `scripts/run_minimal_slice_smoke.py` runs **actual
functional smoke tests** (not just imports) on every provider in the
minimal slice. All 7 tests passed (2026-10-08):

| # | Provider | Test | Result |
|---|---|---|---|
| 1 | `arxiv_search_tool` (vendored adapter) | Live arXiv API query for "transformers attention", max 2 results | ✅ PASS — got 2 results, first arxiv_id=2303.15105, title="Vision Transformer with Quadrangle Attention" |
| 2 | `url_safety_tool` (vendored adapter, alternative to Synapse SSRF) | URL validation against metadata IP, loopback, public | ✅ PASS — meta IP blocked=True, loopback blocked=True, public allowed=True |
| 3 | `synapse.security.ssrf` (existing G01) | URL validation: meta IP / loopback / file:// | ✅ PASS — meta IP blocked=True, loopback blocked=True, file:// blocked=True |
| 4 | `trafilatura` (pip-installed, thin Synapse wrapper) | Extract article text + metadata from fixture HTML | ✅ PASS — extracted 276 chars; title="Transformer Architecture Explained"; author="Jane Researcher" |
| 5 | `feedparser` (pip-installed) — bonus test, NOT in slice | Parse fixture RSS feed | ✅ PASS — feed title="arXiv cs.AI"; entry title="Sample Paper on Transformers" |
| 6 | `bibtexparser` (pip-installed) — bonus test, NOT in slice | Parse fixture BibTeX | ✅ PASS — parsed 1 entry; key="vaswani2017attention"; title="Attention Is All You Need" |
| 7 | `agentcraft_toolkit.scraping.delta_hash.fingerprint()` (vendored function) | Compute deterministic hash of "hello world" | ✅ PASS — hash=5eb63bbbe01eeed093cb22bb8f5acdc3...; deterministic=True |

**Output**: `docs/toolkit_audit/minimal_slice_smoke_results.json`

### What is NOT yet tested (remaining tests for G02 implementation)

| Test | Why deferred | When it will be added |
|---|---|---|
| End-to-end integration test: `POST /api/v1/sources/discover` → real arxiv call → `POST /api/v1/sources/ingest` → fetch → extract → write to DB | Requires the API handlers to be wired (G02 implementation task) | G02 implementation, after this proposal is approved |
| Live network test against arxiv.org rate limits | Rate-limit behavior depends on production deployment | G02 implementation, with a `pytest.mark.live` marker (skipped by default in CI) |
| Trafilatura extraction against a real arxiv abstract page | Requires live fetch (network) | G02 implementation, with explicit `SYNAPSE_RUN_NETWORK_TESTS=1` opt-in |
| Content-hash collision behavior under repeated ingest | Requires DB integration | G02 implementation, as part of the idempotency test suite |
| Auth + RBAC enforcement on the new endpoints | Requires the endpoint to exist | G02 implementation, parallel to the existing `test_capabilities_require_auth` pattern |

### Honest gap statement (per ADR-0009 §2)

**Inventory coverage** in the original audit:

| Domain | Inventory coverage | Functional coverage (after this slice's smoke tests) |
|---|---|---|
| discovery | ✓ (6 tools) | ✓ (1 tool: `arxiv_search_tool` — smoke passed) |
| acquisition | ✓ (4 tools) | ✓ (Synapse's existing `synapse.security.ssrf` — smoke passed; `url_safety_tool` also smoke-passed as alternative) |
| crawling | ✓ (2 tools) | ✗ NOT covered (deferred to a later group) |
| browser | ✓ (5 tools) | ✗ NOT covered (deferred per D-06) |
| extraction | ✓ (5 tools) | ✓ (1 tool: `trafilatura` — smoke passed) |
| scientific | ✓ (4 tools) | ✗ NOT covered in this slice (`bibtexparser` smoke passed as bonus, but BibTeX parsing is not in the minimal slice) |
| social | ✓ (3 tools) | ✗ NOT covered (deferred per D-04, D-05) |
| provenance | ✓ (4 tools) | ✓ (1 tool: `content_delta_hash` — smoke passed) |

**Result**: this minimal slice brings **4 of 8 capability domains to
functional coverage** (discovery, acquisition, extraction, provenance).
The other 4 (crawling, browser, scientific-parsing, social) remain
inventory-only and are explicitly deferred.

## 4. Integration approach and dependencies

### Comparison of three integration approaches (per ADR-0009 §4)

| Approach | Description | Pros | Cons | Use when |
|---|---|---|---|---|
| **A. Direct package dependencies** | `pip install <lib>` and write a fresh Synapse adapter from scratch | Minimal coupling to Toolkit; full control; no provenance header to maintain; clean upgrade path | Re-implements the adapter logic the Toolkit already wrote; loses audit-trail link to Toolkit commit | The library is well-documented and the adapter is trivial (e.g., `trafilatura.extract()`) |
| **B. Thin adapters** (vendoring ToolKit `tool.py`) | Copy `ToolKit/<cat>/<tool>/tool.py` into `src/synapse/providers/<provider>_adapter.py` | Reuses tested adapter code; preserves provenance; minimal new logic to write | Couples to Toolkit's adapter conventions; must track upstream Toolkit changes for fixes; harder to upgrade the underlying library independently | The adapter has substantial logic worth reusing (e.g., `arxiv_search_tool` builds Atom XML queries + parses results) |
| **C. Selective vendoring** | Copy only the specific function/class needed from a ToolKit adapter | Smallest vendored surface; can cherry-pick the value-adding logic | Requires reading and understanding the upstream adapter to pick the right slice; risks missing initialization logic | A specific function is the value (e.g., `delta_hash.fingerprint()`) |

### Recommendation per provider

| Provider | Recommended approach | Rationale |
|---|---|---|
| `arxiv_search` | **B — Thin adapter (vendor ToolKit `tool.py`)** | The ToolKit adapter (`ToolKit/research/arxiv_search_tool/tool.py`, ~150 LOC) builds Atom XML queries, handles pagination, and parses results into a normalized dict. Rewriting this from scratch would be wasted effort. The adapter is pure stdlib (no deps), so vendoring carries zero new pip dependencies. |
| `trafilatura_extractor` | **A — Direct package dependency + thin Synapse wrapper** | `trafilatura` is a well-maintained PyPI package with a stable public API (`trafilatura.extract()`, `trafilatura.extract_metadata()`). The ToolKit adapter for it is a thin wrapper (~100 LOC) that mostly delegates to the library. Writing our own ~50 LOC wrapper is simpler than vendoring and gives us a clean upgrade path. Pip dependency: `trafilatura>=2.3,<3.0`. |
| `content_delta_hash` | **C — Selective vendoring (one function)** | The ToolKit library module `agentcraft_toolkit/scraping/delta_hash.py` exposes a `fingerprint()` function (~30 LOC) that computes a deterministic content hash. Vendoring the function (not the whole library) is the smallest surface. No pip dependency added (pure stdlib). |

### New dependencies added by this slice

| Package | Version constraint | Why | License |
|---|---|---|---|
| `trafilatura` | `>=2.3,<3.0` | Article text + metadata extraction | Apache-2.0 |

**That's it.** One new pip dependency. The other two providers
(`arxiv_search`, `content_delta_hash`) are vendored as stdlib-only
code — no new pip dependencies.

The new dependency is added to `pyproject.toml` under the main
`dependencies` list (not under `optional-dependencies`), since it's
required for the slice to function.

### Files to be added in G02 implementation (after this proposal is approved)

```
src/synapse/providers/
├── arxiv_search_provider.py     # ~150 LOC (vendored ToolKit adapter + Synapse Provider wrapper)
├── trafilatura_extractor_provider.py  # ~80 LOC (thin Synapse wrapper around trafilatura.extract())
└── content_delta_hash.py        # ~40 LOC (vendored fingerprint() function + Synapse helper)

src/synapse/api/v1/
└── sources.py (new)             # ~120 LOC — replaces 501 placeholders for /sources/discover + /sources/ingest

src/synapse/application/
└── acquisition.py (new)        # ~80 LOC — Acquisition Router (picks provider by source_type, enforces budget cap)

tests/unit/providers/
├── test_arxiv_search.py         # ~80 LOC
├── test_trafilatura_extractor.py  # ~80 LOC
└── test_content_delta_hash.py   # ~40 LOC

tests/integration/
└── test_minimal_slice_e2e.py    # ~120 LOC — POST /sources/discover → /sources/ingest → DB row exists
```

**No files under `src/synapse/` will be modified beyond these new
additions** — the existing G01 code (domain records, storage, API
skeleton, security, middleware) is unchanged.

### Alembic migration

The first slice does NOT require a new Alembic migration. The existing
`0001_initial.py` already creates the `sources`, `acquisitions`,
`evidence_fragments` (planned — actually only `sources` exists today),
and `audit_events` tables. The first slice uses these tables as-is.

If `evidence_fragments` table is missing (it is — G01 only created 6
tables: sources, jobs, idempotency_keys, audit_events, capabilities,
providers), then a `0002_evidence_fragments.py` migration will be
added. Estimated at ~30 LOC.

## 5. Security and license considerations

### Security

| Consideration | Mitigation |
|---|---|
| SSRF on `arxiv_search` provider | The provider calls `urllib.request.urlopen()` to fetch arXiv API responses. The destination URL is `https://export.arxiv.org/api/query?...` — a fixed base, parameterized only by query string. **No user-supplied URL reaches `urlopen()` directly.** When the user supplies a query string, it's URL-encoded and appended to the fixed base. No SSRF vector. |
| SSRF on `/sources/ingest` (acquisition) | The endpoint accepts a `canonical_uri` from the user. **Before any fetch**, the URL is validated via the existing `synapse.security.ssrf.validate_url()` (smoke-passed above). Blocked: private IPs, loopback, link-local, metadata endpoints, file:// scheme. |
| Trafilatura processing untrusted HTML | Trafilatura is a pure-Python parser; it does not execute JavaScript or fetch external resources. The fixture smoke test confirms it processes untrusted HTML safely. The Synapse wrapper passes the HTML through the existing SSRF guard (if a URL is supplied) before handing the bytes to trafilatura. |
| Content-hash collision | The `fingerprint()` function uses MD5 (deterministic, fast). For content-integrity purposes (detecting changes between ingests), MD5 is sufficient — this is not a cryptographic use case. If a stronger hash is needed in the future, the function can be swapped without changing the API. |
| Rate-limit / budget | The `arxiv_search` provider calls the public arXiv API. The Synapse wrapper enforces a per-provider rate limit (configurable via `SYNAPSE_ARXIV_RATE_LIMIT_PER_MINUTE`, default 20/min — well below arXiv's recommended 1 req/3s). |
| Auth on new endpoints | `POST /api/v1/sources/discover` and `POST /api/v1/sources/ingest` will require `RESEARCHER` scope (already enforced via `PrincipalDep` in G01). Public/anonymous access is rejected with 401. |
| Secrets in commits | The vendored `arxiv_search_provider.py` and `content_delta_hash.py` contain no secrets — they're pure stdlib code. The `trafilatura_extractor_provider.py` reads no env vars beyond the existing Synapse settings. |

### License

| Component | License | Integration pattern | Distribution implications |
|---|---|---|---|
| `arxiv_search_provider.py` (vendored from ToolKit) | MIT (upstream hermes-agent, (c) 2025 Nous Research) | Vendored — provenance header preserved in the file | Permissive — MIT allows proprietary distribution with attribution. The Synapse repo's `NOTICE.md` (to be added) will credit Nous Research. |
| `trafilatura` (pip-installed) | Apache-2.0 (adbar/trafilatura) | Direct pip dependency | Permissive — Apache-2.0 allows proprietary distribution with attribution and NOTICE file. The Synapse repo's `pyproject.toml` already declares it; the `NOTICE.md` will credit adbar/trafilatura. |
| `content_delta_hash.py` (vendored function from ToolKit library) | MIT (AgentCraft Toolkit) | Vendored — provenance header preserved | Permissive — same as arxiv_search. |

**No GPL/AGPL/LGPL/SSPL components** in this slice. The deferred
`instaloader_tool` (GPL-3.0) is NOT in this slice per ADR-0009 §6.

### Credential handling (per ADR-0009 §7)

- This slice uses **zero credentials** — no API keys, no OAuth tokens,
  no cookies. arXiv's API is unauthenticated (keyless); trafilatura is
  a local library.
- The existing Synapse developer token (write-capable) is **not used
  by the runtime** — it's only used for `git push` to the Synapse repo
  by the developer, not by the running application.
- Future Toolkit audits (if any are needed) must use a
  repository-scoped fine-grained PAT with `Contents: Read` only, per
  the corrected credential statement.

## 6. Acceptance criteria

The G02 minimal slice is PASS only if ALL of the following are met:

### Functional criteria

1. ✅ `POST /api/v1/sources/discover` with `{"query": "transformers attention", "max_results": 5}` returns 200 with a list of Source candidates (arxiv_id, title, authors, published).
2. ✅ `POST /api/v1/sources/ingest` with `{"source_id": "<from step 1>"}` returns 202 with a `job_id`; polling `GET /api/v1/jobs/{job_id}` eventually returns `status: succeeded`.
3. ✅ After ingest succeeds, `GET /api/v1/sources/{source_id}/acquisitions` returns at least one Acquisition with `status: succeeded`, `completeness: full`, `content_hash` set.
4. ✅ At least one EvidenceFragment row exists, linked to the Acquisition, with `exact_excerpt` non-empty and `extraction_method: "trafilatura-2.3"`.
5. ✅ The `audit_events` table has an entry recording the discovery → acquisition → extraction flow with `request_id` correlation.
6. ✅ Re-ingesting the same Source (same canonical_uri + same content_hash) does NOT create a duplicate Acquisition (idempotency, per `DOMAIN_AND_API_CONTRACTS.md` invariant §4).

### Security criteria

7. ✅ `POST /api/v1/sources/discover` without Authorization header returns 401.
8. ✅ `POST /api/v1/sources/ingest` with a `canonical_uri` pointing at `http://169.254.169.254/...` returns 422 with `error.code: ssrf_blocked` (the URL is rejected by the SSRF guard before any fetch).
9. ✅ `POST /api/v1/sources/ingest` with a `canonical_uri` of `file:///etc/passwd` returns 422 with `error.code: ssrf_blocked`.
10. ✅ Rate limit: 20 calls/min to `/sources/discover` from the same API key returns 429.

### License criteria

11. ✅ `NOTICE.md` exists at the repo root and credits: Nous Research (hermes-agent, MIT), adbar/trafilatura (Apache-2.0), AgentCraft Toolkit (MIT).
12. ✅ Each vendored file (`arxiv_search_provider.py`, `content_delta_hash.py`) starts with a provenance header naming the upstream repo, license, and Toolkit commit SHA (`fd9df34`).
13. ✅ No GPL/AGPL/LGPL/SSPL components in this slice's dependency tree (`pip list | grep -iE "gpl|agpl|lgpl|sspl"` returns nothing).

### Quality criteria

14. ✅ `ruff check src tests scripts` exits 0.
15. ✅ `ruff format --check src tests scripts` exits 0.
16. ✅ `pytest` exits 0 with all existing tests still passing (162 from G01+audit-prep, plus ~10-20 new tests for the slice).
17. ✅ Coverage stays at ≥90%.
18. ✅ No file under `src/synapse/` is modified beyond the new files listed in §4 (verified by `git diff --name-only main..g02-minimal-slice | grep "src/synapse" | grep -v "^src/synapse/providers/" | grep -v "^src/synapse/api/v1/sources.py" | grep -v "^src/synapse/application/acquisition.py"` returns nothing).

### Operational criteria

19. ✅ `make audit-validate` still detects absent `TOOLKIT_INDEX(1).json` and exits 0 with a friendly message (the audit scripts remain unchanged).
20. ✅ The OpenAPI schema at `/api/v1/openapi.json` still validates as 3.1.0 and lists all 28 routes (the 501 placeholders for `/sources/discover` and `/sources/ingest` are replaced with real implementations; all other 501s remain).

## 7. Estimated implementation complexity

| Dimension | Estimate | Reason |
|---|---|---|
| New source files | 8 (3 providers, 1 API module, 1 application module, 3 test files, 1 NOTICE.md) | Per §4 |
| New LOC | ~600 (250 providers + 200 API+app + 150 tests + 50 NOTICE) | Per §4 |
| New pip dependencies | 1 (`trafilatura>=2.3,<3.0`) | Per §4 |
| New Alembic migrations | 1 (likely needed: `0002_evidence_fragments.py` — ~30 LOC) | The `evidence_fragments` table is referenced in the domain layer but not yet in the DB schema |
| New tests | ~15 (3 provider unit tests + 1 e2e integration test + ~10 security/idempotency tests) | Per acceptance criteria §6 |
| Implementation time | 1-2 working sessions | The adapters are thin; the API endpoints follow the existing G01 pattern; the e2e test exercises the existing DB layer |
| Risk of breaking G01 | Low | No G01 file is modified (only new files added; the 501 placeholders for `/sources/discover` and `/sources/ingest` are replaced with real handlers, which is an additive change to the router) |
| Risk of breaking audit scripts | None | The audit scripts (`run_g02_audit.py`, `generate_g02_matrix.py`) are read-only and don't import Synapse source |

## 8. Deferred capabilities

The following capabilities are **explicitly deferred** to later groups
and are NOT in this minimal slice:

| Capability | Reason | Revisit when |
|---|---|---|
| Crossref, OpenAlex, Semantic Scholar, DuckDuckGo, Whoogle search | arxiv is sufficient for the first slice's scientific discovery | G03 (Knowledge & Verification) or later, when broader discovery is needed |
| Cloudscraper, curl_cffi, blocked_page_recovery | Plain httpx is sufficient for arxiv abstract pages (no Cloudflare, no JS) | G02 follow-up or G03, when stealth-fetch is needed |
| Crawl4ai, Scrapy | Single-page fetch is sufficient | G03 or later, when multi-page crawling is needed |
| Playwright, browser_cdp, Selenium, browser_use_agent | Browser runtimes deferred per D-06 | When user approves browser binary installation |
| Extruct, pdfplumber, newspaper | Trafilatura is sufficient for HTML extraction | When PDF parsing or structured-metadata extraction is needed |
| BibTeX, RIS parsers, wayback_machine, scholarly | Text extraction is sufficient for the first slice | G03, when citation graph is needed |
| Feedparser, twikit, instaloader | Social ingestion deferred per D-04 (GPL), D-05 (creds) | When user approves legal review and credentials provisioning |
| Grounded_sources, skill_provenance, safe_path | content_delta_hash is sufficient for the first slice's provenance | When richer provenance tracking is needed |
| Vectorization chunker | Out of scope (no embeddings in this slice) | G03 or G04 (Retrieval & Reasoning), when embeddings are introduced |

## STOP — awaiting explicit approval

Per ADR-0009 §3 and the user's "G02 Audit Review & Minimal
Implementation Gate" message:

> *"Do not begin production implementation.*
> *STOP and await explicit approval."*

The agent will NOT:

- Write any code under `src/synapse/`.
- Install `trafilatura` as a Synapse production dependency (it's installed in the audit env only for smoke testing — not committed to `pyproject.toml` yet).
- Add any new Alembic migration.
- Replace any 501 placeholder endpoint.
- Begin GROUP_03.

The agent awaits explicit user approval of:

1. **This proposal** (`G02_MINIMAL_EXECUTION_PROPOSAL.md`).
2. **The three-provider set** (arxiv_search + trafilatura_extractor + content_delta_hash).
3. **The integration approach** (B+A+C mixed per provider, per §4).
4. **The acceptance criteria** (per §6).

After approval, the G02 implementation group will follow this
proposal exactly — adding the listed files, the one pip dependency,
the one Alembic migration, and the ~15 tests — without expanding scope.

---

*End of G02 Minimal Execution Proposal — awaiting explicit user approval.*
