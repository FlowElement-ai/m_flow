# Changelog

All notable changes to the M-flow MCP Server are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Silent data loss on background-task failures (#111).** `memorize` and
  `save_interaction` previously used `asyncio.create_task` and immediately
  returned a "started" message, so any failure in the background coroutine
  (expired LLM key, locked graph DB, malformed input, etc.) was only logged
  server-side. The MCP caller never learned about it, producing a silent
  data-loss scenario.
- **`query` and `prune` unavailable in remote / API mode (#112).** When the
  MCP server ran with `--api-url` (the recommended Docker production
  topology), three tools raised `NotImplementedError` and asked the user
  to fall back to in-process direct mode, which defeats the purpose of the
  API architecture. They now call real backend endpoints:
  - `query()` → `POST /api/v1/search/query` (new endpoint, see *Added*).
  - `prune_data()` → `POST /api/v1/prune/data`.
  - `prune_system()` → `POST /api/v1/prune/system`.

  The two prune endpoints already existed with full security (superuser
  auth, confirmation strings, active-pipeline check, distributed lock,
  cooldown, master switch) and required no backend changes — the fix was
  simply to wire the MCP client to call them.

### Added

- Module-level **task registry** (bounded LRU, default 100 records) that
  captures each background task's `state` (`running`/`success`/`failed`),
  timestamps, dataset, error type, and error message.
- `memorize` and `save_interaction` now return a **`task_id`** in the
  response payload that callers can use to poll the outcome.
- `memorize_status` now accepts an optional **`task_id`** argument and, when
  provided, returns the recorded task outcome instead of pipeline-level
  status. Without `task_id` the historical pipeline-level behaviour is
  preserved (backward compatible).
- `memorize` and `save_interaction` accept a new **`wait: bool = False`**
  parameter; when `True` the tool synchronously awaits the background task
  (with a configurable 600 s timeout) and returns the actual outcome inline,
  which is useful for CI / scripting flows that need a definitive result.
- 11 new unit tests in `test_server_task_tracking.py` covering success,
  failure, status lookup, sync mode (success / error / timeout), and LRU
  eviction.
- New backend endpoint **`POST /api/v1/search/query`** (in
  `m_flow/api/v1/search/routers/get_search_router.py`) that wraps the
  existing in-process `m_flow.api.v1.search.search.query()` helper so the
  simplified question / mode / top_k contract is reachable over HTTP.
  Authentication and dataset-visibility rules match the existing
  `POST /api/v1/search` endpoint.
- 8 new client tests in `test_m_flow_client_remote_query_prune.py`
  covering remote-mode `query()`, `prune_data()`, `prune_system()` URL,
  payload, auth header, and error propagation.
- 5 new structural tests in
  `m_flow/tests/unit/api/test_search_simplification.py` confirming the
  new `/query` endpoint, its DTOs, delegation to the existing helper,
  and authentication enforcement.

### Changed

- The `prune` and `query` MCP tools now surface backend HTTP error codes
  (`403` "API disabled", `401` "no superuser token", `409` "active
  pipelines", `429` "cooldown") with actionable hints, replacing the
  previous "use direct mode" message which was no longer accurate.

## [0.6.0] - 2026-03-19

### Added

#### New Tools
- `learn`: Extract procedural memory from existing episodes
  - Parameters: `datasets`, `episode_ids`, `run_in_background`
  - Converts episodic memory into executable steps and rules
- `update_data`: Update existing data items
  - Parameters: `data_id` (required), `data` (required), `dataset_id` (required)
  - Includes UUID format validation
- `ingest`: One-step data ingestion (add + memorize)
  - Parameters: `data` (required), `dataset_name`, `skip_memorize`
  - Simplifies the data import workflow
- `query`: Simplified query interface
  - Parameters: `question` (required), `datasets`, `mode`, `top_k`
  - Supported modes: episodic, triplet, chunks, procedural, cypher

#### Enhanced Tools
- `search`: Added `datasets`, `system_prompt`, `enable_hybrid_search` parameters
- `prune`: Added `graph`, `vector`, `metadata`, `cache` parameters for selective cleanup
  - `metadata` defaults to `False` for safety
- `memorize`: Added `dataset_name` parameter for dataset targeting

#### Testing
- Added 8 new unit tests (24 total test methods)
- Created `test_integration.py` with 5 integration test scenarios:
  - Full workflow test
  - Dataset isolation test
  - Error recovery test
  - Concurrent operations test
  - API mode handling test
- Created `test_e2e.sh` for Docker end-to-end testing with 9 verification steps

### Fixed

#### Critical Bugs
- Fixed INSIGHTS dead code causing KeyError in search
- Fixed `delete` mode parameter case mismatch (uppercase passed to API expecting lowercase)
- Fixed `client.py` example missing required parameters for `memorize` and `search`

#### Docker/Deployment
- Fixed environment variable passing in `entrypoint.sh` (TRANSPORT_MODE, MCP_PORT, MCP_LOG_LEVEL)
- Fixed `--log-level` argument not being applied in server startup
- Fixed Dockerfile HEALTHCHECK (corrected port from 8765 to 8000, added curl)
- Fixed Docker port mapping consistency (8001:8000)

#### Testing
- Fixed test coverage for `save_interaction` tool
- Added missing tests for parameter validation (invalid mode, invalid top_k, invalid UUID)

#### Code Quality
- Fixed httpx files parameter format (using list format for multipart)
- Added `_client.close()` for proper resource cleanup in server shutdown
- Added return type annotations to `prune()` and `memorize_status()`

### Changed

- Updated version from 0.5.2 to 0.6.0
- Unified environment variable naming to `TRANSPORT_MODE`
- Enhanced parameter validation:
  - `search`: validates `recall_mode` against whitelist
  - `query`: validates `mode` against whitelist
  - `delete`: validates `mode` against whitelist and standardizes to lowercase
  - All UUID parameters validated before processing
  - `top_k` validated to range 1-100

### Improved

- Better exception handling with `NotImplementedError` for unsupported API mode operations
- More informative error messages with emoji indicators (✅ ❌ ⚠️)
- Enhanced logging throughout all tools
- Improved traceback capture for debugging memorize failures

### Documentation

- Completely rewrote `README.md`:
  - Added comprehensive testing section (unit, integration, E2E)
  - Added advanced configuration section
  - Updated tool documentation with all parameters
- Updated `docs/guides/integrations/mcp.md`:
  - Added all 11 tools with detailed parameter documentation
  - Added IDE integration examples (Cursor, Claude Desktop, VS Code + Continue)
  - Added troubleshooting guide
- Updated `__init__.py` module docstring:
  - Listed all 11 available tools
  - Documented command-line arguments
  - Added API mode explanation

---

## [0.5.2] - 2026-03-18

### Fixed
- Minor documentation updates
- Version synchronization between pyproject.toml and __init__.py

---

## [0.5.1] - 2026-03-15

### Added
- Initial MCP server implementation
- Core tools: memorize, save_interaction, search, list_data, delete, prune, memorize_status
- Support for stdio, SSE, and HTTP transport modes
- Docker deployment support
- Basic test client
