"""Tests for the content_delta_hash provider (vendored function).

Per the user's G02 authorization §Required Tests:
  4. Provenance and fingerprint consistency test.
"""

from __future__ import annotations

from synapse.providers.content_delta_hash import (
    ContentDeltaHashProvider,
    __license__,
    __source_repo__,
    __toolkit_commit__,
    fingerprint,
)


def test_provenance_header_present():
    assert __toolkit_commit__ == "fd9df34c51781bd12effab62762022ab04dbd771"
    assert __source_repo__ == "https://github.com/mayakilzy/AgentCraft-Toolkit"
    assert __license__ == "MIT"


def test_provider_implements_protocol():
    p = ContentDeltaHashProvider()
    assert p.name == "content_delta_hash"
    assert p.kind == "provenance"
    assert p.enabled is True
    assert callable(p.health_check)
    assert callable(p.run)


def test_provider_health_check_returns_true():
    """health_check verifies deterministic same-input-same-output behavior."""
    p = ContentDeltaHashProvider()
    assert p.health_check() is True


def test_fingerprint_deterministic():
    """Same content → same fingerprint (Required Test #4 part 1)."""
    h1 = fingerprint("hello world")
    h2 = fingerprint("hello world")
    h3 = fingerprint("different content")
    assert h1 == h2
    assert h1 != h3


def test_fingerprint_normalizes_whitespace():
    """Whitespace-only differences should produce the same fingerprint."""
    h1 = fingerprint("hello world")
    h2 = fingerprint("hello   world")
    h3 = fingerprint("hello\nworld")
    h4 = fingerprint("  hello world  ")
    assert h1 == h2 == h3 == h4


def test_fingerprint_normalizes_case():
    """Case differences are normalized away (content-integrity, not
    cryptographic)."""
    h1 = fingerprint("Hello World")
    h2 = fingerprint("hello world")
    assert h1 == h2


def test_fingerprint_returns_hex_string():
    """Returns a 32-char MD5 hex string."""
    h = fingerprint("test content")
    assert len(h) == 32
    assert all(c in "0123456789abcdef" for c in h)


def test_fingerprint_empty_content():
    """Empty content returns the MD5 of the empty string — not an error."""
    h = fingerprint("")
    assert h == "d41d8cd98f00b204e9800998ecf8427e"


def test_fingerprint_none_content():
    """None is treated as empty."""
    h1 = fingerprint(None)  # type: ignore[arg-type]
    h2 = fingerprint("")
    assert h1 == h2


def test_provider_run_returns_dict_with_required_fields():
    """Per the user's G02 authorization §Architecture: content_fingerprint
    + toolkit_commit_sha must be in the output."""
    p = ContentDeltaHashProvider()
    r = p.run("test content for fingerprinting")
    assert r["ok"] is True
    assert "fingerprint" in r
    assert len(r["fingerprint"]) == 32
    assert r["toolkit_commit_sha"] == "fd9df34c51781bd12effab62762022ab04dbd771"
    assert "input_length" in r
    assert "normalized_length" in r


def test_provider_run_consistent_with_module_function():
    """The provider's run() must produce the same hash as the
    standalone fingerprint() function."""
    p = ContentDeltaHashProvider()
    content = "consistency check content"
    direct = fingerprint(content)
    via_provider = p.run(content)["fingerprint"]
    assert direct == via_provider


def test_provider_run_long_content():
    """Long content (e.g., an extracted article) hashes correctly."""
    long_content = "the quick brown fox jumps over the lazy dog. " * 1000
    p = ContentDeltaHashProvider()
    r = p.run(long_content)
    assert r["ok"] is True
    assert len(r["fingerprint"]) == 32
    assert r["input_length"] == len(long_content)
