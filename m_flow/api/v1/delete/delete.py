"""
Data deletion module.

Provides functionality to delete data from knowledge graph, vector database and relational database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.sql import delete as sql_delete

from m_flow.adapters.graph import get_graph_provider
from m_flow.adapters.relational import get_db_adapter
from m_flow.adapters.vector import get_vector_provider
from m_flow.api.v1.exceptions import (
    DatasetNotFoundError,
    DocumentNotFoundError,
    DocumentSubgraphNotFoundError,
)
from m_flow.auth.methods import get_seed_user
from m_flow.auth.models import User
from m_flow.context_global_variables import set_db_context
from m_flow.core import MemoryNode
from m_flow.data.methods import get_authorized_existing_datasets
from m_flow.data.models import Data, Dataset, DatasetEntry
from m_flow.data.models.graph_relationship_ledger import GraphRelationshipLedger
from m_flow.knowledge.graph_ops.utils.convert_node_to_memory_node import (
    get_all_subclasses,
)
from m_flow.shared.logging_utils import get_logger

_log = get_logger()

# Default vector collection list
_FALLBACK_COLLECTIONS = [
    "Episode_summary",
    "ContentFragment_text",
    "RelationType_relationship_name",
    "Entity_name",
    "Concept_name",  # Backward compat: old data may use this collection
    "TextDocument_name",
    "FragmentDigest_text",
]


def _convert_to_uuid(raw_id: str | UUID) -> Optional[UUID]:
    """Convert ID to UUID format."""
    try:
        if isinstance(raw_id, UUID):
            return raw_id
        normalized = raw_id.replace("-", "")
        return UUID(normalized)
    except Exception as err:
        _log.error("ID conversion failed %s: %s", raw_id, err)
        return None


def _discover_vector_collections() -> list[str]:
    """Dynamically discover vector collection names."""
    all_subclasses = get_all_subclasses(MemoryNode)
    discovered: list[str] = []

    for cls in all_subclasses:
        meta_field = cls.model_fields.get("metadata")
        if meta_field is None:
            continue

        default_meta = meta_field.default
        if not isinstance(default_meta, dict):
            continue

        idx_fields = default_meta.get("index_fields", [])
        for fld in idx_fields:
            discovered.append(f"{cls.__name__}_{fld}")

    return discovered if discovered else _FALLBACK_COLLECTIONS


async def _verify_data_access(
    data_id: UUID,
    dataset_id: UUID,
    user: User,
) -> tuple[Dataset, str]:
    """Verify user's access to data and return dataset and data ID."""
    # Verify dataset permissions
    authorized = await get_authorized_existing_datasets([dataset_id], "delete", user)
    if not authorized:
        raise DatasetNotFoundError(f"Dataset not found or access denied: {dataset_id}")

    target_dataset = authorized[0]

    # Set database context
    await set_db_context(target_dataset.id, target_dataset.owner_id)

    # Verify data existence
    db = get_db_adapter()
    async with db.get_async_session() as sess:
        record = (await sess.execute(select(Data).filter(Data.id == data_id))).scalar_one_or_none()

        if record is None:
            raise DocumentNotFoundError(f"Data not found with ID: {data_id}")

        # Verify data belongs to specified dataset
        link = (
            await sess.execute(
                select(DatasetEntry).filter(
                    DatasetEntry.data_id == data_id,
                    DatasetEntry.dataset_id == dataset_id,
                )
            )
        ).scalar_one_or_none()

        if link is None:
            raise DocumentNotFoundError(f"Data {data_id} not found in dataset {dataset_id}")

        return target_dataset, str(record.id)


async def _purge_from_vector_store(node_ids: list[UUID]) -> None:
    """Delete nodes from vector database."""
    vec_engine = get_vector_provider()
    collections = _discover_vector_collections()
    str_ids = [str(nid) for nid in node_ids]

    for coll_name in collections:
        if await vec_engine.has_collection(coll_name):
            await vec_engine.delete_memory_nodes(coll_name, str_ids)


