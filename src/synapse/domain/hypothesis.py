"""Hypothesis — a proposed explanation that has not yet been tested.

Per `DOMAIN_AND_API_CONTRACTS.md`: ``Hypothesis`` carries description,
premise_relationship_ids, assumptions, missing_evidence, status,
proposed_experiment_ids. Hypotheses are first-class because Synapse's
purpose is to *generate and test* hypotheses, not just answer questions.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, StrEnum


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    TESTABLE = "testable"
    EXPERIMENT_RUNNING = "experiment_running"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


# Allowed transitions
HYPOTHESIS_TRANSITIONS: dict[HypothesisStatus, set[HypothesisStatus]] = {
    HypothesisStatus.PROPOSED: {
        HypothesisStatus.UNDER_REVIEW,
        HypothesisStatus.TESTABLE,
        HypothesisStatus.SUPERSEDED,
    },
    HypothesisStatus.UNDER_REVIEW: {
        HypothesisStatus.TESTABLE,
        HypothesisStatus.PROPOSED,
        HypothesisStatus.SUPERSEDED,
    },
    HypothesisStatus.TESTABLE: {
        HypothesisStatus.EXPERIMENT_RUNNING,
        HypothesisStatus.SUPERSEDED,
    },
    HypothesisStatus.EXPERIMENT_RUNNING: {
        HypothesisStatus.SUPPORTED,
        HypothesisStatus.WEAKENED,
        HypothesisStatus.REJECTED,
    },
    HypothesisStatus.SUPPORTED: {HypothesisStatus.SUPERSEDED},
    HypothesisStatus.WEAKENED: {
        HypothesisStatus.SUPERSEDED,
        HypothesisStatus.EXPERIMENT_RUNNING,
    },
    HypothesisStatus.REJECTED: {HypothesisStatus.SUPERSEDED},
    HypothesisStatus.SUPERSEDED: set(),  # terminal
}


class Hypothesis(DomainRecord):
    description: str = Field(..., min_length=1)
    premise_relationship_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    proposed_experiment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _must_have_premise_or_assumption(self) -> Hypothesis:
        """A hypothesis with neither premises nor assumptions is just text."""
        if not self.premise_relationship_ids and not self.assumptions:
            raise ValueError(
                "Hypothesis must have at least one premise_relationship_id or assumption"
            )
        return self
