import pathlib
import os

import m_flow
from m_flow.shared.logging_utils import get_logger
from m_flow.shared.files.storage import get_storage_config
from m_flow.auth.methods import get_seed_user
from m_flow.search.types import RecallMode
from m_flow.search.operations import get_history

_logger = get_logger()

_QDRANT_URL = os.environ.get("VECTOR_DB_URL", "http://localhost:6333")


async def validate_document_retrieval(target_dataset: str):
    from m_flow.auth.permissions.methods import get_document_ids_for_user

    current_user = await get_seed_user()

    filtered_docs = await get_document_ids_for_user(current_user.id, [target_dataset])
    assert len(filtered_docs) == 1, f"Expected 1 document in dataset, found {len(filtered_docs)}"

    all_docs = await get_document_ids_for_user(current_user.id)
    assert len(all_docs) == 2, f"Expected 2 total documents, found {len(all_docs)}"


async def validate_unlimited_vector_search():
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

    assert len(unlimited_results) > 15, f"Unlimited search returned only {len(unlimited_results)} results"


async def main():
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
    m_flow.config.set_vector_db_config(
        {
            "vector_db_url": _QDRANT_URL,
            "vector_db_provider": "qdrant",
        }
    )

    test_base = pathlib.Path(__file__).parent
    data_storage = (test_base / ".data_storage" / "test_qdrant").resolve()
    system_storage = (test_base / ".mflow/system" / "test_qdrant").resolve()

    m_flow.config.data_root_directory(str(data_storage))
    m_flow.config.system_root_directory(str(system_storage))

    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)

    nlp_dataset = "natural_language"
    quantum_dataset = "quantum"

    nlp_file = test_base / "test_data" / "Natural_language_processing.txt"
    quantum_file = test_base / "test_data" / "Quantum_computers.txt"

    await m_flow.add([str(nlp_file)], nlp_dataset)
    await m_flow.add([str(quantum_file)], quantum_dataset)
    await m_flow.memorize([quantum_dataset, nlp_dataset])

    await validate_document_retrieval(nlp_dataset)

    from m_flow.adapters.vector import get_vector_provider

    vec_engine = get_vector_provider()

    concept_results = await vec_engine.search("Entity_name", "Quantum computer")
    sample_concept = concept_results[0].payload["text"]

    graph_results = await m_flow.search(
        query_type=RecallMode.TRIPLET_COMPLETION,
        query_text=sample_concept,
    )
    assert len(graph_results) > 0, "TRIPLET_COMPLETION search returned no results"
    _logger.info("Graph completion results: %d items", len(graph_results))

    episodic_results = await m_flow.search(
        query_type=RecallMode.EPISODIC,
        query_text=sample_concept,
        datasets=[quantum_dataset],
    )
    assert len(episodic_results) > 0, "EPISODIC search returned no results"
    _logger.info("Episodic search results: %d items", len(episodic_results))

    filtered_completion = await m_flow.search(
        query_type=RecallMode.TRIPLET_COMPLETION,
        query_text=sample_concept,
        datasets=[quantum_dataset],
    )
    assert len(filtered_completion) > 0, "Filtered completion returned no results"

    current_user = await get_seed_user()
    search_history = await get_history(current_user.id)
    assert len(search_history) >= 6, (
        f"Expected at least 6 history entries (3 searches × user+system), found {len(search_history)}"
    )

    await m_flow.prune.prune_data()
    storage_config = get_storage_config()
    assert not os.path.isdir(storage_config["data_root_directory"]), "Data directory should be deleted after prune"

    await m_flow.prune.prune_system(metadata=True)
    for coll in ["Entity_name", "Episode_summary", "ContentFragment_text"]:
        assert not await vec_engine.has_collection(coll), f"Vector collection '{coll}' still exists after cleanup"

    await validate_unlimited_vector_search()

    _logger.info("Qdrant integration tests completed successfully")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
