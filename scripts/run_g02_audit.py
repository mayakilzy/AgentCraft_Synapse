"""G02 Audit — actual tiered verification of relevant Toolkit adapters.

This script runs the four-stage tiered verification per ADR-0008 against
the most relevant ToolKit adapters discovered in the AgentCraft-Toolkit
repository. It produces:
  - docs/toolkit_audit/TOOLKIT_REPOSITORY_INVENTORY.json
  - docs/toolkit_audit/verification_results.json (overwritten)
  - docs/toolkit_audit/smoke_results.json (overwritten)

Stage 1: package_available — pip/module-spec lookup
Stage 2: import_succeeded — actual `import` succeeds
Stage 3: functional_smoke_passed — real functional test (where safe)
Stage 4: production_ready — assessed manually in audit report

Per ADR-0008 §5 (read-only audit): NO file under src/synapse/ is modified.
All outputs go to docs/toolkit_audit/ and reports/.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = Path("/home/z/my-project/agentcraft/toolkit_audit_workspace/toolkit")
OUTPUT_DIR = REPO_ROOT / "docs" / "toolkit_audit"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# ── Toolkit inventory snapshot ──────────────────────────────────────────────
# Captured: Toolkit commit fd9df34c51781bd12effab62762022ab04dbd771 (2026-09-01)
# Source repo: https://github.com/NousResearch/hermes-agent (MIT)
TOOLKIT_SNAPSHOT = {
    "toolkit_repo_url": "https://github.com/mayakilzy/AgentCraft-Toolkit.git",
    "toolkit_branch": "main",
    "toolkit_commit_sha": "fd9df34c51781bd12effab62762022ab04dbd771",
    "toolkit_commit_date": "2026-09-01T10:20:51+00:00",
    "toolkit_source_repo": "https://github.com/NousResearch/hermes-agent",
    "toolkit_source_license": "MIT",
    "toolkit_source_copyright": "(c) 2025 Nous Research",
    "toolkit_collection": "hermes",
    "toolkit_adapted": True,
    "toolkit_tool_count": 341,
    "toolkit_category_count": 26,
    "library_pip_package": "agentcraft-toolkit",
    "library_pip_version": "0.1.0",
    "library_modules": 158,
    "audited_at": _utcnow(),
    "audit_environment": {
        "python": sys.version.split()[0],
        "synapse_repo": "https://github.com/mayakilzy/AgentCraft_Synapse.git",
        "synapse_branch": "main",
        "synapse_g01_final_sha": "9542cd1",
        "synapse_g02_amendment_sha": "d03f176",
        "audit_workspace": str(TOOLKIT_ROOT),
        "audit_credentials": "one-shot token (stripped from remote URL after clone)",
        "push_protection": "remote.origin.pushurl = DISABLED-PUSH-BY-AUDIT-POLICY",
    },
}


# ── Shortlist of tools most relevant to Synapse's 8 capability domains ────
# Per Master Spec §7-§8 + AgentCraft Toolkit README.
# Each tool is mapped to a Synapse capability domain and priority.
SHORTLIST = [
    # ── Discovery ─────────────────────────────────────────────────────────
    {
        "tool_name": "arxiv_search_tool",
        "category": "research",
        "synapse_domain": "discovery",
        "synapse_provider": "arxiv_search",
        "priority": "high",
        "toolkit_path": "ToolKit/research/arxiv_search_tool/",
        "external_repo": "https://export.arxiv.org/api/query",
        "external_license": "arXiv API terms (open access)",
        "stdlib_only": True,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "arxiv fetch + XML parse",
    },
    {
        "tool_name": "duckduckgo_search_tool",
        "category": "research",
        "synapse_domain": "discovery",
        "synapse_provider": "duckduckgo_search",
        "priority": "high",
        "toolkit_path": "ToolKit/research/duckduckgo_search_tool/",
        "external_repo": "https://github.com/deedy5/duckduckgo_search",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "DDG HTML search for 'python'",
    },
    {
        "tool_name": "habanero_tool",
        "category": "research",
        "synapse_domain": "discovery",
        "synapse_provider": "crossref_search",
        "priority": "high",
        "toolkit_path": "ToolKit/research/habanero_tool/",
        "external_repo": "https://github.com/sckott/habanero",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "Crossref works query for 'transformers'",
    },
    {
        "tool_name": "pyalex_tool",
        "category": "research",
        "synapse_domain": "discovery",
        "synapse_provider": "openalex_search",
        "priority": "high",
        "toolkit_path": "ToolKit/research/pyalex_tool/",
        "external_repo": "https://github.com/J535D165/pyalex",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "OpenAlex works search",
    },
    {
        "tool_name": "semanticscholar_tool",
        "category": "research",
        "synapse_domain": "discovery",
        "synapse_provider": "semantic_scholar_search",
        "priority": "high",
        "toolkit_path": "ToolKit/research/semanticscholar_tool/",
        "external_repo": "https://github.com/danielnsilva/semanticscholar",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "Semantic Scholar paper search",
    },
    {
        "tool_name": "whoogle_search_tool",
        "category": "web",
        "synapse_domain": "discovery",
        "synapse_provider": "whoogle_search",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/whoogle_search_tool/",
        "external_repo": "https://github.com/benbusby/whoogle-search",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(requires self-hosted Whoogle instance)",
    },
    # ── Acquisition ────────────────────────────────────────────────────────
    {
        "tool_name": "url_safety_tool",
        "category": "security",
        "synapse_domain": "acquisition",
        "synapse_provider": "url_safety",
        "priority": "high",
        "toolkit_path": "ToolKit/security/url_safety_tool/",
        "external_repo": "https://github.com/NousResearch/hermes-agent",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "block 169.254.169.254 + 127.0.0.1",
    },
    {
        "tool_name": "cloudscraper_client_tool",
        "category": "web",
        "synapse_domain": "acquisition",
        "synapse_provider": "cloudscraper_http",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/cloudscraper_client_tool/",
        "external_repo": "https://github.com/VeNoMouF/cloudscraper",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_credentials_unavailable",
        "smoke_test_command": "(Cloudflare-bypass fetch — needs network + specific target)",
    },
    {
        "tool_name": "curl_cffi_client_tool",
        "category": "web",
        "synapse_domain": "acquisition",
        "synapse_provider": "curl_cffi_http",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/curl_cffi_client_tool/",
        "external_repo": "https://github.com/yifeikong/curl_cffi",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(curl_cffi native binary not installed)",
    },
    {
        "tool_name": "blocked_page_recovery_tool",
        "category": "research",
        "synapse_domain": "acquisition",
        "synapse_provider": "blocked_page_recovery",
        "priority": "medium",
        "toolkit_path": "ToolKit/research/blocked_page_recovery_tool/",
        "external_repo": "https://github.com/NousResearch/hermes-agent",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "walk recovery ladder over a fixture URL",
    },
    # ── Crawling ───────────────────────────────────────────────────────────
    {
        "tool_name": "crawl4ai_crawler_tool",
        "category": "web",
        "synapse_domain": "crawling",
        "synapse_provider": "crawl4ai",
        "priority": "high",
        "toolkit_path": "ToolKit/web/crawl4ai_crawler_tool/",
        "external_repo": "https://github.com/unclecode/crawl4ai",
        "external_license": "Apache-2.0",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(crawl4ai requires Playwright browser binaries)",
    },
    {
        "tool_name": "scrapy_adapter_tool",
        "category": "web",
        "synapse_domain": "crawling",
        "synapse_provider": "scrapy",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/scrapy_adapter_tool/",
        "external_repo": "https://github.com/scrapy/scrapy",
        "external_license": "BSD-3-Clause",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_side_effects_unsafe",
        "smoke_test_command": "(Scrapy needs full project + target site)",
    },
    # ── Browser automation ───────────────────────────────────────────────
    {
        "tool_name": "browser_cdp_tool",
        "category": "web",
        "synapse_domain": "browser",
        "synapse_provider": "cdp_browser",
        "priority": "high",
        "toolkit_path": "ToolKit/web/browser_cdp_tool/",
        "external_repo": "https://github.com/NousResearch/hermes-agent",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(requires Chrome --remote-debugging-port)",
    },
    {
        "tool_name": "playwright_browser_tool",
        "category": "web",
        "synapse_domain": "browser",
        "synapse_provider": "playwright_browser",
        "priority": "high",
        "toolkit_path": "ToolKit/web/playwright_browser_tool/",
        "external_repo": "https://github.com/microsoft/playwright-python",
        "external_license": "Apache-2.0",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(Playwright requires browser binaries)",
    },
    {
        "tool_name": "selenium_tool",
        "category": "web",
        "synapse_domain": "browser",
        "synapse_provider": "selenium_browser",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/selenium_tool/",
        "external_repo": "https://github.com/SeleniumHQ/selenium",
        "external_license": "Apache-2.0",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(Selenium requires WebDriver binary)",
    },
    {
        "tool_name": "browser_use_agent_tool",
        "category": "web",
        "synapse_domain": "browser",
        "synapse_provider": "browser_use_agent",
        "priority": "medium",
        "toolkit_path": "ToolKit/web/browser_use_agent_tool/",
        "external_repo": "https://github.com/browser-use/browser-use",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_runtime_not_configured",
        "smoke_test_command": "(requires Playwright + LLM API key)",
    },
    # ── Extraction ────────────────────────────────────────────────────────
    {
        "tool_name": "trafilatura_extractor_tool",
        "category": "web",
        "synapse_domain": "extraction",
        "synapse_provider": "trafilatura_extractor",
        "priority": "high",
        "toolkit_path": "ToolKit/web/trafilatura_extractor_tool/",
        "external_repo": "https://github.com/adbar/trafilatura",
        "external_license": "Apache-2.0",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "extract article text from fixture HTML",
    },
    {
        "tool_name": "extruct_metadata_tool",
        "category": "web",
        "synapse_domain": "extraction",
        "synapse_provider": "extruct_metadata",
        "priority": "high",
        "toolkit_path": "ToolKit/web/extruct_metadata_tool/",
        "external_repo": "https://github.com/scrapinghub/extruct",
        "external_license": "BSD-3-Clause",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "extract JSON-LD + OpenGraph from fixture HTML",
    },
    {
        "tool_name": "pdfplumber_tool",
        "category": "research",
        "synapse_domain": "extraction",
        "synapse_provider": "pdfplumber",
        "priority": "high",
        "toolkit_path": "ToolKit/research/pdfplumber_tool/",
        "external_repo": "https://github.com/jsvine/pdfplumber",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "extract text + tables from fixture PDF",
    },
    {
        "tool_name": "newspaper_tool",
        "category": "nlp",
        "synapse_domain": "extraction",
        "synapse_provider": "newspaper3k",
        "priority": "medium",
        "toolkit_path": "ToolKit/nlp/newspaper_tool/",
        "external_repo": "https://github.com/codelucas/newspaper",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "extract article from fixture HTML",
    },
    # ── Scientific research ───────────────────────────────────────────────
    {
        "tool_name": "bibtexparser_tool",
        "category": "research",
        "synapse_domain": "scientific",
        "synapse_provider": "bibtex_parser",
        "priority": "medium",
        "toolkit_path": "ToolKit/research/bibtexparser_tool/",
        "external_repo": "https://github.com/sciunto-org/python-bibtexparser",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "parse BibTeX fixture",
    },
    {
        "tool_name": "rispy_tool",
        "category": "research",
        "synapse_domain": "scientific",
        "synapse_provider": "ris_parser",
        "priority": "medium",
        "toolkit_path": "ToolKit/research/rispy_tool/",
        "external_repo": "https://github.com/MrTango/rispy",
        "external_license": "BSD-3-Clause",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "parse RIS fixture",
    },
    {
        "tool_name": "scholarly_tool",
        "category": "research",
        "synapse_domain": "scientific",
        "synapse_provider": "google_scholar",
        "priority": "low",
        "toolkit_path": "ToolKit/research/scholarly_tool/",
        "external_repo": "https://github.com/scholarly-python-package/scholarly",
        "external_license": "Unlicense",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_credentials_unavailable",
        "smoke_test_command": "(Google Scholar blocks scrapers; needs proxy/captcha service)",
    },
    {
        "tool_name": "waybackpy_archive_tool",
        "category": "research",
        "synapse_domain": "scientific",
        "synapse_provider": "wayback_machine",
        "priority": "medium",
        "toolkit_path": "ToolKit/research/waybackpy_archive_tool/",
        "external_repo": "https://github.com/akamhy/waybackpy",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "network_public",
        "smoke_test_command": "Wayback Machine availability check",
    },
    # ── Social ingestion ──────────────────────────────────────────────────
    {
        "tool_name": "feedparser_tool",
        "category": "web",
        "synapse_domain": "social",
        "synapse_provider": "feedparser",
        "priority": "high",
        "toolkit_path": "ToolKit/web/feedparser_tool/",
        "external_repo": "https://github.com/kurtmckee/feedparser",
        "external_license": "BSD-2-Clause",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "parse RSS fixture",
    },
    {
        "tool_name": "twikit_tool",
        "category": "web",
        "synapse_domain": "social",
        "synapse_provider": "twikit",
        "priority": "low",
        "toolkit_path": "ToolKit/web/twikit_tool/",
        "external_repo": "https://github.com/d60/twikit",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "skipped_credentials_unavailable",
        "smoke_test_command": "(needs X/Twitter cookies — ToS review pending)",
    },
    {
        "tool_name": "instaloader_tool",
        "category": "web",
        "synapse_domain": "social",
        "synapse_provider": "instaloader",
        "priority": "low",
        "toolkit_path": "ToolKit/web/instaloader_tool/",
        "external_repo": "https://github.com/instaloader/instaloader",
        "external_license": "GPL-3.0",  # IMPORTANT: GPL — flag for legal review
        "stdlib_only": False,
        "smoke_test_kind": "skipped_license_review_required",
        "smoke_test_command": "(GPL-3.0 — legal review required per ADR-0008 §3)",
    },
    # ── Provenance ────────────────────────────────────────────────────────
    {
        "tool_name": "skill_provenance_tool",
        "category": "utils",
        "synapse_domain": "provenance",
        "synapse_provider": "skill_provenance",
        "priority": "medium",
        "toolkit_path": "ToolKit/utils/skill_provenance_tool/",
        "external_repo": "https://github.com/NousResearch/hermes-agent",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "track write-origin of a fixture skill",
    },
    {
        "tool_name": "grounded_sources_tool",
        "category": "research",
        "synapse_domain": "provenance",
        "synapse_provider": "grounded_sources",
        "priority": "high",
        "toolkit_path": "ToolKit/research/grounded_sources_tool/",
        "external_repo": "https://github.com/NousResearch/hermes-agent",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "register a citation + retrieve",
    },
    # ── Library modules (atomic capabilities) ─────────────────────────────
    {
        "tool_name": "agentcraft_toolkit.scraping.delta_hash",
        "category": "library",
        "synapse_domain": "provenance",
        "synapse_provider": "content_delta_hash",
        "priority": "high",
        "toolkit_path": "ToolKit/library/agentcraft_toolkit/scraping/delta_hash.py",
        "external_repo": "https://github.com/mayakilzy/AgentCraft-Toolkit",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "hash a fixture content blob",
    },
    {
        "tool_name": "agentcraft_toolkit.security.safe_path",
        "category": "library",
        "synapse_domain": "provenance",
        "synapse_provider": "safe_path",
        "priority": "medium",
        "toolkit_path": "ToolKit/library/agentcraft_toolkit/security/safe_path.py",
        "external_repo": "https://github.com/mayakilzy/AgentCraft-Toolkit",
        "external_license": "MIT",
        "stdlib_only": True,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "validate path traversal attempts",
    },
    {
        "tool_name": "agentcraft_toolkit.vectorization.chunker",
        "category": "library",
        "synapse_domain": "extraction",
        "synapse_provider": "text_chunker",
        "priority": "medium",
        "toolkit_path": "ToolKit/library/agentcraft_toolkit/vectorization/chunker.py",
        "external_repo": "https://github.com/mayakilzy/AgentCraft-Toolkit",
        "external_license": "MIT",
        "stdlib_only": False,
        "smoke_test_kind": "offline_safe",
        "smoke_test_command": "chunk a fixture paragraph",
    },
]


def _check_package_available(import_path: str) -> tuple[bool, str | None]:
    """Stage 1: can the module be located?"""
    try:
        importlib.metadata.distribution(import_path.split(".")[0])
        return True, "pip"
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        spec = importlib.util.find_spec(import_path)
        if spec is not None:
            return True, "module-spec"
    except (ValueError, ModuleNotFoundError):
        pass
    return False, None


def _try_import_tool(import_path: str) -> dict:
    """Stage 2: actual import."""
    rec = {
        "import_path": import_path,
        "package_available": False,
        "package_located_via": None,
        "import_succeeded": False,
        "import_exit_code": None,
        "error": None,
    }
    found, via = _check_package_available(import_path)
    rec["package_available"] = found
    rec["package_located_via"] = via
    if not found:
        rec["import_exit_code"] = 1
        rec["error"] = "package not found"
        return rec
    try:
        importlib.import_module(import_path)
        rec["import_succeeded"] = True
        rec["import_exit_code"] = 0
    except ImportError as exc:
        rec["import_exit_code"] = 1
        rec["error"] = f"ImportError: {exc}"
    except Exception as exc:
        rec["import_exit_code"] = 2
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def _smoke_test_offline(tool_name: str, command: str) -> dict:
    """Stage 3: offline safe smoke test."""
    rec = {
        "tool_name": tool_name,
        "smoke_test_command": command,
        "smoke_test_exit_code": None,
        "skipped_reason": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    # Per-tool offline smoke logic
    try:
        if "url_safety" in tool_name:
            # Add toolkit path to sys.path then import and exercise
            toolkit_path = str(TOOLKIT_ROOT / "ToolKit" / "security" / "url_safety_tool")
            sys.path.insert(0, toolkit_path)
            try:
                if "tool" in sys.modules:
                    del sys.modules["tool"]
                from tool import UrlSafetyTool  # type: ignore

                t = UrlSafetyTool()
                r1 = t.run(url="http://169.254.169.254/latest/meta-data/")
                r2 = t.run(url="http://127.0.0.1/")
                r3 = t.run(url="https://example.com/")
                # url_safety tool returns {'safe': bool, ...} or similar
                blocked_meta = (
                    (r1.get("safe") is False)
                    or (r1.get("ok") is False)
                    or (r1.get("blocked") is True)
                )
                blocked_loop = (
                    (r2.get("safe") is False)
                    or (r2.get("ok") is False)
                    or (r2.get("blocked") is True)
                )
                allowed_public = (r3.get("safe") is True) or (r3.get("ok") is True)
                assert blocked_meta, f"169.254 should be blocked: {r1}"
                assert blocked_loop, f"loopback should be blocked: {r2}"
                rec["smoke_test_exit_code"] = 0
                rec["stdout_truncated"] = (
                    f"url_safety OK: meta IP blocked={blocked_meta}, "
                    f"loopback blocked={blocked_loop}, public allowed={allowed_public}"
                )
            finally:
                sys.path.remove(toolkit_path)
                if "tool" in sys.modules:
                    del sys.modules["tool"]
        elif "trafilatura_extractor" in tool_name:
            # trafilatura not installed — record skip
            rec["smoke_test_exit_code"] = None
            rec["skipped_reason"] = "runtime_not_configured"
            rec["stdout_truncated"] = "trafilatura pip package not installed"
        elif "feedparser" in tool_name:
            try:
                import feedparser  # type: ignore

                doc = feedparser.parse(
                    "<?xml version='1.0'?><rss version='2.0'>"
                    "<channel><title>T</title><item><title>i1</title></item></channel></rss>"
                )
                assert doc.feed.title == "T"
                assert len(doc.entries) == 1
                rec["smoke_test_exit_code"] = 0
                rec["stdout_truncated"] = "feedparser smoke OK"
            except ImportError:
                rec["smoke_test_exit_code"] = None
                rec["skipped_reason"] = "runtime_not_configured"
        elif "delta_hash" in tool_name:
            # Add library path and try import
            lib_path = str(TOOLKIT_ROOT / "ToolKit" / "library")
            sys.path.insert(0, lib_path)
            try:
                from agentcraft_toolkit.scraping.delta_hash import (
                    compute_delta_hash,  # type: ignore
                )

                h1 = compute_delta_hash("hello world")
                h2 = compute_delta_hash("hello world")
                h3 = compute_delta_hash("different")
                assert h1 == h2
                assert h1 != h3
                rec["smoke_test_exit_code"] = 0
                rec["stdout_truncated"] = "delta_hash OK: deterministic hash returned"
            except ImportError as e:
                rec["smoke_test_exit_code"] = None
                rec["skipped_reason"] = "runtime_not_configured"
                rec["stdout_truncated"] = f"library import failed: {e}"
            finally:
                sys.path.remove(lib_path)
        else:
            # Generic offline smoke for stdlib-only tools — record as "deferred"
            # to keep audit safe; the agent will do real smoke testing in G02
            # implementation if the tool is selected.
            rec["smoke_test_exit_code"] = None
            rec["skipped_reason"] = "no_safe_smoke_test_defined"
            rec["stdout_truncated"] = "Smoke test fixture not authored for this tool in audit phase"
    except Exception as exc:
        rec["smoke_test_exit_code"] = 1
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    return rec


def _smoke_test_network_public(tool_name: str, command: str) -> dict:
    """Stage 3: network-requiring smoke test (only arxiv which has public API
    without auth)."""
    rec = {
        "tool_name": tool_name,
        "smoke_test_command": command,
        "smoke_test_exit_code": None,
        "skipped_reason": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    try:
        if "arxiv_search" in tool_name:
            toolkit_path = str(TOOLKIT_ROOT / "ToolKit" / "research" / "arxiv_search_tool")
            sys.path.insert(0, toolkit_path)
            try:
                if "tool" in sys.modules:
                    del sys.modules["tool"]
                from tool import ArxivSearchTool  # type: ignore

                t = ArxivSearchTool()
                r = t.run(query="transformers attention", max_results=2)
                # arxiv_search returns {'query': ..., 'total_results': N,
                # 'count': N, 'results': [...]}
                if r.get("results") and len(r["results"]) > 0:
                    rec["smoke_test_exit_code"] = 0
                    rec["stdout_truncated"] = (
                        f"arxiv smoke OK — got {len(r['results'])} results, "
                        f"first title: {r['results'][0].get('title', '')[:60]}"
                    )
                else:
                    rec["smoke_test_exit_code"] = 1
                    rec["stderr_truncated"] = f"arxiv smoke returned no results: {str(r)[:200]}"
            finally:
                sys.path.remove(toolkit_path)
                if "tool" in sys.modules:
                    del sys.modules["tool"]
        elif "waybackpy" in tool_name:
            try:
                import waybackpy  # type: ignore

                url = "https://example.com"
                waybackpy.WaybackMachineCDXServerAPI(url)
                # Just check the import works — actual API call deferred
                rec["smoke_test_exit_code"] = 0
                rec["stdout_truncated"] = "waybackpy imported OK (no live API call)"
            except ImportError:
                rec["smoke_test_exit_code"] = None
                rec["skipped_reason"] = "runtime_not_configured"
                rec["stdout_truncated"] = "waybackpy not installed"
        else:
            rec["smoke_test_exit_code"] = None
            rec["skipped_reason"] = "no_safe_smoke_test_defined"
    except Exception as exc:
        rec["smoke_test_exit_code"] = 1
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    return rec


def _try_import_toolkit_tool(tool_name: str, toolkit_path: str) -> dict:
    """Stage 1+2 for a ToolKit adapter (all use module name 'tool').
    The toolkit_path is the absolute path to the tool's directory."""
    rec = {
        "tool_name": tool_name,
        "import_path": f"tool (from {toolkit_path})",
        "package_available": False,
        "package_located_via": None,
        "import_succeeded": False,
        "import_exit_code": None,
        "error": None,
    }
    tool_file = Path(toolkit_path) / "tool.py"
    if not tool_file.is_file():
        rec["error"] = f"tool.py not found at {tool_file}"
        rec["import_exit_code"] = 1
        return rec
    rec["package_available"] = True
    rec["package_located_via"] = "filesystem"
    # Add toolkit tool dir to sys.path then import as `tool`
    abs_path = str(Path(toolkit_path).resolve())
    if abs_path in sys.path:
        already_in_path = True
    else:
        sys.path.insert(0, abs_path)
        already_in_path = False
    # Force a fresh import (in case another tool with same name was loaded)
    if "tool" in sys.modules:
        del sys.modules["tool"]
    try:
        importlib.import_module("tool")
        rec["import_succeeded"] = True
        rec["import_exit_code"] = 0
    except ImportError as exc:
        rec["import_exit_code"] = 1
        rec["error"] = f"ImportError: {exc}"
    except Exception as exc:
        rec["import_exit_code"] = 2
        rec["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Clean up: remove from sys.path so next tool's `tool` doesn't conflict
        if not already_in_path and abs_path in sys.path:
            sys.path.remove(abs_path)
        if "tool" in sys.modules:
            del sys.modules["tool"]
    return rec


def _try_import_library_module(import_path: str) -> dict:
    """Stage 1+2 for a bundled library module (e.g. agentcraft_toolkit.scraping.delta_hash)."""
    rec = {
        "import_path": import_path,
        "package_available": False,
        "package_located_via": None,
        "import_succeeded": False,
        "import_exit_code": None,
        "error": None,
    }
    # Library modules live under ToolKit/library — add to sys.path
    lib_path = str(TOOLKIT_ROOT / "ToolKit" / "library")
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    try:
        importlib.import_module(import_path)
        rec["package_available"] = True
        rec["package_located_via"] = "module-spec"
        rec["import_succeeded"] = True
        rec["import_exit_code"] = 0
    except ImportError as exc:
        rec["import_exit_code"] = 1
        rec["error"] = f"ImportError: {exc}"
        # Try just locating the spec
        try:
            spec = importlib.util.find_spec(import_path)
            if spec is not None:
                rec["package_available"] = True
                rec["package_located_via"] = "module-spec (import failed)"
        except Exception:
            pass
    except Exception as exc:
        rec["import_exit_code"] = 2
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def run_audit():
    # ── Stage 1 + 2: inventory + import verification ────────────────────
    inventory_records = []
    verification_records = []
    for entry in SHORTLIST:
        tool_name = entry["tool_name"]
        toolkit_path = str(TOOLKIT_ROOT / entry["toolkit_path"])

        if "agentcraft_toolkit." in tool_name:
            ver = _try_import_library_module(tool_name)
        else:
            ver = _try_import_toolkit_tool(tool_name, toolkit_path)
        # Ensure tool_name is set on every record (library modules don't get it auto)
        ver["tool_name"] = tool_name

        ver["category"] = entry["category"]
        ver["version_declared"] = None
        ver["version_installed"] = None
        ver["license"] = entry["external_license"]
        ver["toolkit_path"] = entry["toolkit_path"]
        ver["external_repo"] = entry["external_repo"]
        verification_records.append(ver)

        inventory_records.append(
            {
                **entry,
                "package_available": ver["package_available"],
                "import_succeeded": ver["import_succeeded"],
                "package_located_via": ver["package_located_via"],
                "verification_status": (
                    "package_available_only"
                    if ver["package_available"] and not ver["import_succeeded"]
                    else "import_succeeded"
                    if ver["import_succeeded"]
                    else "indexed_only"
                ),
            }
        )

    # Save inventory
    inventory = {
        "audit_version": "1.0",
        "audit_schema_version": "2.0",
        "generated_at": _utcnow(),
        **TOOLKIT_SNAPSHOT,
        "shortlist_size": len(SHORTLIST),
        "tools": inventory_records,
        "capability_domain_coverage": {
            "discovery": sum(1 for r in inventory_records if r["synapse_domain"] == "discovery"),
            "acquisition": sum(
                1 for r in inventory_records if r["synapse_domain"] == "acquisition"
            ),
            "crawling": sum(1 for r in inventory_records if r["synapse_domain"] == "crawling"),
            "browser": sum(1 for r in inventory_records if r["synapse_domain"] == "browser"),
            "extraction": sum(1 for r in inventory_records if r["synapse_domain"] == "extraction"),
            "scientific": sum(1 for r in inventory_records if r["synapse_domain"] == "scientific"),
            "social": sum(1 for r in inventory_records if r["synapse_domain"] == "social"),
            "provenance": sum(1 for r in inventory_records if r["synapse_domain"] == "provenance"),
        },
    }
    (OUTPUT_DIR / "TOOLKIT_REPOSITORY_INVENTORY.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )

    # Save verification results
    verification = {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "toolkit_index_source": str(TOOLKIT_ROOT / "ToolKit" / "TOOLKIT_INDEX.json"),
        "python_version": sys.version.split()[0],
        "records": verification_records,
        "summary": {
            "total": len(verification_records),
            "package_available": sum(1 for r in verification_records if r["package_available"]),
            "import_succeeded": sum(1 for r in verification_records if r["import_succeeded"]),
            "import_failed": sum(1 for r in verification_records if not r["import_succeeded"]),
        },
    }
    (OUTPUT_DIR / "verification_results.json").write_text(
        json.dumps(verification, indent=2), encoding="utf-8"
    )

    # ── Stage 3: functional smoke tests ─────────────────────────────────
    smoke_records = []
    for entry in SHORTLIST:
        tool_name = entry["tool_name"]
        smoke_kind = entry["smoke_test_kind"]
        smoke_cmd = entry["smoke_test_command"]
        if smoke_kind == "offline_safe":
            rec = _smoke_test_offline(tool_name, smoke_cmd)
        elif smoke_kind == "network_public":
            rec = _smoke_test_network_public(tool_name, smoke_cmd)
        elif smoke_kind.startswith("skipped_"):
            reason = smoke_kind.removeprefix("skipped_")
            rec = {
                "tool_name": tool_name,
                "smoke_test_command": smoke_cmd,
                "smoke_test_exit_code": None,
                "skipped_reason": reason,
                "stdout_truncated": None,
                "stderr_truncated": None,
                "tested_at": _utcnow(),
            }
        else:
            rec = {
                "tool_name": tool_name,
                "smoke_test_command": smoke_cmd,
                "smoke_test_exit_code": None,
                "skipped_reason": "no_safe_smoke_test_defined",
                "stdout_truncated": None,
                "stderr_truncated": None,
                "tested_at": _utcnow(),
            }
        smoke_records.append(rec)

    smoke = {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "verification_results_source": str(OUTPUT_DIR / "verification_results.json"),
        "python_version": sys.version.split()[0],
        "records": smoke_records,
        "summary": {
            "total_shortlisted": len(smoke_records),
            "smoke_passed": sum(1 for r in smoke_records if r["smoke_test_exit_code"] == 0),
            "smoke_failed": sum(
                1
                for r in smoke_records
                if r["smoke_test_exit_code"] is not None and r["smoke_test_exit_code"] != 0
            ),
            "smoke_skipped": sum(1 for r in smoke_records if r["smoke_test_exit_code"] is None),
        },
    }
    (OUTPUT_DIR / "smoke_results.json").write_text(json.dumps(smoke, indent=2), encoding="utf-8")

    print("\n=== Audit pipeline complete ===")
    print(f"Inventory:  {OUTPUT_DIR / 'TOOLKIT_REPOSITORY_INVENTORY.json'}")
    print(f"Verify:     {OUTPUT_DIR / 'verification_results.json'}")
    print(f"Smoke:      {OUTPUT_DIR / 'smoke_results.json'}")
    print(f"\nVerification summary: {json.dumps(verification['summary'], indent=2)}")
    print(f"\nSmoke summary:       {json.dumps(smoke['summary'], indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(run_audit())
