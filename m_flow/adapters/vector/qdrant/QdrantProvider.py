from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

from m_flow.core import MemoryNode
from m_flow.core.utils.parse_id import parse_id
from m_flow.shared.logging_utils import get_logger
from m_flow.storage.utils_mod.utils import get_own_properties

from ..exceptions import CollectionNotFoundError
from ..models.VectorSearchHit import VectorSearchHit
from ..vector_db_interface import VectorProvider

_log = get_logger(__name__)

_PREFIX = "mflow"

_FILTER_WHITELIST: Dict[str, Optional[List[str]]] = {
    "memory_type": ["atomic", "episodic"],
    "dataset_id": None,
}


def _check_filter_security(filter_expr: str) -> tuple[str, str]:
    matched = re.match(r"^payload\.(\w+)\s*=\s*'([\w-]+)'$", filter_expr.strip())
    if not matched:
        raise ValueError(f"过滤表达式格式无效: '{filter_expr}'。期望格式: payload.field = 'value'")

    fld, val = matched.groups()

    if fld not in _FILTER_WHITELIST:
        raise ValueError(f"不允许过滤字段 '{fld}'。允许的字段: {list(_FILTER_WHITELIST.keys())}")

    allowed_values = _FILTER_WHITELIST[fld]
    if allowed_values is not None and val not in allowed_values:
        raise ValueError(f"字段 '{fld}' 的值 '{val}' 无效。允许的值: {allowed_values}")

    return fld, val


def _point_id(raw_id: Any) -> str:
    text = str(raw_id)
    try:
        return str(UUID(text))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"mflow:{text}"))


class IndexSchema(MemoryNode):
    text: str
    dataset_id: Optional[str] = None
    memory_type: Optional[str] = None
    metadata: dict = {"index_fields": ["text"]}


