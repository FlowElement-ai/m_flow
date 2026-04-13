"""
Neo4j graph database adapter implementation.

Provides async interface for graph operations using the Neo4j Python driver.
Supports node/edge CRUD, graph projections, and metrics via the GDS library.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type
from uuid import UUID

from neo4j import AsyncGraphDatabase, AsyncSession
from neo4j.exceptions import Neo4jError

from m_flow.adapters.graph.graph_db_interface import (
    GraphProvider,
    record_graph_changes,
)
from m_flow.core import MemoryNode
from m_flow.shared.logging_utils import get_logger
from m_flow.storage.utils_mod.utils import JSONEncoder

from mflow_workers.tasks.queued_add_edges import queued_add_edges
from mflow_workers.tasks.queued_add_nodes import queued_add_nodes
from mflow_workers.utils import override_distributed

from .deadlock_retry import deadlock_retry
from .neo4j_metrics_utils import (
    count_self_loops,
    get_avg_clustering,
    get_edge_density,
    get_num_connected_components,
    get_shortest_path_lengths,
    get_size_of_connected_components,
)

if TYPE_CHECKING:
    pass

_log = get_logger("Neo4jDB")

# Base label applied to all graph nodes for consistent querying
_BASE_NODE_LABEL = "__Node__"


def _encode_props_for_storage(raw_props: dict) -> dict:
    """
    Transform property values to Neo4j-compatible formats.

    UUIDs become strings, nested dicts become JSON strings.
    """
    encoded = {}
    for key, val in raw_props.items():
        if isinstance(val, UUID):
            encoded[key] = str(val)
        elif isinstance(val, dict):
            encoded[key] = json.dumps(val, cls=JSONEncoder)
        else:
            encoded[key] = val
    return encoded


def _flatten_edge_props(props: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten nested structures for Neo4j edge properties.

    Neo4j doesn't support nested dicts/lists directly. This flattens
    'weights' dicts into individual prefixed properties.
    """
    flat = {}
    for key, val in props.items():
        if key == "weights" and isinstance(val, dict):
            for wname, wval in val.items():
                flat[f"weight_{wname}"] = wval
        elif isinstance(val, dict):
            flat[f"{key}_json"] = json.dumps(val, cls=JSONEncoder)
        elif isinstance(val, list):
            flat[f"{key}_json"] = json.dumps(val, cls=JSONEncoder)
        else:
            flat[key] = val
    return flat


