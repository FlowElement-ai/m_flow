# m_flow/memory/procedural/procedure_state.py
"""
Procedure State Fetching (mirrors episodic/state.py)

Fetch existing procedure state from graph before writing:
- title/name, signature, version, status (prevent drift)
- Existing KeyPoints list (prevent duplicates during patch)
- Existing ContextPoints list (prevent duplicates during patch)
- supersedes chain (version history)

Used by:
- procedure_builder: fetch full state before incremental update decisions
- procedure_router: get_procedure_version, deprecate_procedure, merge_procedure_content

Design: Uses adapter-agnostic graph_engine.get_node() + get_edges()
(same pattern as episodic/state.py, no raw Cypher needed for reads)
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from pydantic import BaseModel

from m_flow.shared.logging_utils import get_logger

logger = get_logger("procedural.state")


# ============================================================
# Data Classes (mirror episodic/state.py pattern)
# ============================================================


class ExistingKeyPoint(BaseModel):
    """Existing KeyPoint information (mirrors ExistingFacet).

    Used for deduplication during incremental patch updates.
    """

    id: str
    search_text: str
    step_number: Optional[int] = None
    description: Optional[str] = None


class ExistingContextPoint(BaseModel):
    """Existing ContextPoint information (mirrors ExistingEntity).

    Used for deduplication during incremental patch updates.
    """

    id: str
    search_text: str
    point_type: Optional[str] = None
    description: Optional[str] = None


class ProcedureState(BaseModel):
    """Current state of a Procedure (mirrors EpisodeState).

    Fetched from graph database before writing to enable:
    - Incremental patch: merge new content with existing
    - Version management: track version chain via supersedes
    - Drift prevention: preserve stable title/signature
    """

    procedure_id: str
    exists: bool = False  # Whether already exists in database

    # Basic attributes (from node properties)
    title: Optional[str] = None
    signature: Optional[str] = None
    summary: Optional[str] = None
    search_text: Optional[str] = None
    version: int = 1
    status: str = "active"  # "active" | "deprecated" | "superseded"
    confidence: str = "high"  # "high" | "low"

    # Structured content (display attributes)
    context_text: Optional[str] = None
    points_text: Optional[str] = None

    # Child nodes (mirrors EpisodeState.facets / entities)
    key_points: List[ExistingKeyPoint] = []
    context_points: List[ExistingContextPoint] = []

    # Version management (Procedural-specific, Episode doesn't need this)
    supersedes_ids: List[str] = []  # IDs of procedures this one supersedes
    superseded_by: Optional[str] = None  # ID of procedure that supersedes this one
    source_refs: List[str] = []
    updated_at: Optional[str] = None

    # Time fields (same pattern as EpisodeState)
    mentioned_time_start_ms: Optional[int] = None
    mentioned_time_end_ms: Optional[int] = None
    mentioned_time_confidence: Optional[float] = None
    mentioned_time_text: Optional[str] = None

    @property
    def has_mentioned_time(self) -> bool:
        """Whether has valid event time."""
        return self.mentioned_time_start_ms is not None and self.mentioned_time_end_ms is not None

    @property
    def is_active(self) -> bool:
        """Whether this procedure is currently active (not deprecated/superseded)."""
        return self.status == "active"


# ============================================================
# State Query Functions
# ============================================================


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_str(val: Any, default: Optional[str] = None) -> Optional[str]:
    """Safely convert a value to str or None."""
    if val is None:
        return default
    return str(val)


def _safe_list(val: Any) -> List[str]:
    """Safely convert a value to List[str]."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


