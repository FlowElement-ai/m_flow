"""
Environment file persistence utilities.

Provides atomic write operations for .env file updates,
preserving comments and formatting while updating values.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Optional
import asyncio

from m_flow.shared.logging_utils import get_logger

logger = get_logger("config.persist")

# File lock for concurrent access protection
_env_lock = asyncio.Lock()


def _find_env_file() -> Path:
    """
    Locate the .env file to update.

    Search order:
    1. MFLOW_ENV_FILE environment variable
    2. Current working directory
    3. Project root (assuming standard layout)

    Returns:
        Path to .env file (may not exist yet).
    """
    # Check explicit path first
    explicit = os.getenv("MFLOW_ENV_FILE")
    if explicit:
        return Path(explicit)

    # Check current directory
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env

    # Check project root (assuming m_flow is installed in site-packages or project dir)
    try:
        import m_flow

        module_path = Path(m_flow.__file__).parent

        # If running from source: m_flow/../.env
        project_root = module_path.parent
        if (project_root / ".env").exists():
            return project_root / ".env"

        # If running from mflow-main: mflow-main/.env
        mflow_main = project_root
        if mflow_main.name == "m_flow" and (mflow_main.parent / ".env").exists():
            return mflow_main.parent / ".env"
    except Exception:
        pass

    # Default to current directory
    return cwd_env


def _parse_env_file(content: str) -> list[tuple[str, Optional[str], str]]:
    """
    Parse .env file content into structured lines.

    Returns list of tuples:
        (key, value, original_line)

    For non-assignment lines (comments, empty):
        (None, None, original_line)
    """
    lines = []
    for line in content.split("\n"):
        stripped = line.strip()

        # Empty line or comment
        if not stripped or stripped.startswith("#"):
            lines.append((None, None, line))
            continue

        # Try to parse KEY=VALUE
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", stripped)
        if match:
            key = match.group(1)
            value = match.group(2)
            # Remove quotes if present
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            lines.append((key, value, line))
        else:
            # Unparseable line, keep as-is
            lines.append((None, None, line))

    return lines


def _format_value(value: str) -> str:
    """
    Format a value for .env file.

    Quotes values containing special characters.
    """
    # Check if quoting is needed
    if any(c in value for c in [" ", "\t", '"', "'", "$", "`", "\\", "\n"]):
        # Escape existing quotes and backslashes
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


async def persist_env_values(updates: Dict[str, str]) -> bool:
    """
    Atomically update .env file with new values.

    - Updates existing keys in place, preserving order and comments
    - Appends new keys at the end
    - Uses atomic write (temp file + rename)

    Args:
        updates: Dictionary of key-value pairs to update.

    Returns:
        True if file was updated, False on error.

    Note:
        Empty values are preserved (key=).
        Pass None to skip a key update.
    """
    async with _env_lock:
        env_path = _find_env_file()

        # Filter out None values
        updates = {k: v for k, v in updates.items() if v is not None}
        if not updates:
            return True

        try:
            # Read existing content
            if env_path.exists():
                content = env_path.read_text(encoding="utf-8")
            else:
                content = ""

            # Parse into structured lines
            lines = _parse_env_file(content)

            # Track which keys we've updated
            updated_keys = set()

            # Process existing lines
            new_lines = []
            for key, _old_value, original_line in lines:
                if key and key in updates:
                    # Update this key
                    new_value = _format_value(updates[key])
                    new_lines.append(f"{key}={new_value}")
                    updated_keys.add(key)
                else:
                    # Keep original line
                    new_lines.append(original_line)

            # Append new keys that weren't in the file
            new_keys = set(updates.keys()) - updated_keys
            if new_keys:
                # Add blank line separator if file doesn't end with newline
                if new_lines and new_lines[-1].strip():
                    new_lines.append("")

                # Add comment for new keys
                new_lines.append("# Added by M-flow API")
                for key in sorted(new_keys):
                    new_value = _format_value(updates[key])
                    new_lines.append(f"{key}={new_value}")

            # Write to temp file, then rename (atomic on most filesystems)
            new_content = "\n".join(new_lines)
            if not new_content.endswith("\n"):
                new_content += "\n"

            # Create temp file in same directory for atomic rename
            dir_path = env_path.parent
            dir_path.mkdir(parents=True, exist_ok=True)

            fd, temp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".env.tmp")
            try:
                os.write(fd, new_content.encode("utf-8"))
                os.close(fd)

                # Atomic rename
                os.replace(temp_path, str(env_path))

                logger.info(f"Persisted {len(updates)} config values to {env_path}")
                return True

            except Exception as e:
                # Cleanup temp file on error
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                raise e

        except Exception as e:
            logger.error(f"Failed to persist env values: {e}")
            return False


async def persist_llm_config(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> bool:
    """
    Persist LLM configuration to .env file.

    Only non-None values are updated.
    """
    updates = {}
    if provider is not None:
        updates["LLM_PROVIDER"] = provider
    if model is not None:
        updates["LLM_MODEL"] = model
    if api_key is not None:
        updates["LLM_API_KEY"] = api_key

    return await persist_env_values(updates)


async def persist_vector_db_config(
    provider: Optional[str] = None,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> bool:
    """
    Persist vector database configuration to .env file.

    Only non-None values are updated.
    """
    updates = {}
    if provider is not None:
        updates["VECTOR_DB_PROVIDER"] = provider
    if url is not None:
        updates["VECTOR_DB_URL"] = url
    if api_key is not None:
        updates["VECTOR_DB_KEY"] = api_key

    return await persist_env_values(updates)
