#!/usr/bin/env python3
"""AgentCraft Synapse -- minimal external HTTP client example.

Per the user's G04-T05 mission briefing §6 (Minimal external client example):

  "Provide one small, practical Python HTTP client example using the
   existing supported dependencies or standard library."

  "Demonstrate:
    - Sending a retrieval query.
    - Requesting grounded reasoning.
    - Reading structured findings.
    - Extracting citation references.
    - Handling HTTP and validation errors."

  "The example must use actual existing API contracts."

  "Do not build a distributable SDK, CLI framework, generated client
   package or separate service."

This script demonstrates how an external application (which imports
NOTHING from `synapse`) consumes the public HTTP API.

It uses ``httpx`` (already a Synapse dependency) — no new packages
required.

Usage (against a running uvicorn server):

    python examples/client_g04.py \\
        --base-url http://127.0.0.1:8000 \\
        --api-key "$SYNAPSE_API_KEY"

Or programmatically:

    from client_g04 import main
    main(base_url="http://127.0.0.1:8000", api_key="...")

Exit code:
    0  — success
    1  — at least one step failed (HTTP or validation error)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

# ── Step 1: send a retrieval query ─────────────────────────────────────────


def retrieve_knowledge(
    client: httpx.Client,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """POST /api/v1/knowledge/retrieve — hybrid retrieval.

    Returns the structured response envelope:
        {
            "data": {"claims": [...], "relationships": [...], ...},
            "meta": {"request_id": ..., "pagination": ...},
            "error": null,
        }
    """
    r = client.post(
        "/api/v1/knowledge/retrieve",
        json={"query": query, "limit": limit},
    )
    r.raise_for_status()
    return r.json()


# ── Step 2: request grounded reasoning ────────────────────────────────────


def answer_reasoning_query(
    client: httpx.Client,
    *,
    query: str,
    candidate_entity_ids: list[str] | None = None,
    context: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """POST /api/v1/reasoning/queries — evidence-grounded reasoning.

    Returns the structured reasoning answer with findings, cited claims,
    evidence chain, unknowns, contradictions, and confidence bounds.
    """
    body: dict[str, Any] = {"query": query, "limit": limit}
    if candidate_entity_ids:
        body["candidate_entity_ids"] = candidate_entity_ids
    if context:
        body["context"] = context
    r = client.post("/api/v1/reasoning/queries", json=body)
    r.raise_for_status()
    return r.json()


# ── Step 3: read structured findings ───────────────────────────────────────


def extract_findings(reasoning_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the findings list from a reasoning response.

    Each finding has:
        - text: human-readable summary
        - type: "documented_fact" | "derived_finding" | "hypothesis" | "unknown"
        - sources: list of capability IDs (or entity/relationship IDs)
        - evidence_refs: list of evidence fragment IDs
        - confidence: float in [0.0, 0.90] (capped; VERIFIED unreachable)
        - caveat: optional explanation
    """
    data = reasoning_response.get("data") or {}
    return data.get("findings") or []


