"""
Vector database engine factory.

Creates vector store adapters based on provider configuration.
"""

from __future__ import annotations

from functools import lru_cache

from .embeddings import get_embedding_engine
from .supported_databases import supported_databases


@lru_cache
def create_vector_engine(
    vector_db_provider: str,
    vector_db_url: str,
    vector_db_name: str,
    vector_db_port: str = "",
    vector_db_key: str = "",
    vector_dataset_database_handler: str = "",
):
    """
    Instantiate vector database adapter.

    Supports: PGVector, ChromaDB, LanceDB, Qdrant, Neptune Analytics,
    and any registered custom providers.

    Args:
        vector_db_provider: Provider name.
        vector_db_url: Connection URL.
        vector_db_name: Database name.
        vector_db_port: Connection port.
        vector_db_key: API key.
        vector_dataset_database_handler: Handler class name.

    Returns:
        Configured vector database adapter.

    Raises:
        EnvironmentError: Missing credentials.
        ImportError: Missing dependencies.
    """
    embedder = get_embedding_engine()

    # Check registered providers first
    if vector_db_provider in supported_databases:
        adapter_cls = supported_databases[vector_db_provider]
        return adapter_cls(
            url=vector_db_url,
            api_key=vector_db_key,
            embedding_engine=embedder,
            database_name=vector_db_name,
        )

    provider_lower = vector_db_provider.lower()

    # PGVector - PostgreSQL with vector extension
    if provider_lower == "pgvector":
        return _create_pgvector_adapter(embedder, vector_db_key)

    # ChromaDB - open-source vector store
    if provider_lower == "chromadb":
        return _create_chromadb_adapter(vector_db_url, vector_db_key, embedder)

    # Neptune Analytics - AWS managed service
    if provider_lower == "neptune_analytics":
        return _create_neptune_adapter(vector_db_url, embedder)

    # Qdrant - open-source vector store (remote server)
    if provider_lower == "qdrant":
        from .qdrant.QdrantProvider import QdrantProvider

        return QdrantProvider(
            url=vector_db_url,
            api_key=vector_db_key,
            embedding_engine=embedder,
        )

    # Pinecone - cloud vector store (M-flow exclusive)
    if provider_lower == "pinecone":
        from .pinecone.PineconeProvider import PineconeProvider

        return PineconeProvider(
            api_key=vector_db_key,
            index_name=vector_db_name or "m_flow",
            embedding_engine=embedder,
        )

    # Milvus / Zilliz - distributed vector store (M-flow exclusive)
    if provider_lower == "milvus":
        from .milvus.MilvusProvider import MilvusProvider

        return MilvusProvider(
            uri=vector_db_url,
            token=vector_db_key,
            collection_prefix=vector_db_name or "mflow",
            embedding_engine=embedder,
        )

    # LanceDB - local vector store
    if provider_lower == "lancedb":
        from .lancedb.LanceDBAdapter import LanceDBAdapter

        return LanceDBAdapter(
            url=vector_db_url,
            api_key=vector_db_key,
            embedding_engine=embedder,
        )

    # Unknown provider
    known = list(supported_databases.keys()) + [
        "LanceDB",
        "PGVector",
        "neptune_analytics",
        "ChromaDB",
        "Pinecone",
        "Milvus",
    ]
    raise EnvironmentError(f"Unknown vector provider: {vector_db_provider}. Supported: {', '.join(known)}")


def _create_pgvector_adapter(embedder, api_key: str):
    """Build PGVector adapter from relational config."""
    from m_flow.adapters.relational import get_relational_config

    cfg = get_relational_config()
    required = [cfg.db_host, cfg.db_port, cfg.db_name, cfg.db_username, cfg.db_password]

    if not all(required):
        raise EnvironmentError("Missing PGVector credentials")

    conn_str = f"postgresql+asyncpg://{cfg.db_username}:{cfg.db_password}@{cfg.db_host}:{cfg.db_port}/{cfg.db_name}"

    try:
        from .pgvector.PGVectorAdapter import PGVectorAdapter
    except ImportError:
        raise ImportError("PGVector dependencies missing. Install with: pip install m_flow[postgres]")

    return PGVectorAdapter(conn_str, api_key, embedder)


def _create_chromadb_adapter(url: str, api_key: str, embedder):
    """Build ChromaDB adapter."""
    try:
        import chromadb  # noqa: F401
    except ImportError:
        raise ImportError("ChromaDB not installed. Run: pip install chromadb")

    from .chromadb.ChromaDBAdapter import ChromaDBAdapter

    return ChromaDBAdapter(url=url, api_key=api_key, embedding_engine=embedder)


def _create_neptune_adapter(url: str, embedder):
    """Build Neptune Analytics adapter."""
    try:
        from langchain_aws import NeptuneAnalyticsGraph  # noqa: F401
    except ImportError:
        raise ImportError("langchain_aws not installed. Run: pip install langchain_aws")

    if not url:
        raise EnvironmentError("Neptune endpoint URL required")

    from m_flow.adapters.hybrid.neptune_analytics.NeptuneAnalyticsAdapter import (
        NEPTUNE_ANALYTICS_ENDPOINT_URL,
        NeptuneAnalyticsAdapter,
    )

    if not url.startswith(NEPTUNE_ANALYTICS_ENDPOINT_URL):
        raise ValueError(f"Neptune URL must start with '{NEPTUNE_ANALYTICS_ENDPOINT_URL}'")

    graph_id = url.replace(NEPTUNE_ANALYTICS_ENDPOINT_URL, "")
    return NeptuneAnalyticsAdapter(graph_id=graph_id, embedding_engine=embedder)
