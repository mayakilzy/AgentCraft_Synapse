"""G06-T01 — Future Signal Identification.

Deterministic identification of current knowledge-base signals.

Per the approved G06 plan (section 4.1, sections 4.1.1-4.1.4, section 13.1, section 16):

- **Layer 1 only** (current evidence snapshot). Layer 2 (verified
  historical change) is NOT available — Synapse does not persist
  comparable KB snapshots over time.
- Every signal has ``temporal_type="snapshot"`` and ``snapshot_only=True``.
- No ``gap_transition`` or ``dependency_shift`` signal kinds exist
  (they would require Layer 2 data).
- Distinct source URLs are NOT independent origins. The
  ``independent_origin_count`` is conservative (1, per PRB-04) unless
  explicit provenance metadata (distinct ``SourceRow.publisher`` or
  ``SourceRow.author``) is available and differs. Even then, differing
  unverified publisher strings are NOT sufficient — the default remains
  conservative.
- No fabricated evidence, trends, growth rates, or measurements.
- Signal IDs are deterministic (hash of kind + entity/capability +
  sorted evidence_refs). Repeated reads with identical KB state produce
  identical signal content, ordering, and IDs.
- No write path: the service is read-only. No new entities, rows, or
  audit events are created.

Five snapshot signal kinds are supported:

1. ``gap_classification_snapshot`` — the current ``analyze_gap()``
   classification for a capability.
2. ``evidence_footprint_snapshot`` — the current count of evidence
   fragments and distinct source URLs citing a capability.
3. ``combination_density_snapshot`` — the current number of
   ``combine_knowledge()`` candidates that reference a capability.
4. ``contradiction_exposure_snapshot`` — capabilities with ``CONTESTED``
   gap classifications (active disagreement in the current KB).
5. ``dependency_graph_snapshot`` — the current set of PROVIDES /
   DEPENDS_ON / LIMITS edges incident to a capability.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.capability_registry import list_capabilities
from synapse.application.gap_analyzer import (
    DEFAULT_LIMIT as GAP_DEFAULT_LIMIT,
)
from synapse.application.gap_analyzer import (
    GapAnalysisResult,
    analyze_gap,
)
from synapse.application.innovation import combine_knowledge
from synapse.storage.models import (
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    RelationshipRow,
    SourceRow,
)

__all__ = [
    "SIGNAL_KINDS",
    "SIGNAL_TEMPORAL_TYPE",
    "identify_future_signals",
]

# ── Constants ──────────────────────────────────────────────────────────────

SIGNAL_TEMPORAL_TYPE = "snapshot"

SIGNAL_KINDS = (
    "gap_classification_snapshot",
    "evidence_footprint_snapshot",
    "combination_density_snapshot",
    "contradiction_exposure_snapshot",
    "dependency_graph_snapshot",
)

# Predicates considered "dependency graph" edges for the
# dependency_graph_snapshot signal. Only existing predicates from the
# 16-predicate vocabulary (src/synapse/domain/relationship.py) are used.
_DEPENDENCY_GRAPH_PREDICATES = ("PROVIDES", "DEPENDS_ON", "LIMITS")

# Conservative default for independent-origin count, per PRB-04
# (verification.py::_count_independent_primary_origins always returns 1).
_CONSERVATIVE_INDEPENDENT_ORIGIN_COUNT = 1


# ── Public API ─────────────────────────────────────────────────────────────


async def identify_future_signals(
    session: AsyncSession,
    *,
    focus_capability_id: str | None = None,
    focus_entity_id: str | None = None,
    combination_problem_domain: str | None = None,
    combination_context: str | None = None,
    limit: int = 50,
    requester: str | None = None,
) -> dict[str, Any]:
    """Identify current knowledge-base signals (Layer 1 snapshots only).

    This is a **read-only** service. It does not persist any new record.

    Args:
        session: AsyncSession bound to the Synapse database.
        focus_capability_id: if provided, restrict signals to this
            capability. Otherwise all capabilities are scanned.
        focus_entity_id: if provided (and focus_capability_id is None),
            restrict signals to capabilities linked to this entity via
            PROVIDES/ENABLES/PRODUCES relationships. Otherwise all
            capabilities are scanned.
        combination_problem_domain: optional problem domain passed to
            ``combine_knowledge()`` for the combination_density_snapshot
            signal. If omitted, a generic default is used.
        combination_context: optional context passed to
            ``combine_knowledge()``.
        limit: max number of capabilities to scan (bounded to [1, 100]).
        requester: optional actor name (for logging only; no audit event
            is emitted by this read-only service).

    Returns:
        A dict with keys:
            - ``signals``: list of signal dicts (deterministic order).
            - ``temporal_type``: always ``"snapshot"``.
            - ``snapshot_only``: always ``True``.
            - ``generated_at``: ISO8601 response-generation timestamp
              (NOT a persisted observation timestamp).
            - ``limitations``: list of explicit limitation strings.
            - ``request_id``: stable request identifier.
    """
    limit = max(1, min(100, limit))
    request_id = _deterministic_request_id(
        focus_capability_id,
        focus_entity_id,
        combination_problem_domain,
        combination_context,
        limit,
    )

    # ── Resolve the capability set to scan ─────────────────────────────
    capabilities = await _resolve_capabilities(session, focus_capability_id, focus_entity_id, limit)

    if not capabilities:
        return _empty_result(request_id)

    # ── Gather the gap analysis once for all scanned capabilities ──────
    capability_names = [c["canonical_name"] for c in capabilities if c["canonical_name"]]
    gap_result: GapAnalysisResult | None = None
    if capability_names:
        gap_result = await analyze_gap(
            session,
            capability_names,
            context=combination_context,
            limit=min(len(capability_names), GAP_DEFAULT_LIMIT * 2),
            requester=requester or "g06-t01-future-signal",
        )

    # ── Build a capability_id → RequirementAssessment index ────────────
    gap_by_cap_id: dict[str, dict[str, Any]] = {}
    if gap_result:
        for req in gap_result.get("requirements", []):
            cap_id = req.get("capability_id")
            if cap_id:
                gap_by_cap_id[cap_id] = req

    # ── Compute combination density once (if a problem domain is given) ──
    # Distinguish success-with-zero from failure (Correction A):
    # - On success: combination_count = the real count (0 is factual).
    # - On failure: combination_count = None + an explicit limitation.
    combination_counts: dict[str, int] = {}
    combination_density_status = "computed"  # default: succeeded with 0
    combination_density_error: str | None = None
    if combination_problem_domain or capabilities:
        domain = combination_problem_domain or "technology landscape"
        try:
            combos = await combine_knowledge(
                session,
                domain,
                context=combination_context,
                requester=requester or "g06-t01-future-signal",
            )
            for combo in combos.get("combinations", []):
                for comp in combo.get("components", []):
                    comp_id = comp.get("entity_id") if isinstance(comp, dict) else comp
                    if comp_id:
                        combination_counts[comp_id] = combination_counts.get(comp_id, 0) + 1
        except Exception as exc:
            # combine_knowledge failed — do NOT pretend count is 0.
            # Mark as unavailable so consumers know the density is unknown.
            combination_density_status = "unavailable"
            combination_density_error = f"{type(exc).__name__}: {exc}"
            combination_counts = {}  # no reliable counts

    # ── Build signals for each capability ──────────────────────────────
    signals: list[dict[str, Any]] = []
    limitations: list[str] = []

    for cap in capabilities:
        cap_id = cap["id"]
        cap_name = cap["canonical_name"]
        gap_assessment = gap_by_cap_id.get(cap_id)

        # Gather evidence footprint for this capability
        evidence_info = await _gather_evidence_footprint(session, cap_id)

        # Gather dependency-graph edges
        dep_edges = await _gather_dependency_graph_edges(session, cap_id)

        # Combination count: None when density computation failed
        # (Correction A); the real count (possibly 0) when it succeeded.
        if combination_density_status == "unavailable":
            cap_combination_count: int | None = None
        else:
            cap_combination_count = combination_counts.get(cap_id, 0)

        cap_signals = _build_signals_for_capability(
            cap_id=cap_id,
            cap_name=cap_name,
            gap_assessment=gap_assessment,
            evidence_info=evidence_info,
            dep_edges=dep_edges,
            combination_count=cap_combination_count,
            combination_density_status=combination_density_status,
        )
        signals.extend(cap_signals)

    # Deterministic ordering: by (kind, capability_id) — stable across runs
    signals.sort(key=lambda s: (s["kind"], s["capability_id"] or ""))

    # Document limitations
    if combination_density_status == "unavailable":
        limitations.append(
            "combination_density_snapshot: combine_knowledge() failed "
            f"({combination_density_error}); combination_count is null "
            "(unavailable) for all capabilities. This is a computation "
            "failure, not a factual zero."
        )
    elif not combination_counts:
        limitations.append(
            "combination_density_snapshot: combine_knowledge() returned 0 "
            "combinations for the given domain; combination_count is 0 for "
            "all capabilities. This is a factual empty result (status="
            "'computed'), not an error."
        )
    limitations.append(
        "Layer 2 (verified historical change) is not available: Synapse does "
        "not persist comparable KB snapshots over time. All signals are "
        "Layer 1 (current snapshot). gap_transition and dependency_shift "
        "signal kinds are not supported."
    )
    limitations.append(
        "independent_origin_count is 0 when there are no evidence fragments; "
        "1 (conservative, per PRB-04) when there is >=1 fragment and "
        "provenance independence is unverified. G06-T01 does not introduce a "
        "new provenance inference algorithm; distinct source URLs are "
        "reported as distinct_source_uri_count but do not inflate "
        "independent_origin_count."
    )

    return {
        "signals": signals,
        "temporal_type": SIGNAL_TEMPORAL_TYPE,
        "snapshot_only": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "limitations": limitations,
        "request_id": request_id,
    }


# ── Signal builders ────────────────────────────────────────────────────────


def _build_signals_for_capability(
    *,
    cap_id: str,
    cap_name: str,
    gap_assessment: dict[str, Any] | None,
    evidence_info: dict[str, Any],
    dep_edges: list[dict[str, Any]],
    combination_count: int | None,
    combination_density_status: str,
) -> list[dict[str, Any]]:
    """Build all applicable signals for a single capability.

    Returns up to 5 signals (one per kind). Each signal is only emitted
    if it has grounded content.

    ``combination_count`` is ``None`` when ``combine_knowledge()`` failed
    (status="unavailable"); it is the real count (possibly 0) when
    status="computed" (Correction A).
    """
    signals: list[dict[str, Any]] = []

    classification = None
    if gap_assessment:
        classification = gap_assessment.get("classification")

    # Common evidence fields
    evidence_refs = evidence_info["fragment_ids"]
    distinct_source_uri_count = evidence_info["distinct_source_uri_count"]
    independent_origin_count = evidence_info["independent_origin_count"]
    first_seen_at = evidence_info["first_seen_at"]
    last_seen_at = evidence_info["last_seen_at"]

    # 1. gap_classification_snapshot — always emit if we have a classification
    if classification is not None:
        signals.append(
            _make_signal(
                kind="gap_classification_snapshot",
                capability_id=cap_id,
                capability_name=cap_name,
                evidence_refs=evidence_refs,
                gap_classification=classification,
                combination_count=combination_count,
                combination_density_status=combination_density_status,
                distinct_source_uri_count=distinct_source_uri_count,
                independent_origin_count=independent_origin_count,
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
                notes=(
                    f"Capability '{cap_name}' has current gap classification "
                    f"'{classification}'. This is a snapshot of the current KB "
                    f"state; no trend is inferred."
                ),
                extra={
                    "match_quality": gap_assessment.get("match_quality"),
                    "reason": gap_assessment.get("reason"),
                },
            )
        )

    # 2. evidence_footprint_snapshot — emit if there is ≥1 fragment OR
    #    the capability exists (so the caller can see the zero-footprint
    #    state honestly).
    signals.append(
        _make_signal(
            kind="evidence_footprint_snapshot",
            capability_id=cap_id,
            capability_name=cap_name,
            evidence_refs=evidence_refs,
            gap_classification=classification,
            combination_count=combination_count,
            combination_density_status=combination_density_status,
            distinct_source_uri_count=distinct_source_uri_count,
            independent_origin_count=independent_origin_count,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            notes=(
                f"Capability '{cap_name}' has {len(evidence_refs)} evidence "
                f"fragment(s) from {distinct_source_uri_count} distinct "
                f"source URL(s). independent_origin_count="
                f"{independent_origin_count} (conservative, per PRB-04). "
                f"No growth rate is inferred."
            ),
            extra={
                "fragment_count": len(evidence_refs),
            },
        )
    )

    # 3. combination_density_snapshot — always emit. When status="computed",
    #    combination_count is the real count (0 is factual). When
    #    status="unavailable", combination_count is None (Correction A).
    if combination_count is None:
        density_notes = (
            f"Capability '{cap_name}' combination density is UNAVAILABLE: "
            f"combine_knowledge() failed. combination_count is null. This "
            f"is a computation failure, not a factual zero."
        )
    else:
        density_notes = (
            f"Capability '{cap_name}' appears in {combination_count} "
            f"combine_knowledge() candidate(s) for the given domain "
            f"(status={combination_density_status}). This is a structural "
            f"snapshot of graph centrality, not a trend."
        )
    signals.append(
        _make_signal(
            kind="combination_density_snapshot",
            capability_id=cap_id,
            capability_name=cap_name,
            evidence_refs=evidence_refs,
            gap_classification=classification,
            combination_count=combination_count,
            combination_density_status=combination_density_status,
            distinct_source_uri_count=distinct_source_uri_count,
            independent_origin_count=independent_origin_count,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            notes=density_notes,
        )
    )

    # 4. contradiction_exposure_snapshot — emit only if classification is CONTESTED
    if classification == "contested":
        contradicting_refs: list[str] = []
        if gap_assessment:
            contradicting_refs = list(gap_assessment.get("contradicting_evidence", []))
        signals.append(
            _make_signal(
                kind="contradiction_exposure_snapshot",
                capability_id=cap_id,
                capability_name=cap_name,
                evidence_refs=evidence_refs,
                gap_classification=classification,
                combination_count=combination_count,
                combination_density_status=combination_density_status,
                distinct_source_uri_count=distinct_source_uri_count,
                independent_origin_count=independent_origin_count,
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
                notes=(
                    f"Capability '{cap_name}' has CONTESTED evidence: "
                    f"both supporting and contradicting evidence exist in "
                    f"the current KB. This is a snapshot of active "
                    f"disagreement, not a claim that disagreement is "
                    f"increasing."
                ),
                extra={
                    "contradicting_evidence_refs": contradicting_refs,
                },
            )
        )

    # 5. dependency_graph_snapshot — emit if there is ≥1 edge OR the
    #    capability exists (so the caller can see the zero-edge state).
    signals.append(
        _make_signal(
            kind="dependency_graph_snapshot",
            capability_id=cap_id,
            capability_name=cap_name,
            evidence_refs=evidence_refs,
            gap_classification=classification,
            combination_count=combination_count,
            combination_density_status=combination_density_status,
            distinct_source_uri_count=distinct_source_uri_count,
            independent_origin_count=independent_origin_count,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            notes=(
                f"Capability '{cap_name}' has {len(dep_edges)} incident "
                f"PROVIDES/DEPENDS_ON/LIMITS edge(s) in the current KB. "
                f"This is a snapshot of the dependency graph, not a "
                f"dependency shift."
            ),
            extra={
                "dependency_edges": dep_edges,
            },
        )
    )

    return signals


def _make_signal(
    *,
    kind: str,
    capability_id: str,
    capability_name: str,
    evidence_refs: list[str],
    gap_classification: str | None,
    combination_count: int | None,
    combination_density_status: str,
    distinct_source_uri_count: int,
    independent_origin_count: int,
    first_seen_at: str | None,
    last_seen_at: str | None,
    notes: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a single signal dict with a deterministic signal_id.

    Identity semantics (Correction C):

    - ``signal_id`` is a **subject identity**: ``sig-`` + SHA-256(kind +
      capability_id)[:16]. It is stable across content changes — the
      same (kind, capability) pair always produces the same signal_id,
      even when evidence_refs, gap_classification, or other content
      fields change between scans. This lets consumers track "the
      evidence-footprint signal for capability X" over time.

    - ``content_version`` is a **content identity**: ``cv-`` +
      SHA-256(all materially relevant content fields)[:16]. It changes
      when any content field changes, so consumers can detect that a
      signal's snapshot content has changed between scans by comparing
      content_version values.

    Both are computed deterministically; neither is persisted. No new
    infrastructure is introduced.
    """
    signal_id = _deterministic_signal_id(
        kind=kind,
        capability_id=capability_id,
    )
    signal: dict[str, Any] = {
        "signal_id": signal_id,
        "kind": kind,
        "temporal_type": SIGNAL_TEMPORAL_TYPE,
        "snapshot_only": True,
        "entity_id": None,
        "capability_id": capability_id,
        "capability_name": capability_name,
        "evidence_refs": list(evidence_refs),
        "gap_classification": gap_classification,
        "combination_count": combination_count,
        "combination_density_status": combination_density_status,
        "distinct_source_uri_count": distinct_source_uri_count,
        "independent_origin_count": independent_origin_count,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "notes": notes,
    }
    if extra:
        for k, v in extra.items():
            if k not in signal:
                signal[k] = v
    # Compute content_version AFTER extra fields are merged, so all
    # materially relevant content fields are included in the fingerprint.
    signal["content_version"] = _deterministic_content_version(signal)
    return signal


