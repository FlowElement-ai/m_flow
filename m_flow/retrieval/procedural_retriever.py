"""
Procedural Retriever.

Retriever for procedural memory retrieval, aligned with UnifiedTripletSearch interface.

- get_context(): Returns Procedure-Context-Steps triplets
- get_completion(): Converts triplets to text, feeds to LLM to answer method-type questions

Key design:
- Uses procedural_bundle_search for retrieval
- Injects text attributes to be compatible with resolve_edges_to_text
- Forces dual output: context + steps always returned together
"""

import asyncio
import os
from typing import Any, Optional, Type, List, Set

from m_flow.adapters.graph import get_graph_provider
from m_flow.knowledge.graph_ops.m_flow_graph.MemoryGraphElements import Edge
from m_flow.knowledge.graph_ops.utils.resolve_edges_to_text import resolve_edges_to_text
from m_flow.retrieval.base_graph_retriever import BaseGraphRetriever
from m_flow.retrieval.utils.procedural_bundle_search import procedural_bundle_search
from m_flow.retrieval.utils.completion import generate_completion, summarize_text
from m_flow.retrieval.utils.session_cache import (
    save_conversation_history,
    get_conversation_history,
)
from m_flow.shared.logging_utils import get_logger
from m_flow.context_global_variables import session_user
from m_flow.adapters.cache.config import CacheConfig


logger = get_logger("ProceduralRetriever")


def _build_procedural_structured_json(triplets: List[Edge]) -> str:
    """Standalone helper: inject text + build structured JSON from procedural edges."""
    import json as _json

    _inject_text_for_procedural_nodes(triplets)

    procedures: dict = {}
    seen_ids: set = set()

    for edge in triplets:
        for nd in (edge.node1, edge.node2):
            nid = str(nd.id)
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            attrs = nd.attributes
            ntype = (attrs.get("type") or "").strip()

            if ntype == "Procedure":
                props = attrs.get("properties") or {}
                if isinstance(props, str):
                    try:
                        props = _json.loads(props)
                    except Exception:
                        props = {}
                procedures[nid] = {
                    "id": nid,
                    "title": attrs.get("name") or props.get("name", "Procedure"),
                    "summary": props.get("summary") or attrs.get("summary", ""),
                    "search_text": props.get("search_text") or attrs.get("search_text", ""),
                    "context_text": props.get("context_text") or attrs.get("context_text", ""),
                    "points_text": props.get("points_text") or attrs.get("points_text", ""),
                    "version": props.get("version", 1),
                    "status": props.get("status", "active"),
                    "confidence": props.get("confidence", "high"),
                    "steps": [],
                    "context_points": [],
                }

    for edge in triplets:
        n1_type = (edge.node1.attributes.get("type") or "").strip()
        n2_type = (edge.node2.attributes.get("type") or "").strip()
        n1_id, n2_id = str(edge.node1.id), str(edge.node2.id)

        proc_id = None
        child_node = None

        if n1_type == "Procedure" and n1_id in procedures:
            proc_id = n1_id
            child_node = edge.node2
        elif n2_type == "Procedure" and n2_id in procedures:
            proc_id = n2_id
            child_node = edge.node1

        if proc_id and child_node:
            child_type = (child_node.attributes.get("type") or "").strip()
            child_text = (
                child_node.attributes.get("text")
                or child_node.attributes.get("search_text")
                or child_node.attributes.get("name")
                or ""
            )
            if child_type == "ProcedureStepPoint" and child_text:
                procedures[proc_id]["steps"].append(child_text)
            elif child_type == "ProcedureContextPoint" and child_text:
                props = child_node.attributes.get("properties") or {}
                if isinstance(props, str):
                    try:
                        props = _json.loads(props)
                    except Exception:
                        props = {}
                ptype = props.get("point_type") or child_node.attributes.get("point_type", "context")
                procedures[proc_id]["context_points"].append({"type": ptype, "text": child_text})

    if not procedures:
        first_proc = {
            "id": "unknown",
            "title": "Procedure",
            "summary": "",
            "search_text": "",
            "context_text": "",
            "points_text": "",
            "version": 1,
            "status": "active",
            "confidence": "high",
            "steps": [],
            "context_points": [],
        }
        for edge in triplets:
            for nd in (edge.node1, edge.node2):
                attrs = nd.attributes
                ntype = (attrs.get("type") or "").strip()
                text = attrs.get("text") or attrs.get("search_text") or attrs.get("name") or ""
                if ntype == "Procedure":
                    first_proc["title"] = attrs.get("name") or text or "Procedure"
                    first_proc["summary"] = attrs.get("summary") or text
                elif ntype == "ProcedureStepPoint" and text:
                    first_proc["steps"].append(text)
                elif ntype == "ProcedureContextPoint" and text:
                    first_proc["context_points"].append({"type": "context", "text": text})
                elif text:
                    first_proc["steps"].append(text)
        if first_proc["steps"] or first_proc["context_points"] or first_proc["summary"]:
            procedures["fallback"] = first_proc

    _noise = {"auto_coverage", "none", "None", ""}

    for proc in procedures.values():
        proc["steps"] = [s for s in dict.fromkeys(proc["steps"]) if s.strip() not in _noise]
        seen_ctx: set = set()
        deduped = []
        for cp in proc["context_points"]:
            if cp["text"].strip() in _noise:
                continue
            key = cp["text"][:50]
            if key not in seen_ctx:
                seen_ctx.add(key)
                deduped.append(cp)
        proc["context_points"] = deduped

    final_procs = [p for p in procedures.values() if p["steps"] or p["context_points"] or p["summary"]]
    result = {"__procedural_structured__": True, "procedures": final_procs}
    return _json.dumps(result, ensure_ascii=False)


