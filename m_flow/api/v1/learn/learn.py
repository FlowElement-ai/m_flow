# m_flow/api/v1/learn/learn.py
"""
Learn Operation - Extract Procedural Memory from existing Episodes.

Workflow:
1. Fetch Episode nodes and associated Facets from graph database
2. Reorganize Episode content into FragmentDigest format
3. Call write_procedural_memories to extract methods/steps
4. Write to graph database
5. Create Episode -[derived_procedure]-> Procedure edges
"""

from __future__ import annotations

from typing import Union, Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime

from m_flow.shared.logging_utils import get_logger
from m_flow.auth.models import User
from m_flow.pipeline import execute_workflow
from m_flow.pipeline.tasks import Stage

from m_flow.adapters.graph import get_graph_provider
from m_flow.memory.procedural import write_procedural_memories
from m_flow.storage import persist_memory_nodes
from m_flow.knowledge.summarization.models import FragmentDigest

# Models
from m_flow.core.domain.models import Episode
from m_flow.ingestion.chunking.models import ContentFragment
from m_flow.data.processing.document_types import Document

logger = get_logger("learn")


def _create_virtual_chunk_for_episode(episode: Episode) -> ContentFragment:
    """
    Create virtual ContentFragment for Episode so FragmentDigest.made_from always has a value.

    This is an adapter pattern that adapts Episode data to the format expected by existing system.

    Benefits:
    - No need to modify existing code (13 places accessing .made_from remain unaffected)
    - Maintains backward compatibility
    - Source can be identified via metadata.source_type == "episode"
    """
    # Create virtual Document object
    virtual_doc = Document(
        id=episode.id,
        name=f"[Episode] {episode.name or 'Unnamed'}",
        processed_path="memory://episode",
        external_metadata=None,
        mime_type="text/episode",
    )

    episode_text = episode.summary or ""

    # Create virtual ContentFragment
    return ContentFragment(
        id=episode.id,
        text=episode_text,
        chunk_size=len(episode_text),
        chunk_index=0,
        cut_type="episode",
        is_part_of=virtual_doc,
        contains=[],  # Empty list to satisfy Pydantic validation
        metadata={"source_type": "episode", "episode_id": str(episode.id)},
    )


async def _filter_virtual_nodes(items: List[Any]) -> List[Any]:
    """
    Filter out virtual FragmentDigest nodes that should not be persisted to the database.

    This is a pipeline task that filters FragmentDigest objects created from Episode data.
    These virtual nodes are only used to pass data between pipeline stages, not for storage.

    Filter criteria:
    - Only filters FragmentDigest type objects
    - Checks made_from.metadata.source_type == "episode"
    - Preserves all other node types (Procedure, MemorySpace, Points, etc.)

    Args:
        items: List of MemoryNode objects from write_procedural_memories

    Returns:
        Filtered list without virtual FragmentDigest nodes
    """
    # FragmentDigest is already imported at module level (line 27)
    result = []
    filtered_count = 0

    for item in items:
        if isinstance(item, FragmentDigest):
            # Safely access nested attributes
            made_from = getattr(item, "made_from", None)
            if made_from is not None:
                metadata = getattr(made_from, "metadata", None)
                if metadata is not None and isinstance(metadata, dict):
                    source_type = metadata.get("source_type")
                    if source_type == "episode":
                        filtered_count += 1
                        continue  # Skip virtual node
        result.append(item)

    if filtered_count > 0:
        logger.info(f"[learn] Filtered {filtered_count} virtual FragmentDigest nodes")

    return result


