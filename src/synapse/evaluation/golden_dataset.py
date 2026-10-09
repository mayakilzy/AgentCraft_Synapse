"""G04-T04 -- Golden evaluation dataset.

Per the user's G04-T04 mission briefing §3:

  "Create a compact, versioned collection of realistic technical
   evaluation cases."

  "Ground truth must be defined independently of the implementation
   output. Do not copy actual system responses into expected answers
   merely to make tests pass."

The dataset is **deterministic and explicitly labeled**. Each case
references fixture identifiers that the corresponding test fixture
(``_seed_evaluation_fixture`` in
``tests/integration/test_g04_t04_evaluation.py``) seeds into the
in-memory SQLite database. The expected relevant IDs are derived from
the fixture's *structural shape*, NOT from any prior implementation
output.

Twelve categories (mission §3):

  1. DIRECT_KNOWLEDGE_LOOKUP
  2. MULTI_TERM_TECHNICAL_RETRIEVAL
  3. CAPABILITY_DISCOVERY
  4. PROVIDER_ATTRIBUTION
  5. DEPENDENCY_REASONING
  6. DOCUMENTED_ALTERNATIVES
  7. CONTEXT_SENSITIVE_CONSTRAINTS
  8. APPLICABLE_CONTRADICTIONS
  9. OUT_OF_CONTEXT_CONTRADICTIONS
  10. MISSING_EVIDENCE
  11. MULTI_SOURCE_SYNTHESIS
  12. UNSUPPORTED_HYPOTHESES

Compactness: 14 cases total (12 categories + 2 extra coverage cases).
The dataset fits on a single screen; maintenance overhead is minimal.

Versioning: ``GOLDEN_DATASET_VERSION`` is bumped whenever a case is
added, removed, or its expected behavior changes. Evaluation reports
embed the version so regressions can be detected across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Bumped on any structural change to the dataset.
GOLDEN_DATASET_VERSION: str = "1.0.0"


class EvaluationCategory(StrEnum):
    """The 12 mandatory evaluation categories per mission §3."""

    DIRECT_KNOWLEDGE_LOOKUP = "direct_knowledge_lookup"
    MULTI_TERM_TECHNICAL_RETRIEVAL = "multi_term_technical_retrieval"
    CAPABILITY_DISCOVERY = "capability_discovery"
    PROVIDER_ATTRIBUTION = "provider_attribution"
    DEPENDENCY_REASONING = "dependency_reasoning"
    DOCUMENTED_ALTERNATIVES = "documented_alternatives"
    CONTEXT_SENSITIVE_CONSTRAINTS = "context_sensitive_constraints"
    APPLICABLE_CONTRADICTIONS = "applicable_contradictions"
    OUT_OF_CONTEXT_CONTRADICTIONS = "out_of_context_contradictions"
    MISSING_EVIDENCE = "missing_evidence"
    MULTI_SOURCE_SYNTHESIS = "multi_source_synthesis"
    UNSUPPORTED_HYPOTHESES = "unsupported_hypotheses"


@dataclass(frozen=True)
class GoldenCase:
    """A single golden evaluation case.

    Fields:
        case_id: stable identifier (e.g., "G04T04-C01"). Never reused.
        category: one of the 12 EvaluationCategory values.
        query: input natural-language query (max 512 chars).
        context: optional technical context (for applicability matching).
        candidate_entity_ids: optional list of entity IDs to scope to.
        expected_relevant_ids: stable knowledge-graph identifiers that
            the relevant retrieved/cited results SHOULD include. Used for
            Precision@K, Recall@K, and MRR. Defined structurally from
            the fixture -- never copied from implementation output.
        expected_routing_path: "A" (direct lookup), "B" (hybrid retrieval),
            or "C" (grounded reasoning). Per mission §7.
        expected_intent: optional ReasoningIntent value the router
            should classify the query into. None for cases where the
            intent is irrelevant (e.g., pure lexical lookup).
        expected_min_findings: minimum number of findings the response
            must contain to be considered "covered". Used for the
            finding_coverage metric.
        expected_citation_validity: expected lower bound on citation
            validity (1.0 = all cited IDs must exist). Defaults to 1.0.
        expected_no_unsupported_hypothesis: True if the response must
            NOT contain any DOCUMENTED_FACT finding without evidence_refs.
        expected_contradiction_preserved: True if the response must
            preserve an applicable contradiction (CONTESTED classification
            or non-empty ``contradictions`` list).
        notes: free-text rationale / explanation.
    """

    case_id: str
    category: EvaluationCategory
    query: str
    context: str | None = None
    candidate_entity_ids: list[str] = field(default_factory=list)
    expected_relevant_ids: list[str] = field(default_factory=list)
    expected_routing_path: str = "B"
    expected_intent: str | None = None
    expected_min_findings: int = 0
    expected_citation_validity: float = 1.0
    expected_no_unsupported_hypothesis: bool = True
    expected_contradiction_preserved: bool = False
    notes: str = ""


def load_golden_dataset() -> list[GoldenCase]:
    """Return the versioned golden evaluation dataset.

    Returns 14 cases across the 12 mandatory categories. Identifiers
    reference the ``_seed_evaluation_fixture`` in
    ``tests/integration/test_g04_t04_evaluation.py`` (which extends the
    G04-T03 reasoning fixture and the G04-T03C context-contradiction
    fixture with a single shared DB seeded once per test session).

    The dataset is intentionally compact: 14 cases covering ~12 fields each is
    enough to cover the 12 categories with 2 extra coverage cases
    (general knowledge fallback + ambiguous query) without creating
    maintenance overhead.
    """
    return [
        # ── 1. Direct knowledge lookup ─────────────────────────────────
        GoldenCase(
            case_id="G04T04-C01",
            category=EvaluationCategory.DIRECT_KNOWLEDGE_LOOKUP,
            query="synapse",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["ent-synapse"],
            expected_routing_path="B",
            expected_intent=None,  # GENERAL_KNOWLEDGE -- no reasoning intent triggered
            expected_min_findings=0,
            notes="Single-term direct entity lookup via hybrid_retrieve.",
        ),
        # ── 2. Multi-term technical retrieval ───────────────────────────
        GoldenCase(
            case_id="G04T04-C02",
            category=EvaluationCategory.MULTI_TERM_TECHNICAL_RETRIEVAL,
            query="source discovery arxiv",
            expected_relevant_ids=["cap-source-discovery", "ent-arxiv"],
            expected_routing_path="B",
            expected_intent=None,
            expected_min_findings=0,
            notes="Multi-term retrieval should surface both the capability and the arxiv provider.",
        ),
        # ── 3. Capability discovery (PATH A direct structured lookup) ──
        GoldenCase(
            case_id="G04T04-C03",
            category=EvaluationCategory.CAPABILITY_DISCOVERY,
            query="What capabilities does synapse provide?",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=[
                "cap-source-discovery",
                "cap-content-extraction",
                "cap-evidence-verification",
                "cap-dependency-analysis",
            ],
            expected_routing_path="A",
            expected_intent="capability_explanation",
            expected_min_findings=0,  # PATH A returns capabilities, not findings
            notes=(
                "Capability-explanation intent with a named candidate routes to PATH A "
                "(direct structured lookup via find_capabilities). The direct lookup returns "
                "ALL capabilities in the knowledge graph (not filtered by candidate), but "
                "the runner exposes the candidate's capabilities via the "
                "candidate_capability_map for the provider-attribution correctness metric."
            ),
        ),
        # ── 4. Provider attribution (PATH C capability_explanation) ────
        # Routes to PATH C because the query contains "explain" (a complex
        # marker). The reasoning layer's gap_analyzer applies the G04-T02C
        # provider-attribution safeguard.
        GoldenCase(
            case_id="G04T04-C04",
            category=EvaluationCategory.PROVIDER_ATTRIBUTION,
            query="What can synapse do? Explain capabilities.",
            context="AI agent systems",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["claim-rsn-sd", "claim-rsn-ce"],
            expected_routing_path="C",
            expected_intent="capability_explanation",
            expected_min_findings=1,
            notes=(
                "Provider-attribution case: claims attributed to ent-synapse must be retrievable. "
                "G04-T02C safeguard prevents leakage from ent-arxiv's claims. Query contains "
                "'explain' marker → routes to PATH C (grounded reasoning) so the gap analyzer's "
                "attribution safeguard applies."
            ),
        ),
        # ── 5. Dependency reasoning ─────────────────────────────────────
        GoldenCase(
            case_id="G04T04-C05",
            category=EvaluationCategory.DEPENDENCY_REASONING,
            query="What does synapse require?",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["ent-rs"],
            expected_routing_path="C",
            expected_intent="dependency_analysis",
            expected_min_findings=1,
            notes=(
                "Dependency-reasoning intent routes to PATH C (grounded reasoning). "
                "Synapse REQUIRES RelationshipService (ent-rs) -- directionality must be preserved."
            ),
        ),
        # ── 6. Documented alternatives ─────────────────────────────────
        GoldenCase(
            case_id="G04T04-C06",
            category=EvaluationCategory.DOCUMENTED_ALTERNATIVES,
            query="What are the alternatives to synapse?",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["ent-alttool"],
            expected_routing_path="C",
            expected_intent="documented_alternatives",
            expected_min_findings=1,
            notes=(
                "AltTool REPLACES synapse (documented alternative). "
                "INTEGRATES_WITH must NOT be reported as ALTERNATIVE_TO."
            ),
        ),
        # ── 7. Context-sensitive constraints ────────────────────────────
        GoldenCase(
            case_id="G04T04-C07",
            category=EvaluationCategory.CONTEXT_SENSITIVE_CONSTRAINTS,
            query="What limits synapse?",
            context="AI agent systems",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["ent-no-vector"],
            expected_routing_path="C",
            expected_intent="constraint_analysis",
            expected_min_findings=1,
            notes=(
                "Constraint analysis: ent-no-vector LIMITS cap-evidence-verification. "
                "Context 'AI agent systems' should match claim-rsn-ev's validity_conditions."
            ),
        ),
        # ── 8. Applicable contradictions ───────────────────────────────
        GoldenCase(
            case_id="G04T04-C08",
            category=EvaluationCategory.APPLICABLE_CONTRADICTIONS,
            query="What can tool A do? Explain capabilities.",
            context="AI agents",
            candidate_entity_ids=["ent-ctx-A"],
            expected_relevant_ids=["claim-A-applicable", "claim-A-universal"],
            expected_routing_path="C",
            expected_intent="capability_explanation",
            expected_min_findings=1,
            expected_contradiction_preserved=True,
            notes=(
                "Applicable contradiction: claim-A-applicable has validity_conditions=['AI agents'] "
                "and contradicting_refs=['frag-ctx-opp1']. Context 'AI agents' matches → "
                "CONTESTED must fire (preserved, not suppressed). Universal claim also fires "
                "CONTESTED unconditionally. Query phrased to trigger capability_explanation "
                "intent (PATH C) so the reasoning layer's gap analyzer evaluates it."
            ),
        ),
        # ── 9. Out-of-context contradictions ──────────────────────────
        GoldenCase(
            case_id="G04T04-C09",
            category=EvaluationCategory.OUT_OF_CONTEXT_CONTRADICTIONS,
            query="What can tool A do? Explain capabilities.",
            context="mobile apps",
            candidate_entity_ids=["ent-ctx-A"],
            expected_relevant_ids=["claim-A-universal"],
            expected_routing_path="C",
            expected_intent="capability_explanation",
            expected_min_findings=1,
            expected_contradiction_preserved=False,  # NOT CONTESTED for cap-ctx-X
            notes=(
                "Out-of-context contradiction: claim-A-inapplicable has validity_conditions="
                "['embedded systems'] which does NOT match context 'mobile apps'. "
                "Its contradiction must be preserved as out_of_context_contradictions metadata, "
                "but must NOT force CONTESTED classification for cap-ctx-X. The universal claim "
                "(no validity_conditions) is still applicable → CONTESTED for cap-ctx-universal."
            ),
        ),
        # ── 10. Missing evidence ───────────────────────────────────────
        GoldenCase(
            case_id="G04T04-C10",
            category=EvaluationCategory.MISSING_EVIDENCE,
            query="What is missing for an AI research assistant?",
            context="AI agent systems",
            candidate_entity_ids=["ent-synapse"],
            expected_relevant_ids=["cap-knowledge-extraction"],
            expected_routing_path="C",
            expected_intent="gap_explanation",
            expected_min_findings=1,
            notes=(
                "Gap-explanation intent: cap-knowledge-extraction has a PROVIDES edge but no "
                "evidence_refs and no attributed claim → NOT_EVIDENCED. Missing evidence is "
                "reported honestly (absence ≠ evidence of absence)."
            ),
        ),
        # ── 11. Multi-source synthesis ─────────────────────────────────
        GoldenCase(
            case_id="G04T04-C11",
            category=EvaluationCategory.MULTI_SOURCE_SYNTHESIS,
            query="Compare synapse and arxiv",
            candidate_entity_ids=["ent-synapse", "ent-arxiv"],
            expected_relevant_ids=["cap-source-discovery"],
            expected_routing_path="C",
            expected_intent="technical_comparison",
            expected_min_findings=1,
            notes=(
                "Technical-comparison intent: both providers PROVIDE cap-source-discovery. "
                "PATH C synthesizes findings from both candidates."
            ),
        ),
        # ── 12. Unsupported hypotheses ─────────────────────────────────
        GoldenCase(
            case_id="G04T04-C12",
            category=EvaluationCategory.UNSUPPORTED_HYPOTHESES,
            query="What can tool B do? Explain capabilities.",
            context="embedded systems",
            candidate_entity_ids=["ent-ctx-B"],
            expected_relevant_ids=["claim-B-only"],
            expected_routing_path="C",
            expected_intent="capability_explanation",
            expected_min_findings=1,
            expected_no_unsupported_hypothesis=True,
            notes=(
                "claim-B-only has validity_conditions=['AI agents'] but context='embedded systems' "
                "does not match → claim is inapplicable. The handler should NOT promote the claim "
                "to a DOCUMENTED_FACT without applicable evidence. Query phrased to trigger "
                "capability_explanation intent (PATH C)."
            ),
        ),
        # ── 13. Extra coverage: ambiguous query (safe fallback) ────────
        GoldenCase(
            case_id="G04T04-C13",
            category=EvaluationCategory.MULTI_TERM_TECHNICAL_RETRIEVAL,
            query="aardvark picnic galoshes",
            expected_relevant_ids=[],  # no relevant results expected
            expected_routing_path="B",
            expected_intent="unknown",
            expected_min_findings=0,
            notes=(
                "Ambiguous query with no recognizable intent keyword. Router must fall back to "
                "PATH B (hybrid retrieval) safely. Empty result set is truthful (no fabrication)."
            ),
        ),
        # ── 14. Extra coverage: universal-claim contradiction ─────────
        GoldenCase(
            case_id="G04T04-C14",
            category=EvaluationCategory.APPLICABLE_CONTRADICTIONS,
            query="What can tool A do? Explain capabilities.",
            context="embedded systems",
            candidate_entity_ids=["ent-ctx-A"],
            expected_relevant_ids=["claim-A-universal"],
            expected_routing_path="C",
            expected_intent="capability_explanation",
            expected_min_findings=1,
            expected_contradiction_preserved=True,
            notes=(
                "Universal claim (claim-A-universal has no validity_conditions) is ALWAYS "
                "applicable. Its contradiction must fire CONTESTED regardless of context. "
                "Query phrased to trigger capability_explanation intent (PATH C)."
            ),
        ),
    ]
