"""Content delta hash — vendored function from AgentCraft-Toolkit library.

Origin: ToolKit/library/agentcraft_toolkit/scraping/delta_hash.py
Toolkit repo: https://github.com/mayakilzy/AgentCraft-Toolkit.git
Toolkit commit: fd9df34c51781bd12effab62762022ab04dbd771 (2026-09-01)
Upstream source: AgentCraft in-house (MIT)
Adapted: yes

Per ADR-0009 §1: provenance — frozen at audit time.
Per ADR-0009 §4 (least complex maintainable option): selective vendoring
is the right choice for this single function — we copy `fingerprint()`
(~30 LOC) without pulling the whole library.
Per the user's G02 authorization §Architecture: content_fingerprint is
a required provenance field for every EvidenceFragment.

The fingerprint is MD5-based (deterministic, fast). For content-integrity
purposes (detecting changes between ingests), MD5 is sufficient — this
is not a cryptographic use case. If a stronger hash is needed in the
future, the function can be swapped without changing the API.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from synapse.providers import Provider

__source_repo__ = "https://github.com/mayakilzy/AgentCraft-Toolkit"
__license__ = "MIT"
__toolkit_commit__ = "fd9df34c51781bd12effab62762022ab04dbd771"
__toolkit_path__ = "ToolKit/library/agentcraft_toolkit/scraping/delta_hash.py"


def _normalize(content: str) -> str:
    """Normalize content for stable hashing.

    Strips whitespace runs and lowercases. The goal is that two
    semantically-equivalent but whitespace-different blobs produce
    the same fingerprint.
    """
    if not content:
        return ""
    # Collapse all whitespace (spaces, tabs, newlines) to a single space.
    normalized = re.sub(r"\s+", " ", content)
    return normalized.strip().lower()


def fingerprint(content: str) -> str:
    """Compute a deterministic content fingerprint.

    Args:
        content: any text content (extracted article, raw HTML, JSON, etc.).

    Returns:
        A 32-char MD5 hex string. The same content always produces the
        same fingerprint; whitespace-only differences are normalized
        away. Empty content returns the MD5 of the empty string.

    This is NOT a cryptographic hash — it is a content-integrity marker
    used to detect changes between two ingests of the same source.
    """
    normalized = _normalize(content or "")
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


class ContentDeltaHashProvider(Provider):
    """Synapse Provider wrapper around the vendored fingerprint() function."""

    name = "content_delta_hash"
    kind = "provenance"
    enabled = True

    def health_check(self) -> bool:
        """Deterministic: same input → same output."""
        h1 = fingerprint("hello world")
        h2 = fingerprint("hello world")
        h3 = fingerprint("different")
        return h1 == h2 and h1 != h3

    def run(self, content: str) -> dict[str, Any]:
        """Compute fingerprint for a content blob.

        Returns:
            {"ok": True, "fingerprint": "<32-char hex>", "input_length": N,
             "normalized_length": M, "toolkit_commit_sha": "..."}
        """
        fp = fingerprint(content)
        normalized = _normalize(content or "")
        return {
            "ok": True,
            "fingerprint": fp,
            "input_length": len(content or ""),
            "normalized_length": len(normalized),
            "toolkit_commit_sha": __toolkit_commit__,
        }


# Module-level singleton — registered on import.
PROVIDER = ContentDeltaHashProvider()
