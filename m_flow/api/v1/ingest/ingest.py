"""
M-flow one-step ingestion API.

Provides a simplified interface that combines data ingestion (add) and knowledge
graph construction (memorize) into a single step.
Aims to improve usability while preserving flexibility of underlying add() and memorize() APIs.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Union, Optional, List, BinaryIO, Tuple, Set
from uuid import UUID

import os

from m_flow.api.v1.add import add
from m_flow.api.v1.memorize import memorize
from m_flow.pipeline.models.RunEvent import RunEvent
from m_flow.shared.enums import ContentType
from m_flow.shared.logging_utils import get_logger

__all__ = ["ingest", "IngestResult", "IngestStatus"]

logger = get_logger("ingest")


class IngestStatus(str, Enum):
    """
    Ingestion status enum.

    Inherits str to support direct string comparison (backwards compatible).

    Values:
        COMPLETED: Synchronously completed, data is queryable.
        BACKGROUND_STARTED: Background processing has started.
        MEMORIZE_SKIPPED: Only add was executed, data is not queryable.
        MEMORIZE_FAILED: add succeeded but memorize failed.
    """

    COMPLETED = "completed"
    BACKGROUND_STARTED = "background_started"
    MEMORIZE_SKIPPED = "memorize_skipped"
    MEMORIZE_FAILED = "memorize_failed"


def _get_param_sets() -> Tuple[Set[str], Set[str]]:
    """
    Get parameter sets for add and memorize.

    Recomputed each call so signature changes (new kwargs) are picked up without stale cache.
    Excludes data/dataset_name/datasets/kwargs which are handled directly by ingest.

    Returns:
        Tuple[add_params, memorize_params]: Two sets of parameter names.
    """
    add_params = set(inspect.signature(add).parameters.keys()) - {"data", "dataset_name"}
    memorize_params = set(inspect.signature(memorize).parameters.keys()) - {"datasets", "kwargs"}

    # Add dynamic kwargs params that memorize() accepts via **kwargs
    # These are feature toggles that override environment variables
    memorize_kwargs_params = {
        # Feature toggles (override env vars)
        "enable_episodic",
        "enable_procedural",
        "enable_content_routing",
        "enable_episode_routing",
        "enable_semantic_merge",
        "enable_facet_points",
        "extract_relationships",
        # Episodic config params (passed to write_episodic_memories)
        "enable_llm_entity_for_routing",
        "semantic_merge_threshold",
        "facet_points_prompt_file",
        # Content type for sentence splitting (required when enable_content_routing=True)
        "content_type",
        # Precise summarization mode
        "precise_mode",
        # Not on memorize() signature; ingest maps it to memorize incremental only
        "memorize_incremental_loading",
    }
    memorize_params = memorize_params | memorize_kwargs_params

    return add_params, memorize_params


@dataclass
class IngestResult:
    """
    Ingestion result.

    Attributes:
        dataset_id: Unique dataset identifier.
        dataset_name: Dataset name.
        status: Ingestion status (see IngestStatus enum).
        add_run_id: Pipeline run ID for add phase.
        memorize_run_id: Pipeline run ID for memorize phase (may be None).
        error_message: Error message on failure (only when status=MEMORIZE_FAILED).

    Note:
        Detailed statistics need to be obtained from logs or database queries.
        RunEvent currently does not include fields like episodes_created.
    """

    dataset_id: UUID
    dataset_name: str
    status: IngestStatus
    add_run_id: UUID
    memorize_run_id: Optional[UUID] = None
    error_message: Optional[str] = None

    def is_complete(self) -> bool:
        """Check if synchronously completed (data is queryable)."""
        return self.status == IngestStatus.COMPLETED

    def is_completed(self) -> bool:
        """Check if synchronously completed (alias for is_complete, for API consistency)."""
        return self.is_complete()

    def is_background(self) -> bool:
        """Check if processing in background."""
        return self.status == IngestStatus.BACKGROUND_STARTED

    def is_success(self) -> bool:
        """Check if successful (includes background start)."""
        return self.status in (IngestStatus.COMPLETED, IngestStatus.BACKGROUND_STARTED)

    def needs_retry(self) -> bool:
        """Check if memorize needs retry."""
        return self.status == IngestStatus.MEMORIZE_FAILED

    def to_dict(self) -> dict[str, Any]:
        """Convert to serializable dict (UUID to str, enum to str)."""
        return {
            "dataset_id": str(self.dataset_id),
            "dataset_name": self.dataset_name,
            "status": self.status.value,
            "add_run_id": str(self.add_run_id),
            "memorize_run_id": str(self.memorize_run_id) if self.memorize_run_id else None,
            "error_message": self.error_message,
        }


async def ingest(
    data: Union[str, List[str], BinaryIO, List[BinaryIO]],
    dataset_name: Optional[str] = None,
    *,
    skip_memorize: bool = False,
    **kwargs,
) -> IngestResult:
    """
    一步完成数据入库和知识图谱构建。

    等价于:
        add_result = await m_flow.add(data, dataset_name=dataset_name, **add_kwargs)
        await m_flow.memorize(datasets=[add_result.dataset_name], **memorize_kwargs)

    Args:
        data: 文档内容、文件路径或 URL
            - str: 文本内容或文件路径
            - List[str]: 多个文本/路径
            - BinaryIO: 文件对象
            - List[BinaryIO]: 多个文件对象
        dataset_name: 数据集名称（默认 "main_dataset"）
        skip_memorize: 仅执行 add，跳过 memorize
        **kwargs: 透传给 add() 和 memorize() 的所有参数

    Kwargs:
        **kwargs 会自动透传给 add() 和 memorize()。
        使用 inspect 动态提取有效参数，无效参数会抛出 TypeError。

        常用参数（完整列表见 add()/memorize() 文档）:
        - chunk_size: 分块大小
        - chunker: 自定义分块器
        - run_in_background: 后台运行
        - incremental_loading: 仅作用于 add()；memorize 默认仍为增量（可用 memorize_incremental_loading 覆盖）
        - user: 认证用户
        - vector_db_config / graph_db_config: 数据库配置

    Warning:
        skip_memorize=True 时，数据仅保存到关系数据库，**不能进行任何查询**。
        必须后续调用 memorize() 才能使数据可查询。

    Returns:
        IngestResult: 包含 dataset_id、状态和 pipeline run IDs

    Raises:
        TypeError: 传入了 add() 或 memorize() 不支持的关键字参数。
        ValueError: 没有数据被摄取。

    Example:
        >>> import m_flow
        >>>
        >>> # Simple usage
        >>> result = await m_flow.ingest("Document content...")
        >>> print(result.status)  # IngestStatus.COMPLETED
        >>>
        >>> # Specify dataset name
        >>> result = await m_flow.ingest(["doc1.txt", "doc2.txt"], dataset_name="my_docs")
        >>>
        >>> # Background processing
        >>> result = await m_flow.ingest(data, run_in_background=True)
        >>> print(result.status)  # IngestStatus.BACKGROUND_STARTED
    """
    # ---------------------------------------------------------------------------
    # Content Type Validation
    # ---------------------------------------------------------------------------
    # When enable_content_routing=True (default), content_type must be declared

    def _env_bool(env_var: str, default: bool) -> bool:
        """Parse environment variable as boolean (consistent with memorize.py)."""
        val = os.getenv(env_var, str(default).lower()).lower()
        return val in ("1", "true", "yes", "y", "on")

    # Only validate if memorize will be executed
    if not skip_memorize:
        # Determine if content_routing is enabled
        enable_content_routing = kwargs.get("enable_content_routing")
        if enable_content_routing is None:
            enable_content_routing = _env_bool("MFLOW_CONTENT_ROUTING", True)

        content_type = kwargs.get("content_type")

        if enable_content_routing and content_type is None:
            # Default to TEXT when content_type is not specified
            from m_flow.shared.enums import ContentType as CT

            content_type = CT.TEXT
            kwargs["content_type"] = content_type

    # Use default dataset_name
    if not dataset_name:
        dataset_name = "main_dataset"

    logger.info(f"[ingest] Starting ingestion to dataset '{dataset_name}'")

    add_params, memorize_params = _get_param_sets()
    all_valid_params = add_params | memorize_params

    # Detect invalid parameters
    invalid_params = set(kwargs.keys()) - all_valid_params - {"datasets"}
    if invalid_params:
        logger.warning(f"[ingest] Invalid parameters detected: {invalid_params}")
        raise TypeError(
            f"ingest() got unexpected keyword argument(s): {invalid_params}. Valid params: {sorted(all_valid_params)}"
        )

    # Warning: datasets parameter is ignored
    if "datasets" in kwargs:
        logger.warning(
            "[ingest] 'datasets' parameter is ignored. "
            "ingest() always processes the data just added. "
            "Use memorize() directly for processing other datasets."
        )

    # Auto-separate parameters
    add_kwargs = {k: v for k, v in kwargs.items() if k in add_params}
    memorize_kwargs = {k: v for k, v in kwargs.items() if k in memorize_params}

    # Memorize loads *all* Data rows for the dataset each call (see pipeline._execute_for_dataset).
    # incremental_loading=False on memorize would re-run full extraction on every row on every
    # /ingest (O(n²) cost, duplicate graph/vector risk). Only add() should follow the request's
    # incremental_loading unless memorize_incremental_loading is set explicitly.
    memorize_kwargs.pop("memorize_incremental_loading", None)
    mi = kwargs.get("memorize_incremental_loading")
    memorize_kwargs["incremental_loading"] = True if mi is None else mi

    # Determine if background mode requested
    run_in_background = kwargs.get("run_in_background", False)

    # Step 1: Add (always sync — memorize needs the dataset_id)
    add_result: Optional[RunEvent] = await add(
        data=data,
        dataset_name=dataset_name,
        **add_kwargs,
    )

    # Edge case: add() returns None
    if add_result is None:
        logger.warning("[ingest] add() returned None - no data was ingested")
        raise ValueError("No data was ingested. Check if data is empty or already processed.")

    if skip_memorize:
        logger.info(f"[ingest] Skipping memorize (skip_memorize=True), dataset_id={add_result.dataset_id}")
        return IngestResult(
            dataset_id=add_result.dataset_id,
            dataset_name=add_result.dataset_name,
            status=IngestStatus.MEMORIZE_SKIPPED,
            add_run_id=add_result.workflow_run_id,
            memorize_run_id=None,
        )

    # Step 2: Memorize
    actual_dataset_name = add_result.dataset_name

    if run_in_background:
        # Run memorize in background — fire and return immediately
        async def _memorize_bg():
            try:
                await memorize(datasets=[actual_dataset_name], **memorize_kwargs)
            except Exception as e:
                logger.warning(f"[ingest] background memorize failed: {e}", exc_info=True)

        asyncio.create_task(_memorize_bg())
        logger.info(f"[ingest] Background ingest started, dataset_id={add_result.dataset_id}")
        return IngestResult(
            dataset_id=add_result.dataset_id,
            dataset_name=add_result.dataset_name,
            status=IngestStatus.BACKGROUND_STARTED,
            add_run_id=add_result.workflow_run_id,
            memorize_run_id=None,
        )

    # Synchronous memorize (original behavior)
    try:
        memorize_result = await memorize(
            datasets=[actual_dataset_name],
            **memorize_kwargs,
        )
    except Exception as e:
        logger.warning(f"[ingest] memorize failed after add succeeded: {e}", exc_info=True)
        return IngestResult(
            dataset_id=add_result.dataset_id,
            dataset_name=add_result.dataset_name,
            status=IngestStatus.MEMORIZE_FAILED,
            add_run_id=add_result.workflow_run_id,
            memorize_run_id=None,
            error_message=str(e),
        )

    # Extract memorize run_id
    memorize_run_id = _extract_memorize_run_id(memorize_result, add_result.dataset_id)

    logger.info(f"[ingest] Completed with status=COMPLETED, dataset_id={add_result.dataset_id}")

    return IngestResult(
        dataset_id=add_result.dataset_id,
        dataset_name=add_result.dataset_name,
        status=IngestStatus.COMPLETED,
        add_run_id=add_result.workflow_run_id,
        memorize_run_id=memorize_run_id,
    )


def _extract_memorize_run_id(
    memorize_result: Any,
    dataset_id: UUID,
) -> Optional[UUID]:
    """Extract run_id from memorize() return value."""
    if not isinstance(memorize_result, dict):
        logger.debug(f"[ingest] memorize returned unexpected type: {type(memorize_result).__name__}")
        return None

    if not memorize_result:
        return None

    # Try multiple key formats
    ds_id_str = str(dataset_id)

    run_detail = None
    if ds_id_str in memorize_result:
        run_detail = memorize_result[ds_id_str]
    elif dataset_id in memorize_result:
        run_detail = memorize_result[dataset_id]
    elif len(memorize_result) == 1:
        # If only one result, use it directly
        run_detail = next(iter(memorize_result.values()))
    else:
        logger.debug(
            f"[ingest] memorize returned dict without expected dataset_id. "
            f"Expected: {ds_id_str}, Got keys: {list(memorize_result.keys())}"
        )

    if run_detail:
        return getattr(run_detail, "workflow_run_id", None)

    return None
