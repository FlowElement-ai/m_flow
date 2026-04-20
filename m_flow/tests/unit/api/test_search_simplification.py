# m_flow/tests/unit/api/test_search_simplification.py
"""
Unit tests for Search API simplification (P2).

Tests cover:
1. query() function
2. QueryResult dataclass
3. SearchConfig dataclass
4. Mode mapping
"""

import ast
import pathlib
import pytest


# ============================================================
# Test QueryResult Dataclass
# ============================================================


class TestQueryResult:
    """Tests for QueryResult dataclass."""

    def test_query_result_exists(self):
        """Verify QueryResult class exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "class QueryResult" in content

    def test_query_result_has_answer_field(self):
        """Verify answer field exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "answer:" in content

    def test_query_result_has_context_field(self):
        """Verify context field exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "context:" in content

    def test_query_result_has_datasets_field(self):
        """Verify datasets field exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "datasets:" in content

    def test_query_result_has_to_dict(self):
        """Verify to_dict method exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        # Find QueryResult class and check for to_dict
        assert "def to_dict(" in content

    def _get_search_file(self) -> pathlib.Path:
        """Get path to search.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "search.py"


# ============================================================
# Test SearchConfig Dataclass
# ============================================================


class TestSearchConfig:
    """Tests for SearchConfig dataclass."""

    def test_search_config_exists(self):
        """Verify SearchConfig class exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "class SearchConfig" in content

    def test_search_config_has_system_prompt(self):
        """Verify system_prompt field exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "system_prompt" in content

    def _get_search_file(self) -> pathlib.Path:
        """Get path to search.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "search.py"


# ============================================================
# Test query() Function
# ============================================================


class TestQueryFunction:
    """Tests for query() function."""

    def test_query_function_exists(self):
        """Verify query() function exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "async def query(" in content

    def test_query_has_question_param(self):
        """Verify question parameter exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "question: str" in content

    def test_query_has_mode_param(self):
        """Verify mode parameter exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "mode: str =" in content

    def test_query_has_datasets_param(self):
        """Verify datasets parameter exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        # Find query function definition
        assert "datasets:" in content

    def test_query_has_top_k_param(self):
        """Verify top_k parameter exists."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "top_k: int" in content

    def test_query_returns_query_result(self):
        """Verify query returns QueryResult."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert "-> QueryResult" in content

    def _get_search_file(self) -> pathlib.Path:
        """Get path to search.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "search.py"


# ============================================================
# Test Mode Mapping
# ============================================================


class TestModeMapping:
    """Tests for mode string to RecallMode mapping."""

    def test_episodic_mode_supported(self):
        """Verify episodic mode is supported."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert '"episodic"' in content

    def test_triplet_mode_supported(self):
        """Verify triplet mode is supported."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert '"triplet"' in content

    def test_chunks_mode_supported(self):
        """Verify chunks mode is supported."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        assert '"chunks"' in content

    def _get_search_file(self) -> pathlib.Path:
        """Get path to search.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "search.py"


# ============================================================
# Test Exports
# ============================================================


class TestSearchExports:
    """Tests for search module exports."""

    def test_query_exported(self):
        """Verify query is exported."""
        init_file = self._get_init_file()
        content = init_file.read_text()

        assert "query" in content

    def test_query_result_exported(self):
        """Verify QueryResult is exported."""
        init_file = self._get_init_file()
        content = init_file.read_text()

        assert "QueryResult" in content

    def test_search_config_exported(self):
        """Verify SearchConfig is exported."""
        init_file = self._get_init_file()
        content = init_file.read_text()

        assert "SearchConfig" in content

    def _get_init_file(self) -> pathlib.Path:
        """Get path to search __init__.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "__init__.py"


# ============================================================
# Test Syntax
# ============================================================


class TestSearchSyntax:
    """Test search.py has valid Python syntax."""

    def test_valid_python_syntax(self):
        """Verify search.py is valid Python."""
        search_file = self._get_search_file()
        content = search_file.read_text()

        try:
            ast.parse(content)
        except SyntaxError as e:
            pytest.fail(f"search.py has syntax error: {e}")

    def _get_search_file(self) -> pathlib.Path:
        """Get path to search.py file."""
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "search.py"


# ============================================================
# Test the simplified /query HTTP endpoint added for issue #112
# ============================================================


class TestSimplifiedQueryEndpoint:
    """Verify the POST /api/v1/search/query endpoint is wired up."""

    def test_router_declares_query_payload_dto(self):
        """The new endpoint exposes a typed request body."""
        router_file = self._get_router_file()
        content = router_file.read_text()
        assert "class QueryPayloadDTO" in content
        assert "question:" in content
        assert "mode:" in content
        assert "top_k:" in content

    def test_router_declares_query_response_dto(self):
        """The new endpoint declares a typed response."""
        router_file = self._get_router_file()
        content = router_file.read_text()
        assert "class QueryResponseDTO" in content
        assert "answer:" in content
        assert "context:" in content
        assert "datasets:" in content

    def test_router_registers_post_query_endpoint(self):
        """Confirm POST /query is registered on the search router."""
        router_file = self._get_router_file()
        content = router_file.read_text()
        assert '@router.post("/query"' in content
        assert "async def execute_query" in content

    def test_endpoint_delegates_to_search_query(self):
        """The handler must delegate to the existing query() helper."""
        router_file = self._get_router_file()
        content = router_file.read_text()
        # The handler imports and awaits the simplified query() function
        # rather than re-implementing the logic.
        assert "from m_flow.api.v1.search.search import query as query_impl" in content
        assert "await query_impl(" in content

    def test_endpoint_enforces_authentication(self):
        """The handler depends on the authenticated-user fixture."""
        router_file = self._get_router_file()
        content = router_file.read_text()
        # `_auth_dep()` is the project's authenticated-user dependency.
        # Both /search and /search/query must use it so that dataset
        # visibility and permission enforcement match.
        assert content.count("Depends(_auth_dep())") >= 2

    def _get_router_file(self) -> pathlib.Path:
        current = pathlib.Path(__file__)
        mflow_root = current.parent.parent.parent.parent
        return mflow_root / "api" / "v1" / "search" / "routers" / "get_search_router.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
