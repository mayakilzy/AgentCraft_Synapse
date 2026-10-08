"""Capability tests."""

from __future__ import annotations

from synapse.domain.capability import Capability, CapabilitySpec


def test_capability_basic():
    c = Capability(
        name="web.fetch",
        inputs=[CapabilitySpec(name="url", type="string")],
        outputs=[CapabilitySpec(name="content", type="bytes")],
    )
    assert c.name == "web.fetch"
    assert len(c.inputs) == 1


def test_capability_empty_ok():
    """A capability with no inputs/outputs is allowed (e.g. notification sinks)."""
    c = Capability(name="noop")
    assert c.inputs == []
    assert c.outputs == []


def test_capability_spec_required_default_true():
    s = CapabilitySpec(name="url", type="string")
    assert s.required is True
