"""Generate the Layer-1 Capability Matrix from index + verification + smoke results.

Per ADR-0008: the matrix uses a tiered verification model with FOUR
cumulative boolean flags (package_available, import_succeeded,
functional_smoke_passed, production_ready) plus a derived `disposition`.

License conflicts are NOT auto-rejected — they are flagged for legal
review with documented integration and distribution implications.

Usage:
    python scripts/generate_capability_matrix.py \\
        <TOOLKIT_INDEX.json> \\
        <verification_results.json> \\
        [<smoke_results.json>] \\
        --out-json docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.json \\
        --out-md   docs/toolkit_audit/LAYER_1_CAPABILITY_MATRIX.md

This script is invoked AFTER ``verify_tool_imports.py`` (and optionally
``functional_smoke_tests.py``) have produced their JSON outputs.

`adopted` disposition is NOT set by this script — promotion to adopted
requires the agent's manual review and the user's explicit approval
via PROVIDER_SELECTION.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# License substrings that require legal review (per ADR-0008 §3).
# Not auto-rejected — flagged for compatibility and legal review.
REVIEW_REQUIRED_LICENSE_KEYWORDS: dict[str, str] = {
    "GPL": "GPL — copyleft; linking may trigger source-disclosure obligations.",
    "AGPL": "AGPL — network-use copyleft; SaaS deployments may be affected.",
    "LGPL": "LGPL — weak copyleft; dynamic linking usually OK, static linking may require disclosure.",
    "SSPL": "SSPL — server-side copyleft; offering Synapse as a service may trigger disclosure.",
    "CC-BY-NC": "Creative Commons NonCommercial — commercial use prohibited without separate license.",
    "CC-BY-SA": "Creative Commons ShareAlike — derivative works must use the same license.",
    "CC-BY-ND": "Creative Commons NoDerivatives — modifications prohibited.",
}


def _license_review_required(license_text: str | None) -> tuple[bool, str | None]:
    """Return (review_required, reason)."""
    if not license_text:
        # No license info at all — that itself is a review flag.
        return (
            True,
            "License not specified — defaults to 'all rights reserved' under most jurisdictions.",
        )
    upper = license_text.upper()
    for keyword, reason in REVIEW_REQUIRED_LICENSE_KEYWORDS.items():
        if keyword.upper() in upper:
            return True, reason
    # Recognized permissive licenses — no review required.
    permissive = {"MIT", "BSD", "APACHE", "APACHE-2.0", "ISC", "MPL", "PSF", "PYTHON"}
    if any(p in upper for p in permissive):
        return False, None
    # Unknown license — flag for review.
    return True, f"Unrecognized license string {license_text!r} — review required."


def _classify(
    *,
    package_available: bool,
    import_succeeded: bool,
    functional_smoke_passed: bool | None,
    production_ready: bool | None,
    license_review_required: bool,
) -> str:
    """Derive the disposition from the four verification flags + license
    review status. Promotion to `adopted` is NOT done here — that
    requires manual review and user approval."""
    # License review flag dominates if everything else is fine but the
    # license needs review.
    if license_review_required:
        # Still record the verification stage reached — but disposition
        # is "flagged_for_legal_review" until user legal approves.
        return "flagged_for_legal_review"
    if production_ready is True:
        return "production_ready"
    if functional_smoke_passed is True:
        return "functional_smoke_passed"
    if import_succeeded is True:
        # import_only — but if smoke was attempted and FAILED, that's
        # still import_only (not functional_smoke_passed).
        return "import_only"
    if package_available is True:
        return "package_available_only"
    return "indexed_only"


def _empty_provenance() -> dict[str, bool | None]:
    return {
        "source_structure_preserved": None,
        "metadata_preserved": None,
        "links_preserved": None,
        "citations_preserved": None,
        "version_preserved": None,
    }


def build_matrix(
    index: dict,
    verification: dict,
    smoke: dict | None,
) -> dict:
    """Combine index + verification + smoke into a matrix object."""
    ver_by_name: dict[str, dict] = {}
    for rec in verification.get("records", []):
        ver_by_name[rec["tool_name"]] = rec

    smoke_by_name: dict[str, dict] = {}
    if smoke:
        for rec in smoke.get("records", []):
            smoke_by_name[rec["tool_name"]] = rec

    rows = []
    counts = {
        "indexed_only": 0,
        "package_available_only": 0,
        "import_only": 0,
        "functional_smoke_passed": 0,
        "production_ready": 0,
        "flagged_for_legal_review": 0,
        "deferred": 0,
        "rejected": 0,
        "adopted": 0,
    }
    domain_coverage: dict[str, list[dict]] = {}

    for cat_name, cat in index.get("categories", {}).items():
        for tool in cat.get("tools", []):
            name = tool.get("name") or tool.get("pypi") or ""
            if not name:
                continue
            ver = ver_by_name.get(name, {})
            smk = smoke_by_name.get(name, {})
            license_text = ver.get("license") or tool.get("license")
            lic_review, lic_reason = _license_review_required(license_text)

            package_available = bool(ver.get("package_available", False))
            import_succeeded = bool(ver.get("import_succeeded", False))
            smoke_exit = smk.get("smoke_test_exit_code")
            smoke_passed = True if smoke_exit == 0 else False if smoke_exit is not None else None
            smoke_skipped_reason = smk.get("skipped_reason")
            # production_ready is set manually in the audit report; not
            # auto-derived here.
            production_ready = None

            disposition = _classify(
                package_available=package_available,
                import_succeeded=import_succeeded,
                functional_smoke_passed=smoke_passed,
                production_ready=production_ready,
                license_review_required=lic_review,
            )
            counts[disposition] += 1

            domain = tool.get("category") or cat_name
            domain_coverage.setdefault(domain, []).append(
                {
                    "tool_name": name,
                    "disposition": disposition,
                }
            )

            evidence_parts = []
            if package_available:
                evidence_parts.append(f"package located via {ver.get('package_located_via', '?')}")
            if import_succeeded:
                evidence_parts.append(f"import OK ({ver.get('import_command', 'N/A')})")
            if smoke_passed is True:
                evidence_parts.append(f"smoke OK ({smk.get('smoke_test_command', 'N/A')[:80]})")
            elif smoke_passed is False:
                evidence_parts.append(f"smoke FAILED exit={smoke_exit}")
            elif smoke_skipped_reason:
                evidence_parts.append(f"smoke skipped: {smoke_skipped_reason}")
            else:
                evidence_parts.append("smoke not shortlisted")
            if lic_review:
                evidence_parts.append(f"license flagged: {lic_reason}")
            evidence = "; ".join(evidence_parts) or "indexed only"

            rows.append(
                {
                    "tool_name": name,
                    "capability_domain": domain,
                    "disposition": disposition,
                    "package_available": package_available,
                    "import_succeeded": import_succeeded,
                    "functional_smoke_passed": smoke_passed,
                    "functional_smoke_skipped_reason": smoke_skipped_reason,
                    "production_ready": production_ready,
                    "evidence_provenance": evidence,
                    "license": license_text,
                    "license_review_required": lic_review,
                    "license_review_reason": lic_reason,
                    "license_integration_implications": None,  # manual review
                    "license_distribution_implications": None,  # manual review
                    "version_declared": tool.get("version"),
                    "version_installed": ver.get("version_installed"),
                    "import_command": ver.get("import_command"),
                    "import_exit_code": ver.get("import_exit_code"),
                    "functional_smoke_command": smk.get("smoke_test_command"),
                    "functional_smoke_exit_code": smk.get("smoke_test_exit_code"),
                    "functional_smoke_tested_at": smk.get("tested_at"),
                    "test_count": None,  # populated by manual review
                    "supported_sources": tool.get("supported_sources", []),
                    "failure_modes": [],  # populated by manual review
                    "fallback": None,  # populated by manual review
                    "recommended_provider": None,  # populated in PROVIDER_SELECTION.md
                    "dependencies": [],
                    "operational_risks": [],
                    "provenance_preservation": _empty_provenance(),
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
        # A gap means no tool with disposition in {functional_smoke_passed,
        # production_ready, flagged_for_legal_review, adopted}.
        strong_dispositions = {
            "functional_smoke_passed",
            "production_ready",
            "flagged_for_legal_review",
            "adopted",
        }
        has_strong = any(t["disposition"] in strong_dispositions for t in tools)
        if not has_strong:
            gaps.append(
                {
                    "capability_domain": domain,
                    "reason": (
                        f"no production-ready or smoke-passed tool indexed "
                        f"for {domain} (indexed: {len(tools)})"
                    ),
                    "recommended_remediation": "find_alternative" if tools else "build_new",
                }
            )

    return {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": _utcnow(),
        "toolkit_index_source": str(Path(index.get("source", "TOOLKIT_INDEX(1).json"))),
        "verification_results_source": str(
            Path(verification.get("verification_results_source", ""))
            if False
            else verification.get("toolkit_index_source", "")
        ),
        "smoke_results_source": (
            str(Path(smoke.get("verification_results_source", ""))) if smoke else None
        ),
        "rows": rows,
        "summary": {
            "total_tools": len(rows),
            "indexed_only": counts["indexed_only"],
            "package_available_only": counts["package_available_only"],
            "import_only": counts["import_only"],
            "functional_smoke_passed": counts["functional_smoke_passed"],
            "production_ready": counts["production_ready"],
            "flagged_for_legal_review": counts["flagged_for_legal_review"],
            "deferred": counts["deferred"],
            "rejected": counts["rejected"],
            "adopted": counts["adopted"],
            "gaps": gaps,
        },
        "decisions_required": [],
    }


def render_markdown(matrix: dict) -> str:
    """Render the matrix as a Markdown table."""
    lines = [
        "# Layer-1 Capability Matrix (auto-generated)\n",
        f"- Generated: {matrix['generated_at']}",
        f"- Schema version: {matrix['schema_version']}",
        f"- Source: {matrix['toolkit_index_source']}\n",
        "## Summary\n",
        "| Metric | Count |",
        "|--------|-------|",
        f"| Total tools | {matrix['summary']['total_tools']} |",
        f"| indexed_only | {matrix['summary']['indexed_only']} |",
        f"| package_available_only | {matrix['summary']['package_available_only']} |",
        f"| import_only | {matrix['summary']['import_only']} |",
        f"| functional_smoke_passed | {matrix['summary']['functional_smoke_passed']} |",
        f"| production_ready | {matrix['summary']['production_ready']} |",
        f"| flagged_for_legal_review | {matrix['summary']['flagged_for_legal_review']} |",
        f"| deferred | {matrix['summary']['deferred']} |",
        f"| rejected | {matrix['summary']['rejected']} |",
        f"| adopted | {matrix['summary']['adopted']} |",
        f"| Capability gaps | {len(matrix['summary']['gaps'])} |",
        "",
        "## Matrix\n",
        "| Tool | Domain | Disposition | pkg? | import? | smoke? | prod? | "
        "License | Lic review? | Provenance | Notes |",
        "|------|--------|-------------|------|---------|--------|--------|"
        "---------|-----------|-----------|-------|",
    ]
    for r in matrix["rows"]:
        license_str = r["license"] or "?"
        lic_review = "yes" if r["license_review_required"] else "no"
        prov = r["provenance_preservation"]
        # Show provenance summary as N/5 where N = non-null true booleans
        prov_true = sum(1 for v in prov.values() if v is True)
        prov_total = sum(1 for v in prov.values() if v is not None)
        prov_str = f"{prov_true}/{prov_total}" if prov_total else "—"
        smoke_str = (
            "✓"
            if r["functional_smoke_passed"] is True
            else "✗"
            if r["functional_smoke_passed"] is False
            else (r["functional_smoke_skipped_reason"] or "—")[:20]
        )
        prod_str = "✓" if r["production_ready"] is True else "—"
        lines.append(
            f"| {r['tool_name']} | {r['capability_domain']} | "
            f"{r['disposition']} | "
            f"{'✓' if r['package_available'] else '✗'} | "
            f"{'✓' if r['import_succeeded'] else '✗'} | "
            f"{smoke_str} | {prod_str} | "
            f"{license_str} | {lic_review} | {prov_str} | "
            f"{r['notes'] or ''} |"
        )

    if matrix["summary"]["gaps"]:
        lines.append("\n## Capability gaps\n")
        lines.append("| Domain | Reason | Recommended remediation |")
        lines.append("|--------|--------|--------------------------|")
        for g in matrix["summary"]["gaps"]:
            lines.append(
                f"| {g['capability_domain']} | {g['reason']} | {g['recommended_remediation']} |"
            )

    if any(r["license_review_required"] for r in matrix["rows"]):
        lines.append("\n## Flagged for legal review\n")
        lines.append(
            "The following tools require legal review before adoption. "
            "Per ADR-0008 §3, they are NOT auto-rejected — they are "
            "flagged so the user can decide whether to approve "
            "integration. See `PROVIDER_SELECTION.md` for the full "
            "integration & distribution implications.\n"
        )
        lines.append("| Tool | License | Reason |")
        lines.append("|------|---------|--------|")
        for r in matrix["rows"]:
            if r["license_review_required"]:
                lines.append(
                    f"| {r['tool_name']} | {r['license'] or '?'} | "
                    f"{r['license_review_reason'] or '(unspecified)'} |"
                )

    lines.append(
        "\n## STOP\n\n"
        "This matrix is auto-generated evidence for the G02 audit. "
        "Promotion of any tool to `adopted` requires explicit user approval "
        "via `PROVIDER_SELECTION.md`. License-flagged tools require "
        "separate legal review per ADR-0008 §3.\n"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("index", help="Path to TOOLKIT_INDEX.json")
    ap.add_argument("verification", help="Path to verification_results.json")
    ap.add_argument(
        "smoke",
        nargs="?",
        default=None,
        help="Path to smoke_results.json (optional)",
    )
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    verification = json.loads(Path(args.verification).read_text(encoding="utf-8"))
    smoke = None
    if args.smoke:
        smoke = json.loads(Path(args.smoke).read_text(encoding="utf-8"))

    matrix = build_matrix(index, verification, smoke)
    Path(args.out_json).write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    Path(args.out_md).write_text(render_markdown(matrix), encoding="utf-8")

    print(f"Wrote JSON: {args.out_json}")
    print(f"Wrote MD:   {args.out_md}")
    print(f"\nSummary: {json.dumps(matrix['summary'], indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
