"""G04-T04 -- Retrieval & Reasoning Evaluation + Cost-Aware Routing.

Per the user's G04-T04 mission briefing §1:

  "Build a lightweight, reproducible evaluation system for Synapse retrieval
   and grounded reasoning, together with a deterministic, cost-aware
   execution-routing policy."

  "Primary principle: Measure quality before optimizing cost."

This package is a **read-only evaluation harness**. It does NOT modify the
knowledge graph, introduce new persistence, replace production execution
flow, or add new public API endpoints. It composes the existing G04-T01
(``hybrid_retrieve``), G04-T02 (``list_capabilities`` /
``find_capabilities``), G04-T02C (``analyze_gap`` provider-attribution
safeguard), and G04-T03 (``answer_query``) services into a measurement
and routing layer.

Submodules:

- ``golden_dataset`` -- versioned compact evaluation cases across the 12
  categories enumerated in mission §3. Ground truth is defined
  independently of implementation output (no copied system responses).
- ``metrics`` -- Precision@K, Recall@K, MRR, citation validity,
  evidence-grounded finding rate, unsupported factual-claim rate,
  hypothesis-promotion error rate, finding coverage.
- ``router`` -- deterministic cost-aware routing policy with three paths
  (PATH A direct structured lookup, PATH B hybrid retrieval, PATH C
  grounded reasoning). Routes to the least complex path that satisfies
  the request; safe fallback for ambiguous queries.
- ``runner`` -- orchestrates case execution, measures resources, evaluates
  the 6 mandatory quality gates (mission §6), and produces a structured
  ``EvaluationReport``. Distinguishes MEASURED / ESTIMATED / NOT_MEASURED
  cost dimensions per mission §8.

Architectural discipline (mission §9 "Implementation discipline"):

- No new database, no vector DB, no LLM provider, no agent framework.
- No rebuild of retrieval or reasoning.
- No new migrations, no new pip dependencies, no new API endpoints.
- No frontend components.
- No AgentCraft-Toolkit access (READ-ONLY boundary preserved).
- PRB-01..PRB-07 production-readiness blockers unchanged.
"""

from __future__ import annotations

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
from synapse.evaluation.runner import (
    CaseMetrics,
    CostMeasurement,
    EvaluationReport,
    QualityGateResult,
    ResourceReport,
    run_evaluation,
)

__all__ = [
    "DEFAULT_BUDGET",
    "GOLDEN_DATASET_VERSION",
    "CaseMetrics",
    "CostMeasurement",
    "EvaluationCategory",
    "EvaluationReport",
    "GoldenCase",
    "QualityGateResult",
    "ResourceBudget",
    "ResourceReport",
    "RoutingDecision",
    "RoutingPath",
    "citation_validity",
    "evidence_grounded_finding_rate",
    "finding_coverage",
    "hypothesis_promotion_error_rate",
    "load_golden_dataset",
    "mrr",
    "precision_at_k",
    "recall_at_k",
    "route_query",
    "run_evaluation",
    "unsupported_factual_claim_rate",
]
