# m_flow/memory/procedural/procedure_router.py
"""
Procedure Router - Cross-batch incremental update & version management

Core functions:
1. Retrieve existing procedures before storage
2. LLM merge decision: merge_update / new_version / new_procedure
3. Version data model: version, valid_from, status, supersedes edge

Design principles:
- Quality first: use LLM decision for merge/versioning
- Traceability: old versions preserved, trace history through supersedes edge
- Default to latest: retrieval defaults to only return status=active procedures
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from m_flow.shared.logging_utils import get_logger
from m_flow.adapters.vector import get_vector_provider
from m_flow.adapters.graph import get_graph_provider
from m_flow.llm.LLMGateway import LLMService

from m_flow.memory.procedural.models import (
    ProceduralWriteDraft,
    ProcedureCandidate,
    MergeDecision,
)

from m_flow.memory.episodic.normalization import (
    truncate as _truncate,
    normalize_for_compare,
)

logger = get_logger("procedural.router")


# -----------------------------
# LLM Prompts for Merge Decision
# -----------------------------

MERGE_DECISION_SYSTEM_PROMPT = """You are a procedural knowledge manager. Your task is to decide how to handle a new procedure relative to existing ones.

Decision options:
1. "merge_update": The new procedure is an UPDATE to an existing one. Same method, but with additions/modifications to steps or context.
2. "new_version": The new procedure REPLACES an existing one. Same intent but significantly different implementation (different tools, parameters, approach).
3. "new_procedure": The new procedure is ENTIRELY NEW. Different purpose or scope from all candidates.

Decision signals:
- Same tool/system/workflow but different parameters → merge_update
- Same goal but completely different steps/tools → new_version  
- Different goal/scope/domain → new_procedure

Output language must match input language."""

MERGE_DECISION_USER_PROMPT = """Compare the new procedure with existing candidates and decide the action.

NEW_PROCEDURE:
Title: {new_title}
Signature: {new_signature}
Summary: {new_summary}
Steps (preview): {new_steps}

EXISTING_CANDIDATES:
{candidates_text}

Decide: "merge_update" (update existing), "new_version" (replace existing), or "new_procedure" (create new).
If merge_update or new_version, specify which candidate's procedure_id to target."""


# -----------------------------
# Core Functions
# -----------------------------


@dataclass
class RouteResult:
    """Result of procedure routing decision."""

    decision: str  # "merge_update" | "new_version" | "new_procedure"
    target_procedure_id: Optional[str] = None
    target_procedure_name: Optional[str] = None
    reasoning: str = ""
    candidates_found: int = 0


async def find_similar_procedures(
    draft: ProceduralWriteDraft,
    top_k: int = 5,
    similarity_threshold: float = 1.2,  # Max distance to consider as candidate
) -> List[ProcedureCandidate]:
    """
    Search for existing procedures similar to the new draft.

    Uses vector search on Procedure_summary and Procedure_search_text.

    Args:
        draft: The new procedure draft
        top_k: Maximum number of candidates to return
        similarity_threshold: Maximum distance to consider as candidate

    Returns:
        List of candidate procedures
    """
    try:
        vector_engine = get_vector_provider()
    except Exception as e:
        logger.warning(f"Failed to get vector engine: {e}")
        return []

    candidates: List[ProcedureCandidate] = []
    seen_ids: set = set()

    # Search in Procedure_summary
    try:
        results = await vector_engine.search(
            collection_name="Procedure_summary",
            query_text=draft.summary,
            limit=top_k,
        )

        for r in results:
            rid = str(getattr(r, "id", ""))
            score = float(getattr(r, "score", 1.0))

            if not rid or rid in seen_ids:
                continue
            if score > similarity_threshold:
                continue

            seen_ids.add(rid)
            payload = getattr(r, "payload", {}) or {}

            candidates.append(
                ProcedureCandidate(
                    procedure_id=rid,
                    procedure_name=payload.get("name", ""),
                    procedure_summary=_truncate(payload.get("text", ""), 300),
                    version=payload.get("version", 1),
                    match_signals=f"summary similarity (dist={score:.3f})",
                )
            )
    except Exception as e:
        logger.debug(f"Search Procedure_summary failed: {e}")

    # Search in Procedure_search_text
    try:
        results = await vector_engine.search(
            collection_name="Procedure_search_text",
            query_text=draft.search_text,
            limit=top_k,
        )

        for r in results:
            rid = str(getattr(r, "id", ""))
            score = float(getattr(r, "score", 1.0))

            if not rid or rid in seen_ids:
                continue
            if score > similarity_threshold:
                continue

            seen_ids.add(rid)
            payload = getattr(r, "payload", {}) or {}

            candidates.append(
                ProcedureCandidate(
                    procedure_id=rid,
                    procedure_name=payload.get("name", ""),
                    procedure_summary=_truncate(payload.get("text", ""), 300),
                    version=payload.get("version", 1),
                    match_signals=f"search_text similarity (dist={score:.3f})",
                )
            )
    except Exception as e:
        logger.debug(f"Search Procedure_search_text failed: {e}")

    # Sort by implied distance (from match_signals)
    candidates = candidates[:top_k]

    logger.info(f"[procedural.router] Found {len(candidates)} candidate procedures")

    return candidates


