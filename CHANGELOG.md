# Changelog

All notable changes to M-flow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.4] - 2026-04-13

### Fixed
- **Critical**: Remove `max_tokens` parameter from LLM calls — incompatible with GPT-5 series,
  caused all LLM-dependent operations to fail with "Connection error" after 120s retry
- **Critical**: Fix session/conversation history crash — 3 retrievers called `compress_text(str)`
  but function requires `list[ContentFragment]`; replaced with correct `summarize_text(str)`
- Fix `LLMGateway.transcribe_audio` calling non-existent `create_transcript` method on adapter
- Fix `LLMGateway.describe_image` calling non-existent `transcribe_image` method on adapter
- Fix UUID serialization in search results, cache adapters, and graph property encoding
- Fix `_hash_sensitive` dead code — unreachable `return obj` caused telemetry data corruption
- Fix CLI `_ToggleDebug`/`_LaunchUi` argparse `dest` keyword conflict
- Fix ImageLoader JPEG extension mismatch (`.jpe`/`.jpeg` had leading dots)
- Fix Bedrock adapter `show_prompt` calling non-existent `LLMService.read_query_prompt`
- Fix `OpenAIAdapter.describe_image` blocking event loop (sync `litellm.completion` → async `acompletion`)
- Fix `OpenAIAdapter.transcribe_audio` blocking event loop (`litellm.transcription` → `atranscription`)
- Fix retry decorator across all 14 LLM/embedding adapters — now excludes `BadRequestError`
  and `AuthenticationError` to prevent 120-second retry storms on deterministic client errors
- Fix dedup `_build_modern_id` tenant_id normalization (None → empty string)
- Fix graph relationship ledger UUID collision on batch insert
- Fix PostgreSQL `delete_database` missing `cache_clear()` (SQLite branch already had it)
- Remove WARNING-level debug log that printed user input on every structured extraction call

### Changed
- Replace `Concept_name` with `Entity_name` in vector collection references (class renamed during migration)
- Update ruff configuration and format entire codebase (311+ files)

### Security
- Pin all GitHub Actions to exact commit SHA (81 GitHub-owned + 21 third-party)
- Add `permissions: {contents: read}` to 7 workflow files lacking token restrictions
- Add `.github/dependabot.yml` for automated dependency updates
- Fix OpenSSF Scorecard SARIF upload (codeql-action SHA dereference)

### CI
- Configure GitHub Secrets for LLM/embedding API keys
- Fix workflow YAML syntax errors (matrix refs in workflow_call job-level conditions)
- Fix `uv.lock` consistency
- Update test assertions to match current API signatures and edge type names
- Remove 6 phantom workflow jobs referencing non-existent test scripts
- Skip S3/Modal tests requiring external paid services

## [0.3.3] - 2026-04-12

### Added
- Episode naming in precise mode when content routing is disabled
- New LLM prompt `precise_name_single_content.txt` for single-content Episode naming

### Fixed
- Generate meaningful Episode names instead of hardcoded "Content" in precise mode

## [0.3.2] - 2026-04-05

### Added
- Precise summarization mode (`precise_mode`): preserves all original factual information
  (dates, numbers, names, constraints) with lower compression ratio — RAG context will be
  longer but more accurate. Uses two-step pipeline: JSON topic routing + per-section
  concurrent compression with anchor verification
- Configurable via API parameter, environment variable (`MFLOW_PRECISE_MODE`), or frontend toggle
- KuzuDB adapter: entry-level deduplication and broadened error recovery for batch operations

### Fixed
- KuzuDB "duplicated primary key" crash in UNWIND+MERGE operations
- Edge deduplication key collision in `deduplicate_nodes_and_edges`

## [0.3.1] - 2026-03-28

### Added
- Procedural memory extraction and retrieval
- Model Context Protocol (MCP) server support
- Frontend Knowledge Graph visualization with procedural subgraph
- Structured entry (manual ingest) with display text support
- Episodic retriever with adaptive bundle search
- Multi-dataset search with access control
- Docker Compose profiles for flexible deployment

### Changed
- Improved episodic retrieval scoring with path-cost model
- Enhanced content routing with atomic mode for short inputs
- Structured JSON output for procedural search results
- Unified triplet search with configurable vector collections

### Fixed
- Episode naming using dedicated summarization prompt
- Content inflation from prompt injection in short inputs
- WebSocket authentication token handling
- Pipeline status tracking and stale detection
- Manual ingest graph creation and embedding generation

## [0.3.0] - 2026-02-15

### Added
- Multi-user access control with dataset isolation
- Episodic memory architecture (Episodes, Facets, FacetPoints, Entities)
- Frontend dashboard with real-time pipeline monitoring
- LanceDB vector storage integration
- KuzuDB graph database adapter

For detailed release notes, see [GitHub Releases](https://github.com/FlowElement-xinliuyuansu/m_flow/releases).
