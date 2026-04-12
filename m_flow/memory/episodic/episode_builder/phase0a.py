# m_flow/memory/episodic/episode_builder/phase0a.py
"""
Phase 0A: Three-way Parallel Optimization

This module implements the first phase of episode processing:
1. Entity name extraction (from cache or LLM fallback)
2. Facet generation via summarize_by_event
3. Semantic matcher preparation

Phase 4-New-B: Extracted from write_episodic_memories.py

Design:
- All closures are converted to explicit parameters
- Returns Phase0AResult with all outputs
- Procedural candidates are collected and returned (not stored in closure)
- Exception handling follows the original pattern (return_exceptions=True)
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

from m_flow.shared.logging_utils import get_logger

# Normalization functions
from m_flow.memory.episodic.normalization import (
    _nfkc,
    truncate as _truncate,
)

# Pure functions (extracted in Phase 1)
from m_flow.memory.episodic.utils.pure_functions import (
    _has_valid_sections,
    _extract_event_sentences,
    _create_facets_from_sections_direct,
    _generate_episode_summary_from_sections,
)

# LLM utilities
from m_flow.shared.llm_concurrency import get_global_llm_semaphore

# LLM tasks
from m_flow.memory.episodic.llm_tasks import (
    llm_extract_entity_names as _llm_extract_entity_names,
)
from m_flow.knowledge.summarization.summarize_by_event import (
    summarize_by_event,
    summarize_by_event_with_procedural,
)

# Data classes
from m_flow.memory.episodic.models import (
    EpisodicWriteDraft,
    EpisodicFacetDraft,
)

# Semantic merge
from m_flow.memory.episodic.semantic_merge import SemanticFacetMatcher, ExistingFacetInfo

# Pipeline contexts
from m_flow.memory.episodic.episode_builder.pipeline_contexts import (
    EpisodeContext,
    Phase0AResult,
)

if TYPE_CHECKING:
    from m_flow.knowledge.summarization.models import FragmentDigest


logger = get_logger(__name__)


# ============================================================
# Task 1: Entity Name Extraction
# ============================================================


async def _task_extract_entity_names(
    episode_id_str: str,
    episode_source_events: Dict[str, List[str]],
    doc_entity_cache: Dict[str, List[str]],
    doc_summaries: List["FragmentDigest"],
    chunk_summaries: List[str],
    max_entities_per_episode: int,
) -> List[str]:
    """
    Extract entity names for an episode.

    Tries to use cached entity names from routing phase first.
    Falls back to LLM extraction if cache is empty.

    Args:
        episode_id_str: Episode identifier
        episode_source_events: Mapping of episode_id to event_ids
        doc_entity_cache: Cache of entity names from routing phase
        doc_summaries: List of document summaries
        chunk_summaries: List of chunk summary texts
        max_entities_per_episode: Maximum number of entities (0 = unlimited)

    Returns:
        List of extracted entity names
    """
    # Get related event IDs for this episode
    related_event_ids: set = set()
    source_events = episode_source_events.get(episode_id_str, [])
    for evt_id in source_events:
        related_event_ids.add(evt_id)

    # Merge cached entity names from all related events
    # Distinguish "cache miss" (routing didn't run) from "cache hit with empty result"
    cache_was_populated = False
    cached_entity_names: List[str] = []
    for evt_id in related_event_ids:
        if evt_id in doc_entity_cache:
            cache_was_populated = True
            cached_entity_names.extend(doc_entity_cache[evt_id])

    # Use cached entity names from routing phase (including empty results)
    if cache_was_populated:
        logger.info(
            f"[episodic] Entity extraction from routing cache: {len(cached_entity_names)} names "
            f"from {len(related_event_ids)} event(s)"
        )

        # Deduplicate and count frequency
        entity_freq: Dict[str, int] = {}
        for name in cached_entity_names:
            name_clean = _nfkc(name)
            if name_clean:
                entity_freq[name_clean] = entity_freq.get(name_clean, 0) + 1

        _top_names = sorted(entity_freq.keys(), key=lambda n: entity_freq[n], reverse=True)
        if max_entities_per_episode > 0:
            _top_names = _top_names[:max_entities_per_episode]

        logger.info(
            f"[episodic] Entity extraction complete: "
            f"{len(entity_freq)} unique, top {len(_top_names)} selected"
        )
        return _top_names
    else:
        # No cache available — fallback to LLM extraction
        logger.info(
            "[episodic] Entity cache empty (routing skipped), "
            "extracting entities via LLM in Phase 0A"
        )

        # Prepare text segments
        text_segments: List[str] = []
        if _has_valid_sections(doc_summaries):
            for ts in doc_summaries:
                sections = getattr(ts, "sections", None) or []
                for sec in sections:
                    content = (sec.text or "").strip()
                    if content:
                        text_segments.append(content)

        if not text_segments:
            text_segments = [s for s in chunk_summaries if s.strip()]

        if not text_segments:
            for ts in doc_summaries:
                text = getattr(ts, "text", "") or ""
                if text.strip():
                    text_segments.append(text)

        if not text_segments:
            logger.warning("[episodic] No text segments for entity extraction, returning empty")
            return []

        # Merge all segments into one LLM call
        combined_text = "\n\n".join(text_segments)
        total_chars = len(combined_text)

        logger.info(
            f"[episodic] Phase 0A entity extraction: "
            f"{len(text_segments)} segments merged → {total_chars} chars (1 LLM call)"
        )

        _fallback_sem = get_global_llm_semaphore()

        try:
            async with _fallback_sem:
                result = await _llm_extract_entity_names(text=combined_text, batch_index=0)
                all_names = result.names or []
        except Exception as e:
            logger.warning(f"[episodic] Phase 0A entity extraction failed: {e}")
            all_names = []

        # Deduplicate
        seen: set = set()
        _top_names: List[str] = []
        for name in all_names:
            name_clean = _nfkc(name)
            if name_clean and name_clean not in seen:
                seen.add(name_clean)
                _top_names.append(name_clean)

        if max_entities_per_episode > 0:
            _top_names = _top_names[:max_entities_per_episode]

        logger.info(
            f"[episodic] Phase 0A LLM fallback: extracted {len(_top_names)} unique entities "
            f"from {len(text_segments)} merged segments"
        )
        return _top_names


# ============================================================
# Task 2: Facet Generation
# ============================================================


async def _task_generate_facets(
    episode_id_str: str,
    episode_source_events: Dict[str, List[str]],
    original_event_routing_types: Dict[str, str],
    doc_summaries: List["FragmentDigest"],
    doc_title: str,
    prev_title: str,
    prev_signature: str,
    prev_summary: str,
    enable_procedural_routing: bool,
    content_routing_disabled: bool = False,
    config: Any = None,
) -> Tuple["EpisodicWriteDraft", List[Dict[str, Any]]]:
    """
    Generate facets via summarize_by_event.

    Args:
        episode_id_str: Episode identifier
        episode_source_events: Mapping of episode_id to event_ids
        original_event_routing_types: Original routing types for events
        doc_summaries: List of document summaries
        doc_title: Document title
        prev_title: Previous episode title (from state)
        prev_signature: Previous episode signature (from state)
        prev_summary: Previous episode summary (from state)
        enable_procedural_routing: Whether to enable procedural routing
        content_routing_disabled: When True, summarize_by_event uses the naming-aware
                                 prompt to generate an Episode name (since content routing
                                 did not provide one via event_topic).

    Returns:
        Tuple of (EpisodicWriteDraft, list of procedural candidates)
    """
    section_facets = []
    combined_summary = ""
    procedural_candidates: List[Dict[str, Any]] = []

    # Extract reference_date for relative time conversion in summarization
    # Priority: 1. Document.created_at (from API created_at parameter)
    #           2. ContentFragment.created_at (default: current system time)
    # This allows LLM to convert "yesterday" → "October 14, 2023" when content was created on Oct 15
    reference_date = None
    for ts in doc_summaries:
        chunk = ts.made_from
        if chunk:
            # Priority 1: Try Document.created_at (preserves API-provided historical timestamp)
            if hasattr(chunk, "is_part_of") and chunk.is_part_of:
                doc = chunk.is_part_of
                if hasattr(doc, "created_at") and doc.created_at:
                    reference_date = doc.created_at
                    break
            # Priority 2: Fall back to ContentFragment.created_at
            if hasattr(chunk, "created_at") and chunk.created_at:
                reference_date = chunk.created_at
                break

    # Get source event IDs
    source_event_ids = episode_source_events.get(episode_id_str, [])

    # Extract sentences for this Episode
    if source_event_ids:
        event_sentences, event_topic, is_atomic = _extract_event_sentences(
            doc_summaries,
            source_event_ids,
            original_event_routing_types,
        )
    else:
        event_sentences = []
        event_topic = "Content"
        is_atomic = False

        for ts in doc_summaries:
            chunk_text = ts.made_from.text if ts.made_from else ""
            if chunk_text:
                event_sentences.append(chunk_text)

        logger.debug(
            f"[episodic] No event_ids for episode={episode_id_str[:20]}..., "
            f"using {len(event_sentences)} chunk texts"
        )

    # Generate sections via summarize_by_event
    generated_episode_name = ""
    if event_sentences:
        try:
            async with get_global_llm_semaphore():
                if enable_procedural_routing:
                    summarize_result = await summarize_by_event_with_procedural(
                        event_sentences=event_sentences,
                        event_topic=event_topic,
                        is_atomic=is_atomic,
                        reference_date=reference_date,
                        generate_episode_name=content_routing_disabled,
                    )
                    event_sections = summarize_result.sections
                    if content_routing_disabled:
                        generated_episode_name = summarize_result.episode_name or ""

                    # Collect procedural candidates
                    for candidate in summarize_result.candidates or []:
                        proc_entry = {
                            "episode_id": episode_id_str,
                            "candidate": candidate,
                            "event_sentences": event_sentences,
                            "event_topic": event_topic,
                        }
                        procedural_candidates.append(proc_entry)
                        logger.info(
                            f"[episodic] Procedural candidate: type={candidate.procedural_type}, "
                            f"search_text='{candidate.search_text[:50]}...'"
                        )
                else:
                    _precise = getattr(config, "precise_mode", False)
                    _date_hdr = ""
                    if _precise and reference_date is not None:
                        from m_flow.knowledge.summarization.summarize_by_event import _format_reference_date
                        _fmt = _format_reference_date(reference_date)
                        if _fmt:
                            _date_hdr = f"[Session Date: {_fmt}]"
                    result = await summarize_by_event(
                        event_sentences=event_sentences,
                        event_topic=event_topic,
                        is_atomic=is_atomic,
                        reference_date=reference_date,
                        generate_episode_name=content_routing_disabled,
                        precise_mode=_precise,
                        session_date_header=_date_hdr,
                    )
                    from m_flow.knowledge.summarization.summarize_by_event import SummarizeResult
                    if content_routing_disabled and isinstance(result, SummarizeResult):
                        event_sections = result.sections
                        generated_episode_name = result.episode_name or ""
                    else:
                        event_sections = result

                if event_sections:
                    section_facets = _create_facets_from_sections_direct(event_sections)
                    combined_summary = _generate_episode_summary_from_sections(event_sections)

                    logger.info(
                        f"[episodic] Generated {len(event_sections)} sections, "
                        f"{len(section_facets)} facets, topic='{event_topic[:30]}...'"
                        + (f", episode_name='{generated_episode_name[:40]}'" if generated_episode_name else "")
                    )
        except Exception as e:
            logger.warning(f"[episodic] summarize_by_event failed: {e}, using fallback")

    # Fallback: Create simple facet from chunk text
    if not section_facets:
        logger.warning(
            f"[episodic] No sections generated for episode={episode_id_str[:20]}..., "
            f"creating fallback facet"
        )
        for ts in doc_summaries:
            chunk_text = (ts.made_from.text or "")[:500] if ts.made_from else ""
            if chunk_text:
                fallback_search = generated_episode_name or doc_title or "Content"
                section_facets.append(
                    EpisodicFacetDraft(
                        facet_type="content",
                        search_text=fallback_search,
                        description=chunk_text,
                        aliases=[],
                    )
                )
                if not combined_summary:
                    combined_summary = chunk_text

    # Fallback chain for combined_summary
    if not combined_summary:
        overall_topics = [
            getattr(ts, "overall_topic", None) or ""
            for ts in doc_summaries
            if getattr(ts, "overall_topic", None)
        ]
        if overall_topics:
            combined_summary = "; ".join(overall_topics)
        elif section_facets:
            section_titles = [f.search_text for f in section_facets]
            combined_summary = "Topics: " + ", ".join(section_titles[:10])
        else:
            combined_summary = doc_title or "Episode content"

    if not section_facets:
        logger.warning(
            f"[episodic] No facets created for episode={episode_id_str[:20]}..., "
            f"this may indicate an issue with summarization"
        )

    if prev_summary.strip():
        combined_summary = prev_summary.strip() + " " + combined_summary

    effective_title = prev_title or generated_episode_name or doc_title or "Episode"

    _draft = EpisodicWriteDraft(
        title=_truncate(effective_title, 40),
        signature=prev_signature or _truncate(effective_title, 24) or "episode",
        summary=combined_summary,
        facets=section_facets,
        alias_updates=[],
    )
    logger.info(f"[episodic] Facet creation: {len(section_facets)} facets")

    return _draft, procedural_candidates


# ============================================================
# Task 3: Semantic Matcher Preparation
# ============================================================


async def _task_prepare_matcher(
    state_facets: List[Any],
    enable_semantic_merge: bool,
    semantic_merge_threshold: float,
) -> "SemanticFacetMatcher":
    """
    Prepare semantic facet matcher with existing facets.

    Args:
        state_facets: List of existing facets from episode state
        enable_semantic_merge: Whether semantic merge is enabled
        semantic_merge_threshold: Threshold for semantic matching

    Returns:
        Prepared SemanticFacetMatcher instance
    """
    _matcher = SemanticFacetMatcher(
        enabled=bool(enable_semantic_merge),
        threshold=float(semantic_merge_threshold),
    )
    existing_facet_infos = [
        ExistingFacetInfo(
            id=f.id,
            facet_type=f.facet_type,
            search_text=f.search_text,
            aliases=f.aliases,
        )
        for f in state_facets
    ]
    await _matcher.prepare(existing_facet_infos)
    logger.info(
        f"[episodic] semantic_matcher prepared with {len(existing_facet_infos)} existing facets"
    )
    return _matcher


# ============================================================
# Main Execution Function
# ============================================================


async def execute_phase0a(ctx: EpisodeContext) -> Phase0AResult:
    """
    Execute Phase 0A: Three-way parallel optimization.

    Runs three tasks in parallel:
    1. Entity name extraction (from cache or LLM fallback)
    2. Facet generation via summarize_by_event
    3. Semantic matcher preparation

    Args:
        ctx: Episode context containing all required inputs

    Returns:
        Phase0AResult containing:
        - top_entity_names: Extracted entity names
        - draft: Generated EpisodicWriteDraft
        - semantic_matcher: Prepared SemanticFacetMatcher
        - procedural_candidates: Collected procedural candidates
    """
    _parallel_phase_start = time.time()
    logger.info("[episodic] ========== Phase 0A: Three-way parallel start ==========")

    # Default values for error cases
    top_entity_names: List[str] = []
    draft: EpisodicWriteDraft = EpisodicWriteDraft(
        title=ctx.prev_title or _truncate(ctx.doc_title, 40) or "Episode",
        signature=ctx.prev_signature or _truncate(ctx.doc_title, 24) or "episode",
        summary=ctx.prev_summary or "",
        facets=[],
        alias_updates=[],
    )
    semantic_matcher: SemanticFacetMatcher = SemanticFacetMatcher(
        enabled=bool(ctx.config.enable_semantic_merge),
        threshold=float(ctx.config.semantic_merge_threshold),
    )
    procedural_candidates: List[Dict[str, Any]] = []

    try:
        # Execute three tasks in parallel
        parallel_results = await asyncio.gather(
            _task_extract_entity_names(
                episode_id_str=ctx.episode_id_str,
                episode_source_events=ctx.episode_source_events,
                doc_entity_cache=ctx.doc_entity_cache,
                doc_summaries=ctx.doc_summaries,
                chunk_summaries=ctx.chunk_summaries,
                max_entities_per_episode=ctx.config.max_entities_per_episode,
            ),
            _task_generate_facets(
                episode_id_str=ctx.episode_id_str,
                episode_source_events=ctx.episode_source_events,
                original_event_routing_types=ctx.original_event_routing_types,
                doc_summaries=ctx.doc_summaries,
                doc_title=ctx.doc_title,
                prev_title=ctx.prev_title,
                prev_signature=ctx.prev_signature,
                prev_summary=ctx.prev_summary,
                enable_procedural_routing=ctx.config.enable_procedural_routing,
                content_routing_disabled=ctx.config.content_routing_disabled,
                config=ctx.config,
            ),
            _task_prepare_matcher(
                state_facets=ctx.state.facets,
                enable_semantic_merge=ctx.config.enable_semantic_merge,
                semantic_merge_threshold=ctx.config.semantic_merge_threshold,
            ),
            return_exceptions=True,
        )

        # Handle entity name extraction result
        if isinstance(parallel_results[0], Exception):
            logger.error(f"[episodic] Phase 0A: Entity extraction failed: {parallel_results[0]}")
        else:
            top_entity_names = parallel_results[0]

        # Handle facet generation result
        if isinstance(parallel_results[1], Exception):
            logger.error(f"[episodic] Phase 0A: Facet generation failed: {parallel_results[1]}")
        else:
            draft, procedural_candidates = parallel_results[1]

        # Handle matcher preparation result
        if isinstance(parallel_results[2], Exception):
            logger.error(f"[episodic] Phase 0A: Matcher preparation failed: {parallel_results[2]}")
            semantic_matcher = SemanticFacetMatcher(
                enabled=bool(ctx.config.enable_semantic_merge),
                threshold=float(ctx.config.semantic_merge_threshold),
            )
        else:
            semantic_matcher = parallel_results[2]

    except Exception as e:
        logger.error(f"[episodic] Phase 0A: Unexpected error in parallel execution: {e}")
        # Continue with default values (already set above)

    _parallel_phase_elapsed = time.time() - _parallel_phase_start
    logger.info(
        f"[episodic] ========== Phase 0A completed: {_parallel_phase_elapsed:.2f}s =========="
        f" (entity_names={len(top_entity_names)}, facets={len(draft.facets or [])})"
    )

    return Phase0AResult(
        top_entity_names=top_entity_names,
        draft=draft,
        semantic_matcher=semantic_matcher,
        procedural_candidates=procedural_candidates,
    )


# ============================================================
# Module exports
# ============================================================

__all__ = [
    "execute_phase0a",
    "_task_extract_entity_names",
    "_task_generate_facets",
    "_task_prepare_matcher",
]
