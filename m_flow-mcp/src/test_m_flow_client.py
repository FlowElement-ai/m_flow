from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from m_flow_client import MflowClient


def test_direct_search_forwards_datasets_to_engine() -> None:
    async def run() -> None:
        client = object.__new__(MflowClient)
        client._remote = False

        captured: dict[str, object] = {}

        async def fake_search(**kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"status": "ok"}

        client._engine = SimpleNamespace(search=fake_search)

        result = await client.search(
            query_text="where is alpha",
            query_type="EPISODIC",
            datasets=["alpha"],
            top_k=3,
        )

        assert result == {"status": "ok"}
        assert captured["datasets"] == ["alpha"]
        assert captured["top_k"] == 3

    asyncio.run(run())


class DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class RecordingAsyncClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict, dict]] = []
        self.patches: list[tuple[str, dict, list, dict]] = []

    async def post(self, url: str, json: dict, headers: dict) -> DummyResponse:
        self.posts.append((url, json, headers))
        return DummyResponse({"success": True, "message": "started"})

    async def patch(self, url: str, params: dict, files: list, headers: dict) -> DummyResponse:
        self.patches.append((url, params, files, headers))
        return DummyResponse({"success": True, "message": "updated"})


def test_remote_update_uses_multipart_without_json_content_type() -> None:
    async def run() -> None:
        client = MflowClient(server_url="https://example.com", auth_token="secret")
        client._http = RecordingAsyncClient()

        result = await client.update(
            data_id="11111111-1111-1111-1111-111111111111",
            dataset_id="22222222-2222-2222-2222-222222222222",
            data="patched content",
        )

        assert result == {"success": True, "message": "updated"}
        assert client._http.patches

        url, params, files, headers = client._http.patches[0]
        assert url == "https://example.com/api/v1/update"
        assert params == {
            "data_id": "11111111-1111-1111-1111-111111111111",
            "dataset_id": "22222222-2222-2222-2222-222222222222",
        }
        assert headers == {"Authorization": "Bearer secret"}
        assert files[0][0] == "data"

    asyncio.run(run())


def test_remote_learn_targets_requested_dataset_names() -> None:
    async def run() -> None:
        client = MflowClient(server_url="https://example.com", auth_token="secret")
        client._http = RecordingAsyncClient()

        async def fake_list_datasets() -> list[dict[str, str]]:
            return [
                {"id": "11111111-1111-1111-1111-111111111111", "name": "alpha"},
                {"id": "22222222-2222-2222-2222-222222222222", "name": "beta"},
            ]

        client.list_datasets = fake_list_datasets

        result = await client.learn(datasets=["beta"])

        assert result == {"success": True, "message": "started"}
        assert client._http.posts == [
            (
                "https://example.com/api/v1/procedural/extract-from-episodic",
                {
                    "dataset_id": "22222222-2222-2222-2222-222222222222",
                    "limit": 100,
                    "force_reprocess": False,
                    "run_in_background": False,
                },
                {"Content-Type": "application/json", "Authorization": "Bearer secret"},
            )
        ]

    asyncio.run(run())


def test_remote_learn_raises_for_unknown_dataset_names() -> None:
    async def run() -> None:
        client = MflowClient(server_url="https://example.com")

        async def fake_list_datasets() -> list[dict[str, str]]:
            return [{"id": "11111111-1111-1111-1111-111111111111", "name": "alpha"}]

        client.list_datasets = fake_list_datasets

        with pytest.raises(ValueError, match="Unknown dataset"):
            await client.learn(datasets=["missing"])

    asyncio.run(run())


def test_remote_learn_forwards_run_in_background_flag() -> None:
    async def run() -> None:
        client = MflowClient(server_url="https://example.com", auth_token="secret")
        client._http = RecordingAsyncClient()

        async def fake_list_datasets() -> list[dict[str, str]]:
            return [{"id": "11111111-1111-1111-1111-111111111111", "name": "alpha"}]

        client.list_datasets = fake_list_datasets

        result = await client.learn(datasets=["alpha"], run_in_background=True)

        assert result == {"success": True, "message": "started"}
        assert client._http.posts == [
            (
                "https://example.com/api/v1/procedural/extract-from-episodic",
                {
                    "dataset_id": "11111111-1111-1111-1111-111111111111",
                    "limit": 100,
                    "force_reprocess": False,
                    "run_in_background": True,
                },
                {"Content-Type": "application/json", "Authorization": "Bearer secret"},
            )
        ]

    asyncio.run(run())


def test_remote_learn_rejects_episode_ids_until_backend_supports_them() -> None:
    async def run() -> None:
        client = MflowClient(server_url="https://example.com")

        with pytest.raises(NotImplementedError, match="episode_ids"):
            await client.learn(episode_ids=["ep-1"])

    asyncio.run(run())
