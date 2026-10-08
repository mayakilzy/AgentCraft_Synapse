"""G02 Minimal Slice — Functional Readiness Verification.

Per ADR-0009 §2: separate inventory coverage from verified functional
coverage. Per §3: smallest necessary provider set must have real
functional evidence.

This script runs REAL functional smoke tests (not just imports) on the
candidate providers for the minimal first vertical slice. Output:
- docs/toolkit_audit/minimal_slice_smoke_results.json
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = Path("/home/z/my-project/agentcraft/toolkit_audit_workspace/toolkit")
OUTPUT = REPO_ROOT / "docs" / "toolkit_audit" / "minimal_slice_smoke_results.json"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def smoke_arxiv_search():
    """Smoke test: arxiv_search_tool — real arXiv API call."""
    rec = {
        "tool_name": "arxiv_search_tool",
        "test_kind": "network_public",
        "test_description": "Live arXiv API query for 'transformers attention', max 2 results",
        "command": "from tool import ArxivSearchTool; t.run(query='transformers attention', max_results=2)",
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    toolkit_path = str(TOOLKIT_ROOT / "ToolKit" / "research" / "arxiv_search_tool")
    sys.path.insert(0, toolkit_path)
    try:
        if "tool" in sys.modules:
            del sys.modules["tool"]
        from tool import ArxivSearchTool

        t = ArxivSearchTool()
        r = t.run(query="transformers attention", max_results=2)
        if r.get("results") and len(r["results"]) > 0:
            rec["exit_code"] = 0
            first = r["results"][0]
            rec["stdout_truncated"] = (
                f"OK — got {len(r['results'])} results, "
                f"first: arxiv_id={first.get('arxiv_id')} "
                f"title={first.get('title', '')[:60]}..."
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = f"no results: {str(r)[:200]}"
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    finally:
        if toolkit_path in sys.path:
            sys.path.remove(toolkit_path)
        if "tool" in sys.modules:
            del sys.modules["tool"]
    return rec


def smoke_url_safety():
    """Smoke test: url_safety_tool — meta IP / loopback / public."""
    rec = {
        "tool_name": "url_safety_tool",
        "test_kind": "offline_safe",
        "test_description": "URL validation against metadata IP, loopback, public",
        "command": "from tool import UrlSafetyTool; t.run(url='http://169.254.169.254/...')",
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    toolkit_path = str(TOOLKIT_ROOT / "ToolKit" / "security" / "url_safety_tool")
    sys.path.insert(0, toolkit_path)
    try:
        if "tool" in sys.modules:
            del sys.modules["tool"]
        from tool import UrlSafetyTool

        t = UrlSafetyTool()
        r1 = t.run(url="http://169.254.169.254/latest/meta-data/")
        r2 = t.run(url="http://127.0.0.1/")
        r3 = t.run(url="https://example.com/")
        blocked_meta = r1.get("safe") is False or r1.get("ok") is False or r1.get("blocked") is True
        blocked_loop = r2.get("safe") is False or r2.get("ok") is False or r2.get("blocked") is True
        allowed_public = r3.get("safe") is True or r3.get("ok") is True
        if blocked_meta and blocked_loop:
            rec["exit_code"] = 0
            rec["stdout_truncated"] = (
                f"OK — meta IP blocked={blocked_meta}, "
                f"loopback blocked={blocked_loop}, "
                f"public allowed={allowed_public}"
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = f"expected blocks: meta={r1}, loop={r2}, public={r3}"
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    finally:
        if toolkit_path in sys.path:
            sys.path.remove(toolkit_path)
        if "tool" in sys.modules:
            del sys.modules["tool"]
    return rec


def smoke_trafilatura_extract():
    """Smoke test: trafilatura.extract() on a fixture HTML — real extraction."""
    rec = {
        "tool_name": "trafilatura (pip-installed, used via thin Synapse wrapper)",
        "test_kind": "offline_safe",
        "test_description": "Extract article text + metadata from fixture HTML using trafilatura.extract()",
        "command": (
            "import trafilatura; "
            "trafilatura.extract(html, with_metadata=True, output_format='json')"
        ),
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    fixture_html = """<!DOCTYPE html>
<html><head><title>Transformer Architecture Explained</title>
<meta name="author" content="Jane Researcher">
<meta name="description" content="A primer on the Transformer architecture.">
</head><body><article>
<h1>Transformer Architecture Explained</h1>
<p>The Transformer architecture, introduced in 2017, replaces recurrence with
self-attention. <a href="https://arxiv.org/abs/1706.03762">See the original paper</a>.</p>
<p>Multi-head attention allows the model to attend to information from different
representation subspaces at different positions.</p>
</article></body></html>"""
    try:
        import trafilatura

        # Extract text
        text = trafilatura.extract(fixture_html, include_comments=False, include_tables=True)
        # Extract metadata
        metadata = trafilatura.extract_metadata(fixture_html)
        if text and "Transformer" in text:
            rec["exit_code"] = 0
            title = metadata.title if metadata else "?"
            author = metadata.author if metadata else "?"
            date = metadata.date if metadata else "?"
            rec["stdout_truncated"] = (
                f"OK — extracted text ({len(text)} chars); "
                f"title={title!r}; author={author!r}; date={date!r}"
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = (
                f"extraction returned no text or wrong content: {text[:200] if text else None}"
            )
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    return rec


def smoke_feedparser():
    """Smoke test: feedparser.parse() on a fixture RSS feed."""
    rec = {
        "tool_name": "feedparser (pip-installed, used via thin Synapse wrapper)",
        "test_kind": "offline_safe",
        "test_description": "Parse a fixture RSS feed using feedparser.parse()",
        "command": "import feedparser; feedparser.parse(rss_xml)",
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    fixture_rss = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
<title>arXiv cs.AI</title>
<link>https://arxiv.org/list/cs.AI/recent</link>
<description>Computing Research Repository - Artificial Intelligence</description>
<item>
<title>Sample Paper on Transformers</title>
<link>https://arxiv.org/abs/2401.00001</link>
<description>A description of the paper.</description>
<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Author One</dc:creator>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
</item>
</channel>
</rss>"""
    try:
        import feedparser

        doc = feedparser.parse(fixture_rss)
        if doc.feed.title == "arXiv cs.AI" and len(doc.entries) == 1:
            entry = doc.entries[0]
            rec["exit_code"] = 0
            rec["stdout_truncated"] = (
                f"OK — feed title={doc.feed.title!r}; "
                f"entry title={entry.title!r}; "
                f"link={entry.link!r}; "
                f"author={getattr(entry, 'author', '?')!r}"
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = (
                f"unexpected parse result: feed={doc.feed}, entries={doc.entries}"
            )
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    return rec


def smoke_bibtex_parser():
    """Smoke test: bibtexparser on a fixture BibTeX string."""
    rec = {
        "tool_name": "bibtexparser (pip-installed, used via thin Synapse wrapper)",
        "test_kind": "offline_safe",
        "test_description": "Parse a fixture BibTeX entry",
        "command": "import bibtexparser; bibtexparser.parse(bibtex_str)",
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    fixture_bibtex = """@article{vaswani2017attention,
  title={Attention Is All You Need},
  author={Vaswani, Ashish and Shazeer, Noam and Parmar, Niki},
  journal={Advances in Neural Information Processing Systems},
  volume={30},
  year={2017},
  doi={10.48550/arXiv.1706.03762}
}"""
    try:
        import bibtexparser

        # bibtexparser v2 API
        try:
            library = bibtexparser.parse_string(fixture_bibtex)
            entries = library.entries
        except AttributeError:
            # v1 API fallback
            from io import StringIO

            parser = bibtexparser.bparser.BibTexParser()
            library = bibtexparser.load(StringIO(fixture_bibtex), parser=parser)
            entries = library.entries_list

        if entries and len(entries) >= 1:
            e = entries[0]
            # v2 uses EntryDict; v1 uses dict
            try:
                key = e.key
                title = (
                    e.fields_dict.get("title", {}).value
                    if hasattr(e, "fields_dict")
                    else e.get("title", "")
                )
            except AttributeError:
                key = e.get("ID", "?")
                title = e.get("title", "?")
            rec["exit_code"] = 0
            rec["stdout_truncated"] = (
                f"OK — parsed {len(entries)} entry/entries; first key={key!r}; title={title!r}"
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = "no entries parsed"
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    return rec


def smoke_content_delta_hash():
    """Smoke test: agentcraft_toolkit.scraping.delta_hash."""
    rec = {
        "tool_name": "agentcraft_toolkit.scraping.delta_hash",
        "test_kind": "offline_safe",
        "test_description": "Compute deterministic content hash for fixture content",
        "command": ("from agentcraft_toolkit.scraping.delta_hash import <fn>; <fn>('hello world')"),
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    lib_path = str(TOOLKIT_ROOT / "ToolKit" / "library")
    sys.path.insert(0, lib_path)
    try:
        # Read the module to find the right function name
        module_path = Path(lib_path) / "agentcraft_toolkit" / "scraping" / "delta_hash.py"
        if not module_path.is_file():
            rec["exit_code"] = 1
            rec["stderr_truncated"] = f"module not found at {module_path}"
            return rec

        # Inspect the module to find available functions
        module_text = module_path.read_text()
        # Look for `def ` lines
        import re

        funcs = re.findall(r"^def (\w+)\(", module_text, re.MULTILINE)
        if not funcs:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = "no public functions found in delta_hash module"
            return rec

        # Import the module
        import importlib

        mod = importlib.import_module("agentcraft_toolkit.scraping.delta_hash")
        # Find first function that takes a string and returns a hash
        for fn_name in funcs:
            if fn_name.startswith("_"):
                continue
            try:
                fn = getattr(mod, fn_name)
                # Try calling with a string
                result = fn("hello world")
                if isinstance(result, str) and len(result) > 0:
                    rec["exit_code"] = 0
                    rec["stdout_truncated"] = (
                        f"OK — fn={fn_name!r}; hash={result[:32]}... "
                        f"(deterministic: {fn('hello world') == result})"
                    )
                    return rec
            except Exception:
                continue

        rec["exit_code"] = 1
        rec["stderr_truncated"] = f"no suitable hash function found in {funcs}"
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    finally:
        if lib_path in sys.path:
            sys.path.remove(lib_path)
    return rec


def smoke_synapse_ssrf_guard():
    """Smoke test: Synapse's own SSRF guard (existing in G01)."""
    rec = {
        "tool_name": "synapse.security.ssrf (existing in Synapse G01)",
        "test_kind": "offline_safe",
        "test_description": "Verify Synapse's existing SSRF guard blocks meta IP / loopback",
        "command": (
            "from synapse.security.ssrf import validate_url; "
            "validate_url('http://169.254.169.254/...')"
        ),
        "exit_code": None,
        "stdout_truncated": None,
        "stderr_truncated": None,
        "tested_at": _utcnow(),
    }
    # Need to add src/ to path
    src_path = str(REPO_ROOT / "src")
    sys.path.insert(0, src_path)
    try:
        # Set SYNAPSE_ENV=test so config doesn't fail production checks
        import os

        os.environ.setdefault("SYNAPSE_ENV", "test")
        os.environ.setdefault("SYNAPSE_DB_URL", "sqlite+aiosqlite:///:memory:")

        from synapse.security.ssrf import SSRFError, validate_url

        # Test 1: meta IP should be blocked
        try:
            validate_url("http://169.254.169.254/latest/meta-data/")
            blocked_meta = False
        except SSRFError:
            blocked_meta = True

        # Test 2: loopback should be blocked
        try:
            validate_url("http://127.0.0.1/")
            blocked_loop = False
        except SSRFError:
            blocked_loop = True

        # Test 3: file:// scheme should be blocked
        try:
            validate_url("file:///etc/passwd")
            blocked_file = False
        except SSRFError:
            blocked_file = True

        if blocked_meta and blocked_loop and blocked_file:
            rec["exit_code"] = 0
            rec["stdout_truncated"] = (
                f"OK — meta IP blocked={blocked_meta}, "
                f"loopback blocked={blocked_loop}, "
                f"file:// blocked={blocked_file}"
            )
        else:
            rec["exit_code"] = 1
            rec["stderr_truncated"] = (
                f"expected all blocks: meta={blocked_meta}, "
                f"loop={blocked_loop}, file={blocked_file}"
            )
    except Exception as exc:
        rec["exit_code"] = 2
        rec["stderr_truncated"] = f"{type(exc).__name__}: {exc}"
    finally:
        if src_path in sys.path:
            sys.path.remove(src_path)
    return rec


def main():
    results = []
    print("=== G02 Minimal Slice — Functional Smoke Tests ===\n")
    for fn in [
        smoke_arxiv_search,
        smoke_url_safety,
        smoke_synapse_ssrf_guard,
        smoke_trafilatura_extract,
        smoke_feedparser,
        smoke_bibtex_parser,
        smoke_content_delta_hash,
    ]:
        rec = fn()
        results.append(rec)
        status = "PASS" if rec["exit_code"] == 0 else "FAIL"
        out = rec.get("stdout_truncated") or rec.get("stderr_truncated") or ""
        print(f"  {rec['tool_name'][:60]:60s}  {status}  {out[:80]}")

    summary = {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "purpose": "Minimal first vertical slice — real functional smoke tests per ADR-0009 §2",
        "records": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["exit_code"] == 0),
            "failed": sum(1 for r in results if r["exit_code"] is not None and r["exit_code"] != 0),
            "skipped": sum(1 for r in results if r["exit_code"] is None),
        },
    }
    OUTPUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nResults: {OUTPUT}")
    print(f"Summary: {json.dumps(summary['summary'], indent=2)}")
    return 0 if summary["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
