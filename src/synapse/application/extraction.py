"""Deterministic knowledge extractor — regex-based pattern matching.

Per the user's G03-T01 authorization §4: implement a small deterministic
extractor for supported technical patterns, with explicit evidence spans.

Per §7: make extraction deterministic, idempotent and testable.

Supported patterns:
  - arXiv IDs: \\d{4}\\.\\d{4,5}  →  Entity(kind=paper)
  - DOIs: 10\\.\\d{4,}/\\S+  →  Entity(kind=paper)
  - GitHub repos: github.com/\\S+/\\S+  →  Entity(kind=repository)
  - URLs: https?://\\S+  →  Entity(kind=other) [fallback]
  - "introduces/enables/requires" patterns  →  Claim

Each extraction produces an ExtractionResult with a SourceSpan that
records the exact character offsets and excerpt of the matched text.

Extension point: a future LLMExtractor class can implement the same
``extract()`` interface and produce ExtractionResult objects with
``extraction_method="llm-future-v1"``. The persistence and verification
layers consume ExtractionResult regardless of how it was produced.
"""

from __future__ import annotations

import re
from typing import Any

from synapse.domain._base import EntityType, EpistemicState
from synapse.domain.extraction_contract import (
    ExtractionResult,
    KnowledgeUnitType,
    SourceSpan,
    entity_kind_for_unit_type,
    subtype_for_unit_type,
)

# ── Compiled patterns ────────────────────────────────────────────────────────
#
# Each pattern is a compiled regex with a named group for the matched
# text. The pattern is applied to the evidence fragment's extracted
# text (EvidenceFragment.exact_excerpt). Each match produces one
# ExtractionResult.

# arXiv IDs: 2303.15105, 2401.00001
# Not preceded by a digit (to avoid matching version numbers like 1.2303)
_ARXIV_ID = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?!\d)")

# DOIs: 10.48550/arXiv.1706.03762
_DOI = re.compile(r"(10\.\d{4,}/[^\s\"<>)]+)")

# GitHub repos: github.com/owner/repo
_GITHUB_REPO = re.compile(r"(github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")

# URLs: https://example.com/path
_URL = re.compile(r"(https?://[^\s\"<>)\]]+)")

# Technique introduction patterns:
# "This paper introduces the self-attention mechanism"
# "We propose a novel approach called X"
# "X allows/allows for/enables Y"
_INTRODUCES = re.compile(
    r"(?:this (?:paper|work|study)\s+(?:introduces|proposes|presents)\s+"
    r"|we (?:introduce|propose|present)\s+"
    r"|allows?\s+(?:for\s+)?|enables?\s+)"
    r"([A-Za-z][A-Za-z0-9\s\-]{2,60}?)(?:[.,;]|\n|$)",
    re.IGNORECASE,
)

# Constraint patterns:
# "X requires/needs Y"
# "X is limited by Y"
_CONSTRAINT = re.compile(
    r"(?:requires?|needs?|is limited by|is constrained by)\s+"
    r"([A-Za-z][A-Za-z0-9\s\-]{2,60}?)(?:[.,;]|\n|$)",
    re.IGNORECASE,
)

# Capability/outperforms patterns:
# "X outperforms Y"
# "X improves Y"
_OUTPERFORMS = re.compile(
    r"(?:outperforms?|improves?\s+(?:over|upon)?)\s+"
    r"([A-Za-z][A-Za-z0-9\s\-]{2,60}?)(?:[.,;]|\n|$)",
    re.IGNORECASE,
)


def _make_span(
    evidence_fragment_id: str,
    text: str,
    match: re.Match,
    context_chars: int = 50,
) -> SourceSpan:
    """Build a SourceSpan from a regex match.

    The span covers the matched group (group(1) if present, else group(0)).
    Offsets are relative to ``text`` (the EvidenceFragment.exact_excerpt).
    """
    # Prefer group(1) — the captured content — over group(0) — the
    # full match including the pattern prefix.
    if match.lastindex and match.lastindex >= 1:
        start = match.start(1)
        end = match.end(1)
    else:
        start = match.start(0)
        end = match.end(0)

    excerpt = text[start:end]
    context_before = text[max(0, start - context_chars) : start] or None
    context_after = text[end : end + context_chars] or None

    return SourceSpan(
        evidence_fragment_id=evidence_fragment_id,
        start_offset=start,
        end_offset=end,
        excerpt=excerpt,
        context_before=context_before,
        context_after=context_after,
    )


def _make_identifier_result(
    evidence_fragment_id: str,
    text: str,
    match: re.Match,
    *,
    canonical_name: str,
    entity_kind: EntityType,
    pattern_name: str,
) -> ExtractionResult:
    """Build an ExtractionResult for an identifier match."""
    span = _make_span(evidence_fragment_id, text, match)
    return ExtractionResult(
        evidence_fragment_id=evidence_fragment_id,
        source_span=span,
        unit_type=KnowledgeUnitType.IDENTIFIER,
        entity_kind=entity_kind,
        canonical_name=canonical_name,
        attributes={
            "pattern": pattern_name,
            "raw_match": match.group(0),
        },
        proposition=None,
        epistemic_state=EpistemicState.SUPPORTED,
        extraction_method=f"regex-{pattern_name}-v1",
    )


