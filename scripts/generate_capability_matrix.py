"""Generate the Layer-1 Capability Matrix from index + verification results.

Usage:
    python scripts/generate_capability_matrix.py \
        <TOOLKIT_INDEX.json> <verification_results.json> \
        --out-json docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json \
        --out-md   docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md

This script is invoked AFTER ``verify_tool_imports.py`` has produced
``verification_results.json``. It produces:

  1. A JSON matrix conforming to LAYER_1_CAPABILITY_MATRIX.schema.json
  2. A human-readable Markdown matrix

Classification rules:
  - indexed_only      : in inventory, but import failed (or not attempted)
  - verified_runnable : in inventory, import succeeded (exit 0), license clean
  - adopted           : verified_runnable AND manually approved by agent
  - deferred          : verified_runnable but blocked on a decision (e.g. paid API)
  - rejected          : license conflict, unmaintained, or duplicate of adopted

NOTE: ``adopted`` classification requires the agent's judgement and
explicit user approval. This script sets verified_runnable as the
ceiling; promotion to adopted is done via the PROVIDER_SELECTION.md
document.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# License substrings that conflict with proprietary distribution.
# This is a conservative list; the agent should review edge cases.
_CONFLICTING_LICENSE_KEYWORDS = (
    "GPL",
    "AGPL",
    "LGPL",
    "SSPL",
    "CC-BY-NC",
    "CC-BY-SA",
)


def _has_license_conflict(license_text: str | None) -> bool:
    if not license_text:
        return False
    upper = license_text.upper()
    return any(kw.upper() in upper for kw in _CONFLICTING_LICENSE_KEYWORDS)


def classify(
    indexed: bool,
    import_exit_code: int | None,
    license_conflict: bool,
) -> str:
    """Apply classification rules. Promotion to 'adopted' is NOT done here."""
    if not indexed:
        return "indexed_only"
    if import_exit_code != 0:
        return "indexed_only"
    if license_conflict:
        return "rejected"
    return "verified_runnable"


def build_matrix(index: dict, verification: dict) -> dict:
    """Combine index + verification into a matrix object."""
    ver_by_name: dict[str, dict] = {}
    for rec in verification.get("records", []):
        ver_by_name[rec["tool_name"]] = rec

    rows = []
    counts = {
        "indexed_only": 0,
        "verified_runnable": 0,
        "adopted": 0,
        "deferred": 0,
        "rejected": 0,
    }
    domain_coverage: dict[str, list[str]] = {}

    for cat_name, cat in index.get("categories", {}).items():
        for tool in cat.get("tools", []):
            name = tool.get("name") or tool.get("pypi") or ""
            if not name:
                continue
            ver = ver_by_name.get(name, {})
            license_text = ver.get("license") or tool.get("license")
            license_conflict = _has_license_conflict(license_text)

            classification = classify(
                indexed=True,
                import_exit_code=ver.get("import_exit_code"),
                license_conflict=license_conflict,
            )
            counts[classification] += 1

            domain = tool.get("category") or cat_name
            domain_coverage.setdefault(domain, []).append(name)

            rows.append(
                {
                    "tool_name": name,
                    "capability_domain": domain,
                    "classification": classification,
                    "evidence_provenance": (
                        f"imported via {ver.get('import_command', 'N/A')} "
                        f"(exit={ver.get('import_exit_code')})"
                        if ver
                        else "indexed only — no verification record"
                    ),
                    "license": license_text,
                    "license_conflict": license_conflict,
                    "version_declared": tool.get("version"),
                    "version_installed": ver.get("version_installed"),
                    "import_command": ver.get("import_command"),
                    "import_exit_code": ver.get("import_exit_code"),
                    "test_count": None,  # populated by manual review
                    "supported_sources": tool.get("supported_sources", []),
                    "failure_modes": [],  # populated by manual review
                    "fallback": None,  # populated by manual review
                    "recommended_provider": None,  # populated in PROVIDER_SELECTION.md
                    "dependencies": [],
                    "operational_risks": [],
                    "notes": tool.get("notes") or tool.get("description"),
                }
            )

    required_domains = {
        "discovery",
        "acquisition",
        "crawling",
        "browser",
        "extraction",
        "scientific",
        "social",
        "provenance",
    }
    gaps = []
    for domain in required_domains:
        tools = domain_coverage.get(domain, [])
        verified = any(
            r["classification"] == "verified_runnable"
            for r in rows
            if r["capability_domain"] == domain
        )
        if not verified:
            gaps.append(
                {
                    "capability_domain": domain,
                    "reason": (
                        f"no verified-runnable tool indexed for {domain} (indexed: {len(tools)})"
                    ),
                    "recommended_remediation": "find_alternative" if tools else "build_new",
                }
            )

    return {
        "audit_version": "1.0",
        "generated_at": _utcnow(),
        "toolkit_index_source": str(Path(index.get("source", "TOOLKIT_INDEX(1).json"))),
        "rows": rows,
        "summary": {
            "total_tools": len(rows),
            "indexed_only": counts["indexed_only"],
            "verified_runnable": counts["verified_runnable"],
            "adopted": counts["adopted"],
            "deferred": counts["deferred"],
            "rejected": counts["rejected"],
            "gaps": gaps,
        },
        "decisions_required": [],
    }


def render_markdown(matrix: dict) -> str:
    """Render the matrix as a Markdown table."""
    lines = [
        "# Layer-1 Capability Matrix (auto-generated)\n",
        f"- Generated: {matrix['generated_at']}",
        f"- Source: {matrix['toolkit_index_source']}\n",
        "## Summary\n",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total tools | {matrix['summary']['total_tools']} |",
        f"| Indexed-only | {matrix['summary']['indexed_only']} |",
        f"| Verified runnable | {matrix['summary']['verified_runnable']} |",
        f"| Adopted | {matrix['summary']['adopted']} |",
        f"| Deferred | {matrix['summary']['deferred']} |",
        f"| Rejected | {matrix['summary']['rejected']} |",
        f"| Capability gaps | {len(matrix['summary']['gaps'])} |",
        "",
        "## Matrix\n",
        "| Tool | Domain | Classification | License | License conflict? | "
        "Version (declared/installed) | Import verified? | Notes |",
        "|------|--------|----------------|---------|-------------------|"
        "------------------------------|------------------|-------|",
    ]
    for r in matrix["rows"]:
        license_str = r["license"] or "?"
        conflict = "yes" if r["license_conflict"] else "no"
        verified = "yes" if r["import_exit_code"] == 0 else "no"
        version = f"{r['version_declared'] or '?'}/{r['version_installed'] or '?'}"
        lines.append(
            f"| {r['tool_name']} | {r['capability_domain']} | "
            f"{r['classification']} | {license_str} | {conflict} | "
            f"{version} | {verified} | {r['notes'] or ''} |"
        )
    lines.append("")
    if matrix["summary"]["gaps"]:
        lines.append("## Capability gaps\n")
        lines.append("| Domain | Reason | Recommended remediation |")
        lines.append("|--------|--------|--------------------------|")
        for g in matrix["summary"]["gaps"]:
            lines.append(
                f"| {g['capability_domain']} | {g['reason']} | {g['recommended_remediation']} |"
            )
        lines.append("")
    lines.append(
        "## STOP\n\n"
        "This matrix is auto-generated evidence for the G02 audit. "
        "Promotion of any tool to `adopted` requires explicit user approval "
        "via `PROVIDER_SELECTION.md`.\n"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("index", help="Path to TOOLKIT_INDEX.json")
    ap.add_argument("verification", help="Path to verification_results.json")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    verification = json.loads(Path(args.verification).read_text(encoding="utf-8"))

    matrix = build_matrix(index, verification)
    Path(args.out_json).write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(matrix), encoding="utf-8")

    print(f"Wrote JSON: {args.out_json}")
    print(f"Wrote MD:   {args.out_md}")
    print(f"\nSummary: {matrix['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
