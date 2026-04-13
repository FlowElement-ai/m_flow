# m_flow/memory/episodic/entity_description_optimizer.py
"""
Entity Description Optimizer

Post-ingestion LLM module to optimize merged entity descriptions.

This module is NOT automatically triggered during ingestion.
It should be manually invoked after ingestion to optimize all
entities that have been merged (merge_count > 0 or multiple context roles).

Usage:
    from m_flow.memory.episodic.entity_description_optimizer import optimize_merged_descriptions

    # Optimize all merged entities in database
    stats = await optimize_merged_descriptions()

    # Or optimize specific episodes
    stats = await optimize_merged_descriptions(episode_ids=["episode_123", "episode_456"])
"""

from __future__ import annotations

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from m_flow.shared.logging_utils import get_logger
from m_flow.memory.episodic.entity_description_merger import (
    count_context_roles,
)
from m_flow.llm.LLMGateway import LLMService
from m_flow.llm.prompts.read_query_prompt import read_query_prompt
from m_flow.adapters.graph import get_graph_provider
from m_flow.memory.episodic.edge_text_generators import make_facet_involves_entity_edge_text
from m_flow.memory.episodic.normalization import truncate
from m_flow.knowledge.graph_ops.models.RelationType import RelationType
from m_flow.core.domain.utils.generate_edge_id import generate_edge_id
from m_flow.storage.index_memory_nodes import index_memory_nodes

logger = get_logger("episodic.desc_optimizer")


# ============================================================
# Optimization Statistics
# ============================================================


@dataclass
class OptimizationStats:
    """Statistics from description optimization run."""

    total_entities_scanned: int = 0
    entities_needing_optimization: int = 0
    entities_optimized: int = 0
    entities_failed: int = 0
    entities_skipped: int = 0  # Already optimized or too short


# ============================================================
# LLM Optimization
# ============================================================


async def _llm_optimize_description(
    entity_name: str,
    merged_description: str,
    entity_type: str = "Thing",
) -> Optional[str]:
    """
    Use LLM to rewrite a merged description into an optimal form.

    Args:
        entity_name: Entity name
        merged_description: Description with multiple context roles
        entity_type: Entity type (Person, Organization, etc.)

    Returns:
        Optimized description, or None if failed
    """
    system_prompt = read_query_prompt("optimize_merged_description.txt")
    if not system_prompt:
        # Fallback system prompt
        system_prompt = """You are rewriting entity descriptions to be more coherent.

The input description has been built incrementally with multiple context roles appended.
Your task is to synthesize these into a single, high-quality description.

FORMAT:
- First clause: Brief definition of the entity (what it is)
- Second clause(s): Key roles/significance across contexts
- Use semicolons to separate clauses
- Include specific facts and numbers when available
- Keep concise (2-3 sentences max)
- Preserve all important information, remove redundancy

OUTPUT: Return ONLY the optimized description text, no explanation."""

    user_prompt = f"""ENTITY_NAME: {entity_name}
ENTITY_TYPE: {entity_type}

CURRENT_DESCRIPTION (merged from multiple events):
{merged_description}

Please rewrite this into an optimal, coherent description."""

    try:
        result = await LLMService.acreate(
            text_input=user_prompt,
            system_prompt=system_prompt,
        )

        optimized = result.content.strip() if result and result.content else None

        if optimized:
            logger.debug(
                f"[optimizer] Optimized description for '{entity_name}': {len(merged_description)} -> {len(optimized)} chars"
            )

        return optimized

    except Exception as e:
        logger.warning(f"[optimizer] LLM failed for '{entity_name}': {e}")
        return None


# ============================================================
# Main Optimization Function
# ============================================================


