# m_flow/memory/procedural/governance/reconcile_active.py
"""
Active governance: reconcile procedure active status

Ensure only one active per procedure_key.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional

from m_flow.shared.tracing import TraceManager
from m_flow.shared.logging_utils import get_logger

logger = get_logger()


@dataclass
class ReconcileResult:
    """Reconcile result"""

    key: str
    deactivated_count: int
    active_id: Optional[str]
    versions_found: List[int] = field(default_factory=list)


class ActiveReconciler:
    """Active state reconciler"""

    async def reconcile(
        self,
        procedure_key: str,
        graph_engine: Any,
    ) -> ReconcileResult:
        """
        Reconcile Active state.

        Ensure only one active per procedure_key.

        Args:
            procedure_key: Procedure key
            graph_engine: Graph engine

        Returns:
            ReconcileResult
        """
        try:
            all_nodes, _ = await graph_engine.query_by_attributes([{"type": ["Procedure"]}])
        except Exception as e:
            logger.error(f"Failed to query procedures for key={procedure_key}: {e}")
            return ReconcileResult(
                key=procedure_key,
                deactivated_count=0,
                active_id=None,
            )

        rows = []
        for nid, props in all_nodes:
            p = props if isinstance(props, dict) else {}
            if p.get("signature") == procedure_key or p.get("procedure_key") == procedure_key:
                rows.append(
                    {
                        "id": nid,
                        "version": p.get("version"),
                        "status": p.get("status"),
                        "updated_at": p.get("updated_at"),
                    }
                )

        if not rows:
            return ReconcileResult(
                key=procedure_key,
                deactivated_count=0,
                active_id=None,
            )

        # Find version that should be active (latest version)
        versions_found = [r.get("version", 1) for r in rows]

        # Highest version as active
        rows_sorted = sorted(rows, key=lambda r: (r.get("version", 0), r.get("updated_at", 0)), reverse=True)
        active_row = rows_sorted[0]
        active_id = active_row.get("id")

        # Set other versions to non-active
        deactivated_count = 0
        for row in rows_sorted[1:]:
            proc_id = row.get("id")
            if row.get("status") == "active":
                try:
                    await graph_engine.update_node(proc_id, {"status": "superseded"})
                    deactivated_count += 1
                except Exception as e:
                    logger.error(f"Failed to deactivate procedure {proc_id}: {e}")

        # Ensure active version status is correct
        if active_row.get("status") != "active":
            try:
                await graph_engine.update_node(active_id, {"status": "active"})
            except Exception as e:
                logger.error(f"Failed to activate procedure {active_id}: {e}")

        result = ReconcileResult(
            key=procedure_key,
            deactivated_count=deactivated_count,
            active_id=active_id,
            versions_found=versions_found,
        )

        # Tracing
        TraceManager.event(
            "procedural.active.reconciled",
            {
                "key": procedure_key,
                "deactivated_count": deactivated_count,
                "active_id": active_id,
                "versions_found": versions_found,
            },
        )

        return result


# ========== Convenience Functions ==========


async def reconcile_active(
    procedure_key: str,
    graph_engine: Any,
) -> ReconcileResult:
    """
    Reconcile Active state.

    Args:
        procedure_key: Procedure key
        graph_engine: Graph engine

    Returns:
        ReconcileResult
    """
    reconciler = ActiveReconciler()
    return await reconciler.reconcile(procedure_key, graph_engine)
