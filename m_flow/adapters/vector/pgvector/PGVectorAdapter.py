"""
PGVector vector database adapter.

Provides vector storage functionality based on PostgreSQL+pgvector extension.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, List, Optional
from uuid import UUID as _PyUUID

from asyncpg import DeadlockDetectedError, DuplicateTableError, UniqueViolationError
from sqlalchemy import JSON, Column, MetaData, Table, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapped, mapped_column
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from mflow_workers.tasks.queued_add_memory_nodes import queued_add_memory_nodes
from mflow_workers.utils import override_distributed
from m_flow.adapters.exceptions import MissingQueryParameterError
from m_flow.adapters.relational import get_db_adapter
from m_flow.core import MemoryNode
from m_flow.core.utils import parse_id
from m_flow.shared.logging_utils import get_logger

from ...relational.ModelBase import Base
from ...relational.sqlalchemy.SqlAlchemyAdapter import SQLAlchemyAdapter
from ..embeddings.EmbeddingEngine import EmbeddingEngine
from ..exceptions import CollectionNotFoundError
from ..models.VectorSearchHit import VectorSearchHit
from ..utils import normalize_distances
from ..vector_db_interface import VectorProvider
from .serialize_data import serialize_data

_log = get_logger("PGVectorAdapter")

# Safe filter whitelist
# None value means any value is allowed (for dynamic fields like dataset_id)
_FILTER_ALLOWED: dict[str, list[str] | None] = {
    "memory_type": ["atomic", "episodic"],
    "dataset_id": None,  # Allow any UUID value
}


def _obj_to_dict(instance: Any) -> dict:
    """将ORM对象转换为字典"""
    return {col.key: getattr(instance, col.key) for col in inspect(instance).mapper.column_attrs}


def _validate_filter_expr(expr: str) -> tuple[str, str]:
    """验证并解析过滤表达式"""
    if not expr:
        return None, None

    # 值部分使用 [\w-]+ 以支持 UUID（包含连字符）
    pat = r"^payload\.(\w+)\s*=\s*'([\w-]+)'$"
    m = re.match(pat, expr.strip())

    if not m:
        raise ValueError(f"过滤表达式格式无效: '{expr}'。期望格式: payload.field = 'value'")

    fld, val = m.groups()

    if fld not in _FILTER_ALLOWED:
        raise ValueError(f"不允许过滤字段 '{fld}'。允许的字段: {list(_FILTER_ALLOWED.keys())}")

    allowed_values = _FILTER_ALLOWED[fld]
    # None 表示允许任意值（用于 dataset_id 等动态字段）
    if allowed_values is not None and val not in allowed_values:
        raise ValueError(f"字段 '{fld}' 的值 '{val}' 无效。允许的值: {allowed_values}")

    return fld, val


class IndexSchema(MemoryNode):
    """文本索引schema定义"""

    text: str
    # Dataset isolation: 用于 Episode Routing 的 dataset 过滤
    dataset_id: Optional[str] = None
    # Memory type: 用于 atomic/episodic 过滤
    memory_type: Optional[str] = None
    metadata: dict = {"index_fields": ["text"]}


class PGVectorAdapter(SQLAlchemyAdapter, VectorProvider):
    """PGVector向量数据库适配器"""

    def __init__(
        self,
        connection_string: str,
        api_key: Optional[str],
        embedding_engine: EmbeddingEngine,
    ):
        self.api_key = api_key
        self.embedding_engine = embedding_engine
        self.db_uri = connection_string
        self._lock = asyncio.Lock()

        # 获取关系数据库引擎
        rel_db = get_db_adapter()

        # 复用PostgreSQL连接
        if rel_db.engine.dialect.name == "postgresql":
            self.engine = rel_db.engine
            self.sessionmaker = rel_db.sessionmaker
        else:
            self.engine = create_async_engine(self.db_uri)
            self.sessionmaker = async_sessionmaker(bind=self.engine, expire_on_commit=False)

        # 延迟导入Vector类型
        from pgvector.sqlalchemy import Vector

        self.Vector = Vector

    async def embed_data(self, texts: list[str]) -> list[list[float]]:
        """将文本转换为向量"""
        return await self.embedding_engine.embed_text(texts)

    async def has_collection(self, collection_name: str) -> bool:
        """检查集合是否存在"""
        async with self.engine.begin() as conn:
            meta = MetaData()
            await conn.run_sync(meta.reflect)
            return collection_name in meta.tables

    @retry(
        retry=retry_if_exception_type((DuplicateTableError, UniqueViolationError, ProgrammingError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=6),
    )
    async def create_collection(self, collection_name: str, payload_schema=None) -> None:
        """创建向量集合"""
        vec_dim = self.embedding_engine.get_vector_size()

        exists = await self.has_collection(collection_name)
        if exists:
            return

        async with self._lock:
            if await self.has_collection(collection_name):
                return

            # 动态创建表模型
            class _VectorRecord(Base):
                """向量记录模型"""

                __tablename__ = collection_name
                __table_args__ = {"extend_existing": True}

                id: Mapped[_PyUUID] = mapped_column(primary_key=True)
                payload = Column(JSON)
                vector = Column(self.Vector(vec_dim))

                def __init__(self, id, payload, vector):
                    self.id = id
                    self.payload = payload
                    self.vector = vector

            async with self.engine.begin() as conn:
                if Base.metadata.tables:
                    await conn.run_sync(Base.metadata.create_all, tables=[_VectorRecord.__table__])

    @retry(
        retry=retry_if_exception_type(DeadlockDetectedError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=6),
    )
    @override_distributed(queued_add_memory_nodes)
    async def create_memory_nodes(self, collection_name: str, memory_nodes: List[MemoryNode]) -> None:
        """创建或更新内存节点"""
        if not memory_nodes:
            return

        if not await self.has_collection(collection_name):
            await self.create_collection(
                collection_name=collection_name,
                payload_schema=type(memory_nodes[0]),
            )

        vec_dim = self.embedding_engine.get_vector_size()

        # 提取可嵌入数据
        embeddable = [MemoryNode.extract_index_text(n) for n in memory_nodes]

        # 分离有效和无效数据
        valid_pairs = [(idx, txt) for idx, txt in enumerate(embeddable) if txt is not None]
        valid_texts = [txt for _, txt in valid_pairs]

        # 生成向量
        if valid_texts:
            computed = await self.embed_data(valid_texts)
        else:
            computed = []

        # 构建完整向量列表
        empty_vec = [0.0] * vec_dim
        all_vecs: list[list[float]] = []
        computed_idx = 0

        for txt in embeddable:
            if txt is not None:
                all_vecs.append(computed[computed_idx])
                computed_idx += 1
            else:
                all_vecs.append(empty_vec)

        # 动态创建记录模型
        class _VectorRecord(Base):
            """向量记录"""

            __tablename__ = collection_name
            __table_args__ = {"extend_existing": True}

            id: Mapped[_PyUUID] = mapped_column(primary_key=True)
            payload = Column(JSON)
            vector = Column(self.Vector(vec_dim))

            def __init__(self, id, payload, vector):
                self.id = id
                self.payload = payload
                self.vector = vector

        # 构建记录列表
        records = [
            _VectorRecord(
                id=node.id,
                vector=all_vecs[idx],
                payload=serialize_data(node.model_dump()),
            )
            for idx, node in enumerate(memory_nodes)
        ]

        # 执行upsert
        async with self.get_async_session() as sess:
            stmt = insert(_VectorRecord).values([_obj_to_dict(rec) for rec in records])
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "vector": stmt.excluded.vector,
                    "payload": stmt.excluded.payload,
                },
            )
            await sess.execute(stmt)
            await sess.commit()

    async def create_vector_index(self, index_name: str, index_property_name: str) -> None:
        """创建向量索引"""
        full_name = f"{index_name}_{index_property_name}"
        await self.create_collection(full_name)

    async def index_memory_nodes(
        self,
        index_name: str,
        index_property_name: str,
        memory_nodes: list[MemoryNode],
    ) -> None:
        """为内存节点建立索引

        保留 dataset_id 和 memory_type 字段以支持：
        - Episode Routing 的 dataset 隔离过滤
        - Retrieval 的 memory_type 过滤
        """
        full_name = f"{index_name}_{index_property_name}"
        entries = [
            IndexSchema(
                id=node.id,
                text=MemoryNode.extract_index_text(node),
                dataset_id=getattr(node, "dataset_id", None),
                memory_type=getattr(node, "memory_type", None),
            )
            for node in memory_nodes
        ]
        await self.create_memory_nodes(full_name, entries)

    async def get_table(self, collection_name: str) -> Table:
        """动态加载数据库表"""
        async with self.engine.begin() as conn:
            meta = MetaData()
            await conn.run_sync(meta.reflect)

            if collection_name not in meta.tables:
                raise CollectionNotFoundError(f"Collection '{collection_name}' not found!")

            return meta.tables[collection_name]

    async def retrieve(self, collection_name: str, memory_node_ids: List[str]) -> list[VectorSearchHit]:
        """根据ID检索节点"""
        tbl = await self.get_table(collection_name)

        async with self.get_async_session() as sess:
            rows = await sess.execute(select(tbl).where(tbl.c.id.in_(memory_node_ids)))
            rows = rows.all()

            return [VectorSearchHit(id=parse_id(r.id), payload=r.payload, score=0) for r in rows]

    async def search(
        self,
        collection_name: str,
        query_text: Optional[str] = None,
        query_vector: Optional[List[float]] = None,
        limit: Optional[int] = 15,
        with_vector: bool = False,
        where_filter: Optional[str] = None,
    ) -> List[VectorSearchHit]:
        """执行向量相似性搜索"""
        # 参数验证
        if query_text is None and query_vector is None:
            raise MissingQueryParameterError()

        if query_text and query_vector is None:
            embeddings = await self.embedding_engine.embed_text([query_text])
            query_vector = embeddings[0]

        tbl = await self.get_table(collection_name)

        # 处理limit
        if limit is None:
            async with self.get_async_session() as sess:
                cnt_q = select(func.count()).select_from(tbl)
                res = await sess.execute(cnt_q)
                limit = res.scalar_one()

        if limit <= 0:
            return []

        # 构建过滤条件
        filter_clause = None
        if where_filter:
            fld, val = _validate_filter_expr(where_filter)
            if fld and val:
                filter_clause = tbl.c.payload[fld].astext == val

        # 执行搜索
        async with self.get_async_session() as sess:
            q = select(
                tbl,
                tbl.c.vector.cosine_distance(query_vector).label("dist"),
            ).order_by("dist")

            if filter_clause is not None:
                q = q.where(filter_clause)

            if limit > 0:
                q = q.limit(limit)

            raw_results = await sess.execute(q)

        # 构建结果列表
        items = []
        for row in raw_results.all():
            items.append(
                {
                    "id": parse_id(str(row.id)),
                    "payload": row.payload,
                    "_distance": row.dist,
                }
            )

        if not items:
            return []

        # 归一化分数
        scores = normalize_distances(items)
        for idx, score in enumerate(scores):
            items[idx]["score"] = score

        return [
            VectorSearchHit(
                id=item["id"],
                payload=item["payload"],
                score=item["score"],
            )
            for item in items
        ]

    async def batch_search(
        self,
        collection_name: str,
        query_texts: List[str],
        limit: int = None,
        with_vectors: bool = False,
    ) -> list[list[VectorSearchHit]]:
        """批量向量搜索"""
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

    async def delete_memory_nodes(self, collection_name: str, memory_node_ids: list[str]) -> Any:
        """删除指定节点"""
        async with self.get_async_session() as sess:
            tbl = await self.get_table(collection_name)
            result = await sess.execute(delete(tbl).where(tbl.c.id.in_(memory_node_ids)))
            await sess.commit()
            return result

    async def prune(self) -> None:
        """清理数据库"""
        await self.delete_database()
