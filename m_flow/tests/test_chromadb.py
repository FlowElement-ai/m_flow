"""
ChromaDB Integration Test Module
================================
m_flow.tests.test_chromadb

Validates ChromaDB vector database functionality including:
- Vector storage and retrieval operations
- Collection management and cleanup
- Integration with m_flow memorization pipeline
- Search operations across different recall modes
"""

import pathlib
import hashlib
import os

import m_flow
from m_flow.shared.logging_utils import get_logger
from m_flow.shared.files.storage import get_storage_config
from m_flow.data.models import Data
from m_flow.auth.methods import get_seed_user
from m_flow.search.types import RecallMode
from m_flow.search.operations import get_history

_logger = get_logger()


async def validate_local_file_cleanup(text_content: str, external_file_path: str):
    """
    Validates that local file deletion behaves correctly.

    This test verifies two scenarios:
    1. Files created by m_flow should be deleted along with data entities
    2. External files referenced by m_flow should NOT be deleted

    Args:
        text_content: The text content that was added to m_flow
        external_file_path: Path to an external file added to m_flow
    """
    from sqlalchemy import select
    from m_flow.adapters.relational import get_db_adapter

    db_engine = get_db_adapter()

    # Scenario 1: Internal files should be cleaned up
    async with db_engine.get_async_session() as db_session:
        content_hash = hashlib.md5(text_content.encode("utf-8")).hexdigest()

        query_result = await db_session.scalars(select(Data).where(Data.content_hash == content_hash))
        data_record = query_result.one()

        file_path = data_record.processed_path.replace("file://", "")
        assert os.path.isfile(file_path), f"Expected file at: {data_record.processed_path}"

        await db_engine.delete_data_entity(data_record.id)

        assert not os.path.exists(file_path), f"File should be deleted: {data_record.processed_path}"

    # Scenario 2: External files should remain untouched
    async with db_engine.get_async_session() as db_session:
        query_result = await db_session.scalars(select(Data).where(Data.processed_path == external_file_path))
        data_record = query_result.one()

        file_path = data_record.processed_path.replace("file://", "")
        assert os.path.isfile(file_path), f"Expected file at: {data_record.processed_path}"

        await db_engine.delete_data_entity(data_record.id)

        assert os.path.exists(file_path), f"External file should NOT be deleted: {data_record.processed_path}"


async def validate_document_retrieval(target_dataset: str):
    """
    Validates document retrieval for search operations.

    Tests both filtered and unfiltered document retrieval scenarios.
    """
    from m_flow.auth.permissions.methods import get_document_ids_for_user

    current_user = await get_seed_user()

    # Filtered retrieval - should return only documents in specified dataset
    filtered_docs = await get_document_ids_for_user(current_user.id, [target_dataset])
    assert len(filtered_docs) == 1, f"Expected 1 document in dataset, found {len(filtered_docs)}"

    # Unfiltered retrieval - should return all accessible documents
    all_docs = await get_document_ids_for_user(current_user.id)
    assert len(all_docs) == 2, f"Expected 2 total documents, found {len(all_docs)}"


async def validate_unlimited_vector_search():
    """
    Ensures vector search with no limit returns all matching results.

    This test guards against accidental default limits in the search pipeline.
    """
    test_dir = pathlib.Path(__file__).parent
    quantum_file = test_dir / "test_data" / "Quantum_computers.txt"
    nlp_file = test_dir / "test_data" / "Natural_language_processing.txt"

    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)

    await m_flow.add(str(quantum_file))
    await m_flow.add(str(nlp_file))
    await m_flow.memorize()

    from m_flow.adapters.vector import get_vector_provider

    vec_engine = get_vector_provider()

    search_query = "Tell me about Quantum computers"
    query_embedding = (await vec_engine.embedding_engine.embed_text([search_query]))[0]

    unlimited_results = await vec_engine.search(
        collection_name="Entity_name",
        query_vector=query_embedding,
        limit=None,
    )

    # Verify no hidden limits were applied (common defaults: 5, 10, 15)
    assert len(unlimited_results) > 15, f"Unlimited search returned only {len(unlimited_results)} results"


async def main():
    """
    Main test execution for ChromaDB integration.

    Configures ChromaDB as vector backend and runs comprehensive tests.
    """
    # Configure ChromaDB connection
    # ChromaDB has no dataset-level partition handler, so disable access control
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
    m_flow.config.set_vector_db_config(
        {
            "vector_db_url": "http://localhost:3002",
            "vector_db_key": "test-token",
            "vector_db_provider": "chromadb",
        }
    )

    # Setup test directories
    test_base = pathlib.Path(__file__).parent
    data_storage = (test_base / ".data_storage" / "test_chromadb").resolve()
    system_storage = (test_base / ".mflow/system" / "test_chromadb").resolve()

    m_flow.config.data_root_directory(str(data_storage))
    m_flow.config.system_root_directory(str(system_storage))

    # Clean state
    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)

    # Prepare test datasets
    nlp_dataset = "natural_language"
    quantum_dataset = "quantum"

    nlp_file = test_base / "test_data" / "Natural_language_processing.txt"
    quantum_file = test_base / "test_data" / "Quantum_computers.txt"

    await m_flow.add([str(nlp_file)], nlp_dataset)
    await m_flow.add([str(quantum_file)], quantum_dataset)
    await m_flow.memorize([quantum_dataset, nlp_dataset])

    # Run document retrieval tests
    await validate_document_retrieval(nlp_dataset)

    # Test search functionality
    from m_flow.adapters.vector import get_vector_provider

    vec_engine = get_vector_provider()

    concept_results = await vec_engine.search("Entity_name", "Quantum computer")
    sample_concept = concept_results[0].payload["text"]

    # Test TRIPLET_COMPLETION search
    graph_results = await m_flow.search(
        query_type=RecallMode.TRIPLET_COMPLETION,
        query_text=sample_concept,
    )
    assert len(graph_results) > 0, "TRIPLET_COMPLETION search returned no results"
    _logger.info("Graph completion results: %d items", len(graph_results))

    # Test EPISODIC search with dataset filter
    episodic_results = await m_flow.search(
        query_type=RecallMode.EPISODIC,
        query_text=sample_concept,
        datasets=[quantum_dataset],
    )
    assert len(episodic_results) > 0, "EPISODIC search returned no results"
    _logger.info("Episodic search results: %d items", len(episodic_results))

    # Test filtered graph completion
    filtered_completion = await m_flow.search(
        query_type=RecallMode.TRIPLET_COMPLETION,
        query_text=sample_concept,
        datasets=[quantum_dataset],
    )
    assert len(filtered_completion) > 0, "Filtered completion returned no results"

    # Verify search history
    current_user = await get_seed_user()
    search_history = await get_history(current_user.id)
    assert len(search_history) == 8, f"Expected 8 history entries, found {len(search_history)}"

    # Cleanup and verify
    await m_flow.prune.prune_data()
    storage_config = get_storage_config()
    assert not os.path.isdir(storage_config["data_root_directory"]), "Data directory should be deleted after prune"

    await m_flow.prune.prune_system(metadata=True)
    remaining_collections = await vec_engine.get_collection_names()
    assert len(remaining_collections) == 0, f"ChromaDB should be empty, found {len(remaining_collections)} collections"

    # Run unlimited search validation
    await validate_unlimited_vector_search()

    _logger.info("ChromaDB integration tests completed successfully")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