def _inject_text_for_procedural_nodes(triplets: List[Edge]) -> None:
    """
    Enable resolve_edges_to_text to correctly display procedural anchor/detail.

    Injects text attribute for rendering:
    - Procedure.summary -> node.text
    - ContextPoint/KeyPoint.search_text -> node.text

    This is a non-persistent injection, only affects current rendering.
    """
    for edge in triplets:
        for node in (edge.node1, edge.node2):
            node_type = node.attributes.get("type")

            # Skip if text already exists
            if node.attributes.get("text"):
                continue

            if node_type == "Procedure" and node.attributes.get("summary"):
                node.attributes["text"] = node.attributes.get("summary")

            elif node_type in ("ProcedureContextPoint", "ProcedureStepPoint"):
                node.attributes["text"] = (
                    node.attributes.get("description")
                    or node.attributes.get("search_text")
                    or node.attributes.get("name")
                )

            # Legacy: Pack nodes (old data)
            elif node_type in ("ProcedureContextPack", "ProcedureStepsPack"):
                node.attributes["text"] = (
                    node.attributes.get("anchor_text")
                    or node.attributes.get("search_text")
                    or node.attributes.get("name")
                )


class ProceduralRetriever(BaseGraphRetriever):
    """
    Procedural Memory Retriever

    Used for method-type question retrieval, returns Procedure-Context-Steps triplets.

    Aligned with UnifiedTripletSearch interface:
    - get_context(): Returns List[Edge]
    - get_completion(): Returns LLM-generated answer

    Features:
    - Hitting any node of Procedure will return both context and steps
    - Default top_k is small (2-3), suitable for precise method-type questions
    """

    def __init__(
        self,
        user_prompt_path: str = "graph_retrieval_context.txt",
        system_prompt_path: str = "answer_procedure_question.txt",
        system_prompt: Optional[str] = None,
        top_k: Optional[int] = 3,
        procedural_nodeset_name: str = "Procedural",
        wide_search_top_k: Optional[int] = 50,
        edge_type_whitelist: Optional[Set[str]] = None,
        enable_time_bonus: bool = True,
    ):
        """
        Initialize ProceduralRetriever.

        Args:
            user_prompt_path: User prompt file path
            system_prompt_path: System prompt file path
            system_prompt: Custom system prompt
            top_k: Number of top procedures to return
            procedural_nodeset_name: Procedural MemorySpace name
            wide_search_top_k: Initial recall count per collection
            edge_type_whitelist: Set of allowed edge types
            enable_time_bonus: Enable time-based relevance bonus
        """
        self.user_prompt_path = user_prompt_path
        self.system_prompt_path = system_prompt_path
        self.system_prompt = system_prompt
        self.top_k = top_k if top_k is not None else 3
        self.procedural_nodeset_name = procedural_nodeset_name
        self.wide_search_top_k = wide_search_top_k
        self.edge_type_whitelist = edge_type_whitelist
        self.enable_time_bonus = enable_time_bonus

    async def get_triplets(self, query: str) -> List[Edge]:
        """
        Retrieve triplets using procedural_bundle_search.

        Returns:
            List[Edge]: Procedural triplets (Procedure-Context/Steps + Points)
        """
        return await procedural_bundle_search(
            query=query,
            top_k=self.top_k,
            procedural_nodeset_name=self.procedural_nodeset_name,
            wide_search_top_k=self.wide_search_top_k,
            strict_nodeset_filtering=True,
            edge_miss_cost=float(os.getenv("MFLOW_PROCEDURAL_EDGE_MISS_COST", "0.9")),
            hop_cost=float(os.getenv("MFLOW_PROCEDURAL_HOP_COST", "0.05")),
            enable_time_bonus=self.enable_time_bonus,
        )

    async def get_context(self, query: str) -> List[Edge]:
        """
        Retrieve procedural triplets as context.

        Args:
            query: Search query

        Returns:
            List[Edge]: Procedural triplets
        """
        graph_engine = await get_graph_provider()
        if await graph_engine.is_empty():
            logger.warning("M-Flow retrieval skipped: knowledge graph has no data")
            return []

        return await self.get_triplets(query)

    async def convert_retrieved_objects_to_context(self, triplets: List[Edge]) -> str:
        """
        Convert triplets to structured JSON context.

        Returns a JSON string with procedures, steps, and context points
        for clean frontend rendering. Falls back to resolve_edges_to_text
        for LLM completion path (which re-calls this with its own formatting).
        """
        _inject_text_for_procedural_nodes(triplets)

        try:
            return self._build_structured_context(triplets)
        except Exception:
            return await resolve_edges_to_text(triplets)

    def _build_structured_context(self, triplets: List[Edge]) -> str:
        """Build structured JSON from procedural triplets."""
        import json as _json

        procedures: dict = {}
        seen_ids: set = set()

        for edge in triplets:
            for nd in (edge.node1, edge.node2):
                nid = str(nd.id)
                if nid in seen_ids:
                    continue
                seen_ids.add(nid)

                attrs = nd.attributes
                ntype = attrs.get("type", "")

                if ntype == "Procedure":
                    props = attrs.get("properties") or {}
                    if isinstance(props, str):
                        try:
                            props = _json.loads(props)
                        except Exception:
                            props = {}
                    procedures[nid] = {
                        "id": nid,
                        "title": attrs.get("name") or props.get("name", "Procedure"),
                        "summary": props.get("summary") or attrs.get("summary", ""),
                        "search_text": props.get("search_text", ""),
                        "context_text": props.get("context_text", ""),
                        "points_text": props.get("points_text", ""),
                        "version": props.get("version", 1),
                        "status": props.get("status", "active"),
                        "confidence": props.get("confidence", "high"),
                        "steps": [],
                        "context_points": [],
                    }

        for edge in triplets:
            for nd in (edge.node1, edge.node2):
                attrs = nd.attributes
                ntype = attrs.get("type", "")
                nid = str(nd.id)
                text = attrs.get("text") or attrs.get("search_text") or attrs.get("name", "")

                if ntype == "ProcedureStepPoint" and text:
                    for _pid, proc in procedures.items():
                        proc["steps"].append(text)
                        break
                elif ntype == "ProcedureContextPoint" and text:
                    props = attrs.get("properties") or {}
                    if isinstance(props, str):
                        try:
                            props = _json.loads(props)
                        except Exception:
                            props = {}
                    ptype = props.get("point_type", "context")
                    for _pid, proc in procedures.items():
                        proc["context_points"].append({"type": ptype, "text": text})
                        break

        for proc in procedures.values():
            proc["steps"] = list(dict.fromkeys(proc["steps"]))
            seen_ctx = set()
            deduped = []
            for cp in proc["context_points"]:
                key = cp["text"][:50]
                if key not in seen_ctx:
                    seen_ctx.add(key)
                    deduped.append(cp)
            proc["context_points"] = deduped

        result = {"__procedural_structured__": True, "procedures": list(procedures.values())}
        return _json.dumps(result, ensure_ascii=False)

    async def get_completion(
        self,
        query: str,
        context: Optional[List[Edge]] = None,
        session_id: Optional[str] = None,
        response_model: Type = str,
    ) -> List[Any]:
        """
        Generate completion based on procedural triplets.

        Args:
            query: User query
            context: Optional pre-retrieved triplets
            session_id: Session ID (for caching)
            response_model: Response model type

        Returns:
            List[Any]: LLM-generated answer
        """
        triplets = context if context is not None else await self.get_context(query)
        _inject_text_for_procedural_nodes(triplets)
        context_text = await resolve_edges_to_text(triplets)

        cache_config = CacheConfig()
        user = session_user.get()
        user_id = getattr(user, "id", None)
        session_save = user_id and cache_config.caching

        if session_save:
            conversation_history = await get_conversation_history(session_id=session_id)
            context_summary, completion = await asyncio.gather(
                summarize_text(context_text),
                generate_completion(
                    query=query,
                    context=context_text,
                    user_prompt_path=self.user_prompt_path,
                    system_prompt_path=self.system_prompt_path,
                    system_prompt=self.system_prompt,
                    conversation_history=conversation_history,
                    response_model=response_model,
                ),
            )
            await save_conversation_history(
                query=query,
                context_summary=context_summary,
                answer=completion,
                session_id=session_id,
            )
        else:
            completion = await generate_completion(
                query=query,
                context=context_text,
                user_prompt_path=self.user_prompt_path,
                system_prompt_path=self.system_prompt_path,
                system_prompt=self.system_prompt,
                response_model=response_model,
            )

        return [completion]


