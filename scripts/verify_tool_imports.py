"""Verify which indexed tools are actually importable in this environment.

Per ADR-0008 §1: distinguish FOUR verification stages:
  1. package_available   — the package can be located
  2. import_succeeded   — `import <tool>` exited 0
  3. functional_smoke   — actual functional test (handled by
                           ``scripts/functional_smoke_tests.py``)
  4. production_ready    — production-readiness checklist (manual review)

This script covers stages 1 and 2 only. It writes
``docs/toolkit_audit/verification_results.json`` with one record per
indexed tool. Stage 3 and stage 4 are produced by separate scripts and
manual review, respectively.

Usage:
    python scripts/verify_tool_imports.py <path-to-TOOLKIT_INDEX.json>

Exit codes:
    0 — at least one tool verified importable (does not require all)
    1 — index file missing or invalid
    2 — no tools in index (cannot verify anything)
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


def _check_package_available(tool_name: str) -> tuple[bool, str | None]:
    """Stage 1: can we locate the package?

    Tries, in order:
      1. importlib.metadata.distribution(tool_name) — pip-installed package
      2. importlib.util.find_spec(tool_name) — any importable module
         (stdlib, namespace package, toolkit source on sys.path)
    Returns (found, where_found_or_None).
    """
    import importlib.util as importlib_util

    # Try pip-installed package first
    try:
        importlib.metadata.distribution(tool_name)
        return True, "pip"
    except importlib.metadata.PackageNotFoundError:
        pass
    # Try find_spec — catches stdlib modules, namespace packages, and
    # any module discoverable on sys.path (e.g. toolkit source tree).
    try:
        spec = importlib_util.find_spec(tool_name)
        if spec is not None:
            return True, "module-spec"
    except (ValueError, ModuleNotFoundError):
        pass
    return False, None


def _try_import(tool_name: str) -> dict:
    """Stage 2: attempt to import the tool. Returns a verification record."""
    record = {
        "tool_name": tool_name,
        "package_available": False,
        "package_located_via": None,
        "import_succeeded": False,
        "import_command": f'python -c "import {tool_name}"',
        "import_exit_code": None,
        "error": None,
        "version_declared": None,
        "version_installed": None,
        "license": None,
    }

    # Stage 1: locate the package
    found, located_via = _check_package_available(tool_name)
    record["package_available"] = found
    record["package_located_via"] = located_via

    if not found:
        # No point attempting the import — we already know it'll fail.
        record["import_exit_code"] = 1
        record["error"] = "package not found via pip or module spec"
        return record

    # Stage 2: attempt the actual import
    try:
        mod = importlib.import_module(tool_name)
        record["import_succeeded"] = True
        record["import_exit_code"] = 0
        # Try to enrich with installed version + license
        try:
            dist = importlib.metadata.distribution(tool_name)
            record["version_installed"] = dist.version
            license_text = dist.metadata.get("License")
            record["license"] = license_text if license_text else None
        except importlib.metadata.PackageNotFoundError:
            # Module imports but is not a pip package (stdlib or toolkit source).
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
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "toolkit_index_source": str(index_path),
        "python_version": sys.version.split()[0],
        "records": [],
        "summary": {
            "total": 0,
            "package_available": 0,
            "import_succeeded": 0,
            "import_failed": 0,
        },
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
            if record["package_available"]:
                results["summary"]["package_available"] += 1
            if record["import_succeeded"]:
                results["summary"]["import_succeeded"] += 1
            else:
                results["summary"]["import_failed"] += 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"\nVerification results written to: {OUTPUT_PATH}")
    print(f"Total tools:        {results['summary']['total']}")
    print(f"Package available:  {results['summary']['package_available']}")
    print(f"Import succeeded:   {results['summary']['import_succeeded']}")
    print(f"Import failed:      {results['summary']['import_failed']}")
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
