"""
Amazon Neptune Analytics graph database adapter.

Provides an implementation of GraphProvider for Neptune Analytics,
supporting openCypher queries with automatic retry and bulk operations.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Type
from uuid import UUID

from m_flow.shared.logging_utils import get_logger

if TYPE_CHECKING:
    pass

from m_flow.adapters.graph.graph_db_interface import (
    EdgeTuple as EdgeData,
    GraphProvider,
    NodeTuple as Node,
    NodeProps as NodeData,
    record_graph_changes,
)
from m_flow.core import MemoryNode
from m_flow.storage.utils_mod.utils import JSONEncoder

from .exceptions import NeptuneAnalyticsConfigurationError
from .neptune_utils import (
    build_neptune_config,
    format_neptune_error,
    validate_aws_region,
    validate_graph_id,
)

_log = get_logger("NeptuneAdapter")

# Lazy-loaded Neptune Analytics client
_NEPTUNE_CLIENT_CLASS = None
_NEPTUNE_AVAILABLE = None


def _ensure_neptune_available() -> bool:
    """Check and cache Neptune Analytics availability."""
    global _NEPTUNE_AVAILABLE, _NEPTUNE_CLIENT_CLASS

    if _NEPTUNE_AVAILABLE is not None:
        return _NEPTUNE_AVAILABLE

    try:
        from langchain_aws import NeptuneAnalyticsGraph

        _NEPTUNE_CLIENT_CLASS = NeptuneAnalyticsGraph
        _NEPTUNE_AVAILABLE = True
    except ImportError:
        _log.warning("Neptune Analytics SDK unavailable - functionality will be limited")
        _NEPTUNE_AVAILABLE = False

    return _NEPTUNE_AVAILABLE


def _get_neptune_client_class():
    """Get the Neptune Analytics client class."""
    _ensure_neptune_available()
    return _NEPTUNE_CLIENT_CLASS


# Node label constant for M-flow graph nodes
_NODE_TYPE_LABEL = "MFLOW_NODE"

# Neptune Analytics protocol prefix
_NEPTUNE_PROTOCOL = "neptune-graph://"


def _prepare_properties_for_storage(props: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert property values to Neptune-compatible formats.

    UUIDs are converted to strings, complex objects (dict/list) are JSON-serialized.
    """
    result = {}

    for key, val in props.items():
        if isinstance(val, UUID):
            result[key] = str(val)
        elif isinstance(val, (dict, list)):
            result[key] = json.dumps(val, cls=JSONEncoder)
        else:
            result[key] = val

    return result


def _transform_record_to_edge(rec: dict) -> EdgeData:
    """Convert a query result record into EdgeData tuple format."""
    return (
        rec["source_id"],
        rec["target_id"],
        rec["relationship_name"],
        rec["properties"],
    )


