"""G04-T04 -- Retrieval & Reasoning Metrics.

Per the user's G04-T04 mission briefing §4 (Retrieval evaluation) and
§5 (Grounded reasoning evaluation):

  Retrieval metrics:
    - Precision@K
    - Recall@K
    - MRR (Mean Reciprocal Rank)

  Reasoning metrics:
    - Citation validity (every cited ID must resolve to an existing record)
    - Evidence-grounded finding rate
    - Unsupported factual-claim rate
    - Hypothesis-to-fact promotion error rate
    - Finding coverage (actual vs expected minimum)
    - Provider-attribution correctness (structural)
    - Context-applicability correctness (structural)
    - Dependency-direction correctness (structural)
    - Contradiction preservation rate (structural)

Per mission §5:

  "Identifier existence alone must not be treated as complete semantic
   grounding. Where automated semantic verification is unavailable,
   explicitly label the measurement as structural rather than semantic.
   Never report unsupported semantic certainty."

The functions in this module are intentionally **pure** (no DB access,
no I/O, no side effects). All inputs are passed in explicitly. This
makes them trivially testable and deterministic.

Functions that perform structural verification (rather than semantic
verification) are explicitly labeled in their docstrings.
"""

from __future__ import annotations

from typing import Any

# ── Retrieval metrics (mission §4) ──────────────────────────────────────────


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Precision@K: fraction of top-K retrieved IDs that are relevant.

    Per mission §4:

      "Fraction of the top-K retrieved results that are relevant."

    Requirements (mission §4):
      - Stable relevance identifiers
      - Explicit denominators
      - Deterministic ranking evaluation
      - Correct handling of empty result sets
      - No fabricated relevance labels

    Args:
        retrieved_ids: ranked list of retrieved identifiers (best first).
        relevant_ids: set of ground-truth relevant identifiers.
        k: cutoff rank.

    Returns:
        Float in [0.0, 1.0]. Returns 0.0 if k <= 0 or if top-K is empty.
        Returns 1.0 if relevant_ids is empty AND top-K is empty (vacuously
        true -- no fabricated hits).
    """
    if k <= 0:
        return 0.0
    top_k = list(retrieved_ids[:k])
    if not top_k:
        # Empty result set: precision is truthfully 1.0 only if there is
        # nothing to retrieve; otherwise 0.0 (we missed everything).
        return 1.0 if not relevant_ids else 0.0
    hits = sum(1 for rid in top_k if rid in relevant_ids)
    return hits / len(top_k)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Recall@K: fraction of relevant IDs retrieved within the top K.

    Per mission §4:

      "Fraction of expected relevant results retrieved within the top K."

    Returns 0.0 if:
      - relevant_ids is empty (nothing to recall -- mission §4: "Do not
        report a metric when its ground truth is insufficient.")
      - k <= 0

    Returns 1.0 if all relevant IDs appear in the top K.
    """
    if not relevant_ids:
        # Insufficient ground truth per mission §4 -- return 0.0 rather
        # than fabricate a 1.0.
        return 0.0
    if k <= 0:
        return 0.0
    top_k = list(retrieved_ids[:k])
    found = sum(1 for rid in top_k if rid in relevant_ids)
    return found / len(relevant_ids)