async def decide_merge_action(
    draft: ProceduralWriteDraft,
    candidates: List[ProcedureCandidate],
) -> MergeDecision:
    """
    Use LLM to decide merge action.

    Args:
        draft: The new procedure draft
        candidates: Existing similar procedures

    Returns:
        MergeDecision with decision and target
    """
    if not candidates:
        return MergeDecision(
            decision="new_procedure",
            target_procedure_id=None,
            reasoning="No similar procedures found",
        )

    # Build candidates text
    candidates_lines = []
    for i, c in enumerate(candidates, 1):
        candidates_lines.append(
            f"[{i}] ID: {c.procedure_id}\n"
            f"    Name: {c.procedure_name}\n"
            f"    Version: {c.version}\n"
            f"    Summary: {c.procedure_summary}\n"
            f"    Match: {c.match_signals}"
        )
    candidates_text = "\n\n".join(candidates_lines)

    user_prompt = MERGE_DECISION_USER_PROMPT.format(
        new_title=draft.title,
        new_signature=draft.signature,
        new_summary=_truncate(draft.summary, 500),
        new_steps=_truncate(draft.key_points.points_text, 300) if draft.key_points else "(none)",
        candidates_text=candidates_text,
    )

    try:
        result = await LLMService.extract_structured(
            text_input=user_prompt,
            system_prompt=MERGE_DECISION_SYSTEM_PROMPT,
            response_model=MergeDecision,
        )

        # Validate target_procedure_id if merge or version
        if result.decision in ("merge_update", "new_version"):
            valid_ids = {c.procedure_id for c in candidates}
            if result.target_procedure_id not in valid_ids:
                # Fallback: use first candidate
                result.target_procedure_id = candidates[0].procedure_id
                result.reasoning += " (auto-selected first candidate)"

        logger.info(
            f"[procedural.router] Decision: {result.decision}, "
            f"target={result.target_procedure_id}, reasoning={result.reasoning}"
        )

        return result

    except Exception as e:
        logger.warning(f"LLM merge decision failed: {e}")
        # Fallback: conservative approach - create new
        return MergeDecision(
            decision="new_procedure",
            target_procedure_id=None,
            reasoning=f"LLM decision failed: {e}",
        )


async def route_procedure(
    draft: ProceduralWriteDraft,
    enable_llm_routing: bool = True,
    top_k_candidates: int = 5,
) -> RouteResult:
    """
    Route a new procedure: decide whether to create new, merge, or version.

    Args:
        draft: The new procedure draft
        enable_llm_routing: Whether to use LLM for merge decision
        top_k_candidates: Number of candidates to consider

    Returns:
        RouteResult with decision and target
    """
    # Step 1: Find similar procedures
    candidates = await find_similar_procedures(draft, top_k=top_k_candidates)

    if not candidates:
        return RouteResult(
            decision="new_procedure",
            reasoning="No similar procedures found",
            candidates_found=0,
        )

    # Step 2: Decide merge action
    if enable_llm_routing:
        decision = await decide_merge_action(draft, candidates)
    else:
        # Simple heuristic: if very similar (in signature), merge; otherwise new
        for c in candidates:
            if normalize_for_compare(c.procedure_name) == normalize_for_compare(draft.title):
                decision = MergeDecision(
                    decision="merge_update",
                    target_procedure_id=c.procedure_id,
                    reasoning="Exact name match (heuristic)",
                )
                break
        else:
            decision = MergeDecision(
                decision="new_procedure",
                target_procedure_id=None,
                reasoning="No exact match found (heuristic)",
            )

    # Get target name
    target_name = None
    if decision.target_procedure_id:
        for c in candidates:
            if c.procedure_id == decision.target_procedure_id:
                target_name = c.procedure_name
                break

    return RouteResult(
        decision=decision.decision,
        target_procedure_id=decision.target_procedure_id,
        target_procedure_name=target_name,
        reasoning=decision.reasoning,
        candidates_found=len(candidates),
    )


