"""FastAPI application factory.

This module exposes ``create_app()`` so tests can build a fresh app per
test session. The runnable entrypoint is ``uvicorn synapse.main:app``
which calls ``create_app()`` once at module import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from synapse.api.errors import register_error_handlers
from synapse.api.middleware import install_middleware
from synapse.api.v1.router import all_routers, health_router
from synapse.config import get_settings
from synapse.observability.logging import configure_logging, get_logger


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("synapse.main")
    log.info(
        "starting app=%s env=%s auth_mode=%s",
        settings.app_name,
        settings.env,
        settings.auth_mode,
    )
    if settings.env == "production":
        log.info("production mode — fail-closed auth enforced")
    yield
    log.info("shutdown complete")


def create_app() -> FastAPI:
    """Build and configure a fresh FastAPI app.

    Calling this multiple times is safe and produces independent apps,
    which is what tests want.
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Relationship-aware knowledge, hypothesis & future intelligence "
            "engine. API-first; internal and external clients share /api/v1."
        ),
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
        openapi_url="/api/v1/openapi.json",
        lifespan=_lifespan,
        # Production: hide docs
        **(
            {"docs_url": None, "redoc_url": None, "openapi_url": None}
            if settings.env == "production"
            else {}
        ),
    )

    install_middleware(app)
    register_error_handlers(app)

    # Health endpoints live at root level (not under /api/v1)
    app.include_router(health_router)

    for r in all_routers:
        app.include_router(r)

    # Root redirect → /api/v1/docs (dev only)
    if settings.env != "production":

        @app.get("/", include_in_schema=False)
        async def root_redirect_docs():
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/api/v1/docs")

    return app


# Module-level app for `uvicorn synapse.main:app`
app = create_app()