class QdrantProvider(VectorProvider):
    """
    Qdrant vector store adapter.

    Requires: `pip install m_flow[qdrant]`
    """

    name = "Qdrant"

    def __init__(self, url: str = "", api_key: str = "", embedding_engine: Any = None, **kwargs):
        self.embedding_engine = embedding_engine

        try:
            from qdrant_client import AsyncQdrantClient, models
        except ImportError as exc:
            raise ImportError("qdrant-client is required. Install with: pip install m_flow[qdrant]") from exc

        self._models = models

        url = url or os.getenv("VECTOR_DB_URL", "")
        api_key = api_key or os.getenv("VECTOR_DB_KEY", "")

        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        _log.info("Qdrant connected (remote): url=%s", url)

    def _col(self, name: str) -> str:
        return f"{_PREFIX}_{name}"

    def _dim(self) -> int:
        return int(self.embedding_engine.get_vector_size())

    async def _ensure_collection(self, collection_name: str) -> None:
        col = self._col(collection_name)
        if await self.has_collection(collection_name):
            return

        await self._client.create_collection(
            collection_name=col,
            vectors_config=self._models.VectorParams(size=self._dim(), distance=self._models.Distance.COSINE),
        )
        _log.info("Qdrant collection created: %s", col)

    async def has_collection(self, collection_name: str) -> bool:
        cols = await self._client.get_collections()
        return self._col(collection_name) in {c.name for c in cols.collections}

    async def create_collection(self, collection_name: str, payload_schema=None) -> None:
        await self._ensure_collection(collection_name)

    async def create_memory_nodes(
        self,
        collection_name: str,
        memory_nodes: List["MemoryNode"],
    ) -> None:
        if not memory_nodes:
            return

        await self._ensure_collection(collection_name)
        col = self._col(collection_name)

        texts = [MemoryNode.extract_index_text(node) for node in memory_nodes]
        valid_texts = [t for t in texts if t]
        computed_vecs = await self.embed_data(valid_texts) if valid_texts else []

        empty_vec = [0.0] * self._dim()
        vec_by_text = dict(zip(valid_texts, computed_vecs))

        points = []
        for node, text in zip(memory_nodes, texts):
            payload = self._sanitize_payload(get_own_properties(node))
            points.append(
                self._models.PointStruct(
                    id=_point_id(node.id),
                    vector=vec_by_text.get(text, empty_vec) if text else empty_vec,
                    payload=payload,
                )
            )

        await self._client.upsert(collection_name=col, points=points)
        _log.info("Qdrant upserted %d points to %s", len(points), col)

    async def retrieve(self, collection_name: str, memory_node_ids: List[str]) -> List[VectorSearchHit]:
        if not memory_node_ids:
            return []
        await self._require_collection(collection_name)
        found = await self._client.retrieve(
            collection_name=self._col(collection_name),
            ids=[_point_id(i) for i in memory_node_ids],
            with_payload=True,
        )
        return [VectorSearchHit(id=parse_id(str(p.id)), score=0.0, payload=dict(p.payload or {})) for p in found]

    async def delete_memory_nodes(self, collection_name: str, memory_node_ids: List[str]) -> None:
        await self._require_collection(collection_name)
        await self._client.delete(
            collection_name=self._col(collection_name),
            points_selector=self._models.PointIdsList(points=[_point_id(i) for i in memory_node_ids]),
        )

    async def search(
        self,
        collection_name: str,
        query_text: Optional[str] = None,
        query_vector: Optional[List[float]] = None,
        limit: Optional[int] = 15,
        with_vector: bool = False,
        where_filter: Optional[str] = None,
    ) -> List[VectorSearchHit]:
        from m_flow.adapters.exceptions import MissingQueryParameterError

        if query_text is None and query_vector is None:
            raise MissingQueryParameterError()

        if query_text and query_vector is None:
            embeddings = await self.embedding_engine.embed_text([query_text])
            if not embeddings or embeddings[0] is None:
                return []
            query_vector = embeddings[0]

        await self._require_collection(collection_name)
        col = self._col(collection_name)

        if limit is None:
            limit = (await self._client.count(collection_name=col, exact=True)).count
        if limit <= 0:
            return []

        query_filter = None
        if where_filter:
            fld, val = _check_filter_security(where_filter)
            query_filter = self._models.Filter(
                must=[self._models.FieldCondition(key=fld, match=self._models.MatchValue(value=val))]
            )

        response = await self._client.query_points(
            collection_name=col,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=with_vector,
        )

        hits: List[VectorSearchHit] = []
        for p in response.points:
            similarity = max(-1.0, min(1.0, float(p.score)))
            hit = VectorSearchHit(
                id=parse_id(str(p.id)),
                score=(1.0 - similarity) / 2.0,
                payload=dict(p.payload or {}),
                raw_distance=max(0.0, 1.0 - similarity),
                collection_name=collection_name,
            )
            if with_vector:
                hit.vector = list(p.vector) if p.vector is not None else None
            hits.append(hit)
        return hits

    async def batch_search(
        self,
        collection_name: str,
        query_texts: List[str],
        limit: Optional[int] = None,
        with_vectors: bool = False,
    ) -> List[List[VectorSearchHit]]:
        if not query_texts:
            return []
        vectors = await self.embedding_engine.embed_text(query_texts)
        tasks = [
            self.search(
                collection_name=collection_name,
                query_vector=vec,
                limit=limit,
                with_vector=with_vectors,
            )
            for vec in vectors
        ]
        return await asyncio.gather(*tasks)

    async def embed_data(self, data: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = await self.embedding_engine.embed_text(data)
        return vectors

    async def prune(self) -> None:
        cols = await self._client.get_collections()
        for info in cols.collections:
            if info.name.startswith(f"{_PREFIX}_"):
                await self._client.delete_collection(info.name)
        _log.info("Qdrant pruned all %s_* collections", _PREFIX)

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        await self._ensure_collection(f"{index_name}_{index_property_name}")

    async def index_memory_nodes(
        self,
        index_name: str,
        index_property_name: str,
        memory_nodes: List["MemoryNode"],
    ) -> None:
        entries = []
        for node in memory_nodes:
            text = MemoryNode.extract_index_text(node)
            if not text:
                continue  # nothing indexable - avoid junk zero-vector points
            entries.append(
                IndexSchema(
                    id=node.id,
                    text=text,
                    dataset_id=str(ds) if (ds := getattr(node, "dataset_id", None)) else None,
                    memory_type=getattr(node, "memory_type", None),
                )
            )
        if entries:
            await self.create_memory_nodes(f"{index_name}_{index_property_name}", entries)

    async def get_connection(self) -> Any:
        return self._client

    async def _require_collection(self, collection_name: str) -> None:
        if not await self.has_collection(collection_name):
            raise CollectionNotFoundError(f"Collection '{self._col(collection_name)}' not found!")

    @staticmethod
    def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        for key, value in payload.items():
            if value is None or isinstance(value, (bool, int, float, str)):
                clean[key] = value
            elif isinstance(value, UUID):
                clean[key] = str(value)
            elif isinstance(value, list):
                clean[key] = [str(v) if not isinstance(v, (bool, int, float, str)) else v for v in value]
            else:
                clean[key] = str(value)
        return clean
