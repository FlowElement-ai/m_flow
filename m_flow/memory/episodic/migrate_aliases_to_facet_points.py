# m_flow/memory/episodic/migrate_aliases_to_facet_points.py
"""
Stage 4: Migration and convergence

Migrate information points in existing Facet.aliases to FacetPoint,
then converge Facet.aliases to pure synonym/restatement purposes.

Usage:
    python -m m_flow.memory.episodic.migrate_aliases_to_facet_points

Or call in code:
    from m_flow.memory.episodic.migrate_aliases_to_facet_points import migrate_aliases_to_facet_points
    await migrate_aliases_to_facet_points()
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Set

from m_flow.shared.logging_utils import get_logger
from m_flow.adapters.graph import get_graph_provider
from m_flow.adapters.vector import get_vector_provider
from m_flow.core.domain.models import FacetPoint
from m_flow.core.domain.models.memory_space import MemorySpace
from m_flow.core import Edge
from m_flow.core.domain.utils.generate_node_id import generate_node_id


logger = get_logger("MigrateAliasesToFacetPoints")


def _normalize_for_id(text: str) -> str:
    """Normalize text for stable ID generation."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_aggressive(text: str) -> str:
    """
    Aggressive normalization: removes ALL punctuation and whitespace.

    Used for deduplication where punctuation differences should be ignored.
    Note: Different from normalization.normalize_for_compare which only collapses whitespace.
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def _is_bad_search_text(st: str) -> bool:
    """Check if a search_text is too generic or template-like."""
    if not st or len(st) < 2:
        return True

    # Generic terms without context
    bad_terms = {
        "原因",
        "影响",
        "问题",
        "方案",
        "进展",
        "风险",
        "背景",
        "目标",
        "决策",
        "计划",
        "结果",
        "内容",
        "情况",
        "说明",
        "备注",
        "其他",
        "cause",
        "effect",
        "issue",
        "solution",
        "progress",
        "risk",
    }
    if _normalize_aggressive(st) in bad_terms:
        return True

    # Template-like patterns
    template_patterns = [
        r"^facet\s*\d*$",
        r"^point\s*\d*$",
        r"^item\s*\d*$",
        r"^\d+\.$",
    ]
    for pat in template_patterns:
        if re.match(pat, st.lower()):
            return True

    return False


@dataclass
class MigrationStats:
    facets_processed: int = 0
    points_created: int = 0
    points_skipped_duplicate: int = 0
    points_skipped_bad: int = 0
    aliases_retained: int = 0
    aliases_removed: int = 0
    errors: int = 0


async def fetch_all_facets(graph_engine) -> List[Dict]:
    """Fetch all Facet nodes from the graph."""
    try:
        nodes, _edges = await graph_engine.query_by_attributes([{"type": ["Facet"]}])
        facets = []
        for node_id, props in nodes:
            props = dict(props) if props else {}
            inner = props.get("properties", {})
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except (json.JSONDecodeError, TypeError):
                    inner = {}
            if isinstance(inner, dict):
                props.update(inner)
            facets.append({"id": str(node_id), "name": str(props.get("name", "")), **props})
        return facets
    except Exception as e:
        logger.error(f"Failed to fetch facets: {e}")
        return []


async def fetch_existing_point_search_texts(graph_engine, facet_id: str) -> Set[str]:
    """Fetch existing FacetPoint search_texts under a Facet."""
    try:
        edges = await graph_engine.get_edges(facet_id)
        existing = set()
        for _src_props, rel_name, dst_props in edges:
            if rel_name != "has_point":
                continue
            if not isinstance(dst_props, dict) or dst_props.get("type") != "FacetPoint":
                continue
            props = dst_props.get("properties", {})
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (json.JSONDecodeError, TypeError):
                    props = {}
            if isinstance(props, dict):
                st = props.get("search_text", "")
                if st:
                    existing.add(_normalize_aggressive(st))
        return existing
    except Exception as e:
        logger.debug(f"Failed to fetch existing points for facet {facet_id}: {e}")
        return set()


def _make_has_point_edge_text(
    facet_type: str,
    facet_search_text: str,
    point_search_text: str,
) -> str:
    """Create edge_text for has_point edge (concise format)."""
    # Concise format to reduce vector search interference
    return f"{facet_search_text} -> {point_search_text}"


async def migrate_aliases_to_facet_points(
    dry_run: bool = False,
    keep_synonyms: bool = True,
    max_synonyms_to_keep: int = 3,
    verbose: bool = True,
) -> MigrationStats:
    """
    Migrate Facet.aliases to FacetPoints.

    Args:
        dry_run: If True, only report what would be done without making changes.
        keep_synonyms: If True, retain a few synonyms in Facet.aliases for fallback.
        max_synonyms_to_keep: Maximum number of synonyms to keep per Facet.
        verbose: Print progress information.

    Returns:
        MigrationStats with counts of processed items.
    """
    stats = MigrationStats()

    if verbose:
        print("=" * 70)
        print("Stage 4: Migrate Facet.aliases to FacetPoints")
        print("=" * 70)
        if dry_run:
            print("[DRY RUN] No changes will be made.")
        print()

    try:
        graph_engine = await get_graph_provider()
        get_vector_provider()
    except Exception as e:
        logger.error(f"Failed to initialize engines: {e}")
        stats.errors += 1
        return stats

    # Fetch Episodic MemorySpace
    try:
        ns_nodes, _ns_edges = await graph_engine.query_by_attributes([{"type": ["MemorySpace"], "name": ["Episodic"]}])
        if not ns_nodes:
            logger.warning("Episodic MemorySpace not found, will skip memory_spaces")
            episodic_nodeset = None
        else:
            episodic_nodeset = MemorySpace(id=str(ns_nodes[0][0]), name="Episodic")
    except Exception as e:
        logger.warning(f"Failed to fetch Episodic MemorySpace: {e}")
        episodic_nodeset = None

    # Fetch all Facets
    facets = await fetch_all_facets(graph_engine)
    if verbose:
        print(f"Found {len(facets)} Facets to process")
        print()

    points_to_add = []
    edges_to_add = []

    for facet in facets:
        stats.facets_processed += 1

        fid = facet.get("id", "")
        fname = facet.get("name", "")
        ftype = facet.get("facet_type", "")
        fsearch = facet.get("search_text", "") or fname
        aliases = facet.get("aliases", [])

        if not isinstance(aliases, list):
            aliases = []

        if not aliases:
            continue

        # Fetch existing points to avoid duplicates
        existing_points = await fetch_existing_point_search_texts(graph_engine, fid)

        synonyms_to_keep = []
        info_points = []

        for alias in aliases:
            alias = str(alias).strip()
            if not alias:
                continue

            alias_norm = _normalize_aggressive(alias)

            # Check if it's a duplicate
            if alias_norm in existing_points:
                stats.points_skipped_duplicate += 1
                continue

            # Check if it's a bad search_text
            if _is_bad_search_text(alias):
                stats.points_skipped_bad += 1
                # Keep as synonym if short
                if len(alias) < 50 and keep_synonyms and len(synonyms_to_keep) < max_synonyms_to_keep:
                    synonyms_to_keep.append(alias)
                    stats.aliases_retained += 1
                else:
                    stats.aliases_removed += 1
                continue

            # Classify: short ones are synonyms, longer ones are info points
            if len(alias) < 30:
                if keep_synonyms and len(synonyms_to_keep) < max_synonyms_to_keep:
                    synonyms_to_keep.append(alias)
                    stats.aliases_retained += 1
                else:
                    info_points.append(alias)
            else:
                info_points.append(alias)

        # Create FacetPoints for info points
        for point_text in info_points:
            point_id = str(generate_node_id(f"FacetPoint:{fid}:{_normalize_for_id(point_text)}"))

            point_dp = FacetPoint(
                id=point_id,
                name=point_text,
                search_text=point_text,
                aliases=None,
                aliases_text=None,
                description=None,
                memory_spaces=[episodic_nodeset] if episodic_nodeset else None,
            )

            edge_text = _make_has_point_edge_text(
                facet_type=ftype,
                facet_search_text=fsearch,
                point_search_text=point_text,
            )

            edge = Edge(
                source_node_id=fid,
                target_node_id=point_id,
                relationship_name="has_point",
                edge_text=edge_text,
            )

            points_to_add.append(point_dp)
            edges_to_add.append(edge)
            existing_points.add(_normalize_aggressive(point_text))
            stats.points_created += 1

        # Update aliases count
        stats.aliases_removed += len(aliases) - len(synonyms_to_keep) - len(info_points)

        if verbose and (stats.facets_processed % 10 == 0):
            print(f"  Processed {stats.facets_processed}/{len(facets)} facets...")

    # Apply changes
    # Note: For now, we only support dry-run reporting.
    # The actual write should be done through re-memorizing with MFLOW_EPISODIC_ENABLE_FACET_POINTS=true,
    # which uses the correct write pipeline (persist_memory_nodes with proper edge handling).
    if not dry_run and points_to_add:
        if verbose:
            print()
            print(f"[WARN] Found {len(points_to_add)} FacetPoints to migrate.")
            print("   Recommended: Re-run memorize with MFLOW_EPISODIC_ENABLE_FACET_POINTS=true")
            print("   The new Stage2 write pipeline will automatically extract FacetPoints from Facet.description.")
            print()
            print("   Alternatively, you can manually trigger migration by:")
            print("   1. Delete and re-add your documents")
            print("   2. Run memorize again")
            print()

        # For now, we don't perform the actual write
        # This is because FacetPoint edges need to be attached to Facet MemoryNodes
        # for persist_memory_nodes to correctly generate the graph structure
        logger.info(f"Migration identified {len(points_to_add)} FacetPoints from aliases. Re-memorize to apply.")

    # Summary
    if verbose:
        print()
        print("=" * 70)
        print("Migration Summary")
        print("=" * 70)
        print(f"  Facets processed:         {stats.facets_processed}")
        print(f"  FacetPoints created:      {stats.points_created}")
        print(f"  Points skipped (dup):     {stats.points_skipped_duplicate}")
        print(f"  Points skipped (bad):     {stats.points_skipped_bad}")
        print(f"  Aliases retained:         {stats.aliases_retained}")
        print(f"  Aliases removed:          {stats.aliases_removed}")
        print(f"  Errors:                   {stats.errors}")
        print("=" * 70)

    return stats


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Migrate Facet.aliases to FacetPoints")
    parser.add_argument("--dry-run", action="store_true", help="Only report, don't make changes")
    parser.add_argument("--no-keep-synonyms", action="store_true", help="Don't keep any synonyms in aliases")
    parser.add_argument("--max-synonyms", type=int, default=3, help="Max synonyms to keep per Facet")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")

    await migrate_aliases_to_facet_points(
        dry_run=args.dry_run,
        keep_synonyms=not args.no_keep_synonyms,
        max_synonyms_to_keep=args.max_synonyms,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    asyncio.run(main())
