"""API v1 router aggregator."""

from __future__ import annotations

from fastapi import APIRouter

from synapse.api.v1 import capabilities, health, providers, system

# Health endpoints live at root level (/health/*) per DOMAIN_AND_API_CONTRACTS.md
# — they're system probes, not API content.
health_router = health.router

router = APIRouter(prefix="/api/v1")
router.include_router(capabilities.router)
router.include_router(providers.router)
router.include_router(system.router)
router.include_router(system.jobs_router)

# Placeholder routers for the routes declared in DOMAIN_AND_API_CONTRACTS.md
# but not yet implemented. Each future group will swap these out for real
# implementations. We expose the *route names* in OpenAPI today so the
# contract is stable.

from fastapi import status  # noqa: E402

from synapse.api.errors import UnsupportedFeatureError  # noqa: E402

_placeholder = APIRouter(prefix="/api/v1", tags=["placeholder"])


def _not_implemented(feature: str):
    async def _handler():
        raise UnsupportedFeatureError(
            f"{feature} is not implemented in G01. It will land in a later group.",
        )

    _handler.__name__ = f"_not_implemented_{feature.replace(' ', '_').lower()}"
    return _handler


# Sources
_placeholder.add_api_route(
    "/sources/discover",
    _not_implemented("POST /sources/discover"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/sources/ingest",
    _not_implemented("POST /sources/ingest"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/sources",
    _not_implemented("GET /sources"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/sources/{source_id}",
    _not_implemented("GET /sources/{id}"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/sources/{source_id}/acquisitions",
    _not_implemented("GET /sources/{id}/acquisitions"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)

# Entities / relationships / claims
_placeholder.add_api_route(
    "/entities",
    _not_implemented("GET /entities"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/entities/{entity_id}",
    _not_implemented("GET /entities/{id}"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/relationships",
    _not_implemented("GET /relationships"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/relationships/{relationship_id}",
    _not_implemented("GET /relationships/{id}"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/claims/{claim_id}/evidence",
    _not_implemented("GET /claims/{id}/evidence"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)

# Knowledge / reasoning / innovations
_placeholder.add_api_route(
    "/knowledge/search",
    _not_implemented("POST /knowledge/search"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/reasoning/queries",
    _not_implemented("POST /reasoning/queries"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/innovations/generate",
    _not_implemented("POST /innovations/generate"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/innovations/{innovation_id}/critique",
    _not_implemented("POST /innovations/{id}/critique"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)

# Experiments / hypotheses
_placeholder.add_api_route(
    "/experiments",
    _not_implemented("POST /experiments"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/experiments/{experiment_id}/execute",
    _not_implemented("POST /experiments/{id}/execute"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/experiments/{experiment_id}",
    _not_implemented("GET /experiments/{id}"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/hypotheses/{hypothesis_id}/evidence-deltas",
    _not_implemented("GET /hypotheses/{id}/evidence-deltas"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)

# Future scenarios
_placeholder.add_api_route(
    "/future/scenarios",
    _not_implemented("POST /future/scenarios"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/future/scenarios/{scenario_id}",
    _not_implemented("GET /future/scenarios/{id}"),
    methods=["GET"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
_placeholder.add_api_route(
    "/future/scenarios/{scenario_id}/prototype-plan",
    _not_implemented("POST /future/scenarios/{id}/prototype-plan"),
    methods=["POST"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)


# Both routers mounted together — main.py also mounts health_router at root.
all_routers = [router, _placeholder]