class NeptuneGraphDB(GraphProvider):
    """
    Neptune Analytics adapter implementing the GraphProvider.

    Supports openCypher query execution with bulk node and edge operations,
    automatic fallback for failed bulk operations, and graph metrics retrieval.
    """

    _GRAPH_NODE_LABEL = _NODE_TYPE_LABEL

    def __init__(
        self,
        graph_id: str,
        region: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
    ):
        """
        Create a Neptune Analytics adapter instance.

        Args:
            graph_id: Neptune Analytics graph identifier
            region: AWS region (defaults to us-east-1 if not specified)
            aws_access_key_id: Optional AWS access key
            aws_secret_access_key: Optional AWS secret key
            aws_session_token: Optional session token for temporary credentials

        Raises:
            ImportError: If langchain_aws is not installed
            NeptuneAnalyticsConfigurationError: If configuration is invalid
        """
        self._verify_dependencies()
        self._validate_config(graph_id, region)

        self._graph_id = graph_id
        self._region = region
        self._credentials = {
            "access_key": aws_access_key_id,
            "secret_key": aws_secret_access_key,
            "session_token": aws_session_token,
        }

        self.config = build_neptune_config(
            graph_id=graph_id,
            region=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )

        self._client = self._create_client()
        _log.info(f"Neptune adapter ready: graph={graph_id}, region={region}")

    # Compatibility properties
    @property
    def graph_id(self) -> str:
        return self._graph_id

    @property
    def region(self) -> Optional[str]:
        return self._region

    def _verify_dependencies(self) -> None:
        """Ensure required dependencies are available."""
        if not _ensure_neptune_available():
            raise ImportError("langchain_aws package required for Neptune Analytics support")

    def _validate_config(self, graph_id: str, region: Optional[str]) -> None:
        """Validate configuration parameters."""
        if not validate_graph_id(graph_id):
            raise NeptuneAnalyticsConfigurationError(message=f"Graph ID invalid: {graph_id}")

        if region and not validate_aws_region(region):
            raise NeptuneAnalyticsConfigurationError(message=f"Region invalid: {region}")

    def _create_client(self):
        """Initialize the Neptune Analytics client."""
        from botocore.config import Config as BotoConfig

        NeptuneAnalyticsGraph = _get_neptune_client_class()

        client_params = {
            "graph_identifier": self._graph_id,
            "config": BotoConfig(user_agent_appid="Mflow"),
        }

        if self._region:
            client_params["region_name"] = self._region

        for param_name, cred_key in [
            ("aws_access_key_id", "access_key"),
            ("aws_secret_access_key", "secret_key"),
            ("aws_session_token", "session_token"),
        ]:
            cred_val = self._credentials.get(cred_key)
            if cred_val:
                client_params[param_name] = cred_val

        try:
            client = NeptuneAnalyticsGraph(**client_params)
            _log.info("Neptune client initialized successfully")
            return client
        except Exception as exc:
            raise NeptuneAnalyticsConfigurationError(
                message=f"Client initialization failed: {format_neptune_error(exc)}"
            ) from exc

    async def query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        """
        Execute an openCypher query and return results.

        Args:
            query: OpenCypher query string
            params: Optional parameter dictionary

        Returns:
            List of result records
        """
        query_params = params if params else {}
        _log.debug(f"Executing query:\n{query}")

        try:
            raw_result = self._client.query(query, query_params)

            if isinstance(raw_result, list):
                return raw_result
            if isinstance(raw_result, dict):
                return [raw_result]
            return [{"result": raw_result}]

        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Query failed: {err}")
            raise Exception(f"Query execution error: {err}") from exc

    # -------------------------------------------------------------------------
    # Node Operations
    # -------------------------------------------------------------------------

    async def has_node(self, node_id: str) -> bool:
        """Check if a node exists by ID."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $node_id
        RETURN count(n) > 0 AS exists
        """
        try:
            result = await self.query(cypher, {"node_id": node_id})
            if result and isinstance(result[0], dict):
                return bool(result[0].get("exists", False))
            return bool(result and result[0])
        except Exception:
            return False

    async def add_node(self, node: MemoryNode) -> None:
        """Add or update a single node in the graph."""
        props = _prepare_properties_for_storage(node.model_dump())

        cypher = f"""
        MERGE (n:{self._GRAPH_NODE_LABEL} {{`~id`: $node_id}})
        ON CREATE SET n = $properties, n.updated_at = timestamp()
        ON MATCH SET n += $properties, n.updated_at = timestamp()
        RETURN n
        """

        try:
            await self.query(cypher, {"node_id": str(node.id), "properties": props})
            _log.debug(f"Node upserted: {node.id}")
        except Exception as exc:
            _log.error(f"Node add failed [{node.id}]: {format_neptune_error(exc)}")
            raise

    @record_graph_changes
    async def add_nodes(self, nodes: List[MemoryNode]) -> None:
        """Bulk insert/update nodes using UNWIND."""
        if not nodes:
            return

        cypher = f"""
        UNWIND $items AS item
        MERGE (n:{self._GRAPH_NODE_LABEL} {{`~id`: item.node_id}})
        ON CREATE SET n = item.properties, n.updated_at = timestamp()
        ON MATCH SET n += item.properties, n.updated_at = timestamp()
        RETURN count(n) AS processed
        """

        items = [
            {
                "node_id": str(n.id),
                "properties": _prepare_properties_for_storage(n.model_dump()),
            }
            for n in nodes
        ]

        try:
            result = await self.query(cypher, {"items": items})
            count = result[0].get("processed", 0) if result else 0
            _log.debug(f"Bulk node upsert: {count} processed")
        except Exception as exc:
            _log.error(f"Bulk node insert failed: {format_neptune_error(exc)}")
            _log.info("Attempting individual node inserts")
            for n in nodes:
                try:
                    await self.add_node(n)
                except Exception as inner_exc:
                    _log.error(f"Individual node failed [{n.id}]: {format_neptune_error(inner_exc)}")

    async def delete_node(self, node_id: str) -> None:
        """Remove a node and its relationships by ID."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $nid
        DETACH DELETE n
        """

        try:
            await self.query(cypher, {"nid": node_id})
            _log.debug(f"Node deleted: {node_id}")
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Node deletion failed [{node_id}]: {err}")
            raise Exception(f"Delete node error: {err}") from exc

    async def update_node(self, node_id: str, props: Dict[str, Any]) -> None:
        """Merge props into an existing node."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $nid
        SET n += $props
        """
        try:
            await self.query(cypher, {"nid": node_id, "props": props})
        except Exception as exc:
            _log.error(f"update_node failed [{node_id}]: {format_neptune_error(exc)}")
            raise

    async def delete_edge(self, src: str, dst: str, rel: str) -> None:
        """Remove a specific directed edge."""
        cypher = f"""
        MATCH (a:{self._GRAPH_NODE_LABEL})-[r:{rel}]->(b:{self._GRAPH_NODE_LABEL})
        WHERE id(a) = $src AND id(b) = $dst
        DELETE r
        """
        try:
            await self.query(cypher, {"src": src, "dst": dst})
        except Exception as exc:
            _log.error(f"delete_edge failed [{src}->{dst}]: {format_neptune_error(exc)}")
            raise

    async def delete_nodes(self, node_ids: List[str]) -> None:
        """Bulk delete nodes by IDs."""
        if not node_ids:
            return

        cypher = f"""
        UNWIND $ids AS nid
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = nid
        DETACH DELETE n
        """

        try:
            await self.query(cypher, {"ids": node_ids})
            _log.debug(f"Bulk deleted {len(node_ids)} nodes")
        except Exception as exc:
            _log.error(f"Bulk delete failed: {format_neptune_error(exc)}")
            _log.info("Attempting individual deletions")
            for nid in node_ids:
                try:
                    await self.delete_node(nid)
                except Exception:
                    pass

    async def get_node(self, node_id: str) -> Optional[NodeData]:
        """Retrieve a single node by ID."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $nid
        RETURN n
        """

        try:
            results = await self.query(cypher, {"nid": node_id})

            if results and len(results) == 1:
                return results[0]["n"]

            if not results:
                _log.debug(f"Node not found: {node_id}")
            elif len(results) > 1:
                _log.warning(f"Multiple nodes returned for ID: {node_id}")

            return None

        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get node failed [{node_id}]: {err}")
            raise Exception(f"Get node error: {err}") from exc

    async def get_nodes(self, node_ids: List[str]) -> List[NodeData]:
        """Retrieve multiple nodes by IDs."""
        if not node_ids:
            return []

        cypher = f"""
        UNWIND $ids AS nid
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = nid
        RETURN n
        """

        try:
            results = await self.query(cypher, {"ids": node_ids})
            nodes = [r["n"] for r in results]
            _log.debug(f"Retrieved {len(nodes)}/{len(node_ids)} nodes")
            return nodes
        except Exception as exc:
            _log.error(f"Bulk get nodes failed: {format_neptune_error(exc)}")
            # Fallback to individual retrieval
            found = []
            for nid in node_ids:
                try:
                    node = await self.get_node(nid)
                    if node:
                        found.append(node)
                except Exception:
                    pass
            return found

    async def extract_node(self, node_id: str):
        """Alias for retrieving a single node."""
        nodes = await self.extract_nodes([node_id])
        return nodes[0] if nodes else None

    async def extract_nodes(self, node_ids: List[str]):
        """Retrieve nodes by IDs (alternative method)."""
        cypher = f"""
        UNWIND $ids AS target_id
        MATCH (n:{self._GRAPH_NODE_LABEL}) WHERE id(n) = target_id
        RETURN n
        """
        results = await self.query(cypher, {"ids": node_ids})
        return [r["n"] for r in results]

    # -------------------------------------------------------------------------
    # Edge Operations
    # -------------------------------------------------------------------------

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship_name: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create or update an edge between two nodes."""
        edge_props = _prepare_properties_for_storage(properties or {})

        cypher = f"""
        MATCH (src:{self._GRAPH_NODE_LABEL}) WHERE id(src) = $src_id
        MATCH (tgt:{self._GRAPH_NODE_LABEL}) WHERE id(tgt) = $tgt_id
        MERGE (src)-[e:{relationship_name}]->(tgt)
        ON CREATE SET e = $props, e.updated_at = timestamp()
        ON MATCH SET e = $props, e.updated_at = timestamp()
        RETURN e
        """

        params = {"src_id": source_id, "tgt_id": target_id, "props": edge_props}

        try:
            await self.query(cypher, params)
            _log.debug(f"Edge created: {source_id} -[{relationship_name}]-> {target_id}")
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Edge creation failed: {err}")
            raise Exception(f"Add edge error: {err}") from exc

    @record_graph_changes
    async def add_edges(self, edges: List[Tuple[str, str, str, Optional[Dict[str, Any]]]]) -> None:
        """Bulk insert edges, grouped by relationship type."""
        if not edges:
            return

        # Group edges by relationship type for efficient batch queries
        grouped: Dict[str, List] = {}
        for edge in edges:
            rel_type = edge[2]
            if rel_type not in grouped:
                grouped[rel_type] = []
            grouped[rel_type].append(edge)

        total_processed = 0

        for rel_type, rel_edges in grouped.items():
            cypher = f"""
            UNWIND $edges AS e
            MATCH (src:{self._GRAPH_NODE_LABEL}) WHERE id(src) = e.from_id
            MATCH (tgt:{self._GRAPH_NODE_LABEL}) WHERE id(tgt) = e.to_id
            MERGE (src)-[r:{rel_type}]->(tgt)
            ON CREATE SET r = e.props, r.updated_at = timestamp()
            ON MATCH SET r = e.props, r.updated_at = timestamp()
            RETURN count(*) AS cnt
            """

            edge_data = [
                {
                    "from_id": str(e[0]),
                    "to_id": str(e[1]),
                    "props": _prepare_properties_for_storage(e[3] if len(e) > 3 and e[3] else {}),
                }
                for e in rel_edges
            ]

            try:
                result = await self.query(cypher, {"edges": edge_data})
                total_processed += result[0].get("cnt", 0) if result else 0
            except Exception as exc:
                _log.error(f"Bulk edge insert failed for {rel_type}: {format_neptune_error(exc)}")
                # Fallback
                for e in rel_edges:
                    props = e[3] if len(e) > 3 else {}
                    try:
                        await self.add_edge(e[0], e[1], e[2], props)
                    except Exception:
                        pass

        _log.debug(f"Bulk edge insert: {total_processed} processed")

    async def has_edge(self, source_id: str, target_id: str, relationship_name: str) -> bool:
        """Check if an edge exists between two nodes."""
        cypher = f"""
        MATCH (s:{self._GRAPH_NODE_LABEL})-[r:{relationship_name}]->(t:{self._GRAPH_NODE_LABEL})
        WHERE id(s) = $sid AND id(t) = $tid
        RETURN COUNT(r) > 0 AS exists
        """

        try:
            result = await self.query(cypher, {"sid": source_id, "tid": target_id})
            exists = result[0].get("exists", False) if result else False
            _log.debug(f"Edge check [{source_id}]-[{relationship_name}]->[{target_id}]: {exists}")
            return exists
        except Exception as exc:
            _log.error(f"Edge existence check failed: {format_neptune_error(exc)}")
            return False

    async def has_edges(self, edges: List[EdgeData]) -> List[bool]:
        """Check existence of multiple edges."""
        cypher = f"""
        UNWIND $items AS item
        MATCH (a:{self._GRAPH_NODE_LABEL})-[r]->(b:{self._GRAPH_NODE_LABEL})
        WHERE id(a) = item.src AND id(b) = item.tgt AND type(r) = item.rel
        RETURN item.src AS src, item.tgt AS tgt, item.rel AS rel, count(r) > 0 AS found
        """

        items = [{"src": str(e[0]), "tgt": str(e[1]), "rel": e[2]} for e in edges]

        try:
            results = await self.query(cypher, {"items": items})
            return [r["found"] for r in results]
        except Exception as exc:
            _log.error(f"Bulk edge check failed: {format_neptune_error(exc)}")
            return []

    async def get_edges(self, node_id: str) -> list:
        """Get all edges connected to a node.

        Returns list of (source_node_props, relationship_name, target_node_props)
        matching the Kuzu adapter's contract expected by all callers.
        """
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})-[r]-(m:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $nid
        RETURN id(n) AS nid, properties(n) AS nprops,
               type(r) AS rel,
               id(m) AS mid, properties(m) AS mprops
        """

        try:
            results = await self.query(cypher, {"nid": node_id})
            edges = [
                (
                    {"id": r["nid"], **(r["nprops"] or {})},
                    r["rel"],
                    {"id": r["mid"], **(r["mprops"] or {})},
                )
                for r in results
            ]
            _log.debug(f"Retrieved {len(edges)} edges for node {node_id}")
            return edges
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get edges failed [{node_id}]: {err}")
            raise Exception(f"Get edges error: {err}") from exc

    # -------------------------------------------------------------------------
    # Graph Operations
    # -------------------------------------------------------------------------

    async def delete_graph(self) -> None:
        """Delete all nodes and edges from the graph."""
        cypher = f"MATCH (n:{self._GRAPH_NODE_LABEL}) DETACH DELETE n"

        try:
            await self.query(cypher)
            _log.info("Graph cleared")
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Graph deletion failed: {err}")
            raise Exception(f"Delete graph error: {err}") from exc

    async def get_graph_data(self) -> Tuple[List[Node], List[EdgeData]]:
        """Retrieve all nodes and edges."""
        node_cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        RETURN id(n) AS node_id, properties(n) AS properties
        """

        edge_cypher = f"""
        MATCH (s:{self._GRAPH_NODE_LABEL})-[r]->(t:{self._GRAPH_NODE_LABEL})
        RETURN id(s) AS source_id, id(t) AS target_id, type(r) AS relationship_name, properties(r) AS properties
        """

        try:
            node_results = await self.query(node_cypher)
            edge_results = await self.query(edge_cypher)

            nodes = [(r["node_id"], r["properties"]) for r in node_results]
            edges = [(r["source_id"], r["target_id"], r["relationship_name"], r["properties"]) for r in edge_results]

            _log.debug(f"Graph data: {len(nodes)} nodes, {len(edges)} edges")
            return (nodes, edges)
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get graph data failed: {err}")
            raise Exception(f"Get graph data error: {err}") from exc

    async def get_graph_metrics(self, extended: bool = False) -> Dict[str, Any]:
        """Calculate graph statistics and metrics."""
        node_count, edge_count = await self._fetch_basic_counts()
        component_count, component_sizes = await self._analyze_components()

        metrics = {
            "num_nodes": node_count,
            "num_edges": edge_count,
            "mean_degree": (2 * edge_count / node_count) if node_count else None,
            "edge_density": (edge_count / (node_count * (node_count - 1)) if node_count > 1 else None),
            "num_connected_components": component_count,
            "sizes_of_connected_components": component_sizes,
            "num_selfloops": -1,
            "diameter": -1,
            "avg_shortest_path_length": -1,
            "avg_clustering": -1,
        }

        if extended:
            metrics["num_selfloops"] = await self._count_loops()

        return metrics

    # -------------------------------------------------------------------------
    # Neighbor/Connection Operations
    # -------------------------------------------------------------------------

    async def get_neighbors(self, node_id: str) -> List[NodeData]:
        """Get all neighboring nodes."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})-[r]-(nbr:{self._GRAPH_NODE_LABEL})
        WHERE id(n) = $nid
        RETURN DISTINCT id(nbr) AS nbr_id, properties(nbr) AS props
        """

        try:
            results = await self.query(cypher, {"nid": node_id})
            neighbors = [{"id": r["nbr_id"], **r["props"]} for r in results]
            _log.debug(f"Found {len(neighbors)} neighbors for {node_id}")
            return neighbors
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get neighbors failed [{node_id}]: {err}")
            raise Exception(f"Get neighbors error: {err}") from exc

    async def get_triplets(self, node_id: UUID) -> list:
        """Get all connections with full relationship details."""
        cypher = f"""
        MATCH (s:{self._GRAPH_NODE_LABEL})-[r]->(t:{self._GRAPH_NODE_LABEL})
        WHERE id(s) = $nid OR id(t) = $nid
        RETURN
            id(s) AS sid, properties(s) AS sprops,
            id(t) AS tid, properties(t) AS tprops,
            type(r) AS rel_type, properties(r) AS rprops
        """

        try:
            results = await self.query(cypher, {"nid": str(node_id)})

            connections = [
                (
                    {"id": r["sid"], **r["sprops"]},
                    {"relationship_name": r["rel_type"], **r["rprops"]},
                    {"id": r["tid"], **r["tprops"]},
                )
                for r in results
            ]

            _log.debug(f"Found {len(connections)} connections for {node_id}")
            return connections
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get connections failed [{node_id}]: {err}")
            raise Exception(f"Get connections error: {err}") from exc

    async def get_predecessors(self, node_id: str, edge_label: str = "") -> list[str]:
        """Get nodes pointing to this node."""
        label_clause = f" :{edge_label}" if edge_label else ""
        cypher = f"""
        MATCH (n)<-[r{label_clause}]-(pred)
        WHERE n.id = $nid
        RETURN pred
        """
        results = await self.query(cypher, {"nid": node_id})
        return [r["pred"] for r in results]

    async def get_successors(self, node_id: str, edge_label: str = "") -> list[str]:
        """Get nodes this node points to."""
        label_clause = f" :{edge_label}" if edge_label else ""
        cypher = f"""
        MATCH (n)-[r{label_clause}]->(succ)
        WHERE n.id = $nid
        RETURN succ
        """
        results = await self.query(cypher, {"nid": node_id})
        return [r["succ"] for r in results]

    async def get_disconnected_nodes(self) -> list[str]:
        """Find nodes with no connections."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE NOT (n)--()
        RETURN COLLECT(ID(n)) as ids
        """
        results = await self.query(cypher)
        return results[0]["ids"] if results else []

    # -------------------------------------------------------------------------
    # Subgraph Operations
    # -------------------------------------------------------------------------

    async def extract_typed_subgraph(
        self, node_type: Type[Any], node_name: List[str]
    ) -> Tuple[List[Tuple[int, dict]], List[Tuple[int, int, str, dict]]]:
        """Fetch a subgraph for specific nodes and their neighbors."""
        cypher = f"""
        UNWIND $names AS wanted
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE n.name = wanted AND n.type = $type_name
        WITH collect(DISTINCT n) AS primary
        UNWIND primary AS p
        OPTIONAL MATCH (p)-[r]-(nbr:{self._GRAPH_NODE_LABEL})
        WITH primary, collect(DISTINCT nbr) AS nbrs, collect(DISTINCT r) AS rels
        WITH primary + nbrs AS all_nodes, rels
        UNWIND all_nodes AS node
        WITH collect(DISTINCT node) AS nodes, rels
        MATCH (a:{self._GRAPH_NODE_LABEL})-[r]-(b:{self._GRAPH_NODE_LABEL})
        WHERE a IN nodes AND b IN nodes
        WITH nodes, collect(DISTINCT r) AS all_rels
        RETURN
          [n IN nodes | {{ id: id(n), properties: properties(n) }}] AS rawNodes,
          [r IN all_rels | {{ source_id: id(startNode(r)), target_id: id(endNode(r)), type: type(r), properties: properties(r) }}] AS rawRels
        """

        try:
            results = await self.query(cypher, {"names": node_name, "type_name": node_type.__name__})

            if not results:
                return ([], [])

            raw = results[0]
            nodes = [(n["id"], n["properties"]) for n in raw["rawNodes"]]
            edges = [(r["source_id"], r["target_id"], r["type"], r["properties"]) for r in raw["rawRels"]]

            _log.debug(f"Subgraph: {len(nodes)} nodes, {len(edges)} edges for {node_type.__name__}")
            return (nodes, edges)
        except Exception as exc:
            err = format_neptune_error(exc)
            _log.error(f"Get subgraph failed: {err}")
            raise Exception(f"Get subgraph error: {err}") from exc

    async def get_document_subgraph(self, data_id: str):
        """Retrieve document-related subgraph with chunks and concepts."""
        cypher = f"""
        MATCH (doc)
        WHERE (doc:{self._GRAPH_NODE_LABEL})
        AND doc.type in ['TextDocument', 'PdfDocument']
        AND doc.id = $doc_id

        OPTIONAL MATCH (doc)<-[:is_part_of]-(chunk {{type: 'ContentFragment'}})
        OPTIONAL MATCH (chunk)-[:contains]->(concept)
          WHERE concept.type IN ['Entity', 'Entity']
        OPTIONAL MATCH (concept)<-[:contains]-(otherChunk {{type: 'ContentFragment'}})-[:is_part_of]->(otherDoc)
          WHERE otherDoc.type in ['TextDocument', 'PdfDocument']
          AND otherDoc.id <> doc.id
        OPTIONAL MATCH (chunk)<-[:made_from]-(digest {{type: 'FragmentDigest'}})
        OPTIONAL MATCH (concept)-[:is_a]->(ctype {{type: 'EntityType'}})
        OPTIONAL MATCH (ctype)<-[:is_a]-(otherConcept)<-[:contains]-(otherChunk {{type: 'ContentFragment'}})-[:is_part_of]->(otherDoc)
          WHERE otherConcept.type IN ['Entity', 'Entity']
          WHERE otherDoc.type in ['TextDocument', 'PdfDocument']
          AND otherDoc.id <> doc.id

        WITH doc, concept, chunk, digest, ctype, otherDoc
        WHERE otherDoc IS NULL

        RETURN
            collect(DISTINCT doc) as document,
            collect(DISTINCT chunk) as chunks,
            collect(DISTINCT concept) as orphan_entities,
            collect(DISTINCT digest) as made_from_nodes,
            collect(DISTINCT ctype) as orphan_types
        """

        results = await self.query(cypher, {"doc_id": data_id})
        return results[0] if results else None

    async def query_by_attributes(self, attribute_filters: list[dict[str, list]]) -> Tuple[List[Tuple], List[Tuple]]:
        """Get nodes and edges filtered by attributes."""
        where_n = []
        where_m = []

        for attr, vals in attribute_filters[0].items():
            vals_str = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in vals)
            where_n.append(f"n.{attr} IN [{vals_str}]")
            where_m.append(f"m.{attr} IN [{vals_str}]")

        n_clause = " AND ".join(where_n)
        m_clause = " AND ".join(where_m)
        edge_clause = f"{n_clause} AND {m_clause}"

        node_cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE {n_clause}
        RETURN ID(n) AS id, labels(n) AS labels, properties(n) AS properties
        """

        edge_cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})-[r]->(m:{self._GRAPH_NODE_LABEL})
        WHERE {edge_clause}
        RETURN ID(n) AS source, ID(m) AS target, TYPE(r) AS type, properties(r) AS properties
        """

        node_results = await self.query(node_cypher)
        edge_results = await self.query(edge_cypher)

        nodes = [(r["id"], r["properties"]) for r in node_results]
        edges = [(r["source"], r["target"], r["type"], r["properties"]) for r in edge_results]

        return (nodes, edges)

    async def get_degree_one_nodes(self, node_type: str) -> list:
        """Find nodes with exactly one connection."""
        valid_types = ["Entity", "EntityType"]
        if node_type not in valid_types:
            raise ValueError(f"node_type must be one of {valid_types}")

        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WHERE size((n)--()) = 1 AND n.type = $ntype
        RETURN n
        """

        results = await self.query(cypher, {"ntype": node_type})
        return [r["n"] for r in results] if results else []

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def remove_connection_to_predecessors_of(self, node_ids: list[str], edge_label: str) -> None:
        """Remove outgoing edges of specified label from nodes."""
        cypher = f"""
        UNWIND $ids AS nid
        MATCH ({{`~id`: nid}})-[r:{edge_label}]->(pred)
        DELETE r
        """
        await self.query(cypher, {"ids": node_ids})

    async def remove_connection_to_successors_of(self, node_ids: list[str], edge_label: str) -> None:
        """Remove incoming edges of specified label to nodes."""
        cypher = f"""
        UNWIND $ids AS nid
        MATCH ({{`~id`: nid}})<-[r:{edge_label}]-(succ)
        DELETE r
        """
        await self.query(cypher, {"ids": node_ids})

    # -------------------------------------------------------------------------
    # Schema Operations (Placeholders for GDS - not supported)
    # -------------------------------------------------------------------------

    async def get_node_labels_string(self) -> str:
        """Get all node labels as a string."""
        cypher = "CALL neptune.graph.pg_schema() YIELD schema RETURN schema.nodeLabels as labels"
        results = await self.query(cypher)
        labels = results[0]["labels"] if results else []

        if not labels:
            raise ValueError("No node labels found")

        return str(labels)

    async def get_relationship_labels_string(self) -> str:
        """Get all relationship types as a string."""
        cypher = "CALL neptune.graph.pg_schema() YIELD schema RETURN schema.edgeLabels as relationships"
        results = await self.query(cypher)
        rels = results[0]["relationships"] if results else []

        if not rels:
            raise ValueError("No relationship types found")

        formatted = "{" + ", ".join(f"{r}: {{orientation: 'UNDIRECTED'}}" for r in rels) + "}"
        return formatted

    async def drop_graph(self, graph_name: str = "myGraph") -> None:
        """Placeholder - GDS projection not supported in Neptune Analytics."""
        pass

    async def graph_exists(self, graph_name: str = "myGraph") -> Optional[bool]:
        """Placeholder - GDS projection not supported in Neptune Analytics."""
        pass

    async def project_entire_graph(self, graph_name: str = "myGraph") -> None:
        """Placeholder - GDS projection not supported in Neptune Analytics."""
        pass

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    async def _fetch_basic_counts(self) -> Tuple[int, int]:
        """Get node and edge counts."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        WITH count(n) AS nc
        MATCH (a:{self._GRAPH_NODE_LABEL})-[r]->(b:{self._GRAPH_NODE_LABEL})
        RETURN nc AS numNodes, count(r) AS numEdges
        """

        results = await self.query(cypher)
        return (
            results[0].get("numNodes", 0) if results else 0,
            results[0].get("numEdges", 0) if results else 0,
        )

    async def _analyze_components(self) -> Tuple[int, List[int]]:
        """Analyze connected components."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})
        CALL neptune.algo.wcc(n, {{}})
        YIELD node, component
        RETURN component, count(*) AS size
        ORDER BY size DESC
        """

        results = await self.query(cypher)
        sizes = [r["size"] for r in results] if results else []
        return (len(results), sizes)

    async def _count_loops(self) -> int:
        """Count self-loop edges."""
        cypher = f"""
        MATCH (n:{self._GRAPH_NODE_LABEL})-[r]->(n:{self._GRAPH_NODE_LABEL})
        RETURN count(r) AS loop_count
        """
        results = await self.query(cypher)
        return results[0]["loop_count"] if results else 0

    # Compatibility alias
    @staticmethod
    def _serialize_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
        return _prepare_properties_for_storage(properties)

    @staticmethod
    def _convert_relationship_to_edge(relationship: dict) -> EdgeData:
        return _transform_record_to_edge(relationship)

    # Internal method aliases for compatibility
    async def _get_model_independent_graph_data(self):
        return await self._fetch_basic_counts()

    async def _get_connected_components_stat(self):
        return await self._analyze_components()

    async def _count_self_loops(self):
        return await self._count_loops()
