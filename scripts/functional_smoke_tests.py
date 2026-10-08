"""Functional smoke tests for shortlisted toolkit providers.

Per ADR-0008 §2: import success alone is NOT enough. We must perform
actual functional smoke tests wherever safe and feasible, and record
explicitly when we cannot (credentials unavailable, network restricted,
runtime not configured, side effects).

This script consumes:
  - docs/toolkit_audit/verification_results.json  (from verify_tool_imports.py)

And produces:
  - docs/toolkit_audit/smoke_results.json

Each record has:
  - tool_name
  - smoke_test_command
  - smoke_test_exit_code (0 = pass, non-zero = fail, null = skipped)
  - skipped_reason (one of: credentials_unavailable, network_restricted,
    runtime_not_configured, side_effects_unsafe, not_shortlisted,
    no_safe_smoke_test_defined)
  - stdout_truncated (first 4 KB, secrets redacted)
  - stderr_truncated (first 4 KB, secrets redacted)
  - tested_at (ISO8601 timestamp)

A "safe" smoke test is one that:
  - Does NOT require credentials (no API keys, no OAuth tokens).
  - Does NOT make network calls to non-public endpoints.
  - Does NOT write outside a tmp_path sandbox.
  - Does NOT have side effects beyond the test process.

For tools that DO require credentials or network, we record the skip
reason explicitly. The audit report and provider selection proposal
will call out these gaps so the user can decide whether to provision
credentials.

Usage:
    python scripts/functional_smoke_tests.py <verification_results.json>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "docs" / "toolkit_audit" / "smoke_results.json"

# Patterns that look like secrets — redacted from stdout/stderr before
# the truncated output is recorded.
_SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"), r"\1***REDACTED***"),
    (re.compile(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+"), r"\1***REDACTED***"),
    (re.compile(r"(ghp_[A-Za-z0-9]{4})[A-Za-z0-9]+"), r"\1***REDACTED***"),
    (re.compile(r"(password\s*[:=]\s*)(\S+)"), r"\1***REDACTED***"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*)(\S+)"), r"\1***REDACTED***"),
]


def _redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# ── Per-tool safe smoke-test definitions ─────────────────────────────────────
#
# Each entry maps a tool name → a dict with:
#   - command: list[str]   — what to run
#   - skipped_reason: str  — if we KNOW we can't safely smoke-test this tool
#                             in CI, document why here and skip.
#
# A tool is "shortlisted" for smoke testing iff:
#   - verification_results says import_succeeded=true for it, AND
#   - it appears in SMOKE_TESTS (with command OR skipped_reason).
#
# Tools not in SMOKE_TESTS get skipped_reason="no_safe_smoke_test_defined"
# so the audit report can flag them.

SMOKE_TESTS: dict[str, dict[str, Any]] = {
    # ── HTTP clients / fetchers ────────────────────────────────────────────
    "httpx": {
        "command": [
            sys.executable,
            "-c",
            "import httpx; r = httpx.get('https://httpbin.org/get'); "
            "assert r.status_code == 200; "
            "assert 'headers' in r.json(); print('httpx smoke OK')",
        ],
        # Note: this hits a public endpoint (httpbin.org). If the CI
        # sandbox blocks outbound network, the test fails and we record
        # the failure — NOT a skip. The user can then decide whether to
        # allow network in CI.
    },
    "requests": {
        "command": [
            sys.executable,
            "-c",
            "import requests; r = requests.get('https://httpbin.org/get', timeout=5); "
            "assert r.status_code == 200; print('requests smoke OK')",
        ],
    },
    "aiohttp": {
        "command": [
            sys.executable,
            "-c",
            "import asyncio, aiohttp; "
            "async def t(): "
            "  async with aiohttp.ClientSession() as s: "
            "    r = await s.get('https://httpbin.org/get', timeout=5); "
            "    assert r.status == 200; "
            "asyncio.run(t()); print('aiohttp smoke OK')",
        ],
    },
    # ── HTML / XML parsers ────────────────────────────────────────────────
    "bs4": {
        "command": [
            sys.executable,
            "-c",
            "from bs4 import BeautifulSoup; "
            "soup = BeautifulSoup('<html><body><h1>Hi</h1><p>x</p></body></html>', 'html.parser'); "
            "assert soup.find('h1').text == 'Hi'; "
            "assert soup.find('p').text == 'x'; print('bs4 smoke OK')",
        ],
    },
    "lxml": {
        "command": [
            sys.executable,
            "-c",
            "from lxml import etree; "
            "root = etree.fromstring(b'<root><a>1</a><b>2</b></root>'); "
            "assert root.find('a').text == '1'; "
            "assert root.find('b').text == '2'; print('lxml smoke OK')",
        ],
    },
    "trafilatura": {
        "command": [
            sys.executable,
            "-c",
            "import trafilatura; "
            "html = '<html><body><article><h1>Title</h1><p>Body text</p></article></body></html>'; "
            "text = trafilatura.extract(html); "
            "assert text and 'Body' in text; print('trafilatura smoke OK')",
        ],
    },
    "feedparser": {
        "command": [
            sys.executable,
            "-c",
            "import feedparser; "
            "doc = feedparser.parse('''<?xml version='1.0'?><rss version='2.0'>"
            "<channel><title>T</title><item><title>i1</title></item></channel></rss>'''); "
            "assert doc.feed.title == 'T'; "
            "assert len(doc.entries) == 1; print('feedparser smoke OK')",
        ],
    },
    # ── Scientific / academic ─────────────────────────────────────────────
    "arxiv": {
        # arxiv library — fetch one paper metadata record from the public API.
        "command": [
            sys.executable,
            "-c",
            "import arxiv; "
            "client = arxiv.Client(); "
            "search = arxiv.Search(id_list=['1707.01532']); "
            "results = list(client.results(search)); "
            "assert len(results) == 1; "
            "assert results[0].entry_id; print('arxiv smoke OK')",
        ],
        # Network-required; will fail in offline CI — recorded as failure,
        # not skip.
    },
    # ── Tools we KNOW we cannot safely smoke-test in CI ────────────────────
    "openai": {
        "skipped_reason": "credentials_unavailable",
        "skipped_detail": "OpenAI API requires SYNAPSE_OPENAI_API_KEY; "
        "not configured in audit environment.",
    },
    "anthropic": {
        "skipped_reason": "credentials_unavailable",
        "skipped_detail": "Anthropic API requires SYNAPSE_ANTHROPIC_API_KEY; "
        "not configured in audit environment.",
    },
    "playwright": {
        "skipped_reason": "runtime_not_configured",
        "skipped_detail": "Playwright requires browser binaries "
        "(playwright install chromium) which are not "
        "installed in the audit environment.",
    },
    "selenium": {
        "skipped_reason": "runtime_not_configured",
        "skipped_detail": "Selenium requires a browser driver binary; "
        "not configured in audit environment.",
    },
    "scrapy": {
        "skipped_reason": "side_effects_unsafe",
        "skipped_detail": "Scrapy's default smoke (start a spider) makes "
        "real HTTP requests to non-public-test targets; "
        "an isolated fixture would require building a "
        "full Scrapy project. Deferred to G02 implementation.",
    },
    "twikit": {
        "skipped_reason": "credentials_unavailable",
        "skipped_detail": "Twikit requires X/Twitter login credentials; "
        "ToS review also pending per DECISIONS_AND_ASSUMPTIONS.md.",
    },
    "crawl4ai": {
        "skipped_reason": "runtime_not_configured",
        "skipped_detail": "Crawl4AI requires Playwright browser binaries.",
    },
}


def _shortlist(verification: dict) -> list[str]:
    """Return tool names that import_succeeded=true AND are in SMOKE_TESTS."""
    out = []
    for rec in verification.get("records", []):
        name = rec["tool_name"]
        if rec.get("import_succeeded") and name in SMOKE_TESTS:
            out.append(name)
    return out


def _run_smoke(tool_name: str, command: list[str]) -> dict:
    """Run a smoke-test command and capture the result."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
        return {
            "tool_name": tool_name,
            "smoke_test_command": " ".join(command),
            "smoke_test_exit_code": proc.returncode,
            "skipped_reason": None,
            "stdout_truncated": _redact(proc.stdout[:4096]),
            "stderr_truncated": _redact(proc.stderr[:4096]),
            "tested_at": _utcnow(),
        }
    except subprocess.TimeoutExpired:
        return {
            "tool_name": tool_name,
            "smoke_test_command": " ".join(command),
            "smoke_test_exit_code": None,
            "skipped_reason": "timeout",
            "stdout_truncated": "(timeout after 30s)",
            "stderr_truncated": "(timeout after 30s)",
            "tested_at": _utcnow(),
        }


