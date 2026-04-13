# m_flow/memory/procedural/procedure_builder/write.py
"""
Write: Branch write logic with version management.

Mirrors episodic/episode_builder/step35_node_edge_creation.py pattern.
Handles create_new, patch (UPSERT), new_version (deprecate+supersedes), and skip.
"""

from __future__ import annotations

import json
from typing import List, Optional

from m_flow.shared.logging_utils import get_logger
from m_flow.core import Edge
from m_flow.core.domain.models import Procedure
from m_flow.core.domain.models.memory_space import MemorySpace
from m_flow.core.domain.utils.generate_node_id import generate_node_id
from m_flow.adapters.graph import get_graph_provider
from m_flow.retrieval.time import extract_mentioned_time

from m_flow.memory.procedural.models import (
    ProceduralWriteDraft,
    KeyPointsPackDraft,
)
from m_flow.memory.procedural.procedural_points_builder import (
    build_step_points,
    build_context_points,
    normalize_for_id,
)

from .pipeline_contexts import MergeAction, ExistingProcedureInfo
from .merge_utils import merge_points_text_with_dedup, merge_context_fields

logger = get_logger("procedural.incremental.write")


# ============================================================
# Branch Write Entry Point
# ============================================================


async def write_by_action(
    action: MergeAction,
    draft: ProceduralWriteDraft,
    nodeset: MemorySpace,
    existing_procedure: Optional[ExistingProcedureInfo] = None,
    source_refs: Optional[List[str]] = None,
) -> Optional[Procedure]:
    """
    Branch write: different write logic for different actions.

    - create_new: Standard write (version 1)
    - patch: Merge points_text, same ID (UPSERT update)
    - new_version: New ID with version suffix, deprecate old, create supersedes edge
    - skip: Return None

    Args:
        action: Merge action from decision stage
        draft: Compiled ProceduralWriteDraft
        nodeset: MemorySpace for graph organization
        existing_procedure: Info about existing procedure (for patch/new_version)
        source_refs: Source tracing

    Returns:
        Procedure MemoryNode or None
    """
    logger.info(
        f"[procedural.incremental.write] Writing action={action.value}, "
        f"title='{draft.title[:40] if draft.title else '?'}', "
        f"existing={existing_procedure.procedure_id[:12] + '...' if existing_procedure else 'none'}"
    )

    if action == MergeAction.skip:
        return None

    if action == MergeAction.create_new:
        return await _write_create_new(draft, nodeset, source_refs)

    if action == MergeAction.patch:
        if not existing_procedure:
            logger.warning(
                "[procedural.incremental.write] patch write but no existing_procedure, fallback to create_new"
            )
            return await _write_create_new(draft, nodeset, source_refs)
        return await _write_patch(draft, existing_procedure, nodeset, source_refs)

    if action == MergeAction.new_version:
        if not existing_procedure:
            logger.warning(
                "[procedural.incremental.write] new_version write but no existing_procedure, fallback to create_new"
            )
            return await _write_create_new(draft, nodeset, source_refs)
        return await _write_new_version(draft, existing_procedure, nodeset, source_refs)

    return None


# ============================================================
# Write Implementations
# ============================================================


async def _write_create_new(
    draft: ProceduralWriteDraft,
    nodeset: MemorySpace,
    source_refs: Optional[List[str]] = None,
) -> Optional[Procedure]:
    """create_new: Standard write with version 1."""
    return await build_procedure_node(
        draft=draft,
        nodeset=nodeset,
        version=1,
        source_refs=source_refs,
    )


async def _write_patch(
    draft: ProceduralWriteDraft,
    existing: ExistingProcedureInfo,
    nodeset: MemorySpace,
    source_refs: Optional[List[str]] = None,
) -> Optional[Procedure]:
    """
    patch: Merge points_text with dedup, keep same signature/ID.

    Critical: Force using existing signature to ensure same ID for UPSERT.
    """
    # Merge points_text with line-level dedup
    merged_points_text = merge_points_text_with_dedup(
        existing.points_text,
        draft.key_points.points_text if draft.key_points else "",
    )

    # Merge context (new adds to existing)
    merged_context = merge_context_fields(existing.context_text, draft.context)

    # Build merged draft with FORCED existing signature
    merged_draft = ProceduralWriteDraft(
        title=existing.title,  # Keep existing
        signature=existing.signature,  # ★ Force existing signature for same ID
        search_text=existing.search_text,  # Keep existing
        key_points=KeyPointsPackDraft(points_text=merged_points_text),
        context=merged_context,
    )

    # Build and write (ID based on signature → same ID → UPSERT update)
    return await build_procedure_node(
        draft=merged_draft,
        nodeset=nodeset,
        version=existing.version,  # Keep same version
        source_refs=source_refs,
    )


