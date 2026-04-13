"""
Abstract interface for graph database adapters.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Type, TYPE_CHECKING, Union
from uuid import NAMESPACE_OID, UUID, uuid5

from m_flow.shared.logging_utils import get_logger

if TYPE_CHECKING:
    from m_flow.core import MemoryNode

_log = get_logger()

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

NodeProps = Dict[str, Any]
EdgeTuple = Tuple[str, str, str, Dict[str, Any]]  # (src_id, dst_id, rel, props) — used by get_graph_data / add_edges
NodeTuple = Tuple[str, NodeProps]  # (node_id, props)
EdgeTriple = Tuple[NodeProps, str, NodeProps]  # (src_props, rel_name, dst_props) — used by get_edges

# ---------------------------------------------------------------------------
# Decorator for ledger tracking
# ---------------------------------------------------------------------------


def _track_changes(fn):
    """Record node/edge mutations in the relationship ledger."""

    @wraps(fn)
    async def _inner(self, *args, **kwargs):
        # Lazy imports to avoid circular dependency
        from m_flow.adapters.relational.get_db_adapter import get_db_adapter
        from m_flow.data.models.graph_relationship_ledger import GraphRelationshipLedger

        db = get_db_adapter()

        # Determine caller context
        caller = _resolve_caller()

        result = await fn(self, *args, **kwargs)

        async with db.get_async_session() as session:
            ledger_rows = []
            now_ts = datetime.now(timezone.utc).timestamp()

            if fn.__name__ == "add_nodes":
                for node in args[0]:
                    nid = UUID(str(node.id))
                    ledger_rows.append(
                        GraphRelationshipLedger(
                            id=uuid5(NAMESPACE_OID, f"{now_ts}:{node.id}"),
                            source_node_id=nid,
                            destination_node_id=nid,
                            creator_function=f"{caller}.node",
                            node_label=getattr(node, "name", None) or str(node.id),
                        )
                    )
            elif fn.__name__ == "add_edges":
                for edge in args[0]:
                    src = UUID(str(edge[0]))
                    dst = UUID(str(edge[1]))
                    rel = str(edge[2])
                    ledger_rows.append(
                        GraphRelationshipLedger(
                            id=uuid5(NAMESPACE_OID, f"{now_ts}:{edge[0]}:{edge[1]}:{rel}"),
                            source_node_id=src,
                            destination_node_id=dst,
                            creator_function=f"{caller}.{rel}",
                        )
                    )

            if ledger_rows:
                try:
                    session.add_all(ledger_rows)
                    await session.flush()
                except Exception as exc:
                    _log.debug("Ledger insert error: %s", exc)
                    await session.rollback()

            try:
                await session.commit()
            except Exception as exc:
                _log.debug("Session commit error: %s", exc)

        return result

    return _inner


def _resolve_caller() -> str:
    """Walk the call stack to find the first non-wrapper caller."""
    frame = inspect.currentframe()
    while frame:
        parent = frame.f_back
        if parent and parent.f_code.co_name not in ("_inner", "_track_changes"):
            loc = parent.f_locals
            cls = loc.get("self")
            cls_name = cls.__class__.__name__ if cls else None
            fn_name = parent.f_code.co_name
            return f"{cls_name}.{fn_name}" if cls_name else fn_name
        frame = parent
    return "unknown"


# Alias
record_graph_changes = _track_changes

# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class GraphProvider(ABC):
    """
    Contract for graph storage backends (Neo4j, Kuzu, FalkorDB, Neptune, etc.).
    """

    # -------------------------------------------------------------------------
    # Core query
    # -------------------------------------------------------------------------

    @abstractmethod
    async def query(self, cypher: str, params: Dict[str, Any]) -> List[Any]:
        """Execute a raw Cypher/Gremlin query."""
        ...

    @abstractmethod
    async def is_empty(self) -> bool:
        """Return True if the graph has no nodes."""
        ...

    # -------------------------------------------------------------------------
    # Node CRUD
    # -------------------------------------------------------------------------

    @abstractmethod
    async def add_node(
        self,
        node: Union["MemoryNode", str],
        props: Optional[NodeProps] = None,
    ) -> None:
        """Insert a single node."""
        ...

    @abstractmethod
    @_track_changes
    async def add_nodes(self, nodes: List[Any]) -> None:
        """Bulk insert nodes."""
        ...

    @abstractmethod
    async def has_node(self, node_id: str) -> bool:
        """Check if a node exists by ID."""
        ...

    @abstractmethod
    async def get_node(self, node_id: str) -> Optional[NodeProps]:
        """Fetch a node by ID."""
        ...

    @abstractmethod
    async def get_nodes(self, ids: List[str]) -> List[NodeProps]:
        """Fetch multiple nodes."""
        ...

    @abstractmethod
    async def delete_node(self, node_id: str) -> None:
        """Remove a single node."""
        ...

    @abstractmethod
    async def delete_nodes(self, ids: List[str]) -> None:
        """Remove multiple nodes."""
        ...

    # -------------------------------------------------------------------------
    # Edge CRUD
    # -------------------------------------------------------------------------

    @abstractmethod
    async def add_edge(
        self,
        src: str,
        dst: str,
        rel: str,
        props: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create a relationship between two nodes."""
        ...

    @abstractmethod
    @_track_changes
    async def add_edges(self, edges: List[EdgeTuple]) -> None:
        """Bulk create relationships."""
        ...

    @abstractmethod
    async def has_edge(self, src: str, dst: str, rel: str) -> bool:
        """Check if a relationship exists."""
        ...

    @abstractmethod
    async def has_edges(self, edges: List[EdgeTuple]) -> List[EdgeTuple]:
        """Filter to existing relationships."""
        ...

    @abstractmethod
    async def get_edges(self, node_id: str) -> List[EdgeTriple]:
        """Get all edges connected to a node.

        Returns:
            List of ``(source_node_props, relationship_name, target_node_props)``.
            Both source and target are property dicts with at least
            ``id``, ``type``, ``name`` keys.

        .. note::

           This returns :pydata:`EdgeTriple` (3-tuple with full node dicts),
           **not** :pydata:`EdgeTuple` (4-tuple with string IDs used by
           :meth:`get_graph_data` and :meth:`add_edges`).
        """
        ...

    # -------------------------------------------------------------------------
    # Graph-level operations
    # -------------------------------------------------------------------------

    @abstractmethod
    async def delete_graph(self) -> None:
        """Wipe the entire graph."""
        ...

    @abstractmethod
    async def get_graph_data(self) -> Tuple[List[NodeTuple], List[EdgeTuple]]:
        """Return all nodes and edges."""
        ...

    @abstractmethod
    async def get_graph_metrics(self, extended: bool = False) -> Dict[str, Any]:
        """Return graph statistics."""
        ...

    @abstractmethod
    async def query_by_attributes(
        self,
        attribute_filters: List[Dict[str, List[Union[str, int]]]],
    ) -> Tuple[List[NodeTuple], List[EdgeTuple]]:
        """Return nodes/edges matching attribute filters."""
        ...

    # -------------------------------------------------------------------------
    # Traversal helpers
    # -------------------------------------------------------------------------

    @abstractmethod
    async def get_neighbors(self, node_id: str) -> List[NodeProps]:
        """Get directly connected nodes."""
        ...

    @abstractmethod
    async def get_triplets(
        self,
        node_id: Union[str, UUID],
    ) -> List[Tuple[NodeProps, Dict[str, Any], NodeProps]]:
        """Get triples: (source_props, edge_props, target_props)."""
        ...

    @abstractmethod
    async def extract_typed_subgraph(
        self,
        node_type: Type[Any],
        names: List[str],
    ) -> Tuple[List[Tuple[int, dict]], List[Tuple[int, int, str, dict]]]:
        """Extract a subgraph for a set of named nodes."""
        ...

    # -------------------------------------------------------------------------
    # Extended CRUD (default implementations; adapters should override)
    # -------------------------------------------------------------------------

    async def update_node(self, node_id: str, props: Dict[str, Any]) -> None:
        """Merge *props* into an existing node's properties.

        The semantics are **merge** (patch), not replace: keys present in
        *props* overwrite existing values; keys absent from *props* are
        retained.

        Default implementation: read-modify-write via :meth:`get_node` +
        :meth:`query`.  Adapters should override with a native single-query
        implementation for atomicity and performance.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement update_node; "
            "use query() with backend-specific Cypher as a workaround."
        )

    async def delete_edge(self, src: str, dst: str, rel: str) -> None:
        """Remove a specific directed edge between two nodes.

        Default: not implemented.  Adapters should override.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement delete_edge; "
            "use query() with backend-specific Cypher as a workaround."
        )

    async def get_document_subgraph(self, data_id: str) -> Optional[Dict[str, list]]:
        """Return the subgraph rooted at a document node for deletion.

        Expected keys in the returned dict: ``document``, ``chunks``,
        ``orphan_entities``, ``made_from_nodes``, ``orphan_types``.
        Returns ``None`` when the document is not found.

        Default: not implemented.  All current adapters (Kuzu, Neo4j,
        Neptune) already provide this method.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement get_document_subgraph.")

    # -------------------------------------------------------------------------
    # Persistence / WAL management (optional)
    # -------------------------------------------------------------------------

    async def checkpoint(self) -> None:
        """
        Force durability checkpoint for databases using Write-Ahead Logging (WAL).

        Some embedded databases (e.g., Kuzu, SQLite in WAL mode) buffer writes
        in a WAL file before persisting to the main database. This method forces
        a checkpoint to ensure all data is durably stored.

        This is a no-op by default for backends that don't require explicit
        checkpointing (e.g., Neo4j, cloud-hosted graph databases).

        Should be called after critical write operations (e.g., after memorize)
        to prevent data loss on abnormal shutdown.
        """
        pass  # Default: no-op for databases that auto-checkpoint
