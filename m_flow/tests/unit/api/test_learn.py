# m_flow/tests/unit/api/test_learn.py
"""
Unit tests for the learn API module.

Tests cover:
- _create_virtual_chunk_for_episode
- episodes_to_summaries
- fetch_episodes_from_graph
- _create_derived_procedure_edges
"""

import importlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Import the module itself for patch.object (must use importlib to get actual module, not function)
learn_module = importlib.import_module("m_flow.api.v1.learn.learn")
from m_flow.api.v1.learn.learn import (
    _create_virtual_chunk_for_episode,
    episodes_to_summaries,
    fetch_episodes_from_graph,
    _create_derived_procedure_edges,
)
from m_flow.core.domain.models import Episode


class TestCreateVirtualChunkForEpisode:
    """Tests for _create_virtual_chunk_for_episode function."""

    def test_basic_virtual_chunk(self):
        """Test basic virtual chunk creation."""
        episode = Episode(
            id=uuid4(),
            name="Test Episode",
            summary="This is a test summary.",
        )

        chunk = _create_virtual_chunk_for_episode(episode)

        assert chunk.id == episode.id
        assert chunk.text == "This is a test summary."
        assert chunk.chunk_index == 0
        assert chunk.cut_type == "episode"
        assert chunk.metadata["source_type"] == "episode"
        assert chunk.metadata["episode_id"] == str(episode.id)

    def test_virtual_chunk_empty_summary(self):
        """Test virtual chunk with empty summary."""
        episode = Episode(
            id=uuid4(),
            name="Empty Summary Episode",
            summary="",
        )

        chunk = _create_virtual_chunk_for_episode(episode)

        assert chunk.text == ""
        assert chunk.chunk_size == 0

    def test_virtual_chunk_document_properties(self):
        """Test that virtual document has correct properties."""
        episode = Episode(
            id=uuid4(),
            name="Doc Test Episode",
            summary="Content here",
        )

        chunk = _create_virtual_chunk_for_episode(episode)
        doc = chunk.is_part_of

        assert doc.id == episode.id
        assert "[Episode]" in doc.name
        assert doc.processed_path == "memory://episode"
        assert doc.mime_type == "text/episode"


class TestEpisodesToSummaries:
    """Tests for episodes_to_summaries function."""

    @pytest.mark.asyncio
    async def test_empty_episodes(self):
        """Test with empty episodes list."""
        summaries = await episodes_to_summaries([])

        assert summaries == []

    @pytest.mark.asyncio
    async def test_basic_conversion(self):
        """Test basic episode to summary conversion."""
        mock_engine = AsyncMock()
        mock_engine.get_edges.return_value = []

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episode = Episode(
                id=uuid4(),
                name="Test Episode",
                summary="Episode content here.",
            )

            summaries = await episodes_to_summaries([episode])

        assert len(summaries) == 1
        assert summaries[0].text == "Episode content here."
        assert summaries[0].overall_topic == "Test Episode"
        assert summaries[0].metadata["source_episode_id"] == str(episode.id)

    @pytest.mark.asyncio
    async def test_with_facets(self):
        """Test conversion with associated facets."""
        mock_engine = AsyncMock()
        facet_id = str(uuid4())
        ep_id = uuid4()

        # get_edges returns List[Tuple[Dict, str, Dict]]
        mock_engine.get_edges.return_value = [
            (
                {"id": str(ep_id), "type": "Episode"},  # src
                "has_facet",  # rel
                {  # dst
                    "id": facet_id,
                    "type": "Facet",
                    "search_text": "Key detail",
                    "description": "Detailed description",
                },
            ),
        ]

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episode = Episode(
                id=uuid4(),
                name="Episode with Facets",
                summary="Main summary.",
            )

            summaries = await episodes_to_summaries([episode])

        assert len(summaries) == 1
        assert "Main summary." in summaries[0].text
        assert "Key details:" in summaries[0].text
        assert "Key detail" in summaries[0].text

    @pytest.mark.asyncio
    async def test_multiple_episodes(self):
        """Test conversion of multiple episodes."""
        mock_engine = AsyncMock()
        mock_engine.get_edges.return_value = []

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episodes = [Episode(id=uuid4(), name=f"Episode {i}", summary=f"Content {i}") for i in range(3)]

            summaries = await episodes_to_summaries(episodes)

        assert len(summaries) == 3


