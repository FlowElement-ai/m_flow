# m_flow/memory/episodic/episode_router.py
"""Episode Router (LLM-driven ingestion-time routing)

Goal: Route new documents to existing Episode (incremental update) or create new Episode.

Design:
- Use vector recall to gather candidate Episodes (summary, facets, entities)
- Fetch detailed info (summary, top facets) for each candidate
- LLM makes final decision: CREATE_NEW or MERGE_TO_EXISTING

Environment variables:
- MFLOW_EPISODIC_ENABLE_ROUTING: enable/disable routing (default True)
- MFLOW_EPISODIC_ROUTING_USE_LLM: use LLM for decision (default True)
- MFLOW_EPISODIC_ROUTING_SUMMARY_K: top-k for summary search
- MFLOW_EPISODIC_ROUTING_FACET_K: top-k for facet search
- MFLOW_EPISODIC_ROUTING_MAX_CANDIDATES: max candidates to pass to LLM
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from m_flow.shared.logging_utils import get_logger
from m_flow.shared.llm_concurrency import get_global_llm_semaphore
from m_flow.adapters.graph import get_graph_provider
from m_flow.adapters.vector import get_vector_provider
from m_flow.adapters.vector.exceptions import CollectionNotFoundError

# LanceError: raised by LanceDB when searching empty/corrupted tables
# Catches "Generic memory error: Invalid range 0..0 for object of size 0 bytes"
try:
    import lancedb
    _LANCE_ERROR = (lancedb.LanceError,) if hasattr(lancedb, "LanceError") else (Exception,)
except ImportError:
    _LANCE_ERROR = (Exception,)
from m_flow.llm.LLMGateway import LLMService
from m_flow.llm.prompts import read_query_prompt

from m_flow.memory.episodic.models import (
    EpisodeCandidate as EpisodeCandidateModel,
    RouteDecision,
)
from m_flow.memory.episodic.llm_call_tracker import get_llm_tracker


logger = get_logger("episodic.router")


# Import utility functions from unified module
from m_flow.memory.episodic.env_utils import as_bool_env as _as_bool_env
from m_flow.memory.episodic.env_utils import as_int_env as _as_int_env
from m_flow.memory.episodic.normalization import truncate as _truncate


# =============================================================================
# Constants for routing heuristics
# =============================================================================

# Initial rank value indicating "no evidence" (effectively infinity)
_NO_EVIDENCE_RANK: int = 10**9

# Thresholds for heuristic merge decision (when LLM is disabled)
# Merge if summary_rank <= threshold AND facet_rank <= threshold
_HEURISTIC_SUMMARY_RANK_THRESHOLD: int = 3
_HEURISTIC_FACET_RANK_THRESHOLD: int = 5


@dataclass
class CandidateInfo:
    """Internal candidate info for routing decision."""

    episode_id: str
    sources: Set[str] = field(default_factory=set)

    # Ranks (lower is better, _NO_EVIDENCE_RANK means no evidence found)
    summary_rank: int = _NO_EVIDENCE_RANK
    facet_rank: int = _NO_EVIDENCE_RANK
    anchor_rank: int = _NO_EVIDENCE_RANK

    # Note: entity_hits removed for parallel optimization (v1.3)
    # Entity extraction now runs in parallel with routing

    # Detailed info (fetched from graph)
    episode_name: str = ""
    episode_summary: str = ""
    top_facets: List[str] = field(default_factory=list)
    facet_count: int = 0  # Total number of facets in this Episode

    def has_any_evidence(self) -> bool:
        """Check if this candidate has any matching evidence from vector search."""
        return (
            self.summary_rank < _NO_EVIDENCE_RANK
            or self.facet_rank < _NO_EVIDENCE_RANK
            or self.anchor_rank < _NO_EVIDENCE_RANK
        )

    def match_signals_str(self) -> str:
        """Generate a human-readable string describing the matching signals."""
        signals = []
        if self.summary_rank < _NO_EVIDENCE_RANK:
            signals.append(f"summary(rank={self.summary_rank})")
        if self.facet_rank < _NO_EVIDENCE_RANK:
            signals.append(f"facet(rank={self.facet_rank})")
        if self.anchor_rank < _NO_EVIDENCE_RANK:
            signals.append(f"anchor(rank={self.anchor_rank})")
        return "; ".join(signals) if signals else "none"


def _pick_routing_text(doc_title: str, chunk_summaries: List[str]) -> str:
    """Build routing query text: title + key chunks (entities removed for parallel optimization)."""
    parts: List[str] = []
    if doc_title:
        parts.append(f"TITLE: {doc_title}")

    cs = [s.strip() for s in (chunk_summaries or []) if (s or "").strip()]
    focus = cs[:4] + (cs[-2:] if len(cs) > 6 else [])
    if focus:
        parts.append("CHUNKS:")
        parts.extend([f"- {x[:420]}" for x in focus])

    # Note: ENTITIES section removed - entity names are already embedded in chunk_summaries
    # This enables parallel execution of entity extraction and routing

    txt = "\n".join(parts).strip()
    return txt[:2400]


async def _search_collection(
    vector_engine,
    collection: str,
    query_vector,
    limit: int,
    where_filter: Optional[str] = None,
):
    """Safe search with collection not found handling.

    Args:
        vector_engine: Vector database engine
        collection: Collection name to search
        query_vector: Query embedding vector
        limit: Maximum results to return
        where_filter: Optional SQL-style filter (e.g., "payload.dataset_id = 'xxx'")
    """
    try:
        return await vector_engine.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=limit,
            where_filter=where_filter,
        )
    except CollectionNotFoundError:
        return []
    except _LANCE_ERROR as e:
        # LanceDB raises LanceError(IO) when searching empty or corrupted tables:
        # "Generic memory error: Invalid range 0..0 for object of size 0 bytes"
        # This is safe to degrade from — return empty results and route to new Episode
        logger.warning(f"[router] search {collection} failed (LanceDB error): {e}")
        return []
    except Exception as e:
        logger.warning(f"[router] search {collection} failed: {e}")
        return []


async def _neighbor_episode_ids(graph_engine, node_id: str, rel_name: str) -> List[str]:
    """
    Get Episode IDs connected to a node via a specific relationship.
    """
    try:
        edges = await graph_engine.get_edges(node_id)
    except Exception as e:
        logger.debug(f"[episode_router] get_edges failed for {node_id}: {e}")
        return []

    out: List[str] = []
    for src, rel, dst in edges:
        if rel != rel_name:
            continue
        # Check both directions
        if isinstance(src, dict) and (src.get("type") or "") == "Episode":
            ep_id = str(src.get("id") or "")
            if ep_id:
                out.append(ep_id)
        if isinstance(dst, dict) and (dst.get("type") or "") == "Episode":
            ep_id = str(dst.get("id") or "")
            if ep_id:
                out.append(ep_id)
    return list(set(out))


async def _fetch_episode_details(graph_engine, episode_id: str) -> Dict[str, Any]:
    """Fetch Episode name, summary, facet count, top facets, and nodeset_id from graph."""
    result = {
        "name": "",
        "summary": "",
        "facets": [],
        "facet_count": 0,
        "nodeset_id": None,  # For dataset filtering
    }

    try:
        node = await graph_engine.get_node(episode_id)
        if node and isinstance(node, dict):
            result["name"] = str(node.get("name") or "")
            props = node.get("properties") or {}
            if isinstance(props, dict):
                result["summary"] = str(props.get("summary") or "")
    except Exception as e:
        logger.debug(f"[router] Failed to fetch episode {episode_id}: {e}")
        return result

    # Fetch facets and nodeset via edges
    all_facets = []
    try:
        edges = await graph_engine.get_edges(episode_id)
        for _src, rel, dst in edges:  # _src unused but required for unpacking
            if rel == "has_facet" and isinstance(dst, dict):
                facet_props = dst.get("properties") or {}
                search_text = facet_props.get("search_text") or dst.get("name") or ""
                if search_text:
                    all_facets.append(str(search_text))
            # Extract nodeset_id from memory_spaces edge
            elif rel == "memory_spaces" and isinstance(dst, dict):
                nodeset_id = dst.get("id")
                if nodeset_id:
                    result["nodeset_id"] = str(nodeset_id)
    except Exception as e:
        logger.debug(f"[router] Failed to fetch facets for {episode_id}: {e}")

    # Return total count and truncated facets (20 for better context)
    result["facet_count"] = len(all_facets)
    result["facets"] = all_facets[:20]
    return result


async def _llm_route_decision(
    *,
    doc_title: str,
    chunk_summaries: List[str],
    candidates: List[CandidateInfo],
    prompt_file_name: str = "episodic_route_decision.txt",
    is_single_event: bool = True,  # V2 mode (single event) vs V1 mode (multi-chunk document)
) -> RouteDecision:
    """
    Use LLM to decide: CREATE_NEW or MERGE_TO_EXISTING.

    Input design differs by mode:
    - V2 mode (is_single_event=True): Full event content without truncation
    - V1 mode (is_single_event=False): Multiple chunks with truncation for prompt size control

    Both modes:
    - CANDIDATE_EPISODES: Only Name and Facet titles (no Summary) for focus
    - Entities are embedded in content (no separate list needed)
    """
    # Build candidate list for prompt (simplified: name + facet count + facets)
    candidate_models: List[EpisodeCandidateModel] = [
        EpisodeCandidateModel(
            episode_id=c.episode_id,
            episode_name=c.episode_name or "(unknown)",
            episode_summary="",  # Removed - not needed for routing decision
            top_facets=c.top_facets[:20],  # Increased to 20 for better context
            match_signals="",  # Removed - not needed for routing decision
            facet_count=c.facet_count,  # Total facet count for size awareness
        )
        for c in candidates
    ]

    # Build prompt inputs - mode-dependent truncation
    if is_single_event:
        # V2 mode: Single event, show FULL content without truncation
        # chunk_summaries has exactly 1 element containing the complete event text
        chunks_str = "\n".join([f"- {s}" for s in chunk_summaries])
    else:
        # V1 mode: Multi-chunk document, apply truncation for prompt size control
        # Limit to 8 chunks, each truncated to 300 chars
        chunks_str = "\n".join([f"- {_truncate(s, 300)}" for s in chunk_summaries[:8]])

    # Candidate display: Name + Facet Count + Facet Titles (no Summary, no Match Signals)
    candidates_str = ""
    for i, cm in enumerate(candidate_models):
        facets_str = "; ".join(cm.top_facets) if cm.top_facets else "(none)"
        candidates_str += f"\n[Candidate {i + 1}]\n"
        candidates_str += f"  ID: {cm.episode_id}\n"
        candidates_str += f"  Name: {cm.episode_name}\n"
        candidates_str += f"  Facet Count: {cm.facet_count}\n"
        candidates_str += f"  Facet Titles: {facets_str}\n"

    system_prompt = read_query_prompt(prompt_file_name)
    user_prompt = f"""NEW_DOCUMENT_TITLE: {doc_title}

