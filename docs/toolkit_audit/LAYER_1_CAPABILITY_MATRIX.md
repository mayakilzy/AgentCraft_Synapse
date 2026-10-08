# Layer-1 Capability Matrix (auto-generated)

- Generated: 2026-10-08T15:55:00Z
- Schema version: 2.0
- Source: https://github.com/mayakilzy/AgentCraft-Toolkit.git @ fd9df34c51781bd12effab62762022ab04dbd771

## Summary

| Metric | Count |
|--------|-------|
| Total shortlisted tools | 32 |
| Package available (Stage 1) | 32 |
| Import succeeded (Stage 2) | 32 |
| Functional smoke passed (Stage 3) | 2 |
| Production ready (Stage 4 — manual) | _pending user approval_ |
| Flagged for legal review | 1 (instaloader — GPL-3.0) |
| Deferred | 6 (need credentials/runtime) |
| Capability gaps | 0 (all 8 domains covered) |

## Matrix — shortlisted candidates for Synapse

| Tool | Domain | Provider name | pkg? | import? | smoke? | prod? | License | Lic review? | Provenance | Notes |
|------|--------|---------------|------|---------|--------|-------|---------|-----------|-----------|-------|
| arxiv_search_tool | discovery | arxiv_search | ✓ | ✓ | ✓ | — | MIT (arXiv API ToS) | no | High | Pure stdlib; no API key needed |
| duckduckgo_search_tool | discovery | duckduckgo_search | ✓ | ✓ | skip | — | MIT | no | Medium | `duckduckgo_search` package |
| habanero_tool | discovery | crossref_search | ✓ | ✓ | skip | — | MIT | no | High | Crossref REST API |
| pyalex_tool | discovery | openalex_search | ✓ | ✓ | skip | — | MIT | no | High | OpenAlex (keyless) |
| semanticscholar_tool | discovery | semantic_scholar_search | ✓ | ✓ | skip | — | MIT | no | High | Semantic Scholar API |
| whoogle_search_tool | discovery | whoogle_search | ✓ | ✓ | skip:runtime | — | MIT | no | Medium | Self-hosted Whoogle required |
| url_safety_tool | acquisition | url_safety | ✓ | ✓ | ✓ | — | MIT | no | High | SSRF guard, blocks 169.254.169.254 |
| cloudscraper_client_tool | acquisition | cloudscraper_http | ✓ | ✓ | skip:creds | — | MIT | no | Medium | Cloudflare bypass; ToS review needed |
| curl_cffi_client_tool | acquisition | curl_cffi_http | ✓ | ✓ | skip:runtime | — | MIT | no | Medium | Native TLS impersonation |
| blocked_page_recovery_tool | acquisition | blocked_page_recovery | ✓ | ✓ | skip | — | MIT | no | Medium | Recovery ladder |
| crawl4ai_crawler_tool | crawling | crawl4ai | ✓ | ✓ | skip:runtime | — | Apache-2.0 | no | High | Requires Playwright binaries |
| scrapy_adapter_tool | crawling | scrapy | ✓ | ✓ | skip:safety | — | BSD-3-Clause | no | High | Full project setup needed |
| browser_cdp_tool | browser | cdp_browser | ✓ | ✓ | skip:runtime | — | MIT | no | High | Chrome --remote-debugging-port |
| playwright_browser_tool | browser | playwright_browser | ✓ | ✓ | skip:runtime | — | Apache-2.0 | no | High | Best browser automation choice |
| selenium_tool | browser | selenium_browser | ✓ | ✓ | skip:runtime | — | Apache-2.0 | no | Medium | WebDriver binary needed |
| browser_use_agent_tool | browser | browser_use_agent | ✓ | ✓ | skip:runtime | — | MIT | no | Medium | AI browser agent |
| trafilatura_extractor_tool | extraction | trafilatura_extractor | ✓ | ✓ | skip:runtime | — | Apache-2.0 | no | High | Article extraction + metadata |
| extruct_metadata_tool | extraction | extruct_metadata | ✓ | ✓ | skip | — | BSD-3-Clause | no | High | JSON-LD, OpenGraph, microdata |
| pdfplumber_tool | extraction | pdfplumber | ✓ | ✓ | skip | — | MIT | no | High | PDF text + tables |
| newspaper_tool | extraction | newspaper3k | ✓ | ✓ | skip | — | MIT | no | Medium | Article extraction (alternative) |
| bibtexparser_tool | scientific | bibtex_parser | ✓ | ✓ | skip | — | MIT | no | High | BibTeX citations |
| rispy_tool | scientific | ris_parser | ✓ | ✓ | skip | — | BSD-3-Clause | no | High | RIS citations |
| scholarly_tool | scientific | google_scholar | ✓ | ✓ | skip:creds | — | Unlicense | no | Medium | ToS risk; needs proxy |
| waybackpy_archive_tool | scientific | wayback_machine | ✓ | ✓ | skip:runtime | — | MIT | no | High | Internet Archive |
| feedparser_tool | social | feedparser | ✓ | ✓ | skip:runtime | — | BSD-2-Clause | no | High | RSS/Atom |
| twikit_tool | social | twikit | ✓ | ✓ | skip:creds | — | MIT | no | Medium | X/Twitter; ToS review |
| instaloader_tool | social | instaloader | ✓ | ✓ | skip:license | — | GPL-3.0 | **YES** | Medium | Flagged for legal review |
| skill_provenance_tool | provenance | skill_provenance | ✓ | ✓ | skip | — | MIT | no | High | Provenance tracking |
| grounded_sources_tool | provenance | grounded_sources | ✓ | ✓ | skip | — | MIT | no | High | Citation persistence |
| agentcraft_toolkit.scraping.delta_hash | provenance | content_delta_hash | ✓ | ✓ | skip:runtime | — | MIT | no | High | Content integrity hash |
| agentcraft_toolkit.security.safe_path | provenance | safe_path | ✓ | ✓ | skip | — | MIT | no | High | Path traversal guard |
| agentcraft_toolkit.vectorization.chunker | extraction | text_chunker | ✓ | ✓ | skip | — | MIT | no | Medium | Text chunking for embeddings |

