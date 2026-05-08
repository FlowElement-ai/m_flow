"""
Graph database engine factory.

Supports Kuzu (local/remote), Neo4j, Neptune, Neptune Analytics, and
extensible via ``supported_databases`` registry.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from .config import get_graph_context_config
from .graph_db_interface import GraphProvider
from .supported_databases import supported_databases

# ---------------------------------------------------------------------------
# Public async factory
# ---------------------------------------------------------------------------


async def get_graph_provider() -> GraphProvider:
    """
    Resolve configuration and return an initialised graph adapter.

    Because adapter construction may involve async I/O (e.g., schema sync),
    callers must ``await`` this factory.
    """
    cfg = get_graph_context_config()
    adapter = _build_adapter(**cfg)

    if hasattr(adapter, "initialize"):
        await adapter.initialize()

    return adapter


# ---------------------------------------------------------------------------
# Cached sync builder
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _build_adapter(
    graph_database_provider: str,
    graph_file_path: str = "",
    graph_database_url: str = "",
    graph_database_name: str = "",
    graph_database_username: str = "",
    graph_database_password: str = "",
    graph_database_port: str = "",
    graph_database_key: str = "",
    graph_dataset_database_handler: str = "",
) -> GraphProvider:
    """
    Instantiate the appropriate graph adapter for *graph_database_provider*.

    Raises
    ------
    EnvironmentError
        When required parameters are missing or provider is unknown.
    """
    provider = graph_database_provider.lower()

    # Check extensible registry first
    if provider in supported_databases:
        ctor = supported_databases[provider]
        return ctor(
            graph_database_url=graph_database_url,
            graph_database_username=graph_database_username,
            graph_database_password=graph_database_password,
            database_name=graph_database_name,
        )

    # Built-in providers
    if provider == "neo4j":
        _require(graph_database_url, "Neo4j URL")
        from .neo4j_driver.adapter import Neo4jAdapter

        return Neo4jAdapter(
            graph_database_url=graph_database_url,
            graph_database_username=graph_database_username or None,
            graph_database_password=graph_database_password or None,
            graph_database_name=graph_database_name or None,
        )

    if provider == "kuzu":
        _require(graph_file_path, "Kuzu database path")
        from .kuzu.adapter import KuzuAdapter

        return KuzuAdapter(db_path=graph_file_path)

    if provider == "kuzu-remote":
        _require(graph_database_url, "Kuzu remote URL")
        from .kuzu.remote_kuzu_adapter import RemoteKuzuAdapter

        return RemoteKuzuAdapter(
            api_url=graph_database_url,
            username=graph_database_username,
            password=graph_database_password,
        )

    if provider == "neptune":
        _ensure_langchain_aws()
        _require(graph_database_url, "Neptune endpoint")
        from .neptune_driver.adapter import NEPTUNE_ENDPOINT_URL, NeptuneGraphDB

        _validate_prefix(graph_database_url, NEPTUNE_ENDPOINT_URL)
        gid = graph_database_url.replace(NEPTUNE_ENDPOINT_URL, "")
        return NeptuneGraphDB(graph_id=gid)

    if provider == "neptune_analytics":
        _ensure_langchain_aws()
        _require(graph_database_url, "Neptune Analytics endpoint")
        from ..hybrid.neptune_analytics.NeptuneAnalyticsAdapter import (
            NEPTUNE_ANALYTICS_ENDPOINT_URL,
            NeptuneAnalyticsAdapter,
        )

        _validate_prefix(graph_database_url, NEPTUNE_ANALYTICS_ENDPOINT_URL)
        gid = graph_database_url.replace(NEPTUNE_ANALYTICS_ENDPOINT_URL, "")
        return NeptuneAnalyticsAdapter(graph_id=gid)

    known = [
        *supported_databases.keys(),
        "neo4j",
        "kuzu",
        "kuzu-remote",
        "neptune",
        "neptune_analytics",
    ]
    raise EnvironmentError(f"Unknown graph provider '{graph_database_provider}'. Supported: {', '.join(known)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require(val: Optional[str], label: str) -> None:
    if not val:
        raise EnvironmentError(f"Missing required configuration: {label}")


def _validate_prefix(url: str, prefix: str) -> None:
    if not url.startswith(prefix):
        raise ValueError(f"URL must start with '{prefix}'")


def _ensure_langchain_aws() -> None:
    try:
        import langchain_aws  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "langchain_aws is required for Neptune support. Install with: pip install langchain_aws"
        ) from exc


# Backward-compatible aliases (deprecated)