async def _purge_from_relational(
    data_id: str,
    dataset_id: UUID,
    removed_nodes: list[UUID],
) -> None:
    """Delete related records from relational database."""
    db = get_db_adapter()

    async with db.get_async_session() as sess:
        # Update deletion timestamp in relationship ledger
        ledger_update = (
            update(GraphRelationshipLedger)
            .where(
                or_(
                    GraphRelationshipLedger.source_node_id.in_(removed_nodes),
                    GraphRelationshipLedger.destination_node_id.in_(removed_nodes),
                )
            )
            .values(deleted_at=datetime.now())
        )
        await sess.execute(ledger_update)

        # Get data record
        data_record = (await sess.execute(select(Data).filter(Data.id == UUID(data_id)))).scalar_one_or_none()

        if data_record is None:
            raise DocumentNotFoundError(f"Document not found in relational DB: {data_id}")

        doc_uuid = data_record.id

        # Verify dataset exists
        ds = (await sess.execute(select(Dataset).filter(Dataset.id == dataset_id))).scalar_one_or_none()

        if ds is None:
            raise DatasetNotFoundError(f"Dataset not found: {dataset_id}")

        # Delete dataset-data association
        await sess.execute(
            sql_delete(DatasetEntry).where(
                DatasetEntry.data_id == doc_uuid,
                DatasetEntry.dataset_id == ds.id,
            )
        )

        # Check if data is still in other datasets
        other_links = (
            await sess.execute(select(DatasetEntry).filter(DatasetEntry.data_id == doc_uuid))
        ).scalar_one_or_none()

        # If no other datasets reference it, delete the data record
        if other_links is None:
            await sess.execute(sql_delete(Data).where(Data.id == doc_uuid))

        await sess.commit()


async def _remove_subgraph(doc_id: str, deletion_mode: str) -> dict:
    """Delete document subgraph."""
    graph = await get_graph_provider()
    sub = await graph.get_document_subgraph(doc_id)

    if not sub:
        raise DocumentSubgraphNotFoundError(f"Document not found: {doc_id}")

    # Define deletion order to maintain graph integrity
    removal_sequence = [
        ("orphan_entities", "orphaned entities"),
        ("orphan_types", "orphaned entity types"),
        ("made_from_nodes", "made_from nodes"),
        ("chunks", "document chunks"),
        ("document", "document"),
    ]

    counts: dict[str, int] = {}
    purged_ids: list[str] = []

    for key, desc in removal_sequence:
        items = sub.get(key, [])
        if not items:
            continue

        for item in items:
            nid = item["id"]
            await graph.delete_node(nid)
            purged_ids.append(nid)

        counts[desc] = len(items)

    # Hard delete mode: clean up orphan nodes
    if deletion_mode == "hard":
        # Clean up degree-one concept nodes
        lone_concepts = await graph.get_degree_one_nodes("Entity")
        for node in lone_concepts:
            await graph.delete_node(node["id"])
            purged_ids.append(node["id"])
            counts["degree_one_entities"] = counts.get("degree_one_entities", 0) + 1

        # Clean up degree-one concept type nodes
        lone_types = await graph.get_degree_one_nodes("EntityType")
        for node in lone_types:
            await graph.delete_node(node["id"])
            purged_ids.append(node["id"])
            counts["degree_one_types"] = counts.get("degree_one_types", 0) + 1

    return {
        "status": "success",
        "deleted_counts": counts,
        "document_id": doc_id,
        "deleted_node_ids": purged_ids,
    }


async def _cleanup_orphan_episodic_nodes(graph) -> list[str]:
    """Remove Episode/Facet/FacetPoint nodes that lost all source evidence.

    After phase-1 deletion removes ContentFragment nodes, DETACH DELETE
    automatically severs ``includes_chunk`` and ``supported_by`` edges.
    This function finds nodes that became orphans as a result and removes
    them together with their vector-store entries.

    Provider-agnostic: uses only ``get_graph_data`` / ``delete_node``
    from the abstract ``GraphProvider`` interface — works for Kuzu, Neo4j,
    Neptune, and any future backend.

    Returns list of deleted node IDs (strings).
    """
    purged: list[str] = []

    nodes, edges = await graph.get_graph_data()

    # Build outgoing edge index: node_id → [(relationship_name, target_id)]
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for src, tgt, rel, _ in edges:
        outgoing.setdefault(src, []).append((rel, tgt))

    node_type_map = {nid: props.get("type", "") for nid, props in nodes}

    # Step 1: Facets with no remaining supported_by edges → delete FacetPoints first
    for nid, props in nodes:
        if props.get("type") != "Facet":
            continue
        if any(r == "supported_by" for r, _ in outgoing.get(nid, [])):
            continue

        # Delete child FacetPoints
        for rel, tgt in outgoing.get(nid, []):
            if rel == "has_point" and node_type_map.get(tgt) == "FacetPoint":
                await graph.delete_node(tgt)
                purged.append(tgt)

        await graph.delete_node(nid)
        purged.append(nid)

    # Step 2: Episodes with no remaining includes_chunk edges
    for nid, props in nodes:
        if props.get("type") != "Episode":
            continue
        if any(r == "includes_chunk" for r, _ in outgoing.get(nid, [])):
            continue

        await graph.delete_node(nid)
        purged.append(nid)

    if purged:
        _log.info("Cleaned %d orphan episodic nodes (Facet/FacetPoint/Episode)", len(purged))

    return purged


