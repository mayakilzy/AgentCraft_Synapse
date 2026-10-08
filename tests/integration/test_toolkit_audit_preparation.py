"""Toolkit audit preparation tests.

Verifies that the audit scaffolding (schema validator + matrix generator)
works end-to-end with a sample inventory. Does NOT verify any real tool —
this only tests the audit pipeline's machinery.

These tests can run BEFORE the user provides TOOLKIT_INDEX(1).json
because they use a fixture inventory written by the test itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "toolkit_audit" / "TOOLKIT_INDEX_SCHEMA.json"
MATRIX_SCHEMA_PATH = REPO_ROOT / "docs" / "toolkit_audit" / "LAYER_1_CAPABILITY_MATRIX.schema.json"
VALIDATE_SCRIPT = REPO_ROOT / "scripts" / "validate_toolkit_index.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_tool_imports.py"
GENERATE_SCRIPT = REPO_ROOT / "scripts" / "generate_capability_matrix.py"


# ── Sample fixture inventory ─────────────────────────────────────────────────


SAMPLE_INVENTORY = {
    "version": "1.0",
    "generated_at": "2026-10-08T00:00:00Z",
    "source": "test-fixture",
    "total_tools": 4,
    "categories": {
        "discovery": {
            "description": "search and discovery",
            "tools": [
                {
                    "name": "httpx",  # actually installed in test env
                    "category": "discovery",
                    "license": "BSD-3-Clause",
                    "version": "0.28.1",
                    "supported_sources": ["web"],
                    "description": "HTTP client",
                },
                {
                    "name": "this_tool_does_not_exist_xyz",
                    "category": "discovery",
                    "license": "MIT",
                    "description": "non-existent tool to test failure path",
                },
            ],
        },
        "extraction": {
            "tools": [
                {
                    "name": "json",  # stdlib — should import OK
                    "category": "extraction",
                    "license": "PSF-2.0",
                    "description": "JSON parser (stdlib)",
                }
            ],
        },
        "browser": {
            "tools": [
                {
                    "name": "playwright_not_installed_xyz",
                    "category": "browser",
                    "license": "Apache-2.0",
                    "description": "browser automation — not installed",
                }
            ],
        },
    },
}


@pytest.fixture()
def sample_inventory_file(tmp_path: Path) -> Path:
    p = tmp_path / "TOOLKIT_INDEX_test.json"
    p.write_text(json.dumps(SAMPLE_INVENTORY), encoding="utf-8")
    return p


# ── Schema tests ──────────────────────────────────────────────────────────────


def test_toolkit_index_schema_exists():
    assert SCHEMA_PATH.is_file(), f"schema missing: {SCHEMA_PATH}"
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["title"] == "AgentCraft Toolkit Index"
    assert "categories" in schema["required"]


def test_matrix_schema_exists():
    assert MATRIX_SCHEMA_PATH.is_file()
    schema = json.loads(MATRIX_SCHEMA_PATH.read_text())
    assert schema["title"] == "Layer-1 Capability Matrix"


def test_sample_inventory_passes_schema_validation(sample_inventory_file):
    """The fixture inventory must validate against the schema."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text())
    data = json.loads(sample_inventory_file.read_text())
    jsonschema.validate(data, schema)  # raises ValidationError if invalid


# ── Validator script tests ───────────────────────────────────────────────────


def _run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )


def test_validator_returns_0_on_valid_inventory(sample_inventory_file):
    r = _run_script(VALIDATE_SCRIPT, str(sample_inventory_file))
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "PASS" in r.stdout
    assert "Total tools: 4" in r.stdout


def test_validator_returns_1_on_invalid_inventory(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"version": "1.0"}),  # missing required fields
        encoding="utf-8",
    )
    r = _run_script(VALIDATE_SCRIPT, str(bad))
    assert r.returncode == 1
    assert "FAIL" in r.stderr or "schema validation error" in r.stderr


def test_validator_returns_2_on_missing_file():
    r = _run_script(VALIDATE_SCRIPT, "/nonexistent/path.json")
    assert r.returncode == 2


def test_validator_returns_2_on_no_args():
    r = _run_script(VALIDATE_SCRIPT)
    assert r.returncode == 2


# ── Verify-imports script tests ───────────────────────────────────────────────


