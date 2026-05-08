# m_flow/memory/episodic/entity_description_merger.py
"""
Entity Description Merger

Smart merging of entity descriptions when events are merged into existing episodes.

Design:
1. Description has two parts: "Definition; Role in this context"
2. When merging, only append the "Role in this context" part
3. Use vector similarity to avoid duplicate appending
4. Track merge count for later LLM optimization
"""

from __future__ import annotations

from typing import Optional, Tuple, List

from m_flow.shared.logging_utils import get_logger
from m_flow.adapters.vector.embeddings import get_embedding_engine

logger = get_logger("episodic.entity_merger")


# ============================================================
# Description Parsing
# ============================================================


def parse_description(description: str) -> Tuple[str, str]:
    """
    Parse description into (definition, context_role) parts.

    Format: "Definition; Role in this context"

    Args:
        description: Full description string

    Returns:
        (definition, context_role) tuple
        If no semicolon found, returns (description, "")
    """
    if not description:
        return ("", "")

    # Try to split by semicolon (first occurrence)
    parts = description.split(";", 1)

    if len(parts) == 2:
        definition = parts[0].strip()
        context_role = parts[1].strip()
        return (definition, context_role)
    else:
        # No semicolon - treat entire description as definition
        return (description.strip(), "")


def extract_context_role(description: str) -> str:
    """
    Extract only the context-specific role part from description.

    Args:
        description: Full description string

    Returns:
        Context role part (after first semicolon), or empty string
    """
    _, context_role = parse_description(description)
    return context_role


# ============================================================
# Vector Similarity (Thread-Safe Lazy Loading)
# ============================================================

import asyncio
import numpy as np

_embed_engine = None
_embed_engine_lock = asyncio.Lock()


async def _get_embed_engine():
    """Lazy load embedding engine with thread safety."""
    global _embed_engine
    if _embed_engine is not None:
        return _embed_engine

    async with _embed_engine_lock:
        # Double-check after acquiring lock
        if _embed_engine is None:
            _embed_engine = get_embedding_engine()
        return _embed_engine


def _cosine_similarity_vectors(vec1, vec2) -> float:
    """Compute cosine similarity between two vectors."""
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))


async def compute_cosine_similarity(text1: str, text2: str) -> float:
    """
    Compute cosine similarity between two texts using embeddings.

    Args:
        text1: First text
        text2: Second text

    Returns:
        Cosine similarity score (0.0 to 1.0)
    """
    if not text1 or not text2:
        return 0.0

    try:
        engine = await _get_embed_engine()
        embeddings = await engine.embed_text([text1, text2])

        if len(embeddings) != 2:
            return 0.0

        return _cosine_similarity_vectors(embeddings[0], embeddings[1])
    except Exception as e:
        logger.warning(f"Failed to compute similarity: {e}")
        return 0.0


async def compute_max_similarity_batch(existing_texts: List[str], new_text: str) -> float:
    """
    Compute max similarity between new_text and all existing_texts.

    Optimized: Uses single batch embedding call instead of N separate calls.

    Args:
        existing_texts: List of existing texts to compare against
        new_text: New text to check

    Returns:
        Maximum similarity score across all comparisons
    """
    if not existing_texts or not new_text:
        return 0.0

    try:
        engine = await _get_embed_engine()

        # Batch embed all texts at once
        all_texts = [*existing_texts, new_text]
        embeddings = await engine.embed_text(all_texts)

        if len(embeddings) != len(all_texts):
            return 0.0

        # Last embedding is for new_text
        new_vec = embeddings[-1]

        # Compute similarity with each existing text
        max_sim = 0.0
        for i in range(len(existing_texts)):
            sim = _cosine_similarity_vectors(embeddings[i], new_vec)
            if sim > max_sim:
                max_sim = sim

        return max_sim
    except Exception as e:
        logger.warning(f"Failed to compute batch similarity: {e}")
        return 0.0


# ============================================================
# Smart Description Merging
# ============================================================


async def merge_entity_description(
    existing_desc: str,
    new_desc: str,
    entity_name: str,
    similarity_threshold: float = 0.75,
    max_total_length: int = 100000,  # Increased from 1500 to preserve full content
    max_context_roles: int = 50,  # Increased from 5
) -> Tuple[str, bool]:
    """
    Smart merge of entity descriptions.

    Only appends the "context role" part of the new description if it's
    semantically different from existing context roles.

    Args:
        existing_desc: Existing description in database
        new_desc: New description from current event
        entity_name: Entity name (for logging)
        similarity_threshold: Min similarity to consider as duplicate (default 0.75)
        max_total_length: Max total description length (default 1500)
        max_context_roles: Max number of context roles to keep (default 5)

    Returns:
        (merged_description, was_merged) tuple
        was_merged is True if new content was appended
    """
    if not existing_desc:
        return (new_desc, False)

    if not new_desc:
        return (existing_desc, False)

    # Parse both descriptions
    existing_def, existing_role = parse_description(existing_desc)
    new_def, new_role = parse_description(new_desc)

    # If new description has no context role, nothing to append
    if not new_role:
        logger.debug(f"[merger] Entity '{entity_name}': no context role in new description, keeping existing")
        return (existing_desc, False)

    # Extract all existing context roles (including appended ones)
    existing_roles = _extract_all_context_roles(existing_desc)

    # Check if we've reached max context roles
    if len(existing_roles) >= max_context_roles:
        logger.debug(f"[merger] Entity '{entity_name}': max context roles ({max_context_roles}) reached, skipping")
        return (existing_desc, False)

    # Check similarity with ALL existing context roles (batch optimized)
    max_similarity = await compute_max_similarity_batch(existing_roles, new_role)
    if max_similarity >= similarity_threshold:
        logger.debug(
            f"[merger] Entity '{entity_name}': new role similar to existing (max_sim={max_similarity:.3f}), skipping"
        )
        return (existing_desc, False)

    # Append new context role
    merged = f"{existing_desc}【+】{new_role}"

    # Length limit
    if len(merged) > max_total_length:
        # Truncate new role to fit
        available_len = max_total_length - len(existing_desc) - 10  # 10 for "【+】" + "..."
        if available_len < 20:
            # Not enough space, skip
            logger.debug(f"[merger] Entity '{entity_name}': no space for new role, skipping")
            return (existing_desc, False)
        merged = f"{existing_desc}【+】{new_role[:available_len]}..."

    logger.info(f"[merger] Entity '{entity_name}': appended new context role (now {len(existing_roles) + 1} roles)")

    return (merged, True)


def _extract_all_context_roles(description: str) -> List[str]:
    """
    Extract all context roles from a (possibly merged) description.

    Format examples:
    - "Definition; Role1" → ["Role1"]
    - "Definition; Role1【+】Role2【+】Role3" → ["Role1", "Role2", "Role3"]

    Returns:
        List of context role strings
    """
    if not description:
        return []

    # First, get the main context role (after first semicolon)
    _, context_part = parse_description(description)

    if not context_part:
        return []

    # Split by 【+】 delimiter
    roles = context_part.split("【+】")
    return [r.strip() for r in roles if r.strip()]


def count_context_roles(description: str) -> int:
    """
    Count the number of context roles in a description.

    Returns:
        Number of context roles (0 if none)
    """
    return len(_extract_all_context_roles(description))


# ============================================================
# Merge Count Tracking
# ============================================================


def increment_merge_count(current_count: Optional[int]) -> int:
    """Increment merge count, handling None."""
    return (current_count or 0) + 1
