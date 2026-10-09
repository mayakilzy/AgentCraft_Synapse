"""G04-T04C -- Negative quality-gate tests.

Per the user's G04-T04C mission briefing §5:

  "Verify that the six quality gates actually fail when deliberately
   presented with violating outputs. Use focused negative tests rather
   than relying only on passing golden cases."

This test module deliberately constructs VIOLATING outputs (synthetic
``result`` dicts that breach each gate's invariant) and asserts that
the corresponding ``_evaluate_quality_gates`` gate reports
``passed=False`` with a meaningful detail message.

The 6 mandatory quality gates (mission §6 of G04-T04):

  1. no_fabricated_citations
  2. no_provider_attribution_leakage
  3. no_unsupported_hypothesis_promotion
  4. applicable_contradictions_preserved
  5. no_false_contested_from_unrelated_context
  6. bounded_retrieval_and_traversal

For each gate, this module contains ONE positive test (the gate
passes when the output is valid) and ONE negative test (the gate
fails when the output is invalid). The negative tests prove the
gates are not vacuous.
"""

from __future__ import annotations

from typing import Any

from synapse.evaluation.golden_dataset import (
    EvaluationCategory,
    GoldenCase,
)
from synapse.evaluation.router import (
    DEFAULT_BUDGET,
    ResourceBudget,
    RoutingDecision,
    RoutingPath,
)
from synapse.evaluation.runner import (
    _evaluate_quality_gates,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_decision(
    path: RoutingPath = RoutingPath.GROUNDED_REASONING,
    budget: ResourceBudget = DEFAULT_BUDGET,
) -> RoutingDecision:
    """Build a minimal RoutingDecision for testing."""
    from synapse.application.reasoning import ReasoningIntent

    return RoutingDecision(
        path=path,
        intent=ReasoningIntent.CAPABILITY_EXPLANATION,
        reason="test",
        budget=budget,
    )


def _make_case(
    *,
    case_id: str = "G04T04C-NEG",
    category: EvaluationCategory = EvaluationCategory.PROVIDER_ATTRIBUTION,
    candidate_entity_ids: list[str] | None = None,
    expected_contradiction_preserved: bool = False,
) -> GoldenCase:
    """Build a minimal GoldenCase for testing."""
    return GoldenCase(
        case_id=case_id,
        category=category,
        query="test query",
        candidate_entity_ids=candidate_entity_ids or [],
        expected_relevant_ids=[],
        expected_routing_path="C",
        expected_intent="capability_explanation",
        expected_min_findings=0,
        expected_contradiction_preserved=expected_contradiction_preserved,
        notes="negative-test case",
    )


def _existing_ids() -> dict[str, set[str]]:
    """A typical 'existing IDs' fixture for the negative tests."""
    return {
        "claims": {"claim-real-1", "claim-real-2"},
        "relationships": {"rel-real-1"},
        "fragments": {"frag-real-1", "frag-real-2"},
        "entities": {"ent-real-1"},
    }


def _candidate_capability_map() -> dict[str, list[str]]:
    """A typical candidate→capability map for the negative tests."""
    return {
        "ent-real-1": ["cap-real-1", "cap-real-2"],
    }


# ── Gate 1: no_fabricated_citations ───────────────────────────────────────


def test_gate1_positive_no_fabricated_citations():
    """Gate 1 PASSES when all cited IDs exist in the DB."""
    decision = _make_decision()
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": ["claim-real-1", "claim-real-2"],
        "cited_relationships": ["rel-real-1"],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_fabricated_citations")
    assert gate.passed is True
    assert "invalid_claims=[]" in gate.detail


def test_gate1_negative_fabricated_claim_id():
    """Gate 1 FAILS when a cited claim ID does NOT exist in the DB.

    Per mission §6 invariant 1: "Zero fabricated citation identifiers."
    """
    decision = _make_decision()
    case = _make_case()
    # 'claim-fabricated-xyz' does NOT exist in _existing_ids()['claims'].
    result: dict[str, Any] = {
        "cited_claims": ["claim-real-1", "claim-fabricated-xyz"],
        "cited_relationships": ["rel-real-1"],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_fabricated_citations")
    assert gate.passed is False, (
        "Gate 1 must FAIL when a cited claim ID does not exist; "
        f"got passed={gate.passed}, detail={gate.detail}"
    )
    assert "claim-fabricated-xyz" in gate.detail


def test_gate1_negative_fabricated_relationship_id():
    """Gate 1 also FAILS when a cited relationship ID does NOT exist."""
    decision = _make_decision()
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": ["claim-real-1"],
        "cited_relationships": ["rel-real-1", "rel-fabricated-xyz"],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_fabricated_citations")
    assert gate.passed is False
    assert "rel-fabricated-xyz" in gate.detail


# ── Gate 2: no_provider_attribution_leakage ────────────────────────────────


def test_gate2_positive_attribution_correct():
    """Gate 2 PASSES when DOCUMENTED_FACT findings' sources are in the
    candidate capability set."""
    decision = _make_decision()
    case = _make_case(candidate_entity_ids=["ent-real-1"])
    result: dict[str, Any] = {
        "cited_claims": ["claim-real-1"],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "real capability is supported",
                "sources": ["cap-real-1"],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.7,
                "caveat": None,
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_provider_attribution_leakage")
    assert gate.passed is True


def test_gate2_negative_attribution_leak():
    """Gate 2 FAILS when a DOCUMENTED_FACT finding cites a capability NOT
    provided by any named candidate.

    Per mission §6 invariant 2: "Zero known provider-attribution leakage."
    Per G04-T02C: a SUPPORTED classification for a named candidate
    must require a valid, evidence-grounded association between that
    candidate and the capability.
    """
    decision = _make_decision()
    case = _make_case(candidate_entity_ids=["ent-real-1"])
    # 'cap-leaked-xyz' is NOT in the candidate's capability set
    # (only cap-real-1 and cap-real-2 are).
    result: dict[str, Any] = {
        "cited_claims": ["claim-real-1"],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "leaked capability is supported",
                "sources": ["cap-leaked-xyz"],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.7,
                "caveat": None,
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_provider_attribution_leakage")
    assert gate.passed is False, (
        "Gate 2 must FAIL when a DOCUMENTED_FACT finding cites a capability "
        "outside the candidate set; "
        f"got passed={gate.passed}, detail={gate.detail}"
    )
    assert "1 DOCUMENTED_FACT" in gate.detail


# ── Gate 3: no_unsupported_hypothesis_promotion ──────────────────────────


def test_gate3_positive_no_promotion():
    """Gate 3 PASSES when all DOCUMENTED_FACT findings have evidence_refs."""
    decision = _make_decision()
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "fact 1",
                "sources": ["cap-real-1"],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.7,
                "caveat": None,
            },
            {
                "type": "unknown",
                "text": "unknown fact",
                "sources": [],
                "evidence_refs": [],
                "confidence": 0.1,
                "caveat": None,
            },
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_unsupported_hypothesis_promotion")
    assert gate.passed is True


def test_gate3_negative_unsupported_fact_promotion():
    """Gate 3 FAILS when a DOCUMENTED_FACT finding has no evidence_refs.

    Per mission §6 invariant 3: "Zero unsupported hypothesis promotion."
    A DOCUMENTED_FACT without evidence is structurally a promoted
    hypothesis — the finding should be labeled UNKNOWN or HYPOTHESIS
    instead.
    """
    decision = _make_decision()
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "this fact has no evidence",
                "sources": [],
                "evidence_refs": [],  # <-- no evidence → unsupported
                "confidence": 0.7,
                "caveat": None,
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_unsupported_hypothesis_promotion")
    assert gate.passed is False, (
        "Gate 3 must FAIL when a DOCUMENTED_FACT finding has no evidence_refs; "
        f"got passed={gate.passed}, detail={gate.detail}"
    )
    assert "1 DOCUMENTED_FACT" in gate.detail


# ── Gate 4: applicable_contradictions_preserved ──────────────────────────


def test_gate4_positive_contradiction_preserved():
    """Gate 4 PASSES when expected_contradiction_preserved=True and the
    answer has visible contradictions."""
    decision = _make_decision()
    case = _make_case(expected_contradiction_preserved=True)
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "has conflicting evidence (CONTESTED)",
                "sources": [],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.2,
                "caveat": None,
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "applicable_contradictions_preserved")
    assert gate.passed is True


def test_gate4_negative_contradiction_suppressed():
    """Gate 4 FAILS when expected_contradiction_preserved=True but the
    answer has NO visible contradictions.

    Per mission §6 invariant 4: "No suppression of applicable
    contradictions." If a case expects an applicable contradiction
    (CONTESTED classification or non-empty contradictions list), the
    answer must actually surface it — silently dropping it is a
    suppression.
    """
    decision = _make_decision()
    case = _make_case(expected_contradiction_preserved=True)
    # Findings have NO "contested" text and contradictions list is empty.
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "supported by source A",
                "sources": [],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.7,
                "caveat": None,
            }
        ],
        "contradictions": [],  # <-- empty
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "applicable_contradictions_preserved")
    assert gate.passed is False, (
        "Gate 4 must FAIL when applicable contradictions are expected but "
        f"missing; got passed={gate.passed}, detail={gate.detail}"
    )


