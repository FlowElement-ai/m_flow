"""
Neptune Analytics hybrid adapter.

Unifies vector search and graph operations on AWS Neptune Analytics.
"""

from __future__ import annotations

import asyncio

from m_flow.adapters.exceptions import (
    MissingQueryParameterError,
    MutuallyExclusiveQueryParametersError,
)
from m_flow.adapters.graph.neptune_driver.adapter import NeptuneGraphDB
from m_flow.adapters.vector.embeddings.EmbeddingEngine import EmbeddingEngine
from m_flow.adapters.vector.models.PayloadSchema import PayloadSchema
from m_flow.adapters.vector.models.VectorSearchHit import VectorSearchHit
from m_flow.adapters.vector.vector_db_interface import VectorProvider
from m_flow.core import MemoryNode
from m_flow.shared.logging_utils import get_logger

_log = get_logger("NeptuneAnalyticsAdapter")

# Limits
_TOP_K_MIN = 0
_TOP_K_MAX = 10


class IndexSchema(MemoryNode):
    """Node model for vector index entries."""

    id: str
    text: str
    metadata: dict = {"index_fields": ["text"]}


class NeptuneAnalyticsAdapter(NeptuneGraphDB, VectorProvider):
    """
    Combined vector + graph adapter for Neptune Analytics.

    Extends NeptuneGraphDB and implements VectorProvider.
    """

    _VEC_LABEL = "MFLOW_NODE"
    _COLL_TAG = "VECTOR_COLLECTION"

    def __init__(
        self,
        graph_id: str,
        embedding_engine: EmbeddingEngine | None = None,
        region: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
    ):
        """
        Args:
            graph_id: Neptune Analytics graph identifier.
            embedding_engine: Embedding generator for text->vector.
            region: AWS region.
            aws_access_key_id: AWS key.
            aws_secret_access_key: AWS secret.
            aws_session_token: Temporary credentials token.
        """
        super().__init__(
            graph_id=graph_id,
            region=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )
        self.embedding_engine = embedding_engine
        _log.info("Neptune Analytics adapter ready: graph=%s, region=%s", graph_id, self.region)

    # --------------- VectorProvider compliance ---------------

    async def get_connection(self):
        """Not applicable for Neptune Analytics."""
        return None

    async def embed_data(self, data: list[str]) -> list[list[float]]:
        """Embed text strings into vectors."""
        self._require_embedder()
        return await self.embedding_engine.embed_text(data)

    async def has_collection(self, collection_name: str) -> bool:
        """Always True (vectors stored per-node)."""
        return True

    async def create_collection(
        self,
        collection_name: str,
        payload_schema: PayloadSchema | None = None,
    ) -> None:
        """No-op (node-level storage)."""
        pass

    async def get_collection(self, collection_name: str):
        """Not applicable for Neptune Analytics."""
        return None

    async def create_memory_nodes(
        self,
        collection_name: str,
        memory_nodes: list[MemoryNode],
    ) -> None:
        """
        Insert nodes with embeddings into Neptune Analytics.

        Merges each node and upserts its vector via neptune.algo.vectors.upsert.
        """
        self._require_embedder()

        texts = [MemoryNode.extract_index_text(n) for n in memory_nodes]
        valid_idx_map = [(i, t) for i, t in enumerate(texts) if t is not None]
        valid_texts = [t for _, t in valid_idx_map]

        vec_size = self.embedding_engine.get_vector_size()
        if valid_texts:
            computed = await self.embedding_engine.embed_text(valid_texts)
        else:
            computed = []

        zero_vec = [0.0] * vec_size
        vectors = []
        ptr = 0
        for _i, txt in enumerate(texts):
            if txt is not None:
                vectors.append(computed[ptr])
                ptr += 1
            else:
                vectors.append(zero_vec)

        for idx, node in enumerate(memory_nodes):
            props = self._serialize_properties(node.model_dump())
            props[self._COLL_TAG] = collection_name

            cypher = (
                f"MERGE (n:{self._VEC_LABEL} {{`~id`: $nid}}) "
                "ON CREATE SET n = $props, n.updated_at = timestamp() "
                "ON MATCH SET n += $props, n.updated_at = timestamp() "
                "WITH n, $emb AS emb "
                "CALL neptune.algo.vectors.upsert(n, emb) "
                "YIELD success RETURN success"
            )
            params = {"nid": str(node.id), "props": props, "emb": vectors[idx]}
            try:
                self._client.query(cypher, params)
            except Exception as exc:
                self._handle_error(exc, cypher)

    async def retrieve(
        self,
        collection_name: str,
        memory_node_ids: list[str],
    ) -> list[VectorSearchHit]:
        """Fetch nodes by ID from a collection."""
        cypher = f"MATCH (n:{self._VEC_LABEL}) WHERE id(n) IN $ids AND n.{self._COLL_TAG} = $coll RETURN n AS payload"
        params = {"ids": memory_node_ids, "coll": collection_name}
        try:
            rows = self._client.query(cypher, params)
            return [self._to_scored(r) for r in rows]
        except Exception as exc:
            self._handle_error(exc, cypher)

    async def search(
        self,
        collection_name: str,
        query_text: str | None = None,
        query_vector: list[float] | None = None,
        limit: int | None = None,
        with_vector: bool = False,
        where_filter: str | None = None,
    ) -> list[VectorSearchHit]:
        """
        Perform vector similarity search.

        Supply exactly one of query_text or query_vector.
        Note: where_filter is accepted for interface compatibility but not implemented.
        """
        if where_filter:
            _log.warning("where_filter is not supported by NeptuneAnalyticsAdapter, ignoring.")
        self._require_embedder()

        if with_vector:
            _log.warning("with_vector=True may slow queries due to vector retrieval.")

        if not limit or limit <= _TOP_K_MIN or limit > _TOP_K_MAX:
            _log.warning("Invalid limit (%s), defaulting to %d", limit, _TOP_K_MAX)
            limit = _TOP_K_MAX

        if query_text and query_vector:
            raise MutuallyExclusiveQueryParametersError()
        if query_text is None and query_vector is None:
            raise MissingQueryParameterError()

        if query_vector:
            emb = query_vector
        else:
            vecs = await self.embedding_engine.embed_text([query_text])
            emb = vecs[0]

        cypher = f"""
        CALL neptune.algo.vectors.topKByEmbeddingWithFiltering({{
            topK: {limit},
            embedding: {emb},
            nodeFilter: {{ equals: {{property: '{self._COLL_TAG}', value: '{collection_name}'}} }}
        }})
        YIELD node, score
        """
        if with_vector:
            cypher += """
            WITH node, score
            CALL neptune.algo.vectors.get(node)
            YIELD embedding
            RETURN node AS payload, score, embedding
            """
        else:
            cypher += "RETURN node AS payload, score"

        try:
            rows = self._client.query(cypher, {"embedding": emb, "topK": limit})
            return [self._to_scored(r, include_score=True) for r in rows]
        except Exception as exc:
            self._handle_error(exc, cypher)

    async def batch_search(
        self,
        collection_name: str,
        query_texts: list[str],
        limit: int,
        with_vectors: bool = False,
    ) -> list[list[VectorSearchHit]]:
        """Run multiple searches in parallel."""
        self._require_embedder()
        vecs = await self.embedding_engine.embed_text(query_texts)
        tasks = [self.search(collection_name, None, v, limit, with_vectors) for v in vecs]
        return await asyncio.gather(*tasks)

    async def delete_memory_nodes(
        self,
        collection_name: str,
        memory_node_ids: list[str],
    ) -> None:
        """Remove nodes from a collection."""
        cypher = f"MATCH (n:{self._VEC_LABEL}) WHERE id(n) IN $ids AND n.{self._COLL_TAG} = $coll DETACH DELETE n"
        params = {"ids": memory_node_ids, "coll": collection_name}
        try:
            self._client.query(cypher, params)
        except Exception as exc:
            self._handle_error(exc, cypher)

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        """No-op (indexes are node-based)."""
        await self.create_collection(f"{index_name}_{index_property_name}")

    async def index_memory_nodes(
        self,
        index_name: str,
        index_property_name: str,
        memory_nodes: list[MemoryNode],
    ) -> None:
        """Index nodes using their specified property."""
        coll = f"{index_name}_{index_property_name}"
        items = [
            IndexSchema(
                id=str(n.id),
                text=getattr(n, n.metadata["index_fields"][0]),
            )
            for n in memory_nodes
        ]
        await self.create_memory_nodes(coll, items)

    async def prune(self) -> None:
        """Delete all vector nodes."""
        self._client.query(f"MATCH (n:{self._VEC_LABEL}) DETACH DELETE n")

    async def is_empty(self) -> bool:
        """Check if graph has any nodes."""
        rows = await self._client.query("MATCH (n) RETURN true LIMIT 1")
        return len(rows) == 0

    # --------------- Helpers ---------------

    def _require_embedder(self) -> None:
        if self.embedding_engine is None:
            raise ValueError("Embedding engine required for vector operations")

    def _handle_error(self, exc: Exception, query: str) -> None:
        _log.error("Neptune query failed: %s | %s", exc, query)
        raise exc

    @staticmethod
    def _to_scored(
        row: dict,
        include_vec: bool = False,
        include_score: bool = False,
    ) -> VectorSearchHit:
        payload = row.get("payload", {})
        return VectorSearchHit(
            id=payload.get("~id"),
            payload=payload.get("~properties"),
            score=row.get("score", 0) if include_score else 0,
            vector=row.get("embedding") if include_vec else None,
        )
