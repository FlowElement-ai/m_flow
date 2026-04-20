"""M-flow MCP Server — Model Context Protocol integration.

This package implements an MCP server that exposes M-flow's knowledge graph
operations to AI assistants and tools via the Model Context Protocol.

Usage:
    # Start the MCP server (stdio mode, default)
    python -m src.server

    # Start with SSE transport
    python -m src.server --transport sse --port 8000

    # Via Docker (recommended)
    docker compose --profile mcp up -d

Available MCP Tools (11):
    - memorize         : Convert data to knowledge graph (data, dataset_name, wait)
                         Returns a task_id; use `wait=True` for synchronous mode.
    - save_interaction : Save user-agent interaction records (data, wait)
                         Returns a task_id; use `wait=True` for synchronous mode.
    - search           : Search knowledge graph (search_query, recall_mode, top_k, datasets,
                         system_prompt, enable_hybrid_search)
    - list_data        : List datasets and data items (dataset_id)
    - delete           : Delete data items (data_id, dataset_id, mode)
    - prune            : Reset knowledge graph (graph, vector, metadata, cache)
    - memorize_status  : Get memorization pipeline status, or per-task outcome
                         when called with `task_id` (issue #111).
    - learn            : Extract procedural memory (datasets, episode_ids, run_in_background)
    - update_data      : Update existing data (data_id, data, dataset_id)
    - ingest           : One-step ingestion (data, dataset_name, skip_memorize)
    - query            : Simplified query (question, datasets, mode, top_k)

Supported Recall Modes:
    - TRIPLET_COMPLETION : Complete triplets + LLM answer
    - EPISODIC           : Retrieve episodic memories
    - PROCEDURAL         : Retrieve procedural knowledge
    - CYPHER             : Execute Cypher queries (advanced)
    - CHUNKS_LEXICAL     : Lexical chunk retrieval (exact match)

Transport Modes:
    - stdio : Standard I/O (default for local, IDE integration)
    - sse   : Server-Sent Events (default for Docker, web clients)
    - http  : HTTP streaming (REST API clients)

Environment Variables:
    - TRANSPORT_MODE  : Transport mode (stdio/sse/http)
    - MCP_PORT        : Server port (default: 8000)
    - MCP_LOG_LEVEL   : Logging level (default: info)

Command-line Arguments:
    --transport     : Transport mode (stdio/sse/http, default: stdio)
    --host          : Bind host (default: 127.0.0.1)
    --port          : Bind port (default: 8000)
    --log-level     : Log level (debug/info/warning/error, default: info)
    --api-url       : M-flow API URL (enables API mode)
    --api-token     : API authentication token
    --no-migration  : Skip database migrations
    --path          : HTTP endpoint path (default: /mcp)

API Mode:
    When --api-url is set, MCP server calls M-flow via HTTP API instead of
    direct function calls. Useful for distributed deployments.
    Note: prune operation is not available in API mode.
"""

__version__ = "0.6.0"
__all__ = ["server", "client", "m_flow_client"]