class Neo4jAdapter(GraphProvider):
    """
    Async Neo4j graph database adapter.

    Implements GraphProvider for Neo4j, providing CRUD operations
    on nodes and edges, graph projections via GDS, and metric calculations.
    """

    def __init__(
        self,
        graph_database_url: str,
        graph_database_username: Optional[str] = None,
        graph_database_password: Optional[str] = None,
        graph_database_name: Optional[str] = None,
        driver: Optional[Any] = None,
    ):
        """
        Create a Neo4j adapter instance.

        Args:
            graph_database_url: Neo4j bolt URL
            graph_database_username: Auth username (optional)
            graph_database_password: Auth password (optional)
            graph_database_name: Target database name
            driver: Pre-configured driver (optional, for testing)
        """
        auth_tuple = self._build_auth(graph_database_username, graph_database_password)

        self._db_name = graph_database_name
        self._driver = driver or AsyncGraphDatabase.driver(
            graph_database_url,
            auth=auth_tuple,
            max_connection_lifetime=120,
            notifications_min_severity="OFF",
            keep_alive=True,
        )

    @property
    def graph_database_name(self) -> Optional[str]:
        return self._db_name

    @property
    def driver(self):
        return self._driver

    def _build_auth(self, username: Optional[str], password: Optional[str]) -> Optional[tuple]:
        """Construct auth tuple if credentials are complete."""
        if username and password:
            return (username, password)
        if username or password:
            _log.warning("Incomplete Neo4j credentials - using anonymous auth")
        return None

    async def initialize(self) -> None:
        """Create uniqueness constraint on node IDs."""
        constraint_cypher = f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{_BASE_NODE_LABEL}`) REQUIRE n.id IS UNIQUE;"
        await self.query(constraint_cypher)

    @asynccontextmanager
    async def get_session(self) -> AsyncSession:
        """Acquire a database session."""
        async with self._driver.session(database=self._db_name) as sess:
            yield sess

    async def is_empty(self) -> bool:
        """Check if the database contains any nodes."""
        check_cypher = "RETURN EXISTS { MATCH (n) } AS has_nodes;"
        result = await self.query(check_cypher)
        return not result[0]["has_nodes"] if result else True

    @deadlock_retry()
    async def query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Execute Cypher query with automatic deadlock retry.

        Args:
            query: Cypher query string
            params: Query parameters

        Returns:
            List of result records as dicts
        """
        try:
            async with self.get_session() as sess:
                cursor = await sess.run(query, parameters=params)
                return await cursor.data()
        except Neo4jError as err:
            _log.error("Cypher execution error: %s", err, exc_info=True)
            raise

    # =========================================================================
    # Node Operations
    # =========================================================================

    async def has_node(self, node_id: str) -> bool:
        """Check if node with given ID exists."""
        cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}`)
        WHERE n.id = $nid
        RETURN COUNT(n) > 0 AS exists
        """
        result = await self.query(cypher, {"nid": node_id})
        return result[0]["exists"] if result else False

    async def add_node(self, node: MemoryNode):
        """Insert or update a single node."""
        props = _encode_props_for_storage(node.model_dump())
        label = type(node).__name__

        cypher = dedent(f"""
            MERGE (n:`{_BASE_NODE_LABEL}` {{id: $nid}})
            ON CREATE SET n += $props, n.updated_at = timestamp()
            ON MATCH SET n += $props, n.updated_at = timestamp()
            WITH n, $label AS lbl
            CALL apoc.create.addLabels(n, [lbl]) YIELD node AS labeled
            RETURN ID(labeled) AS internal_id, labeled.id AS node_id
        """)

        return await self.query(
            cypher,
            {
                "nid": str(node.id),
                "label": label,
                "props": props,
            },
        )

    @record_graph_changes
    @override_distributed(queued_add_nodes)
    async def add_nodes(self, nodes: list[MemoryNode]) -> None:
        """Bulk insert/update multiple nodes."""
        if not nodes:
            return None

        cypher = f"""
        UNWIND $items AS item
        MERGE (n:`{_BASE_NODE_LABEL}` {{id: item.nid}})
        ON CREATE SET n += item.props, n.updated_at = timestamp()
        ON MATCH SET n += item.props, n.updated_at = timestamp()
        WITH n, item.label AS lbl
        CALL apoc.create.addLabels(n, [lbl]) YIELD node AS labeled
        RETURN ID(labeled) AS internal_id, labeled.id AS node_id
        """

        items = [
            {
                "nid": str(n.id),
                "label": type(n).__name__,
                "props": _encode_props_for_storage(dict(n)),
            }
            for n in nodes
        ]

        return await self.query(cypher, {"items": items})

    async def extract_node(self, node_id: str):
        """Retrieve single node by ID."""
        nodes = await self.extract_nodes([node_id])
        return nodes[0] if nodes else None

    async def extract_nodes(self, node_ids: List[str]):
        """Retrieve multiple nodes by IDs."""
        cypher = f"""
        UNWIND $ids AS target_id
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: target_id}})
        RETURN n
        """
        results = await self.query(cypher, {"ids": node_ids})
        return [r["n"] for r in results]

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get single node as dict."""
        cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})
        RETURN n
        """
        results = await self.query(cypher, {"nid": node_id})
        return results[0]["n"] if results else None

    async def get_nodes(self, node_ids: List[str]) -> List[Dict[str, Any]]:
        """Get multiple nodes as dicts."""
        cypher = f"""
        UNWIND $ids AS target_id
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: target_id}})
        RETURN n
        """
        results = await self.query(cypher, {"ids": node_ids})
        return [r["n"] for r in results]

    async def delete_node(self, node_id: str):
        """Remove node and its relationships."""
        cypher = f"MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}}) DETACH DELETE n"
        return await self.query(cypher, {"nid": node_id})

    async def delete_nodes(self, node_ids: list[str]) -> None:
        """Remove multiple nodes and their relationships."""
        cypher = f"""
        UNWIND $ids AS target_id
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: target_id}})
        DETACH DELETE n
        """
        return await self.query(cypher, {"ids": node_ids})

    async def update_node(self, node_id: str, props: Dict[str, Any]) -> None:
        """Merge props into an existing node (top-level property merge)."""
        cypher = f"MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}}) SET n += $props"
        await self.query(cypher, {"nid": node_id, "props": props})

    async def delete_edge(self, src: str, dst: str, rel: str) -> None:
        """Remove a specific directed edge."""
        cypher = (
            f"MATCH (a:`{_BASE_NODE_LABEL}` {{id: $src}})-[r:`{rel}`]->(b:`{_BASE_NODE_LABEL}` {{id: $dst}}) DELETE r"
        )
        await self.query(cypher, {"src": src, "dst": dst})

    # =========================================================================
    # Edge Operations
    # =========================================================================

    async def has_edge(self, from_node: UUID, to_node: UUID, edge_label: str) -> bool:
        """Check if specific edge exists."""
        cypher = f"""
        MATCH (src:`{_BASE_NODE_LABEL}`)-[:`{edge_label}`]->(tgt:`{_BASE_NODE_LABEL}`)
        WHERE src.id = $src_id AND tgt.id = $tgt_id
        RETURN COUNT(*) > 0 AS exists
        """
        result = await self.query(
            cypher,
            {
                "src_id": str(from_node),
                "tgt_id": str(to_node),
            },
        )
        return result[0]["exists"] if result else False

    async def has_edges(self, edges):
        """Check existence of multiple edges."""
        cypher = """
        UNWIND $items AS item
        MATCH (a)-[r]->(b)
        WHERE id(a) = item.src AND id(b) = item.tgt AND type(r) = item.rel
        RETURN item.src AS src, item.tgt AS tgt, item.rel AS rel, count(r) > 0 AS exists
        """

        items = [{"src": str(e[0]), "tgt": str(e[1]), "rel": e[2]} for e in edges]

        try:
            results = await self.query(cypher, {"items": items})
            return [r["exists"] for r in results]
        except Neo4jError as err:
            _log.error("Edge check error: %s", err, exc_info=True)
            raise

    async def add_edge(
        self,
        from_node: UUID,
        to_node: UUID,
        relationship_name: str,
        edge_properties: Optional[Dict[str, Any]] = None,
    ):
        """Create or update single edge."""
        props = _encode_props_for_storage(edge_properties or {})

        cypher = dedent(f"""
            MATCH (src:`{_BASE_NODE_LABEL}` {{id: $src_id}}),
                  (tgt:`{_BASE_NODE_LABEL}` {{id: $tgt_id}})
            MERGE (src)-[r:`{relationship_name}`]->(tgt)
            ON CREATE SET r += $props, r.updated_at = timestamp()
            ON MATCH SET r += $props, r.updated_at = timestamp()
            RETURN r
        """)

        return await self.query(
            cypher,
            {
                "src_id": str(from_node),
                "tgt_id": str(to_node),
                "props": props,
            },
        )

    @record_graph_changes
    @override_distributed(queued_add_edges)
    async def add_edges(self, edges: list[tuple[str, str, str, dict[str, Any]]]) -> None:
        """Bulk insert/update multiple edges."""
        if not edges:
            return None

        cypher = f"""
        UNWIND $items AS item
        MATCH (src:`{_BASE_NODE_LABEL}` {{id: item.src_id}})
        MATCH (tgt:`{_BASE_NODE_LABEL}` {{id: item.tgt_id}})
        CALL apoc.merge.relationship(
            src,
            item.rel_type,
            {{ source_node_id: item.src_id, target_node_id: item.tgt_id }},
            item.props,
            tgt
        ) YIELD rel
        RETURN rel
        """

        items = [
            {
                "src_id": str(e[0]),
                "tgt_id": str(e[1]),
                "rel_type": e[2],
                "props": _flatten_edge_props(
                    {
                        **(e[3] if e[3] else {}),
                        "source_node_id": str(e[0]),
                        "target_node_id": str(e[1]),
                    }
                ),
            }
            for e in edges
        ]

        try:
            return await self.query(cypher, {"items": items})
        except Neo4jError as err:
            _log.error("Bulk edge insert error: %s", err, exc_info=True)
            raise

    async def get_edges(self, node_id: str):
        """Get all edges connected to a node.

        Returns list of (source_node_props, relationship_name, target_node_props)
        matching the Kuzu adapter's contract expected by all callers.
        """
        cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})-[r]-(m)
        RETURN properties(n) AS src, TYPE(r) AS rel, properties(m) AS dst
        """
        results = await self.query(cypher, {"nid": node_id})
        return [(r["src"], r["rel"], r["dst"]) for r in results]

    # =========================================================================
    # Graph Traversal
    # =========================================================================

    async def get_predecessors(self, node_id: str, edge_label: str = None) -> list[str]:
        """Get nodes pointing to this node."""
        if edge_label:
            cypher = f"""
            MATCH (n:`{_BASE_NODE_LABEL}`)<-[:`{edge_label}`]-(pred)
            WHERE n.id = $nid
            RETURN pred
            """
        else:
            cypher = f"""
            MATCH (n:`{_BASE_NODE_LABEL}`)<-[]-(pred)
            WHERE n.id = $nid
            RETURN pred
            """
        results = await self.query(cypher, {"nid": node_id})
        return [r["pred"] for r in results]

    async def get_successors(self, node_id: str, edge_label: str = None) -> list[str]:
        """Get nodes this node points to."""
        if edge_label:
            cypher = f"""
            MATCH (n:`{_BASE_NODE_LABEL}`)-[:`{edge_label}`]->(succ)
            WHERE n.id = $nid
            RETURN succ
            """
        else:
            cypher = f"""
            MATCH (n:`{_BASE_NODE_LABEL}`)-[]->(succ)
            WHERE n.id = $nid
            RETURN succ
            """
        results = await self.query(cypher, {"nid": node_id})
        return [r["succ"] for r in results]

    async def get_neighbors(self, node_id: str) -> List[Dict[str, Any]]:
        """Get all directly connected nodes."""
        cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}` {{id: $nid}})-[r]-(m)
        RETURN DISTINCT properties(m) AS props
        """
        results = await self.query(cypher, {"nid": node_id})
        return [r["props"] for r in results]

    async def get_disconnected_nodes(self) -> list[str]:
        """Find nodes not in the largest connected component."""
        cypher = """
        MATCH (n)
        WITH COLLECT(n) AS all_nodes
        CALL {
            WITH all_nodes
            UNWIND all_nodes AS start
            MATCH path = (start)-[*]-(connected)
            WITH start, COLLECT(DISTINCT connected) AS component
            RETURN component
        }
        WITH COLLECT(component) AS components
        UNWIND components AS comp
        WITH comp ORDER BY SIZE(comp) DESC LIMIT 1
        WITH comp AS largest
        MATCH (n) WHERE NOT n IN largest
        RETURN COLLECT(ID(n)) AS isolated_ids
        """
        results = await self.query(cypher)
        return results[0]["isolated_ids"] if results else []

    async def get_triplets(self, node_id: UUID) -> list:
        """Get all incoming and outgoing connections.

        Returns list of (source_props, edge_dict, target_props) matching
        the Kuzu/Neptune contract.
        """
        incoming_cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}`)<-[r]-(nbr)
        WHERE n.id = $nid
        RETURN properties(nbr) AS src, TYPE(r) AS rel, properties(r) AS rprops, properties(n) AS dst
        """
        outgoing_cypher = f"""
        MATCH (n:`{_BASE_NODE_LABEL}`)-[r]->(nbr)
        WHERE n.id = $nid
        RETURN properties(n) AS src, TYPE(r) AS rel, properties(r) AS rprops, properties(nbr) AS dst
        """

        incoming, outgoing = await asyncio.gather(
            self.query(incoming_cypher, {"nid": str(node_id)}),
            self.query(outgoing_cypher, {"nid": str(node_id)}),
        )

        connections = []
        for rec in incoming:
            connections.append((rec["src"], {"relationship_name": rec["rel"], **(rec["rprops"] or {})}, rec["dst"]))
        for rec in outgoing:
            connections.append((rec["src"], {"relationship_name": rec["rel"], **(rec["rprops"] or {})}, rec["dst"]))

        return connections

    async def remove_connection_to_predecessors_of(self, node_ids: list[str], edge_label: str) -> None:
        """Delete outgoing edges with given label from nodes."""
        cypher = f"""
        UNWIND $ids AS nid
        MATCH ({{id: nid}})-[r:{edge_label}]->(pred)
        DELETE r
        """
        return await self.query(cypher, {"ids": node_ids})

    async def remove_connection_to_successors_of(self, node_ids: list[str], edge_label: str) -> None:
        """Delete incoming edges with given label to nodes."""
        cypher = f"""
        UNWIND $ids AS nid
        MATCH ({{id: nid}})<-[r:{edge_label}]-(succ)
        DELETE r
        """
        return await self.query(cypher, {"ids": node_ids})

    # =========================================================================
    # Graph Management
    # =========================================================================

    async def delete_graph(self):
        """Remove all nodes and edges."""
        labels = await self.get_node_labels()
        for lbl in labels:
            cypher = f"MATCH (n:`{lbl}`) DETACH DELETE n"
            await self.query(cypher)

    async def get_node_labels(self):
        """List all node labels in database."""
        cypher = "CALL db.labels()"
        results = await self.query(cypher)
        return [r["label"] for r in results]

    async def get_relationship_labels_string(self):
        """Get relationship types formatted for GDS projection."""
        cypher = "CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) AS rels"
        results = await self.query(cypher)
        rels = results[0]["rels"] if results else []

        if not rels:
            raise ValueError("No relationship types found")

        return "{" + ", ".join(f"{r}: {{orientation: 'UNDIRECTED'}}" for r in rels) + "}"

    async def graph_exists(self, graph_name: str = "myGraph") -> bool:
        """Check if GDS graph projection exists."""
        cypher = "CALL gds.graph.list() YIELD graphName RETURN collect(graphName) AS names"
        results = await self.query(cypher)
        names = results[0]["names"] if results else []
        return graph_name in names

    async def project_entire_graph(self, graph_name: str = "myGraph"):
        """Create GDS in-memory graph projection."""
        if await self.graph_exists(graph_name):
            return

        labels = await self.get_node_labels()
        rel_str = await self.get_relationship_labels_string()

        cypher = f"""
        CALL gds.graph.project(
            '{graph_name}',
            ['{"', '".join(labels)}'],
            {rel_str}
        ) YIELD graphName
        """
        await self.query(cypher)

    async def drop_graph(self, graph_name: str = "myGraph"):
        """Remove GDS graph projection."""
        if await self.graph_exists(graph_name):
            cypher = f"CALL gds.graph.drop('{graph_name}')"
            await self.query(cypher)

    # =========================================================================
    # Data Retrieval
    # =========================================================================

    async def get_model_independent_graph_data(self):
        """Get raw nodes and edges without type filtering."""
        nodes_cypher = "MATCH (n) RETURN collect(n) AS nodes"
        edges_cypher = "MATCH (n)-[r]->(m) RETURN collect([n, r, m]) AS elements"

        nodes = await self.query(nodes_cypher)
        edges = await self.query(edges_cypher)

        return (nodes, edges)

    async def get_graph_data(self):
        """Get all nodes and edges with properties."""
        t0 = time.time()

        try:
            node_cypher = "MATCH (n) RETURN ID(n) AS id, labels(n) AS labels, properties(n) AS props"
            node_results = await self.query(node_cypher)

            nodes = [(r["props"]["id"], r["props"]) for r in node_results]

            edge_cypher = """
            MATCH (n)-[r]->(m)
            RETURN ID(n) AS src, ID(m) AS tgt, TYPE(r) AS type, properties(r) AS props
            """
            edge_results = await self.query(edge_cypher)

            edges = [
                (
                    r["props"]["source_node_id"],
                    r["props"]["target_node_id"],
                    r["type"],
                    r["props"],
                )
                for r in edge_results
            ]

            _log.info(f"Retrieved {len(nodes)} nodes, {len(edges)} edges in {time.time() - t0:.2f}s")
            return (nodes, edges)

        except Exception as err:
            _log.error(f"Graph data retrieval failed: {err}")
            raise

    async def get_id_filtered_graph_data(self, target_ids: list[str]):
        """Get graph data filtered by node IDs."""
        t0 = time.time()

        try:
            if not target_ids:
                _log.warning("Empty target IDs for filtered retrieval")
                return [], []

            cypher = """
            MATCH ()-[r]-()
            WHERE startNode(r).id IN $ids OR endNode(r).id IN $ids
            WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b
            RETURN
                properties(a) AS a_props,
                properties(b) AS b_props,
                type(r) AS rel_type,
                properties(r) AS rel_props
            """

            results = await self.query(cypher, {"ids": target_ids})

            nodes_map = {}
            edges = []

            for r in results:
                a_props, b_props = r["a_props"], r["b_props"]
                nodes_map[a_props["id"]] = (a_props["id"], a_props)
                nodes_map[b_props["id"]] = (b_props["id"], b_props)

                rel_props = r["rel_props"]
                edges.append(
                    (
                        rel_props.get("source_node_id", a_props["id"]),
                        rel_props.get("target_node_id", b_props["id"]),
                        r["rel_type"],
                        rel_props,
                    )
                )

            _log.info(f"ID-filtered: {len(nodes_map)} nodes, {len(edges)} edges in {time.time() - t0:.2f}s")
            return list(nodes_map.values()), edges

        except Exception as err:
            _log.error(f"ID-filtered retrieval failed: {err}")
            raise

    async def extract_typed_subgraph(
        self, node_type: Type[Any], node_name: List[str]
    ) -> Tuple[List[Tuple[int, dict]], List[Tuple[int, int, str, dict]]]:
        """Get subgraph for specific node names and type."""
        t0 = time.time()
        label = node_type.__name__

        try:
            cypher = f"""
            UNWIND $names AS wanted
            MATCH (n:`{label}`) WHERE n.name = wanted
            WITH collect(DISTINCT n) AS primary
            UNWIND primary AS p
            OPTIONAL MATCH (p)--(nbr)
            WITH primary, collect(DISTINCT nbr) AS nbrs
            WITH primary + nbrs AS all_nodes
            UNWIND all_nodes AS node
            WITH collect(DISTINCT node) AS nodes
            MATCH (a)-[r]-(b) WHERE a IN nodes AND b IN nodes
            WITH nodes, collect(DISTINCT r) AS rels
            RETURN
                [n IN nodes | {{id: n.id, props: properties(n)}}] AS raw_nodes,
                [r IN rels | {{type: type(r), props: properties(r)}}] AS raw_rels
            """

            results = await self.query(cypher, {"names": node_name})

            if not results:
                return [], []

            raw_nodes = results[0]["raw_nodes"]
            raw_rels = results[0]["raw_rels"]

            nodes = [(n["props"]["id"], n["props"]) for n in raw_nodes]
            edges = [
                (
                    r["props"]["source_node_id"],
                    r["props"]["target_node_id"],
                    r["type"],
                    r["props"],
                )
                for r in raw_rels
            ]

            _log.info(f"Subgraph [{label}]: {len(nodes)} nodes, {len(edges)} edges in {time.time() - t0:.2f}s")
            return nodes, edges

        except Exception as err:
            _log.error(f"Subgraph retrieval failed: {err}")
            raise

    async def query_by_attributes(self, attribute_filters):
        """Get nodes/edges filtered by attribute criteria (parameterized)."""
        where_parts = []
        params: Dict[str, Any] = {}
        for i, flt in enumerate(attribute_filters):
            for attr, vals in flt.items():
                pname = f"vals_{i}_{attr}"
                where_parts.append(f"n.{attr} IN ${pname}")
                params[pname] = vals

        where_clause = " AND ".join(where_parts)

        node_cypher = f"""
        MATCH (n) WHERE {where_clause}
        RETURN n.id AS id, labels(n) AS labels, properties(n) AS props
        """
        node_results = await self.query(node_cypher, params)
        nodes = [(r["id"], r["props"]) for r in node_results]

        edge_params = dict(params)
        m_where = where_clause.replace("n.", "m.")
        edge_cypher = f"""
        MATCH (n)-[r]->(m)
        WHERE {where_clause} AND {m_where}
        RETURN n.id AS src, m.id AS tgt, TYPE(r) AS type, properties(r) AS props
        """
        edge_results = await self.query(edge_cypher, edge_params)
        edges = [
            (
                r["props"]["source_node_id"],
                r["props"]["target_node_id"],
                r["type"],
                r["props"],
            )
            for r in edge_results
        ]

        return (nodes, edges)

    async def get_document_subgraph(self, data_id: str):
        """Get document-related subgraph with chunks and concepts."""
        cypher = """
        MATCH (doc)
        WHERE (doc:TextDocument OR doc:PdfDocument OR doc:UnstructuredDocument 
               OR doc:AudioDocument OR doc:ImageDocument)
        AND doc.id = $doc_id
        
        OPTIONAL MATCH (doc)<-[:is_part_of]-(chunk:ContentFragment)
        OPTIONAL MATCH (chunk)-[:contains]->(concept)
        WHERE (concept:Entity OR concept:Entity)
        AND NOT EXISTS {
            MATCH (concept)<-[:contains]-(other:ContentFragment)-[:is_part_of]->(other_doc)
            WHERE (other_doc:TextDocument OR other_doc:PdfDocument 
                   OR other_doc:UnstructuredDocument OR other_doc:AudioDocument 
                   OR other_doc:ImageDocument)
            AND other_doc.id <> doc.id
        }
        OPTIONAL MATCH (chunk)<-[:made_from]-(digest:FragmentDigest)
        OPTIONAL MATCH (concept)-[:is_a]->(ctype)
        WHERE (ctype:EntityType OR ctype:EntityType)
        AND NOT EXISTS {
            MATCH (ctype)<-[:is_a]-(other_concept)<-[:contains]-(other:ContentFragment)-[:is_part_of]->(other_doc)
            WHERE (other_concept:Entity OR other_concept:Entity)
            WHERE (other_doc:TextDocument OR other_doc:PdfDocument 
                   OR other_doc:UnstructuredDocument OR other_doc:AudioDocument 
                   OR other_doc:ImageDocument)
            AND other_doc.id <> doc.id
        }
        
        RETURN
            collect(DISTINCT doc) as document,
            collect(DISTINCT chunk) as chunks,
            collect(DISTINCT concept) as orphan_entities,
            collect(DISTINCT digest) as made_from_nodes,
            collect(DISTINCT ctype) as orphan_types
        """
        results = await self.query(cypher, {"doc_id": data_id})
        return results[0] if results else None

    async def get_degree_one_nodes(self, node_type: str):
        """Get nodes with exactly one connection."""
        valid = ["Entity", "EntityType", "Entity", "EntityType"]
        if node_type not in valid:
            raise ValueError(f"node_type must be one of {valid}")

        cypher = f"""
        MATCH (n:{node_type})
        WHERE COUNT {{ MATCH (n)--() }} = 1
        RETURN n
        """
        results = await self.query(cypher)
        return [r["n"] for r in results] if results else []

    # =========================================================================
    # Metrics
    # =========================================================================

    async def get_graph_metrics(self, extended: bool = False):
        """Calculate graph statistics."""
        nodes_data, edges_data = await self.get_model_independent_graph_data()

        projection_name = "myGraph"
        await self.drop_graph(projection_name)
        await self.project_entire_graph(projection_name)

        num_nodes = len(nodes_data[0]["nodes"]) if nodes_data else 0
        num_edges = len(edges_data[0]["elements"]) if edges_data else 0

        metrics = {
            "num_nodes": num_nodes,
            "num_edges": num_edges,
            "mean_degree": (2 * num_edges / num_nodes) if num_nodes else None,
            "edge_density": await get_edge_density(self),
            "num_connected_components": await get_num_connected_components(self, projection_name),
            "sizes_of_connected_components": await get_size_of_connected_components(self, projection_name),
        }

        if extended:
            path_lengths = await get_shortest_path_lengths(self, projection_name)
            metrics.update(
                {
                    "num_selfloops": await count_self_loops(self),
                    "diameter": max(path_lengths) if path_lengths else -1,
                    "avg_shortest_path_length": (sum(path_lengths) / len(path_lengths) if path_lengths else -1),
                    "avg_clustering": await get_avg_clustering(self, projection_name),
                }
            )
        else:
            metrics.update(
                {
                    "num_selfloops": -1,
                    "diameter": -1,
                    "avg_shortest_path_length": -1,
                    "avg_clustering": -1,
                }
            )

        return metrics

    # =========================================================================
    # User Interaction & Feedback
    # =========================================================================

    async def get_last_user_interaction_ids(self, limit: int) -> List[str]:
        """Get recent user interaction node IDs."""
        cypher = """
        MATCH (n) WHERE n.type = 'MflowUserInteraction'
        RETURN n.id as id
        ORDER BY n.created_at DESC
        LIMIT $max_results
        """
        rows = await self.query(cypher, {"max_results": limit})
        return [r["id"] for r in rows if "id" in r]

    async def apply_feedback_weight(self, node_ids: List[str], weight: float) -> None:
        """Adjust feedback weight on answer relationships."""
        cypher = """
        MATCH (n)-[r]->()
        WHERE n.id IN $ids AND r.relationship_name = 'used_graph_element_to_answer'
        SET r.feedback_weight = coalesce(r.feedback_weight, 0) + $delta
        """
        await self.query(cypher, {"ids": list(node_ids), "delta": float(weight)})

    async def get_triplets_batch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Get batch of triplets for export."""
        cypher = f"""
        MATCH (start:`{_BASE_NODE_LABEL}`)-[rel]->(end:`{_BASE_NODE_LABEL}`)
        RETURN start, properties(rel) AS rel_props, end
        SKIP $skip LIMIT $max
        """
        return await self.query(cypher, {"skip": offset, "max": limit})

    # =========================================================================
    # Compatibility Aliases
    # =========================================================================

    def serialize_properties(self, properties: dict = None) -> dict:
        """Alias for _encode_props_for_storage."""
        return _encode_props_for_storage(properties or {})

    def _flatten_edge_properties(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for _flatten_edge_props."""
        return _flatten_edge_props(properties)
