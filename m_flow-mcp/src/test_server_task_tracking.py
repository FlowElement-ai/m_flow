"""Tests for the task-tracking machinery introduced for issue #111.

These tests verify that `memorize` and `save_interaction` no longer drop
background-task failures silently. Each tool now returns a `task_id`, the
outcome is recorded into a bounded in-memory registry, and
`memorize_status(task_id=...)` surfaces the actual success / failure to the
MCP caller. A `wait=True` synchronous mode is also exercised.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Each test gets a clean registry so cases are independent."""
    server._task_registry.clear()
    yield
    server._task_registry.clear()


def _capture_create_task(scheduled: list[asyncio.Task[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    original = asyncio.create_task

    def capture(coro: Any) -> asyncio.Task[Any]:
        task = original(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(server.asyncio, "create_task", capture)


def _extract_task_id(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("task_id:"):
            return line.split(":", 1)[1].strip().strip('"')
    raise AssertionError(f"No task_id line in TextContent payload: {text!r}")


# ---------------------------------------------------------------------------
# memorize: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memorize_returns_task_id_and_records_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add(content: str, dataset_name: str) -> None:
        return None

    async def fake_memorize(**kwargs: object) -> None:
        return None

    scheduled: list[asyncio.Task[Any]] = []
    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))
    _capture_create_task(scheduled, monkeypatch)

    result = await server.memorize("hello", dataset_name="beta")
    await asyncio.gather(*scheduled)

    assert len(result) == 1
    payload = result[0].text
    assert "✅ 后台任务已启动" in payload
    task_id = _extract_task_id(payload)

    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.state == server.TaskState.SUCCESS
    assert record.tool == "memorize"
    assert record.dataset_name == "beta"
    assert record.error_message is None


# ---------------------------------------------------------------------------
# memorize: failure path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memorize_records_failure_when_background_task_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add(content: str, dataset_name: str) -> None:
        raise RuntimeError("LLM key expired")

    async def fake_memorize(**kwargs: object) -> None:  # pragma: no cover - never reached
        return None

    scheduled: list[asyncio.Task[Any]] = []
    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))
    _capture_create_task(scheduled, monkeypatch)

    result = await server.memorize("hello")
    # Background task is expected to fail; gather with return_exceptions=True
    # so the test runner does not see the propagated exception.
    await asyncio.gather(*scheduled, return_exceptions=True)

    task_id = _extract_task_id(result[0].text)
    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.state == server.TaskState.FAILED
    assert record.error_type == "RuntimeError"
    assert "LLM key expired" in (record.error_message or "")


# ---------------------------------------------------------------------------
# memorize_status: per-task lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memorize_status_with_task_id_returns_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add(content: str, dataset_name: str) -> None:
        raise ValueError("graph DB locked")

    async def fake_memorize(**kwargs: object) -> None:  # pragma: no cover
        return None

    scheduled: list[asyncio.Task[Any]] = []
    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))
    _capture_create_task(scheduled, monkeypatch)

    started = await server.memorize("payload", dataset_name="alpha")
    await asyncio.gather(*scheduled, return_exceptions=True)

    task_id = _extract_task_id(started[0].text)
    status = await server.memorize_status(task_id=task_id)

    assert len(status) == 1
    text = status[0].text
    assert f"task_id: {task_id}" in text
    assert "任务状态: failed" in text
    assert "错误类型: ValueError" in text
    assert "graph DB locked" in text


@pytest.mark.asyncio
async def test_memorize_status_with_unknown_task_id_returns_friendly_message() -> None:
    status = await server.memorize_status(task_id="nonexistent-task")
    assert len(status) == 1
    assert "未找到 task_id=nonexistent-task" in status[0].text


# ---------------------------------------------------------------------------
# memorize: synchronous (wait=True) mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memorize_with_wait_returns_inline_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add(content: str, dataset_name: str) -> None:
        return None

    async def fake_memorize(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))

    result = await server.memorize("hello", dataset_name="x", wait=True)

    payload = result[0].text
    assert "✅ 同步执行成功" in payload
    task_id = _extract_task_id(payload)
    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.state == server.TaskState.SUCCESS


