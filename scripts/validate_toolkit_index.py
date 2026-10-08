"""Validate TOOLKIT_INDEX(1).json against the inventory schema.

Usage:
    python scripts/validate_toolkit_index.py <path-to-TOOLKIT_INDEX.json>

Exit codes:
    0 — valid
    1 — schema validation failed
    2 — file not found / IO error
    3 — schema file missing (environment issue)

This script does NOT verify runtime fitness — it only validates that the
inventory's STRUCTURE matches the schema. Runtime verification is done
by ``scripts/verify_tool_imports.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "docs" / "toolkit_audit" / "TOOLKIT_INDEX_SCHEMA.json"


def _try_import_jsonschema() -> None:
    try:
        import jsonschema  # noqa: F401
    except ImportError as exc:
        print(
            "ERROR: jsonschema is not installed. Install with: pip install jsonschema",
            file=sys.stderr,
        )
        raise SystemExit(3) from exc


def validate(index_path: Path) -> int:
    _try_import_jsonschema()
    import jsonschema

    if not index_path.is_file():
        print(f"ERROR: file not found: {index_path}", file=sys.stderr)
        return 2
    if not SCHEMA_PATH.is_file():
        print(f"ERROR: schema not found: {SCHEMA_PATH}", file=sys.stderr)
        return 3

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {index_path}: {exc}", file=sys.stderr)
        return 1

    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        print(f"FAIL: schema validation error in {index_path}", file=sys.stderr)
        print(f"  Path:    {' -> '.join(str(p) for p in exc.absolute_path) or '(root)'}")
        print(f"  Message: {exc.message}")
        return 1

    # Summary
    categories = data.get("categories", {})
    total = 0
    for cat_name, cat in categories.items():
        n = len(cat.get("tools", []))
        total += n
        print(f"  category: {cat_name:30s}  tools: {n}")
    print(f"\nPASS: {index_path.name} is valid.")
    print(f"  Categories: {len(categories)}")
    print(f"  Total tools: {total}")
    if "total_tools" in data and data["total_tools"] != total:
        print(f"  WARNING: declared total_tools={data['total_tools']} != actual={total}")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_toolkit_index.py <path>", file=sys.stderr)
        return 2
    return validate(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
