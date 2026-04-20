"""
M-flow MCP服务器

Model Context Protocol服务器实现，将M-flow知识图谱功能暴露给AI助手。
支持stdio、SSE和HTTP传输模式。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import traceback
from collections import OrderedDict
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

import httpx
import mcp.types as types
import uvicorn
from mcp.server import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from m_flow.shared.logging_utils import get_log_file_location, get_logger, setup_logging
from m_flow.storage.utils_mod.utils import JSONEncoder

if TYPE_CHECKING:
    from m_flow_client import MflowClient

try:
    from .m_flow_client import MflowClient
except ImportError:
    from m_flow_client import MflowClient

# FastMCP服务器实例
_mcp = FastMCP("Mflow")
_log = get_logger()
_client: Optional[MflowClient] = None

_CORS_ORIGINS = ["http://localhost:3000"]


# ============================================================
# Background-task tracking (issue #111)
#
# The MCP `memorize` and `save_interaction` tools were historically
# fire-and-forget: they used `asyncio.create_task(...)` and immediately
# returned a "started" message. If the background coroutine raised
# (e.g. expired LLM key, locked graph DB, malformed input), the failure
# was only logged server-side — the MCP caller (IDE / agent) never
# learned about it, producing a silent data-loss scenario.
#
# This module-level registry records each background task's outcome so
# callers can later query `memorize_status(task_id=...)` and see the
# actual success / failure / error message. The registry is an in-memory
# bounded LRU; restarting the MCP server clears it (acceptable for the
# short-lived agent sessions this server targets).
# ============================================================


class TaskState(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class TaskRecord:
    """Outcome of a background MCP task surfaced to the caller."""

    task_id: str
    tool: str
    state: TaskState
    started_at: str
    finished_at: Optional[str] = None
    dataset_name: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Bounded registry: keep the most recent N task records. Older entries
# are evicted (LRU) so the dict never grows unbounded.
_TASK_REGISTRY_MAX = 100
# Default sync-mode timeout — `wait=True` callers (CI, scripts) get a
# bounded wait; on timeout the task continues running in the background
# and can still be queried via `memorize_status(task_id=...)`.
_WAIT_TIMEOUT_SECS = 600

_task_registry: "OrderedDict[str, TaskRecord]" = OrderedDict()
_task_registry_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    """Generate a short, URL-friendly task ID."""
    return uuid4().hex[:12]


async def _record_task_start(
    tool: str,
    dataset_name: Optional[str] = None,
    **metadata: Any,
) -> str:
    """Create a new task record in RUNNING state and return its ID."""
    task_id = _short_id()
    record = TaskRecord(
        task_id=task_id,
        tool=tool,
        state=TaskState.RUNNING,
        started_at=_now_iso(),
        dataset_name=dataset_name,
        metadata=dict(metadata),
    )
    async with _task_registry_lock:
        _task_registry[task_id] = record
        # LRU eviction: drop oldest entries until under the cap.
        while len(_task_registry) > _TASK_REGISTRY_MAX:
            _task_registry.popitem(last=False)
    return task_id


async def _record_task_outcome(
    task_id: str,
    state: TaskState,
    error: Optional[BaseException] = None,
) -> None:
    """Mark a task as completed (or failed) and record any error."""
    async with _task_registry_lock:
        record = _task_registry.get(task_id)
        if record is None:
            # Already evicted from the LRU — safe to ignore.
            return
        record.state = state
        record.finished_at = _now_iso()
        if error is not None:
            record.error_type = type(error).__name__
            record.error_message = str(error)
        # Refresh recency so that completed records are not the first
        # to be evicted while RUNNING placeholders age.
        _task_registry.move_to_end(task_id)


async def _get_task_record(task_id: str) -> Optional[TaskRecord]:
    async with _task_registry_lock:
        return _task_registry.get(task_id)


async def _run_tracked_task(
    task_id: str,
    coro_factory: Callable[[], Awaitable[None]],
) -> None:
    """Execute a background coroutine and record its outcome.

    Wraps the user-provided coroutine factory so any exception is captured
    into the task registry. Errors are still logged at ERROR level so the
    existing log-based observability continues to work; the registry only
    adds an additional channel that callers can poll.
    """
    try:
        await coro_factory()
        await _record_task_outcome(task_id, TaskState.SUCCESS)
    except Exception as exc:
        _log.error("[task=%s] background task failed: %s", task_id, exc)
        _log.debug("[task=%s] traceback:\n%s", task_id, traceback.format_exc())
        await _record_task_outcome(task_id, TaskState.FAILED, error=exc)


def _format_task_record(record: TaskRecord) -> str:
    """Render a task record for the MCP TextContent payload."""
    lines = [
        f"任务状态: {record.state.value}",
        f"task_id: {record.task_id}",
        f"工具: {record.tool}",
    ]
    if record.dataset_name:
        lines.append(f"数据集: {record.dataset_name}")
    lines.append(f"开始: {record.started_at}")
    if record.finished_at:
        lines.append(f"结束: {record.finished_at}")
    if record.error_type:
        lines.append(f"错误类型: {record.error_type}")
    if record.error_message:
        lines.append(f"错误信息: {record.error_message}")
    return "\n".join(lines)


async def _run_sse_server():
    """启动SSE传输服务器"""
    app = _mcp.sse_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    cfg = uvicorn.Config(
        app,
        host=_mcp.settings.host,
        port=_mcp.settings.port,
        log_level=_mcp.settings.log_level.lower(),
    )
    await uvicorn.Server(cfg).serve()


async def _run_http_server():
    """启动HTTP传输服务器"""
    app = _mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    cfg = uvicorn.Config(
        app,
        host=_mcp.settings.host,
        port=_mcp.settings.port,
        log_level=_mcp.settings.log_level.lower(),
    )
    await uvicorn.Server(cfg).serve()


@_mcp.custom_route("/health", methods=["GET"])
async def _health_check(request):
    """健康检查端点"""
    return JSONResponse({"status": "ok"})


# ============================================================
# MCP工具定义
# ============================================================


@_mcp.tool()
async def memorize(
    data: str,
    dataset_name: str = "main_dataset",
    wait: bool = False,
) -> list:
    """
    将数据转换为结构化知识图谱

    这是 M-flow 的核心处理步骤，将原始文本转换为智能知识图谱。

    参数
    ----
    data : str
        待处理的数据内容
    dataset_name : str, optional
        目标数据集名称 (默认: main_dataset)
    wait : bool, optional
        默认 False — 异步触发，立即返回 task_id。设为 True 时同步等待
        任务完成（最多 600 秒），适合 CI / 脚本场景需要拿到真正结果的情形；
        若超时，任务继续在后台运行，可通过 memorize_status(task_id=...) 查询。

    返回
    ----
    list
        包含 task_id 和后台任务启动 / 完成信息的 TextContent 列表。
        失败时也会返回带 task_id 的错误说明，调用方可据此进一步排查。
    """
    task_id = await _record_task_start("memorize", dataset_name=dataset_name)
    log_path = get_log_file_location()

    async def _do_work() -> None:
        with redirect_stdout(sys.stderr):
            _log.info("[task=%s] 记忆化处理开始: dataset=%s", task_id, dataset_name)
            await _client.add(data, dataset_name=dataset_name)
            await _client.memorize(datasets=[dataset_name], enable_content_routing=False)
            _log.info("[task=%s] 记忆化处理完成: dataset=%s", task_id, dataset_name)

    bg_task = asyncio.create_task(_run_tracked_task(task_id, _do_work))

    if wait:
        try:
            await asyncio.wait_for(asyncio.shield(bg_task), timeout=_WAIT_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"⏳ 同步等待超时（任务仍在后台运行）\n"
                        f"task_id: {task_id}\n"
                        f"数据集: {dataset_name}\n"
                        f'使用 memorize_status(task_id="{task_id}") 查询最终状态\n'
                        f"日志: {log_path}"
                    ),
                )
            ]
        record = await _get_task_record(task_id)
        if record and record.state == TaskState.SUCCESS:
            return [
                types.TextContent(
                    type="text",
                    text=(f"✅ 同步执行成功\ntask_id: {task_id}\n数据集: {dataset_name}\n日志: {log_path}"),
                )
            ]
        err = record.error_message if record and record.error_message else "未知错误"
        return [
            types.TextContent(
                type="text",
                text=(f"❌ 同步执行失败\ntask_id: {task_id}\n数据集: {dataset_name}\n错误: {err}\n日志: {log_path}"),
            )
        ]

    return [
        types.TextContent(
            type="text",
            text=(
                f"✅ 后台任务已启动\n"
                f"task_id: {task_id}\n"
                f"数据集: {dataset_name}\n"
                f'使用 memorize_status(task_id="{task_id}") 查询任务状态\n'
                f"日志: {log_path}"
            ),
        )
    ]


@_mcp.tool(name="save_interaction", description="保存用户-Agent交互记录")
async def save_interaction(data: str, wait: bool = False) -> list:
    """
    保存用户交互数据

    参数
    ----
    data : str
        交互内容
    wait : bool, optional
        默认 False — 异步触发，立即返回 task_id。设为 True 时同步等待
        任务完成（最多 600 秒）。

    返回
    ----
    list
        包含 task_id 的 TextContent 列表，便于后续通过
        memorize_status(task_id=...) 查询任务结果。
    """
    task_id = await _record_task_start("save_interaction")
    log_path = get_log_file_location()

    async def _do_work() -> None:
        with redirect_stdout(sys.stderr):
            _log.info("[task=%s] 保存交互开始", task_id)
            await _client.add(data, graph_scope=["user_agent_interaction"])
            # 交互记录是简单文本，禁用 content_routing
            await _client.memorize(enable_content_routing=False)
            _log.info("[task=%s] 保存交互完成", task_id)

    bg_task = asyncio.create_task(_run_tracked_task(task_id, _do_work))

    if wait:
        try:
            await asyncio.wait_for(asyncio.shield(bg_task), timeout=_WAIT_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"⏳ 同步等待超时（任务仍在后台运行）\n"
                        f"task_id: {task_id}\n"
                        f'使用 memorize_status(task_id="{task_id}") 查询最终状态\n'
                        f"日志: {log_path}"
                    ),
                )
            ]
        record = await _get_task_record(task_id)
        if record and record.state == TaskState.SUCCESS:
            return [
                types.TextContent(
                    type="text",
                    text=(f"✅ 同步执行成功\ntask_id: {task_id}\n日志: {log_path}"),
                )
            ]
        err = record.error_message if record and record.error_message else "未知错误"
        return [
            types.TextContent(
                type="text",
                text=(f"❌ 同步执行失败\ntask_id: {task_id}\n错误: {err}\n日志: {log_path}"),
            )
        ]

    return [
        types.TextContent(
            type="text",
            text=(
                f"✅ 后台处理交互数据\n"
                f"task_id: {task_id}\n"
                f'使用 memorize_status(task_id="{task_id}") 查询任务状态\n'
                f"日志: {log_path}"
            ),
        )
    ]


@_mcp.tool()
async def search(
    search_query: str,
    recall_mode: str,
    top_k: int = 5,
    datasets: list = None,
    system_prompt: str = None,
    enable_hybrid_search: bool = None,
) -> list:
    """
    搜索知识图谱

    参数
    ----
    search_query : str
        搜索查询文本
    recall_mode : str
        召回模式: TRIPLET_COMPLETION, EPISODIC, PROCEDURAL, CYPHER, CHUNKS_LEXICAL
    top_k : int, optional
        返回结果数量 (默认: 5，范围: 1-100)
    datasets : list, optional
        限定搜索的数据集名称列表
    system_prompt : str, optional
        自定义系统提示词（用于 TRIPLET_COMPLETION 模式）
    enable_hybrid_search : bool, optional
        是否启用混合搜索（用于 EPISODIC 模式）

    返回
    ----
    list
        搜索结果
    """
    # 参数验证：检查 recall_mode 是否有效
    VALID_MODES = {"CHUNKS_LEXICAL", "TRIPLET_COMPLETION", "CYPHER", "EPISODIC", "PROCEDURAL"}
    if recall_mode.upper() not in VALID_MODES:
        return [
            types.TextContent(
                type="text", text=f"❌ 无效的召回模式: {recall_mode}\n有效值: {', '.join(sorted(VALID_MODES))}"
            )
        ]

    # 参数验证：检查 top_k 是否为有效正整数
    if top_k < 1 or top_k > 100:
        return [types.TextContent(type="text", text=f"❌ 无效的 top_k 值: {top_k}\n有效范围: 1-100")]

    async def _exec_search(
        query: str,
        mode: str,
        k: int,
        ds: list = None,
        prompt: str = None,
        hybrid: bool = None,
    ) -> str:
        with redirect_stdout(sys.stderr):
            results = await _client.search(
                query_text=query,
                query_type=mode,
                top_k=k,
                datasets=ds,
                system_prompt=prompt,
                enable_hybrid_search=hybrid,
            )

            if getattr(_client, "_remote", False):
                if isinstance(results, str):
                    return results
                elif isinstance(results, list):
                    if mode.upper() == "TRIPLET_COMPLETION" and results:
                        return str(results[0])
                    return str(results)
                return json.dumps(results, cls=JSONEncoder)
            else:
                if mode.upper() == "TRIPLET_COMPLETION":
                    return str(results[0]) if results else ""
                return str(results)

    try:
        result = await _exec_search(search_query, recall_mode, top_k, datasets, system_prompt, enable_hybrid_search)
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        _log.error("搜索失败: %s", e)
        return [types.TextContent(type="text", text=f"❌ 搜索失败: {str(e)}")]


@_mcp.tool()
async def list_data(dataset_id: str = None) -> list:
    """
    列出数据集和数据项

    参数
    ----
    dataset_id : str, optional
        数据集ID，如果提供则只列出该数据集的内容

    返回
    ----
    list
        数据集信息
    """
    from uuid import UUID

    with redirect_stdout(sys.stderr):
        try:
            lines = []

            if dataset_id:
                if getattr(_client, "_remote", False):
                    return [
                        types.TextContent(
                            type="text",
                            text="❌ API模式不支持详细数据列表\n请使用直接模式或API",
                        )
                    ]

                from m_flow.auth.methods import get_seed_user
                from m_flow.data.methods import get_dataset, fetch_dataset_items

                _log.info("列出数据集: %s", dataset_id)
                user = await get_seed_user()
                ds = await get_dataset(user.id, UUID(dataset_id))

                if not ds:
                    return [types.TextContent(type="text", text=f"❌ 数据集不存在: {dataset_id}")]

                items = await fetch_dataset_items(ds.id)
                lines.extend(
                    [
                        f"📁 数据集: {ds.name}",
                        f"   ID: {ds.id}",
                        f"   创建时间: {ds.created_at}",
                        f"   数据项: {len(items)}",
                        "",
                    ]
                )

                for i, item in enumerate(items, 1):
                    lines.extend(
                        [
                            f"   📄 数据项 #{i}:",
                            f"      ID: {item.id}",
                            f"      名称: {item.name or '未命名'}",
                            f"      创建时间: {item.created_at}",
                            "",
                        ]
                    )
            else:
                _log.info("列出所有数据集")
                datasets = await _client.list_datasets()

                if not datasets:
                    return [
                        types.TextContent(
                            type="text",
                            text="📂 暂无数据集\n使用 memorize 创建第一个数据集!",
                        )
                    ]

                lines.extend(["📂 可用数据集:", "=" * 50, ""])

                for i, ds in enumerate(datasets, 1):
                    if isinstance(ds, dict):
                        lines.extend(
                            [
                                f"{i}. 📁 {ds.get('name', '未命名')}",
                                f"   ID: {ds.get('id')}",
                                f"   创建时间: {ds.get('created_at', 'N/A')}",
                            ]
                        )
                    else:
                        lines.extend(
                            [
                                f"{i}. 📁 {ds.name}",
                                f"   ID: {ds.id}",
                                f"   创建时间: {ds.created_at}",
                            ]
                        )
                    lines.append("")

                lines.extend(
                    [
                        "🗑️  删除数据:",
                        '   delete(data_id="...", dataset_id="...")',
                    ]
                )

            _log.info("数据列表完成")
            return [types.TextContent(type="text", text="\n".join(lines))]

        except ValueError as e:
            msg = f"❌ UUID格式错误: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            msg = f"❌ 列表失败: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]


@_mcp.tool()
async def delete(data_id: str, dataset_id: str, mode: str = "soft") -> list:
    """
    删除数据

    参数
    ----
    data_id : str
        数据项ID
    dataset_id : str
        数据集ID
    mode : str, optional
        删除模式: soft (默认) 或 hard

    返回
    ----
    list
        删除结果
    """
    # 参数验证：检查 mode 是否有效，并标准化为小写
    VALID_MODES = {"soft", "hard"}
    mode_lower = mode.lower()
    if mode_lower not in VALID_MODES:
        return [types.TextContent(type="text", text=f"❌ 无效的删除模式: {mode}\n有效值: soft, hard")]

    from uuid import UUID

    with redirect_stdout(sys.stderr):
        try:
            _log.info("删除: data=%s, dataset=%s, mode=%s", data_id, dataset_id, mode_lower)

            result = await _client.delete(
                data_id=UUID(data_id),
                dataset_id=UUID(dataset_id),
                mode=mode_lower,
            )

            _log.info("删除完成: %s", result)
            formatted = json.dumps(result, indent=2, cls=JSONEncoder)

            return [
                types.TextContent(
                    type="text",
                    text=f"✅ 删除成功\n\n{formatted}",
                )
            ]
        except ValueError as e:
            msg = f"❌ UUID格式错误: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            msg = f"❌ 删除失败: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]


@_mcp.tool()
async def prune(
    graph: bool = True,
    vector: bool = True,
    metadata: bool = False,
    cache: bool = True,
) -> list:
    """
    重置知识图谱

    选择性清除系统数据。此操作不可逆，请谨慎使用。

    参数
    ----
    graph : bool, optional
        是否清除图数据库 (默认: True)
    vector : bool, optional
        是否清除向量库 (默认: True)
    metadata : bool, optional
        是否清除元数据 (默认: False，更安全)
    cache : bool, optional
        是否清除缓存 (默认: True)

    返回
    ----
    list
        操作结果
    """
    with redirect_stdout(sys.stderr):
        try:
            # 先清除数据
            await _client.prune_data()
            # 再清除系统数据（可选组件）
            await _client.prune_system(
                graph=graph,
                vector=vector,
                metadata=metadata,
                cache=cache,
            )

            cleared = []
            if graph:
                cleared.append("图数据库")
            if vector:
                cleared.append("向量库")
            if metadata:
                cleared.append("元数据")
            if cache:
                cleared.append("缓存")

            return [types.TextContent(type="text", text=f"✅ 已清除: {', '.join(cleared) if cleared else '无'}")]
        except httpx.HTTPStatusError as http_err:
            # In API mode the prune endpoint enforces several admin guards
            # (master-switch flag, superuser auth, active-pipeline check,
            # cooldown). Surface the server's status code so the caller can
            # distinguish "feature disabled" from "permission denied" from
            # "in-flight pipelines blocking" and react appropriately.
            status_code = http_err.response.status_code
            try:
                detail = http_err.response.json().get("detail", str(http_err))
            except Exception:
                detail = http_err.response.text or str(http_err)
            hint_map = {
                403: "API 模式下 prune 默认关闭。请在 backend 设置 MFLOW_ENABLE_PRUNE_API=true 并以 superuser 身份调用。",
                401: "未授权：API 模式下 prune 必须携带 superuser 的 auth token。",
                409: "当前有 pipeline 正在运行，无法执行 prune。",
                429: "未到 cooldown 间隔，请稍后再试。",
            }
            hint = hint_map.get(status_code, "")
            msg = f"❌ 清空失败 (HTTP {status_code}): {detail}"
            if hint:
                msg += f"\n💡 {hint}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            msg = f"❌ 清空失败: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]


@_mcp.tool()
async def memorize_status(task_id: Optional[str] = None) -> list:
    """
    获取记忆化管道状态，或某次后台任务的最终结果。

    参数
    ----
    task_id : str, optional
        当指定时，返回该后台任务的状态与错误信息（来自 issue #111 引入的
        task registry）。可用于检查 `memorize` / `save_interaction` 异步
        触发的任务是否成功，避免 silent data loss。

        不指定时退回到历史行为：返回 dataset 维度的 pipeline 状态。

    返回
    ----
    list
        TextContent 列表。task 维度查询返回任务记录详情；pipeline 维度
        查询返回各 dataset 的 memorize_pipeline 状态。
    """
    # Per-task lookup (issue #111). Backward compatible: when caller does
    # not pass task_id, fall through to the original pipeline-level query.
    if task_id:
        record = await _get_task_record(task_id)
        if record is None:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"ℹ️ 未找到 task_id={task_id} 的记录。\n"
                        f"可能原因：任务记录已被淘汰（仅保留最近 {_TASK_REGISTRY_MAX} 条）"
                        f"或 task_id 输入有误。"
                    ),
                )
            ]
        return [types.TextContent(type="text", text=_format_task_record(record))]

    with redirect_stdout(sys.stderr):
        try:
            if getattr(_client, "_remote", False):
                from uuid import UUID

                datasets = await _client.list_datasets()
                if not datasets:
                    return [types.TextContent(type="text", text="ℹ️ 当前没有可查询的数据集")]

                status_map = await _client.get_workflow_status(
                    [UUID(ds["id"]) for ds in datasets],
                    "memorize_pipeline",
                )
                if not status_map:
                    return [types.TextContent(type="text", text="ℹ️ 当前没有记忆化管道状态记录")]

                lines = ["记忆化管道状态:"]
                for dataset in datasets:
                    ds_id = str(dataset.get("id"))
                    ds_name = dataset.get("name") or ds_id
                    lines.append(f"- {ds_name}: {status_map.get(ds_id, 'unknown')}")
                return [types.TextContent(type="text", text="\n".join(lines))]

            from m_flow.auth.methods import get_seed_user
            from m_flow.data.methods.get_unique_dataset_id import get_unique_dataset_id

            user = await get_seed_user()
            ds_id = await get_unique_dataset_id("main_dataset", user)
            status = await _client.get_workflow_status([ds_id], "memorize_pipeline")
            return [types.TextContent(type="text", text=str(status))]
        except Exception as e:
            msg = f"❌ 获取状态失败: {e}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]


@_mcp.tool()
async def learn(
    datasets: list = None,
    episode_ids: list = None,
    run_in_background: bool = False,
) -> list:
    """
    从已有 Episode 提取程序性记忆 (Procedural Memory)

    将情景记忆转化为可执行的操作步骤和规则。

    参数
    ----
    datasets : list, optional
        要处理的数据集名称列表
    episode_ids : list, optional
        要处理的 Episode ID 列表
    run_in_background : bool, optional
        是否在后台执行（默认: False）

    返回
    ----
    list
        学习结果
    """
    with redirect_stdout(sys.stderr):
        try:
            _log.info("开始学习: datasets=%s, episodes=%s, background=%s", datasets, episode_ids, run_in_background)

            result = await _client.learn(
                datasets=datasets,
                episode_ids=episode_ids,
                run_in_background=run_in_background,
            )

            _log.info("学习完成: %s", result)
            return [
                types.TextContent(
                    type="text",
                    text=f"✅ 学习完成\n{json.dumps(result, ensure_ascii=False, indent=2, cls=JSONEncoder)}",
                )
            ]
        except NotImplementedError as e:
            msg = f"⚠️ {str(e)}\n请使用直接模式运行 MCP 服务器"
            _log.warning(msg)
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            _log.error("学习失败: %s", e)
            return [types.TextContent(type="text", text=f"❌ 学习失败: {str(e)}")]


@_mcp.tool()
async def update_data(
    data_id: str,
    data: str,
    dataset_id: str,
) -> list:
    """
    更新已有数据项

    参数
    ----
    data_id : str
        要更新的数据项 ID (UUID 格式)
    data : str
        新的数据内容
    dataset_id : str
        数据集 ID (UUID 格式)

    返回
    ----
    list
        更新结果
    """
    from uuid import UUID

    with redirect_stdout(sys.stderr):
        try:
            # 验证 UUID 格式
            try:
                UUID(data_id)
                UUID(dataset_id)
            except ValueError as e:
                return [types.TextContent(type="text", text=f"❌ 无效的 UUID 格式: {str(e)}")]

            _log.info("更新数据: data_id=%s, dataset_id=%s", data_id, dataset_id)

            result = await _client.update(
                data_id=data_id,
                data=data,
                dataset_id=dataset_id,
            )

            _log.info("更新完成: %s", result)
            return [types.TextContent(type="text", text="✅ 数据已更新")]
        except Exception as e:
            _log.error("更新失败: %s", e)
            return [types.TextContent(type="text", text=f"❌ 更新失败: {str(e)}")]


@_mcp.tool()
async def ingest(
    data: str,
    dataset_name: str = "main_dataset",
    skip_memorize: bool = False,
) -> list:
    """
    一步入库 (add + memorize 的简化版本)

    自动完成数据添加和记忆化处理。

    参数
    ----
    data : str
        要入库的数据内容
    dataset_name : str, optional
        目标数据集名称 (默认: main_dataset)
    skip_memorize : bool, optional
        是否跳过记忆化处理，仅添加数据 (默认: False)

    返回
    ----
    list
        入库结果
    """
    with redirect_stdout(sys.stderr):
        try:
            _log.info("开始入库: dataset=%s, data_length=%d, skip_memorize=%s", dataset_name, len(data), skip_memorize)

            # MCP 工具输入通常是单条文本，禁用 content_routing 或跳过 memorize
            result = await _client.ingest(
                data=data,
                dataset_name=dataset_name,
                skip_memorize=skip_memorize,
                enable_content_routing=False,  # MCP 工具输入不需要句子级路由
            )

            _log.info("入库完成: %s", result)
            return [types.TextContent(type="text", text=f"✅ 数据已入库到 {dataset_name}")]
        except Exception as e:
            _log.error("入库失败: %s", e)
            return [types.TextContent(type="text", text=f"❌ 入库失败: {str(e)}")]


@_mcp.tool()
async def query(
    question: str,
    datasets: list = None,
    mode: str = "episodic",
    top_k: int = 10,
) -> list:
    """
    简化查询 (用户友好的搜索接口)

    直接用自然语言提问，系统自动选择最佳搜索策略。

    参数
    ----
    question : str
        用户问题
    datasets : list, optional
        限定搜索的数据集
    mode : str, optional
        检索模式 (默认: "episodic")
        - "episodic": 情景记忆检索
        - "triplet": 三元组补全
        - "chunks": 原始文本块
        - "procedural": 程序性记忆
        - "cypher": 直接 Cypher 查询
    top_k : int, optional
        返回结果数量 (默认: 10)

    返回
    ----
    list
        查询答案
    """
    # 参数验证：检查 mode 是否有效
    VALID_MODES = {"episodic", "triplet", "chunks", "procedural", "cypher"}
    if mode.lower() not in VALID_MODES:
        return [
            types.TextContent(type="text", text=f"❌ 无效的查询模式: {mode}\n有效值: {', '.join(sorted(VALID_MODES))}")
        ]

    # 参数验证：检查 top_k 是否有效
    if top_k < 1 or top_k > 100:
        return [types.TextContent(type="text", text=f"❌ 无效的 top_k 值: {top_k}\n有效范围: 1-100")]

    with redirect_stdout(sys.stderr):
        try:
            _log.info("查询: %s (mode=%s, top_k=%d)", question, mode, top_k)

            answer = await _client.query(
                question=question,
                datasets=datasets,
                mode=mode,
                top_k=top_k,
            )

            _log.info("查询完成")
            return [types.TextContent(type="text", text=answer)]
        except httpx.HTTPStatusError as http_err:
            try:
                detail = http_err.response.json().get("error", str(http_err))
            except Exception:
                detail = http_err.response.text or str(http_err)
            msg = f"❌ 查询失败 (HTTP {http_err.response.status_code}): {detail}"
            _log.error(msg)
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            _log.error("查询失败: %s", e)
            return [types.TextContent(type="text", text=f"❌ 查询失败: {str(e)}")]


# ============================================================
# 辅助函数
# ============================================================


def _format_node(node: dict) -> str:
    """格式化节点为字符串"""
    parts = [f'{k}: "{v}"' for k, v in node.items() if k in ("id", "name")]
    return f"Node({', '.join(parts)})"


def _format_edges(results: list) -> str:
    """格式化边关系为字符串"""
    lines = []
    for node1, edge, node2 in results:
        rel = edge["relationship_name"]
        lines.append(f"{_format_node(node1)} {rel} {_format_node(node2)}")
    return "\n".join(lines)


def node_to_string(node: dict) -> str:
    """兼容性导出"""
    return _format_node(node)


def retrieved_edges_to_string(results: list) -> str:
    """兼容性导出"""
    return _format_edges(results)


def load_class(model_file: str, model_name: str):
    """动态加载类"""
    path = os.path.abspath(model_file)
    spec = importlib.util.spec_from_file_location("graph_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, model_name)


# ============================================================
# 主入口
# ============================================================


async def main():
    """MCP服务器主函数"""
    global _client

    parser = argparse.ArgumentParser(description="M-flow MCP服务器")
    parser.add_argument(
        "--transport",
        choices=["sse", "stdio", "http"],
        default="stdio",
        help="传输模式 (默认: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="绑定主机")
    parser.add_argument("--port", type=int, default=8000, help="绑定端口")
    parser.add_argument("--path", default="/mcp", help="HTTP端点路径")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="日志级别",
    )
    parser.add_argument("--no-migration", action="store_true", help="跳过数据库迁移")
    parser.add_argument("--api-url", help="M-flow API服务器URL")
    parser.add_argument("--api-token", help="API认证令牌")

    args = parser.parse_args()

    _client = MflowClient(server_url=args.api_url, auth_token=args.api_token)
    _mcp.settings.host = args.host
    _mcp.settings.port = args.port
    _mcp.settings.log_level = args.log_level.upper()

    # 执行数据库迁移
    if not args.no_migration and not args.api_url:
        from m_flow.core.domain.operations.setup import setup

        await setup()

        _log.info("执行数据库迁移...")
        result = subprocess.run(
            ["python", "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        )

        if result.returncode != 0:
            output = result.stderr + result.stdout
            if "UserAlreadyExists" in output:
                _log.warning("默认用户已存在，继续启动...")
            else:
                _log.error("迁移失败: %s", output)
                sys.exit(1)

        _log.info("数据库迁移完成")
    elif args.api_url:
        _log.info("API模式，跳过迁移")

    _log.info("启动MCP服务器: transport=%s", args.transport)

    try:
        if args.transport == "stdio":
            await _mcp.run_stdio_async()
        elif args.transport == "sse":
            _log.info("SSE模式: %s:%s", args.host, args.port)
            await _run_sse_server()
        elif args.transport == "http":
            _log.info("HTTP模式: %s:%s%s", args.host, args.port, args.path)
            await _run_http_server()
    finally:
        # 确保关闭 HTTP 客户端，防止资源泄漏
        if _client is not None:
            await _client.close()


if __name__ == "__main__":
    logger = setup_logging()
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error("服务器启动失败: %s", e)
        raise
