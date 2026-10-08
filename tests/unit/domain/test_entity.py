"""Entity tests — duplicate aliases and canonical-name collision."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.domain._base import EntityType
from synapse.domain.entity import Entity


def test_entity_basic():
    e = Entity(kind=EntityType.TOOL, canonical_name="FastAPI")
    assert e.kind == "tool"
    assert e.canonical_name == "FastAPI"


def test_entity_rejects_duplicate_aliases():
    with pytest.raises(ValidationError):
        Entity(
            kind=EntityType.TOOL,
            canonical_name="FastAPI",
            aliases=["fastapi", "fastapi"],
        )


def test_entity_rejects_canonical_in_aliases():
    with pytest.raises(ValidationError):
        Entity(
            kind=EntityType.TOOL,
            canonical_name="FastAPI",
            aliases=["FastAPI"],
        )


def test_entity_extra_field_forbidden():
    with pytest.raises(ValidationError):
        Entity(kind=EntityType.TOOL, canonical_name="x", bogus=True)


def test_entity_supports_all_kinds():
    """All EntityType values must be constructible."""
    for kind in EntityType:
        Entity(kind=kind, canonical_name=kind.value)
