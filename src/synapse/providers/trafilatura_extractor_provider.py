"""Trafilatura extractor provider — thin Synapse wrapper.

Origin: pip-installed `trafilatura` (https://github.com/adbar/trafilatura, Apache-2.0)
Toolkit commit referenced: fd9df34c51781bd12effab62762022ab04dbd771
  (the ToolKit adapter at ToolKit/web/trafilatura_extractor_tool/tool.py
  informed this wrapper, but is NOT vendored — per ADR-0009 §4 the
  least-complex option is direct pip dependency + thin Synapse wrapper.)

Per the user's G02 authorization §Architecture: this provider extracts
article text + metadata from HTML and returns a structured record
suitable for EvidenceFragment persistence. Per §Security: the caller
must validate any URL via `synapse.security.ssrf.validate_url()` BEFORE
calling this provider — this provider only parses HTML bytes.

Per ADR-0009 §1: provenance — trafilatura version recorded per call.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from synapse.providers import Provider

# Lazy import so the module loads even if trafilatura is missing
# (matches the ToolKit's lazy-dep pattern).
_TRAFILATURA_VERSION = None
try:
    import trafilatura

    _TRAFILATURA_VERSION = getattr(trafilatura, "__version__", "unknown")
except ImportError:  # pragma: no cover — tested by import-failure path
    trafilatura = None

__source_repo__ = "https://github.com/adbar/trafilatura"
__license__ = "Apache-2.0"
__toolkit_commit__ = "fd9df34c51781bd12effab62762022ab04dbd771"
__toolkit_path__ = "ToolKit/web/trafilatura_extractor_tool/tool.py (reference only — NOT vendored)"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class TrafilaturaExtractorProvider(Provider):
    """Extract article text + metadata from HTML using trafilatura.

    Caller responsibility: validate any URL via synapse.security.ssrf
    BEFORE fetching HTML to pass to this provider. This provider does
    not perform any network I/O itself.
    """

    name = "trafilatura"
    kind = "extraction"
    enabled = True

    def __init__(self) -> None:
        if trafilatura is None:
            raise RuntimeError(
                "trafilatura is not installed; install with: pip install 'trafilatura>=2.3,<3.0'"
            )
        self._mod = trafilatura

    def health_check(self) -> bool:
        """Importable + version discoverable."""
        return _TRAFILATURA_VERSION is not None

    def extract(self, html: str, source_uri: str | None = None) -> dict[str, Any]:
        """Extract article text + metadata from a single HTML document.

        Args:
            html: raw HTML bytes (UTF-8 decoded string).
            source_uri: optional canonical URI of the source (preserved in
              output for provenance; never fetched here).

        Returns a dict with keys:
          - ok: bool
          - extracted_text: str | None
          - excerpt_hash: str | None  (SHA-256 of extracted_text, first 16 chars)
          - title, author, published_at, description: provenance metadata (or None)
          - retrieved_at: ISO8601 timestamp
          - source_uri: passthrough
          - extraction_method: "trafilatura-<version>"
          - error: str | None

        Unknown metadata stays None — never fabricated.
        """
        retrieved_at = _utcnow_iso()
        extraction_method = f"trafilatura-{_TRAFILATURA_VERSION or 'unknown'}"
        try:
            text = self._mod.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_recall=True,
            )
            metadata = self._mod.extract_metadata(html)

            excerpt_hash = None
            if text:
                # SHA-256 first 16 chars — deterministic, collision-resistant
                # for content-integrity purposes.
                excerpt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

            title = getattr(metadata, "title", None) if metadata else None
            author = getattr(metadata, "author", None) if metadata else None
            published_at = getattr(metadata, "date", None) if metadata else None
            description = getattr(metadata, "description", None) if metadata else None

            return {
                "ok": text is not None and len(text) > 0,
                "extracted_text": text,
                "excerpt_hash": excerpt_hash,
                "title": title or None,
                "author": author or None,
                "published_at": published_at or None,
                "description": description or None,
                "retrieved_at": retrieved_at,
                "source_uri": source_uri,
                "extraction_method": extraction_method,
                "error": None if text else "no content extracted",
            }
        except Exception as exc:
            return {
                "ok": False,
                "extracted_text": None,
                "excerpt_hash": None,
                "title": None,
                "author": None,
                "published_at": None,
                "description": None,
                "retrieved_at": retrieved_at,
                "source_uri": source_uri,
                "extraction_method": extraction_method,
                "error": f"{type(exc).__name__}: {exc}",
            }


# Module-level singleton — registered on import.
PROVIDER = TrafilaturaExtractorProvider()
