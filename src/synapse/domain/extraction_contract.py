"""Extraction contract — typed knowledge units and source spans.

Per the user's G03-T01 authorization §2: define the minimal
``ExtractionResult``, ``SourceSpan``, and typed knowledge-unit
contracts.

Per §3: preserve all existing G01 domain semantics. Do not force
knowledge categories into an incompatible ``Entity.kind`` enum.
Instead, typed knowledge units use the existing ``EntityType`` kinds
plus an ``attributes["subtype"]`` field for finer-grained categories
(Tradeoff, FailureMode, Applicability, Opportunity) that don't have
their own EntityType.

Per §8: define a clean extension point for future semantic extraction
without implementing an LLM framework. The ``ExtractionResult`` shape
is the contract — a future ``LLMExtractor`` produces the same shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from synapse.domain._base import DomainRecord, EntityType, EpistemicState, StrEnum


class KnowledgeUnitType(StrEnum):
    """Typed knowledge unit categories for extraction.

    These are the categories the extraction layer can produce. Each
    maps to an ``Entity.kind`` (from the existing G01 EntityType enum)
    plus an optional ``attributes["subtype"]`` for finer-grained
    categories that don't have their own EntityType.
    """

    IDENTIFIER = "identifier"  # arXiv ID, DOI, GitHub repo, URL
    CAPABILITY = "capability"  # what a tool/technique can do
    TECHNIQUE = "technique"  # a method or approach
    CONSTRAINT = "constraint"  # a limitation or requirement
    TRADEOFF = "tradeoff"  # a cost/benefit tension
    FAILURE_MODE = "failure_mode"  # how a system can fail
    APPLICABILITY = "applicability"  # when something is applicable
    OPPORTUNITY = "opportunity"  # a potential improvement
    CLAIM = "claim"  # a proposition about the world


# Mapping from KnowledgeUnitType to Entity.kind.
# Types not in EntityType (Tradeoff, FailureMode, Applicability,
# Opportunity) map to the closest existing kind, with the specific
# type recorded in attributes["subtype"]. This preserves the G01
# enum without forcing incompatible categories.
_UNIT_TYPE_TO_ENTITY_KIND: dict[KnowledgeUnitType, EntityType] = {
    KnowledgeUnitType.IDENTIFIER: EntityType.PAPER,  # overridden by pattern
    KnowledgeUnitType.CAPABILITY: EntityType.CAPABILITY,
    KnowledgeUnitType.TECHNIQUE: EntityType.TECHNIQUE,
    KnowledgeUnitType.CONSTRAINT: EntityType.CONSTRAINT,
    KnowledgeUnitType.TRADEOFF: EntityType.CONSTRAINT,  # subtype="tradeoff"
    KnowledgeUnitType.FAILURE_MODE: EntityType.CONSTRAINT,  # subtype="failure_mode"
    KnowledgeUnitType.APPLICABILITY: EntityType.CONSTRAINT,  # subtype="applicability"
    KnowledgeUnitType.OPPORTUNITY: EntityType.CAPABILITY,  # subtype="opportunity"
    KnowledgeUnitType.CLAIM: EntityType.TECHNIQUE,  # overridden by context
}


class SourceSpan(DomainRecord):
    """Precise reference to where in the evidence text a unit was found.

    Per requirement #8: preserve source spans so knowledge claims can
    be traced back to their supporting content.

    The offsets are character offsets into
    ``EvidenceFragment.exact_excerpt``.
    """

    evidence_fragment_id: str = Field(..., min_length=1)
    start_offset: int = Field(..., ge=0)
    end_offset: int = Field(..., ge=0)
    excerpt: str = Field(..., min_length=1)
    context_before: str | None = None
    context_after: str | None = None

    @model_validator(mode="after")
    def _span_valid(self) -> SourceSpan:
        if self.start_offset >= self.end_offset:
            raise ValueError(
                f"start_offset ({self.start_offset}) must be < end_offset ({self.end_offset})"
            )
        if self.end_offset - self.start_offset != len(self.excerpt):
            raise ValueError(
                f"excerpt length ({len(self.excerpt)}) does not match "
                f"offset span ({self.end_offset - self.start_offset})"
            )
        return self


class ExtractionResult(DomainRecord):
    """Output of a single extraction pass on one EvidenceFragment.

    This is the contract between the extraction layer and the
    persistence layer. Future LLM-based extractors must produce the
    same shape.

    Per requirement #5: distinguish extracted source statements from
    verified claims. An ExtractionResult with ``epistemic_state=
    supported`` means "this text was found in the source" — it is NOT
    a verified fact. Verification is a separate step (G03-T04) that
    requires independent source tracking and review.
    """

    evidence_fragment_id: str = Field(..., min_length=1)
    source_span: SourceSpan
    unit_type: KnowledgeUnitType
    entity_kind: EntityType
    canonical_name: str = Field(..., min_length=1, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)
    proposition: str | None = Field(default=None, max_length=2048)
    epistemic_state: EpistemicState = EpistemicState.HYPOTHESIZED
    extraction_method: str = Field(..., min_length=1)
    # extraction_confidence is method-specific (e.g., regex match
    # certainty). It is NOT an empirical probability and must never
    # be used to auto-promote hypotheses to verified facts.
    extraction_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _epistemic_consistency(self) -> ExtractionResult:
        """Per requirement #6: no automatic promotion to verified.

        Extraction can only produce 'supported' (found in source text)
        or 'hypothesized' (inferred but not directly stated). It can
        NEVER produce 'verified' — that requires the verification
        engine (G03-T04).
        """
        if self.epistemic_state not in (EpistemicState.SUPPORTED, EpistemicState.HYPOTHESIZED):
            raise ValueError(
                f"ExtractionResult.epistemic_state must be 'supported' or "
                f"'hypothesized', not {self.epistemic_state!r}. "
                f"'verified' requires the verification engine (G03-T04)."
            )
        # Supported means "found in source text" — it requires a
        # source_span (already enforced by the field being required).
        # Hypothesized means "inferred but not directly stated" — it
        # should NOT have an evidence_ref (the source_span points to
        # the text that triggered the inference, but the inference
        # itself is not evidence).
        return self

    @model_validator(mode="after")
    def _claim_requires_proposition(self) -> ExtractionResult:
        """If the unit type is 'claim', a proposition is required."""
        if self.unit_type == KnowledgeUnitType.CLAIM and not self.proposition:
            raise ValueError("ExtractionResult with unit_type=claim requires a proposition")
        return self


def entity_kind_for_unit_type(
    unit_type: KnowledgeUnitType,
    *,
    identifier_kind: EntityType = EntityType.PAPER,
) -> EntityType:
    """Map a KnowledgeUnitType to an Entity.kind.

    For IDENTIFIER, the kind depends on what was matched (paper,
    repository, etc.) — the caller passes the resolved kind.

    For types not in the EntityType enum (Tradeoff, FailureMode,
    Applicability, Opportunity), the closest existing kind is returned.
    The specific type is recorded in the entity's attributes["subtype"].
    """
    if unit_type == KnowledgeUnitType.IDENTIFIER:
        return identifier_kind
    return _UNIT_TYPE_TO_ENTITY_KIND[unit_type]


def subtype_for_unit_type(unit_type: KnowledgeUnitType) -> str | None:
    """Return the attributes['subtype'] value for a unit type, if any.

    Types that map directly to an EntityType (Capability, Technique,
    Constraint, Claim) return None — no subtype needed.
    """
    if unit_type in (
        KnowledgeUnitType.TRADEOFF,
        KnowledgeUnitType.FAILURE_MODE,
        KnowledgeUnitType.APPLICABILITY,
        KnowledgeUnitType.OPPORTUNITY,
    ):
        return unit_type.value
    return None


__all__ = [
    "ExtractionResult",
    "KnowledgeUnitType",
    "SourceSpan",
    "entity_kind_for_unit_type",
    "subtype_for_unit_type",
]
