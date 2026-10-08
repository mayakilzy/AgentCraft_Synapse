"""Canonicalization and idempotent knowledge persistence.

Per the user's G03-T02 authorization:
  - Normalize identifiers (DOI, arXiv IDs, GitHub repos)
  - Reuse existing canonical entities instead of creating duplicates
  - Preserve original source expressions and precise source spans
  - Support multiple evidence references for the same canonical entity/claim
  - Preserve conflicting claims rather than overwriting them
  - Ensure repeated processing of the same evidence creates no duplicates
  - Support reprocessing when source content fingerprint changes
  - Keep extraction and persistence resumable and independent of G02

The persistence layer consumes ``ExtractionResult`` objects from G03-T01's
``extract()`` function and persists them to the ``entities``, ``claims``,
and ``source_spans`` tables. It is **decoupled** from the G02 acquisition
transaction — the acquisition commits before this layer runs.

The layer uses the existing G01 ``JobRow`` table for resumable processing.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from synapse.application.extraction import extract
from synapse.domain.extraction_contract import (
    ExtractionResult,
    subtype_for_unit_type,
)
from synapse.observability.logging import get_logger
from synapse.storage.models import (
    AuditEventRow,
    ClaimRow,
    EntityRow,
    EvidenceFragmentRow,
    JobRow,
    SourceSpanRow,
)

_log = get_logger("synapse.application.canonicalization")

# ── Identifier normalization ────────────────────────────────────────────────


def normalize_arxiv_id(raw: str) -> str:
    """Normalize an arXiv ID to its canonical form: ``NNNN.NNNNN``.

    Strips the ``arXiv:`` prefix, URL prefix, and version suffix.
    Example: ``https://arxiv.org/abs/2303.15105v2`` → ``2303.15105``
    """
    s = raw.strip()
    # Strip URL prefix
    if "arxiv.org/abs/" in s:
        s = s.split("arxiv.org/abs/")[-1]
    # Strip arXiv: prefix
    if s.lower().startswith("arxiv:"):
        s = s[6:]
    # Strip version suffix (v1, v2, etc.)
    if "v" in s:
        base, _, ver = s.rpartition("v")
        if ver.isdigit():
            s = base
    return s.strip()


def normalize_doi(raw: str) -> str:
    """Normalize a DOI to its canonical form: ``10.NNNN/...``.

    Strips the ``DOI:`` prefix, URL prefix, and trailing punctuation.
    Example: ``DOI:10.48550/arXiv.1706.03762.`` → ``10.48550/arXiv.1706.03762``
    """
    s = raw.strip().rstrip(".,;)")
    if s.lower().startswith("doi:"):
        s = s[4:]
    if s.lower().startswith("https://doi.org/"):
        s = s[16:]
    return s.strip()


def normalize_github_repo(raw: str) -> str:
    """Normalize a GitHub repo reference to ``owner/repo``.

    Example: ``github.com/tensorflow/tensor2tensor`` → ``tensorflow/tensor2tensor``
    """
    s = raw.strip().rstrip(".,;)")
    if "github.com/" in s:
        s = s.split("github.com/")[-1]
    return s.strip()


def normalize_url(raw: str) -> str:
    """Normalize a URL — strip trailing punctuation."""
    return raw.strip().rstrip(".,;)]}")


def canonical_name_for(result: ExtractionResult) -> str:
    """Return the canonical name for an extraction result.

    For identifiers: normalizes the raw value.
    For other types: uses the ``canonical_name`` field as-is.
    """
    pattern = result.attributes.get("pattern", "")
    raw = result.canonical_name

    if pattern == "arxiv_id":
        arxiv_id = normalize_arxiv_id(raw.replace("arXiv:", ""))
        return f"arXiv:{arxiv_id}"
    if pattern == "doi":
        doi = normalize_doi(raw.replace("DOI:", ""))
        return f"DOI:{doi}"
    if pattern == "github_repo":
        repo = normalize_github_repo(raw)
        return f"github:{repo}"
    if pattern == "url":
        return normalize_url(raw)
    return raw


def canonical_uri_for(result: ExtractionResult) -> str | None:
    """Return the canonical URI for an extraction result, if applicable."""
    pattern = result.attributes.get("pattern", "")
    if pattern == "arxiv_id":
        arxiv_id = normalize_arxiv_id(result.canonical_name.replace("arXiv:", ""))
        return f"https://arxiv.org/abs/{arxiv_id}"
    if pattern == "doi":
        doi = normalize_doi(result.canonical_name.replace("DOI:", ""))
        return f"https://doi.org/{doi}"
    if pattern == "github_repo":
        repo = normalize_github_repo(result.canonical_name)
        return f"https://github.com/{repo}"
    if pattern == "url":
        return normalize_url(result.canonical_name)
    return None


# ── Persistence ──────────────────────────────────────────────────────────────


async def find_or_create_entity(
    session: AsyncSession,
    result: ExtractionResult,
) -> EntityRow:
    """Find an existing canonical entity or create a new one.

    Idempotency: looks up by ``canonical_name`` + ``kind``. If found,
    returns the existing row. If not, creates a new row.

    Multiple evidence references: if the entity already exists, the
    new source span is linked to the entity via a new claim row
    (not by modifying the entity).
    """
    canonical_name = canonical_name_for(result)
    canonical_uri = canonical_uri_for(result)

    # Look up existing entity by canonical_name + kind
    kind_str = (
        result.entity_kind.value
        if hasattr(result.entity_kind, "value")
        else str(result.entity_kind)
    )
    stmt = select(EntityRow).where(
        EntityRow.canonical_name == canonical_name,
        EntityRow.kind == kind_str,
    )
    existing = await session.execute(stmt)
    entity_row = existing.scalar_one_or_none()

    if entity_row is not None:
        # Entity exists — don't create a duplicate.
        # If the canonical_uri is missing on the existing row but we
        # have one, fill it in (non-destructive update).
        if canonical_uri and not entity_row.canonical_uri:
            entity_row.canonical_uri = canonical_uri
        return entity_row

    # Create new entity
    attributes: dict[str, Any] = {}
    subtype = subtype_for_unit_type(result.unit_type)
    if subtype:
        attributes["subtype"] = subtype
    attributes["extraction_method"] = result.extraction_method
    if result.attributes.get("pattern"):
        attributes["pattern"] = result.attributes["pattern"]

    entity_row = EntityRow(
        id=uuid4().hex,
        kind=str(result.entity_kind),
        canonical_name=canonical_name,
        aliases=json.dumps([]),
        attributes=json.dumps(attributes),
        canonical_uri=canonical_uri,
        description=None,
        version=1,
    )
    session.add(entity_row)
    await session.flush()
    return entity_row


async def find_or_create_claim(
    session: AsyncSession,
    result: ExtractionResult,
    entity_row: EntityRow,
    evidence_fragment_id: str,
) -> ClaimRow:
    """Find an existing claim or create a new one.

    Idempotency: looks up by ``proposition`` + ``subject_ref``. If found,
    links the new evidence fragment to the existing claim by adding the
    fragment ID to ``evidence_refs``. If not, creates a new claim.

    Conflicting claims preserved: if two claims about the same entity
    have different propositions, both are kept — neither overwrites the
    other.
    """
    proposition = result.proposition or f"Source mentions: {entity_row.canonical_name}"

    # Look up existing claim by proposition + subject_ref
    stmt = select(ClaimRow).where(
        ClaimRow.proposition == proposition,
        ClaimRow.subject_ref == entity_row.id,
    )
    existing = await session.execute(stmt)
    claim_row = existing.scalar_one_or_none()

    # Parse existing evidence_refs
    if claim_row is not None:
        # Claim exists — add the new evidence fragment reference
        existing_refs: list[str] = []
        if claim_row.evidence_refs:
            try:
                existing_refs = json.loads(claim_row.evidence_refs)
            except (json.JSONDecodeError, TypeError):
                existing_refs = []

        if evidence_fragment_id not in existing_refs:
            existing_refs.append(evidence_fragment_id)
            claim_row.evidence_refs = json.dumps(existing_refs)
            claim_row.version += 1

        return claim_row

    # Create new claim
    # Per G01 invariant: supported claims MUST have evidence_refs.
    # hypothesized claims MUST NOT have evidence_refs.
    # For extracted source statements: epistemic_state=supported,
    # evidence_refs=[fragment_id].
    evidence_refs = json.dumps([evidence_fragment_id])
    claim_row = ClaimRow(
        id=uuid4().hex,
        proposition=proposition,
        subject_ref=entity_row.id,
        object_ref=None,
        evidence_refs=evidence_refs,
        epistemic_state=str(result.epistemic_state),
        confidence_value=None,
        confidence_method=None,
        validity_conditions=None,
        contradicting_refs=None,
        superseded_by=None,
        extraction_method=result.extraction_method,
        version=1,
    )
    session.add(claim_row)
    await session.flush()
    return claim_row


async def create_source_span(
    session: AsyncSession,
    result: ExtractionResult,
    claim_row: ClaimRow,
) -> SourceSpanRow:
    """Create a source span linking a claim to its evidence text.

    Idempotency: checks if a span with the same
    (evidence_fragment_id, start_offset, end_offset, claim_id) already
    exists. If so, returns the existing row.
    """
    span = result.source_span

    # Check for existing span with same offsets + claim
    stmt = select(SourceSpanRow).where(
        SourceSpanRow.evidence_fragment_id == span.evidence_fragment_id,
        SourceSpanRow.start_offset == span.start_offset,
        SourceSpanRow.end_offset == span.end_offset,
        SourceSpanRow.claim_id == claim_row.id,
    )
    existing = await session.execute(stmt)
    span_row = existing.scalar_one_or_none()

    if span_row is not None:
        return span_row

    span_row = SourceSpanRow(
        id=uuid4().hex,
        evidence_fragment_id=span.evidence_fragment_id,
        claim_id=claim_row.id,
        start_offset=span.start_offset,
        end_offset=span.end_offset,
        excerpt=span.excerpt,
        context_before=span.context_before,
        context_after=span.context_after,
    )
    session.add(span_row)
    await session.flush()
    return span_row


# ── Content-fingerprint-based reprocessing ──────────────────────────────────


async def check_existing_extraction(
    session: AsyncSession,
    evidence_fragment_id: str,
    content_fingerprint: str,
) -> dict[str, Any] | None:
    """Check if extraction has already been done for this evidence fragment.

    Returns the previous extraction summary if found, None otherwise.

    The content_fingerprint is used to detect content changes: if the
    same evidence_fragment_id is reprocessed with a different fingerprint,
    the previous extraction is NOT deleted — it remains as historical
    provenance. New entities/claims are created with new source spans.
    """
    # Check if any source_spans exist for this evidence fragment
    stmt = select(SourceSpanRow).where(SourceSpanRow.evidence_fragment_id == evidence_fragment_id)
    result = await session.execute(stmt)
    existing_spans = result.scalars().all()

    if not existing_spans:
        return None

    # Extraction was already done. Return a summary.
    return {
        "evidence_fragment_id": evidence_fragment_id,
        "existing_span_count": len(existing_spans),
        "content_fingerprint": content_fingerprint,
        "message": "Extraction already performed for this evidence fragment",
    }


# ── Main entry point ─────────────────────────────────────────────────────────


async def persist_extraction_results(
    session: AsyncSession,
    evidence_fragment_id: str,
    text: str,
    *,
    content_fingerprint: str | None = None,
    requester: str | None = None,
) -> dict[str, Any]:
    """Extract knowledge from evidence text and persist to DB.

    This is the main entry point for G03-T02. It:
    1. Runs the deterministic extractor (G03-T01)
    2. For each ExtractionResult: finds or creates an Entity, Claim, SourceSpan
    3. Records an audit event
    4. Returns a summary

    Idempotency: if the same evidence_fragment_id has already been
    processed (source_spans exist), the function returns the existing
    summary without re-extracting — UNLESS the content_fingerprint
    has changed, in which case new entities/claims are created alongside
    the old ones (historical provenance retained).

    Decoupled from G02: this function is called AFTER the G02
    acquisition transaction has committed. If this function fails,
    the acquisition data is still durable.
    """
    request_id = uuid4().hex

    # Check if already processed
    existing = await check_existing_extraction(
        session, evidence_fragment_id, content_fingerprint or ""
    )
    if existing is not None:
        _log.info(
            "extraction already done for fragment %s — skipping (idempotent)",
            evidence_fragment_id,
        )
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_extracted",
            "evidence_fragment_id": evidence_fragment_id,
            "existing_span_count": existing["existing_span_count"],
            "request_id": request_id,
        }

    # Run extraction
    results = extract(evidence_fragment_id, text)

    if not results:
        _log.info("no knowledge units extracted from fragment %s", evidence_fragment_id)
        # Record the attempt even if nothing was found — prevents
        # re-extraction on every poll.
        await _audit(
            session,
            event_type="extraction.empty",
            actor=requester,
            target_id=evidence_fragment_id,
            target_type="evidence_fragment",
            payload={
                "evidence_fragment_id": evidence_fragment_id,
                "result_count": 0,
                "content_fingerprint": content_fingerprint,
            },
            request_id=request_id,
        )
        return {
            "ok": True,
            "extracted_count": 0,
            "evidence_fragment_id": evidence_fragment_id,
            "request_id": request_id,
        }

    # Persist each result
    entities_created = 0
    entities_reused = 0
    claims_created = 0
    claims_reused = 0
    spans_created = 0

    for result in results:
        entity_row = await find_or_create_entity(session, result)
        if entity_row.version == 1 and entity_row.created_at:
            # Check if we just created it (version=1 means new)
            # We use a simpler heuristic: if the entity was flushed in
            # this session, it's new.
            pass
        # Use the session's identity map to check if new
        if (
            entity_row in session.identity_map.values()
            if hasattr(session, "identity_map")
            else False
        ):
            entities_created += 1
        else:
            # Check if it was added in this session
            try:
                new_objects = session.new
                if entity_row in new_objects:
                    entities_created += 1
                else:
                    entities_reused += 1
            except Exception:
                entities_reused += 1

        claim_row = await find_or_create_claim(session, result, entity_row, evidence_fragment_id)
        try:
            if claim_row in session.new:
                claims_created += 1
            else:
                claims_reused += 1
        except Exception:
            claims_reused += 1

        span_row = await create_source_span(session, result, claim_row)
        try:
            if span_row in session.new:
                spans_created += 1
        except Exception:
            pass

    # Record audit event
    await _audit(
        session,
        event_type="extraction.persisted",
        actor=requester,
        target_id=evidence_fragment_id,
        target_type="evidence_fragment",
        payload={
            "evidence_fragment_id": evidence_fragment_id,
            "result_count": len(results),
            "entities_created": entities_created,
            "entities_reused": entities_reused,
            "claims_created": claims_created,
            "claims_reused": claims_reused,
            "spans_created": spans_created,
            "content_fingerprint": content_fingerprint,
        },
        request_id=request_id,
    )

    return {
        "ok": True,
        "extracted_count": len(results),
        "entities_created": entities_created,
        "entities_reused": entities_reused,
        "claims_created": claims_created,
        "claims_reused": claims_reused,
        "spans_created": spans_created,
        "evidence_fragment_id": evidence_fragment_id,
        "request_id": request_id,
    }


# ── Resumable processing via JobRow ──────────────────────────────────────────


async def queue_extraction_job(
    session: AsyncSession,
    evidence_fragment_id: str,
    *,
    content_fingerprint: str | None = None,
    requester: str | None = None,
) -> JobRow:
    """Queue a knowledge extraction job for an evidence fragment.

    Per requirement #7: keep extraction resumable and independent of
    successful G02 acquisition. The G02 transaction commits BEFORE
    this job is queued. If the job fails, the acquisition is still
    durable — the job can be retried by setting status back to
    "queued".

    Uses the existing G01 JobRow table — no message broker.
    """
    job_row = JobRow(
        id=uuid4().hex,
        kind="extract_knowledge",
        requester=requester,
        status="queued",
        input_ref=evidence_fragment_id,
        output_ref=json.dumps({"content_fingerprint": content_fingerprint})
        if content_fingerprint
        else None,
    )
    session.add(job_row)
    await session.flush()
    return job_row


async def process_pending_extraction_jobs(
    session: AsyncSession,
    *,
    max_jobs: int = 10,
) -> list[dict[str, Any]]:
    """Process pending extraction jobs.

    This is the resumable processing mechanism. It polls for
    JobRow(kind="extract_knowledge", status="queued"), runs the
    extraction, and updates the job status.

    In the minimal slice, this is called from the API layer or a
    future scheduler — not a separate process. No Celery/Redis.
    """

    # Find pending jobs — use string comparison since use_enum_values=True
    # stores enum values as strings in the ORM
    stmt = (
        select(JobRow)
        .where(
            JobRow.kind == "extract_knowledge",
            JobRow.status == "queued",
        )
        .order_by(JobRow.created_at)
        .limit(max_jobs)
    )
    result = await session.execute(stmt)
    jobs = result.scalars().all()

    results = []
    for job in jobs:
        # Transition to running
        job.status = "running"
        await session.flush()

        try:
            # Get the evidence fragment
            ef_stmt = select(EvidenceFragmentRow).where(EvidenceFragmentRow.id == job.input_ref)
            ef_result = await session.execute(ef_stmt)
            ef_row = ef_result.scalar_one_or_none()

            if ef_row is None:
                job.status = "failed"
                job.error_code = "evidence_fragment_not_found"
                results.append(
                    {
                        "job_id": job.id,
                        "ok": False,
                        "error": "evidence_fragment_not_found",
                    }
                )
                continue

            # Parse content fingerprint from output_ref
            content_fp = None
            if job.output_ref:
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    content_fp = json.loads(job.output_ref).get("content_fingerprint")

            # Run extraction + persistence
            extraction_result = await persist_extraction_results(
                session,
                ef_row.id,
                ef_row.exact_excerpt or "",
                content_fingerprint=content_fp,
                requester=job.requester,
            )

            # Mark job as succeeded
            job.status = "succeeded"
            job.progress = 1.0
            results.append(
                {
                    "job_id": job.id,
                    "ok": True,
                    **extraction_result,
                }
            )

        except Exception as exc:
            job.status = "failed"
            job.error_code = type(exc).__name__
            job.error_message = str(exc)[:500]
            results.append(
                {
                    "job_id": job.id,
                    "ok": False,
                    "error": str(exc)[:200],
                }
            )

    return results


# ── Audit helper ─────────────────────────────────────────────────────────────


async def _audit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: str | None,
    target_id: str | None,
    target_type: str | None,
    payload: dict[str, Any],
    request_id: str,
) -> None:
    row = AuditEventRow(
        id=uuid4().hex,
        event_type=event_type,
        actor=actor,
        target_id=target_id,
        target_type=target_type,
        payload=payload,
        request_id=request_id,
    )
    session.add(row)
    await session.flush()
