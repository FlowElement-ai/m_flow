import pathlib
import os
import m_flow
from m_flow.adapters.graph import get_graph_provider
from m_flow.knowledge.graph_ops.m_flow_graph.MemoryGraphElements import Edge
from m_flow.knowledge.graph_ops.utils import resolve_edges_to_text
from m_flow.retrieval.unified_triplet_search import UnifiedTripletSearch
from m_flow.shared.logging_utils import get_logger
from m_flow.search.types import RecallMode
from collections import Counter

logger = get_logger()

# Removed retrievers (functionality discontinued):
# - GraphCompletionContextExtensionRetriever
# - GraphSummaryCompletionRetriever
# - UserQAFeedback (FEEDBACK search type)
# - GraphCompletionCotRetriever (Phase 6, 2026-02-04)


async def main():
    # This test runs for multiple db settings, to run this locally set the corresponding db envs
    await m_flow.prune.prune_data()
    await m_flow.prune.prune_system(metadata=True)

    dataset_name = "test_dataset"

    text_1 = """Germany is located in europe right next to the Netherlands"""
    await m_flow.add(text_1, dataset_name)

    explanation_file_path_quantum = os.path.join(pathlib.Path(__file__).parent, "test_data/Quantum_computers.txt")

    await m_flow.add([explanation_file_path_quantum], dataset_name)

    await m_flow.memorize([dataset_name])

    # create_triplet_embeddings + MemoryTriplet_text assertion removed (Phase 8, 2026-02-06)

    # Test UnifiedTripletSearch
    context_gk = await UnifiedTripletSearch().get_context(query="Next to which country is Germany located?")

    for name, context in [
        ("UnifiedTripletSearch", context_gk),
    ]:
        assert isinstance(context, list), f"{name}: Context should be a list"
        assert len(context) > 0, f"{name}: Context should not be empty"

        context_text = await resolve_edges_to_text(context)
        lower = context_text.lower()
        assert "germany" in lower or "netherlands" in lower, (
            f"{name}: Context did not contain 'germany' or 'netherlands'; got: {context!r}"
        )

    triplets_gk = await UnifiedTripletSearch().get_triplets(query="Next to which country is Germany located?")

    for name, triplets in [
        ("UnifiedTripletSearch", triplets_gk),
    ]:
        assert isinstance(triplets, list), f"{name}: MemoryTriplets should be a list"
        assert triplets, f"{name}: MemoryTriplets list should not be empty"
        for edge in triplets:
            assert isinstance(edge, Edge), f"{name}: Elements should be Edge instances"
            distance = edge.attributes.get("vector_distance")
            node1_distance = edge.node1.attributes.get("vector_distance")
            node2_distance = edge.node2.attributes.get("vector_distance")
            assert isinstance(distance, float), f"{name}: vector_distance should be float, got {type(distance)}"
            assert 0 <= distance <= 1, f"{name}: edge vector_distance {distance} out of [0,1], this shouldn't happen"
            assert 0 <= node1_distance <= 1, (
                f"{name}: node_1 vector_distance {distance} out of [0,1], this shouldn't happen"
            )
            assert 0 <= node2_distance <= 1, (
                f"{name}: node_2 vector_distance {distance} out of [0,1], this shouldn't happen"
            )

    # Test TRIPLET_COMPLETION search type
    completion_gk = await m_flow.search(
        query_type=RecallMode.TRIPLET_COMPLETION,
        query_text="Where is germany located, next to which country?",
        save_interaction=True,
    )

    for name, search_results in [
        ("TRIPLET_COMPLETION", completion_gk),
    ]:
        assert isinstance(search_results, list), f"{name}: should return a list"
        assert len(search_results) == 1, f"{name}: expected single-element list, got {len(search_results)}"

        from m_flow.context_global_variables import backend_access_control_enabled

        if backend_access_control_enabled():
            text = search_results[0]["search_result"][0]
        else:
            text = search_results[0]
        assert isinstance(text, str), f"{name}: element should be a string"
        assert text.strip(), f"{name}: string should not be empty"
        assert "netherlands" in text.lower(), f"{name}: expected 'netherlands' in result, got: {text!r}"

    graph_engine = await get_graph_provider()
    graph = await graph_engine.get_graph_data()

    type_counts = Counter(node_data[1].get("type", {}) for node_data in graph[0])
    edge_type_counts = Counter(edge_type[2] for edge_type in graph[1])

    # Assert there is exactly 1 MflowUserInteraction node
    assert type_counts.get("MflowUserInteraction", 0) == 1, (
        f"Expected exactly one MflowUserInteraction node, but found {type_counts.get('MflowUserInteraction', 0)}"
    )

    # Assert at least one MemorySpace exists (Episodic + Procedural create separate instances).
    assert type_counts.get("MemorySpace", 0) >= 1, (
        f"Expected at least one MemorySpace node, but found {type_counts.get('MemorySpace', 0)}"
    )

    # Assert that there are at least 5 'used_graph_element_to_answer' edges.
    assert edge_type_counts.get("used_graph_element_to_answer", 0) >= 5, (
        f"Expected at least five 'used_graph_element_to_answer' edges, but found {edge_type_counts.get('used_graph_element_to_answer', 0)}"
    )

    nodes = graph[0]

    required_fields_user_interaction = {"question", "answer", "context"}

    for node_id, data in nodes:
        if data.get("type") == "MflowUserInteraction":
            assert required_fields_user_interaction.issubset(data.keys()), (
                f"Node {node_id} is missing fields: {required_fields_user_interaction - set(data.keys())}"
            )

            for field in required_fields_user_interaction:
                value = data[field]
                assert isinstance(value, str) and value.strip(), (
                    f"Node {node_id} has invalid value for '{field}': {value!r}"
                )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
