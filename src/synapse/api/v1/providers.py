"""``/api/v1/providers`` — list configured provider adapters.

G01 ships a static registry (no providers enabled yet). The endpoint
exists so external clients and the OpenAPI schema are stable from day
one. Real provider registration begins in G02.
"""

from __future__ import annotations

from fastapi import APIRouter

from synapse.api.deps import PrincipalDep, RequestIDDep
from synapse.api.responses import Envelope, PaginationMeta

router = APIRouter(prefix="/providers", tags=["providers"])


def _seed_providers() -> list[dict]:
    """Static catalog of providers that *could* be wired in G02+.

    All are disabled by default — enabling any of them requires explicit
    configuration (per Master Spec §6 "adopt|adapt|defer|reject|gap").
    """
    return [
        {
            "name": "openai",
            "kind": "llm",
            "enabled": False,
            "config": {},
            "notes": "LLM provider — requires API key + budget cap",
        },
        {
            "name": "anthropic",
            "kind": "llm",
            "enabled": False,
            "config": {},
            "notes": "LLM provider — requires API key + budget cap",
        },
        {
            "name": "http-fetch",
            "kind": "fetch",
            "enabled": True,
            "config": {"timeout_seconds": 10, "max_bytes": 10485760},
            "notes": "Plain HTTP fetch with SSRF guard",
        },
        {
            "name": "arxiv",
            "kind": "search",
            "enabled": False,
            "config": {},
            "notes": "arXiv paper search — public API, rate-limited",
        },
        {
            "name": "openalex",
            "kind": "search",
            "enabled": False,
            "config": {},
            "notes": "OpenAlex academic graph — public API",
        },
        {
            "name": "github",
            "kind": "repository",
            "enabled": False,
            "config": {},
            "notes": "GitHub repository + releases — requires token + ToS review",
        },
    ]


@router.get("")
async def list_providers(
    principal: PrincipalDep,
    request_id: RequestIDDep,
) -> Envelope[dict]:
    providers = _seed_providers()
    return Envelope.success(
        data={
            "items": providers,
            "count": len(providers),
            "enabled_count": sum(1 for p in providers if p["enabled"]),
        },
        request_id=request_id,
        pagination=PaginationMeta(limit=50, total=len(providers)),
    )