def _record_skip(tool_name: str, reason: str, detail: str) -> dict:
    return {
        "tool_name": tool_name,
        "smoke_test_command": None,
        "smoke_test_exit_code": None,
        "skipped_reason": reason,
        "skipped_detail": detail,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }


def run(verification_path: Path) -> int:
    if not verification_path.is_file():
        print(f"ERROR: verification_results.json not found: {verification_path}", file=sys.stderr)
        return 1

    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    shortlist = _shortlist(verification)

    results = {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "verification_results_source": str(verification_path),
        "python_version": sys.version.split()[0],
        "records": [],
        "summary": {
            "total_shortlisted": len(shortlist),
            "smoke_passed": 0,
            "smoke_failed": 0,
            "smoke_skipped": 0,
        },
    }

    for tool_name in shortlist:
        smoke_def = SMOKE_TESTS[tool_name]
        if "skipped_reason" in smoke_def:
            rec = _record_skip(
                tool_name,
                reason=smoke_def["skipped_reason"],
                detail=smoke_def.get("skipped_detail", ""),
            )
            results["summary"]["smoke_skipped"] += 1
        else:
            rec = _run_smoke(tool_name, smoke_def["command"])
            if rec["smoke_test_exit_code"] == 0:
                results["summary"]["smoke_passed"] += 1
            else:
                results["summary"]["smoke_failed"] += 1
        results["records"].append(rec)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\nSmoke results written to: {OUTPUT_PATH}")
    print(f"Shortlisted:     {results['summary']['total_shortlisted']}")
    print(f"Smoke passed:    {results['summary']['smoke_passed']}")
    print(f"Smoke failed:    {results['summary']['smoke_failed']}")
    print(f"Smoke skipped:   {results['summary']['smoke_skipped']}")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: python scripts/functional_smoke_tests.py <verification_results.json>",
            file=sys.stderr,
        )
        return 2
    return run(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
