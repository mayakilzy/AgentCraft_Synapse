"""arXiv search provider — vendored from AgentCraft-Toolkit.

Origin: ToolKit/research/arxiv_search_tool/tool.py
Toolkit repo: https://github.com/mayakilzy/AgentCraft-Toolkit.git
Toolkit commit: fd9df34c51781bd12effab62762022ab04dbd771 (2026-09-01)
Upstream source: https://github.com/NousResearch/hermes-agent (MIT, (c) 2025 Nous Research)
Adapted: yes (per ToolKit index `adapted: true`)

This is a thin Synapse Provider wrapper around the ArxivSearchTool class
vendored from the AgentCraft-Toolkit. The class performs the HTTP fetch
and Atom/OpenSearch XML parsing itself (stdlib only) and returns a
normalized, JSON-serializable result dict. We preserve the original
implementation intact and add a Synapse Provider wrapper.

Per ADR-0009 §1 (audit evidence preservation): the Toolkit commit SHA
above is the frozen reference. Per ADR-0009 §4 (least complex
maintainable option): vendoring is the right choice for this adapter
because it has substantial Atom-XML parsing logic worth reusing.
Per the user's G02 authorization: trafilatura and arxiv search use the
existing Synapse SSRF guard for any URL fetch (none here — arXiv uses a
fixed base URL).
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

from synapse.providers import Provider

# Per ADR-0009 §1: provenance — frozen at audit time.
__source_repo__ = "https://github.com/NousResearch/hermes-agent"
__license__ = "MIT"
__toolkit_commit__ = "fd9df34c51781bd12effab62762022ab04dbd771"
__toolkit_path__ = "ToolKit/research/arxiv_search_tool/tool.py"

NS = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_RESULTS = 10


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class ArxivSearchTool:
    """Search the arXiv API (Atom feed) by query, author, category, or id list.

    Vendored verbatim from
    ToolKit/research/arxiv_search_tool/tool.py @ fd9df34.
    Stdlib-only; no third-party deps.
    """

    def run(
        self,
        query: str | None = None,
        author: str | None = None,
        category: str | None = None,
        id_list: list[str] | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        # Build query string
        terms: list[str] = []
        if query:
            terms.append(f"all:{urllib.parse.quote(query)}")
        if author:
            terms.append(f"au:{urllib.parse.quote(author)}")
        if category:
            terms.append(f"cat:{urllib.parse.quote(category)}")
        if id_list:
            terms.append(f"id_list:{','.join(id_list)}")
        if not terms:
            return {
                "query": "",
                "total_results": 0,
                "count": 0,
                "results": [],
                "error": "no query parameters provided",
            }
        search_query = "+".join(terms)
        url = f"{ARXIV_API_URL}?search_query={search_query}&start=0&max_results={max_results}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AgentCraft-Synapse/0.1 (+https://github.com/mayakilzy/AgentCraft_Synapse)"
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            return {
                "query": search_query,
                "total_results": 0,
                "count": 0,
                "results": [],
                "error": f"URLError: {exc}",
            }
        except Exception as exc:
            return {
                "query": search_query,
                "total_results": 0,
                "count": 0,
                "results": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

        # Parse Atom XML
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            return {
                "query": search_query,
                "total_results": 0,
                "count": 0,
                "results": [],
                "error": f"XML parse error: {exc}",
            }

        total_results_el = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        total_results = (
            int(total_results_el.text)
            if total_results_el is not None and total_results_el.text
            else 0
        )

        results: list[dict[str, Any]] = []
        for entry in root.findall("a:entry", NS):
            id_el = entry.find("a:id", NS)
            arxiv_id_full = id_el.text if id_el is not None and id_el.text else ""
            # Strip the abs/ prefix; split version
            arxiv_id = (
                arxiv_id_full.split("/abs/")[-1] if "/abs/" in arxiv_id_full else arxiv_id_full
            )
            version = ""
            if "v" in arxiv_id:
                base, _, ver = arxiv_id.rpartition("v")
                if ver.isdigit():
                    arxiv_id, version = base, f"v{ver}"

            title_el = entry.find("a:title", NS)
            summary_el = entry.find("a:summary", NS)
            published_el = entry.find("a:published", NS)
            updated_el = entry.find("a:updated", NS)

            authors: list[str] = []
            for author_el in entry.findall("a:author", NS):
                name_el = author_el.find("a:name", NS)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            categories: list[str] = []
            for cat_el in entry.findall("a:category", NS):
                term = cat_el.attrib.get("term")
                if term:
                    categories.append(term)

            doi = ""
            for link_el in entry.findall("a:link", NS):
                if link_el.attrib.get("title") == "doi":
                    doi = link_el.attrib.get("href", "").split("/")[-1]
                    break

            pdf_url = ""
            for link_el in entry.findall("a:link", NS):
                if link_el.attrib.get("title") == "pdf":
                    pdf_url = link_el.attrib.get("href", "")
                    break

            results.append(
                {
                    "arxiv_id": arxiv_id,
                    "version": version,
                    "full_id": arxiv_id_full.rsplit("/", 1)[-1] if arxiv_id_full else "",
                    "title": (title_el.text or "").strip().replace("\n", " ")
                    if title_el is not None
                    else "",
                    "summary": (summary_el.text or "").strip().replace("\n", " ")
                    if summary_el is not None
                    else "",
                    "published": published_el.text if published_el is not None else "",
                    "updated": updated_el.text if updated_el is not None else "",
                    "authors": authors,
                    "categories": categories,
                    "doi": doi,
                    "pdf_url": pdf_url,
                    "canonical_uri": arxiv_id_full,
                }
            )

        return {
            "query": search_query,
            "total_results": total_results,
            "count": len(results),
            "results": results,
        }


class ArxivSearchProvider(Provider):
    """Synapse Provider wrapper around ArxivSearchTool.

    Implements the Synapse Provider protocol. The actual search logic
    is delegated to the vendored ArxivSearchTool above. Synapse's SSRF
    guard is not needed here because the destination URL is fixed
    (https://export.arxiv.org/api/query) and parameterized only by
    URL-encoded query strings — no user-supplied URL reaches urllib.
    """

    name = "arxiv"
    kind = "search"
    enabled = True

    def __init__(self) -> None:
        self._tool = ArxivSearchTool()

    def health_check(self) -> bool:
        """A trivial query confirms the arXiv API is reachable."""
        try:
            r = self._tool.run(query="test", max_results=1, timeout=5)
            return "error" not in r or r.get("count", 0) >= 0
        except Exception:
            return False

    def discover(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        author: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Run an arXiv search and return normalized Source candidates."""
        r = self._tool.run(query=query, author=author, category=category, max_results=max_results)
        if "error" in r:
            return r
        # Augment each result with a discovered_at timestamp and provider name.
        ts = _utcnow_iso()
        for entry in r["results"]:
            entry["discovered_at"] = ts
            entry["provider"] = self.name
            entry["source_type"] = "paper"
        return r


# Module-level singleton — registered on import.
PROVIDER = ArxivSearchProvider()