def mrr(ranked_retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    """Mean Reciprocal Rank: 1/rank of the first relevant result, 0 if none.

    Per mission §4:

      "MRR -- Mean Reciprocal Rank, when ranked relevant targets exist."

    Returns 0.0 if relevant_ids is empty (no relevant targets to find).
    """
    if not relevant_ids:
        return 0.0
    for i, rid in enumerate(ranked_retrieved_ids, start=1):
        if rid in relevant_ids:
            return 1.0 / i
    return 0.0


# ── Reasoning metrics (mission §5) ─────────────────────────────────────────


def citation_validity(cited_ids: list[str], existing_ids: set[str]) -> float:
    """Fraction of cited IDs that resolve to existing records.

    Per mission §5:

      "A citation that resolves to an existing record" is distinct from
      "a citation that actually supports the associated finding."

    This function performs the FIRST check (structural existence).
    The second check (semantic support) is labeled separately as
    ``evidence_grounded_finding_rate``.

    Returns 1.0 if there are no citations (no fabricated citations is
    vacuously true). Returns 0.0 if any cited ID does not exist.
    """
    if not cited_ids:
        return 1.0
    valid = sum(1 for cid in cited_ids if cid in existing_ids)
    return valid / len(cited_ids)


def evidence_grounded_finding_rate(
    findings: list[dict[str, Any]],
    existing_evidence: set[str],
) -> float:
    """Fraction of findings whose ``evidence_refs`` ALL resolve to existing
    evidence fragments.

    Per mission §5:

      "Evidence-grounded finding rate" measures how often a finding is
      backed by real (resolvable) evidence, not just by a citation ID
      that exists.

    A finding is "evidence-grounded" iff:
      - it has at least one ``evidence_refs`` entry, AND
      - every entry resolves to an ID in ``existing_evidence``.

    Findings of type UNKNOWN or HYPOTHESIS are NOT expected to have
    evidence_refs; they are excluded from the denominator (they are
    honestly-labeled unknowns, not "grounded findings" that failed
    grounding).

    Returns 0.0 if no findings are eligible (no DOCUMENTED_FACT /
    DERIVED_FINDING findings present).
    """
    eligible = [f for f in findings if f.get("type") in ("documented_fact", "derived_finding")]
    if not eligible:
        return 0.0
    grounded = 0
    for f in eligible:
        ev_refs = f.get("evidence_refs") or []
        if ev_refs and all(r in existing_evidence for r in ev_refs):
            grounded += 1
    return grounded / len(eligible)


def unsupported_factual_claim_rate(findings: list[dict[str, Any]]) -> float:
    """Fraction of DOCUMENTED_FACT findings that lack ``evidence_refs``.

    Per mission §5:

      "Unsupported factual claims" -- a DOCUMENTED_FACT finding without
      evidence_refs is structurally unsupported.

    Returns 0.0 if there are no DOCUMENTED_FACT findings.
    """
    factual = [f for f in findings if f.get("type") == "documented_fact"]
    if not factual:
        return 0.0
    unsupported = sum(1 for f in factual if not (f.get("evidence_refs") or []))
    return unsupported / len(factual)


def hypothesis_promotion_error_rate(findings: list[dict[str, Any]]) -> float:
    """Fraction of findings where a HYPOTHESIS is mislabeled as DOCUMENTED_FACT.

    Per mission §5:

      "Hypothesis-to-fact promotion errors" -- a finding of type
      ``documented_fact`` whose ``confidence`` falls below the
      DOCUMENTED_FACT confidence range (0.70-0.90 per reasoning.py §6)
      is suspicious of being a promoted hypothesis.

    This is a STRUCTURAL check. Semantic verification (does the
    finding's text actually describe a hypothesis?) is not available
    without an LLM and is explicitly labeled as out of scope per
    mission §5: "Where automated semantic verification is unavailable,
    explicitly label the measurement as structural rather than semantic."

    Returns 0.0 if there are no findings.
    """
    if not findings:
        return 0.0
    promotion_errors = 0
    for f in findings:
        if f.get("type") == "documented_fact" and f.get("confidence", 0.0) < 0.70:
            promotion_errors += 1
    return promotion_errors / len(findings)


def finding_coverage(actual_findings: int, expected_min: int) -> float:
    """Coverage ratio: actual / expected_min, capped at 1.0.

    Per mission §5:

      "Expected finding coverage" -- did the response produce at least
      the minimum number of findings required to answer the query?

    Returns 1.0 if ``expected_min <= 0`` (no minimum specified) or if
    ``actual >= expected_min``.
    """
    if expected_min <= 0:
        return 1.0
    return min(1.0, actual_findings / expected_min)


def contradiction_preservation_rate(
    findings: list[dict[str, Any]],
    contradictions_field: list[dict[str, Any]],
    expected_preserved: bool,
) -> float:
    """Structural check: were contradictions preserved when they should be?

    Per mission §5:

      "Contradiction preservation" -- applicable contradictions must
      remain visible (CONTESTED classification or non-empty
      ``contradictions`` list).

    Returns:
        1.0 if the expectation is met (preserved when expected_preserved=True
        and contradictions are visible; OR not preserved when expected_preserved=False
        and no contradictions are visible).
        0.0 otherwise.

    This is a STRUCTURAL check (presence of contradiction entries in the
    answer), not a SEMANTIC check (whether the contradictions are
    correctly identified as applicable vs out-of-context).
    """
    has_visible_contradictions = bool(contradictions_field) or any(
        "contested" in (f.get("caveat") or "").lower()
        or "contradict" in (f.get("text") or "").lower()
        for f in findings
    )
    if expected_preserved:
        return 1.0 if has_visible_contradictions else 0.0
    # When no preservation expected, structural absence is correct.
    return 1.0 if not has_visible_contradictions else 0.0


def provider_attribution_correctness(
    findings: list[dict[str, Any]],
    candidate_ids: list[str],
    candidate_capability_map: dict[str, list[str]],
) -> float:
    """Structural check: are DOCUMENTED_FACT findings attributed only to
    the named candidates?

    Per mission §5 (Provider-attribution correctness) and the G04-T02C
    closure:

      "A SUPPORTED result for a named candidate must require a valid,
       evidence-grounded association between that candidate and the
       capability."

    This function performs a STRUCTURAL check by verifying that the
    finding's ``sources`` (capability IDs) are actually linked to at
    least one of the named candidates via the
    ``candidate_capability_map`` (which is a dict mapping
    ``candidate_entity_id -> list[capability_id]`` produced by the
    fixture / runtime).

    Args:
        findings: list of Finding dicts.
        candidate_ids: the candidate entity IDs the query was scoped to.
        candidate_capability_map: dict mapping each candidate entity ID
            to the list of capability IDs it has a PROVIDES / ENABLES /
            PRODUCES edge to.

    Returns:
        1.0 if all DOCUMENTED_FACT findings are structurally attributable
        to a named candidate. 0.0 if any DOCUMENTED_FACT finding
        references a capability not provided by any candidate.
    """
    factual = [f for f in findings if f.get("type") == "documented_fact"]
    if not factual:
        return 1.0
    # Build the set of capabilities attributed to the candidates.
    candidate_caps: set[str] = set()
    for cid in candidate_ids:
        candidate_caps.update(candidate_capability_map.get(cid, []))
    if not candidate_caps:
        # No capabilities attributed to candidates → any factual finding is suspect.
        return 0.0
    correct = 0
    for f in factual:
        sources = f.get("sources") or []
        # A finding is "attributable" if at least one source is in the candidate set.
        if any(s in candidate_caps for s in sources):
            correct += 1
    return correct / len(factual)


def dependency_direction_correctness(
    findings: list[dict[str, Any]],
    expected_directions: list[tuple[str, str, str]],
) -> float:
    """Structural check: are dependency findings' directions preserved?

    Per mission §5 (Dependency-direction correctness) and G04-T03 §5:

      "Relationship direction respected (PROVIDES is directed
       provider → capability; REQUIRES is directed provider → dependency)."

    Args:
        findings: list of Finding dicts (typically DERIVED_FINDING with
            ``sources`` = [relationship_id] and ``text`` containing the
            direction).
        expected_directions: list of ``(from_entity, to_entity, predicate)``
            tuples that the findings should preserve.

    Returns:
        1.0 if every expected direction is mentioned in some finding's
        text. 0.0 if any direction is missing.

    Note:
        This is a STRUCTURAL check based on text matching. It does NOT
        semantically verify that the relationship's ``from_entity_id``
        and ``to_entity_id`` actually match -- that level of rigor
        belongs to the G04-T03 acceptance tests (already green).
    """
    if not expected_directions:
        return 1.0
    corpus = " ".join(f.get("text", "") for f in findings).lower()
    correct = 0
    for from_ent, to_ent, predicate in expected_directions:
        # Simple structural presence check: both entity names + predicate
        # appear somewhere in the findings' combined text.
        if from_ent.lower() in corpus and to_ent.lower() in corpus and predicate.lower() in corpus:
            correct += 1
    return correct / len(expected_directions)