NEW_DOCUMENT_CONTENT:
{chunks_str}

CANDIDATE_EPISODES:
{candidates_str if candidates_str.strip() else "(No candidates found - no existing episodes match)"}

Please decide: CREATE_NEW or MERGE_TO_EXISTING?
If MERGE_TO_EXISTING, specify which candidate's episode_id to merge into.
"""

    logger.debug(f"[router][LLM] system_prompt length={len(system_prompt)}")
    logger.debug(f"[router][LLM] user_prompt length={len(user_prompt)}")
    logger.debug(f"[router][LLM] user_prompt:\n{user_prompt[:1500]}...")

    try:
        # Use global semaphore for concurrency control (shared with entity extraction)
        _llm_semaphore = get_global_llm_semaphore()
        async with _llm_semaphore:
            # Use tracker to track LLM calls
            tracker = get_llm_tracker()
            async with tracker.track("episode_routing", user_prompt, RouteDecision):
                decision = await LLMService.extract_structured(
                    text_input=user_prompt,
                    system_prompt=system_prompt,
                    response_model=RouteDecision,
                )
                tracker.record_attempt(1)
        logger.info(
            f"[router][LLM] decision={decision.decision}, "
            f"target={decision.target_episode_id}, reasoning={_truncate(decision.reasoning, 100)}"
        )
        return decision
    except Exception as e:
        logger.warning(f"[router][LLM] Failed: {e}, fallback to CREATE_NEW")
        return RouteDecision(
            decision="CREATE_NEW",
            target_episode_id=None,
            reasoning=f"LLM call failed: {str(e)[:100]}",
        )


async def route_episode_id_for_doc(
    *,
    doc_title: str,
    chunk_summaries: List[str],
    default_episode_id: str,
    # engines (optional injection)
    graph_engine=None,
    vector_engine=None,
    # routing config
    episode_summary_k: Optional[int] = None,
    facet_k: Optional[int] = None,
    max_candidates_for_llm: Optional[int] = None,
    use_llm: Optional[bool] = None,
    # V2 mode flag: single event (no truncation) vs multi-chunk document (with truncation)
    is_single_event: bool = True,
    # Dataset isolation: only route to Episodes in the same nodeset
    target_nodeset_id: Optional[str] = None,
    # Dataset isolation: only route to Episodes with matching dataset_id
    target_dataset_id: Optional[str] = None,
    # DEPRECATED parameters (ignored, kept for backward compatibility)
    top_entity_ids: Optional[List[str]] = None,
    top_entity_names: Optional[List[str]] = None,
    entity_k: Optional[int] = None,  # No longer used
) -> Tuple[str, Dict[str, Any]]:
    """
    Route a document to an existing Episode or create a new one.

    This function determines whether a new document should be merged into
    an existing Episode or create a new one, using vector similarity search
    and optionally LLM-based decision making.

    Args:
        doc_title: Title or topic of the document being routed
        chunk_summaries: List of summary strings from document chunks
        default_episode_id: Fallback episode ID if routing fails or is disabled
        graph_engine: Optional graph database engine (uses global if None)
        vector_engine: Optional vector database engine (uses global if None)
        episode_summary_k: Number of episode summaries to retrieve (default from env)
        facet_k: Number of facets to retrieve (default from env)
        max_candidates_for_llm: Max candidates to send to LLM (default from env)
        use_llm: Whether to use LLM for final decision (default from env)
        is_single_event: V2 mode flag - True for single events, False for multi-chunk docs
        target_nodeset_id: Only route to Episodes belonging to this nodeset (dataset isolation)
        target_dataset_id: Only route to Episodes with matching dataset_id (vector filter isolation)
        top_entity_ids: DEPRECATED - ignored, kept for backward compatibility
        top_entity_names: DEPRECATED - ignored, kept for backward compatibility
        entity_k: DEPRECATED - ignored, kept for backward compatibility

    Returns:
        Tuple of (chosen_episode_id, debug_dict) where debug_dict contains
        routing decision details including candidates and reasoning.

    Strategy:
        0) If routing disabled or default_episode_id exists -> return it
        1) Vector recall: Episode_summary, Facet_search_text, Facet_anchor_text
        2) Fetch detailed info for top candidates
        3) Filter candidates to same nodeset (dataset isolation)
        4) LLM decision: CREATE_NEW or MERGE_TO_EXISTING

    Note:
        Entity-based routing removed for parallel optimization (v1.3).
        Entity extraction now runs in parallel with routing.
    """
    # Deprecation warning for removed parameters
    if top_entity_ids or top_entity_names:
        logger.debug("[route_episode_id_for_doc] top_entity_ids/top_entity_names are deprecated and ignored")

    # Enable switch
    if not _as_bool_env("MFLOW_EPISODIC_ENABLE_ROUTING", True):
        return default_episode_id, {"reason": "routing_disabled"}

    if graph_engine is None:
        graph_engine = await get_graph_provider()
    if vector_engine is None:
        vector_engine = get_vector_provider()

    # Config defaults
    episode_summary_k = episode_summary_k or _as_int_env("MFLOW_EPISODIC_ROUTING_SUMMARY_K", 25)
    facet_k = facet_k or _as_int_env("MFLOW_EPISODIC_ROUTING_FACET_K", 35)
    max_candidates_for_llm = max_candidates_for_llm or _as_int_env("MFLOW_EPISODIC_ROUTING_MAX_CANDIDATES", 5)
    if use_llm is None:
        use_llm = _as_bool_env("MFLOW_EPISODIC_ROUTING_USE_LLM", True)

    # 0) If default_episode_id already exists -> return it (preserve doc stability)
    try:
        node = await graph_engine.get_node(default_episode_id)
        if node and isinstance(node, dict) and (node.get("type") or "") == "Episode":
            return default_episode_id, {"reason": "default_episode_exists"}
    except Exception as e:
        logger.debug(f"[episode_router] Entity retrieval failed: {e}")

    routing_text = _pick_routing_text(doc_title, chunk_summaries)

    # Get embedding
    try:
        query_vector = (await vector_engine.embedding_engine.embed_text([routing_text]))[0]
    except Exception as e:
        logger.warning(f"[router] Failed to embed routing text: {e}")
        return default_episode_id, {"reason": "embedding_failed", "error": str(e)}

    # Build where_filter for dataset isolation
    where_filter = None
    if target_dataset_id:
        where_filter = f"payload.dataset_id = '{target_dataset_id}'"
        logger.debug(f"[router] Using dataset filter: {where_filter}")

    # Skip vector recall if no collections exist yet (first ingestion)
    required_collections = ["Episode_summary", "Facet_search_text", "Facet_anchor_text"]
    existing_collections = [c for c in required_collections if await vector_engine.has_collection(c)]
    if not existing_collections:
        return default_episode_id, {"reason": "no_collections_exist"}

    # 1) Concurrent vector recall (with dataset isolation filter, only existing collections)
    async def _safe_search(coll):
        if coll not in existing_collections:
            return []
        return await _search_collection(
            vector_engine, coll, query_vector, episode_summary_k if coll == "Episode_summary" else facet_k, where_filter
        )

    episode_res, facet_res, anchor_res = await asyncio.gather(
        _safe_search("Episode_summary"),
        _safe_search("Facet_search_text"),
        _safe_search("Facet_anchor_text"),
    )

    candidates: Dict[str, CandidateInfo] = {}

    def get(ep_id: str) -> CandidateInfo:
        if ep_id not in candidates:
            candidates[ep_id] = CandidateInfo(episode_id=ep_id)
        return candidates[ep_id]

    # 2) Summary candidates
    for rank, r in enumerate(episode_res or []):
        ep_id = str(getattr(r, "id", "") or "")
        if not ep_id:
            continue
        c = get(ep_id)
        c.sources.add("summary")
        c.summary_rank = min(c.summary_rank, rank)

    # 3) Facet -> Episode
    facet_ids = [str(getattr(r, "id", "") or "") for r in (facet_res or [])]
    facet_ids = [x for x in facet_ids if x][: min(12, facet_k)]
    facet_maps = await asyncio.gather(*[_neighbor_episode_ids(graph_engine, fid, "has_facet") for fid in facet_ids])
    for rank, eps in enumerate(facet_maps):
        for ep_id in eps:
            c = get(ep_id)
            c.sources.add("facet_search")
            c.facet_rank = min(c.facet_rank, rank)

    # 4) Anchor facet -> Episode
    anchor_ids = [str(getattr(r, "id", "") or "") for r in (anchor_res or [])]
    anchor_ids = [x for x in anchor_ids if x][: min(12, facet_k)]
    anchor_maps = await asyncio.gather(*[_neighbor_episode_ids(graph_engine, fid, "has_facet") for fid in anchor_ids])
    for rank, eps in enumerate(anchor_maps):
        for ep_id in eps:
            c = get(ep_id)
            c.sources.add("facet_anchor")
            c.anchor_rank = min(c.anchor_rank, rank)

    # Note: Entity overlap (step 5) removed for parallel optimization
    # Entity extraction now runs in parallel with routing, so entity IDs are not available here

    # Filter to candidates with any evidence
    valid_candidates = [c for c in candidates.values() if c.has_any_evidence()]

    if not valid_candidates:
        return default_episode_id, {
            "reason": "no_candidates_found",
            "routing_text_preview": routing_text[:400],
        }

    # Sort by combined signal strength (lower rank is better)
    valid_candidates.sort(key=lambda x: min(x.summary_rank, x.facet_rank, x.anchor_rank))

    # Take top candidates for LLM
    top_candidates = valid_candidates[:max_candidates_for_llm]

    # 6) Fetch detailed info for top candidates
    details_list = await asyncio.gather(*[_fetch_episode_details(graph_engine, c.episode_id) for c in top_candidates])
    for c, details in zip(top_candidates, details_list, strict=True):
        c.episode_name = details.get("name") or ""
        c.episode_summary = details.get("summary") or ""
        c.top_facets = details.get("facets") or []
        c.facet_count = details.get("facet_count", 0)

    # NOTE: Dataset isolation via nodeset_id is NOT effective when all datasets
    # share the same nodeset ("Episodic"). This is a known limitation.
    # To properly isolate datasets, either:
    # 1. Enable BACKEND_ACCESS_CONTROL=true (separate database per dataset), or
    # 2. Pass unique episodic_nodeset_name per dataset to write_episodic_memories
    # For now, this filter is kept but may not provide isolation in single-DB mode.
    if target_nodeset_id:
        filtered_candidates = []
        for c, details in zip(top_candidates, details_list, strict=True):
            candidate_nodeset_id = details.get("nodeset_id")
            if candidate_nodeset_id == target_nodeset_id:
                filtered_candidates.append(c)

        if len(filtered_candidates) < len(top_candidates):
            logger.info(
                f"[router] Filtered {len(top_candidates) - len(filtered_candidates)} candidates "
                f"by nodeset, {len(filtered_candidates)} remaining"
            )
        top_candidates = filtered_candidates

        if not top_candidates:
            return default_episode_id, {
                "reason": "no_candidates_in_nodeset",
                "routing_text_preview": routing_text[:400],
            }

    # Build debug preview
    debug_candidates = [
        {
            "id": c.episode_id[:30],
            "name": _truncate(c.episode_name, 40),
            "signals": c.match_signals_str(),
        }
        for c in top_candidates
    ]

    # 6) LLM decision
    if use_llm:
        decision = await _llm_route_decision(
            doc_title=doc_title,
            chunk_summaries=chunk_summaries,
            candidates=top_candidates,
            is_single_event=is_single_event,
        )

        if decision.decision == "MERGE_TO_EXISTING" and decision.target_episode_id:
            # Validate target exists in candidates
            valid_ids = {c.episode_id for c in top_candidates}
            if decision.target_episode_id in valid_ids:
                return decision.target_episode_id, {
                    "reason": "llm_merge",
                    "llm_reasoning": decision.reasoning,
                    "target": decision.target_episode_id,
                    "candidates": debug_candidates,
                    "routing_text_preview": routing_text[:400],
                }
            else:
                logger.warning(f"[router] LLM returned invalid target_episode_id: {decision.target_episode_id}")

        # CREATE_NEW or invalid merge target
        return default_episode_id, {
            "reason": "llm_create_new",
            "llm_reasoning": decision.reasoning,
            "candidates": debug_candidates,
            "routing_text_preview": routing_text[:400],
        }

    else:
        # Fallback: simple heuristic (use best candidate if very strong signal)
        best = top_candidates[0]
        # Note: entity_hits condition removed for parallel optimization
        if (
            best.summary_rank <= _HEURISTIC_SUMMARY_RANK_THRESHOLD
            and best.facet_rank <= _HEURISTIC_FACET_RANK_THRESHOLD
        ):
            return best.episode_id, {
                "reason": "heuristic_merge",
                "target": best.episode_id,
                "candidates": debug_candidates,
                "routing_text_preview": routing_text[:400],
            }
        else:
            return default_episode_id, {
                "reason": "heuristic_create_new",
                "candidates": debug_candidates,
                "routing_text_preview": routing_text[:400],
            }
