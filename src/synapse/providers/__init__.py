"""Providers package — provider adapter registry (stub in G01).

Per ADR-0001 and START_HERE §6, real provider adapters begin in G02
(Acquisition). G01 ships the registry interface so the API contract
(`/providers`) is stable.
"""

from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    """Adapter protocol for any external provider (LLM, fetch, search, …)."""

    name: str
    kind: str  # "llm" | "fetch" | "search" | "repository"
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
