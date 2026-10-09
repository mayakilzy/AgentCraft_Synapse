"""G04-T04 -- Deterministic Cost-Aware Routing Policy.

Per the user's G04-T04 mission briefing §7:

  "Implement a minimal routing policy with three logical paths."

    PATH A — Direct structured lookup
        For simple, unambiguous requests resolvable through existing
        structured knowledge access.

    PATH B — Hybrid retrieval
        For technical discovery requiring lexical, metadata or
        graph-assisted retrieval.

    PATH C — Grounded reasoning
        For requests requiring evidence synthesis, dependencies,
        comparisons, constraints or contradictions.

  "The router must select the least complex path that satisfies the
   request's requirements."

  "Do not route a complex question to a cheaper path if that would
   remove required reasoning or evidence handling."

  "For uncertain intent, use a safe fallback."

  "A routing policy or dry-run implementation is acceptable for this
   stage. Do not replace the existing production execution flow unless
   required and regression-tested."

This module is a **deterministic policy decision function**. It does
NOT execute the chosen path -- it only decides which path is
appropriate. The ``runner`` module executes the chosen path against
the existing G04-T01/T02/T03 services.

The router is intentionally stateless: same input → same output. No
DB access, no I/O, no side effects. This makes it trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from synapse.application.reasoning import (
    ReasoningIntent,
    classify_reasoning_intent,
)

# ── Routing paths (mission §7) ─────────────────────────────────────────────


class RoutingPath(StrEnum):
    """The three logical execution paths per mission §7."""

    #: Simple, unambiguous requests resolvable through structured access.
    #: Uses: ``find_capabilities(session, entity_id)`` (direct PROVIDES lookup).
    DIRECT_LOOKUP = "A"

    #: Technical discovery requiring lexical + metadata + graph retrieval.
    #: Uses: ``hybrid_retrieve(session, query, ...)``.
    HYBRID_RETRIEVAL = "B"

    #: Evidence synthesis, dependency / alternative / constraint / comparison
    #: / gap reasoning. Uses: ``answer_query(session, query, ...)``.
    GROUNDED_REASONING = "C"


# ── Resource budget (mission §7 + §8) ──────────────────────────────────────


@dataclass(frozen=True)
class ResourceBudget:
    """Per-request resource budget enforced by the router.

    Per mission §7 ("Applicable resource budget") and §8 ("Cost and
    resource measurements"). All fields are upper-bounded so retrieval
    and graph traversal remain bounded (mission §6: "No unbounded
    retrieval or graph traversal").

    Defaults are conservative and match the existing hard limits in
    ``synapse.application.retrieval`` and ``synapse.application.gap_analyzer``.
    """

    max_results: int = 20  # cap on returned items (≤ MAX_LIMIT=100)
    max_evidence_records: int = 200  # cap on evidence fragments processed
    max_graph_depth: int = 3  # BFS depth (≤ MAX_DEPTH=5)
    max_graph_expansion: int = 50  # total related entities per seed (≤ MAX_GRAPH_EXPANSION=50)
    timeout_seconds: float = 30.0  # soft wall-clock budget (NOT enforced here; measured in runner)

    def __post_init__(self) -> None:
        # Enforce the global upper bounds from retrieval.py.
        if self.max_results > 100:
            raise ValueError(f"max_results={self.max_results} exceeds hard ceiling 100")
        if self.max_graph_depth > 5:
            raise ValueError(f"max_graph_depth={self.max_graph_depth} exceeds hard ceiling 5")
        if self.max_graph_expansion > 50:
            raise ValueError(
                f"max_graph_expansion={self.max_graph_expansion} exceeds hard ceiling 50"
            )


#: Default budget used when the caller does not specify one.
DEFAULT_BUDGET = ResourceBudget()


# ── Routing decision ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoutingDecision:
    """A single routing decision returned by ``route_query``.

    Attributes:
        path: the selected ``RoutingPath`` (A / B / C).
        intent: the ``ReasoningIntent`` classified from the query (or
            UNKNOWN for ambiguous queries).
        reason: human-readable explanation for the decision.
        budget: the resource budget applied to the execution.
        fallback: optional fallback path used when the primary path
            produces insufficient results (e.g., PATH B → PATH C
            when retrieval yields zero results).
    """

    path: RoutingPath
    intent: ReasoningIntent
    reason: str
    budget: ResourceBudget
    fallback: RoutingPath | None = None


# ── Intent classification ──────────────────────────────────────────────────

#: Reasoning intents that REQUIRE evidence synthesis (PATH C).
#: These intents are listed in mission §7 PATH C description:
#: "evidence synthesis, dependencies, comparisons, constraints or
#: contradictions".
_COMPLEX_INTENTS: frozenset[ReasoningIntent] = frozenset(
    {
        ReasoningIntent.DEPENDENCY_ANALYSIS,
        ReasoningIntent.DOCUMENTED_ALTERNATIVES,
        ReasoningIntent.CONSTRAINT_ANALYSIS,
        ReasoningIntent.TECHNICAL_COMPARISON,
        ReasoningIntent.GAP_EXPLANATION,
    }
)


def _looks_like_simple_capability_lookup(
    query: str,
    candidate_entity_ids: list[str] | None,
    context: str | None = None,
) -> bool:
    """Heuristic: is this a simple, unambiguous capability-lookup request?

    A request is "simple" if:
      - At least one named candidate is provided (so we can do a
        direct PROVIDES lookup rather than a graph scan).
      - The query is short (≤ 80 chars) -- long queries usually imply
        a complex intent even if they happen to mention "capabilit".
      - The query does NOT contain context-sensitivity markers
        (multiple clauses separated by "?", or words like "explain",
        "compare", "contrast", "detail") that suggest the user wants
        more than a direct lookup.
      - No technical context is provided. When ``context`` is supplied,
        the user wants context-sensitive evaluation, which only PATH C
        (the gap analyzer with applicability matching) can provide.
        Routing such a query to PATH A would silently drop the context.

    Returns True if all conditions hold.
    """
    if not candidate_entity_ids:
        return False
    if len(query) > 80:
        return False
    # G04-T04C defect D1 fix: when context is provided, the user wants
    # context-sensitive analysis. PATH A (list_capabilities) cannot
    # apply context — only PATH C (gap_analyzer with applicability
    # matching) can. Routing a context-bearing capability_explanation
    # query to PATH A would silently drop the context, violating
    # mission §7: "Do not route a complex question to a cheaper path
    # if that would remove required reasoning or evidence handling."
    if context:
        return False
    q_lower = query.lower()
    # Multi-clause queries (containing "?") suggest the user wants more
    # than a simple lookup -- route to PATH C for proper reasoning.
    if q_lower.count("?") > 1:
        return False
    # Explicit "explain / compare / contrast / detail" markers indicate
    # the user wants deeper analysis, not just a lookup.
    complex_markers = ("explain", "compare", "contrast", "detail", "analyze")
    return not any(marker in q_lower for marker in complex_markers)


def route_query(
    query: str,
    *,
    candidate_entity_ids: list[str] | None = None,
    context: str | None = None,
    budget: ResourceBudget | None = None,
) -> RoutingDecision:
    """Select the least-complex execution path that satisfies the request.

    Per mission §7:

      "The router must select the least complex path that satisfies the
       request's requirements."

      "Do not route a complex question to a cheaper path if that would
       remove required reasoning or evidence handling."

      "For uncertain intent, use a safe fallback."

    Decision tree (deterministic):

      1. Classify the query into a ReasoningIntent via the existing
         ``classify_reasoning_intent`` keyword matcher (G04-T03).
      2. If the intent is one of the 5 "complex" intents (dependency,
         alternatives, constraint, comparison, gap) → PATH C
         (grounded reasoning). These intents REQUIRE evidence synthesis
         and must NOT be downgraded.
      3. If the intent is CAPABILITY_EXPLANATION AND a named candidate
         is provided AND the query is short (≤ 80 chars) AND no
         context is provided AND no complex markers are present →
         PATH A (direct structured lookup via ``find_capabilities``).
         (G04-T04C defect D1 fix: when ``context`` is provided, PATH A
         is skipped because it cannot apply context-sensitive
         applicability matching -- only PATH C can.)
      4. If the intent is CAPABILITY_EXPLANATION (but NOT a simple
         lookup -- has context, complex markers, multi-clause, or no
         candidate) → PATH C (grounded reasoning). This ensures the
         gap_analyzer's provider-attribution safeguard applies, and
         that applicable contradictions are surfaced when context is
         provided.
      5. If the intent is UNKNOWN (no keyword matched) → PATH B
         (hybrid retrieval) with fallback=PATH C. This is the "safe
         fallback" per mission §7.
      6. Default → PATH B (hybrid retrieval).

    The router does NOT execute the chosen path. The caller (runner)
    is responsible for invoking the appropriate G04 service.

    Args:
        query: natural-language query (max 512 chars, but not enforced here).
        candidate_entity_ids: optional list of entity IDs the query is
            scoped to.
        context: optional technical context. When provided AND the intent
            is CAPABILITY_EXPLANATION, forces routing to PATH C (the
            gap analyzer applies context-sensitive applicability matching
            via phrase-substring overlap with claim ``validity_conditions``).
        budget: optional ResourceBudget to apply. Defaults to
            ``DEFAULT_BUDGET``.

    Returns:
        A ``RoutingDecision`` with the selected path, classified intent,
        human-readable reason, budget, and optional fallback.
    """
    bud = budget or DEFAULT_BUDGET
    intent = classify_reasoning_intent(query)

    # 1. Complex intent → PATH C (grounded reasoning). Never downgraded.
    if intent in _COMPLEX_INTENTS:
        return RoutingDecision(
            path=RoutingPath.GROUNDED_REASONING,
            intent=intent,
            reason=(
                f"intent={intent.value} requires evidence synthesis "
                f"(gap analysis + relationship traversal + citation chains)"
            ),
            budget=bud,
        )

    # 2. Simple capability lookup with named candidate → PATH A.
    #    G04-T04C defect D1 fix: when context is provided, the request
    #    is NOT a simple lookup -- PATH A cannot apply context-sensitive
    #    applicability matching. Route to PATH C instead.
    if intent == ReasoningIntent.CAPABILITY_EXPLANATION and _looks_like_simple_capability_lookup(
        query, candidate_entity_ids, context=context
    ):
        return RoutingDecision(
            path=RoutingPath.DIRECT_LOOKUP,
            intent=intent,
            reason=(
                "capability_explanation intent with named candidate, short simple query, "
                "and no context — direct structured lookup via find_capabilities is sufficient"
            ),
            budget=bud,
        )

    # 2b. Capability-explanation intent that is NOT a simple lookup (has
    # context, multi-clause, complex markers, or no candidate) → PATH C
    # (grounded reasoning). This ensures the gap_analyzer's provider-
    # attribution safeguard applies, and that applicable contradictions
    # are surfaced when context is provided.
    if intent == ReasoningIntent.CAPABILITY_EXPLANATION:
        return RoutingDecision(
            path=RoutingPath.GROUNDED_REASONING,
            intent=intent,
            reason=(
                "capability_explanation intent with context, complex markers, or "
                "multi-clause query — PATH C grounded reasoning applies the G04-T02C "
                "provider-attribution safeguard and surfaces applicable contradictions "
                "via the gap analyzer"
            ),
            budget=bud,
        )

    # 3. Ambiguous intent → PATH B with fallback to PATH C.
    if intent == ReasoningIntent.UNKNOWN:
        return RoutingDecision(
            path=RoutingPath.HYBRID_RETRIEVAL,
            intent=intent,
            reason=(
                "ambiguous query — no reasoning intent matched; safe fallback "
                "to hybrid retrieval (PATH B), with PATH C as fallback if retrieval "
                "yields zero results"
            ),
            budget=bud,
            fallback=RoutingPath.GROUNDED_REASONING,
        )

    # 4. Default: PATH B (hybrid retrieval).
    return RoutingDecision(
        path=RoutingPath.HYBRID_RETRIEVAL,
        intent=intent,
        reason=(
            f"intent={intent.value} requires lexical + structured + graph retrieval "
            f"but not full evidence-grounded reasoning"
        ),
        budget=bud,
    )
