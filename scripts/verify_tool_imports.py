"""Verify which indexed tools are actually importable in this environment.

Usage:
    python scripts/verify_tool_imports.py <path-to-TOOLKIT_INDEX.json>

Output:
    Writes ``docs/toolkit_audit/verification_results.json`` with one
    record per indexed tool, capturing:
      - import name
      - declared version
      - installed version (via importlib.metadata if found)
      - import command attempted
      - exit code (0 = importable, non-zero = failed)
      - error message (if failed)
      - license (from importlib.metadata if discoverable)

Exit codes:
    0 — at least one tool verified importable (does not require all)
    1 — index file missing or invalid
    2 — no tools in index (cannot verify anything)

This script does NOT mark any tool as "verified runnable" in the matrix.
The matrix generator consumes this output and applies additional checks
(license, tests, failure modes) before assigning that classification.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "docs" / "toolkit_audit" / "verification_results.json"


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _try_import(tool_name: str) -> dict:
    """Attempt to import the tool. Returns a verification record."""
    record = {
        "tool_name": tool_name,
        "import_command": f'python -c "import {tool_name}"',
        "import_exit_code": None,
        "error": None,
        "version_declared": None,
        "version_installed": None,
        "license": None,
    }
    try:
        mod = importlib.import_module(tool_name)
        record["import_exit_code"] = 0
        # Try to get installed version + license via importlib.metadata
        try:
            dist = importlib.metadata.distribution(tool_name)
            record["version_installed"] = dist.version
            record["license"] = dist.metadata.get("License") or None
        except importlib.metadata.PackageNotFoundError:
            # Tool imported but is not a pip package — could be a stdlib
            # module or a namespace package. Note this.
            record["license"] = "(not a pip package)"
        # Some tools expose __version__ on the module
        if hasattr(mod, "__version__") and not record["version_installed"]:
            record["version_installed"] = str(mod.__version__)
    except ImportError as exc:
        record["import_exit_code"] = 1
        record["error"] = f"ImportError: {exc}"
    except Exception as exc:
        record["import_exit_code"] = 2
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def verify(index_path: Path) -> int:
    if not index_path.is_file():
        print(f"ERROR: file not found: {index_path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON: {exc}", file=sys.stderr)
        return 1

    categories = data.get("categories", {})
    if not categories:
        print("ERROR: no categories in index", file=sys.stderr)
        return 2

    results = {
        "audit_version": "1.0",
        "generated_at": _utcnow(),
        "toolkit_index_source": str(index_path),
        "python_version": sys.version.split()[0],
        "records": [],
        "summary": {"total": 0, "importable": 0, "failed": 0},
    }

    for cat_name, cat in categories.items():
        for tool in cat.get("tools", []):
            name = tool.get("name") or tool.get("pypi") or ""
            if not name:
                continue
            record = _try_import(name)
            record["category"] = cat_name
            record["version_declared"] = tool.get("version")
            results["records"].append(record)
            results["summary"]["total"] += 1
            if record["import_exit_code"] == 0:
                results["summary"]["importable"] += 1
            else:
                results["summary"]["failed"] += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\nVerification results written to: {OUTPUT_PATH}")
    print(f"Total tools: {results['summary']['total']}")
    print(f"Importable:  {results['summary']['importable']}")
    print(f"Failed:      {results['summary']['failed']}")
    return 0 if results["summary"]["total"] > 0 else 2


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: python scripts/verify_tool_imports.py <path-to-TOOLKIT_INDEX.json>",
            file=sys.stderr,
        )
        return 2
    return verify(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
