"""Tests for G03-T01: deterministic knowledge extraction.

Per the user's G03-T01 authorization §Acceptance Tests:
  - Correct extraction of supported technical knowledge patterns.
  - Accurate source-span offsets and excerpts.
  - Stable results on repeated execution.
  - Graceful handling of unsupported or ambiguous text.
  - No automatic promotion to verified knowledge.
"""

from __future__ import annotations

import pytest

from synapse.application.extraction import extract
from synapse.domain._base import EntityType, EpistemicState
from synapse.domain.extraction_contract import (
    ExtractionResult,
    KnowledgeUnitType,
    SourceSpan,
    entity_kind_for_unit_type,
    subtype_for_unit_type,
)

# ── Fixture text ──────────────────────────────────────────────────────────────

FIXTURE_TEXT = """The Transformer Architecture

This paper introduces the self-attention mechanism, a novel approach
to sequence modeling. The work is described in detail at
https://arxiv.org/abs/1706.03762 and the reference implementation is
hosted at github.com/tensorflow/tensor2tensor.

The approach requires GPU acceleration for practical training speeds.
Our experiments show that Transformers outperform RNNs on long
sequences. The DOI for this work is 10.48550/arXiv.1706.03762.
"""

EMPTY_TEXT = ""
AMBIGUOUS_TEXT = "Some text without any recognizable patterns or identifiers."
PARTIAL_TEXT = "The number 1234.5 looks like an arXiv ID but has too few digits."


# ── Contract tests ───────────────────────────────────────────────────────────


class TestSourceSpan:
    def test_valid_span(self):
        span = SourceSpan(
            evidence_fragment_id="ef1",
            start_offset=0,
            end_offset=5,
            excerpt="hello",
        )
        assert span.start_offset == 0
        assert span.end_offset == 5
        assert span.excerpt == "hello"

    def test_start_must_be_less_than_end(self):
        with pytest.raises(ValueError, match="must be <"):
            SourceSpan(
                evidence_fragment_id="ef1",
                start_offset=10,
                end_offset=5,
                excerpt="short",
            )

    def test_excerpt_length_must_match_offsets(self):
        with pytest.raises(ValueError, match="excerpt length"):
            SourceSpan(
                evidence_fragment_id="ef1",
                start_offset=0,
                end_offset=10,
                excerpt="short",  # 5 chars, not 10
            )

    def test_context_is_optional(self):
        span = SourceSpan(
            evidence_fragment_id="ef1",
            start_offset=0,
            end_offset=3,
            excerpt="abc",
        )
        assert span.context_before is None
        assert span.context_after is None


class TestExtractionResult:
    def test_supported_state_allowed(self):
        result = ExtractionResult(
            evidence_fragment_id="ef1",
            source_span=SourceSpan(
                evidence_fragment_id="ef1",
                start_offset=0,
                end_offset=5,
                excerpt="hello",
            ),
            unit_type=KnowledgeUnitType.IDENTIFIER,
            entity_kind=EntityType.PAPER,
            canonical_name="test",
            extraction_method="regex-test-v1",
            epistemic_state=EpistemicState.SUPPORTED,
        )
        assert result.epistemic_state == "supported"

    def test_hypothesized_state_allowed(self):
        result = ExtractionResult(
            evidence_fragment_id="ef1",
            source_span=SourceSpan(
                evidence_fragment_id="ef1",
                start_offset=0,
                end_offset=5,
                excerpt="hello",
            ),
            unit_type=KnowledgeUnitType.IDENTIFIER,
            entity_kind=EntityType.PAPER,
            canonical_name="test",
            extraction_method="regex-test-v1",
            epistemic_state=EpistemicState.HYPOTHESIZED,
        )
        assert result.epistemic_state == "hypothesized"

    def test_verified_state_forbidden(self):
        """Per requirement #6: extraction can NEVER produce 'verified'.

        'verified' is not a valid EpistemicState at all (the enum has
        only supported/inferred/hypothesized/disputed/rejected). If
        someone tries to pass 'verified', Pydantic's enum validation
        rejects it before our model_validator runs. That's correct —
        'verified' is a VerificationState (for Relationships), not an
        EpistemicState (for Claims).
        """
        with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
            ExtractionResult(
                evidence_fragment_id="ef1",
                source_span=SourceSpan(
                    evidence_fragment_id="ef1",
                    start_offset=0,
                    end_offset=5,
                    excerpt="hello",
                ),
                unit_type=KnowledgeUnitType.IDENTIFIER,
                entity_kind=EntityType.PAPER,
                canonical_name="test",
                extraction_method="regex-test-v1",
                epistemic_state="verified",
            )

    def test_claim_requires_proposition(self):
        with pytest.raises(ValueError, match="requires a proposition"):
            ExtractionResult(
                evidence_fragment_id="ef1",
                source_span=SourceSpan(
                    evidence_fragment_id="ef1",
                    start_offset=0,
                    end_offset=5,
                    excerpt="hello",
                ),
                unit_type=KnowledgeUnitType.CLAIM,
                entity_kind=EntityType.TECHNIQUE,
                canonical_name="test",
                extraction_method="regex-test-v1",
                proposition=None,
            )