async def _write_new_version(
    draft: ProceduralWriteDraft,
    existing: ExistingProcedureInfo,
    nodeset: MemorySpace,
    source_refs: Optional[List[str]] = None,
) -> Optional[Procedure]:
    """
    new_version: Create new Procedure with version suffix in ID.
    Deprecate old Procedure, attach supersedes edge to the model.

    IMPORTANT: The supersedes edge is set on the Procedure model's `supersedes` field,
    NOT created via graph_engine.add_edge(). This ensures the edge is created by
    persist_memory_nodes → extract_graph at the same time as the node, avoiding
    the race condition where the new node doesn't exist in the graph yet.
    """
    new_version = existing.version + 1

    # Build new Procedure with version suffix in ID
    new_proc = await build_procedure_node(
        draft=draft,
        nodeset=nodeset,
        version=new_version,
        source_refs=source_refs,
        id_version_suffix=f":v{new_version}",  # ID = Procedure:{sig}:v{n+1}
    )

    if not new_proc:
        return None

    # Deprecate old Procedure (updates status in graph via raw Cypher)
    try:
        await deprecate_procedure(existing.procedure_id)
        logger.info(f"[procedural.incremental.write] Deprecated old procedure: {existing.procedure_id}")
    except Exception as e:
        logger.warning(f"[procedural.incremental.write] Failed to deprecate old procedure: {e}")

    # Attach supersedes edge to the Procedure MODEL (not directly to graph!)
    # This ensures extract_graph creates the edge when persist_memory_nodes writes the node.
    # Old approach of calling graph_engine.add_edge() here would silently fail because
    # the new node hasn't been written to the graph yet at this point.
    try:
        # Create reference to old procedure for the supersedes edge target.
        # IMPORTANT: Must include ALL available data (points_text, context_text, etc.)
        # because extract_graph will extract this as a node for UPSERT.
        # Missing fields would overwrite the existing node's rich content with None.
        old_proc_ref = Procedure(
            id=existing.procedure_id,
            name=existing.title,
            summary=existing.summary or "",
            search_text=existing.search_text,
            signature=existing.signature,
            version=existing.version,
            status="deprecated",
            points_text=existing.points_text or None,
            context_text=existing.context_text or None,
        )

        supersedes_edge = Edge(
            relationship_type="supersedes",
            edge_text=(f"version: v{new_version} supersedes v{existing.version}"),
        )

        if new_proc.supersedes is None:
            new_proc.supersedes = []
        new_proc.supersedes.append((supersedes_edge, old_proc_ref))

        logger.info(
            f"[procedural.incremental.write] Attached supersedes edge on model: v{new_version} → v{existing.version}"
        )
    except Exception as e:
        logger.warning(f"[procedural.incremental.write] Failed to attach supersedes edge: {e}")

    return new_proc


# ============================================================
# Version Management Functions
# ============================================================


async def deprecate_procedure(procedure_id: str) -> bool:
    """
    Update Procedure status to 'deprecated'.

    Since Kuzu stores all properties in a JSON column, we need to:
    1. Read the existing properties JSON
    2. Parse and update the status field
    3. Write back the updated JSON

    Note: Uses raw Cypher because GraphProvider lacks update_node_property().
    This is a known limitation documented in the plan.

    Returns:
        True if successful, False otherwise
    """
    try:
        graph_engine = await get_graph_provider()

        node_props = await graph_engine.get_node(procedure_id)
        if not node_props:
            logger.warning(f"[procedural.incremental.write] Procedure not found for deprecation: {procedure_id}")
            return False

        await graph_engine.update_node(procedure_id, {"status": "deprecated"})

        logger.debug(f"[procedural.incremental.write] Procedure {procedure_id} marked as deprecated")
        return True

    except Exception as e:
        logger.warning(f"[procedural.incremental.write] Failed to deprecate procedure: {e}")
        return False


async def create_supersedes_edge(
    new_proc_id: str,
    old_proc_id: str,
    new_version: int,
    old_version: int,
    reason: str = "",
) -> bool:
    """
    Create supersedes edge from new Procedure to old Procedure.

    Returns:
        True if successful, False otherwise
    """
    try:
        graph_engine = await get_graph_provider()

        # Use positional args for adapter-agnostic compatibility
        # (Kuzu uses from_node=, Neo4j uses from_node=, Neptune uses source_id=)
        await graph_engine.add_edge(
            new_proc_id,
            old_proc_id,
            "supersedes",
            {
                "edge_text": f"version: v{new_version} supersedes v{old_version}",
                "old_version": old_version,
                "new_version": new_version,
                "reason": reason,
            },
        )
        return True

    except Exception as e:
        logger.warning(f"[procedural.incremental.write] Failed to create supersedes edge: {e}")
        return False


