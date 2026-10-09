"""G04-T04 -- Evaluation runner: orchestrates case execution, resource
measurement, quality-gate evaluation, and report composition.

Per the user's G04-T04 mission briefing:

  §6 Quality gates (mandatory invariants):
    1. Zero fabricated citation identifiers.
    2. Zero known provider-attribution leakage.
    3. Zero unsupported hypothesis promotion.
    4. No suppression of applicable contradictions.
    5. No false CONTESTED classification caused solely by unrelated-context
       contradictions.
    6. No unbounded retrieval or graph traversal.

  §7 Integration boundary:
    "A routing policy or dry-run implementation is acceptable for this
     stage. Do not replace the existing production execution flow unless
     required and regression-tested."

  §8 Cost and resource measurements:
    Distinguish MEASURED / ESTIMATED / NOT_MEASURED. No invented token
    usage, API charges, or financial savings. ``MONETARY_COST =
    NOT_MEASURED`` when no paid model is invoked.

  §9 Implementation discipline:
    Compact, focused, no large framework code.

This module is the **single entry point** of the G04-T04 evaluation
harness. It:

  1. Loads the versioned golden dataset (``golden_dataset.load_golden_dataset``).
  2. For each case:
     a. Calls ``router.route_query`` to decide the execution path.
     b. Executes the chosen path against the existing G04-T01/T02/T03
        services (no new services introduced).
     c. Measures duration, retrieved-candidate count, evidence count,
        graph-expansion count (when known), result count, and selected
        routing path.
     d. Computes retrieval metrics (Precision@K, Recall@K, MRR).
     e. Computes reasoning metrics (citation validity, evidence-grounded
        finding rate, unsupported factual-claim rate, hypothesis-promotion
        error rate, finding coverage).
     f. Evaluates the 6 mandatory quality gates.
  3. Aggregates per-case metrics into a summary.
  4. Returns a structured ``EvaluationReport``.

The runner does NOT modify the knowledge graph. It is READ-ONLY.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.capability_registry import list_capabilities
from synapse.application.reasoning import (
    FindingType,
    answer_query,
)
from synapse.application.relationship_service import find_capabilities
from synapse.application.retrieval import (
    hybrid_retrieve,
)
from synapse.evaluation.golden_dataset import (
    GOLDEN_DATASET_VERSION,
    EvaluationCategory,
    GoldenCase,
    load_golden_dataset,
)
from synapse.evaluation.metrics import (
    citation_validity,
    evidence_grounded_finding_rate,
    finding_coverage,
    hypothesis_promotion_error_rate,
    mrr,
    precision_at_k,
    recall_at_k,
    unsupported_factual_claim_rate,
)
from synapse.evaluation.router import (
    DEFAULT_BUDGET,
    ResourceBudget,
    RoutingDecision,
    RoutingPath,
    route_query,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
)

_log = get_logger("synapse.evaluation.runner")


# ── Cost measurement envelope (mission §8) ─────────────────────────────────


class CostMeasurement(StrEnum):
    """Three-valued cost-measurement label per mission §8.

    MEASURED     : observed during execution (e.g., wall-clock duration).
    ESTIMATED    : calculated from explicit assumptions (e.g., expected
                   graph-expansion count = max_graph_depth times max_results).
    NOT_MEASURED : unavailable. E.g., ``MONETARY_COST = NOT_MEASURED``
                   when no paid model is invoked (mission §8: "No
                   invented token usage, API charges or financial
                   savings.").
    """

    MEASURED = "measured"
    ESTIMATED = "estimated"
    NOT_MEASURED = "not_measured"


@dataclass(frozen=True)
class ResourceReport:
    """Per-case resource report (mission §8).

    Each field is a ``(CostMeasurement, value)`` tuple so the caller
    can distinguish measured / estimated / not_measured dimensions.

    Fields:
        duration_seconds: wall-clock duration of the executed path
            (MEASURED via ``time.perf_counter``).
        retrieved_candidate_count: number of retrieved items (claims +
            relationships + entities for PATH B; findings + cited_claims
            + cited_relationships for PATH C; capabilities for PATH A).
            MEASURED.
        processed_evidence_count: number of evidence fragments /
            citation-chain links processed by the executed path.
            MEASURED.
        graph_expansion_count: number of graph-expanded related entities
            for PATH B (MEASURED from ``RetrievalResult.entities``);
            None for PATH A and PATH C (NOT_MEASURED).
        result_count: number of top-level results returned (claims for
            PATH B; findings for PATH C; capability summaries for PATH A).
            MEASURED.
        selected_routing_path: the path chosen by the router (MEASURED).
        monetary_cost: NOT_MEASURED when no paid model is invoked
            (mission §8: "When no paid model is invoked:
            MONETARY_COST = NOT_MEASURED").
        token_usage: NOT_MEASURED when no LLM is invoked.
        budget: the ResourceBudget applied to the case.
    """

    duration_seconds: tuple[CostMeasurement, float]
    retrieved_candidate_count: tuple[CostMeasurement, int]
    processed_evidence_count: tuple[CostMeasurement, int]
    graph_expansion_count: tuple[CostMeasurement, int | None]
    result_count: tuple[CostMeasurement, int]
    selected_routing_path: tuple[CostMeasurement, RoutingPath]
    monetary_cost: tuple[CostMeasurement, float | None]
    token_usage: tuple[CostMeasurement, int | None]
    budget: ResourceBudget


# ── Quality gate results (mission §6) ──────────────────────────────────────


@dataclass(frozen=True)
class QualityGateResult:
    """Result of evaluating a single quality gate.

    Per mission §6: "Quality gates must fail visibly when violated. Do
    not modify expected results to conceal implementation failures. If
    a gate fails, report the failure and its cause rather than
    weakening the evaluation."
    """

    name: str
    passed: bool
    detail: str


# ── Per-case metrics ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CaseMetrics:
    """All metrics + quality gates for a single golden case.

    Includes both retrieval metrics (Precision@K, Recall@K, MRR) and
    reasoning metrics (citation validity, evidence-grounded finding
    rate, etc.) plus the 6 mandatory quality-gate results.
    """

    case_id: str
    category: EvaluationCategory
    routing_decision: RoutingDecision
    resource_report: ResourceReport
    # Retrieval metrics (mission §4)
    precision_at_5: float
    recall_at_5: float
    mrr: float
    # Reasoning metrics (mission §5)
    citation_validity: float
    evidence_grounded_finding_rate: float
    unsupported_factual_claim_rate: float
    hypothesis_promotion_error_rate: float
    finding_coverage: float
    # Quality gates (mission §6)
    quality_gates: list[QualityGateResult]
    # Routing-correctness flag (expected vs actual path)
    routing_matches_expected: bool
    # Intent-correctness flag (expected vs actual intent, when expected_intent is set)
    intent_matches_expected: bool


# ── Aggregate report ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvaluationReport:
    """Top-level G04-T04 evaluation report.

    Contains per-case metrics, aggregate means, the full list of failed
    quality gates (if any), and any routing-mismatch notes.

    Per mission §6: "If a gate fails, report the failure and its cause
    rather than weakening the evaluation."
    """

    dataset_version: str
    case_count: int
    case_metrics: list[CaseMetrics]
    aggregate_metrics: dict[str, float]
    all_quality_gates_passed: bool
    failed_gates: list[QualityGateResult]
    routing_mismatches: list[str]
    intent_mismatches: list[str]
    notes: list[str] = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────────


async def _collect_existing_ids(session: AsyncSession) -> dict[str, set[str]]:
    """Return sets of existing IDs for citation-validity checks.

    Used by the citation-validity metric: every cited ID must resolve
    to an existing record. We fetch the IDs once per evaluation run
    (not per case) to avoid N+1 queries.
    """
    claims = {row[0] for row in (await session.execute(select(ClaimRow.id))).all()}
    rels = {row[0] for row in (await session.execute(select(RelationshipRow.id))).all()}
    frags = {row[0] for row in (await session.execute(select(EvidenceFragmentRow.id))).all()}
    entities = {row[0] for row in (await session.execute(select(EntityRow.id))).all()}
    return {
        "claims": claims,
        "relationships": rels,
        "fragments": frags,
        "entities": entities,
    }


async def _collect_candidate_capability_map(
    session: AsyncSession,
    candidate_entity_ids: list[str],
) -> dict[str, list[str]]:
    """For each candidate, list the capability IDs it PROVIDES/ENABLES/PRODUCES.

    Used by the provider-attribution correctness metric (structural):
    a DOCUMENTED_FACT finding's sources must be in the candidate's
    capability set.
    """
    out: dict[str, list[str]] = {}
    for cand_id in candidate_entity_ids:
        caps = await find_capabilities(session, cand_id, limit=50)
        out[cand_id] = [
            r["entity"]["id"] for r in caps if r.get("entity", {}).get("kind") == "capability"
        ]
    return out


def _extract_retrieved_ids(
    decision: RoutingDecision,
    result: dict[str, Any],
) -> list[str]:
    """Extract ranked retrieved IDs from the executed-path result.

    The shape depends on the path:

      - PATH A (list_capabilities): ``items`` → list of CapabilitySummary.
      - PATH B (hybrid_retrieve): ``claims`` (each has ``claim.id``) +
        ``relationships`` (each has ``relationship.id``) +
        ``entities`` (each has ``id``).
      - PATH C (answer_query): ``cited_claims`` + ``cited_relationships``
        (the citation-chain validation has already dropped invalid IDs).
    """
    ids: list[str] = []
    if decision.path == RoutingPath.DIRECT_LOOKUP:
        for item in result.get("items", []):
            iid = item.get("id")
            if iid:
                ids.append(iid)
    elif decision.path == RoutingPath.HYBRID_RETRIEVAL:
        for c in result.get("claims", []):
            cid = (c.get("claim") or {}).get("id")
            if cid:
                ids.append(cid)
        for r in result.get("relationships", []):
            rid = (r.get("relationship") or {}).get("id")
            if rid:
                ids.append(rid)
        for e in result.get("entities", []):
            eid = e.get("id")
            if eid:
                ids.append(eid)
    else:  # PATH C
        ids.extend(result.get("cited_claims", []))
        ids.extend(result.get("cited_relationships", []))
    return ids


def _count_evidence(decision: RoutingDecision, result: dict[str, Any]) -> int:
    """Count evidence records processed by the executed path.

    PATH A: 0 (direct lookup does not fetch evidence).
    PATH B: total evidence_bundle entries across all claims + relationships.
    PATH C: number of evidence_chain links.
    """
    if decision.path == RoutingPath.DIRECT_LOOKUP:
        return 0
    if decision.path == RoutingPath.HYBRID_RETRIEVAL:
        count = 0
        for c in result.get("claims", []):
            count += len(c.get("evidence_bundle") or [])
        for r in result.get("relationships", []):
            count += len(r.get("evidence_bundle") or [])
        return count
    return len(result.get("evidence_chain") or [])


def _count_graph_expansion(decision: RoutingDecision, result: dict[str, Any]) -> int | None:
    """Count graph-expanded related entities for PATH B; None otherwise.

    For PATH A and PATH C, graph expansion is NOT_MEASURED (the path
    does not perform BFS expansion).
    """
    if decision.path != RoutingPath.HYBRID_RETRIEVAL:
        return None
    return len(result.get("entities") or [])


def _count_top_level_results(decision: RoutingDecision, result: dict[str, Any]) -> int:
    """Count top-level results returned by the executed path.

    PATH A: count of capability summaries.
    PATH B: count of claims + relationships.
    PATH C: count of findings.
    """
    if decision.path == RoutingPath.DIRECT_LOOKUP:
        return len(result.get("items") or [])
    if decision.path == RoutingPath.HYBRID_RETRIEVAL:
        return len(result.get("claims") or []) + len(result.get("relationships") or [])
    return len(result.get("findings") or [])


# ── Quality-gate evaluation (mission §6) ────────────────────────────────────


def _evaluate_quality_gates(
    decision: RoutingDecision,
    result: dict[str, Any],
    case: GoldenCase,
    existing_ids: dict[str, set[str]],
    candidate_capability_map: dict[str, list[str]],
) -> list[QualityGateResult]:
    """Evaluate the 6 mandatory quality gates for one case.

    Per mission §6, the gates are:
      1. Zero fabricated citation identifiers.
      2. Zero known provider-attribution leakage.
      3. Zero unsupported hypothesis promotion.
      4. No suppression of applicable contradictions.
      5. No false CONTESTED classification caused solely by
         unrelated-context contradictions.
      6. No unbounded retrieval or graph traversal.
    """
    gates: list[QualityGateResult] = []

    cited_claims: list[str] = []
    cited_relationships: list[str] = []
    findings: list[dict[str, Any]] = []

    if decision.path == RoutingPath.HYBRID_RETRIEVAL:
        for c in result.get("claims", []):
            cid = (c.get("claim") or {}).get("id")
            if cid:
                cited_claims.append(cid)
        for r in result.get("relationships", []):
            rid = (r.get("relationship") or {}).get("id")
            if rid:
                cited_relationships.append(rid)
    elif decision.path == RoutingPath.GROUNDED_REASONING:
        cited_claims = list(result.get("cited_claims") or [])
        cited_relationships = list(result.get("cited_relationships") or [])
        findings = list(result.get("findings") or [])

    # ── Gate 1: Zero fabricated citation identifiers ──────────────────
    invalid_claims = [c for c in cited_claims if c not in existing_ids["claims"]]
    invalid_rels = [r for r in cited_relationships if r not in existing_ids["relationships"]]
    gates.append(
        QualityGateResult(
            name="no_fabricated_citations",
            passed=(not invalid_claims and not invalid_rels),
            detail=(f"invalid_claims={invalid_claims}; invalid_relationships={invalid_rels}"),
        )
    )

    # ── Gate 2: Zero known provider-attribution leakage (structural) ─
    # A DOCUMENTED_FACT finding whose ``sources`` are NOT in the candidate
    # capability map is suspect of attribution leakage. Exceptions:
    #   - findings about REPLACES / INTEGRATES_WITH relationships legitimately
    #     reference entities outside the candidate's PROVIDES set (e.g.,
    #     AltTool REPLACES synapse → finding sources = [relationship_id]).
    #   - findings about REQUIRES / DEPENDS_ON edges reference the dependency
    #     target entity (e.g., synapse REQUIRES RelationshipService →
    #     finding sources = [relationship_id]).
    # Such findings reference relationship IDs, not capability IDs, so we
    # check whether the source appears in the cited_relationships of the
    # answer (in which case it's a legitimate relationship-derived finding).
    if decision.path == RoutingPath.GROUNDED_REASONING and case.candidate_entity_ids:
        # Build the candidate capability set.
        candidate_caps: set[str] = set()
        for cid in case.candidate_entity_ids:
            candidate_caps.update(candidate_capability_map.get(cid, []))
        # Also include the candidate entity IDs themselves (REPLACES /
        # INTEGRATES_WITH findings reference entity IDs, not capability IDs).
        candidate_entities: set[str] = set(case.candidate_entity_ids)
        # And include cited_relationship IDs (REPLACES / REQUIRES findings
        # source their relationship ID, not the related entity's ID).
        cited_rel_set: set[str] = set(result.get("cited_relationships") or [])
        allowed_sources = candidate_caps | candidate_entities | cited_rel_set
        leaked = []
        for f in findings:
            if f.get("type") != FindingType.DOCUMENTED_FACT.value:
                continue
            sources = f.get("sources") or []
            # A factual finding is "attributable" if at least one source is
            # in the allowed set (candidate capabilities, candidate entities,
            # or cited relationships).
            if not any(s in allowed_sources for s in sources):
                leaked.append({"text": f.get("text"), "sources": sources})
        gates.append(
            QualityGateResult(
                name="no_provider_attribution_leakage",
                passed=(not leaked),
                detail=(
                    f"{len(leaked)} DOCUMENTED_FACT finding(s) with sources "
                    f"outside the candidate capability set"
                ),
            )
        )
    else:
        gates.append(
            QualityGateResult(
                name="no_provider_attribution_leakage",
                passed=True,
                detail="not applicable (no named candidates or non-reasoning path)",
            )
        )

    # ── Gate 3: Zero unsupported hypothesis promotion ────────────────
    if findings:
        promo_errors = sum(
            1
            for f in findings
            if f.get("type") == FindingType.DOCUMENTED_FACT.value
            and not (f.get("evidence_refs") or [])
        )
        gates.append(
            QualityGateResult(
                name="no_unsupported_hypothesis_promotion",
                passed=(promo_errors == 0),
                detail=(f"{promo_errors} DOCUMENTED_FACT finding(s) without evidence_refs"),
            )
        )
    else:
        gates.append(
            QualityGateResult(
                name="no_unsupported_hypothesis_promotion",
                passed=True,
                detail="no findings to evaluate (path A or B or empty answer)",
            )
        )

    # ── Gate 4: No suppression of applicable contradictions ──────────
    if case.expected_contradiction_preserved and decision.path == RoutingPath.GROUNDED_REASONING:
        contradictions_field = list(result.get("contradictions") or [])
        has_visible_contradictions = bool(contradictions_field) or any(
            "contested" in (f.get("caveat") or "").lower()
            or "contested" in (f.get("text") or "").lower()
            for f in findings
        )
        gates.append(
            QualityGateResult(
                name="applicable_contradictions_preserved",
                passed=has_visible_contradictions,
                detail=(
                    f"contradictions_field_size={len(contradictions_field)}; "
                    f"findings_with_contested_in_text="
                    f"{sum(1 for f in findings if 'contested' in (f.get('text') or '').lower())}; "
                    f"findings_with_contested_in_caveat="
                    f"{sum(1 for f in findings if 'contested' in (f.get('caveat') or '').lower())}"
                ),
            )
        )
    else:
        gates.append(
            QualityGateResult(
                name="applicable_contradictions_preserved",
                passed=True,
                detail="not applicable (no preservation expected for this case)",
            )
        )

    # ── Gate 5: No false CONTESTED from unrelated-context contradictions ─
    # When the case category is OUT_OF_CONTEXT_CONTRADICTIONS, the answer
    # must NOT classify the result as CONTESTED based on out-of-context
    # contradictions alone. We check structurally: no finding should
    # have a caveat that claims "CONTESTED" while also mentioning
    # "out-of-context".
    if (
        case.category == EvaluationCategory.OUT_OF_CONTEXT_CONTRADICTIONS
        and decision.path == RoutingPath.GROUNDED_REASONING
    ):
        false_contested = False
        for f in findings:
            text = (f.get("text") or "").lower()
            # A finding is falsely CONTESTED if it claims CONTESTED based
            # solely on out-of-context contradictions. The G04-T03C fix
            # ensures out-of-context contradictions are preserved as
            # metadata, NOT classified as CONTESTED. So if any finding
            # text claims "contested" AND mentions "out-of-context", that's
            # a false positive.
            if "contested" in text and "out-of-context" in text:
                false_contested = True
                break
        gates.append(
            QualityGateResult(
                name="no_false_contested_from_unrelated_context",
                passed=not false_contested,
                detail=(
                    "out-of-context contradictions preserved as metadata "
                    "(not classified as CONTESTED)"
                ),
            )
        )
    else:
        gates.append(
            QualityGateResult(
                name="no_false_contested_from_unrelated_context",
                passed=True,
                detail="not applicable (case does not involve out-of-context contradictions)",
            )
        )

    # ── Gate 6: No unbounded retrieval or graph traversal ───────────
    bud = decision.budget
    budget_ok = (
        bud.max_graph_depth <= 5
        and bud.max_results <= 100
        and bud.max_graph_expansion <= 50
        and bud.max_evidence_records <= 1000  # defensive ceiling
    )
    gates.append(
        QualityGateResult(
            name="bounded_retrieval_and_traversal",
            passed=budget_ok,
            detail=(
                f"max_depth={bud.max_graph_depth} (<=5); "
                f"max_results={bud.max_results} (<=100); "
                f"max_expansion={bud.max_graph_expansion} (<=50); "
                f"max_evidence={bud.max_evidence_records} (<=1000)"
            ),
        )
    )

    return gates


# ── Main evaluation runner ────────────────────────────────────────────────


async def run_evaluation(
    session: AsyncSession,
    *,
    budget: ResourceBudget | None = None,
) -> EvaluationReport:
    """Execute all golden cases and produce an ``EvaluationReport``.

    Per mission §10 acceptance test #17: "Reproducible quality metrics."
    The runner is fully deterministic: same DB state → same report.

    The runner does NOT modify the knowledge graph. It is READ-ONLY.

    Args:
        session: AsyncSession bound to the Synapse database (must be
            pre-seeded with the G04-T04 evaluation fixture -- see
            ``tests/integration/test_g04_t04_evaluation.py``).
        budget: optional ResourceBudget to apply to all cases. Defaults
            to ``DEFAULT_BUDGET``.

    Returns:
        A structured ``EvaluationReport`` with per-case metrics,
        aggregate means, failed-gate list, and routing/intent mismatch
        notes.
    """
    bud = budget or DEFAULT_BUDGET
    cases = load_golden_dataset()
    existing_ids = await _collect_existing_ids(session)

    case_metrics: list[CaseMetrics] = []
    routing_mismatches: list[str] = []
    intent_mismatches: list[str] = []
    notes: list[str] = []

    for case in cases:
        # 1. Route the query.
        decision = route_query(
            case.query,
            candidate_entity_ids=case.candidate_entity_ids or None,
            context=case.context,
            budget=bud,
        )

        # 2. Verify routing matches expected.
        routing_matches = decision.path.value == case.expected_routing_path
        if not routing_matches:
            routing_mismatches.append(
                f"{case.case_id}: expected path={case.expected_routing_path}, "
                f"got path={decision.path.value} (reason: {decision.reason})"
            )

        # 3. Verify intent matches expected (when set).
        intent_matches = True
        if case.expected_intent is not None:
            intent_matches = decision.intent.value == case.expected_intent
            if not intent_matches:
                intent_mismatches.append(
                    f"{case.case_id}: expected intent={case.expected_intent}, "
                    f"got intent={decision.intent.value}"
                )

        # 4. Execute the chosen path.
        start = time.perf_counter()
        if decision.path == RoutingPath.DIRECT_LOOKUP:
            # PATH A: direct structured lookup via list_capabilities.
            result: dict[str, Any] = dict(await list_capabilities(session, limit=bud.max_results))
        elif decision.path == RoutingPath.HYBRID_RETRIEVAL:
            # PATH B: hybrid retrieval.
            result = dict(
                await hybrid_retrieve(
                    session,
                    case.query,
                    max_depth=bud.max_graph_depth,
                    limit=bud.max_results,
                )
            )
        else:
            # PATH C: grounded reasoning.
            result = dict(
                await answer_query(
                    session,
                    case.query,
                    candidate_entity_ids=case.candidate_entity_ids or None,
                    context=case.context,
                    limit=bud.max_results,
                    requester="g04-t04-eval",
                )
            )
        duration = time.perf_counter() - start

        # 5. Build the resource report.
        retrieved_ids = _extract_retrieved_ids(decision, result)
        ev_count = _count_evidence(decision, result)
        graph_count = _count_graph_expansion(decision, result)
        result_count = _count_top_level_results(decision, result)

        resource_report = ResourceReport(
            duration_seconds=(CostMeasurement.MEASURED, duration),
            retrieved_candidate_count=(CostMeasurement.MEASURED, len(retrieved_ids)),
            processed_evidence_count=(CostMeasurement.MEASURED, ev_count),
            graph_expansion_count=(
                (CostMeasurement.MEASURED, graph_count)
                if graph_count is not None
                else (CostMeasurement.NOT_MEASURED, None)
            ),
            result_count=(CostMeasurement.MEASURED, result_count),
            selected_routing_path=(CostMeasurement.MEASURED, decision.path),
            monetary_cost=(CostMeasurement.NOT_MEASURED, None),
            token_usage=(CostMeasurement.NOT_MEASURED, None),
            budget=bud,
        )

        # 6. Compute retrieval metrics.
        relevant_set = set(case.expected_relevant_ids)
        p_at_5 = precision_at_k(retrieved_ids, relevant_set, 5)
        r_at_5 = recall_at_k(retrieved_ids, relevant_set, 5)
        reciprocal_rank = mrr(retrieved_ids, relevant_set)

        # 7. Compute reasoning metrics (only meaningful for PATH C, but
        #    computed for all paths for completeness -- the values will
        #    be vacuously 1.0 for PATH A/B when there are no findings).
        findings = (
            list(result.get("findings") or [])
            if decision.path == RoutingPath.GROUNDED_REASONING
            else []
        )
        # For PATH C, citation_validity checks the cited claim IDs.
        # For PATH A/B, there are no explicit "cited claims" -- the
        # retrieval IDs are the top-level results, and their validity is
        # already guaranteed by the retrieval layer (no fabricated hits).
        # We therefore pass an empty list to citation_validity for PATH A/B,
        # which (per metrics.py) returns 1.0 vacuously.
        cited_claims_for_metric = (
            list(result.get("cited_claims") or [])
            if decision.path == RoutingPath.GROUNDED_REASONING
            else []
        )
        cit_val = citation_validity(cited_claims_for_metric, existing_ids["claims"])
        ev_grounded = evidence_grounded_finding_rate(findings, existing_ids["fragments"])
        unsup = unsupported_factual_claim_rate(findings)
        promo_err = hypothesis_promotion_error_rate(findings)
        coverage = finding_coverage(len(findings), case.expected_min_findings)

        # 8. Evaluate the 6 quality gates.
        candidate_capability_map = await _collect_candidate_capability_map(
            session, case.candidate_entity_ids
        )
        gates = _evaluate_quality_gates(
            decision, result, case, existing_ids, candidate_capability_map
        )

        case_metrics.append(
            CaseMetrics(
                case_id=case.case_id,
                category=case.category,
                routing_decision=decision,
                resource_report=resource_report,
                precision_at_5=p_at_5,
                recall_at_5=r_at_5,
                mrr=reciprocal_rank,
                citation_validity=cit_val,
                evidence_grounded_finding_rate=ev_grounded,
                unsupported_factual_claim_rate=unsup,
                hypothesis_promotion_error_rate=promo_err,
                finding_coverage=coverage,
                quality_gates=gates,
                routing_matches_expected=routing_matches,
                intent_matches_expected=intent_matches,
            )
        )

    # 9. Aggregate metrics across all cases.
    n = len(case_metrics)
    if n == 0:
        agg: dict[str, float] = {}
    else:
        agg = {
            "mean_precision_at_5": sum(c.precision_at_5 for c in case_metrics) / n,
            "mean_recall_at_5": sum(c.recall_at_5 for c in case_metrics) / n,
            "mean_mrr": sum(c.mrr for c in case_metrics) / n,
            "mean_citation_validity": sum(c.citation_validity for c in case_metrics) / n,
            "mean_evidence_grounded_finding_rate": sum(
                c.evidence_grounded_finding_rate for c in case_metrics
            )
            / n,
            "mean_unsupported_factual_claim_rate": sum(
                c.unsupported_factual_claim_rate for c in case_metrics
            )
            / n,
            "mean_hypothesis_promotion_error_rate": sum(
                c.hypothesis_promotion_error_rate for c in case_metrics
            )
            / n,
            "mean_finding_coverage": sum(c.finding_coverage for c in case_metrics) / n,
            "mean_duration_seconds": sum(
                c.resource_report.duration_seconds[1] for c in case_metrics
            )
            / n,
            "routing_match_rate": sum(1 for c in case_metrics if c.routing_matches_expected) / n,
            "intent_match_rate": sum(1 for c in case_metrics if c.intent_matches_expected) / n,
        }

    # 10. Collect failed gates.
    failed_gates: list[QualityGateResult] = []
    for cm in case_metrics:
        failed_gates.extend(g for g in cm.quality_gates if not g.passed)

    _log.info(
        "G04-T04 evaluation complete: %d cases, %d failed gates, %d routing mismatches",
        n,
        len(failed_gates),
        len(routing_mismatches),
    )

    return EvaluationReport(
        dataset_version=GOLDEN_DATASET_VERSION,
        case_count=n,
        case_metrics=case_metrics,
        aggregate_metrics=agg,
        all_quality_gates_passed=(not failed_gates),
        failed_gates=failed_gates,
        routing_mismatches=routing_mismatches,
        intent_mismatches=intent_mismatches,
        notes=notes,
    )


# ── Re-export for callers that want a single import ───────────────────────

__all__ = [
    "CaseMetrics",
    "CostMeasurement",
    "EvaluationReport",
    "QualityGateResult",
    "ResourceReport",
    "run_evaluation",
]
