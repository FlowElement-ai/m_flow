# m_flow/memory/procedural/write_procedural_from_episodic.py
"""
Write procedural memories from episodic routing decisions or existing episodic memories.

Two use cases:
1. During unified ingestion: Receive procedural candidates from episodic summarization
2. Post-hoc extraction: Extract procedural from already-stored episodic memories

This module provides a bridge between episodic and procedural memory systems.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from m_flow.shared.logging_utils import get_logger
from m_flow.shared.data_models import ProceduralCandidate
from m_flow.core.domain.models.memory_space import MemorySpace
from m_flow.core.domain.utils.generate_node_id import generate_node_id
from m_flow.adapters.graph import get_graph_provider
from m_flow.knowledge.summarization.models import FragmentDigest
from m_flow.memory.procedural.write_procedural_memories import (
    write_procedural_memories,
    _compile_and_build_procedure,
)
from m_flow.storage import persist_memory_nodes
from m_flow.data.processing.document_types import Document
from m_flow.ingestion.chunking.models import ContentFragment

logger = get_logger("procedural_from_episodic")


def _safe_uuid(s: str) -> UUID:
    """
    Safely convert string to UUID, generate new UUID on failure.

    Args:
        s: String that may or may not be a valid UUID

    Returns:
        UUID object (either parsed from string or newly generated)
    """
    try:
        return UUID(str(s))
    except (ValueError, AttributeError, TypeError):
        return uuid4()


def _create_virtual_chunk_for_extract(episode_id: str, summary: str) -> ContentFragment:
    """
    Create virtual ContentFragment for FragmentDigest.made_from field.

    This is required because FragmentDigest has a required 'made_from' field
    that expects a ContentFragment. For episodic extraction, we create a
    virtual ContentFragment that represents the Episode summary.

    Args:
        episode_id: The episode ID (used for tracing)
        summary: The episode summary text

    Returns:
        A virtual ContentFragment representing the episode summary
    """
    ep_uuid = _safe_uuid(episode_id)

    virtual_doc = Document(
        id=ep_uuid,
        name=f"[Episode Extract] {episode_id[:8] if episode_id else 'unknown'}",
        processed_path="memory://episodic-extract",
        external_metadata=None,
        mime_type="text/episodic-extract",
    )

    return ContentFragment(
        id=ep_uuid,
        text=summary,
        chunk_size=len(summary),
        chunk_index=0,
        cut_type="episodic-extract",
        is_part_of=virtual_doc,
        contains=[],
        metadata={"source_type": "episodic-extract", "episode_id": episode_id},
    )


async def write_procedural_from_decisions(
    decisions: List[Dict],
    **kwargs,
) -> List[Any]:
    """
    Write procedural memories from episodic routing decisions.

    This is used during unified ingestion when episodic summarization
    has already made procedural routing decisions.

    Args:
        decisions: List of decision dicts with:
            - episode_id: str
            - candidate: ProceduralCandidate
            - event_sentences: List[str] (the original content)
            - event_topic: str

    Returns:
        List of Procedure MemoryNode objects
    """
    if not decisions:
        return []

    logger.info(f"[procedural_from_decisions] Processing {len(decisions)} candidates")

    # Get or create MemorySpace with proper initialization
    nodeset: Optional[MemorySpace] = kwargs.get("nodeset")
    if nodeset is None:
        nodeset_name = kwargs.get("procedural_nodeset_name", "Procedural")
        nodeset = MemorySpace(
            id=generate_node_id(f"MemorySpace:{nodeset_name}"),
            name=nodeset_name,
        )

    # Process each candidate directly using _compile_and_build_procedure
    tasks = []

    for dec in decisions:
        candidate = dec.get("candidate")
        if not isinstance(candidate, ProceduralCandidate):
            logger.warning(f"[procedural_from_decisions] Invalid candidate type: {type(candidate)}")
            continue

        event_sentences = dec.get("event_sentences", [])
        episode_id = dec.get("episode_id", "")

        # Build content from event sentences
        content = " ".join(event_sentences) if event_sentences else ""
        if not content:
            continue

        # Build source_refs for tracing
        source_refs = [f"episode:{episode_id}"] if episode_id else None

        # Create compilation task
        tasks.append(
            _compile_and_build_procedure(
                content=content,
                candidate=candidate,
                nodeset=nodeset,
                source_refs=source_refs,
            )
        )

    if not tasks:
        logger.info("[procedural_from_decisions] No valid candidates after filtering")
        return []

    logger.info(f"[procedural_from_decisions] Compiling {len(tasks)} candidates")

    # Execute all compile tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter successful results
    procedures = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[procedural_from_decisions] Task failed: {r}")
        elif r is not None:
            procedures.append(r)

    logger.info(f"[procedural_from_decisions] Created {len(procedures)} procedures")
    return procedures


async def extract_procedural_from_episodic(
    episode_ids: Optional[List[str]] = None,
    limit: int = 100,
    dataset_id: Optional[str] = None,
    force_reprocess: bool = False,
    authorized_dataset_ids: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Extract procedural memories from already-stored episodic memories.

    This is used for post-hoc extraction when procedural was not enabled
    during initial ingestion.

    Uses the Router to support 1 episode → N procedures.

    Args:
        episode_ids: Optional list of episode IDs to process.
                    If None, queries recent episodes that haven't been processed.
        limit: Maximum number of episodes to process
        dataset_id: Optional dataset ID to filter episodes (uses Python filtering)
        force_reprocess: If True, reprocess episodes even if already marked
        authorized_dataset_ids: List of dataset IDs user is authorized to access.
                               If provided, only episodes from these datasets are processed.

    Returns:
        Dict with keys:
        - result: List[MemoryNode] - Generated procedure nodes
        - episodes_analyzed: int - Number of episodes actually analyzed
        - episodes_marked: int - Number of episodes marked as processed
        - nodes_written: int - Number of nodes written to graph database
    """
    # Convert to set for O(1) lookup
    authorized_ds_set = set(authorized_dataset_ids) if authorized_dataset_ids else None

    logger.info(
        f"[extract_from_episodic] Starting extraction, limit={limit}, "
        f"dataset_id={dataset_id}, force={force_reprocess}, "
        f"authorized_datasets={len(authorized_ds_set) if authorized_ds_set else 'all'}"
    )

    # Initialize counters at function start to avoid NameError on early returns
    marked = 0
    nodes_written = 0

    graph_engine = await get_graph_provider()

    # Query episodes from graph using adapter-agnostic approach
    try:
        nodes, _ = await graph_engine.query_by_attributes([{"type": ["Episode"]}])
        if episode_ids:
            ep_id_set = set(episode_ids)
            nodes = [(nid, p) for nid, p in nodes if nid in ep_id_set]
        results = nodes
    except Exception as e:
        logger.error(f"[extract_from_episodic] Query failed: {e}", exc_info=True)
        return {
            "result": [],
            "episodes_analyzed": 0,
            "episodes_marked": 0,
            "nodes_written": 0,
        }

    if not results:
        logger.info("[extract_from_episodic] No episodes to process (query returned empty)")
        return {
            "result": [],
            "episodes_analyzed": 0,
            "episodes_marked": 0,
            "nodes_written": 0,
        }

    logger.info(f"[extract_from_episodic] Found {len(results)} episode candidates")

    # Convert episodes to FragmentDigest for procedural pipeline
    summaries_for_procedural: List[FragmentDigest] = []
    episode_id_list: List[str] = []
    processed_count = 0

    for episode_id, props in results:
        if not episode_id:
            continue

        if not isinstance(props, dict):
            props = {}

        # Get episode's dataset_id from properties
        ep_dataset_id = props.get("dataset_id")

        # Security: Filter by authorized datasets if provided
        if authorized_ds_set:
            if not ep_dataset_id or ep_dataset_id not in authorized_ds_set:
                logger.debug(f"[extract_from_episodic] Skipping {episode_id}: not in authorized datasets")
                continue

        # Filter by specific dataset_id if requested
        if dataset_id:
            if ep_dataset_id != dataset_id:
                continue

        # Skip already-processed episodes unless force_reprocess
        if not force_reprocess and not episode_ids and props.get("procedural_extracted"):
            continue

        summary = props.get("summary", "")
        if not summary:
            continue

        # Create virtual ContentFragment for FragmentDigest.made_from
        virtual_chunk = _create_virtual_chunk_for_extract(str(episode_id), summary)

        digest = FragmentDigest(
            text=summary,
            made_from=virtual_chunk,
            metadata={
                "source_episode_id": str(episode_id),
                "from_episodic_extraction": True,
            },
        )
        summaries_for_procedural.append(digest)
        episode_id_list.append(str(episode_id))

        processed_count += 1
        if processed_count >= limit:
            break

    if not summaries_for_procedural:
        logger.info("[extract_from_episodic] No episodes with content")
        return {
            "result": [],
            "episodes_analyzed": 0,
            "episodes_marked": 0,
            "nodes_written": 0,
        }

    result = await write_procedural_memories(
        summaries_for_procedural,
        **kwargs,
    )

    # Write to graph database (filter out temporary FragmentDigest nodes)
    if result:
        # Filter out FragmentDigest - they are temporary and should not be persisted
        # Only write: Procedure, MemorySpace, ProcedureStepPoint, ProcedureContextPoint
        memory_nodes_to_write = [node for node in result if not isinstance(node, FragmentDigest)]
        if memory_nodes_to_write:
            await persist_memory_nodes(memory_nodes_to_write)
            nodes_written = len(memory_nodes_to_write)
            logger.info(
                f"[extract_from_episodic] Wrote {nodes_written} nodes to graph "
                f"(filtered {len(result) - nodes_written} FragmentDigest)"
            )

    # Skip marking if no nodes were written
    if nodes_written == 0 and not result:
        logger.info("[extract_from_episodic] No procedures created, skipping episode marking")
        return {
            "result": result or [],
            "episodes_analyzed": len(episode_id_list),
            "episodes_marked": 0,
            "nodes_written": 0,
        }

    # Mark episodes as processed via adapter update_node (handles RMW internally)
    if episode_id_list:
        for ep_id in episode_id_list:
            try:
                await graph_engine.update_node(ep_id, {"procedural_extracted": True})
                marked += 1
            except Exception as e:
                logger.debug(f"[extract_from_episodic] Failed to mark episode {ep_id}: {e}")

        logger.info(f"[extract_from_episodic] Marked {marked}/{len(episode_id_list)} episodes as processed")

    return {
        "result": result,
        "episodes_analyzed": len(episode_id_list),
        "episodes_marked": marked,
        "nodes_written": nodes_written,
    }
