"""
M-flow CLI Module
=================

Command-line interface components for M-flow.
Provides protocols, exceptions, and shared constants
used across all CLI commands.
"""

from __future__ import annotations

# Core exception type raised by commands
from m_flow.cli.exceptions import CliCommandException

# Protocol that all command implementations must follow
from m_flow.cli.reference import SupportsCliCommand

# Online documentation base URL for help messages
DEFAULT_DOCS_URL: str = "https://github.com/FlowElement-xinliuyuansu/m_flow"

# Public exports
__all__ = [
    "CliCommandException",
    "DEFAULT_DOCS_URL",
    "SupportsCliCommand",
]