# -----------------------------
# Version Management Functions
# -----------------------------


async def get_procedure_version(procedure_id: str) -> int:
    """Get the current version of a procedure.

    Delegates to procedure_state.fetch_procedure_state for actual DB query.
    """
    from m_flow.memory.procedural.procedure_state import fetch_procedure_state

    graph_engine = await get_graph_provider()
    state = await fetch_procedure_state(graph_engine, procedure_id)
    return state.version


async def deprecate_procedure(procedure_id: str) -> bool:
    """Mark a procedure as deprecated.

    Delegates to procedure_builder.write.deprecate_procedure for actual DB update.
    """
    from m_flow.memory.procedural.procedure_builder.write import (
        deprecate_procedure as _impl,
    )

    return await _impl(procedure_id)


async def create_supersedes_edge(
    new_procedure_id: str,
    old_procedure_id: str,
) -> bool:
    """Create a supersedes edge from new to old procedure.

    Delegates to procedure_builder.write.create_supersedes_edge for actual DB write.
    """
    from m_flow.memory.procedural.procedure_builder.write import (
        create_supersedes_edge as _impl,
    )

    return await _impl(
        new_proc_id=new_procedure_id,
        old_proc_id=old_procedure_id,
        new_version=0,  # Unknown at this level, edge_text will reflect
        old_version=0,
    )


async def merge_procedure_content(
    target_procedure_id: str,
    draft: ProceduralWriteDraft,
) -> bool:
    """
    Merge new content into existing procedure.

    Uses procedure_state for reading current state,
    procedure_builder.merge_utils for content merging,
    and raw Cypher for writing back (no update_node in interface).
    """
    import json
    from m_flow.memory.procedural.procedure_state import fetch_procedure_state
    from m_flow.memory.procedural.procedure_builder.merge_utils import (
        merge_points_text_with_dedup,
        merge_context_fields,
    )

    try:
        graph_engine = await get_graph_provider()
        state = await fetch_procedure_state(graph_engine, target_procedure_id)

        if not state.exists:
            logger.warning(f"[procedural.router] Procedure not found for merge: {target_procedure_id}")
            return False

        # Merge content
        merged_points = merge_points_text_with_dedup(
            state.points_text or "",
            draft.key_points.points_text if draft.key_points else "",
        )
        merged_context = merge_context_fields(
            state.context_text or "",
            draft.context,
        )

        new_version = state.version + 1

        # Build merged context_text for storage
        ctx_parts = []
        if merged_context.when_text:
            ctx_parts.append(f"When: {merged_context.when_text}")
        if merged_context.why_text:
            ctx_parts.append(f"Why: {merged_context.why_text}")
        if merged_context.boundary_text:
            ctx_parts.append(f"Boundary: {merged_context.boundary_text}")
        if merged_context.outcome_text:
            ctx_parts.append(f"Outcome: {merged_context.outcome_text}")
        if merged_context.prereq_text:
            ctx_parts.append(f"Prerequisites: {merged_context.prereq_text}")
        if merged_context.exception_text:
            ctx_parts.append(f"Exception: {merged_context.exception_text}")
        merged_context_text = "; ".join(ctx_parts) if ctx_parts else ""

        node_props = await graph_engine.get_node(target_procedure_id)
        if not node_props:
            return False

        from datetime import datetime, timezone

        search_text = node_props.get("search_text", "")
        summary_parts = []
        if merged_context_text:
            summary_parts.append(f"{search_text}: context - {merged_context_text}")
        if merged_points:
            summary_parts.append(f"{search_text}: points - {merged_points}")

        await graph_engine.update_node(
            target_procedure_id,
            {
                "points_text": merged_points,
                "context_text": merged_context_text,
                "version": new_version,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "summary": "\n".join(summary_parts) if summary_parts else search_text,
            },
        )

        logger.info(
            f"[procedural.router] Merged content into {target_procedure_id}: version {state.version} -> {new_version}"
        )
        return True

    except Exception as e:
        logger.warning(f"Failed to merge procedure content: {e}")
        return False