# ── Gate 5: no_false_contested_from_unrelated_context ────────────────────


def test_gate5_positive_no_false_contested():
    """Gate 5 PASSES when out-of-context contradictions are NOT classified
    as CONTESTED (only preserved as metadata)."""
    decision = _make_decision()
    case = _make_case(category=EvaluationCategory.OUT_OF_CONTEXT_CONTRADICTIONS)
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": "supported fact",
                "sources": [],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.7,
                "caveat": "2 out-of-context contradiction(s) preserved as metadata",
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_false_contested_from_unrelated_context")
    assert gate.passed is True


def test_gate5_negative_false_contested_from_unrelated_context():
    """Gate 5 FAILS when an out-of-context contradiction is incorrectly
    classified as CONTESTED.

    Per mission §6 invariant 5: "No false CONTESTED classification
    caused solely by unrelated-context contradictions."
    Per G04-T03C: out-of-context contradictions must be preserved as
    metadata, NOT classified as CONTESTED. If a finding text claims
    "CONTESTED" while also mentioning "out-of-context", that's a
    false positive.
    """
    decision = _make_decision()
    case = _make_case(category=EvaluationCategory.OUT_OF_CONTEXT_CONTRADICTIONS)
    # Finding text claims CONTESTED AND mentions out-of-context — this is
    # the false-positive pattern G04-T03C was designed to prevent.
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [
            {
                "type": "documented_fact",
                "text": (
                    "capability is CONTESTED due to out-of-context contradiction "
                    "(this is a false positive — the contradiction should be "
                    "metadata only, not a CONTESTED classification)"
                ),
                "sources": [],
                "evidence_refs": ["frag-real-1"],
                "confidence": 0.2,
                "caveat": None,
            }
        ],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "no_false_contested_from_unrelated_context")
    assert gate.passed is False, (
        "Gate 5 must FAIL when out-of-context contradictions are "
        f"incorrectly classified as CONTESTED; got passed={gate.passed}, detail={gate.detail}"
    )


