"""
Temporal Graph Pipeline Integration Test
=========================================
m_flow.tests.test_temporal_graph

Validates time-aware knowledge graph construction across database backends:
- Data ingestion with temporal content
- Memorization producing Episode/Entity/Timestamp nodes
- Temporal search retrieval
- Graph structure validation (nodes, edges, types)
"""

from __future__ import annotations

import asyncio
import pathlib
from collections import Counter

import m_flow
from m_flow.adapters.graph import get_graph_provider
from m_flow.retrieval.unified_triplet_search import UnifiedTripletSearch
from m_flow.search.types import RecallMode
from m_flow.shared.logging_utils import get_logger

_logger = get_logger()
_TEST_DATA = pathlib.Path(__file__).parent / "test_data"

_TEMPORAL_TEXT = """
On January 15, 2024, researchers at MIT published a breakthrough paper on
quantum error correction. The team led by Dr. Sarah Chen demonstrated that
logical qubits could maintain coherence for over 10 seconds using a new
surface code architecture.

In March 2024, Google DeepMind released Gemini 1.5 Pro with a 1-million-token
context window, significantly surpassing previous limits. The model was trained
on a mixture of web text, code, and scientific literature.

During the summer of 2024, OpenAI introduced GPT-4o, a multimodal model capable
of processing text, images, and audio in real time. This represented a major
step forward in unified AI systems.

By December 2024, the European Union's AI Act entered its first enforcement
phase, requiring high-risk AI systems to undergo conformity assessments before
deployment in the EU market.
"""


async def main():
    test_root = pathlib.Path(__file__).parent
    data_dir = (test_root / ".data_storage" / "test_temporal").resolve()
    system_dir = (test_root / ".mflow/system" / "test_temporal").resolve()

    m_flow.config.data_root_directory(str(data_dir))
    m_flow.config.system_root_directory(str(system_dir))

    try:
        await m_flow.prune.prune_data()
        await m_flow.prune.prune_system(metadata=True)

        ds_name = "temporal_events"

        nlp_file = _TEST_DATA / "Natural_language_processing.txt"
        files = [str(nlp_file)] if nlp_file.exists() else []
        files.append(_TEMPORAL_TEXT)

        await m_flow.add(files, ds_name)
        await m_flow.memorize([ds_name])

        graph = await get_graph_provider()
        nodes, edges = await graph.get_graph_data()

        assert len(nodes) > 0, "Graph should contain nodes after memorize"
        assert len(edges) > 0, "Graph should contain edges after memorize"

        type_counts = Counter(data.get("type", "") for _, data in nodes)
        _logger.info("Node type distribution: %s", dict(type_counts))

        assert type_counts.get("Episode", 0) >= 1, (
            f"Expected at least one Episode node, got {type_counts.get('Episode', 0)}"
        )
        assert type_counts.get("Entity", 0) >= 1, (
            f"Expected at least one Entity node, got {type_counts.get('Entity', 0)}"
        )

        search_result = await m_flow.search(
            query_type=RecallMode.TRIPLET_COMPLETION,
            query_text="What happened in quantum computing research?",
        )
        assert len(search_result) > 0, "Search should return results for temporal content"
        _logger.info("Triplet completion: %d results", len(search_result))

        context = await UnifiedTripletSearch().get_context(
            query="When was the EU AI Act enforced?",
        )
        assert isinstance(context, list), "Context should be a list"
        _logger.info("Unified triplet context: %d edges", len(context))

        _logger.info("Temporal graph pipeline test completed")

    finally:
        await m_flow.prune.prune_data()
        await m_flow.prune.prune_system(metadata=True)


if __name__ == "__main__":
    asyncio.run(main())