@pytest.mark.asyncio
async def test_memorize_with_wait_returns_inline_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_add(content: str, dataset_name: str) -> None:
        raise RuntimeError("malformed input")

    async def fake_memorize(**kwargs: object) -> None:  # pragma: no cover
        return None

    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))

    result = await server.memorize("hello", wait=True)

    payload = result[0].text
    assert "❌ 同步执行失败" in payload
    assert "malformed input" in payload


@pytest.mark.asyncio
async def test_memorize_with_wait_timeout_returns_inflight_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Patch the timeout to a very small value so we don't slow the suite.
    monkeypatch.setattr(server, "_WAIT_TIMEOUT_SECS", 0.05)

    async def slow_add(content: str, dataset_name: str) -> None:
        await asyncio.sleep(2.0)

    async def fake_memorize(**kwargs: object) -> None:
        return None

    monkeypatch.setattr(server, "_client", SimpleNamespace(add=slow_add, memorize=fake_memorize))

    result = await server.memorize("hello", wait=True)

    payload = result[0].text
    assert "⏳ 同步等待超时" in payload
    task_id = _extract_task_id(payload)
    # Background task is still running; record should still be RUNNING.
    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.state == server.TaskState.RUNNING


# ---------------------------------------------------------------------------
# save_interaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_interaction_records_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_add(content: str, graph_scope: list[str]) -> None:
        assert graph_scope == ["user_agent_interaction"]

    async def fake_memorize(**kwargs: object) -> None:
        return None

    scheduled: list[asyncio.Task[Any]] = []
    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))
    _capture_create_task(scheduled, monkeypatch)

    result = await server.save_interaction("user said hi")
    await asyncio.gather(*scheduled)

    payload = result[0].text
    assert "✅ 后台处理交互数据" in payload
    task_id = _extract_task_id(payload)
    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.tool == "save_interaction"
    assert record.state == server.TaskState.SUCCESS


@pytest.mark.asyncio
async def test_save_interaction_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_add(content: str, graph_scope: list[str]) -> None:
        raise PermissionError("auth failed")

    async def fake_memorize(**kwargs: object) -> None:  # pragma: no cover
        return None

    scheduled: list[asyncio.Task[Any]] = []
    monkeypatch.setattr(server, "_client", SimpleNamespace(add=fake_add, memorize=fake_memorize))
    _capture_create_task(scheduled, monkeypatch)

    result = await server.save_interaction("payload")
    await asyncio.gather(*scheduled, return_exceptions=True)

    task_id = _extract_task_id(result[0].text)
    record = await server._get_task_record(task_id)
    assert record is not None
    assert record.state == server.TaskState.FAILED
    assert record.error_type == "PermissionError"


# ---------------------------------------------------------------------------
# Registry: bounded LRU eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_registry_evicts_oldest_when_over_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "_TASK_REGISTRY_MAX", 3)

    ids = []
    for i in range(5):
        ids.append(await server._record_task_start("memorize", dataset_name=f"ds{i}"))

    # Only the last 3 should remain.
    assert len(server._task_registry) == 3
    assert list(server._task_registry.keys()) == ids[-3:]
    # The oldest two are gone.
    assert await server._get_task_record(ids[0]) is None
    assert await server._get_task_record(ids[1]) is None


@pytest.mark.asyncio
async def test_outcome_refreshes_lru_recency() -> None:
    """`_record_task_outcome` calls move_to_end so completed records sit at
    the tail of the OrderedDict (more recent than any earlier RUNNING
    record). Pure-LRU eviction will still drop them eventually if newer
    tasks keep arriving — that trade-off is intentional for the bounded
    in-memory registry."""
    a = await server._record_task_start("memorize", dataset_name="a")
    b = await server._record_task_start("memorize", dataset_name="b")

    # a comes first, b comes second.
    assert list(server._task_registry.keys()) == [a, b]

    # Marking `a` as completed bumps it to the end (most recent).
    await server._record_task_outcome(a, server.TaskState.SUCCESS)
    assert list(server._task_registry.keys()) == [b, a]
