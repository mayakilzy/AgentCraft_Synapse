"""Generate LAYER_1_CAPABILITY_MATRIX.json from the audit outputs.

Per ADR-0008: matrix is schema v2.0 with tiered verification.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = Path("/home/z/my-project/agentcraft/toolkit_audit_workspace/toolkit")

INV = REPO_ROOT / "docs" / "toolkit_audit" / "TOOLKIT_REPOSITORY_INVENTORY.json"
VER = REPO_ROOT / "docs" / "toolkit_audit" / "verification_results.json"
SMK = REPO_ROOT / "docs" / "toolkit_audit" / "smoke_results.json"
OUT_JSON = REPO_ROOT / "docs" / "toolkit_audit" / "LAYER_1_CAPABILITY_MATRIX.json"

# License review keywords (per ADR-0008 §3)
REVIEW_KEYWORDS = {
    "GPL": "GPL — copyleft; linking may trigger source-disclosure obligations.",
    "AGPL": "AGPL — network-use copyleft; SaaS deployments may be affected.",
    "LGPL": "LGPL — weak copyleft; dynamic linking usually OK.",
    "SSPL": "SSPL — server-side copyleft.",
    "CC-BY-NC": "Creative Commons NonCommercial — commercial use prohibited.",
    "CC-BY-SA": "Creative Commons ShareAlike — derivative works must use the same license.",
    "CC-BY-ND": "Creative Commons NoDerivatives — modifications prohibited.",
}


def license_review_required(license_text):
    if not license_text:
        return True, "License not specified — defaults to 'all rights reserved'."
    upper = license_text.upper()
    for kw, reason in REVIEW_KEYWORDS.items():
        if kw.upper() in upper:
            return True, reason
    permissive = {"MIT", "BSD", "APACHE", "ISC", "MPL", "PSF", "PYTHON", "UNLICENSE"}
    if any(p in upper for p in permissive):
        return False, None
    return True, f"Unrecognized license string {license_text!r} — review required."


def classify(*, pkg, imp, smoke, prod, lic_review):
    if lic_review:
        return "flagged_for_legal_review"
    if prod is True:
        return "production_ready"
    if smoke is True:
        return "functional_smoke_passed"
    if imp is True:
        return "import_only"
    if pkg is True:
        return "package_available_only"
    return "indexed_only"


def main():
    inventory = json.loads(INV.read_text())
    verification = json.loads(VER.read_text())
    smoke = json.loads(SMK.read_text())

    ver_by_name = {r["tool_name"]: r for r in verification["records"]}
    smk_by_name = {r["tool_name"]: r for r in smoke["records"]}

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
    domain_coverage = {}

    for entry in inventory["tools"]:
        name = entry["tool_name"]
        ver = ver_by_name.get(name, {})
        smk = smk_by_name.get(name, {})
        lic_text = entry["external_license"]
        lic_review, lic_reason = license_review_required(lic_text)

        pkg = bool(ver.get("package_available", False))
        imp = bool(ver.get("import_succeeded", False))
        smoke_code = smk.get("smoke_test_exit_code")
        smoke_passed = True if smoke_code == 0 else False if smoke_code is not None else None
        smoke_skip = smk.get("skipped_reason")
        prod = None  # manual review

        disposition = classify(
            pkg=pkg,
            imp=imp,
            smoke=smoke_passed,
            prod=prod,
            lic_review=lic_review,
        )
        # Override: tools skipped due to credentials/runtime are "deferred"
        if (
            smoke_skip
            in {"credentials_unavailable", "runtime_not_configured", "side_effects_unsafe"}
            and disposition == "import_only"
        ):
            disposition = "deferred"
        counts[disposition] += 1

        domain = entry["synapse_domain"]
        domain_coverage.setdefault(domain, []).append(
            {
                "tool_name": name,
                "disposition": disposition,
            }
        )

        evidence_parts = []
        if pkg:
            evidence_parts.append(f"package via {ver.get('package_located_via', '?')}")
        if imp:
            evidence_parts.append("import OK")
        if smoke_passed is True:
            evidence_parts.append(f"smoke OK ({(smk.get('stdout_truncated') or '')[:60]})")
        elif smoke_passed is False:
            evidence_parts.append(f"smoke FAIL ({(smk.get('stderr_truncated') or '')[:60]})")
        elif smoke_skip:
            evidence_parts.append(f"smoke skipped: {smoke_skip}")
        if lic_review:
            evidence_parts.append(f"license flagged: {lic_reason}")
        evidence = "; ".join(evidence_parts) or "indexed only"

        # Provenance preservation assessment (per ADR-0008 §4)
        # Based on tool inspection — agent's manual assessment from reading DOCS.md
        prov = {
            "source_structure_preserved": None,
            "metadata_preserved": None,
            "links_preserved": None,
            "citations_preserved": None,
            "version_preserved": None,
        }
        # Manual assessment based on tool category
        if entry["synapse_domain"] == "discovery" and "arxiv" in name:
            prov.update(
                {
                    "source_structure_preserved": True,  # Atom XML structure preserved
                    "metadata_preserved": True,  # published, updated, authors, categories
                    "links_preserved": True,  # canonical arxiv_id stored
                    "citations_preserved": True,  # arxiv_id is a stable citation
                    "version_preserved": True,  # version field explicit
                }
            )
        elif entry["synapse_domain"] == "extraction" and "trafilatura" in name:
            prov.update(
                {
                    "source_structure_preserved": True,  # article text + structure
                    "metadata_preserved": True,  # title, author, date, source
                    "links_preserved": True,  # hyperlinks in extracted text
                    "citations_preserved": False,  # trafilatura doesn't extract DOI
                    "version_preserved": True,  # date + URL preserved
                }
            )
        elif entry["synapse_domain"] == "provenance":
            prov.update(
                {
                    "source_structure_preserved": False,
                    "metadata_preserved": True,  # provenance IS metadata
                    "links_preserved": False,
                    "citations_preserved": True,
                    "version_preserved": True,
                }
            )

        rows.append(
            {
                "tool_name": name,
                "capability_domain": domain,
                "disposition": disposition,
                "package_available": pkg,
                "import_succeeded": imp,
                "functional_smoke_passed": smoke_passed,
                "functional_smoke_skipped_reason": smoke_skip,
                "production_ready": prod,
                "evidence_provenance": evidence,
                "license": lic_text,
                "license_review_required": lic_review,
                "license_review_reason": lic_reason,
                "license_integration_implications": (
                    "Subprocess isolation recommended to avoid derivative-work status."
                    if lic_review and "GPL" in (lic_text or "").upper()
                    else None
                ),
                "license_distribution_implications": (
                    "Distribution would require GPL-3.0 attribution + source-disclosure."
                    if lic_review and "GPL" in (lic_text or "").upper()
                    else None
                ),
                "version_declared": entry.get("version_declared"),
                "version_installed": ver.get("version_installed"),
                "import_command": ver.get("import_path"),
                "import_exit_code": ver.get("import_exit_code"),
                "functional_smoke_command": smk.get("smoke_test_command"),
                "functional_smoke_exit_code": smk.get("smoke_test_exit_code"),
                "functional_smoke_tested_at": smk.get("tested_at"),
                "test_count": None,
                "supported_sources": [],
                "failure_modes": [],
                "fallback": None,
                "recommended_provider": entry["synapse_provider"],
                "dependencies": [],
                "operational_risks": [],
                "provenance_preservation": prov,
                "notes": entry.get("smoke_test_command"),
                "external_repo": entry["external_repo"],
                "toolkit_path": entry["toolkit_path"],
                "priority": entry["priority"],
            }
        )

    # Detect gaps (no tool with disposition in {functional_smoke_passed,
    # production_ready, flagged_for_legal_review, adopted, deferred})
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
    strong_dispositions = {
        "functional_smoke_passed",
        "production_ready",
        "flagged_for_legal_review",
        "deferred",
        "adopted",
    }
    gaps = []
    for domain in required_domains:
        tools = domain_coverage.get(domain, [])
        has_strong = any(t["disposition"] in strong_dispositions for t in tools)
        if not has_strong:
            gaps.append(
                {
                    "capability_domain": domain,
                    "reason": f"no production-ready or smoke-passed tool for {domain}",
                    "recommended_remediation": "find_alternative" if tools else "build_new",
                }
            )

    matrix = {
        "audit_version": "1.0",
        "schema_version": "2.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "toolkit_index_source": inventory["toolkit_repo_url"]
        + " @ "
        + inventory["toolkit_commit_sha"],
        "verification_results_source": str(VER),
        "smoke_results_source": str(SMK),
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
        "decisions_required": [
            {
                "id": "D-04",
                "description": "Approve legal review for instaloader_tool (GPL-3.0) OR confirm subprocess-isolation integration pattern.",
                "blocking_for": "G02 social ingestion via Instagram",
                "user_action_required": "Decide whether to (a) approve GPL integration with subprocess isolation, (b) reject and find alternative, or (c) defer social ingestion from Instagram entirely.",
            },
            {
                "id": "D-05",
                "description": "Approve credentials provisioning for LLM providers (openai, anthropic) and authenticated APIs (twikit, scholarly, cloudscraper).",
                "blocking_for": "G02 social/scientific ingestion that needs authentication",
                "user_action_required": "Provide API keys / cookies via secure channel OR defer to a later group.",
            },
            {
                "id": "D-06",
                "description": "Approve runtime binaries installation (Playwright browsers, ChromeDriver, curl_cffi native) for browser/crawling capabilities.",
                "blocking_for": "G02 browser automation and JS-rendered crawling",
                "user_action_required": "Approve `playwright install chromium` in the Synapse dev environment OR defer browser automation to a later group.",
            },
        ],
    }

    OUT_JSON.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"Wrote: {OUT_JSON}")
    print(f"Summary: {json.dumps(matrix['summary'], indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
