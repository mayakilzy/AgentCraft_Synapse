"""Providers package — provider adapter registry.

G01 shipped the registry interface (the `Provider` protocol). G02 minimal
slice registers three concrete providers:
  - ArxivSearchProvider (vendored adapter, MIT, stdlib-only)
  - TrafilaturaExtractorProvider (pip-installed, Apache-2.0)
  - ContentDeltaHashProvider (vendored function, MIT, stdlib-only)

Per ADR-0009 §4: minimal reuse. Each provider follows the existing
contract; no new abstraction layers added.
"""

from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    """Adapter protocol for any external provider (LLM, fetch, search, …)."""

    name: str
    kind: str  # "llm" | "fetch" | "search" | "repository" | "extraction" | "provenance"
    enabled: bool

    def health_check(self) -> bool:  # pragma: no cover - protocol
        ...


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _REGISTRY[provider.name] = provider


def get(name: str) -> Provider | None:
    return _REGISTRY.get(name)


def list_all() -> list[Provider]:
    return list(_REGISTRY.values())


# Register the three G02 minimal-slice providers.
# Import is wrapped so that a missing trafilatura dependency does not
# break the whole package — only the trafilatura provider is skipped.
from synapse.providers.arxiv_search_provider import PROVIDER as _arxiv_provider  # noqa: E402
from synapse.providers.content_delta_hash import PROVIDER as _delta_hash_provider  # noqa: E402

register(_arxiv_provider)
register(_delta_hash_provider)

try:
    from synapse.providers.trafilatura_extractor_provider import (
        PROVIDER as _trafilatura_provider,
    )

    register(_trafilatura_provider)
except RuntimeError:
    # trafilatura not installed — provider stays unregistered.
    # The /providers endpoint will still list arxiv + content_delta_hash.
    pass


__all__ = ["Provider", "get", "list_all", "register"]