class TestFetchEpisodesFromGraph:
    """Tests for fetch_episodes_from_graph function."""

    @pytest.mark.asyncio
    async def test_fetch_by_ids(self):
        """Test fetching episodes by specific IDs."""
        mock_engine = AsyncMock()
        ep_id = uuid4()
        mock_engine.get_node.return_value = {
            "id": str(ep_id),
            "type": "Episode",
            "name": "Test Episode",
            "summary": "Summary",
        }

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episodes = await fetch_episodes_from_graph(episode_ids=[ep_id])

        assert len(episodes) == 1
        assert episodes[0].id == ep_id
        assert episodes[0].name == "Test Episode"

    @pytest.mark.asyncio
    async def test_fetch_all(self):
        """Test fetching all episodes without derived_procedure edge."""
        mock_engine = AsyncMock()
        ep_id = uuid4()

        # Mock query_by_attributes to return NodeTuple format: (node_id, props_dict)
        mock_engine.query_by_attributes.return_value = (
            [(str(ep_id), {"name": "Unprocessed Episode", "summary": "Not yet learned", "type": "Episode"})],
            [],
        )
        # get_edges returns List[Tuple[Dict, str, Dict]] - no derived_procedure edge
        mock_engine.get_edges.return_value = []

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episodes = await fetch_episodes_from_graph()

        assert len(episodes) == 1
        assert episodes[0].id == ep_id

    @pytest.mark.asyncio
    async def test_skip_already_processed(self):
        """Test that already processed episodes are skipped."""
        mock_engine = AsyncMock()
        ep_id = uuid4()
        mock_engine.extract_typed_subgraph.return_value = {
            "nodes": [
                {
                    "id": str(ep_id),
                    "type": "Episode",
                    "name": "Already Processed",
                    "summary": "Already learned",
                },
            ]
        }
        mock_engine.get_edges.return_value = [
            {"relationship_name": "derived_procedure", "target_id": str(uuid4())},
        ]

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episodes = await fetch_episodes_from_graph()

        assert len(episodes) == 0

    @pytest.mark.asyncio
    async def test_handles_errors(self):
        """Test error handling during fetch."""
        mock_engine = AsyncMock()
        mock_engine.get_node.side_effect = Exception("Database error")

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            episodes = await fetch_episodes_from_graph(episode_ids=[uuid4()])

        assert episodes == []


class TestCreateDerivedProcedureEdges:
    """Tests for _create_derived_procedure_edges function."""

    @pytest.mark.asyncio
    async def test_no_procedures(self):
        """Test when no procedures are generated."""
        mock_engine = AsyncMock()

        edges = await _create_derived_procedure_edges(
            episodes=[Episode(id=uuid4(), name="Test", summary="Test summary")],
            result=[],
            graph_engine=mock_engine,
        )

        assert edges == 0
        mock_engine.add_edge.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_edges(self):
        """Test edge creation for matched episodes."""
        from m_flow.core.domain.models import Procedure

        mock_engine = AsyncMock()

        ep_id = uuid4()
        proc_id = uuid4()

        episode = Episode(id=ep_id, name="Test Episode", summary="Test summary")
        procedure = MagicMock(spec=Procedure)
        procedure.id = proc_id
        procedure.name = "Test Procedure"
        procedure.source_refs = [f"episode:{ep_id}"]

        edges = await _create_derived_procedure_edges(
            episodes=[episode],
            result=[procedure],
            graph_engine=mock_engine,
        )

        assert edges == 1
        mock_engine.add_edge.assert_called_once()

        # add_edge now uses positional args: (src, dst, rel, props)
        pos_args = mock_engine.add_edge.call_args[0]
        assert pos_args[0] == str(ep_id)
        assert pos_args[1] == str(proc_id)
        assert pos_args[2] == "derived_procedure"

    @pytest.mark.asyncio
    async def test_no_match(self):
        """Test when procedure doesn't reference the episode."""
        from m_flow.core.domain.models import Procedure

        mock_engine = AsyncMock()

        episode = Episode(id=uuid4(), name="Episode", summary="Episode summary")
        procedure = MagicMock(spec=Procedure)
        procedure.id = uuid4()
        procedure.source_refs = []

        edges = await _create_derived_procedure_edges(
            episodes=[episode],
            result=[procedure],
            graph_engine=mock_engine,
        )

        assert edges == 0
        mock_engine.add_edge.assert_not_called()


class TestLearnIntegration:
    """Integration-style tests for the learn function."""

    @pytest.mark.asyncio
    async def test_no_episodes_found(self):
        """Test learn() when no episodes are found."""
        from m_flow.api.v1.learn.learn import learn

        mock_engine = AsyncMock()
        mock_engine.extract_typed_subgraph.return_value = {"nodes": []}

        with patch.object(learn_module, "get_graph_provider", new_callable=AsyncMock) as mock_get_graph_adapter:
            mock_get_graph_adapter.return_value = mock_engine

            result = await learn()

        assert result["status"] == "completed"
        assert result["episodes_processed"] == 0
        assert result["procedures_created"] == 0