def _make_claim_result(
    evidence_fragment_id: str,
    text: str,
    match: re.Match,
    *,
    proposition: str,
    unit_type: KnowledgeUnitType,
    pattern_name: str,
) -> ExtractionResult:
    """Build an ExtractionResult for a claim/technique/constraint match."""
    span = _make_span(evidence_fragment_id, text, match)
    entity_kind = entity_kind_for_unit_type(unit_type)
    subtype = subtype_for_unit_type(unit_type)
    attributes: dict[str, Any] = {
        "pattern": pattern_name,
        "raw_match": match.group(0),
    }
    if subtype:
        attributes["subtype"] = subtype

    return ExtractionResult(
        evidence_fragment_id=evidence_fragment_id,
        source_span=span,
        unit_type=unit_type,
        entity_kind=entity_kind,
        canonical_name=proposition[:60],  # use the proposition as the name
        attributes=attributes,
        proposition=proposition,
        epistemic_state=EpistemicState.SUPPORTED,
        extraction_method=f"regex-{pattern_name}-v1",
    )


def extract(
    evidence_fragment_id: str,
    text: str,
) -> list[ExtractionResult]:
    """Extract typed knowledge units from evidence text.

    Args:
        evidence_fragment_id: The ID of the EvidenceFragment this text
            came from. Used in every SourceSpan.
        text: The extracted text (EvidenceFragment.exact_excerpt).

    Returns:
        A list of ExtractionResult objects. Each result has a SourceSpan
        with exact offsets. Results are sorted by start_offset for
        deterministic ordering.

    The extraction is:
      - **Deterministic**: same input always produces same output
      - **Idempotent**: running extract() twice on the same text produces
        identical results (the results are value objects, not persisted
        rows — persistence handles idempotency via dedup on
        (evidence_fragment_id, start_offset, end_offset))
      - **Never promotes to verified**: all results have
        epistemic_state in {supported, hypothesized} — never "verified"
    """
    if not text:
        return []

    results: list[ExtractionResult] = []

    # ── Identifier patterns ────────────────────────────────────────

    for match in _ARXIV_ID.finditer(text):
        arxiv_id = match.group(1)
        results.append(
            _make_identifier_result(
                evidence_fragment_id,
                text,
                match,
                canonical_name=f"arXiv:{arxiv_id}",
                entity_kind=EntityType.PAPER,
                pattern_name="arxiv_id",
            )
        )

    for match in _DOI.finditer(text):
        doi = match.group(1).rstrip(".,;)")
        results.append(
            _make_identifier_result(
                evidence_fragment_id,
                text,
                match,
                canonical_name=f"DOI:{doi}",
                entity_kind=EntityType.PAPER,
                pattern_name="doi",
            )
        )

    for match in _GITHUB_REPO.finditer(text):
        repo = match.group(1)
        results.append(
            _make_identifier_result(
                evidence_fragment_id,
                text,
                match,
                canonical_name=repo,
                entity_kind=EntityType.REPOSITORY,
                pattern_name="github_repo",
            )
        )

    # URLs — only if they're not already part of a GitHub match.
    # We skip URLs that contain "github.com" to avoid duplicates.
    for match in _URL.finditer(text):
        url = match.group(1).rstrip(".,;)")
        if "github.com" in url:
            continue  # already captured by _GITHUB_REPO
        results.append(
            _make_identifier_result(
                evidence_fragment_id,
                text,
                match,
                canonical_name=url[:512],
                entity_kind=EntityType.TOOL,  # URLs are often tool/project pages
                pattern_name="url",
            )
        )

    # ── Claim patterns (technique, constraint, capability) ────────

    for match in _INTRODUCES.finditer(text):
        technique = match.group(1).strip()
        if len(technique) < 3:
            continue
        results.append(
            _make_claim_result(
                evidence_fragment_id,
                text,
                match,
                proposition=f"Source mentions technique: {technique}",
                unit_type=KnowledgeUnitType.TECHNIQUE,
                pattern_name="introduces",
            )
        )

    for match in _CONSTRAINT.finditer(text):
        constraint = match.group(1).strip()
        if len(constraint) < 3:
            continue
        results.append(
            _make_claim_result(
                evidence_fragment_id,
                text,
                match,
                proposition=f"Source states constraint: {constraint}",
                unit_type=KnowledgeUnitType.CONSTRAINT,
                pattern_name="constraint",
            )
        )

    for match in _OUTPERFORMS.finditer(text):
        capability = match.group(1).strip()
        if len(capability) < 3:
            continue
        results.append(
            _make_claim_result(
                evidence_fragment_id,
                text,
                match,
                proposition=f"Source claims capability: {capability}",
                unit_type=KnowledgeUnitType.CAPABILITY,
                pattern_name="outperforms",
            )
        )

    # Sort by start_offset for deterministic ordering.
    results.sort(key=lambda r: r.source_span.start_offset)
    return results


# ── Extension point ──────────────────────────────────────────────────────────
#
# A future LLM-based extractor would implement the same interface:
#
# class LLMExtractor:
#     def extract(self, evidence_fragment_id: str, text: str) -> list[ExtractionResult]:
#         # Use an LLM to identify typed knowledge units in the text.
#         # Must produce ExtractionResult objects with:
#         #   - extraction_method = "llm-future-v1"
#         #   - epistemic_state in {supported, hypothesized} (never "verified")
#         #   - A SourceSpan with exact offsets into ``text``
#         # The persistence and verification layers are unchanged.
#         ...
#
# The deterministic extract() function above is the reference implementation.
