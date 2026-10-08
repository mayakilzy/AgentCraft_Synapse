"""``/api/v1/capabilities`` — list the capabilities this Synapse instance
knows about.

In G01, the capability list is a static catalog seeded from ``Capability``
domain records. Future groups will let clients register/replace
capabilities via mutation endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from synapse.api.deps import PrincipalDep, RequestIDDep
from synapse.api.responses import Envelope, PaginationMeta
from synapse.domain.capability import Capability, CapabilitySpec

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def _seed_capabilities() -> list[Capability]:
    """G01 static catalog. Real registry lives in DB; seeds inserted by
    later groups. For G01 we expose these as a static, deterministic list
    so the OpenAPI schema and the endpoint contract are stable."""
    return [
        Capability(
            name="web.fetch",
            description="Fetch a single web page (HTTP/HTTPS) and return its raw bytes.",
            inputs=[CapabilitySpec(name="url", type="string", required=True)],
            outputs=[CapabilitySpec(name="content", type="bytes", required=True)],
            prerequisites=[],
            constraints={"max_bytes": 10 * 1024 * 1024},
            provider_mappings={},
        ),
        Capability(
            name="web.discover",
            description="Discover candidate sources matching a query (search engine).",
            inputs=[CapabilitySpec(name="query", type="string", required=True)],
            outputs=[
                CapabilitySpec(name="candidates", type="list[source_candidate]", required=True)
            ],
            constraints={"max_results_per_call": 50},
            provider_mappings={},
        ),
        Capability(
            name="text.extract",
            description="Extract structured text (headings, paragraphs, code) from HTML.",
            inputs=[CapabilitySpec(name="html", type="string", required=True)],
            outputs=[CapabilitySpec(name="document", type="object", required=True)],
            provider_mappings={},
        ),
        Capability(
            name="reasoning.query",
            description="Run a single reasoning pass against the knowledge graph.",
            inputs=[
                CapabilitySpec(name="objective", type="string", required=True),
                CapabilitySpec(name="constraints", type="list[string]", required=False),
            ],
            outputs=[CapabilitySpec(name="answer", type="object", required=True)],
            provider_mappings={},
        ),
    ]


@router.get("")
async def list_capabilities(
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    """List all capabilities (paginated, deterministic)."""
    caps = _seed_capabilities()
    return Envelope.success(
        data={
            "items": [c.model_dump(by_alias=True, mode="json") for c in caps],
            "count": len(caps),
        },
        request_id=request_id,
        pagination=PaginationMeta(limit=50, total=len(caps)),
    )
