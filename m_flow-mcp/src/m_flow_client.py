"""
M-Flow Client — Dual-mode interface for the M-Flow knowledge-graph platform.

Supports two operating modes:
  * **Direct** (default) — imports m_flow and invokes functions in-process.
  * **Remote** — sends HTTP requests to a running M-Flow FastAPI service.
"""

import sys
import json
from typing import Optional, Any, List, Dict
from uuid import UUID
from contextlib import redirect_stdout

import httpx
from m_flow.shared.logging_utils import get_logger

_log = get_logger()


class MflowClient:
    """Dual-mode client that talks to M-Flow either locally or over HTTP.

    Args:
        server_url: Root URL of the M-Flow API (e.g. ``http://localhost:8000``).
                    When *None* the client falls back to direct in-process calls.
        auth_token: Optional bearer token used for authenticated API endpoints.
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        auth_token: Optional[str] = None,
    ) -> None:
        self._base_url = server_url.rstrip("/") if server_url else None
        self._token = auth_token
        self._remote = server_url is not None

        if self._remote:
            _log.info("M-Flow client operating in remote mode → %s", self._base_url)
            self._http = httpx.AsyncClient(timeout=300.0)
        else:
            _log.info("M-Flow client operating in local/direct mode")
            import m_flow as _mf

            self._engine = _mf

    def _auth_headers(self, include_json_content_type: bool = True) -> Dict[str, str]:
        """Build common request headers including optional authorization."""
        hdrs: Dict[str, str] = {}
        if include_json_content_type:
            hdrs["Content-Type"] = "application/json"
        if self._token:
            hdrs["Authorization"] = f"Bearer {self._token}"
        return hdrs

    async def add(
        self,
        data: Any,
        dataset_name: str = "main_dataset",
        graph_scope: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Ingest raw data into M-Flow for later memorization.

        Args:
            data: Arbitrary payload (text, path, etc.).
            dataset_name: Target dataset identifier.
            graph_scope: Optional list of node tags for graph partitioning.

        Returns:
            Outcome dictionary with at least a ``status`` key.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/add"
            upload = {"data": ("data.txt", str(data), "text/plain")}
            fields: Dict[str, str] = {"datasetName": dataset_name}
            if graph_scope is not None:
                fields["graph_scope"] = json.dumps(graph_scope)

            resp = await self._http.post(
                url,
                files=upload,
                data=fields,
                headers={"Authorization": f"Bearer {self._token}"} if self._token else {},
            )
            resp.raise_for_status()
            return resp.json()

        with redirect_stdout(sys.stderr):
            await self._engine.add(data, dataset_name=dataset_name, graph_scope=graph_scope)
            return {"status": "success", "message": "Data ingested into M-Flow"}

    async def memorize(
        self,
        datasets: Optional[List[str]] = None,
        enable_content_routing: Optional[bool] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build or update the knowledge graph from previously added data.

        Args:
            datasets: Restrict processing to these dataset names.
            enable_content_routing: Toggle sentence-level routing.
            content_type: Either ``'text'`` or ``'dialog'``.

        Returns:
            Outcome dictionary.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/memorize"
            body: Dict[str, Any] = {
                "datasets": datasets or ["main_dataset"],
                "run_in_background": False,
            }
            if enable_content_routing is not None:
                body["enable_content_routing"] = enable_content_routing
            if content_type:
                body["content_type"] = content_type

            resp = await self._http.post(url, json=body, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

        with redirect_stdout(sys.stderr):
            opts: Dict[str, Any] = {}
            if datasets:
                opts["datasets"] = datasets
            if enable_content_routing is not None:
                opts["enable_content_routing"] = enable_content_routing
            if content_type:
                opts["content_type"] = content_type

            await self._engine.memorize(**opts)
            return {"status": "success", "message": "Knowledge graph updated"}

    async def search(
        self,
        query_text: str,
        query_type: str,
        datasets: Optional[List[str]] = None,
        system_prompt: Optional[str] = None,
        top_k: int = 5,
        enable_hybrid_search: Optional[bool] = None,
    ) -> Any:
        """Query the M-Flow knowledge graph.

        Args:
            query_text: Natural-language search string.
            query_type: Recall mode — one of CHUNKS_LEXICAL, TRIPLET_COMPLETION,
                        CYPHER, EPISODIC, PROCEDURAL.
            datasets: Limit search to specific datasets.
            system_prompt: System-level instruction for completion modes.
            top_k: Maximum results to return.
            enable_hybrid_search: Combine vector + lexical in EPISODIC mode.

        Returns:
            Search results in the format dictated by *query_type*.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/search"
            body: Dict[str, Any] = {
                "query": query_text,
                "recall_mode": query_type.upper(),
                "top_k": top_k,
            }
            if datasets:
                body["datasets"] = datasets
            if system_prompt:
                body["system_prompt"] = system_prompt
            if enable_hybrid_search is not None:
                body["enable_hybrid_search"] = enable_hybrid_search

            resp = await self._http.post(url, json=body, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

        from m_flow.search.types import RecallMode

        with redirect_stdout(sys.stderr):
            params: Dict[str, Any] = {
                "query_type": RecallMode[query_type.upper()],
                "query_text": query_text,
                "top_k": top_k,
            }
            if datasets:
                params["datasets"] = datasets
            if system_prompt:
                params["system_prompt"] = system_prompt
            if enable_hybrid_search is not None:
                params["enable_hybrid_search"] = enable_hybrid_search

            return await self._engine.search(**params)

    async def delete(
        self,
        data_id: UUID,
        dataset_id: UUID,
        mode: str = "soft",
    ) -> Dict[str, Any]:
        """Remove a data record from M-Flow.

        Args:
            data_id: Unique identifier of the data item.
            dataset_id: Owning dataset identifier.
            mode: ``"soft"`` (mark deleted) or ``"hard"`` (permanent).

        Returns:
            Deletion result.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/delete"
            qparams = {"data_id": str(data_id), "dataset_id": str(dataset_id), "mode": mode}
            resp = await self._http.delete(url, params=qparams, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

        from m_flow.auth.methods import get_seed_user

        with redirect_stdout(sys.stderr):
            current_user = await get_seed_user()
            return await self._engine.delete(data_id=data_id, dataset_id=dataset_id, mode=mode, user=current_user)

    async def prune_data(self) -> Dict[str, Any]:
        """Wipe stored file artefacts from the knowledge graph backend.

        In **remote** mode this calls the existing
        ``POST /api/v1/prune/data`` admin endpoint, which requires:

        * superuser authentication (via ``auth_token``);
        * the server-side ``MFLOW_ENABLE_PRUNE_API=true`` master switch;
        * a confirmation string the client supplies on the caller's behalf;
        * no active pipelines (HTTP 409 if any are running);
        * a cooldown period since the previous prune (HTTP 429 otherwise).

        Returns:
            Prune outcome dictionary.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response from the API.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/prune/data"
            resp = await self._http.post(
                url,
                json={"confirm": "DELETE_FILES"},
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()

        with redirect_stdout(sys.stderr):
            await self._engine.prune.prune_data()
            return {"status": "success", "message": "All user data removed"}

    async def prune_system(
        self,
        graph: bool = True,
        vector: bool = True,
        metadata: bool = True,
        cache: bool = True,
    ) -> Dict[str, Any]:
        """Wipe system-level stores (graph DB, vectors, metadata, cache).

        In **remote** mode this calls ``POST /api/v1/prune/system`` with the
        same security constraints as :meth:`prune_data` (superuser,
        ``MFLOW_ENABLE_PRUNE_API``, confirmation string, no-active-pipeline,
        cooldown).

        Args:
            graph: Clear graph database.
            vector: Clear vector indices.
            metadata: Clear relational metadata.
            cache: Clear ephemeral cache.

        Returns:
            Prune outcome dictionary.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response from the API.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/prune/system"
            resp = await self._http.post(
                url,
                json={
                    "confirm": "DELETE_SYSTEM",
                    "graph": graph,
                    "vector": vector,
                    "metadata": metadata,
                    "cache": cache,
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return resp.json()

        with redirect_stdout(sys.stderr):
            await self._engine.prune.prune_system(graph=graph, vector=vector, metadata=metadata, cache=cache)
            return {"status": "success", "message": "System stores cleared"}

    async def get_workflow_status(self, dataset_ids: List[UUID], workflow_name: str) -> Dict[str, str]:
        """Return latest pipeline status for one or more datasets.

        Args:
            dataset_ids: Dataset UUIDs the pipeline operates on.
            workflow_name: Registered pipeline identifier.

        Returns:
            Mapping of dataset UUID strings to status strings.
        """
        if self._remote:
            params = [("dataset", str(dataset_id)) for dataset_id in dataset_ids]
            url = f"{self._base_url}/api/v1/datasets/status"
            resp = await self._http.get(url, params=params, headers=self._auth_headers())
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}

        from m_flow.pipeline.operations.get_workflow_status import get_workflow_status

        with redirect_stdout(sys.stderr):
            return await get_workflow_status(dataset_ids, workflow_name)

    async def list_datasets(self) -> List[Dict[str, Any]]:
        """Enumerate all datasets visible to the current user.

        Returns:
            List of dicts with ``id``, ``name``, and ``created_at`` fields.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/datasets"
            resp = await self._http.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

        from m_flow.auth.methods import get_seed_user
        from m_flow.data.methods import get_datasets

        with redirect_stdout(sys.stderr):
            current_user = await get_seed_user()
            rows = await get_datasets(current_user.id)
            return [{"id": str(r.id), "name": r.name, "created_at": str(r.created_at)} for r in rows]

    async def learn(
        self,
        datasets: Optional[List[str]] = None,
        episode_ids: Optional[List[str]] = None,
        run_in_background: bool = False,
    ) -> Dict[str, Any]:
        """Derive procedural memory from existing episodic data.

        Args:
            datasets: Dataset names to learn from.
            episode_ids: Specific episode UUIDs.
            run_in_background: Offload to a background worker.

        Returns:
            Learning outcome.

        Raises:
            NotImplementedError: In remote mode, only when ``episode_ids`` are supplied.
        """
        if self._remote:
            if episode_ids:
                raise NotImplementedError("Remote learn does not yet support episode_ids")

            dataset_ids: List[Optional[str]]
            if datasets:
                available = await self.list_datasets()
                name_to_id = {
                    str(ds.get("name")): str(ds.get("id")) for ds in available if ds.get("name") and ds.get("id")
                }
                missing = [name for name in datasets if name not in name_to_id]
                if missing:
                    raise ValueError(f"Unknown dataset name(s): {', '.join(missing)}")
                dataset_ids = [name_to_id[name] for name in datasets]
            else:
                dataset_ids = [None]

            url = f"{self._base_url}/api/v1/procedural/extract-from-episodic"
            all_results: List[Dict[str, Any]] = []
            for dataset_id in dataset_ids:
                body: Dict[str, Any] = {
                    "dataset_id": dataset_id,
                    "limit": 100,
                    "force_reprocess": False,
                    "run_in_background": run_in_background,
                }
                resp = await self._http.post(url, json=body, headers=self._auth_headers())
                resp.raise_for_status()
                all_results.append(resp.json())

            if len(all_results) == 1:
                return all_results[0]
            return {"status": "success", "datasets_processed": len(all_results), "results": all_results}

        from m_flow import learn as _learn

        with redirect_stdout(sys.stderr):
            outcome = await _learn(
                datasets=datasets,
                episode_ids=episode_ids,
                run_in_background=run_in_background,
            )
            return {"status": "success", "result": outcome}

    async def update(
        self,
        data_id: str,
        data: str,
        dataset_id: str,
    ) -> Dict[str, Any]:
        """Replace content for an existing data record.

        Args:
            data_id: UUID string of the record to update.
            data: New text content.
            dataset_id: UUID string of the owning dataset.

        Returns:
            Update outcome.
        """
        from uuid import UUID as _UUID

        if self._remote:
            import io

            url = f"{self._base_url}/api/v1/update"
            payload = [("data", ("content.txt", io.BytesIO(data.encode("utf-8")), "text/plain"))]
            resp = await self._http.patch(
                url,
                params={"data_id": data_id, "dataset_id": dataset_id},
                files=payload,
                headers=self._auth_headers(include_json_content_type=False),
            )
            resp.raise_for_status()
            return resp.json()

        from m_flow import update as _update

        with redirect_stdout(sys.stderr):
            result = await _update(data_id=_UUID(data_id), data=data, dataset_id=_UUID(dataset_id))
            return {"status": "success", "result": str(result)}

    async def ingest(
        self,
        data: str,
        dataset_name: str = "main_dataset",
        skip_memorize: bool = False,
        enable_content_routing: Optional[bool] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Single-call convenience: add data then optionally memorize.

        Args:
            data: Text content to ingest.
            dataset_name: Target dataset.
            skip_memorize: If *True*, only add without building the graph.
            enable_content_routing: Toggle sentence routing.
            content_type: ``'text'`` or ``'dialog'``.

        Returns:
            Ingestion outcome.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/ingest"
            body: Dict[str, Any] = {
                "content": data,
                "dataset_name": dataset_name,
                "skip_memorize": skip_memorize,
            }
            if enable_content_routing is not None:
                body["enable_content_routing"] = enable_content_routing
            if content_type:
                body["content_type"] = content_type

            resp = await self._http.post(url, json=body, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

        from m_flow import ingest as _ingest

        call_args: Dict[str, Any] = {
            "data": data,
            "dataset_name": dataset_name,
            "skip_memorize": skip_memorize,
        }
        if enable_content_routing is not None:
            call_args["enable_content_routing"] = enable_content_routing
        if content_type:
            call_args["content_type"] = content_type

        with redirect_stdout(sys.stderr):
            result = await _ingest(**call_args)
            return {"status": "success", "result": str(result)}

    async def query(
        self,
        question: str,
        datasets: Optional[List[str]] = None,
        mode: str = "episodic",
        top_k: int = 10,
    ) -> str:
        """High-level natural-language question interface.

        In **remote** mode this calls ``POST /api/v1/search/query``, which
        wraps the same in-process ``m_flow.api.v1.search.search.query()``
        helper used by direct mode under the authenticated user's session.

        Args:
            question: Free-form question.
            datasets: Restrict to these datasets.
            mode: Retrieval strategy (episodic / triplet / chunks / procedural / cypher).
            top_k: Result count cap.

        Returns:
            Answer string. For triplet mode the LLM-generated ``answer`` is
            returned; otherwise the retrieved ``context`` is rendered as a
            string. The contract matches the historical direct-mode return
            shape so existing callers (the MCP ``query`` tool) need no
            changes.

        Raises:
            httpx.HTTPStatusError: For any non-2xx response from the API.
        """
        if self._remote:
            url = f"{self._base_url}/api/v1/search/query"
            payload: Dict[str, Any] = {"question": question, "mode": mode, "top_k": top_k}
            if datasets is not None:
                payload["datasets"] = datasets
            resp = await self._http.post(url, json=payload, headers=self._auth_headers())
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                # Mirror direct-mode collapse: prefer LLM answer, otherwise
                # surface the retrieved context as a string for downstream
                # MCP TextContent rendering.
                if data.get("answer"):
                    return str(data["answer"])
                if "context" in data:
                    return str(data["context"])
            return str(data)

        from m_flow import query as _query

        with redirect_stdout(sys.stderr):
            answer = await _query(question=question, datasets=datasets, mode=mode, top_k=top_k)
            if hasattr(answer, "answer") and answer.answer:
                return answer.answer
            if hasattr(answer, "context"):
                return str(answer.context)
            return str(answer)

    async def close(self) -> None:
        """Shut down the underlying HTTP transport (remote mode only)."""
        if self._remote and hasattr(self, "_http"):
            await self._http.aclose()