def test_verify_imports_writes_results_file(sample_inventory_file, tmp_path, monkeypatch):
    """The verify script must produce a JSON file with one record per tool."""
    # Force the output path to tmp_path
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    r = _run_script(VERIFY_SCRIPT, str(sample_inventory_file))
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "Total tools: 4" in r.stdout

    results_path = REPO_ROOT / "docs" / "toolkit_audit" / "verification_results.json"
    assert results_path.is_file(), f"results file not created: {results_path}"
    try:
        results = json.loads(results_path.read_text())
        assert results["summary"]["total"] == 4
        # httpx and json are importable; the two bogus ones fail
        assert results["summary"]["importable"] == 2
        assert results["summary"]["failed"] == 2
        # Each record has the required fields
        for rec in results["records"]:
            assert "tool_name" in rec
            assert "import_exit_code" in rec
            assert "license" in rec
    finally:
        results_path.unlink(missing_ok=True)


# ── Matrix generator tests ───────────────────────────────────────────────────


def test_matrix_generator_produces_valid_output(sample_inventory_file, tmp_path):
    """End-to-end: validate → verify → generate → check matrix conforms to schema."""
    import jsonschema

    # Step 1: verify imports (produces verification_results.json in docs/toolkit_audit/)
    r = _run_script(VERIFY_SCRIPT, str(sample_inventory_file))
    assert r.returncode == 0
    results_path = REPO_ROOT / "docs" / "toolkit_audit" / "verification_results.json"
    assert results_path.is_file()

    try:
        out_json = tmp_path / "matrix.json"
        out_md = tmp_path / "matrix.md"

        r2 = _run_script(
            GENERATE_SCRIPT,
            str(sample_inventory_file),
            str(results_path),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
        )
        assert r2.returncode == 0, f"stderr={r2.stderr}\nstdout={r2.stdout}"
        assert out_json.is_file()
        assert out_md.is_file()

        # Validate the generated matrix against its schema
        matrix = json.loads(out_json.read_text())
        matrix_schema = json.loads(MATRIX_SCHEMA_PATH.read_text())
        jsonschema.validate(matrix, matrix_schema)

        # Sanity checks on the matrix
        assert matrix["summary"]["total_tools"] == 4
        # httpx + json are importable AND clean-licensed
        assert matrix["summary"]["verified_runnable"] >= 2
        # The two bogus tools are indexed_only (import failed)
        assert matrix["summary"]["indexed_only"] >= 2

        # Check that gaps include 'browser' (playwright_not_installed_xyz)
        gap_domains = [g["capability_domain"] for g in matrix["summary"]["gaps"]]
        assert "browser" in gap_domains

        # Markdown must contain the header and a row per tool
        md = out_md.read_text()
        assert "# Layer-1 Capability Matrix" in md
        assert "httpx" in md
        assert "json" in md
    finally:
        results_path.unlink(missing_ok=True)


# ── ADR-0007 / decision tests ────────────────────────────────────────────────


def test_adr_0007_records_user_decisions():
    """ADR-0007 must exist and capture D-01/D-02/D-03."""
    adr_path = REPO_ROOT / "docs" / "decisions" / "0007-g01-conditional-acceptance.md"
    assert adr_path.is_file(), f"ADR-0007 missing: {adr_path}"
    text = adr_path.read_text(encoding="utf-8")
    assert "D-01" in text
    assert "D-02" in text
    assert "D-03" in text
    assert "TOOLKIT_INDEX(1).json" in text
    assert "Audit Preparation ONLY" in text


def test_no_LICENSE_file_present():
    """Per D-03: no LICENSE file may exist without explicit user approval."""
    assert not (REPO_ROOT / "LICENSE").is_file()
    assert not (REPO_ROOT / "LICENSE.md").is_file()
    assert not (REPO_ROOT / "LICENSE.txt").is_file()


def test_no_g02_implementation_code_added():
    """Per authorization: G02 implementation code must NOT exist yet.
    The only 'providers' code in src/synapse/providers/ should be the
    G01 stub from the original commit."""
    providers_init = REPO_ROOT / "src" / "synapse" / "providers" / "__init__.py"
    text = providers_init.read_text(encoding="utf-8")
    # The stub protocol is allowed; concrete provider adapters are NOT.
    assert "Protocol" in text or "protocol" in text
    # No concrete provider classes that weren't in G01
    assert "Crawl4AIProvider" not in text
    assert "PlaywrightProvider" not in text
    assert "OpenAIProvider" not in text


# ── G01 regression: must still pass ──────────────────────────────────────────


def test_g01_tests_still_pass():
    """Run a subset of G01 tests to confirm we haven't broken anything."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/domain/test_claim.py",
            "tests/unit/security/test_ssrf.py",
            "--no-cov",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "passed" in r.stdout
