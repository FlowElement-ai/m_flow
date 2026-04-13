# m_flow/memory/procedural/governance/update_usage_stats.py
"""
Usage statistics update for procedural memory

Update Procedure usage statistics.
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from m_flow.shared.tracing import TraceManager
from m_flow.shared.logging_utils import get_logger

logger = get_logger()


@dataclass
class UsageStats:
    """Usage statistics"""

    procedure_id: str
    last_used_at: float  # Unix timestamp
    used_count: int
    inject_count: int = 0  # Injection count
    retrieve_count: int = 0  # Retrieval count


class UsageTracker:
    """Usage statistics tracker"""

    async def update_usage(
        self,
        procedure_ids: List[str],
        graph_engine: Any,
        usage_type: str = "inject",  # inject | retrieve
    ) -> List[UsageStats]:
        """
        Update usage statistics.

        Args:
            procedure_ids: List of Procedure IDs
            graph_engine: Graph engine
            usage_type: Usage type

        Returns:
            List[UsageStats]
        """
        if not procedure_ids:
            return []

        results = []
        current_ts = time.time()

        for proc_id in procedure_ids:
            try:
                stats = await self._update_single(proc_id, graph_engine, usage_type, current_ts)
                if stats:
                    results.append(stats)
            except Exception as e:
                logger.error(f"Failed to update usage for {proc_id}: {e}")

        # Tracing
        TraceManager.event(
            "procedural.usage.updated",
            {
                "count": len(results),
                "usage_type": usage_type,
                "procedure_ids": procedure_ids[:5],
            },
        )

        return results

    async def _update_single(
        self,
        procedure_id: str,
        graph_engine: Any,
        usage_type: str,
        current_ts: float,
    ) -> Optional[UsageStats]:
        """Update statistics for a single procedure"""

        try:
            nodes, _ = await graph_engine.query_by_attributes([{"id": [procedure_id], "type": ["Procedure"]}])
        except Exception as e:
            logger.error(f"Failed to read usage for {procedure_id}: {e}")
            return None

        if not nodes:
            return None

        _, props = nodes[0]
        props = props if isinstance(props, dict) else {}
        used_count = (props.get("used_count") or 0) + 1
        inject_count = props.get("inject_count") or 0
        retrieve_count = props.get("retrieve_count") or 0

        if usage_type == "inject":
            inject_count += 1
        else:
            retrieve_count += 1

        try:
            await graph_engine.update_node(
                procedure_id,
                {
                    "last_used_at": current_ts,
                    "used_count": used_count,
                    "inject_count": inject_count,
                    "retrieve_count": retrieve_count,
                },
            )
        except Exception as e:
            logger.error(f"Failed to update usage for {procedure_id}: {e}")
            return None

        return UsageStats(
            procedure_id=procedure_id,
            last_used_at=current_ts,
            used_count=used_count,
            inject_count=inject_count,
            retrieve_count=retrieve_count,
        )


# ========== Convenience Functions ==========

_tracker = UsageTracker()


async def update_usage_stats(
    procedure_ids: List[str],
    graph_engine: Any,
    usage_type: str = "inject",
) -> List[UsageStats]:
    """
    Update Procedure usage statistics.

    Args:
        procedure_ids: List of Procedure IDs
        graph_engine: Graph engine
        usage_type: Usage type (inject | retrieve)

    Returns:
        List[UsageStats]
    """
    return await _tracker.update_usage(procedure_ids, graph_engine, usage_type)
