"""Entity — a node in the knowledge graph.

Per Master Spec §5: supported kinds include tool, technology, technique,
capability, project, constraint, paper, repository, model, dataset,
experiment, scenario. Duplicate Entity IDs are forbidden (invariant in
G01-T02 acceptance: "duplicate entity IDs").
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, EntityType


class Entity(DomainRecord):
    kind: EntityType
    canonical_name: str = Field(..., min_length=1, max_length=512)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    canonical_uri: str | None = None  # for entities that have one (papers, repos)
    description: str | None = None

    @model_validator(mode="after")
    def _no_duplicate_aliases(self) -> Entity:
        """Aliases must be unique (case-sensitive)."""
        seen: set[str] = set()
        for a in self.aliases:
            if a in seen:
                raise ValueError(f"Duplicate alias: {a!r}")
            seen.add(a)
        # canonical_name must not be duplicated in aliases
        if self.canonical_name in self.aliases:
            raise ValueError("canonical_name must not appear in aliases list")
        return self