async def _cleanup_isolated_nodes(graph) -> list[str]:
    """Remove completely disconnected nodes (degree 0).

    After document and episodic cleanup, system-level nodes such as
    MemorySpace, RelationType, or EntityType may lose all edges and
    become isolated.  This phase garbage-collects them.

    Provider-agnostic: re-reads the graph after episodic cleanup so that
    newly-isolated nodes are detected regardless of backend.

    Returns list of deleted node IDs (strings).
    """
    purged: list[str] = []

    nodes, edges = await graph.get_graph_data()

    connected: set[str] = set()
    for src, tgt, _, _ in edges:
        connected.add(src)
        connected.add(tgt)

    for nid, _ in nodes:
        if nid not in connected:
            await graph.delete_node(nid)
            purged.append(nid)

    if purged:
        _log.info("Cleaned %d isolated nodes (MemorySpace, RelationType, etc.)", len(purged))

    return purged


async def delete_single_document(
    data_id: str,
    dataset_id: UUID = None,
    mode: str = "soft",
) -> dict:
    """Delete a single document and its associated data."""
    # Phase 1: Delete ingestion-layer nodes (Document, Chunk, Digest, orphan Entity)
    graph_result = await _remove_subgraph(data_id, mode)
    _log.info("Graph deletion result: %s", graph_result)

    # Convert node IDs to UUID
    uuid_list: list[UUID] = []
    for raw_id in graph_result["deleted_node_ids"]:
        converted = _convert_to_uuid(raw_id)
        if converted is not None:
            uuid_list.append(converted)

    # Phase 2: Clean orphan episodic-layer nodes (Episode, Facet, FacetPoint)
    graph = await get_graph_provider()
    orphan_ids = await _cleanup_orphan_episodic_nodes(graph)
    for oid in orphan_ids:
        converted = _convert_to_uuid(oid)
        if converted is not None:
            uuid_list.append(converted)

    # Phase 3: Clean completely isolated nodes (MemorySpace, RelationType, etc.)
    isolated_ids = await _cleanup_isolated_nodes(graph)
    for oid in isolated_ids:
        converted = _convert_to_uuid(oid)
        if converted is not None:
            uuid_list.append(converted)

    # Delete from vector database (covers all node types via dynamic collection discovery)
    await _purge_from_vector_store(uuid_list)

    # Delete from relational database
    await _purge_from_relational(data_id, dataset_id, uuid_list)

    return {
        "status": "success",
        "message": "Document deleted from both graph and relational databases",
        "graph_deletions": graph_result["deleted_counts"],
        "data_id": data_id,
        "dataset": dataset_id,
        "deleted_node_ids": [str(uid) for uid in uuid_list],
    }


async def delete_document_subgraph(document_id: str, mode: str = "soft") -> dict:
    """Public interface for deleting document subgraph."""
    return await _remove_subgraph(document_id, mode)


async def delete(
    data_id: UUID,
    dataset_id: UUID,
    mode: str = "soft",
    user: User = None,
) -> dict:
    """
    Delete data from specified dataset.

    Args:
        data_id: UUID of data to delete.
        dataset_id: UUID of dataset containing the data.
        mode: ``"soft"`` (default) removes data and edges only; ``"hard"`` additionally
            prunes entity nodes that become orphans (no remaining connections).
        user: User performing the operation, uses default user if None.

    Returns:
        Dict containing deletion results.

    Raises:
        DocumentNotFoundError: Data not found.
        DatasetNotFoundError: Dataset not found or access denied.
    """
    if user is None:
        user = await get_seed_user()

    target_dataset, str_data_id = await _verify_data_access(data_id, dataset_id, user)

    return await delete_single_document(str_data_id, target_dataset.id, mode)
