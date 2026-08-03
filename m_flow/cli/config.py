"""
M-flow CLI configuration and shared constants.

Defines settings, choices, and descriptions used by
CLI commands. Centralizes magic strings for consistency.
"""

from __future__ import annotations

# ============================================================
#  Top-level settings
# ============================================================

CLI_DESCRIPTION: str = "M-flow CLI — Manage knowledge graphs and cognitive processing pipelines."

DEFAULT_DOCS_URL: str = "https://github.com/FlowElement-xinliuyuansu/m_flow"

# ============================================================
#  Command metadata
# ============================================================

COMMAND_DESCRIPTIONS: dict[str, str] = {
    "add": "Ingest data for knowledge graph construction",
    "search": "Retrieve information from the knowledge graph",
    "memorize": "Convert ingested data to a structured graph",
    "delete": "Remove data from the knowledge base",
    "config": "View or modify configuration values",
}

# ============================================================
#  Search / recall mode choices
# ============================================================

RECALL_MODE_CHOICES: list[str] = [
    "TRIPLET_COMPLETION",
    "CYPHER",
    "EPISODIC",
    "PROCEDURAL",
    "CHUNKS_LEXICAL",
]

# ============================================================
#  Chunker options
# ============================================================

CHUNKER_CHOICES: list[str] = [
    "TextChunker",
    "LangchainChunker",
    "CsvChunker",
]

# ============================================================
#  Output formatting
# ============================================================

OUTPUT_FORMAT_CHOICES: list[str] = [
    "json",
    "pretty",
    "simple",
]