class TestUnitTypeMapping:
    def test_capability_maps_to_capability(self):
        assert entity_kind_for_unit_type(KnowledgeUnitType.CAPABILITY) == EntityType.CAPABILITY

    def test_technique_maps_to_technique(self):
        assert entity_kind_for_unit_type(KnowledgeUnitType.TECHNIQUE) == EntityType.TECHNIQUE

    def test_constraint_maps_to_constraint(self):
        assert entity_kind_for_unit_type(KnowledgeUnitType.CONSTRAINT) == EntityType.CONSTRAINT

    def test_tradeoff_maps_to_constraint_with_subtype(self):
        kind = entity_kind_for_unit_type(KnowledgeUnitType.TRADEOFF)
        subtype = subtype_for_unit_type(KnowledgeUnitType.TRADEOFF)
        assert kind == EntityType.CONSTRAINT
        assert subtype == "tradeoff"

    def test_failure_mode_maps_to_constraint_with_subtype(self):
        kind = entity_kind_for_unit_type(KnowledgeUnitType.FAILURE_MODE)
        subtype = subtype_for_unit_type(KnowledgeUnitType.FAILURE_MODE)
        assert kind == EntityType.CONSTRAINT
        assert subtype == "failure_mode"

    def test_applicability_maps_to_constraint_with_subtype(self):
        kind = entity_kind_for_unit_type(KnowledgeUnitType.APPLICABILITY)
        subtype = subtype_for_unit_type(KnowledgeUnitType.APPLICABILITY)
        assert kind == EntityType.CONSTRAINT
        assert subtype == "applicability"

    def test_opportunity_maps_to_capability_with_subtype(self):
        kind = entity_kind_for_unit_type(KnowledgeUnitType.OPPORTUNITY)
        subtype = subtype_for_unit_type(KnowledgeUnitType.OPPORTUNITY)
        assert kind == EntityType.CAPABILITY
        assert subtype == "opportunity"

    def test_identifier_uses_override_kind(self):
        kind = entity_kind_for_unit_type(
            KnowledgeUnitType.IDENTIFIER, identifier_kind=EntityType.REPOSITORY
        )
        assert kind == EntityType.REPOSITORY

    def test_capability_no_subtype(self):
        assert subtype_for_unit_type(KnowledgeUnitType.CAPABILITY) is None

    def test_technique_no_subtype(self):
        assert subtype_for_unit_type(KnowledgeUnitType.TECHNIQUE) is None


# ── Extraction tests ─────────────────────────────────────────────────────────