## Smoke test results (Stage 3)

### Passing functional smoke tests (2/32)

| Tool | Test | Result |
|------|------|--------|
| `arxiv_search_tool` | Real arXiv API query for "transformers attention", max 2 results | ✓ PASS — got 2 results, first title "Vision Transformer with Quadrangle Attention" |
| `url_safety_tool` | URL validation against metadata IP / loopback / public | ✓ PASS — meta IP blocked=True, loopback blocked=True, public allowed=True |

### Skipped with documented reason (30/32)

| Reason | Count | Examples |
|--------|-------|----------|
| `runtime_not_configured` | 11 | playwright, crawl4ai, browser_cdp (browser binaries); trafilatura, waybackpy (pip packages) |
| `no_safe_smoke_test_defined` | 13 | extruct, pdfplumber, bibtexparser (offline-safe but fixture not authored in audit phase) |
| `credentials_unavailable` | 4 | twikit, scholarly, cloudscraper, scholarly |
| `side_effects_unsafe` | 1 | scrapy (requires full project) |
| `license_review_required` | 1 | instaloader (GPL-3.0 — flagged per ADR-0008 §3) |

## Flagged for legal review

Per ADR-0008 §3, the following tool has a license in the review-required list.
**It is NOT auto-rejected** — flagged for compatibility and legal review.

| Tool | License | Review reason | Integration implications | Distribution implications |
|------|---------|----------------|--------------------------|---------------------------|
| `instaloader_tool` | GPL-3.0 | GPL — copyleft; static/dynamic linking may trigger source-disclosure obligations | Linking instaloader into Synapse (a proprietary codebase) may require releasing Synapse source under a GPL-compatible license. **Safer integration pattern**: spawn instaloader as a subprocess (communicate via stdin/stdout/JSON), avoiding derivative-work status. | Shipping instaloader as part of a Synapse distribution would require GPL-3.0 attribution + source-disclosure. Server-side-only (SaaS) deployment of Synapse without distribution may avoid some obligations, but AGPL-style network-use triggers do NOT apply to GPL-3.0. |

**Recommended action**: defer `instaloader_tool` adoption until user provides explicit legal approval. Use `feedparser_tool` + `twikit_tool` (with ToS review) for social ingestion instead.

## Capability gaps

| Domain | Reason | Recommended remediation |
|--------|--------|--------------------------|
| (none) | All 8 capability domains have at least one tool with stage 2 (import succeeded). | No action needed. |

**Domain coverage**:

| Domain | Tools indexed | Imports passed | Smoke passed | Gap? |
|--------|---------------|-----------------|---------------|------|
| discovery | 6 | 6 | 1 | No |
| acquisition | 4 | 4 | 1 | No |
| crawling | 2 | 2 | 0 | Acceptable (runtimes deferred) |
| browser | 5 | 5 | 0 | Acceptable (runtimes deferred) |
| extraction | 5 | 5 | 0 | Acceptable (runtimes deferred) |
| scientific | 4 | 4 | 0 | Acceptable (offline-safe but fixtures deferred) |
| social | 3 | 3 | 0 | Acceptable (creds + ToS + license deferred) |
| provenance | 4 | 4 | 0 | Acceptable (offline-safe but fixtures deferred) |

## STOP

This matrix is auto-generated evidence for the G02 audit. Promotion of any
tool to `adopted` requires explicit user approval via
`PROVIDER_SELECTION.md`. License-flagged tools require separate legal
review per ADR-0008 §3.
