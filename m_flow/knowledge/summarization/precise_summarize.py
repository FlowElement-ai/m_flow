# m_flow/knowledge/summarization/precise_summarize.py
"""
Precise two-step summarization pipeline.

Step 1: LLM JSON routing — split sentences into topic sections (lossless, code-assembled)
Step 2: Per-section concurrent compression with anchor verification fallback

Activated via precise_mode=True in EpisodeConfig / memorize kwargs.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import structlog

from m_flow.llm.prompts import read_query_prompt
from m_flow.llm.LLMGateway import LLMService
from m_flow.shared.data_models import Section

logger = structlog.get_logger("precise_summarize")

# ── Anchor extraction patterns ────────────────────────────────────────

_ANCHOR_PATTERNS: List[Tuple[str, str]] = [
    (r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+(?:st|nd|rd|th)?", "date"),
    (r"\d{4}/\d{2}/\d{2}", "date"),
    (r"\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?", "time"),
    (r"\$\d[\d,]*(?:\.\d+)?", "money"),
    (r"\d{1,3}(?:,\d{3})+", "number"),
    (r"\d+(?:\.\d+)?\s*(?:miles?\s*per\s*gallon|mpg|mph|%|hours?|minutes?|months?|days?|years?|weeks?|km|miles?|lbs?|kg|oz|feet|ft)", "measure"),
    (r"every\s+\d+-\d+\s+\w+", "frequency"),
    (r"\d+(?:st|nd|rd|th)", "ordinal"),
]


@dataclass
class Anchor:
    text: str
    atype: str
    sentence_idx: int
    context: str


def extract_anchors(sentences: List[str]) -> List[Anchor]:
    """Extract all preservable data points from each sentence."""
    anchors: List[Anchor] = []
    seen: set = set()
    for idx, sent in enumerate(sentences):
        for pattern, atype in _ANCHOR_PATTERNS:
            for m in re.finditer(pattern, sent, re.IGNORECASE):
                text = m.group(0).strip()
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                start = max(0, m.start() - 40)
                end = min(len(sent), m.end() + 40)
                anchors.append(Anchor(
                    text=text,
                    atype=atype,
                    sentence_idx=idx,
                    context=sent[start:end],
                ))
    return anchors


# ── Step 1: JSON topic routing ────────────────────────────────────────


async def _name_single_content(text: str) -> str:
    """Generate a short specific title for single-content input via LLM.

    Uses the same Episode Naming Policy as content routing
    (``sentence_grouping.txt``) and the original-mode naming prompt
    (``summarize_content_text_with_naming.txt``): bounded semantic focus,
    distinguishable anchors, no vague umbrella labels.

    Used when content routing is disabled and precise mode needs a
    meaningful Episode name from a single chunk of text.
    """
    system_prompt = read_query_prompt("precise_name_single_content.txt")
    truncated = text[:800]
    try:
        raw = await LLMService.complete_text(truncated, system_prompt)
        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            title = (parsed.get("title") or "").strip()
            if title and title.lower() not in ("content", "untitled", "summary", "text"):
                return title
    except Exception as e:
        logger.warning(f"[precise] Single-content naming failed: {e}")

    # Heuristic fallback: first complete sentence or clause
    m = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    first_sent = m[0].strip() if m else text.strip()
    if len(first_sent) <= 60:
        return first_sent
    return first_sent[:57] + "..."


async def step1_route_sections(
    sentences: List[str],
    generate_episode_name: bool = False,
) -> List[Dict]:
    """
    Ask LLM to group sentence indices into topic sections.
    Returns list of {"title": str, "sentence_indices": [int]}.
    Code assembles original text from indices — zero information loss.

    Args:
        sentences: List of text chunks to route into sections.
        generate_episode_name: When True (content routing disabled), generate
            a meaningful title even for single-element inputs instead of
            the default "Content" placeholder.
    """
    if len(sentences) <= 1:
        if generate_episode_name and sentences:
            title = await _name_single_content(sentences[0])
            logger.info(f"[precise] Single-content naming: '{title[:50]}'")
        else:
            title = "Content"
        return [{"title": title, "sentence_indices": list(range(len(sentences)))}]

    truncated = [s[:150] + "..." if len(s) > 150 else s for s in sentences]
    numbered = "\n".join(f"[{j}] {t}" for j, t in enumerate(truncated))

    system_prompt = (
        "Group the numbered sentences into topic sections. Return JSON:\n"
        '{"sections": [{"title": "name", "sentence_indices": [0,1]}]}\n\n'
        "Rules:\n"
        f"- Every index 0-{len(sentences)-1} must appear exactly once\n"
        "- Do NOT group by speaker role. A question and its answer belong to the SAME section\n"
        "- Group by semantic focus or topic\n"
        "- Titles: short, specific, anchored to a named entity"
    )

    try:
        raw = await LLMService.complete_text(numbered, system_prompt)
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            sections = parsed.get("sections", [])
        else:
            sections = []
    except Exception as e:
        logger.warning(f"[precise] Step1 routing failed: {e}, using single section fallback")
        sections = []

    covered: set = set()
    for sec in sections:
        covered.update(sec.get("sentence_indices", []))
    missing = set(range(len(sentences))) - covered
    if missing:
        sections.append({"title": "Other", "sentence_indices": sorted(missing)})

    if not sections:
        sections = [{"title": "Content", "sentence_indices": list(range(len(sentences)))}]

    logger.info(
        f"[precise] Step1 routed {len(sentences)} sentences into {len(sections)} sections"
    )
    return sections


# ── Step 2: Per-section compression ───────────────────────────────────

async def step2_compress_section(
    section_text: str,
) -> str:
    """Compress a single section using the precise compression prompt."""
    system_prompt = read_query_prompt("precise_compress.txt")
    try:
        result = await LLMService.complete_text(section_text, system_prompt)
        if not result or len(result) < len(section_text) * 0.15:
            logger.warning("[precise] Step2 output too short, using original text")
            return section_text
        return result
    except Exception as e:
        logger.warning(f"[precise] Step2 compression failed: {e}, using original text")
        return section_text


# ── Anchor verification & recovery ────────────────────────────────────

def verify_and_recover(
    output: str,
    section_sent_idxs: List[int],
    sentences: List[str],
    anchors: List[Anchor],
) -> Tuple[str, List[str]]:
    """
    Check that all anchors for this section appear in the output.
    If any are missing, insert the recovery clause at the correct position.
    """
    sec_anchors = [a for a in anchors if a.sentence_idx in section_sent_idxs]
    recovered: List[str] = []

    for anchor in sec_anchors:
        if anchor.text.lower() in output.lower():
            continue

        lines = output.split("\n")
        insert_after = len(lines) - 1
        for prior_idx in sorted(section_sent_idxs, reverse=True):
            if prior_idx >= anchor.sentence_idx:
                continue
            needle = sentences[prior_idx][:30].lower()
            for li, line in enumerate(lines):
                if needle in line.lower():
                    insert_after = li
                    break
            break

        lines.insert(insert_after + 1, anchor.context)
        output = "\n".join(lines)
        recovered.append(anchor.text)

    return output, recovered


# ── Main entry point ──────────────────────────────────────────────────

async def precise_summarize_by_event(
    event_sentences: List[str],
    event_topic: str,
    session_date_header: str = "",
    generate_episode_name: bool = False,
) -> List[Section]:
    """
    Two-step precise summarization.

    1. LLM routes sentences into topic sections (JSON indices).
    2. Code assembles original text per section (lossless).
    3. Each section is concurrently compressed by LLM.
    4. Code verifies all anchors are preserved; recovers any missing ones.

    Args:
        generate_episode_name: When True (content routing disabled), step1
            generates a meaningful title even for single-element inputs.
            The caller reads ``sections[0].heading`` as the Episode name.
    """
    if not event_sentences:
        return []

    anchors = extract_anchors(event_sentences)
    logger.info(f"[precise] Extracted {len(anchors)} anchors from {len(event_sentences)} sentences")

    section_defs = await step1_route_sections(
        event_sentences,
        generate_episode_name=generate_episode_name,
    )

    async def _process_section(sec_def: Dict) -> Section:
        title = sec_def.get("title", "Untitled")
        idxs = sec_def.get("sentence_indices", [])
        valid_idxs = [j for j in idxs if 0 <= j < len(event_sentences)]

        parts = []
        if session_date_header:
            parts.append(session_date_header)
        parts.extend(event_sentences[j] for j in valid_idxs)
        assembled = f"[{title}]\n" + "\n\n".join(parts)

        compressed = await step2_compress_section(assembled)

        patched, recovered = verify_and_recover(
            compressed, set(valid_idxs), event_sentences, anchors
        )
        if recovered:
            logger.info(f"[precise] Recovered {len(recovered)} anchors in [{title}]: {recovered}")

        return Section(heading=title, text=patched)

    tasks = [_process_section(sd) for sd in section_defs]
    sections = await asyncio.gather(*tasks)

    total_in = sum(len(s) for s in event_sentences)
    total_out = sum(len(s.text) for s in sections)
    logger.info(
        f"[precise] Complete: {len(event_sentences)} sentences → "
        f"{len(sections)} sections, {total_in}→{total_out} chars "
        f"(compression {(1 - total_out / max(total_in, 1)) * 100:.0f}%)"
    )

    return list(sections)