# ── Gate 6: bounded_retrieval_and_traversal ──────────────────────────────


def test_gate6_positive_bounded_budget():
    """Gate 6 PASSES when the ResourceBudget is within hard upper bounds."""
    decision = _make_decision(budget=DEFAULT_BUDGET)
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "bounded_retrieval_and_traversal")
    assert gate.passed is True


def test_gate6_negative_unbounded_max_results():
    """Gate 6 FAILS when max_results exceeds the hard ceiling (100).

    Per mission §6 invariant 6: "No unbounded retrieval or graph
    traversal." ResourceBudget.__post_init__ raises ValueError when
    bounds are exceeded, but the gate also checks the budget at
    evaluation time as a defense-in-depth measure.

    We construct a RoutingDecision with a budget whose max_results
    field has been set above 100 via object.__setattr__ (bypassing
    the frozen dataclass's __post_init__). This simulates a future
    bug where the budget is constructed incorrectly.
    """
    # Construct a budget with an invalid max_results by bypassing
    # __post_init__ (frozen dataclass).
    bad_budget = ResourceBudget.__new__(ResourceBudget)
    object.__setattr__(bad_budget, "max_results", 200)  # exceeds ceiling 100
    object.__setattr__(bad_budget, "max_evidence_records", 200)
    object.__setattr__(bad_budget, "max_graph_depth", 3)
    object.__setattr__(bad_budget, "max_graph_expansion", 50)
    object.__setattr__(bad_budget, "timeout_seconds", 30.0)

    decision = _make_decision(budget=bad_budget)
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "bounded_retrieval_and_traversal")
    assert gate.passed is False, (
        "Gate 6 must FAIL when max_results exceeds the hard ceiling; "
        f"got passed={gate.passed}, detail={gate.detail}"
    )
    assert "max_results=200" in gate.detail


def test_gate6_negative_unbounded_max_graph_depth():
    """Gate 6 FAILS when max_graph_depth exceeds the hard ceiling (5)."""
    bad_budget = ResourceBudget.__new__(ResourceBudget)
    object.__setattr__(bad_budget, "max_results", 20)
    object.__setattr__(bad_budget, "max_evidence_records", 200)
    object.__setattr__(bad_budget, "max_graph_depth", 10)  # exceeds ceiling 5
    object.__setattr__(bad_budget, "max_graph_expansion", 50)
    object.__setattr__(bad_budget, "timeout_seconds", 30.0)

    decision = _make_decision(budget=bad_budget)
    case = _make_case()
    result: dict[str, Any] = {
        "cited_claims": [],
        "cited_relationships": [],
        "findings": [],
        "contradictions": [],
    }
    gates = _evaluate_quality_gates(
        decision, result, case, _existing_ids(), _candidate_capability_map()
    )
    gate = next(g for g in gates if g.name == "bounded_retrieval_and_traversal")
    assert gate.passed is False
    assert "max_depth=10" in gate.detail
