# Provider Selection Proposal — G02 Audit

> Per ADR-0008 §4: prefer existing, sufficiently tested capabilities over
> newly written equivalents. Per Master Spec §6: minimal reuse.
>
> **This document is a proposal — adoption requires explicit user approval.**

## Selection principles applied

1. **Minimal reuse** — at most one provider per capability domain in the initial set.
2. **No vendor lock-in** — provider adapters wrap tools behind Synapse's `Provider` protocol.
3. **License-clean** — GPL/AGPL flagged for legal review (NOT auto-adopted).
4. **Compliant** — no scrapers that violate website ToS without explicit review.
5. **Bounded cost** — every external call will have a configured cost cap.
6. **Provenance-first** — prefer tools that preserve source structure, metadata, links, citations, version.
7. **Existing > new** — if an AgentCraft-Toolkit adapter exists and passes Stage 2 (import), prefer it over a fresh pip install of the underlying library.

## Initial Provider Set — RECOMMENDED for G02 implementation

### High-priority providers (license-clean, Stage 2 passed, low operational risk)

| Provider name | Synapse domain | Tool | Toolkit commit | License | Smoke | Reason |
|---------------|---------------|------|-----------------|---------|-------|--------|
| `arxiv_search` | discovery | `arxiv_search_tool` | `fd9df34` | MIT (arXiv API ToS) | ✓ PASS | Pure stdlib; verified live; keyless; preserves arxiv_id + version + authors + categories |
| `crossref_search` | discovery | `habanero_tool` | `fd9df34` | MIT | skip (no fixture) | Crossref REST API; DOI metadata; keyless |
| `openalex_search` | discovery | `pyalex_tool` | `fd9df34` | MIT | skip (no fixture) | OpenAlex graph; keyless; high provenance |
| `semantic_scholar_search` | discovery | `semanticscholar_tool` | `fd9df34` | MIT | skip (no fixture) | Semantic Scholar API; citations; keyless |
| `url_safety` | acquisition | `url_safety_tool` | `fd9df34` | MIT | ✓ PASS | SSRF guard; blocks metadata IP / loopback; complement to Synapse's existing `synapse.security.ssrf` |
| `trafilatura_extractor` | extraction | `trafilatura_extractor_tool` | `fd9df34` | Apache-2.0 | skip (runtime) | Article + metadata extraction; preserves structure |
| `extruct_metadata` | extraction | `extruct_metadata_tool` | `fd9df34` | BSD-3-Clause | skip (no fixture) | JSON-LD / OpenGraph / microdata extraction |
| `pdfplumber` | extraction | `pdfplumber_tool` | `fd9df34` | MIT | skip (no fixture) | PDF text + tables + per-page coords |
| `feedparser` | social | `feedparser_tool` | `fd9df34` | BSD-2-Clause | skip (runtime) | RSS/Atom; preserves author/published/tags |
| `bibtex_parser` | scientific | `bibtexparser_tool` | `fd9df34` | MIT | skip (no fixture) | BibTeX citation parsing |
| `ris_parser` | scientific | `rispy_tool` | `fd9df34` | BSD-3-Clause | skip (no fixture) | RIS citation parsing |
| `wayback_machine` | scientific | `waybackpy_archive_tool` | `fd9df34` | MIT | skip (runtime) | Internet Archive lookups |
| `grounded_sources` | provenance | `grounded_sources_tool` | `fd9df34` | MIT | skip (no fixture) | Citation persistence; persistent JSON log |
| `content_delta_hash` | provenance | `agentcraft_toolkit.scraping.delta_hash` | `fd9df34` | MIT | skip (runtime) | Content integrity hash (deterministic) |
| `safe_path` | provenance | `agentcraft_toolkit.security.safe_path` | `fd9df34` | MIT | skip (no fixture) | Path traversal guard |

### Medium-priority providers (need runtime/credentials approval)

| Provider name | Synapse domain | Tool | Toolkit commit | License | Smoke | Reason | Blocker |
|---------------|---------------|------|-----------------|---------|-------|--------|--------|
| `playwright_browser` | browser | `playwright_browser_tool` | `fd9df34` | Apache-2.0 | skip (runtime) | Best-in-class headless browser | D-06 (browser binaries) |
| `crawl4ai` | crawling | `crawl4ai_crawler_tool` | `fd9df34` | Apache-2.0 | skip (runtime) | Multi-page crawl w/ extraction | D-06 (browser binaries) |
| `cdp_browser` | browser | `browser_cdp_tool` | `fd9df34` | MIT | skip (runtime) | Direct Chrome DevTools Protocol | D-06 (Chrome instance) |
| `duckduckgo_search` | discovery | `duckduckgo_search_tool` | `fd9df34` | MIT | skip (no fixture) | Free text-web-search | None — high priority |
| `cloudscraper_http` | acquisition | `cloudscraper_client_tool` | `fd9df34` | MIT | skip (creds) | Cloudflare-bypass fetch | D-05 (credentials + ToS review) |
| `twikit` | social | `twikit_tool` | `fd9df34` | MIT | skip (creds) | X/Twitter compliant ingestion | D-05 (cookies + ToS review) |
| `scholarly` | scientific | `scholarly_tool` | `fd9df34` | Unlicense | skip (creds) | Google Scholar | D-05 (proxy/captcha service) |

