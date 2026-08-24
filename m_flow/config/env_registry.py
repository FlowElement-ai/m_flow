"""
Centralized environment variable registry.

All core environment variables are defined here to ensure documentation and code stay in sync.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Environment variable registry
# Structure: {var_name: {default, description, category, sensitive?, type?}}
ENV_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ========== Core LLM Configuration ==========
    "LLM_API_KEY": {
        "default": None,
        "description": "LLM API key",
        "category": "core",
        "sensitive": True,
        "type": "str",
    },
    "LLM_MODEL": {
        "default": "gpt-5-nano",
        "description": "LLM model name",
        "category": "core",
        "type": "str",
    },
    "LLM_PROVIDER": {
        "default": "openai",
        "description": "LLM provider (openai, anthropic, azure, etc.)",
        "category": "core",
        "type": "str",
    },
    "LLM_ENDPOINT": {
        "default": None,
        "description": "Custom LLM API endpoint",
        "category": "core",
        "type": "str",
    },
    # ========== Content Routing & Episode ==========
    "MFLOW_CONTENT_ROUTING": {
        "default": "true",
        "description": "Enable content routing (sentence-level grouping)",
        "category": "episodic",
        "type": "bool",
    },
    "MFLOW_ENABLE_EPISODE_SIZE_CHECK": {
        "default": "true",
        "description": "Enable Episode size check and auto-split",
        "category": "episodic",
        "type": "bool",
    },
    "MFLOW_EPISODIC_ENABLED": {
        "default": "true",
        "description": "Enable Episodic memory system",
        "category": "episodic",
        "type": "bool",
    },
    # ========== Episodic Retrieval Parameters ==========
    "MFLOW_EPISODIC_TOP_K": {
        "default": "10",
        "description": "Number of Episodes returned in retrieval",
        "category": "retrieval",
        "type": "int",
    },
    "MFLOW_EPISODIC_WIDE_SEARCH_TOP_K": {
        "default": "100",
        "description": "Wide search candidate count",
        "category": "retrieval",
        "type": "int",
    },
    "MFLOW_EPISODIC_DISPLAY_MODE": {
        "default": "summary",
        "description": "Display mode (summary, detail)",
        "category": "retrieval",
        "type": "str",
    },
    "MFLOW_EPISODIC_MAX_FACETS_PER_EPISODE": {
        "default": "4",
        "description": "Maximum Facets displayed per Episode",
        "category": "retrieval",
        "type": "int",
    },
    "MFLOW_EPISODIC_MAX_POINTS_PER_FACET": {
        "default": "8",
        "description": "Maximum FacetPoints displayed per Facet",
        "category": "retrieval",
        "type": "int",
    },
    # ========== Adaptive Scoring ==========
    "EPISODIC_ENABLE_ADAPTIVE": {
        "default": "true",
        "description": "Enable adaptive weight scoring",
        "category": "retrieval",
        "type": "bool",
    },
    "EPISODIC_ADAPTIVE_DEBUG": {
        "default": "false",
        "description": "Adaptive scoring debug mode",
        "category": "retrieval",
        "type": "bool",
    },
    "EPISODIC_ENABLE_TIME_BONUS": {
        "default": "true",
        "description": "Enable time match bonus",
        "category": "retrieval",
        "type": "bool",
    },
    # ========== Database Configuration ==========
    "GRAPH_DATABASE_PROVIDER": {
        "default": "kuzu",
        "description": "Graph database provider (kuzu, neo4j)",
        "category": "database",
        "type": "str",
    },
    "VECTOR_DB_PROVIDER": {
        "default": "lancedb",
        "description": "Vector database provider (lancedb, chromadb, pgvector, pinecone, milvus, qdrant)",
        "category": "database",
        "type": "str",
    },
    "RELATIONAL_DB_PROVIDER": {
        "default": "sqlite",
        "description": "Relational database provider (sqlite, postgresql)",
        "category": "database",
        "type": "str",
    },
    # ========== Performance Configuration ==========
    "MFLOW_LLM_CONCURRENCY_LIMIT": {
        "default": "10",
        "description": "LLM concurrent request limit",
        "category": "performance",
        "type": "int",
    },
    "MFLOW_EMBEDDING_TIMEOUT": {
        "default": "128",
        "description": "Embedding timeout (seconds)",
        "category": "performance",
        "type": "int",
    },
    "MFLOW_MEMORIZE_LOCK": {
        "default": "false",
        "description": "Enable memorize global lock",
        "category": "performance",
        "type": "bool",
    },
    "MFLOW_PIPELINE_CONCURRENCY": {
        "default": "0",
        "description": "Pipeline data item concurrency limit (0=auto-detect: SQLite=1 serial, PostgreSQL=20 parallel)",
        "category": "performance",
        "type": "int",
    },
    # ========== Authentication & Security ==========
    "MFLOW_ENV": {
        "default": "",
        "description": "Environment mode (development/dev/local allows default keys; production/staging/test requires explicit config)",
        "category": "auth",
        "type": "str",
    },
    "FASTAPI_USERS_JWT_SECRET": {
        "default": None,
        "description": "JWT authentication secret (required in production)",
        "category": "auth",
        "sensitive": True,
        "type": "str",
    },
    "FASTAPI_USERS_RESET_PASSWORD_TOKEN_SECRET": {
        "default": None,
        "description": "Reset password token secret (required in production)",
        "category": "auth",
        "sensitive": True,
        "type": "str",
    },
    "FASTAPI_USERS_VERIFICATION_TOKEN_SECRET": {
        "default": None,
        "description": "Verification token secret (required in production)",
        "category": "auth",
        "sensitive": True,
        "type": "str",
    },
    "ENABLE_BACKEND_ACCESS_CONTROL": {
        "default": "true",
        "description": "Enable backend access control",
        "category": "auth",
        "type": "bool",
    },
    "JWT_SECRET_KEY": {
        "default": None,
        "description": "JWT signing secret (deprecated, use FASTAPI_USERS_JWT_SECRET)",
        "category": "auth",
        "sensitive": True,
        "type": "str",
    },
    # ========== Logging & Debug ==========
    "MFLOW_LOG_LEVEL": {
        "default": "INFO",
        "description": "Log level (DEBUG, INFO, WARNING, ERROR)",
        "category": "logging",
        "type": "str",
    },
    "MFLOW_TRACE_ENABLED": {
        "default": "false",
        "description": "Enable OpenTelemetry tracing",
        "category": "logging",
        "type": "bool",
    },
    # ========== Chunking Configuration ==========
    "CHUNK_SIZE": {
        "default": "1000",
        "description": "Chunk size (character count)",
        "category": "chunking",
        "type": "int",
    },
    "CHUNK_OVERLAP": {
        "default": "200",
        "description": "Chunk overlap size",
        "category": "chunking",
        "type": "int",
    },
    # ========== Coreference Resolution Configuration ==========
    "MFLOW_COREF_ENABLED": {
        "default": "true",
        "description": "Enable coreference resolution preprocessing",
        "category": "coreference",
        "type": "bool",
    },
    "MFLOW_COREF_MAX_HISTORY": {
        "default": "10",
        "description": "Entity history record limit (5-50)",
        "category": "coreference",
        "type": "int",
    },
    "MFLOW_COREF_SESSION_TTL": {
        "default": "3600",
        "description": "Session TTL (seconds, 60-86400)",
        "category": "coreference",
        "type": "int",
    },
    "MFLOW_COREF_MAX_SESSIONS": {
        "default": "10000",
        "description": "Maximum concurrent sessions (100-100000)",
        "category": "coreference",
        "type": "int",
    },
    "MFLOW_COREF_LANGUAGE": {
        "default": "auto",
        "description": "Language mode (auto, zh, en)",
        "category": "coreference",
        "type": "str",
    },
    "MFLOW_COREF_PARAGRAPH_RESET": {
        "default": "true",
        "description": "Reset partial context on new dialog turn",
        "category": "coreference",
        "type": "bool",
    },
}


def get_env(name: str, default: Any = None) -> Any:
    """
    Get environment variable value with type conversion.

    Args:
        name: Environment variable name.
        default: Default value when not set (overrides registry default).

    Returns:
        Environment variable value (type-converted).
    """
    info = ENV_REGISTRY.get(name, {})
    reg_default = info.get("default")
    final_default = default if default is not None else reg_default
    value = os.getenv(name, final_default)

    if value is None:
        return None

    # Type conversion
    var_type = info.get("type", "str")

    if var_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes", "on")

    if var_type == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return final_default

    if var_type == "float":
        try:
            return float(value)
        except (ValueError, TypeError):
            return final_default

    return value


def _get_env_var_registry(
    category: Optional[str] = None,
    mask_sensitive: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """
    Return environment variable registry with current values.

    Args:
        category: Optional filter ("core", "episodic", "retrieval", "database", "performance", "auth", "logging", "chunking").
        mask_sensitive: Whether to mask sensitive values.

    Returns:
        {var_name: {value, default, description, category, sensitive?, type?}}
    """
    result = {}

    for name, info in ENV_REGISTRY.items():
        # Filter by category
        if category and info.get("category") != category:
            continue

        # Get current value
        raw_value = os.getenv(name)
        current_value = raw_value if raw_value is not None else info.get("default")

        # Mask sensitive values
        display_value = current_value
        if mask_sensitive and info.get("sensitive") and current_value:
            val_str = str(current_value)
            if len(val_str) > 8:
                display_value = val_str[:4] + "***" + val_str[-4:]
            else:
                display_value = "***"

        result[name] = {
            "value": display_value,
            "default": info.get("default"),
            "description": info.get("description", ""),
            "category": info.get("category", "other"),
            "sensitive": info.get("sensitive", False),
            "type": info.get("type", "str"),
            "is_set": raw_value is not None,  # Flag indicating if explicitly set by user
        }

    return result


def get_categories() -> list[str]:
    """Return all available environment variable categories."""
    return sorted({v.get("category", "other") for v in ENV_REGISTRY.values()})