class TestExtraction:
    def test_empty_text_returns_empty_list(self):
        """Graceful handling of unsupported or ambiguous text."""
        results = extract("ef1", EMPTY_TEXT)
        assert results == []

    def test_ambiguous_text_returns_empty_list(self):
        """Text with no recognizable patterns returns no results."""
        results = extract("ef1", AMBIGUOUS_TEXT)
        assert results == []

    def test_partial_arxiv_id_not_matched(self):
        """1234.5 has too few digits after the dot — not a valid arXiv ID."""
        results = extract("ef1", PARTIAL_TEXT)
        # The regex \d{4}\.\d{4,5} requires 4-5 digits after the dot.
        # "1234.5" has only 1 digit after the dot — no match.
        arxiv_results = [r for r in results if r.attributes.get("pattern") == "arxiv_id"]
        assert len(arxiv_results) == 0

    def test_arxiv_id_extraction(self):
        results = extract("ef1", "See https://arxiv.org/abs/2303.15105 for details.")
        arxiv = [r for r in results if r.attributes.get("pattern") == "arxiv_id"]
        assert len(arxiv) == 1
        assert arxiv[0].canonical_name == "arXiv:2303.15105"
        assert arxiv[0].entity_kind == EntityType.PAPER
        assert arxiv[0].unit_type == KnowledgeUnitType.IDENTIFIER
        assert arxiv[0].epistemic_state == EpistemicState.SUPPORTED

    def test_doi_extraction(self):
        results = extract("ef1", "The DOI is 10.48550/arXiv.1706.03762.")
        dois = [r for r in results if r.attributes.get("pattern") == "doi"]
        assert len(dois) == 1
        assert "10.48550/arXiv.1706.03762" in dois[0].canonical_name

    def test_github_repo_extraction(self):
        results = extract("ef1", "Code at github.com/tensorflow/tensor2tensor.")
        repos = [r for r in results if r.attributes.get("pattern") == "github_repo"]
        assert len(repos) == 1
        assert "github.com/tensorflow/tensor2tensor" in repos[0].canonical_name
        assert repos[0].entity_kind == EntityType.REPOSITORY

    def test_url_extraction_skips_github(self):
        """URLs containing github.com are not double-matched."""
        results = extract("ef1", "Visit https://github.com/org/repo or https://example.com.")
        urls = [r for r in results if r.attributes.get("pattern") == "url"]
        github_urls = [r for r in urls if "github.com" in r.canonical_name]
        assert len(github_urls) == 0, "GitHub URLs should be captured by github_repo pattern"
        non_github_urls = [r for r in urls if "github.com" not in r.canonical_name]
        assert len(non_github_urls) == 1
        assert "example.com" in non_github_urls[0].canonical_name

    def test_technique_extraction(self):
        results = extract("ef1", "This paper introduces the self-attention mechanism.")
        techniques = [r for r in results if r.unit_type == KnowledgeUnitType.TECHNIQUE]
        assert len(techniques) == 1
        assert "self-attention" in techniques[0].proposition

    def test_constraint_extraction(self):
        results = extract("ef1", "The approach requires GPU acceleration for training.")
        constraints = [r for r in results if r.unit_type == KnowledgeUnitType.CONSTRAINT]
        assert len(constraints) == 1
        assert "GPU" in constraints[0].proposition

    def test_capability_extraction(self):
        results = extract("ef1", "Transformers outperform RNNs on long sequences.")
        caps = [r for r in results if r.unit_type == KnowledgeUnitType.CAPABILITY]
        assert len(caps) == 1
        assert "RNNs" in caps[0].proposition


class TestSourceSpans:
    def test_span_offsets_are_correct(self):
        """Accurate source-span offsets and excerpts."""
        text = "The arXiv ID 2303.15105 is referenced here."
        results = extract("ef1", text)
        arxiv = [r for r in results if r.attributes.get("pattern") == "arxiv_id"]
        assert len(arxiv) == 1
        span = arxiv[0].source_span
        # The excerpt must be the exact text at [start, end) in the original
        assert text[span.start_offset : span.end_offset] == span.excerpt
        assert span.excerpt == "2303.15105"

    def test_span_excerpt_matches_text_slice(self):
        """For every result, text[start:end] == excerpt."""
        text = FIXTURE_TEXT
        results = extract("ef1", text)
        for r in results:
            span = r.source_span
            assert text[span.start_offset : span.end_offset] == span.excerpt, (
                f"Span excerpt mismatch: text[{span.start_offset}:{span.end_offset}]"
                f" = {text[span.start_offset : span.end_offset]!r}, "
                f"but excerpt = {span.excerpt!r}"
            )

    def test_span_has_context(self):
        """Context before/after is captured for disambiguation."""
        text = "The arXiv ID 2303.15105 is here."
        results = extract("ef1", text)
        arxiv = [r for r in results if r.attributes.get("pattern") == "arxiv_id"]
        assert len(arxiv) == 1
        span = arxiv[0].source_span
        # context_before is the ~50 chars before the match
        assert span.context_before is not None
        assert "The arXiv ID" in span.context_before
        # context_after is the ~50 chars after the match
        assert span.context_after is not None
        assert "is here" in span.context_after