# ============================================================
# Procedure Node Builder
# ============================================================


async def build_procedure_node(
    draft: ProceduralWriteDraft,
    nodeset: MemorySpace,
    version: int = 1,
    source_refs: Optional[List[str]] = None,
    id_version_suffix: str = "",
) -> Optional[Procedure]:
    """
    Build Procedure MemoryNode with direct edges to Points (no Pack nodes).

    Architecture: Procedure → Point (2-layer triplet, no Pack intermediate).

    Args:
        draft: Compiled ProceduralWriteDraft
        nodeset: MemorySpace for graph organization
        version: Version number
        source_refs: Source tracing refs
        id_version_suffix: Suffix for versioned IDs (e.g., ":v2")

    Returns:
        Procedure MemoryNode or None
    """
    # Generate ID (deterministic based on signature + optional version suffix)
    base_id = f"Procedure:{normalize_for_id(draft.signature)}"
    procedure_id = str(generate_node_id(base_id + id_version_suffix))

    # Build context_text and points_text (display attributes)
    context_parts = []
    if draft.context.when_text:
        context_parts.append(f"When: {draft.context.when_text}")
    if draft.context.why_text:
        context_parts.append(f"Why: {draft.context.why_text}")
    if draft.context.boundary_text:
        context_parts.append(f"Boundary: {draft.context.boundary_text}")
    if draft.context.outcome_text:
        context_parts.append(f"Outcome: {draft.context.outcome_text}")
    if draft.context.prereq_text:
        context_parts.append(f"Prerequisites: {draft.context.prereq_text}")
    if draft.context.exception_text:
        context_parts.append(f"Exception: {draft.context.exception_text}")
    context_text = "; ".join(context_parts) if context_parts else ""

    points_text = draft.key_points.points_text or ""

    # Build summary (only indexed field)
    summary_parts = []
    if context_text:
        summary_parts.append(f"{draft.search_text}: context - {context_text}")
    if points_text:
        summary_parts.append(f"{draft.search_text}: points - {points_text}")
    constructed_summary = "\n".join(summary_parts) if summary_parts else draft.search_text

    # Time extraction (from constructed summary)
    time_result = extract_mentioned_time(
        text=constructed_summary,
        anchor_time_ms=None,
        min_confidence=0.5,
    )

    procedure_time_fields = {
        "mentioned_time_start_ms": time_result.start_ms if time_result.has_time else None,
        "mentioned_time_end_ms": time_result.end_ms if time_result.has_time else None,
        "mentioned_time_confidence": time_result.confidence if time_result.has_time else None,
        "mentioned_time_text": time_result.evidence_text if time_result.has_time else None,
    }

    # Build ContextPoints
    context_point_pairs = build_context_points(
        context_pack_id=procedure_id,
        when_text=draft.context.when_text,
        why_text=draft.context.why_text,
        boundary_text=draft.context.boundary_text,
        outcome_text=draft.context.outcome_text,
        prereq_text=draft.context.prereq_text,
        exception_text=draft.context.exception_text,
        context_anchor_text=context_text,
        time_fields=procedure_time_fields,
    )
    if context_point_pairs:
        for _, point in context_point_pairs:
            point.memory_spaces = [nodeset]

    # Build KeyPoints
    key_point_pairs = []
    if points_text:
        key_point_pairs = build_step_points(
            steps_pack_id=procedure_id,
            steps_anchor_text=points_text,
            time_fields=procedure_time_fields,
        )
        if key_point_pairs:
            for _, point in key_point_pairs:
                point.memory_spaces = [nodeset]

    # Create Procedure node
    procedure = Procedure(
        id=procedure_id,
        name=draft.title,
        title=draft.title,
        signature=draft.signature,
        search_text=draft.search_text,
        summary=constructed_summary,
        context_text=context_text,
        points_text=points_text,
        version=version,
        status="active",
        memory_spaces=[nodeset],
        source_refs=source_refs,
        has_context_point=context_point_pairs if context_point_pairs else None,
        has_key_point=key_point_pairs if key_point_pairs else None,
        **procedure_time_fields,
    )

    logger.info(
        f"[procedural.incremental.write] Built procedure: id={procedure_id[:12]}..., "
        f"title={draft.title[:30]}, version=v{version}, "
        f"context_points={len(context_point_pairs) if context_point_pairs else 0}, "
        f"key_points={len(key_point_pairs) if key_point_pairs else 0}"
    )

    return procedure