# ── Deterministic identity helpers ─────────────────────────────────────────


def _deterministic_signal_id(
    *,
    kind: str,
    capability_id: str,
) -> str:
    """Compute a deterministic **subject identity** signal ID.

    The ID is ``sig-`` + the first 16 hex chars of SHA-256 over
    (kind, capability_id). It identifies the signal's SUBJECT (which
    capability + which signal kind), NOT the signal's content. It is
    stable across content changes — the same (kind, capability) pair
    always produces the same signal_id, even when evidence_refs or
    other content fields change between scans (Correction C).
    """
    payload = "\n".join([kind, capability_id])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"sig-{digest}"


def _deterministic_content_version(signal: dict[str, Any]) -> str:
    """Compute a deterministic **content version** fingerprint.

    The fingerprint is ``cv-`` + the first 16 hex chars of SHA-256 over
    all materially relevant content fields of the signal. It changes
    when any content field changes, so consumers can detect that a
    signal's snapshot content has changed between scans by comparing
    content_version values (Correction C).

    Materially relevant fields (excluded from the fingerprint:
    signal_id, content_version, notes, temporal_type, snapshot_only —
    these are either the identity itself, derived, or non-material
    metadata):
    - kind, capability_id, capability_name, entity_id
    - evidence_refs (sorted)
    - gap_classification
    - combination_count, combination_density_status
    - distinct_source_uri_count, independent_origin_count
    - first_seen_at, last_seen_at
    - all extra fields (e.g., fragment_count, dependency_edges,
      match_quality, reason, contradicting_evidence_refs)
    """
    # Fields that are identity/metadata, not content.
    _EXCLUDED = {"signal_id", "content_version", "notes", "temporal_type", "snapshot_only"}
    material_fields = []
    for k in sorted(signal.keys()):
        if k in _EXCLUDED:
            continue
        v = signal[k]
        # Normalize lists/dicts to stable JSON for hashing
        if isinstance(v, (list, dict)):
            material_fields.append(f"{k}={json.dumps(v, sort_keys=True, default=str)}")
        else:
            material_fields.append(f"{k}={v}")
    payload = "\n".join(material_fields)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"cv-{digest}"


