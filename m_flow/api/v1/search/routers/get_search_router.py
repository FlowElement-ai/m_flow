"""
Search Router

REST API endpoints for querying the M-flow knowledge graph,
including search history retrieval and semantic search operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field

from m_flow.api.DTO import InDTO, OutDTO
from m_flow.search.types import CombinedSearchResult, RecallMode, SearchResult

if TYPE_CHECKING:
    from m_flow.auth.models import User


# ---------------------------------------------------------------------------
# Request/Response DTOs
# ---------------------------------------------------------------------------


class SearchPayloadDTO(InDTO):
    """Search request parameters."""

    recall_mode: RecallMode = Field(default=RecallMode.TRIPLET_COMPLETION)
    datasets: list[str] | None = Field(default=None)
    dataset_ids: list[UUID] | None = Field(default=None, examples=[[]])
    query: str = Field(default="What is in the document?")
    system_prompt: str | None = Field(
        default="Answer the question using the provided context. Be as brief as possible."
    )
    node_name: list[str] | None = Field(default=None, examples=[[]])
    top_k: int | None = Field(default=10)
    only_context: bool = Field(default=False)
    use_combined_context: bool = Field(default=False)

    # Advanced search parameters
    wide_search_top_k: int | None = Field(
        default=None,
        description="Number of candidates for wide search phase (default: 100 for most modes)",
    )
    triplet_distance_penalty: float | None = Field(
        default=None,
        description="Distance penalty for triplet-based search (default: 3.5)",
    )
    verbose: bool = Field(
        default=False,
        description="If True, return detailed results including graph representation",
    )

    # Episodic retrieval parameters (Phase 0.4)
    enable_hybrid_search: bool | None = Field(
        default=None,
        description="Enable hybrid search combining vector and keyword matching (default: True for EPISODIC)",
    )
    enable_time_bonus: bool | None = Field(
        default=None,
        description="Enable time-based relevance bonus for recent documents (default: True)",
    )
    edge_miss_cost: float | None = Field(
        default=None,
        description="Cost penalty for missing edges in graph traversal (default: 0.9)",
    )
    hop_cost: float | None = Field(
        default=None,
        description="Cost per hop in graph traversal (default: 0.05)",
    )
    full_number_match_bonus: float | None = Field(
        default=None,
        description="Bonus for exact number matches (default: 0.12)",
    )
    enable_adaptive_weights: bool | None = Field(
        default=None,
        description="Enable adaptive scoring weights based on query characteristics (default: True)",
    )
    # Episodic output control
    display_mode: str | None = Field(
        default=None,
        description="Output display mode: 'summary' (Episode summaries only) or 'detail' (include Facet/Entity details)",
    )
    max_facets_per_episode: int | None = Field(
        default=None,
        description="Maximum facets returned per episode (default: 4)",
    )
    max_points_per_facet: int | None = Field(
        default=None,
        description="Maximum detail points per facet (default: 8)",
    )
    # Collection control (TRIPLET / EPISODIC)
    collections: list[str] | None = Field(
        default=None,
        description="Vector collections to search. Available: Episode_summary, Entity_name, "
        "Concept_name, Facet_search_text, Facet_anchor_text, FacetPoint_search_text, "
        "Entity_canonical_name, RelationType_relationship_name. "
        "Default (TRIPLET): Episode_summary + Entity_name + Concept_name + "
        "RelationType_relationship_name (auto-included).",
        examples=[["Episode_summary", "Entity_name", "Facet_search_text"]],
    )

    # Coreference resolution parameters
    coref_enabled: bool | None = Field(
        default=None,
        description="Enable coreference resolution preprocessing (uses config default if None)",
    )
    coref_session_id: str | None = Field(
        default=None,
        max_length=128,
        description="Session ID for streaming coreference context (auto-generated if None)",
    )
    coref_new_turn: bool = Field(
        default=True,
        description="Treat as new conversation turn (resets partial context if enabled)",
    )


class _HistoryEntry(OutDTO):
    """Single search history record."""

    id: UUID
    text: str
    user: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Simplified query DTO (issue #112)
#
# Wraps the in-process `m_flow.api.v1.search.search.query()` helper so that
# remote callers (the MCP server in API mode, third-party clients) can use
# the same simplified question/mode/top_k contract without having to mint
# a full SearchPayloadDTO.
# ---------------------------------------------------------------------------


class QueryPayloadDTO(InDTO):
    """Simplified query request — natural-language question + retrieval mode."""

    question: str = Field(..., description="Natural-language question.")
    datasets: list[str] | None = Field(
        default=None,
        description="Restrict the query to these dataset names. Omit to search all visible datasets.",
    )
    mode: str = Field(
        default="episodic",
        description="Retrieval mode: episodic | triplet | chunks | procedural | cypher.",
    )
    top_k: int = Field(default=10, ge=1, le=100, description="Maximum number of results.")


class QueryResponseDTO(OutDTO):
    """Simplified query response — mirrors `search.QueryResult.to_dict()`."""

    answer: str | None = Field(
        default=None,
        description="LLM-generated answer (populated only in triplet mode).",
    )
    context: list | dict = Field(
        default_factory=list,
        description="Retrieved context (list for episodic/chunks/procedural, dict for triplet).",
    )
    datasets: list[str] = Field(
        default_factory=list,
        description="Source dataset names that contributed to the result.",
    )


# ---------------------------------------------------------------------------
# Telemetry Helper
# ---------------------------------------------------------------------------


def _emit_search_telemetry(event: str, user_id: UUID, **props) -> None:
    """Send telemetry for search API invocations."""
    from m_flow import __version__ as mflow_ver
    from m_flow.shared.utils import send_telemetry

    send_telemetry(
        event,
        user_id,
        additional_properties={"m_flow_version": mflow_ver, **props},
    )


# ---------------------------------------------------------------------------
# Authentication Dependency
# ---------------------------------------------------------------------------


def _auth_dep():
    """Return the authentication dependency."""
    from m_flow.auth.methods import get_authenticated_user

    return get_authenticated_user


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------


def get_search_router() -> APIRouter:
    """
    Build and return the search API router.

    Endpoints:
        GET /  - Retrieve user's search history
        POST / - Execute a knowledge graph search
    """
    router = APIRouter()

    @router.get("", response_model=list[_HistoryEntry])
    async def retrieve_search_history(user: "User" = Depends(_auth_dep())):
        """
        Fetch search history for the authenticated user.

        Returns a list of prior search queries with timestamps,
        ordered by recency (newest first).
        """
        _emit_search_telemetry("Search API Endpoint Invoked", user.id, endpoint="GET /v1/search")

        from m_flow.search.operations import get_history

        try:
            return await get_history(user.id, limit=0)
        except Exception as err:
            return JSONResponse(status_code=500, content={"error": str(err)})

    @router.post("", response_model=list[SearchResult] | CombinedSearchResult | list)
    async def execute_search(
        payload: SearchPayloadDTO,
        user: "User" = Depends(_auth_dep()),
    ):
        """
        Perform semantic search across the knowledge graph.

        Supports multiple recall modes and can be scoped to specific
        datasets. Permission-denied errors return an empty list.

        Optionally applies coreference resolution to resolve pronouns
        in the query using session context.

        Args:
            payload: Search parameters including query, mode, and filters.
            user: Authenticated user context.

        Returns:
            Search results (nodes/relationships) or combined context.
        """
        # Apply coreference preprocessing if enabled
        effective_query = payload.query
        coref_applied = False
        coref_replacements = []

        try:
            from m_flow.preprocessing.coreference import preprocess_query_with_coref_async

            coref_result = await preprocess_query_with_coref_async(
                query=payload.query,
                user_id=str(user.id),
                session_id=payload.coref_session_id,
                enabled=payload.coref_enabled,
                new_turn=payload.coref_new_turn,
            )
            if coref_result.resolved_query != coref_result.original_query:
                effective_query = coref_result.resolved_query
                coref_applied = True
                coref_replacements = coref_result.replacements
        except ImportError:
            pass
        except Exception as coref_err:
            from m_flow.shared.logging_utils import get_logger

            get_logger().warning(f"Coreference preprocessing failed: {coref_err}")

        ds_ids_str = [str(d) for d in payload.dataset_ids or []]
        _emit_search_telemetry(
            "Search API Endpoint Invoked",
            user.id,
            endpoint="POST /v1/search",
            recall_mode=str(payload.recall_mode),
            datasets=payload.datasets,
            dataset_ids=ds_ids_str,
            query=payload.query,
            effective_query=effective_query if coref_applied else None,
            coref_applied=coref_applied,
            coref_replacements_count=len(coref_replacements),
            system_prompt=payload.system_prompt,
            node_name=payload.node_name,
            top_k=payload.top_k,
            only_context=payload.only_context,
            use_combined_context=payload.use_combined_context,
            wide_search_top_k=payload.wide_search_top_k,
            triplet_distance_penalty=payload.triplet_distance_penalty,
            verbose=payload.verbose,
            enable_hybrid_search=payload.enable_hybrid_search,
            enable_time_bonus=payload.enable_time_bonus,
            edge_miss_cost=payload.edge_miss_cost,
            hop_cost=payload.hop_cost,
            full_number_match_bonus=payload.full_number_match_bonus,
            enable_adaptive_weights=payload.enable_adaptive_weights,
            display_mode=payload.display_mode,
            max_facets_per_episode=payload.max_facets_per_episode,
            max_points_per_facet=payload.max_points_per_facet,
        )

        from m_flow.api.v1.search import search as search_impl
        from m_flow.auth.exceptions.exceptions import PermissionDeniedError

        try:
            results = await search_impl(
                query_text=effective_query,
                query_type=payload.recall_mode,
                user=user,
                datasets=payload.datasets,
                dataset_ids=payload.dataset_ids,
                system_prompt=payload.system_prompt,
                node_name=payload.node_name,
                top_k=payload.top_k,
                only_context=payload.only_context,
                use_combined_context=payload.use_combined_context,
                wide_search_top_k=payload.wide_search_top_k,
                triplet_distance_penalty=payload.triplet_distance_penalty,
                verbose=payload.verbose,
                enable_hybrid_search=payload.enable_hybrid_search,
                enable_time_bonus=payload.enable_time_bonus,
                edge_miss_cost=payload.edge_miss_cost,
                hop_cost=payload.hop_cost,
                full_number_match_bonus=payload.full_number_match_bonus,
                enable_adaptive_weights=payload.enable_adaptive_weights,
                display_mode=payload.display_mode,
                max_facets_per_episode=payload.max_facets_per_episode,
                max_points_per_facet=payload.max_points_per_facet,
                collections=payload.collections,
            )
            return jsonable_encoder(results)
        except PermissionDeniedError:
            return []
        except Exception as err:
            return JSONResponse(status_code=409, content={"error": str(err)})

    @router.post("/query", response_model=QueryResponseDTO)
    async def execute_query(
        payload: QueryPayloadDTO,
        user: "User" = Depends(_auth_dep()),
    ):
        """
        Simplified natural-language query (issue #112).

        Wraps the in-process `m_flow.api.v1.search.search.query()` helper so
        that the MCP server (and any other remote consumer) can reach the
        same simplified contract over HTTP. The local function runs under
        the authenticated user's session context, so dataset visibility and
        permission filtering match the existing `/api/v1/search` semantics.
        """
        from m_flow.api.v1.search.search import query as query_impl
        from m_flow.auth.exceptions.exceptions import PermissionDeniedError

        _emit_search_telemetry(
            "Query API Endpoint Invoked",
            user.id,
            endpoint="POST /v1/search/query",
            mode=payload.mode,
            datasets=payload.datasets,
            top_k=payload.top_k,
        )

        # The simplified query helper resolves the seed user internally for
        # local callers; here we explicitly bind the request user so the
        # remote path enforces the same access-control boundary as the
        # existing /api/v1/search endpoint.
        from m_flow.context_global_variables import set_session_user_context_variable

        set_session_user_context_variable(user)

        try:
            result = await query_impl(
                question=payload.question,
                datasets=payload.datasets,
                mode=payload.mode,
                top_k=payload.top_k,
            )
            return jsonable_encoder(result.to_dict())
        except PermissionDeniedError:
            return JSONResponse(
                status_code=403,
                content={"error": "Permission denied for one or more requested datasets."},
            )
        except Exception as err:
            return JSONResponse(status_code=409, content={"error": str(err)})

    return router
