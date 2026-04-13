"""
Prune Procedural Data endpoint.

Surgically removes only Procedure, ProcedureStepPoint, ProcedureContextPoint
nodes and their edges from the graph database. Does NOT touch episodic data.
Also clears procedural_extracted marks on Episodes for clean re-extraction.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from m_flow.auth.methods import get_authenticated_user
from m_flow.auth.models import User
from m_flow.shared.logging_utils import get_logger

logger = get_logger("prune_procedural")

PROCEDURAL_NODE_TYPES = [
    "Procedure",
    "ProcedureStepPoint",
    "ProcedureContextPoint",
    "ProcedureStepsPack",
    "ProcedureContextPack",
]


class PruneProceduralRequest(BaseModel):
    dataset_id: Optional[UUID] = Field(
        default=None,
        description="Specific dataset to clean. If None, cleans all accessible datasets.",
    )


class PruneProceduralResponse(BaseModel):
    success: bool
    datasets_cleaned: int
    nodes_deleted: int
    episodes_unmarked: int
    message: str


def get_prune_procedural_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/procedural",
        response_model=PruneProceduralResponse,
        summary="Remove all procedural data from graph",
        description="Surgically removes Procedure nodes and edges without touching episodic data. "
        "Also clears procedural_extracted marks on Episodes.",
    )
    async def prune_procedural(
        request: PruneProceduralRequest,
        user: User = Depends(get_authenticated_user),
    ) -> PruneProceduralResponse:
        from m_flow.data.methods import get_authorized_existing_datasets
        from m_flow.context_global_variables import set_db_context
        from m_flow.adapters.graph import get_graph_provider

        authorized = await get_authorized_existing_datasets(
            [request.dataset_id] if request.dataset_id else [],
            "write",
            user,
        )

        if not authorized:
            return PruneProceduralResponse(
                success=True,
                datasets_cleaned=0,
                nodes_deleted=0,
                episodes_unmarked=0,
                message="No authorized datasets",
            )

        total_deleted = 0
        total_unmarked = 0
        cleaned = 0

        for ds in authorized:
            try:
                await set_db_context(ds.id, ds.owner_id)
                engine = await get_graph_provider()

                nodes, _ = await engine.query_by_attributes([{"type": PROCEDURAL_NODE_TYPES}])
                node_count = len(nodes)

                if node_count == 0:
                    continue

                node_ids = [n[0] for n in nodes]
                await engine.delete_nodes(node_ids)

                total_deleted += node_count

                ep_nodes, _ = await engine.query_by_attributes([{"type": ["Episode"]}])
                for ep_id, ep_props in ep_nodes:
                    props = ep_props if isinstance(ep_props, dict) else {}
                    if props.get("procedural_extracted"):
                        await engine.update_node(ep_id, {"procedural_extracted": None})
                        total_unmarked += 1

                cleaned += 1
                logger.info(f"[prune_procedural] {ds.name}: deleted {node_count} nodes, unmarked episodes")

            except Exception as e:
                logger.warning(f"[prune_procedural] Failed for {ds.name}: {e}")

        return PruneProceduralResponse(
            success=True,
            datasets_cleaned=cleaned,
            nodes_deleted=total_deleted,
            episodes_unmarked=total_unmarked,
            message=f"Removed {total_deleted} procedural nodes from {cleaned} dataset(s), cleared {total_unmarked} episode marks",
        )

    return router
