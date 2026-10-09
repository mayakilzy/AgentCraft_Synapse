"""``POST /api/v1/reasoning/queries`` -- G04-T03 Evidence-Grounded Reasoning API.

Per the user's G04-T03 mission briefing §7:

  "Implement or complete the existing reasoning endpoint:
   POST /api/v1/reasoning/queries"

This endpoint replaces the G01 501 placeholder for /reasoning/queries
with a real implementation that composes the G01-G04-T02C services into
a deterministic evidence-grounded reasoning pipeline.

Per mission §8 (LLM boundary):

  "Do not introduce a new LLM orchestration subsystem, external model
   dependency, or expensive inference workflow in G04-T03. Prefer
   deterministic evidence composition using existing services."

This endpoint is READ-ONLY: it composes existing services and never
writes to the knowledge graph. The response includes:

  - Original question
  - Identified reasoning intent
  - Structured answer (deterministic summary)
  - Findings (DOCUMENTED_FACT / DERIVED_FINDING / HYPOTHESIS / UNKNOWN)
  - Cited claims + relationships
  - Evidence chain (claim -> fragment -> span -> source_uri)
  - Explicit unknowns
  - Contradictions (preserved, not suppressed)
  - Confidence indicators
  - Reasoning limitations

A hypothesis is NEVER presented as a verified fact.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from synapse.api.deps import DbSessionDep, PrincipalDep, RequestIDDep
from synapse.api.responses import Envelope, PaginationMeta
from synapse.application.reasoning import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_QUERY_CHARS,
    ReasoningIntent,
    answer_query,
)

router = APIRouter(prefix="/reasoning", tags=["reasoning"])


# ── Request / response models ────────────────────────────────────────────────


class ReasoningRequest(BaseModel):
    """Request body for ``POST /api/v1/reasoning/queries``."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUERY_CHARS,
        description="Natural-language reasoning query. Classified into one "
        "of 6 intents (capability_explanation, dependency_analysis, "
        "documented_alternatives, constraint_analysis, technical_comparison, "
        "gap_explanation). Unknown intents receive a bounded 'insufficient "
        "evidence' response.",
    )
    candidate_entity_ids: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Optional list of entity IDs to scope the analysis to. "
        "Required for technical_comparison intent. When None, the analysis "
        "is at the capability level (gap analysis across all capabilities).",
    )
    context: str | None = Field(
        default=None,
        max_length=512,
        description="Optional technical context (e.g., 'AI agent systems in "
        "Python'). Used for applicability matching against claim "
        "validity_conditions via the gap analyzer.",
    )
    limit: int = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="Max number of findings to return (capped at 100).",
    )


# ── POST /api/v1/reasoning/queries ──────────────────────────────────────────


@router.post("/queries", status_code=status.HTTP_200_OK)
async def answer_reasoning_query(
    body: ReasoningRequest,
    principal: PrincipalDep,
    session: DbSessionDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """Answer a technical reasoning query with evidence-grounded findings.

    The reasoning layer composes the existing G01-G04-T02C services into
    a deterministic pipeline:

    Query -> Intent classification -> Retrieval / Gap analysis ->
    Evidence evaluation -> Answer composition -> Citation validation

    Supported intents (deterministic keyword matcher):
      - capability_explanation
      - dependency_analysis
      - documented_alternatives
      - constraint_analysis
      - technical_comparison
      - gap_explanation

    Unknown intents receive a bounded "insufficient evidence" response.

    Findings are typed:
      - DOCUMENTED_FACT    : directly supported by source evidence
      - DERIVED_FINDING    : follows from documented relationships + rules
      - HYPOTHESIS         : plausible but not established by evidence
      - UNKNOWN            : insufficient information

    A HYPOTHESIS is NEVER presented as a verified fact.

    Contradictions are preserved, not suppressed. Missing evidence is
    reported as UNKNOWN (absence of evidence is NOT evidence of absence).

    Citation chains are validated: every cited claim/relationship ID must
    exist in the DB. Untraceable citations are dropped, and the finding's
    type is downgraded to UNKNOWN.
    """
    answer = await answer_query(
        session,
        body.query,
        candidate_entity_ids=body.candidate_entity_ids,
        context=body.context,
        limit=body.limit,
        requester=str(principal),
    )
    return Envelope.success(
        data=answer,
        request_id=request_id,
        pagination=PaginationMeta(limit=body.limit, total=len(answer.get("findings", []))),
    )


# ── GET /api/v1/reasoning/intents (informational) ───────────────────────────


@router.get("/intents", status_code=status.HTTP_200_OK)
async def list_reasoning_intents(
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """List the supported reasoning intents and their keyword triggers.

    Informational endpoint for clients to discover the supported intents.
    """
    intents: list[dict] = []
    for intent in ReasoningIntent:
        # Use the public classifier's keyword map.
        from synapse.application.reasoning import _INTENT_KEYWORDS

        keywords = list(_INTENT_KEYWORDS.get(intent, ()))
        intents.append(
            {
                "intent": intent.value,
                "keyword_triggers": keywords,
            }
        )
    return Envelope.success(
        data={"intents": intents, "count": len(intents)},
        request_id=request_id,
    )