async def optimize_merged_descriptions(
    episode_ids: Optional[List[str]] = None,
    min_context_roles: int = 2,
    dry_run: bool = False,
    batch_size: int = 10,
) -> OptimizationStats:
    """
    Optimize descriptions for all entities that have been merged.

    This function scans the database for entities with multiple context roles
    (from incremental merging) and uses LLM to rewrite them into optimal form.

    Args:
        episode_ids: Optional list of episode IDs to limit scope
        min_context_roles: Minimum number of context roles to trigger optimization (default 2)
        dry_run: If True, don't write changes to database
        batch_size: Number of entities to process per batch

    Returns:
        OptimizationStats with counts
    """
    stats = OptimizationStats()

    logger.info(f"[optimizer] Starting description optimization (min_roles={min_context_roles}, dry_run={dry_run})")

    graph = await get_graph_provider()

    # Query for all Entity nodes via abstract graph API
    try:
        if episode_ids:
            # Get entities connected to specified episodes via edge traversal
            seen_entity_ids: set = set()
            entity_nodes: List[Dict[str, Any]] = []
            for ep_id in episode_ids:
                edges = await graph.get_edges(ep_id)
                for _src_props, rel_name, dst_props in edges:
                    if (
                        rel_name == "involves_entity"
                        and dst_props.get("type") == "Entity"
                        and dst_props.get("id") not in seen_entity_ids
                    ):
                        seen_entity_ids.add(dst_props["id"])
                        entity_nodes.append(dst_props)
        else:
            # All Entity nodes
            nodes, _edges = await graph.query_by_attributes([{"type": ["Entity"]}])
            entity_nodes = [props for _nid, props in nodes]
    except Exception as e:
        logger.error(f"[optimizer] Failed to query entities: {e}")
        return stats

    # Collect entities needing optimization
    entities_to_optimize: List[Dict[str, Any]] = []

    for node in entity_nodes:
        stats.total_entities_scanned += 1

        entity_id = node.get("id")
        name = node.get("name")
        description = node.get("description") or ""
        entity_type = node.get("entity_type") or "Thing"
        merge_count = node.get("merge_count") or 0

        # Check if needs optimization
        num_roles = count_context_roles(description)

        if num_roles >= min_context_roles or merge_count >= min_context_roles:
            entities_to_optimize.append(
                {
                    "id": entity_id,
                    "name": name,
                    "description": description,
                    "entity_type": entity_type,
                    "num_roles": num_roles,
                }
            )

    stats.entities_needing_optimization = len(entities_to_optimize)
    logger.info(f"[optimizer] Found {stats.entities_needing_optimization} entities needing optimization")

    if dry_run:
        logger.info("[optimizer] Dry run - no changes will be written")
        return stats

    # Process in batches
    # Collect edges to re-index after all updates
    edges_to_reindex: List[Dict[str, Any]] = []

    async def optimize_entity(entity: Dict[str, Any]) -> bool:
        """
        Optimize single entity, returns True if successful.

        Updates:
        1. Entity node description
        2. Episode → Entity edges (edge_text = "entity | description")
        3. Facet → Entity edges (edge_text = "entity | description (in: facet_context)")
        """
        optimized = await _llm_optimize_description(
            entity_name=entity["name"],
            merged_description=entity["description"],
            entity_type=entity["entity_type"],
        )

        if not optimized:
            return False

        entity_id = entity["id"]
        entity_name = entity["name"]
        truncated_desc = truncate(optimized, 200)

        try:
            # 1. Update Entity node description
            await graph.update_node(entity_id, {"description": optimized, "optimized": True})

            # 2. Query all involves_entity edges pointing TO this entity
            # Uses abstract get_edges + Python filter for 'involves_entity'
            all_edges = await graph.get_edges(entity_id)
            edge_results = [(_src, _rel, dst) for _src, _rel, dst in all_edges if _rel == "involves_entity"]

            if not edge_results:
                logger.debug(f"[optimizer] No edges found for entity '{entity_name}'")
                return True

            # 3. Update each edge with appropriate edge_text format
            updated_edge_texts: List[str] = []

            for _entity_props, _rel_name, neighbor_props in edge_results:
                src_id = neighbor_props.get("id")
                src_type = neighbor_props.get("type") or ""
                src_search_text = neighbor_props.get("search_text") or ""

                # Generate appropriate edge_text based on source node type
                if src_type == "Facet":
                    # Facet → Entity: include facet context
                    new_edge_text = make_facet_involves_entity_edge_text(
                        entity_name=entity_name,
                        entity_description=truncated_desc,
                        facet_search_text=src_search_text,
                    )
                else:
                    # Episode → Entity: standard format
                    new_edge_text = f"{entity_name} | {truncated_desc}"

                # Update the edge — raw query() required: no abstract method
                # exists for edge property updates.  Backend-specific Cypher.
                update_single_edge_query = """
                    MATCH (src:Node)-[r:EDGE]->(c:Node)
                    WHERE src.id = $src_id AND c.id = $entity_id 
                        AND r.relationship_name = 'involves_entity'
                    SET r.edge_text = $edge_text
                """
                await graph.query(
                    update_single_edge_query,
                    {
                        "src_id": src_id,
                        "entity_id": entity_id,
                        "edge_text": new_edge_text,
                    },
                )

                updated_edge_texts.append(new_edge_text)

            # 4. Collect edge info for re-indexing (all unique edge_texts)
            for edge_text in set(updated_edge_texts):
                edges_to_reindex.append(
                    {
                        "entity_id": entity_id,
                        "entity_name": entity_name,
                        "edge_text": edge_text,
                    }
                )

            logger.debug(
                f"[optimizer] Updated entity + {len(edge_results)} edges: '{entity_name}' "
                f"({len(optimized)} chars desc, {len(updated_edge_texts)} edge_texts)"
            )

            return True
        except Exception as e:
            logger.warning(f"[optimizer] Failed to update '{entity_name}': {e}")
            return False

    # Process batches
    for i in range(0, len(entities_to_optimize), batch_size):
        batch = entities_to_optimize[i : i + batch_size]

        logger.info(f"[optimizer] Processing batch {i // batch_size + 1} ({len(batch)} entities)")

        results = await asyncio.gather(
            *[optimize_entity(e) for e in batch],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                stats.entities_failed += 1
            elif r:
                stats.entities_optimized += 1
            else:
                stats.entities_failed += 1

    logger.info(
        f"[optimizer] Optimization complete: "
        f"scanned={stats.total_entities_scanned}, "
        f"needed={stats.entities_needing_optimization}, "
        f"optimized={stats.entities_optimized}, "
        f"failed={stats.entities_failed}"
    )

    # ============================================================
    # Re-index edge vectors for updated edges
    # ============================================================
    if edges_to_reindex and not dry_run:
        logger.info(f"[optimizer] Re-indexing {len(edges_to_reindex)} updated edge vectors...")

        try:
            await _reindex_updated_edges(edges_to_reindex)
            logger.info("[optimizer] Edge vector re-indexing complete")
        except Exception as e:
            logger.warning(f"[optimizer] Edge vector re-indexing failed: {e}")
            # Don't fail the whole operation for vector indexing failure

    return stats


async def _reindex_updated_edges(edges_to_reindex: List[Dict[str, Any]]) -> None:
    """
    Re-index the vector embeddings for updated edge_text values.

    The edge_text is stored in RelationType_relationship_name vector collection.
    We need to update/insert the new edge_text vectors.
    """
    if not edges_to_reindex:
        return

    # Create RelationType nodes for each unique edge_text
    # Note: RelationType aggregates by edge_text, so we deduplicate
    unique_edge_texts: Dict[str, int] = {}
    for edge_info in edges_to_reindex:
        edge_text = edge_info["edge_text"]
        unique_edge_texts[edge_text] = unique_edge_texts.get(edge_text, 0) + 1

    relation_nodes = [
        RelationType(
            id=generate_edge_id(edge_id=edge_text),
            relationship_name=edge_text,
            number_of_edges=count,
        )
        for edge_text, count in unique_edge_texts.items()
    ]

    # Index the relation nodes (will upsert vectors)
    await index_memory_nodes(relation_nodes)

    logger.info(
        f"[optimizer] Re-indexed {len(relation_nodes)} unique edge_text vectors from {len(edges_to_reindex)} edges"
    )


# ============================================================
# CLI Entry Point
# ============================================================


async def main():
    """CLI entry point for running optimization."""
    import argparse

    parser = argparse.ArgumentParser(description="Optimize merged entity descriptions")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    parser.add_argument("--min-roles", type=int, default=2, help="Min context roles to optimize")
    parser.add_argument("--batch-size", type=int, default=10, help="Batch size")
    parser.add_argument("--episodes", nargs="*", help="Specific episode IDs to optimize")

    args = parser.parse_args()

    stats = await optimize_merged_descriptions(
        episode_ids=args.episodes,
        min_context_roles=args.min_roles,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )

    logger.info("Optimization Complete:")
    logger.info("  Scanned:  %d", stats.total_entities_scanned)
    logger.info("  Needed:   %d", stats.entities_needing_optimization)
    logger.info("  Optimized: %d", stats.entities_optimized)
    logger.info("  Failed:   %d", stats.entities_failed)


if __name__ == "__main__":
    asyncio.run(main())