def group_findings_by_type(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group findings by type so the caller can distinguish:

    - documented_fact: directly supported by source evidence
    - derived_finding: follows from documented relationships + rules
    - hypothesis: plausible but not established by evidence
    - unknown: insufficient information
    """
    groups: dict[str, list[dict[str, Any]]] = {
        "documented_fact": [],
        "derived_finding": [],
        "hypothesis": [],
        "unknown": [],
    }
    for f in findings:
        ftype = f.get("type", "unknown")
        groups.setdefault(ftype, []).append(f)
    return groups


# ── Step 4: extract citation references ──────────────────────────────────


def extract_citation_chain(reasoning_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the evidence chain from a reasoning response.

    Each link in the chain traces:
        cited_claim_id → evidence_fragment_id → source_span → source_uri

    A reviewer can follow the chain from the answer text back to the
    exact source text at the exact character offset.
    """
    data = reasoning_response.get("data") or {}
    return data.get("evidence_chain") or []


def extract_unknowns(reasoning_response: dict[str, Any]) -> list[str]:
    """Extract the unknowns list — what the system could NOT find.

    Per mission §4: "External consumers must be able to distinguish
    documented facts, derived findings, hypotheses, unknowns,
    applicable contradictions, out-of-context contradictions, and
    evidence limitations."

    Unknowns are reported honestly — absence of evidence is NOT
    evidence of absence.
    """
    data = reasoning_response.get("data") or {}
    return data.get("unknowns") or []


def extract_contradictions(reasoning_response: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the contradictions list — applicable conflicting evidence
    that was preserved (not suppressed).

    Per G04-T03C: out-of-context contradictions are preserved as
    ``out_of_context_contradictions`` metadata inside each finding's
    caveat, not in this top-level ``contradictions`` list.
    """
    data = reasoning_response.get("data") or {}
    return data.get("contradictions") or []


# ── Step 5: handle HTTP and validation errors ─────────────────────────────


def handle_error(response: httpx.Response) -> None:
    """Raise a structured error for non-2xx responses.

    Synapse returns ``application/problem+json`` for all errors, with
    a standard envelope:

        {
            "data": null,
            "meta": {"request_id": ..., "api_version": "v1", ...},
            "error": {
                "code": "validation_error" | "not_found" | "not_implemented" | ...,
                "message": "...",
                "details": [...] | {...} | null,
                "retryable": false,
                "trace_id": "..."
            }
        }
    """
    try:
        body = response.json()
    except Exception:
        response.raise_for_status()
        return
    error = body.get("error") or {}
    code = error.get("code", "http_error")
    message = error.get("message", response.text)
    request_id = (body.get("meta") or {}).get("request_id", "?")
    print(
        f"HTTP {response.status_code} {code}: {message} (request_id={request_id})",
        file=sys.stderr,
    )
    if error.get("details"):
        print(f"  details: {json.dumps(error['details'], indent=2)}", file=sys.stderr)
    response.raise_for_status()


# ── Demo entry point ──────────────────────────────────────────────────────


def main(base_url: str, api_key: str) -> bool:
    """Run the full demonstration against a Synapse instance.

    Returns True on success, False on failure.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    success = True
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
            print("=" * 72)
            print("AgentCraft Synapse -- External Client Demo")
            print("=" * 72)

            # 1. Retrieval
            print("\n[1] POST /api/v1/knowledge/retrieve  (query='synapse')")
            try:
                resp = retrieve_knowledge(client, query="synapse", limit=10)
                data = resp.get("data") or {}
                print(f"    intent: {data.get('query_intent')}")
                print(f"    claims: {len(data.get('claims', []))}")
                print(f"    relationships: {len(data.get('relationships', []))}")
                print(f"    entities: {len(data.get('entities', []))}")
                print(f"    unknowns: {data.get('unknowns', [])}")
            except httpx.HTTPStatusError as exc:
                handle_error(exc.response)
                success = False

            # 2. Reasoning
            print("\n[2] POST /api/v1/reasoning/queries  (query='What can synapse do?')")
            try:
                resp = answer_reasoning_query(
                    client,
                    query="What can synapse do?",
                    candidate_entity_ids=["ent-synapse"],
                    context="AI agent systems",
                    limit=20,
                )
                findings = extract_findings(resp)
                groups = group_findings_by_type(findings)
                print(f"    intent: {(resp.get('data') or {}).get('intent')}")
                print(f"    findings: {len(findings)} total")
                print(f"      documented_fact:  {len(groups['documented_fact'])}")
                print(f"      derived_finding:  {len(groups['derived_finding'])}")
                print(f"      hypothesis:       {len(groups['hypothesis'])}")
                print(f"      unknown:          {len(groups['unknown'])}")

                # 3. Citation chain
                chain = extract_citation_chain(resp)
                print(f"    evidence_chain: {len(chain)} link(s)")
                for link in chain[:3]:
                    print(
                        f"      fragment={link.get('fragment_id')} "
                        f"source_uri={link.get('source_uri')} "
                        f"spans={len(link.get('spans') or [])}"
                    )

                # 4. Unknowns
                unknowns = extract_unknowns(resp)
                print(f"    unknowns: {len(unknowns)} item(s) reported honestly")

                # 5. Contradictions (preserved, not suppressed)
                contradictions = extract_contradictions(resp)
                print(f"    contradictions: {len(contradictions)} preserved pair(s)")

                # Confidence bounds
                conf = (resp.get("data") or {}).get("confidence") or {}
                print(f"    overall confidence: {conf.get('overall')}")
                print(f"    policy_version: {conf.get('policy_version')}")
            except httpx.HTTPStatusError as exc:
                handle_error(exc.response)
                success = False

            # 5. Validation error handling demo
            print("\n[3] Validation error demo  (empty query)")
            try:
                client.post(
                    "/api/v1/knowledge/retrieve",
                    json={"query": ""},  # min_length=1 violation
                )
            except httpx.HTTPStatusError as exc:
                # Expected: 422 + application/problem+json
                print(f"    status: {exc.response.status_code} (expected 422)")
                print(f"    content-type: {exc.response.headers.get('content-type')}")
                body = exc.response.json()
                err = body.get("error") or {}
                print(f"    error.code: {err.get('code')}")
                print(f"    error.message: {err.get('message')}")
                if err.get("details"):
                    print(f"    error.details: {json.dumps(err['details'], indent=2)[:300]}")

            print("\n" + "=" * 72)
            print("Demo complete.")
            print("=" * 72)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        success = False
    return success


# ── CLI entry ──────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AgentCraft Synapse external client demo (G04-T05).",
    )
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Synapse base URL (default: http://127.0.0.1:8000).",
    )
    p.add_argument(
        "--api-key",
        default="",
        help="API key (Bearer token). Required for all /api/v1 endpoints.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if not args.api_key:
        print("ERROR: --api-key is required.", file=sys.stderr)
        sys.exit(2)
    ok = main(base_url=args.base_url, api_key=args.api_key)
    sys.exit(0 if ok else 1)
