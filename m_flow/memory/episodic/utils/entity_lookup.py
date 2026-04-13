# m_flow/memory/episodic/utils/entity_lookup.py
"""
Entity lookup functions for episodic memory writing.

This module contains async helper functions for finding existing entities
in the graph database by canonical name. These functions are used during
the entity creation phase to link new entities with existing same-name
entities via same_entity_as edges.

Extracted as part of large file refactoring (Phase 2).

Functions:
    - find_existing_entities_by_canonical_name: Find single entity by canonical name
    - batch_find_existing_entities_by_canonical_names: Batch find entities (N->1 query optimization)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from m_flow.adapters.graph import get_graph_provider
from m_flow.shared.logging_utils import get_logger

logger = get_logger("episodic.utils.entity_lookup")


async def find_existing_entities_by_canonical_name(
    canonical_name: str, exclude_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Find existing Entity nodes in the graph with the same canonical_name.

    This is used to link new entities to existing same-name entities
    via same_entity_as edges.

    Args:
        canonical_name: The normalized entity name to search for
        exclude_id: Optionally exclude an entity ID from results (e.g., the new entity itself)

    Returns:
        List of dicts with entity info: {id, name, description, canonical_name}
    """
    try:
        graph_engine = await get_graph_provider()

        nodes, _ = await graph_engine.query_by_attributes([{"type": ["Entity"]}])

        matching_entities = []
        for entity_id, props_raw in nodes:
            entity_id = str(entity_id)
            entity_name = props_raw.get("name", "") if isinstance(props_raw, dict) else ""

            # Parse properties
            props = props_raw
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (json.JSONDecodeError, TypeError, ValueError):
                    props = {}

            entity_canonical = props.get("canonical_name", "")
            if entity_canonical == canonical_name:
                if exclude_id and entity_id == str(exclude_id):
                    continue
                matching_entities.append(
                    {
                        "id": entity_id,
                        "name": entity_name,
                        "description": props.get("description", ""),
                        "canonical_name": entity_canonical,
                    }
                )

        return matching_entities

    except Exception as e:
        logger.warning(f"[episodic] Failed to find existing entities by canonical_name: {e}")
        return []


async def batch_find_existing_entities_by_canonical_names(
    canonical_names: List[str],
    exclude_ids: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Batch find existing Entity nodes with specified canonical_name.

    Compared to N calls to find_existing_entities_by_canonical_name,
    this function executes only 1 database query, significantly reducing database round trips.

    Args:
        canonical_names: List of canonical_name to search for
        exclude_ids: List of entity IDs to exclude (e.g., newly created entity itself)

    Returns:
        Dict[canonical_name, List[entity_info]]
        where entity_info = {id, name, description, canonical_name}
    """
    if not canonical_names:
        return {}

    exclude_set = set(exclude_ids or [])
    canonical_set = set(canonical_names)

    try:
        graph_engine = await get_graph_provider()

        nodes, _ = await graph_engine.query_by_attributes([{"type": ["Entity"]}])

        # Group by canonical_name
        result_map: Dict[str, List[Dict[str, Any]]] = {cn: [] for cn in canonical_names}

        for entity_id, props_raw in nodes:
            entity_id = str(entity_id)
            entity_name = props_raw.get("name", "") if isinstance(props_raw, dict) else ""

            # Skip excluded IDs
            if entity_id in exclude_set:
                continue

            # Parse properties
            props = props_raw
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (json.JSONDecodeError, TypeError, ValueError):
                    props = {}

            entity_canonical = props.get("canonical_name", "")

            # Only keep canonical_name in query list
            if entity_canonical in canonical_set:
                result_map[entity_canonical].append(
                    {
                        "id": entity_id,
                        "name": entity_name,
                        "description": props.get("description", ""),
                        "canonical_name": entity_canonical,
                    }
                )

        logger.info(
            f"[episodic] Batch entity lookup: {len(canonical_names)} names, "
            f"{sum(len(v) for v in result_map.values())} existing entities found"
        )

        return result_map

    except Exception as e:
        logger.warning(f"[episodic] Batch entity lookup failed: {e}")
        return {cn: [] for cn in canonical_names}


# Backwards compatibility aliases (with underscore prefix)
_find_existing_entities_by_canonical_name = find_existing_entities_by_canonical_name
_batch_find_existing_entities_by_canonical_names = batch_find_existing_entities_by_canonical_names


# ============================================================
# Module exports
# ============================================================

__all__ = [
    "find_existing_entities_by_canonical_name",
    "batch_find_existing_entities_by_canonical_names",
    # Backwards compatibility
    "_find_existing_entities_by_canonical_name",
    "_batch_find_existing_entities_by_canonical_names",
]
