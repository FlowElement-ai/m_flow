"""Tests for remote-mode `query()` and `prune_*()` (issue #112).

In remote / API mode the MCP client previously raised NotImplementedError for
these operations. With the fix in place each call now forwards to a real
backend endpoint:

* ``MflowClient.query()``        -> ``POST /api/v1/search/query``
* ``MflowClient.prune_data()``   -> ``POST /api/v1/prune/data``
* ``MflowClient.prune_system()`` -> ``POST /api/v1/prune/system``

These tests stub the underlying ``httpx.AsyncClient`` so we can verify the
correct URL, payload, auth header, and response handling without exercising
the live FastAPI service.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from m_flow_client import MflowClient  # noqa: E402  -- after sys.path tweak


# ---------------------------------------------------------------------------
# Stub HTTP layer
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code: int = 200, payload: Any | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("POST", "https://example.com"),
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


class _StubHTTP:
    """Recording stub for ``httpx.AsyncClient`` used by remote-mode calls."""

    def __init__(self, *responses: _StubResponse) -> None:
        # Allow either a single response (used for every call) or a queue of
        # responses (popped in order). Defaults to a 200/{} reply.
        if not responses:
            responses = (_StubResponse(),)
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _StubResponse:
        self.calls.append(("POST", url, json, headers))
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def _make_remote_client(token: str = "admin-token") -> MflowClient:
    client = MflowClient(server_url="https://api.example.com", auth_token=token)
    return client


# ---------------------------------------------------------------------------
# query() — remote happy paths
# ---------------------------------------------------------------------------


def test_remote_query_posts_simplified_payload_and_returns_answer() -> None:
    async def run() -> None:
        client = _make_remote_client()
        client._http = _StubHTTP(_StubResponse(200, {"answer": "Paris", "context": [], "datasets": ["geo"]}))

        result = await client.query(question="Capital of France?", datasets=["geo"], mode="triplet", top_k=5)

        assert result == "Paris"
        method, url, payload, headers = client._http.calls[0]
        assert method == "POST"
        assert url == "https://api.example.com/api/v1/search/query"
        assert payload == {
            "question": "Capital of France?",
            "mode": "triplet",
            "top_k": 5,
            "datasets": ["geo"],
        }
        assert headers["Authorization"] == "Bearer admin-token"
        assert headers["Content-Type"] == "application/json"

    asyncio.run(run())


def test_remote_query_returns_context_when_no_answer() -> None:
    async def run() -> None:
        client = _make_remote_client(token="t")
        client._http = _StubHTTP(
            _StubResponse(200, {"answer": None, "context": ["episode-1", "episode-2"], "datasets": []})
        )

        result = await client.query(question="Recent events?", mode="episodic")

        # Without an LLM-generated answer, the context list is rendered as a
        # string for downstream MCP TextContent rendering.
        assert "episode-1" in result
        assert "episode-2" in result

    asyncio.run(run())


def test_remote_query_omits_datasets_when_not_provided() -> None:
    async def run() -> None:
        client = _make_remote_client(token="t")
        client._http = _StubHTTP(_StubResponse(200, {"answer": "ok", "context": [], "datasets": []}))

        await client.query(question="anything?")

        _, _, payload, _ = client._http.calls[0]
        assert "datasets" not in payload  # absent rather than null
        assert payload["mode"] == "episodic"
        assert payload["top_k"] == 10

    asyncio.run(run())


# ---------------------------------------------------------------------------
# prune_data() / prune_system() — remote happy paths
# ---------------------------------------------------------------------------


def test_remote_prune_data_posts_to_correct_endpoint_with_confirm() -> None:
    async def run() -> None:
        client = _make_remote_client()
        client._http = _StubHTTP(
            _StubResponse(200, {"status": "completed", "cleared": {"file_storage": True}, "message": "ok"})
        )

        result = await client.prune_data()

        assert result["status"] == "completed"
        method, url, payload, headers = client._http.calls[0]
        assert method == "POST"
        assert url == "https://api.example.com/api/v1/prune/data"
        assert payload == {"confirm": "DELETE_FILES"}
        assert headers["Authorization"] == "Bearer admin-token"

    asyncio.run(run())


def test_remote_prune_system_forwards_component_flags_and_confirmation() -> None:
    async def run() -> None:
        client = _make_remote_client()
        client._http = _StubHTTP(
            _StubResponse(
                200,
                {
                    "status": "completed",
                    "cleared": {
                        "graph_database": True,
                        "vector_database": False,
                        "relational_database": True,
                        "cache": True,
                    },
                    "message": "ok",
                },
            )
        )

        result = await client.prune_system(graph=True, vector=False, metadata=True, cache=True)

        assert result["status"] == "completed"
        _, url, payload, _ = client._http.calls[0]
        assert url == "https://api.example.com/api/v1/prune/system"
        assert payload == {
            "confirm": "DELETE_SYSTEM",
            "graph": True,
            "vector": False,
            "metadata": True,
            "cache": True,
        }

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Error surfacing: HTTP errors propagate as httpx.HTTPStatusError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_call",
    [
        lambda c: c.prune_data(),
        lambda c: c.prune_system(),
        lambda c: c.query(question="hi"),
    ],
    ids=["prune_data", "prune_system", "query"],
)
def test_remote_calls_raise_on_non_2xx(fn_call) -> None:
    async def run() -> None:
        client = _make_remote_client()
        client._http = _StubHTTP(_StubResponse(403, {"detail": "Prune API is disabled"}))

        with pytest.raises(httpx.HTTPStatusError):
            await fn_call(client)

    asyncio.run(run())