async def fetch_episodes_from_graph(
    datasets: Union[str, list[str], list[UUID]] = None,
    episode_ids: Optional[List[UUID]] = None,
    user: User = None,
    limit: int = 1000,
) -> List[Episode]:
    """
    Fetch Episode nodes from graph database.

    Query logic:
    - If episode_ids specified, only fetch those Episodes
    - Otherwise fetch all unprocessed Episodes (without derived_procedure edges)
    - Skip Episodes already linked to Procedures (prevent duplicates)

    Args:
        datasets: Dataset name or ID (currently unused, reserved for interface)
        episode_ids: Optional, list of Episode IDs to process
        user: User context
        limit: Maximum number to return

    Returns:
        List of Episode objects
    """
    graph_engine = await get_graph_provider()

    episodes: List[Episode] = []

    try:
        if episode_ids:
            # Fetch specified Episodes using adapter-agnostic get_node
            for ep_id in episode_ids:
                try:
                    node_data = await graph_engine.get_node(str(ep_id))
                    if node_data and node_data.get("type") == "Episode":
                        # Extract dataset_id from node properties if available
                        props = node_data.get("properties", {})
                        if isinstance(props, str):
                            import json as _json

                            try:
                                props = _json.loads(props)
                            except (ValueError, TypeError):
                                props = {}
                        ds_id = node_data.get("dataset_id") or props.get("dataset_id")

                        episode = Episode(
                            id=UUID(node_data.get("id")),
                            name=node_data.get("name", ""),
                            summary=node_data.get("summary", ""),
                            dataset_id=ds_id,
                        )
                        episodes.append(episode)
                except Exception as e:
                    logger.warning(f"[learn] Failed to fetch episode {ep_id}: {e}")
        else:
            # Query all Episode nodes that haven't been processed yet
            try:
                nodes, _ = await graph_engine.query_by_attributes([{"type": ["Episode"]}])

                for node_id, props in nodes:
                    if not node_id:
                        continue

                    # Parse properties if stored as JSON string
                    import json as _json

                    if isinstance(props, str):
                        try:
                            props = _json.loads(props)
                        except (ValueError, TypeError):
                            props = {}
                    elif not isinstance(props, dict):
                        props = {}

                    name = props.get("name", "")
                    summary = props.get("summary", "")

                    # Check if already has derived_procedure edge
                    # get_edges returns List[Tuple[Dict, str, Dict]]
                    try:
                        edges = await graph_engine.get_edges(str(node_id))
                        has_derived = any(rel == "derived_procedure" for _src, rel, _dst in edges)
                    except Exception as e:
                        logger.debug("Failed to check edges for node %s: %s", node_id, e)
                        has_derived = False

                    if not has_derived:
                        # Extract dataset_id from properties for data consistency
                        ds_id = props.get("dataset_id")

                        episode = Episode(
                            id=UUID(str(node_id)),
                            name=name or "",
                            summary=summary or "",
                            dataset_id=ds_id,
                        )
                        episodes.append(episode)

                        if len(episodes) >= limit:
                            break

            except Exception as e:
                logger.warning(f"[learn] Episode query failed: {e}")

        logger.info(f"[learn] Found {len(episodes)} episodes to process")
        return episodes

    except Exception as e:
        logger.error(f"[learn] Failed to fetch episodes: {e}")
        return []


async def episodes_to_summaries(episodes: List[Episode]) -> List[FragmentDigest]:
    """
    Convert Episodes to format acceptable by write_procedural_memories.

    Episode contains:
    - summary: Main content
    - facets: Associated details

    Merge into a complete text for Procedural extraction.
    """
    graph_engine = await get_graph_provider()
    summaries: List[FragmentDigest] = []

    for episode in episodes:
        try:
            # Get Facets associated with Episode
            # get_edges returns List[Tuple[Dict, str, Dict]]
            edges = await graph_engine.get_edges(str(episode.id))

            # Extract Facet info directly from edge target nodes
            facet_texts: List[str] = []
            for _src, rel, dst in edges:
                if rel != "has_facet":
                    continue
                if not isinstance(dst, dict):
                    continue
                if dst.get("type") != "Facet":
                    continue

                st = dst.get("search_text") or dst.get("name") or ""
                desc = dst.get("description") or ""
                if st:
                    facet_texts.append(f"- {st}" + (f": {desc}" if desc else ""))

            # Merge Episode.summary + Facet content
            full_text = episode.summary or ""
            if facet_texts:
                full_text += "\n\nKey details:\n" + "\n".join(facet_texts)

            # Use virtual ContentFragment
            virtual_chunk = _create_virtual_chunk_for_episode(episode)

            # Create FragmentDigest
            summary = FragmentDigest(
                id=uuid4(),
                text=full_text.strip(),
                made_from=virtual_chunk,  # Use virtual chunk
                overall_topic=episode.name,
                metadata={
                    "index_fields": ["text"],
                    "source_episode_id": str(episode.id),  # For tracing
                    "source_episode_name": episode.name,
                },
            )
            summaries.append(summary)

        except Exception as e:
            logger.error(f"[learn] Failed to process episode {episode.id}: {e}")
            continue

    logger.info(f"[learn] Created {len(summaries)} summaries from {len(episodes)} episodes")
    return summaries