async def fetch_procedure_state(
    graph_engine,
    procedure_id: str,
) -> ProcedureState:
    """
    Fetch current state of a Procedure from graph database.

    Mirrors fetch_episode_state() pattern:
    1. Get node by ID → extract basic attributes
    2. Get edges → extract child KeyPoints, ContextPoints, supersedes chain

    Args:
        graph_engine: Graph database engine (adapter-agnostic)
        procedure_id: Procedure node ID

    Returns:
        ProcedureState with all available fields populated
    """
    exists = False
    title = None
    signature = None
    summary = None
    search_text = None
    version = 1
    status = "active"
    confidence = "high"
    context_text = None
    points_text = None
    source_refs: List[str] = []
    updated_at = None
    mentioned_time_start_ms = None
    mentioned_time_end_ms = None
    mentioned_time_confidence = None
    mentioned_time_text = None

    # Step 1: Get Procedure node (same pattern as fetch_episode_state)
    try:
        node = await graph_engine.get_node(procedure_id)
        if node and isinstance(node, dict):
            exists = True
            title = node.get("name")
            signature = node.get("signature")
            summary = node.get("summary")
            search_text = node.get("search_text")
            version = _safe_int(node.get("version"), 1)
            status = _safe_str(node.get("status"), "active") or "active"
            confidence = _safe_str(node.get("confidence"), "high") or "high"
            context_text = node.get("context_text")
            points_text = node.get("points_text")
            source_refs = _safe_list(node.get("source_refs"))
            updated_at = _safe_str(node.get("updated_at"))
            # Time fields
            mentioned_time_start_ms = node.get("mentioned_time_start_ms")
            mentioned_time_end_ms = node.get("mentioned_time_end_ms")
            mentioned_time_confidence = node.get("mentioned_time_confidence")
            mentioned_time_text = node.get("mentioned_time_text")
    except Exception as e:
        logger.debug(f"No procedure node found for {procedure_id}: {e}")

    # Step 2: Get edges (same pattern as fetch_episode_state)
    key_points: List[ExistingKeyPoint] = []
    context_points: List[ExistingContextPoint] = []
    supersedes_ids: List[str] = []
    superseded_by: Optional[str] = None

    try:
        edges = await graph_engine.get_edges(procedure_id)
        for src, rel, dst in edges:
            if not isinstance(dst, dict):
                continue
            dst_type = dst.get("type", "")
            dst_id = str(dst.get("id", ""))
            src_id = str(src.get("id", "")) if isinstance(src, dict) else ""

            # KeyPoint edges (Procedure → has_key_point → ProcedureStepPoint)
            if rel == "has_key_point" and dst_type == "ProcedureStepPoint":
                key_points.append(
                    ExistingKeyPoint(
                        id=dst_id,
                        search_text=dst.get("search_text") or dst.get("name") or "",
                        step_number=_safe_int(dst.get("step_number") or dst.get("point_index")),
                        description=dst.get("description"),
                    )
                )

            # ContextPoint edges (Procedure → has_context_point → ProcedureContextPoint)
            elif rel == "has_context_point" and dst_type == "ProcedureContextPoint":
                context_points.append(
                    ExistingContextPoint(
                        id=dst_id,
                        search_text=dst.get("search_text") or dst.get("name") or "",
                        point_type=dst.get("point_type"),
                        description=dst.get("description"),
                    )
                )

            # Supersedes edges
            # get_edges is bidirectional, so we check direction:
            # - If this procedure is src and rel="supersedes": this supersedes dst
            # - If this procedure is dst and rel="supersedes": this is superseded by src
            elif rel == "supersedes":
                if src_id == procedure_id:
                    # This procedure supersedes the other
                    supersedes_ids.append(dst_id)
                elif dst_id == procedure_id:
                    # This procedure is superseded by the other
                    superseded_by = src_id

    except Exception as e:
        logger.debug(f"Failed to get edges for procedure {procedure_id}: {e}")

    # Deduplicate key_points by search_text (same pattern as entity dedup in episodic)
    seen_kp: set = set()
    uniq_kp: List[ExistingKeyPoint] = []
    for kp in key_points:
        key = (kp.search_text or "").strip().lower()
        if key and key not in seen_kp:
            seen_kp.add(key)
            uniq_kp.append(kp)

    # Deduplicate context_points by search_text
    seen_cp: set = set()
    uniq_cp: List[ExistingContextPoint] = []
    for cp in context_points:
        key = (cp.search_text or "").strip().lower()
        if key and key not in seen_cp:
            seen_cp.add(key)
            uniq_cp.append(cp)

    return ProcedureState(
        procedure_id=procedure_id,
        exists=exists,
        title=title,
        signature=signature,
        summary=summary,
        search_text=search_text,
        version=version,
        status=status,
        confidence=confidence,
        context_text=context_text,
        points_text=points_text,
        key_points=uniq_kp,
        context_points=uniq_cp,
        supersedes_ids=supersedes_ids,
        superseded_by=superseded_by,
        source_refs=source_refs,
        updated_at=updated_at,
        mentioned_time_start_ms=mentioned_time_start_ms,
        mentioned_time_end_ms=mentioned_time_end_ms,
        mentioned_time_confidence=mentioned_time_confidence,
        mentioned_time_text=mentioned_time_text,
    )


# ============================================================
# Version Chain Query
# ============================================================


async def get_version_chain(
    graph_engine,
    procedure_id: str,
    max_depth: int = 20,
) -> List[ProcedureState]:
    """
    Get the complete version chain for a procedure.

    Traverses supersedes edges to build the full version history.
    Starts from the given procedure and follows both directions.

    Args:
        graph_engine: Graph database engine
        procedure_id: Starting Procedure node ID
        max_depth: Maximum chain length to prevent infinite loops

    Returns:
        List of ProcedureState objects, sorted by version descending (newest first)
    """
    visited: set = set()
    chain: List[ProcedureState] = []

    # BFS from starting procedure
    queue = [procedure_id]

    while queue and len(visited) < max_depth:
        current_id = queue.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)

        state = await fetch_procedure_state(graph_engine, current_id)
        if not state.exists:
            continue

        chain.append(state)

        # Follow supersedes chain in both directions
        for sid in state.supersedes_ids:
            if sid not in visited:
                queue.append(sid)
        if state.superseded_by and state.superseded_by not in visited:
            queue.append(state.superseded_by)

    # Sort by version descending (newest first)
    chain.sort(key=lambda s: s.version, reverse=True)
    return chain


async def find_active_version_by_signature(
    graph_engine,
    signature: str,
) -> Optional[ProcedureState]:
    """
    Find the active version of a procedure by its signature.

    Uses graph query to find a Procedure node with matching signature
    and active status. This is useful for incremental update when
    we know the procedure key but not the exact node ID.

    Args:
        graph_engine: Graph database engine
        signature: Procedure signature to search for

    Returns:
        ProcedureState if found, None otherwise
    """
    try:
        nodes, _ = await graph_engine.query_by_attributes([{"type": ["Procedure"]}])

        for node_id, props in nodes:
            if not props:
                continue

            node_sig = props.get("signature", "")
            node_status = props.get("status", "active")

            if node_sig == signature and node_status == "active":
                return await fetch_procedure_state(graph_engine, str(node_id))

    except Exception as e:
        logger.warning(f"find_active_version_by_signature failed for '{signature}': {e}")

    return None