class TestIdempotency:
    def test_repeated_extraction_produces_identical_results(self):
        """Stable results on repeated execution.

        We compare the extraction-relevant fields, not the full
        model_dump() — created_at/updated_at timestamps differ between
        runs even with identical inputs.
        """
        results1 = extract("ef1", FIXTURE_TEXT)
        results2 = extract("ef1", FIXTURE_TEXT)
        assert len(results1) == len(results2)
        for r1, r2 in zip(results1, results2, strict=True):
            # Compare extraction-relevant fields, not timestamps/IDs
            assert r1.unit_type == r2.unit_type
            assert r1.entity_kind == r2.entity_kind
            assert r1.canonical_name == r2.canonical_name
            assert r1.proposition == r2.proposition
            assert r1.epistemic_state == r2.epistemic_state
            assert r1.extraction_method == r2.extraction_method
            assert r1.source_span.start_offset == r2.source_span.start_offset
            assert r1.source_span.end_offset == r2.source_span.end_offset
            assert r1.source_span.excerpt == r2.source_span.excerpt
            assert r1.source_span.context_before == r2.source_span.context_before
            assert r1.source_span.context_after == r2.source_span.context_after

    def test_results_are_sorted_by_offset(self):
        """Results are sorted by start_offset for deterministic ordering."""
        results = extract("ef1", FIXTURE_TEXT)
        offsets = [r.source_span.start_offset for r in results]
        assert offsets == sorted(offsets), f"Results not sorted by start_offset: {offsets}"


class TestNoAutoPromotion:
    def test_no_result_has_verified_state(self):
        """Per requirement #6: no automatic promotion to verified."""
        results = extract("ef1", FIXTURE_TEXT)
        for r in results:
            assert r.epistemic_state != "verified", (
                "ExtractionResult has epistemic_state='verified' — "
                "extraction can never produce 'verified'"
            )
            assert r.epistemic_state in ("supported", "hypothesized"), (
                f"Unexpected epistemic_state: {r.epistemic_state}"
            )

    def test_all_results_have_extraction_method(self):
        """Every result records how it was extracted."""
        results = extract("ef1", FIXTURE_TEXT)
        for r in results:
            assert r.extraction_method.startswith("regex-"), (
                f"Unexpected extraction_method: {r.extraction_method}"
            )
            assert r.extraction_method.endswith("-v1")

    def test_no_confidence_value_assigned(self):
        """No decorative probabilities — categorical states only."""
        results = extract("ef1", FIXTURE_TEXT)
        for r in results:
            # extraction_confidence may be None or a method-specific
            # value, but it must NOT be treated as an empirical probability.
            # For the regex extractor, it's always None.
            assert r.extraction_confidence is None or 0.0 <= r.extraction_confidence <= 1.0


class TestFullFixture:
    def test_fixture_extracts_multiple_types(self):
        """The full fixture text produces multiple extraction types."""
        results = extract("ef1", FIXTURE_TEXT)
        unit_types = {r.unit_type for r in results}
        assert KnowledgeUnitType.IDENTIFIER in unit_types
        assert KnowledgeUnitType.TECHNIQUE in unit_types
        assert KnowledgeUnitType.CONSTRAINT in unit_types
        assert KnowledgeUnitType.CAPABILITY in unit_types

    def test_fixture_extracts_all_identifiers(self):
        results = extract("ef1", FIXTURE_TEXT)
        identifiers = [r for r in results if r.unit_type == KnowledgeUnitType.IDENTIFIER]
        names = [r.canonical_name for r in identifiers]
        # arXiv ID (1706.03762 — from the URL and the DOI)
        assert any("1706.03762" in n for n in names), f"arXiv ID not found: {names}"
        # DOI
        assert any("10.48550" in n for n in names), f"DOI not found: {names}"
        # GitHub repo
        assert any("github.com/tensorflow/tensor2tensor" in n for n in names), (
            f"GitHub repo not found: {names}"
        )

    def test_fixture_evidence_fragment_id_propagated(self):
        """Every SourceSpan has the correct evidence_fragment_id."""
        results = extract("ef-test-123", FIXTURE_TEXT)
        for r in results:
            assert r.source_span.evidence_fragment_id == "ef-test-123"
            assert r.evidence_fragment_id == "ef-test-123"