async def _create_derived_procedure_edges(
    episodes: List[Episode],
    result: List[Any],
    graph_engine,
) -> int:
    """
    Create derived_procedure edges for each processed Episode.

    Trace Episodes through Procedure.source_refs.

    Returns:
        Number of edges created
    """
    from m_flow.core.domain.models import Procedure

    # Extract Procedures from result
    procedures = [dp for dp in result if isinstance(dp, Procedure)]

    if not procedures:
        logger.info("[learn] No procedures generated, skipping edge creation")
        return 0

    edges_created = 0

    for episode in episodes:
        episode_id = str(episode.id)

        # Find Procedures that reference this Episode
        for proc in procedures:
            source_refs = proc.source_refs or []
            if f"episode:{episode_id}" in source_refs:
                try:
                    # Create derived_procedure edge
                    # Use positional args to be adapter-agnostic
                    # Interface: add_edge(src, dst, rel, props)
                    await graph_engine.add_edge(
                        episode_id,
                        str(proc.id),
                        "derived_procedure",
                        {
                            "edge_text": f"Episode '{episode.name}' derived into Procedure '{proc.name}'",
                            "created_at": datetime.utcnow().isoformat(),
                        },
                    )
                    edges_created += 1
                    logger.debug(f"[learn] Created derived_procedure edge: {episode.name} -> {proc.name}")
                except Exception as e:
                    logger.warning(f"[learn] Failed to create edge for {episode.name}: {e}")

    logger.info(f"[learn] Created {edges_created} derived_procedure edges")
    return edges_created


async def learn(
    datasets: Union[str, list[str], list[UUID]] = None,
    user: User = None,
    episode_ids: Optional[List[UUID]] = None,
    run_in_background: bool = False,
) -> Dict[str, Any]:
    """
    Extract Procedural Memory from existing memories.

    Workflow:
    1. Fetch Episode nodes and associated Facets from graph
    2. Reorganize Episode content into FragmentDigest format
    3. Call write_procedural_memories to extract methods/steps
    4. Write to graph database
    5. Create derived_procedure edges (prevent duplicate processing)

    Args:
        datasets: Dataset name or ID (currently reserved for interface, may be used for filtering in future)
        user: User context
        episode_ids: Optional, list of Episode IDs to process
        run_in_background: Whether to execute in background

    Returns:
        Dictionary containing processing results
    """
    logger.info("[learn] Starting learn operation")

    # Step 1: Fetch existing Episodes
    episodes = await fetch_episodes_from_graph(
        datasets=datasets,
        episode_ids=episode_ids,
        user=user,
    )

    if not episodes:
        logger.info("[learn] No episodes found to process")
        return {
            "status": "completed",
            "episodes_processed": 0,
            "procedures_created": 0,
            "message": "No episodes found to process",
        }

    logger.info(f"[learn] Found {len(episodes)} episodes to process")

    # Step 2: Convert to FragmentDigest format
    summaries = await episodes_to_summaries(episodes)

    if not summaries:
        logger.warning("[learn] Failed to create summaries from episodes")
        return {
            "status": "completed",
            "episodes_processed": len(episodes),
            "procedures_created": 0,
            "message": "No summaries created from episodes",
        }

    # Step 3: Build pipeline
    # Note: _filter_virtual_nodes removes virtual FragmentDigest nodes
    # that were created from Episode data and should not be persisted
    tasks = [
        Stage(write_procedural_memories, task_config={"batch_size": 10}),
        Stage(_filter_virtual_nodes),  # Filter out virtual FragmentDigest before storage
        Stage(persist_memory_nodes, task_config={"batch_size": 10}),
    ]

    # Step 4: Execute pipeline
    # Note: run_in_background is not yet supported for learn() because
    # we need to wait for pipeline completion to create derived_procedure edges.
    # This will be addressed in a future refactoring.
    if run_in_background:
        logger.warning(
            "[learn] run_in_background=True is not yet supported for learn(). The operation will run synchronously."
        )

    try:
        result_items: List[Any] = []

        async for result in execute_workflow(
            tasks=tasks,
            data=summaries,
            user=user,
            name="learn_pipeline",
        ):
            if hasattr(result, "payload"):
                result_items.extend(result.payload if isinstance(result.payload, list) else [result.payload])

        # Step 5: Create derived_procedure edges
        graph_engine = await get_graph_provider()
        edges_created = await _create_derived_procedure_edges(
            episodes=episodes,
            result=result_items,
            graph_engine=graph_engine,
        )

        # Count results
        from m_flow.core.domain.models import Procedure

        procedures_created = len([r for r in result_items if isinstance(r, Procedure)])

        logger.info(
            f"[learn] Completed: {len(episodes)} episodes -> {procedures_created} procedures, {edges_created} edges"
        )

        return {
            "status": "completed",
            "episodes_processed": len(episodes),
            "procedures_created": procedures_created,
            "edges_created": edges_created,
            "message": f"Successfully processed {len(episodes)} episodes",
        }

    except Exception as e:
        logger.error(f"[learn] Pipeline execution failed: {e}")
        return {
            "status": "error",
            "episodes_processed": len(episodes),
            "procedures_created": 0,
            "error": str(e),
        }
