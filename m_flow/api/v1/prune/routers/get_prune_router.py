"""
Prune API Router.

Administrative endpoints for data cleanup operations.
These are destructive, irreversible operations intended for development,
testing, and administrative maintenance.

Security Features (v1.4):
    - Uses FastAPI-Users current_user(active=True, superuser=True)
    - ALWAYS requires valid credentials (ignores REQUIRE_AUTHENTICATION setting)
    - Pipeline running check (STARTED and INITIATED states)
    - Distributed lock support (Redis if available, fallback to asyncio.Lock)
    - Cooldown period between operations
    - Explicit confirmation string required

Environment Variables:
    MFLOW_ENABLE_PRUNE_API: Master switch (default: false)
    MFLOW_PRUNE_ALLOW_ALL: Allow /prune/all endpoint (default: true)
    MFLOW_PRUNE_ALLOW_DATA: Allow /prune/data endpoint (default: true)
    MFLOW_PRUNE_ALLOW_SYSTEM: Allow /prune/system endpoint (default: true)
    MFLOW_PRUNE_COOLDOWN_SECONDS: Minimum seconds between operations (default: 60)

Warning:
    These operations permanently delete data and cannot be undone.
    Use with extreme caution in production environments.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from m_flow.auth.get_fastapi_users import get_fastapi_users
from m_flow.shared.logging_utils import get_logger

if TYPE_CHECKING:
    from m_flow.auth.models import User

_logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Configuration from Environment
# -----------------------------------------------------------------------------


def _env_bool(key: str, default: str = "false") -> bool:
    """Parse boolean from environment variable."""
    return os.getenv(key, default).lower() == "true"


PRUNE_API_ENABLED = _env_bool("MFLOW_ENABLE_PRUNE_API", "false")
ALLOW_PRUNE_ALL = _env_bool("MFLOW_PRUNE_ALLOW_ALL", "true")
ALLOW_PRUNE_DATA = _env_bool("MFLOW_PRUNE_ALLOW_DATA", "true")
ALLOW_PRUNE_SYSTEM = _env_bool("MFLOW_PRUNE_ALLOW_SYSTEM", "true")
COOLDOWN_SECONDS = int(os.getenv("MFLOW_PRUNE_COOLDOWN_SECONDS", "60"))


# -----------------------------------------------------------------------------
# Process-Local State
# -----------------------------------------------------------------------------

# Fallback lock for single-process deployments
_prune_lock = asyncio.Lock()
_last_prune_time: float = 0.0


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------

# FastAPI-Users instance for authentication
_fastapi_users = get_fastapi_users()

# Strict authentication dependency:
# - No token -> 401 Unauthorized
# - Invalid token -> 401 Unauthorized
# - Inactive user -> 401 Unauthorized
# - Non-superuser -> 403 Forbidden
#
# This ALWAYS requires authentication regardless of REQUIRE_AUTHENTICATION setting.
_require_prune_auth = _fastapi_users.current_user(active=True, superuser=True)


# -----------------------------------------------------------------------------
# Request/Response Models
# -----------------------------------------------------------------------------


class PruneAllRequest(BaseModel):
    """Request body for complete data wipe."""

    confirm: str = Field(
        ...,
        description="Must be exactly 'DELETE_ALL_DATA' to confirm operation",
    )


class PruneDataRequest(BaseModel):
    """Request body for file storage cleanup."""

    confirm: str = Field(
        ...,
        description="Must be exactly 'DELETE_FILES' to confirm operation",
    )


class PruneSystemRequest(BaseModel):
    """Request body for selective system cleanup."""

    confirm: str = Field(
        ...,
        description="Must be exactly 'DELETE_SYSTEM' to confirm operation",
    )
    graph: bool = Field(default=True, description="Clear graph database")
    vector: bool = Field(default=True, description="Clear vector database")
    metadata: bool = Field(default=True, description="Clear relational database")
    cache: bool = Field(default=True, description="Clear cache")


class PruneResponse(BaseModel):
    """Prune operation result."""

    status: str = Field(..., description="Operation status: completed or failed")
    cleared: dict = Field(default_factory=dict, description="Components that were cleared")
    message: str = Field(..., description="Human-readable result message")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings encountered during operation",
    )


# -----------------------------------------------------------------------------
# Safety Checks
# -----------------------------------------------------------------------------


def _check_prune_enabled() -> None:
    """
    Verify prune API is enabled via environment.

    Raises:
        HTTPException: 403 if MFLOW_ENABLE_PRUNE_API is not set to true.
    """
    if not PRUNE_API_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Prune API is disabled. Set MFLOW_ENABLE_PRUNE_API=true to enable.",
        )


async def _check_no_running_pipelines() -> list[str]:
    """
    Verify no pipelines are currently running.

    Uses get_active_pipeline_runs() which correctly handles:
    - Deterministic run_ids (same id for multiple runs)
    - ROW_NUMBER window function to get latest status per (dataset_id, workflow_name)
    - Both STARTED and INITIATED states

    Running prune during active pipeline operations would cause data
    corruption and orphaned records.

    Returns:
        List of warning messages (empty if all checks passed).

    Raises:
        HTTPException: 409 if active pipelines are found.
    """
    from m_flow.pipeline.methods import get_active_pipeline_runs

    warnings: list[str] = []

    try:
        running = await get_active_pipeline_runs()

        if running:
            details = [f"{r['workflow_name']}({r['status']})" for r in running[:5]]
            _logger.warning("[prune] Blocked: Active pipelines: %s", details)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot prune while pipelines are active. "
                    f"Found {len(running)} active pipeline(s): {', '.join(details)}"
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        msg = f"Could not verify pipeline status: {e}"
        _logger.warning("[prune] %s", msg)
        warnings.append(msg)

    return warnings


def _check_cooldown() -> None:
    """
    Enforce minimum time between prune operations.

    Note:
        This only works within a single process. Multi-worker deployments
        should use database-based or Redis-based cooldown tracking for
        full protection across workers.

    Raises:
        HTTPException: 429 if cooldown period has not elapsed.
    """
    now = time.time()
    elapsed = now - _last_prune_time

    if elapsed < COOLDOWN_SECONDS:
        remaining = int(COOLDOWN_SECONDS - elapsed)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Prune cooldown active. Wait {remaining} seconds before retrying.",
            headers={"Retry-After": str(remaining)},
        )


def _update_last_prune_time() -> None:
    """Record timestamp of successful prune operation."""
    global _last_prune_time
    _last_prune_time = time.time()


def _try_acquire_distributed_lock() -> tuple[Optional[Any], list[str]]:
    """
    Try to acquire distributed lock for prune operations.

    Uses a dedicated Redis lock key "mflow_prune_operation" that is
    separate from the Kùzu graph database lock. This prevents prune
    operations from blocking normal graph operations while waiting.

    Returns:
        Tuple of (lock_object, warnings):
        - lock_object: Redis lock if acquired, None if Redis unavailable
        - warnings: List of warning messages about lock acquisition

    Note:
        If Redis is unavailable, falls back to process-local asyncio.Lock
        which only protects within a single process.
    """
    warnings: list[str] = []

    try:
        from m_flow.adapters.cache.config import get_cache_config

        cfg = get_cache_config()
        if not cfg.caching or cfg.cache_backend != "redis":
            msg = "Redis not configured, using process-local lock only"
            _logger.info("[prune] %s", msg)
            warnings.append(msg)
            return None, warnings

        import redis

        redis_client = redis.Redis(
            host=cfg.cache_host,
            port=cfg.cache_port,
            username=cfg.cache_username,
            password=cfg.cache_password,
            socket_connect_timeout=10,
        )

        # Create prune-specific lock with automatic expiration
        lock = redis_client.lock(
            name="mflow_prune_operation",
            timeout=300,  # Lock expires after 5 minutes
            blocking_timeout=10,  # Wait max 10 seconds to acquire
        )

        if lock.acquire(blocking=True):
            return lock, warnings
        else:
            raise RuntimeError("Failed to acquire prune lock (another prune in progress?)")

    except Exception as e:
        msg = f"Redis lock unavailable ({e}), using process-local lock"
        _logger.info("[prune] %s", msg)
        warnings.append(msg)

    return None, warnings


def _release_distributed_lock(lock: Optional[Any]) -> None:
    """
    Release distributed lock if held.

    Args:
        lock: Redis lock object to release, or None if no lock held.
    """
    if lock is None:
        return

    try:
        lock.release()
    except Exception as e:
        _logger.warning("[prune] Failed to release distributed lock: %s", e)


# -----------------------------------------------------------------------------
# Router Factory
# -----------------------------------------------------------------------------


def get_prune_router() -> APIRouter:
    """
    Construct the prune API router.

    Creates an APIRouter with endpoints for administrative data cleanup.
    All endpoints require superuser authentication and explicit confirmation.

    Security Features:
        - FastAPI-Users current_user(active=True, superuser=True)
        - Pipeline running check (STARTED + INITIATED states)
        - Distributed lock (Redis) with process-local fallback
        - Cooldown period between operations
        - Explicit confirmation string

    Returns:
        Configured APIRouter instance.
    """
    router = APIRouter()

    @router.post("/all", response_model=PruneResponse)
    async def prune_all(
        request: PruneAllRequest,
        user: "User" = Depends(_require_prune_auth),
    ) -> PruneResponse:
        """
        Permanently delete ALL M-flow data.

        This operation clears:
        - File storage (.data_storage)
        - Graph database (all nodes and edges)
        - Vector database (all embeddings)
        - Relational database (all metadata including users)
        - Cache

        After this operation, the system will recreate the default superuser
        on the next request or server restart.

        Security Requirements:
        - MFLOW_ENABLE_PRUNE_API environment variable must be "true"
        - Valid authentication token required (ignores REQUIRE_AUTHENTICATION)
        - User must have superuser privileges
        - No pipelines may be in STARTED or INITIATED state
        - Cooldown period must have elapsed since last prune
        - Request body must contain confirm="DELETE_ALL_DATA"

        Warning:
            This operation is IRREVERSIBLE. All data will be permanently lost.
        """
        warnings: list[str] = []

        # Pre-execution validation
        _check_prune_enabled()

        if not ALLOW_PRUNE_ALL:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Prune all operation is disabled via MFLOW_PRUNE_ALLOW_ALL",
            )

        if request.confirm != "DELETE_ALL_DATA":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid confirmation string. Must be exactly 'DELETE_ALL_DATA'",
            )

        _check_cooldown()
        pipeline_warnings = await _check_no_running_pipelines()
        warnings.extend(pipeline_warnings)

        # Acquire distributed lock if available
        dist_lock, lock_warnings = _try_acquire_distributed_lock()
        warnings.extend(lock_warnings)

        # Check process-local lock
        if _prune_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another prune operation is in progress",
            )

        try:
            async with _prune_lock:
                _logger.warning(
                    "[prune.all] User %s (id=%s) initiated complete data wipe",
                    user.email,
                    user.id,
                )

                try:
                    from m_flow.api.v1.prune import prune

                    await prune.all()

                    _update_last_prune_time()
                    _logger.info("[prune.all] Completed successfully by %s", user.email)

                    return PruneResponse(
                        status="completed",
                        cleared={
                            "file_storage": True,
                            "graph_database": True,
                            "vector_database": True,
                            "relational_database": True,
                            "cache": True,
                        },
                        message="All data has been permanently deleted",
                        warnings=warnings,
                    )
                except Exception as e:
                    _logger.error("[prune.all] Failed: %s", e)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Prune operation failed. Check server logs for details.",
                    )
        finally:
            _release_distributed_lock(dist_lock)

    @router.post("/data", response_model=PruneResponse)
    async def prune_data(
        request: PruneDataRequest,
        user: "User" = Depends(_require_prune_auth),
    ) -> PruneResponse:
        """
        Delete file storage only.

        Removes uploaded documents and processed files from .data_storage.
        Does NOT clear database records, which may cause orphaned references.

        For complete cleanup, use /prune/all instead.

        Security Requirements:
        - MFLOW_ENABLE_PRUNE_API must be "true"
        - Valid superuser authentication
        - No active pipelines
        - confirm="DELETE_FILES"
        """
        warnings: list[str] = []

        _check_prune_enabled()

        if not ALLOW_PRUNE_DATA:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Prune data operation is disabled via MFLOW_PRUNE_ALLOW_DATA",
            )

        if request.confirm != "DELETE_FILES":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid confirmation string. Must be exactly 'DELETE_FILES'",
            )

        _check_cooldown()
        pipeline_warnings = await _check_no_running_pipelines()
        warnings.extend(pipeline_warnings)

        dist_lock, lock_warnings = _try_acquire_distributed_lock()
        warnings.extend(lock_warnings)

        if _prune_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another prune operation is in progress",
            )

        try:
            async with _prune_lock:
                _logger.warning("[prune.data] User %s initiated file storage wipe", user.email)

                try:
                    from m_flow.api.v1.prune import prune

                    await prune.prune_data()

                    _update_last_prune_time()
                    _logger.info("[prune.data] Completed successfully by %s", user.email)

                    return PruneResponse(
                        status="completed",
                        cleared={"file_storage": True},
                        message="File storage has been cleared",
                        warnings=warnings,
                    )
                except Exception as e:
                    _logger.error("[prune.data] Failed: %s", e)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Prune operation failed. Check server logs for details.",
                    )
        finally:
            _release_distributed_lock(dist_lock)

    @router.post("/system", response_model=PruneResponse)
    async def prune_system(
        request: PruneSystemRequest,
        user: "User" = Depends(_require_prune_auth),
    ) -> PruneResponse:
        """
        Selectively clear system databases.

        Allows targeted cleanup of specific storage backends:
        - graph: Clear graph database (nodes and edges)
        - vector: Clear vector database (embeddings)
        - metadata: Clear relational database (all tables including users)
        - cache: Clear cached data

        Security Requirements:
        - MFLOW_ENABLE_PRUNE_API must be "true"
        - Valid superuser authentication
        - No active pipelines
        - confirm="DELETE_SYSTEM"
        """
        warnings: list[str] = []

        _check_prune_enabled()

        if not ALLOW_PRUNE_SYSTEM:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Prune system operation is disabled via MFLOW_PRUNE_ALLOW_SYSTEM",
            )

        if request.confirm != "DELETE_SYSTEM":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid confirmation string. Must be exactly 'DELETE_SYSTEM'",
            )

        _check_cooldown()
        pipeline_warnings = await _check_no_running_pipelines()
        warnings.extend(pipeline_warnings)

        dist_lock, lock_warnings = _try_acquire_distributed_lock()
        warnings.extend(lock_warnings)

        if _prune_lock.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another prune operation is in progress",
            )

        try:
            async with _prune_lock:
                _logger.warning(
                    "[prune.system] User %s initiated system wipe (graph=%s, vector=%s, metadata=%s, cache=%s)",
                    user.email,
                    request.graph,
                    request.vector,
                    request.metadata,
                    request.cache,
                )

                try:
                    from m_flow.api.v1.prune import prune

                    await prune.prune_system(
                        graph=request.graph,
                        vector=request.vector,
                        metadata=request.metadata,
                        cache=request.cache,
                    )

                    _update_last_prune_time()
                    _logger.info("[prune.system] Completed successfully by %s", user.email)

                    return PruneResponse(
                        status="completed",
                        cleared={
                            "graph_database": request.graph,
                            "vector_database": request.vector,
                            "relational_database": request.metadata,
                            "cache": request.cache,
                        },
                        message="Selected system components have been cleared",
                        warnings=warnings,
                    )
                except Exception as e:
                    _logger.error("[prune.system] Failed: %s", e)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Prune operation failed. Check server logs for details.",
                    )
        finally:
            _release_distributed_lock(dist_lock)

    return router