def _deterministic_request_id(
    focus_capability_id: str | None,
    focus_entity_id: str | None,
    combination_problem_domain: str | None,
    combination_context: str | None,
    limit: int,
) -> str:
    """Compute a deterministic request ID for the scan invocation.

    This is NOT a persisted idempotency key — it is a stable identifier
    for the response so callers can correlate. It does not depend on
    wall-clock time.
    """
    payload = "\n".join(
        [
            "g06-t01-future-signal",
            focus_capability_id or "",
            focus_entity_id or "",
            combination_problem_domain or "",
            combination_context or "",
            str(limit),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"req-{digest}"


# ── Capability resolution ──────────────────────────────────────────────────


async def _resolve_capabilities(
    session: AsyncSession,
    focus_capability_id: str | None,
    focus_entity_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Resolve the set of capabilities to scan.

    Returns a list of dicts with keys: id, canonical_name.
    Deterministic ordering by canonical_name.
    """
    if focus_capability_id:
        # Verify the focused capability exists and is kind=capability
        stmt = select(EntityRow).where(
            EntityRow.id == focus_capability_id,
            EntityRow.kind == "capability",
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return []
        return [{"id": row.id, "canonical_name": row.canonical_name}]

    if focus_entity_id:
        # Find capabilities linked to this entity via PROVIDES/ENABLES/PRODUCES
        stmt = (
            select(EntityRow)
            .join(RelationshipRow, RelationshipRow.to_entity_id == EntityRow.id)
            .where(
                RelationshipRow.from_entity_id == focus_entity_id,
                RelationshipRow.predicate.in_(("PROVIDES", "ENABLES", "PRODUCES")),
                EntityRow.kind == "capability",
            )
            .order_by(EntityRow.canonical_name.asc())
            .limit(limit)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        return [{"id": r.id, "canonical_name": r.canonical_name} for r in rows]

    # Default: list all capabilities via the existing registry service
    registry = await list_capabilities(session, limit=limit)
    return [
        {"id": item["id"], "canonical_name": item["canonical_name"]}
        for item in registry.get("items", [])
    ]


# ── Evidence footprint ─────────────────────────────────────────────────────


async def _gather_evidence_footprint(
    session: AsyncSession,
    capability_id: str,
) -> dict[str, Any]:
    """Gather the current evidence footprint for a capability.

    Returns a dict with:
        - fragment_ids: list[str] (sorted for determinism)
        - distinct_source_uri_count: int
        - independent_origin_count: int (conservative, per PRB-04)
        - first_seen_at: str | None (earliest retrieved_at)
        - last_seen_at: str | None (latest retrieved_at)
    """
    # Find claims whose subject_ref or object_ref is this capability
    claim_stmt = select(ClaimRow).where(
        (ClaimRow.subject_ref == capability_id) | (ClaimRow.object_ref == capability_id)
    )
    claims = list((await session.execute(claim_stmt)).scalars().all())

    # Collect evidence_refs from those claims
    fragment_ids: set[str] = set()
    for claim in claims:
        refs = _safe_json_loads(claim.evidence_refs)
        for ref in refs:
            fragment_ids.add(ref)

    # Also collect evidence_refs from relationships incident to the capability
    rel_stmt = select(RelationshipRow).where(
        (RelationshipRow.from_entity_id == capability_id)
        | (RelationshipRow.to_entity_id == capability_id)
    )
    rels = list((await session.execute(rel_stmt)).scalars().all())
    for rel in rels:
        refs = _safe_json_loads(rel.evidence_refs)
        for ref in refs:
            fragment_ids.add(ref)

    if not fragment_ids:
        # Correction B: zero evidence fragments => zero independent origins.
        # The conservative PRB-04 default of 1 applies only when there IS
        # evidence but its independence is unverified — not when there is
        # no evidence at all. Zero sources must not imply one independently
        # established origin.
        return {
            "fragment_ids": [],
            "distinct_source_uri_count": 0,
            "independent_origin_count": 0,
            "first_seen_at": None,
            "last_seen_at": None,
        }

    # Load the actual fragment rows
    frag_stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id.in_(fragment_ids))
    fragments = list((await session.execute(frag_stmt)).scalars().all())

    # Distinct source URLs (factual count — NOT independence)
    source_uris = {f.source_uri for f in fragments if f.source_uri}

    # Load the source rows to check provenance metadata (publisher/author)
    source_ids = {f.source_id for f in fragments if f.source_id}
    source_rows: list[SourceRow] = []
    if source_ids:
        src_stmt = select(SourceRow).where(SourceRow.id.in_(source_ids))
        source_rows = list((await session.execute(src_stmt)).scalars().all())

    # ── Conservative independent-origin count (per PRB-04, Correction B) ─
    # When there IS >=1 evidence fragment, the conservative default is 1
    # (per PRB-04: provenance independence is unverified). This applies
    # REGARDLESS of how many distinct source URLs exist — multiple URLs
    # without verified origin independence do not inflate the count.
    # Differing unverified publisher/author strings are NOT sufficient
    # to establish independent primary origins.
    #
    # G06-T01 does NOT introduce a new provenance inference algorithm
    # (per mission section 4). The count is never > 1 in G06-T01.
    #
    # When there are ZERO fragments, independent_origin_count = 0 (handled
    # in the early return above) — zero sources must not imply one
    # independently established origin.
    independent_origin_count = _CONSERVATIVE_INDEPENDENT_ORIGIN_COUNT

    # Timestamps (factual observation of retrieved_at range)
    retrieved_ats = [f.retrieved_at for f in fragments if f.retrieved_at]
    retrieved_ats.sort()
    first_seen_at = retrieved_ats[0] if retrieved_ats else None
    last_seen_at = retrieved_ats[-1] if retrieved_ats else None

    return {
        "fragment_ids": sorted(fragment_ids),
        "distinct_source_uri_count": len(source_uris),
        "independent_origin_count": independent_origin_count,
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "_source_rows": source_rows,  # internal; not exposed in signal output
    }


# ── Dependency graph ───────────────────────────────────────────────────────


async def _gather_dependency_graph_edges(
    session: AsyncSession,
    capability_id: str,
) -> list[dict[str, Any]]:
    """Gather the current PROVIDES/DEPENDS_ON/LIMITS edges for a capability.

    Returns a list of edge dicts (sorted for determinism):
        {from_entity_id, to_entity_id, predicate, relationship_id}
    """
    stmt = (
        select(RelationshipRow)
        .where(
            (RelationshipRow.from_entity_id == capability_id)
            | (RelationshipRow.to_entity_id == capability_id),
            RelationshipRow.predicate.in_(_DEPENDENCY_GRAPH_PREDICATES),
        )
        .order_by(
            RelationshipRow.predicate.asc(),
            RelationshipRow.from_entity_id.asc(),
            RelationshipRow.to_entity_id.asc(),
        )
    )
    rels = list((await session.execute(stmt)).scalars().all())
    return [
        {
            "from_entity_id": r.from_entity_id,
            "to_entity_id": r.to_entity_id,
            "predicate": r.predicate,
            "relationship_id": r.id,
        }
        for r in rels
    ]


# ── Helpers ────────────────────────────────────────────────────────────────


def _safe_json_loads(raw: str | None) -> list[str]:
    """Safely parse a JSON array field; return [] on any failure."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(v) for v in value]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _empty_result(request_id: str) -> dict[str, Any]:
    """Return an empty (but well-formed) signal result."""
    return {
        "signals": [],
        "temporal_type": SIGNAL_TEMPORAL_TYPE,
        "snapshot_only": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "limitations": [
            "No capabilities found in the knowledge base. Empty result — no fabricated signals.",
            "Layer 2 (verified historical change) is not available.",
        ],
        "request_id": request_id,
    }
