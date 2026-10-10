"""API v1 router aggregator."""

from __future__ import annotations

from fastapi import APIRouter

from synapse.api.v1 import (
    capabilities,
    capability_registry,
    experiments,
    health,
    innovations,
    knowledge,
    providers,
    reasoning,
    retrieval,
    sources,
    system,
)

# Health endpoints live at root level (/health/*) per DOMAIN_AND_API_CONTRACTS.md
# — they're system probes, not API content.
health_router = health.router

router = APIRouter(prefix="/api/v1")
router.include_router(capabilities.router)
router.include_router(providers.router)
router.include_router(sources.router)
router.include_router(knowledge.router)
router.include_router(retrieval.router)  # G04-T01: POST /api/v1/knowledge/retrieve
router.include_router(capability_registry.router)  # G04-T02: capability registry + gap analysis
router.include_router(
    reasoning.router
)  # G04-T03: POST /api/v1/reasoning/queries (replaces 501 placeholder)
router.include_router(
    innovations.router
)  # G05-T03: POST /api/v1/innovations/generate (replaces 501 placeholder)
router.include_router(
    experiments.router
)  # G05-T05: POST /api/v1/experiments + GET /api/v1/experiments/{id}
router.include_router(knowledge.entities_router)
router.include_router(knowledge.relationships_router)
router.include_router(knowledge.claims_router)
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


# Sources — implemented in G02 minimal slice (src/synapse/api/v1/sources.py).
# These routes are no longer placeholders; do not duplicate them here.


# Entities / relationships / claims / knowledge search — implemented
# in G03-T05 (src/synapse/api/v1/knowledge.py). No longer placeholders.

# Reasoning queries — implemented in G04-T03 (src/synapse/api/v1/reasoning.py).
# The /reasoning/queries route is no longer a placeholder. Do NOT register
# it here as a placeholder -- the real router at router.include_router(
# reasoning.router) above handles POST /api/v1/reasoning/queries.

# Innovations — POST /innovations/generate and POST /innovations/{id}/critique
# are implemented in G05-T03 + G05-T04 (src/synapse/api/v1/innovations.py).
# Do not duplicate them here as placeholders.


# Experiments / hypotheses
# POST /experiments and GET /experiments/{id} are implemented in G05-T05
# (src/synapse/api/v1/experiments.py). Do not duplicate them here as
# placeholders.

_placeholder.add_api_route(
    "/experiments/{experiment_id}/execute",
    _not_implemented("POST /experiments/{id}/execute"),
    methods=["POST"],
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
