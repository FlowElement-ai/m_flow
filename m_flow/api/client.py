"""
M-flow REST API Server

FastAPI-based HTTP interface for the M-flow knowledge graph framework.
Provides endpoints for data ingestion, memorization, search, and management.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from traceback import format_exc
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Configuration Constants
# ---------------------------------------------------------------------------

_RUNTIME_ENV = os.getenv("ENV", "prod")
_SENTRY_DSN = os.getenv("SENTRY_REPORTING_URL")
_AUTH_COOKIE_KEY = os.getenv("AUTH_TOKEN_COOKIE_NAME", "auth_token")
_DEFAULT_UI_URL = "http://localhost:3000"
_CONSOLE_URL = "http://localhost:3001"


def _parse_allowed_origins() -> list[str]:
    """Build CORS origin whitelist from environment or defaults."""
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS")
    if raw_origins:
        return [o.strip() for o in raw_origins.split(",") if o.strip()]
    return [os.getenv("UI_APP_URL", _DEFAULT_UI_URL), _CONSOLE_URL]


# ---------------------------------------------------------------------------
# Sentry Initialization (production only)
# ---------------------------------------------------------------------------

from m_flow.shared.logging_utils import get_logger, setup_logging

setup_logging()
_log = get_logger()

if _RUNTIME_ENV == "prod" and _SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=_SENTRY_DSN, traces_sample_rate=1.0, profiles_sample_rate=1.0)
    except ImportError:
        _log.info("Sentry SDK unavailable - install m_flow[monitoring] for error tracking")


# ---------------------------------------------------------------------------
# Application Lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _app_lifecycle(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown hooks for the application."""
    # Database initialization
    from m_flow.adapters.relational import get_db_adapter

    engine = get_db_adapter()
    await engine.create_database()

    # Ensure default user exists
    from m_flow.auth.methods import get_seed_user

    await get_seed_user()

    _log.info("Backend server has started")
    yield

    # -------------------------------------------------------------------------
    # Graceful shutdown: checkpoint graph database before exit
    # -------------------------------------------------------------------------
    _log.info("Shutting down - initiating graph database checkpoint...")
    try:
        from m_flow.adapters.graph.get_graph_adapter import get_graph_provider

        graph_engine = await get_graph_provider()
        if hasattr(graph_engine, "checkpoint"):
            await graph_engine.checkpoint()
            _log.info("Graph database checkpoint completed before shutdown")
        if hasattr(graph_engine, "close"):
            graph_engine.close()
            _log.info("Graph database connection closed")
    except Exception as e:
        _log.warning(f"Shutdown checkpoint failed: {e}")

    _log.info("Backend server shutdown complete")


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------


def _create_fastapi_app() -> FastAPI:
    """Construct and configure the FastAPI application instance."""
    is_debug = _RUNTIME_ENV != "prod"
    application = FastAPI(debug=is_debug, lifespan=_app_lifecycle)

    # CORS middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_allowed_origins(),
        allow_credentials=True,
        allow_methods=["OPTIONS", "GET", "PUT", "POST", "DELETE"],
        allow_headers=["*"],
    )

    return application


app = _create_fastapi_app()


# ---------------------------------------------------------------------------
# OpenAPI Customization
# ---------------------------------------------------------------------------


def _build_openapi_schema() -> dict:
    """Generate custom OpenAPI schema with auth schemes."""
    if app.openapi_schema:
        return app.openapi_schema

    from m_flow.auth.methods.get_authenticated_user import REQUIRE_AUTHENTICATION

    schema = get_openapi(
        title="Mflow API",
        version="1.0.0",
        description="Mflow API with Bearer token and Cookie auth",
        routes=app.routes,
    )

    schema["components"]["securitySchemes"] = {
        "BearerAuth": {"type": "http", "scheme": "bearer"},
        "CookieAuth": {"type": "apiKey", "in": "cookie", "name": _AUTH_COOKIE_KEY},
    }

    if REQUIRE_AUTHENTICATION:
        schema["security"] = [{"BearerAuth": []}, {"CookieAuth": []}]

    app.openapi_schema = schema
    return schema


app.openapi = _build_openapi_schema


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(req: Request, err: RequestValidationError) -> JSONResponse:
    """Handle request validation failures."""
    payload = jsonable_encoder({"detail": err.errors(), "body": err.body})
    return JSONResponse(status_code=400, content=payload)