def has_procedural_intent(query: str) -> bool:
    """
    Determine if query has clear procedural intent.

    Used for soft routing: increase procedural top_k when query has strong intent.
    Supports both Chinese and English procedural signal detection.

    Examples:
    - "how to backup" -> True
    - "what's the weather" -> False (asks about state, not method)
    """
    import re

    # Chinese exclusion patterns: state-asking phrases, not procedural
    zh_exclude_patterns = [
        r"怎么样",  # "how is it" - asks about state
        r"怎么了",  # "what happened" - asks about event
        r"如何了",  # "how did it go" - asks about state
    ]

    # Chinese strong signal words for override check
    zh_strong_signals = ["步骤", "流程", "配置", "部署", "安装", "修复"]

    for pattern in zh_exclude_patterns:
        if re.search(pattern, query):
            if not any(s in query for s in zh_strong_signals):
                return False

    # Chinese strong procedural patterns (standalone indicators)
    zh_strong_patterns = [
        r"步骤",  # steps
        r"流程",  # process/workflow
        r"排查",  # troubleshoot
        r"配置",  # configure
        r"回滚",  # rollback
        r"修复",  # fix/repair
        r"部署",  # deploy
        r"上线",  # go live
        r"复盘",  # review/retrospect
        r"安装",  # install
        r"操作流程",  # operation process
        r"操作步骤",  # operation steps
    ]

    for pattern in zh_strong_patterns:
        if re.search(pattern, query):
            return True

    # Chinese weak "how to" patterns (need verb after)
    zh_how_patterns = [
        r"怎么[^\s样了]{1,}",  # "how to" + verb (not state-asking)
        r"如何[^\s了]{1,}",  # "how to" + verb
    ]

    for pattern in zh_how_patterns:
        if re.search(pattern, query):
            return True

    # English procedural signal words
    en_patterns = [
        r"\bhow\s+to\b",
        r"\bsteps?\b",
        r"\bprocedure\b",
        r"\brunbook\b",
        r"\btroubleshoot",
        r"\bchecklist\b",
        r"\brollback\b",
        r"\bdeploy",
        r"\bconfig",
        r"\bsetup\b",
        r"\binstall",
    ]

    query_lower = query.lower()

    for pattern in en_patterns:
        if re.search(pattern, query_lower):
            return True

    return False