### Deferred candidates

| Tool | Reason | Revisit when |
|------|--------|--------------|
| `whoogle_search_tool` | Self-hosted Whoogle instance required | When user provisions Whoogle deployment |
| `scrapy_adapter_tool` | Requires full Scrapy project setup | When Synapse needs high-throughput multi-domain crawling |
| `selenium_tool` | WebDriver binary required; superseded by `playwright_browser` | Unlikely — Playwright is preferred |
| `browser_use_agent_tool` | Requires Playwright + LLM API key | When Synapse needs AI-driven browser interaction |
| `curl_cffi_client_tool` | Native TLS impersonation binary | When stealth-fetch is needed (Cloudflare bypass) |
| `newspaper_tool` | Overlaps with `trafilatura_extractor` | If trafilatura proves insufficient |
| `blocked_page_recovery_tool` | Useful but niche | When Synapse implements retry/recovery ladder |
| `maps_client_tool` | Geo data; out of Synapse's core scope | Not in initial roadmap |
| `instaloader_tool` | **GPL-3.0 — flagged for legal review (D-04)** | When user approves subprocess-isolation integration |

### Rejected candidates

| Tool | Reason |
|------|--------|
| (none) | No tools rejected outright — all license issues flagged for review, not auto-rejected per ADR-0008 §3 |

## Required user decisions before implementation

| ID | Decision | Blocking providers |
|----|----------|-------------------|
| **D-04** | Approve `instaloader_tool` (GPL-3.0) legal review OR confirm subprocess-isolation integration | `instaloader` |
| **D-05** | Approve credentials provisioning for authenticated providers | `twikit`, `scholarly`, `cloudscraper_http`, LLM providers |
| **D-06** | Approve runtime binaries installation | `playwright_browser`, `crawl4ai`, `cdp_browser` |
| **D-07** | Confirm this provider set is sufficient, or request additions | All |

## Cost & rate-limit considerations

| Provider | Cost | Rate limit |
|----------|------|-----------|
| `arxiv_search` | Free | arXiv: 1 request / 3s recommended |
| `crossref_search` | Free | Crossref: 50 req/sec polite pool (mailto:) |
| `openalex_search` | Free | OpenAlex: 100k req/day, 10 req/sec |
| `semantic_scholar_search` | Free (with optional API key) | 100 req/5min unauth; 1 req/sec with key |
| `duckduckgo_search` | Free | DDG anti-scraping may throttle; back off |
| `trafilatura_extractor` | Free (local CPU) | None |
| `extruct_metadata` | Free (local CPU) | None |
| `pdfplumber` | Free (local CPU) | None |
| `feedparser` | Free (per feed host) | Per-feed ToS |
| `bibtex_parser`, `ris_parser` | Free (local CPU) | None |
| `wayback_machine` | Free | Internet Archive: 15 req/min |
| `playwright_browser` | Free (local CPU) | None |
| `crawl4ai` | Free (local CPU) | Per-target ToS |
| `cloudscraper_http` | Free | Cloudflare may block |
| `twikit` | Free (cookie-based) | X ToS — review required |
| `scholarly` | Free | Google blocks scrapers; needs proxy |

## Fallback providers

| Primary | Fallback |
|---------|----------|
| `arxiv_search` (network) | Catch network failure → return empty results with error_code |
| `crossref_search` | If habanero fails → fall back to direct Crossref REST via `httpx` |
| `playwright_browser` | If runtime not configured → fall back to `httpx` fetch (no JS rendering) |
| `trafilatura_extractor` | If trafilatura missing → fall back to `BeautifulSoup` text extraction |
| `twikit` | If ToS not approved → social ingestion limited to RSS feeds via `feedparser` |

## STOP statement

**This proposal is part of G02 Audit Preparation (per ADR-0008 §5).** The
agent will NOT register any provider adapter or write any provider
implementation code until the user explicitly approves this proposal.

After approval, the G02 implementation group will:
1. Wrap each adopted tool in a thin Synapse `Provider` adapter (under `src/synapse/providers/`).
2. Add budget caps + rate limits per the cost table above.
3. Wire SSRF guard around every fetch-capable provider (using the existing `synapse.security.ssrf` module).
4. Add provenance preservation per the matrix's `provenance_preservation` assessment.