@app.exception_handler(Exception)
async def _handle_generic_exception(_: Request, err: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions."""
    from m_flow.exceptions import ServiceFault

    if isinstance(err, ServiceFault):
        if err.name and err.message and err.status_code:
            msg = f"{err.message} [{err.name}]"
            code = err.status_code
        else:
            _log.error("Improperly defined exception: %s", err)
            msg = "An unexpected error occurred."
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
        _log.error(format_exc())
        return JSONResponse(status_code=code, content={"detail": msg})

    # For non-ServiceFault exceptions, return 500
    _log.exception("Unhandled exception: %s", err)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# ---------------------------------------------------------------------------
# Core Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
async def _root_endpoint() -> dict:
    """Root path returning service status."""
    return {"status": "ok", "service": "m_flow"}


@app.get("/health")
async def _health_probe() -> JSONResponse:
    """Liveness/readiness probe for container orchestration."""
    import asyncio
    from m_flow.api.health import HealthStatus, health_checker
    from m_flow.version import get_version

    try:
        # Total timeout of 3 seconds for all health probes
        report = await asyncio.wait_for(health_checker.get_health_status(detailed=False), timeout=3.0)
        is_healthy = report.status != HealthStatus.UNHEALTHY
        return JSONResponse(
            status_code=200 if is_healthy else 503,
            content={
                "status": "ready" if is_healthy else "not ready",
                "health": report.status,
                "version": report.version,
            },
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "health": "warn",
                "version": get_version(),
                "note": "health check timed out (service is running but probes slow)",
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": f"health check failed: {exc}"},
        )


@app.get("/health/detailed")
async def _detailed_health_probe() -> JSONResponse:
    """Extended health status with per-component diagnostics."""
    from m_flow.api.health import HealthStatus, health_checker

    try:
        report = await health_checker.get_health_status(detailed=True)
        http_code = 503 if report.status == HealthStatus.UNHEALTHY else 200
        return JSONResponse(status_code=http_code, content=report.model_dump())
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": f"Health check system failure: {exc}"},
        )


# ---------------------------------------------------------------------------
# Router Registration
# ---------------------------------------------------------------------------


def _mount_routers() -> None:
    """Attach all API v1 routers to the application."""
    from m_flow.api.v1.add.routers import get_add_router
    from m_flow.api.v1.datasets.routers import get_datasets_router
    from m_flow.api.v1.delete.routers import get_delete_router
    from m_flow.api.v1.graph.routers import get_graph_router
    from m_flow.api.v1.ingest.routers import get_ingest_router
    from m_flow.api.v1.memorize.routers import get_memorize_router
    from m_flow.api.v1.permissions.routers import get_permissions_router
    from m_flow.api.v1.prompts.routers import get_prompts_router
    from m_flow.api.v1.responses.routers import get_responses_router
    from m_flow.api.v1.search.routers import get_search_router
    from m_flow.api.v1.settings.routers import get_settings_router
    from m_flow.api.v1.sync.routers import get_sync_router
    from m_flow.api.v1.update.routers import get_update_router
    from m_flow.api.v1.users.routers import (
        get_auth_router,
        get_register_router,
        get_reset_password_router,
        get_users_router,
        get_verify_router,
    )
    from m_flow.api.v1.procedural import get_extract_from_episodic_router
    from m_flow.api.v1.manual.routers import get_manual_router
    from m_flow.api.v1.prune.routers import get_prune_router
    from m_flow.api.v1.prune.routers.get_prune_procedural_router import get_prune_procedural_router
    from m_flow.api.v1.activity.routers import get_activity_router
    from m_flow.api.v1.pipeline.routers import get_pipeline_router
    from m_flow.api.v1.maintenance.routers import get_maintenance_router
    from m_flow.api.v1.coreference import get_coreference_router
    from m_flow.api.v1.playground import get_playground_router

    # Authentication routes
    auth_prefix = "/api/v1/auth"
    app.include_router(get_auth_router(), prefix=auth_prefix, tags=["auth"])
    app.include_router(get_register_router(), prefix=auth_prefix, tags=["auth"])
    app.include_router(get_reset_password_router(), prefix=auth_prefix, tags=["auth"])
    app.include_router(get_verify_router(), prefix=auth_prefix, tags=["auth"])

    # Core feature routes
    route_map = [
        (get_add_router, "/api/v1/add", "add"),
        (get_ingest_router, "/api/v1/ingest", "ingest"),
        (get_memorize_router, "/api/v1/memorize", "memorize"),
        (get_search_router, "/api/v1/search", "search"),
        (get_permissions_router, "/api/v1/permissions", "permissions"),
        (get_datasets_router, "/api/v1/datasets", "datasets"),
        (get_graph_router, "/api/v1/graph", "graph"),
        (get_settings_router, "/api/v1/settings", "settings"),
        (get_prompts_router, "/api/v1/prompts", "prompts"),
        (get_delete_router, "/api/v1/delete", "delete"),
        (get_update_router, "/api/v1/update", "update"),
        (get_responses_router, "/api/v1/responses", "responses"),
        (get_sync_router, "/api/v1/sync", "sync"),
        (get_users_router, "/api/v1/users", "users"),
        (get_extract_from_episodic_router, "/api/v1/procedural", "procedural"),
        (get_manual_router, "/api/v1/manual", "manual"),
        (get_prune_router, "/api/v1/prune", "prune"),
        (get_prune_procedural_router, "/api/v1/prune", "prune"),
        (get_activity_router, "/api/v1/activity", "activity"),
        (get_pipeline_router, "/api/v1/pipeline", "pipeline"),
        (get_maintenance_router, "/api/v1/maintenance", "maintenance"),
        (get_coreference_router, "/api/v1", "coreference"),
        (get_playground_router, "/api/v1/playground", "playground"),
    ]
    for router_fn, prefix, tag in route_map:
        app.include_router(router_fn(), prefix=prefix, tags=[tag])


_mount_routers()


# ---------------------------------------------------------------------------
# Server Entry Point
# ---------------------------------------------------------------------------


def start_api_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Launch the uvicorn server programmatically."""
    _log.info("Starting server at %s:%s", host, port)
    try:
        uvicorn.run(app, host=host, port=port)
    except Exception as exc:
        _log.exception("Failed to start server: %s", exc)
        raise


if __name__ == "__main__":
    setup_logging()
    start_api_server(
        host=os.getenv("HTTP_API_HOST", "0.0.0.0"),
        port=int(os.getenv("HTTP_API_PORT", "8000")),
    )
