"""
Service readiness and liveness probes for the m_flow API layer.

Provides a unified health-check surface that inspects every backing
service (databases, LLM, embeddings, file storage) and returns an
aggregate verdict.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from enum import Enum
from io import BytesIO
from typing import Any, Callable, Coroutine, Dict, List, NamedTuple

from pydantic import BaseModel, Field

from m_flow.shared.logging_utils import get_logger
from m_flow.version import get_version

_log = get_logger()

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    """Tri-state verdict for individual components and the aggregate."""

    UP = "up"
    WARN = "warn"
    DOWN = "down"
    # Backwards-compatible aliases used by api/client.py
    HEALTHY = "up"
    DEGRADED = "warn"
    UNHEALTHY = "down"


class ProbeResult(BaseModel):
    """Outcome of a single backing-service probe."""

    verdict: Verdict
    backend: str = "unknown"
    latency_ms: int = 0
    note: str = ""


class SystemHealth(BaseModel):
    """Top-level response returned by the ``/health`` endpoint."""

    verdict: Verdict
    checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    build: str = Field(default_factory=get_version)
    alive_seconds: int = 0

    # Backwards-compatible property aliases used by api/client.py
    @property
    def status(self) -> Verdict:
        return self.verdict

    @property
    def version(self) -> str:
        return self.build

    probes: Dict[str, ProbeResult] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual probes (lazy imports to avoid startup-time side-effects)
# ---------------------------------------------------------------------------


async def _probe_relational() -> ProbeResult:
    t0 = time.monotonic()
    try:
        from m_flow.adapters.relational.config import get_relational_config
        from m_flow.adapters.relational.get_db_adapter import get_db_adapter

        cfg = get_relational_config()
        eng = get_db_adapter()
        sess = eng.get_session()
        if sess is not None:
            sess.close()
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.UP, backend=cfg.db_provider, latency_ms=ms, note="session ok")
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        _log.error("relational probe failed: %s", exc, exc_info=True)
        return ProbeResult(verdict=Verdict.DOWN, latency_ms=ms, note=str(exc)[:200])


async def _probe_vector() -> ProbeResult:
    t0 = time.monotonic()
    try:
        from m_flow.adapters.vector.config import get_vectordb_config
        from m_flow.adapters.vector.get_vector_adapter import get_vector_provider

        cfg = get_vectordb_config()
        eng = get_vector_provider()

        if callable(getattr(eng, "health_check", None)):
            await eng.health_check()
        elif callable(getattr(eng, "list_tables", None)):
            eng.list_tables()

        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            verdict=Verdict.UP,
            backend=cfg.vector_db_provider,
            latency_ms=ms,
            note="index reachable",
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        _log.error("vector probe failed: %s", exc, exc_info=True)
        return ProbeResult(verdict=Verdict.DOWN, latency_ms=ms, note=str(exc)[:200])


async def _probe_graph() -> ProbeResult:
    """
    Probe graph database connectivity.

    Note: Uses a 3-second timeout to avoid blocking when the graph DB lock
    is held by a long-running operation (e.g., batch ingestion). This prevents
    the entire /health endpoint from timing out.
    """
    t0 = time.monotonic()
    try:
        from m_flow.adapters.graph.config import get_graph_config
        from m_flow.adapters.graph.get_graph_adapter import get_graph_provider

        cfg = get_graph_config()

        # Use timeout to prevent blocking on Kuzu exclusive lock
        try:
            eng = await asyncio.wait_for(get_graph_provider(), timeout=3.0)
        except asyncio.TimeoutError:
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(
                verdict=Verdict.WARN,
                backend=cfg.graph_database_provider,
                latency_ms=ms,
                note="timeout acquiring connection (likely busy with writes)",
            )

        if callable(getattr(eng, "is_empty", None)):
            try:
                await asyncio.wait_for(eng.is_empty(), timeout=2.0)
            except asyncio.TimeoutError:
                ms = int((time.monotonic() - t0) * 1000)
                return ProbeResult(
                    verdict=Verdict.WARN,
                    backend=cfg.graph_database_provider,
                    latency_ms=ms,
                    note="query timeout (database busy)",
                )

        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            verdict=Verdict.UP,
            backend=cfg.graph_database_provider,
            latency_ms=ms,
            note="schema valid",
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        _log.error("graph probe failed: %s", exc, exc_info=True)
        return ProbeResult(verdict=Verdict.DOWN, latency_ms=ms, note=str(exc)[:200])


async def _probe_storage() -> ProbeResult:
    t0 = time.monotonic()
    try:
        from m_flow.base_config import get_base_config
        from m_flow.shared.files.storage.get_file_storage import get_file_storage

        bcfg = get_base_config()
        store = get_file_storage(bcfg.data_root_directory)
        is_remote = bcfg.data_root_directory.startswith("s3://")
        backend_label = "s3" if is_remote else "local"

        sentinel = "_mflow_health_probe"
        if is_remote:
            await store.store(sentinel, BytesIO(b"ok"))
            await store.remove(sentinel)
        else:
            os.makedirs(bcfg.data_root_directory, exist_ok=True)
            full = os.path.join(bcfg.data_root_directory, sentinel)
            with open(full, "w") as fh:
                fh.write("ok")
            os.remove(full)

        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.UP, backend=backend_label, latency_ms=ms, note="read/write ok")
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.DOWN, latency_ms=ms, note=str(exc)[:200])


async def _probe_llm() -> ProbeResult:
    """
    Probe LLM provider configuration.

    Checks that API key and model are configured without making actual API calls.
    Actual LLM connectivity is validated on first real usage.
    """
    t0 = time.monotonic()
    try:
        from m_flow.llm.config import get_llm_config

        cfg = get_llm_config()
        has_key = bool(cfg.llm_api_key)
        has_model = bool(cfg.llm_model)
        ms = int((time.monotonic() - t0) * 1000)

        if has_key and has_model:
            return ProbeResult(
                verdict=Verdict.UP,
                backend=cfg.llm_provider,
                latency_ms=ms,
                note=f"configured ({cfg.llm_model})",
            )
        missing = []
        if not has_key:
            missing.append("LLM_API_KEY")
        if not has_model:
            missing.append("LLM_MODEL")
        return ProbeResult(
            verdict=Verdict.WARN,
            backend=cfg.llm_provider,
            latency_ms=ms,
            note=f"missing: {', '.join(missing)}",
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.WARN, latency_ms=ms, note=str(exc)[:200])


async def _probe_embedding() -> ProbeResult:
    """
    Probe embedding service configuration.

    Checks that embedding provider and model are configured without making actual API calls.
    """
    t0 = time.monotonic()
    try:
        from m_flow.adapters.vector.embeddings.config import get_embedding_config

        cfg = get_embedding_config()
        has_model = bool(cfg.embedding_model)
        ms = int((time.monotonic() - t0) * 1000)

        if has_model:
            return ProbeResult(
                verdict=Verdict.UP,
                backend=cfg.embedding_provider,
                latency_ms=ms,
                note=f"configured ({cfg.embedding_model})",
            )
        return ProbeResult(
            verdict=Verdict.WARN,
            backend=cfg.embedding_provider,
            latency_ms=ms,
            note="EMBEDDING_MODEL not configured",
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.WARN, latency_ms=ms, note=str(exc)[:200])


async def _probe_coreference() -> ProbeResult:
    """Probe coreference resolution module availability and session state."""
    t0 = time.monotonic()
    try:
        from m_flow.preprocessing.coreference import get_coref_config, get_coref_stats

        config = get_coref_config()
        if not config.enabled:
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(
                verdict=Verdict.UP,
                backend="disabled",
                latency_ms=ms,
                note="coreference disabled by config",
            )

        stats = get_coref_stats()
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            verdict=Verdict.UP,
            backend=f"zh+en (lang={config.language})",
            latency_ms=ms,
            note=f"sessions={stats['active_sessions']}/{stats['max_sessions']}",
        )
    except ImportError as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(
            verdict=Verdict.WARN,
            backend="not installed",
            latency_ms=ms,
            note=f"module not available: {exc!s}"[:200],
        )
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(verdict=Verdict.WARN, latency_ms=ms, note=str(exc)[:200])


# ---------------------------------------------------------------------------
# Probe registry
# ---------------------------------------------------------------------------


class _RegisteredProbe(NamedTuple):
    label: str
    fn: Callable[[], Coroutine[Any, Any, ProbeResult]]
    critical: bool


_PROBES: List[_RegisteredProbe] = [
    _RegisteredProbe("relational_db", _probe_relational, critical=True),
    _RegisteredProbe("vector_db", _probe_vector, critical=True),
    _RegisteredProbe("graph_db", _probe_graph, critical=True),
    _RegisteredProbe("file_storage", _probe_storage, critical=True),
    _RegisteredProbe("llm_provider", _probe_llm, critical=False),
    _RegisteredProbe("embedding_service", _probe_embedding, critical=False),
    _RegisteredProbe("coreference", _probe_coreference, critical=False),
]


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

_BOOT_EPOCH = time.monotonic()


async def run_health_probes(*, include_optional: bool = False) -> SystemHealth:
    """
    Execute all registered probes concurrently and return an aggregate
    :class:`SystemHealth` snapshot.

    Parameters
    ----------
    include_optional
        When *True*, non-critical probes are also executed (currently all
        probes run regardless — reserved for future filtering).

    Returns
    -------
    SystemHealth
    """
    tasks = [p.fn() for p in _PROBES]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    collected: Dict[str, ProbeResult] = {}
    for probe_def, raw in zip(_PROBES, outcomes):
        if isinstance(raw, BaseException):
            collected[probe_def.label] = ProbeResult(
                verdict=Verdict.DOWN if probe_def.critical else Verdict.WARN,
                note=f"probe raised: {raw!s}"[:200],
            )
        else:
            collected[probe_def.label] = raw

    # Derive aggregate verdict
    any_critical_down = any(collected[p.label].verdict == Verdict.DOWN for p in _PROBES if p.critical)
    any_warn = any(r.verdict == Verdict.WARN for r in collected.values())

    if any_critical_down:
        agg = Verdict.DOWN
    elif any_warn:
        agg = Verdict.WARN
    else:
        agg = Verdict.UP

    return SystemHealth(
        verdict=agg,
        alive_seconds=int(time.monotonic() - _BOOT_EPOCH),
        probes=collected,
    )


# Backwards-compatible alias expected by api/client.py
class HealthChecker:
    """Thin wrapper kept for call-site compatibility."""

    def __init__(self) -> None:
        self.start_time = time.time()

    async def get_health_status(self, detailed: bool = False) -> SystemHealth:
        return await run_health_probes(include_optional=detailed)


health_checker = HealthChecker()

# Backwards-compatible alias for api/client.py
HealthStatus = Verdict
