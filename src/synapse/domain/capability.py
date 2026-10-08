"""Capability — a typed description of what a tool/technique can do.

Per Master Spec §5: ``Capability`` records name, inputs, outputs,
prerequisites, constraints, provider mappings. Capabilities are first-class
because Synapse reasons about *what is possible*, not about specific tool
brands.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from synapse.domain._base import DomainRecord


class CapabilitySpec(DomainRecord):
    """A single input or output specification."""

    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)  # e.g. "text", "image", "json"
    required: bool = True
    description: str | None = None


class Capability(DomainRecord):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    inputs: list[CapabilitySpec] = Field(default_factory=list)
    outputs: list[CapabilitySpec] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)  # other capability names
    constraints: dict[str, Any] = Field(default_factory=dict)
    provider_mappings: dict[str, str] = Field(default_factory=dict)
    # ^ maps provider name → external capability id (e.g. "openai:gpt-4o")
