# m_flow/knowledge/summarization/summarize_by_event.py
"""
Event-Level Summarization

Generate sections for a single event, rather than for the entire chunk.
This ensures each Episode gets its own dedicated sections, eliminating content duplication.

Phase 1: Event-Level Sections refactoring
Phase 2: Unified episodic + procedural routing (single LLM call when both enabled)
Phase 3: Reference date injection for relative time normalization
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Union
import structlog

from m_flow.shared.data_models import (
    Section,
    SectionedSummary,
    ProceduralCandidate,
    ProceduralCandidateList,
)
from m_flow.llm.prompts import read_query_prompt
from m_flow.llm.LLMGateway import LLMService
from m_flow.knowledge.summarization.text_summary_parser import TextSummaryParser

logger = structlog.get_logger("summarize_by_event")


def _format_reference_date(reference_date: Optional[Union[int, datetime]]) -> Optional[str]:
    """
    Format reference date for injection into prompt.
    
    Args:
        reference_date: Either milliseconds timestamp (int) or datetime object
        
    Returns:
        Formatted date string like "October 15, 2023" or None
    """
    if reference_date is None:
        return None
    
    try:
        if isinstance(reference_date, int):
            # Milliseconds timestamp
            ts = reference_date / 1000 if reference_date > 1e12 else reference_date
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(reference_date, datetime):
            dt = reference_date
        else:
            return None
        
        return dt.strftime("%B %d, %Y")
    except (ValueError, OSError, TypeError):
        return None


def _inject_date_context(prompt: str, reference_date: Optional[Union[int, datetime]]) -> str:
    """
    Inject reference date context into prompt.
    
    Looks for the placeholder in the prompt and replaces it with actual date.
    If no placeholder found, appends date context to the prompt.
    
    Args:
        prompt: Original prompt text
        reference_date: Reference date (ms timestamp or datetime)
        
    Returns:
        Modified prompt with date context
    """
    date_str = _format_reference_date(reference_date)
    if not date_str:
        # No valid date, remove placeholder if exists
        return prompt.replace("\n\nReference Date: {{REFERENCE_DATE}}", "")
    
    # Check for placeholder
    placeholder = "{{REFERENCE_DATE}}"
    if placeholder in prompt:
        return prompt.replace(placeholder, date_str)
    
    # No placeholder, append date context before "Language:" line if exists
    date_context = f"\nReference Date: {date_str} (use this to convert relative times like 'yesterday', 'last week')"
    if "\nLanguage:" in prompt:
        return prompt.replace("\nLanguage:", f"{date_context}\n\nLanguage:")
    
    # Fallback: append at end
    return prompt + date_context


@dataclass
class SummarizeResult:
    """Result of summarize_by_event with optional procedural candidates."""

    sections: List[Section]
    candidates: List[ProceduralCandidate] = None  # type: ignore
    episode_name: str = ""

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


def _extract_episode_name(raw_text: str) -> str:
    """Extract 'Episode Name: ...' from LLM output."""
    import re
    match = re.search(r"^Episode Name:\s*(.+?)(?:\n|$)", raw_text, re.I | re.M)
    return match.group(1).strip() if match else ""


async def summarize_by_event(
    event_sentences: List[str],
    event_topic: str,
    is_atomic: bool = False,
    include_procedural_routing: bool = False,
    reference_date: Optional[Union[int, datetime]] = None,
    generate_episode_name: bool = False,
    precise_mode: bool = False,
    session_date_header: str = "",
) -> Union[List[Section], SummarizeResult]:
    """
    Generate sections for a single event

    Args:
        event_sentences: List of sentences contained in this event
        event_topic: Topic of the event (from V2 routing)
        is_atomic: Whether this is an atomic event (uses single section prompt)
        include_procedural_routing: Whether to include procedural routing decision
                                   (ignored, use summarize_by_event_with_procedural instead)
        reference_date: Reference date for relative time conversion (ms timestamp or datetime).
                       Used to convert "yesterday" → "October 14, 2023" when reference is October 15.
        generate_episode_name: When True (content routing disabled), use the naming-aware
                              prompt and return SummarizeResult with episode_name populated.
                              When False (content routing enabled), return List[Section] as before.
        precise_mode: When True, use two-step pipeline (JSON routing + per-section concurrent
                     compression with anchor verification). When False, use original single-prompt.
        session_date_header: Session date line to inject into each section (precise_mode only).

    Returns:
        List[Section] when generate_episode_name=False (backward compatible),
        SummarizeResult when generate_episode_name=True (includes episode_name).
    """
    combined_text = " ".join(event_sentences)

    logger.warning(
        f"[summarize_by_event.DEBUG] event_sentences_count={len(event_sentences)}, "
        f"combined_text_len={len(combined_text)}, "
        f"combined_text_full={combined_text[:1000]!r}, "
        f"event_topic={event_topic!r}, is_atomic={is_atomic}, "
        f"generate_episode_name={generate_episode_name}, "
        f"precise_mode={precise_mode}"
    )

    if not combined_text.strip():
        logger.debug("[summarize_by_event] Empty content, returning empty sections")
        if generate_episode_name:
            return SummarizeResult(sections=[], episode_name="")
        return []

    # Precise mode: two-step pipeline
    if precise_mode and not is_atomic:
        from m_flow.knowledge.summarization.precise_summarize import (
            precise_summarize_by_event,
        )
        try:
            sections = await precise_summarize_by_event(
                event_sentences=event_sentences,
                event_topic=event_topic,
                session_date_header=session_date_header,
                generate_episode_name=generate_episode_name,
            )
            logger.info(
                f"[summarize_by_event] Precise mode: {len(sections)} sections"
            )
            if generate_episode_name:
                ep_name = sections[0].heading if sections else event_topic
                return SummarizeResult(sections=sections, episode_name=ep_name)
            return sections
        except Exception as e:
            logger.warning(
                f"[summarize_by_event] Precise mode failed: {e}, falling back to original"
            )

    # Original mode: single-prompt
    # Wrap content in explicit tags to prevent LLM from treating it as an instruction
    wrapped_text = f"<source_text>\n{combined_text}\n</source_text>"

    try:
        if is_atomic:
            system_prompt = read_query_prompt("summarize_content_atomic.txt")
            system_prompt = _inject_date_context(system_prompt, reference_date)
            result = await LLMService.extract_structured(
                wrapped_text, system_prompt, Section
            )
            logger.info(
                f"[summarize_by_event] Atomic: generated 1 section, "
                f"title='{result.heading[:30]}...'"
            )
            if generate_episode_name:
                return SummarizeResult(
                    sections=[result],
                    episode_name=result.heading,
                )
            return [result]
        else:
            prompt_file = (
                "summarize_content_text_with_naming.txt"
                if generate_episode_name
                else "summarize_content_text.txt"
            )
            system_prompt = read_query_prompt(prompt_file)
            system_prompt = _inject_date_context(system_prompt, reference_date)
            raw_text = await LLMService.complete_text(
                wrapped_text, system_prompt
            )

            episode_name = _extract_episode_name(raw_text) if generate_episode_name else ""

            result = TextSummaryParser.parse(
                raw_text,
                fallback_title=event_topic or "Content Summary",
            )

            sections = result.parts or []
            logger.info(
                f"[summarize_by_event] Text mode: generated {len(sections)} sections, "
                f"topic='{event_topic[:30]}...'"
                + (f", episode_name='{episode_name[:40]}'" if episode_name else "")
            )

            if generate_episode_name:
                return SummarizeResult(
                    sections=sections,
                    episode_name=episode_name or result.topic or "",
                )
            return sections

    except Exception as e:
        logger.warning(
            f"[summarize_by_event] Failed: {e}, using fallback",
            extra={"error": str(e), "topic": event_topic},
        )
        fallback_title = event_topic or "Content Summary"
        fallback_content = combined_text[:1000]

        sections = [Section(title=fallback_title, content=fallback_content)]
        if generate_episode_name:
            return SummarizeResult(sections=sections, episode_name=fallback_title)
        return sections


async def summarize_by_event_with_procedural(
    event_sentences: List[str],
    event_topic: str,
    is_atomic: bool = False,
    reference_date: Optional[Union[int, datetime]] = None,
    generate_episode_name: bool = False,
) -> SummarizeResult:
    """
    Generate sections for a single event WITH procedural routing.

    This combines episodic summarization and procedural routing in a single LLM call,
    reducing latency and ensuring consistent context for both decisions.

    Args:
        event_sentences: List of sentences contained in this event
        event_topic: Topic of the event (from V2 routing)
        is_atomic: Whether this is an atomic event (uses single section, still routes procedural)
        reference_date: Reference date for relative time conversion (ms timestamp or datetime).
                       Used to convert "yesterday" → "October 14, 2023" when reference is October 15.
        generate_episode_name: When True (content routing disabled), use the naming-aware
                              prompt and populate episode_name in the result.

    Returns:
        SummarizeResult with sections and 0-N procedural candidates
    """
    combined_text = " ".join(event_sentences)

    if not combined_text.strip():
        logger.debug("[summarize_by_event_with_procedural] Empty content")
        return SummarizeResult(sections=[], candidates=[])

    try:
        if is_atomic:
            system_prompt = read_query_prompt("summarize_content_atomic.txt")
            system_prompt = _inject_date_context(system_prompt, reference_date)
            section_result = await LLMService.extract_structured(
                combined_text, system_prompt, Section
            )

            candidates = await _quick_procedural_route_v2(combined_text)

            logger.info(
                f"[summarize_by_event_with_procedural] Atomic: 1 section, "
                f"{len(candidates)} candidates"
            )
            return SummarizeResult(
                sections=[section_result],
                candidates=candidates,
                episode_name=section_result.heading if generate_episode_name else "",
            )
        else:
            prompt_file = (
                "summarize_content_text_with_naming.txt"
                if generate_episode_name
                else "summarize_content_text.txt"
            )
            text_prompt = read_query_prompt(prompt_file)
            text_prompt = _inject_date_context(text_prompt, reference_date)
            raw_text = await LLMService.complete_text(
                combined_text, text_prompt
            )

            episode_name = _extract_episode_name(raw_text) if generate_episode_name else ""

            parsed = TextSummaryParser.parse(
                raw_text,
                fallback_title=event_topic or "Content Summary",
            )
            sections = parsed.parts or []

            candidates = await _quick_procedural_route_v2(combined_text)

            logger.info(
                f"[summarize_by_event_with_procedural] Episodic (split): "
                f"{len(sections)} sections, {len(candidates)} candidates"
                + (f", episode_name='{episode_name[:40]}'" if episode_name else "")
            )
            return SummarizeResult(
                sections=sections,
                candidates=candidates,
                episode_name=(episode_name or parsed.topic or "") if generate_episode_name else "",
            )

    except Exception as e:
        logger.warning(
            f"[summarize_by_event_with_procedural] Failed: {e}, using fallback",
            extra={"error": str(e), "topic": event_topic},
        )
        fallback_title = event_topic or "Content Summary"
        fallback_content = combined_text[:1000]

        return SummarizeResult(
            sections=[Section(title=fallback_title, content=fallback_content)],
            candidates=[],
            episode_name=fallback_title if generate_episode_name else "",
        )


QUICK_ROUTING_PROMPT = """Identify procedural candidates from content. Return JSON only.

Procedural knowledge = user-specific behaviors, preferences, methods, or patterns.

PROCEDURAL TYPES:
- user_preference: personal choice, style, format the user prefers
- user_habit: recurring pattern, routine, established way of working  
- reusable_process: workflow, method, sequence of actions
- persona: user/team identity, background, characteristics

Return: {"candidates": [{"search_text": "...", "confidence": 0.0-1.0, "reason": "...", "procedural_type": "..."}]}
Return empty array if no procedural content."""


async def _quick_procedural_route_v2(content: str) -> List[ProceduralCandidate]:
    """
    Quick procedural routing for short/atomic content.
    Returns 0-N ProceduralCandidate items.
    """
    try:
        result = await LLMService.extract_structured(
            content[:1500], QUICK_ROUTING_PROMPT, ProceduralCandidateList
        )
        return result.candidates or []
    except Exception as e:
        logger.warning(f"[_quick_procedural_route_v2] Failed: {e}")
        return []
