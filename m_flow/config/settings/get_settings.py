# ---------------------------------------------------------------------------
# m_flow.config.settings.get_settings
#
# Build the full settings payload consumed by the management UI.
# ---------------------------------------------------------------------------
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from m_flow.adapters.vector import get_vectordb_config
from m_flow.llm import get_llm_config


# ── helper types ──────────────────────────────────────────────────────


class _Choice(BaseModel):
    """A value/label pair surfaced in drop-downs."""

    value: str
    label: str


class SupportedProvider(str, Enum):
    """LLM providers known to the platform."""

    openai = "openai"
    anthropic = "anthropic"
    gemini = "gemini"
    mistral = "mistral"
    ollama = "ollama"
    bedrock = "bedrock"
    minimax = "minimax"
    atlascloud = "atlascloud"
    custom = "custom"


class _LLMSettings(BaseModel):
    """Shape of the ``llm`` section in the settings response."""

    api_key: str
    model: str
    provider: str
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    models: dict[str, list[_Choice]]
    providers: list[_Choice]


class _VectorDBSettings(BaseModel):
    """Shape of the ``vector_db`` section in the settings response."""

    api_key: str
    url: str
    provider: str
    providers: list[_Choice]


class SettingsDict(BaseModel):
    """Top-level response returned by :func:`get_settings`."""

    llm: _LLMSettings
    vector_db: _VectorDBSettings


# ── static catalogues ─────────────────────────────────────────────────

_VECTOR_PROVIDERS: list[dict[str, str]] = [
    {"value": "lancedb", "label": "LanceDB"},
    {"value": "pgvector", "label": "PGVector"},
]

_LLM_PROVIDERS: list[dict[str, str]] = [{"value": p.value, "label": p.value.capitalize()} for p in SupportedProvider]

_MODEL_CATALOG: dict[str, list[dict[str, str]]] = {
    # Model catalog simplified - frontend uses manual input
    # Only examples retained here for API reference
    "openai": [
        {"value": "gpt-5-nano", "label": "GPT-5 Nano (Benchmark)"},
    ],
    "anthropic": [
        {"value": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    ],
    "gemini": [
        {"value": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite"},
    ],
    "mistral": [
        {"value": "ministral-8b-2512", "label": "Ministral 8B"},
    ],
    "ollama": [
        {"value": "qwen3:8b", "label": "Qwen 3 8B"},
    ],
    "bedrock": [
        {"value": "anthropic.claude-haiku-4-5-v1:0", "label": "Claude Haiku 4.5"},
    ],
    "minimax": [
        {"value": "MiniMax-M2.7", "label": "MiniMax M2.7"},
        {"value": "MiniMax-M2.7-highspeed", "label": "MiniMax M2.7 Highspeed"},
    ],
    "atlascloud": [
        {"value": "deepseek-ai/deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    ],
    "custom": [
        {"value": "deepseek/deepseek-chat", "label": "DeepSeek Chat"},
    ],
}


# ── helpers ───────────────────────────────────────────────────────────


def _mask_key(raw: Optional[str], visible: int = 10) -> str:
    """Return a partially-masked version of *raw* (or empty string)."""
    if not raw:
        return ""
    prefix = raw[:visible]
    hidden = "*" * max(len(raw) - visible, 0)
    return f"{prefix}{hidden}"


# ── entry-point ───────────────────────────────────────────────────────


def get_settings() -> SettingsDict:
    """
    Build and return the full settings payload for the management UI.

    Sensitive keys are masked so they can be safely displayed.
    """
    llm_cfg = get_llm_config()
    vec_cfg = get_vectordb_config()

    payload = {
        "llm": {
            "provider": llm_cfg.llm_provider,
            "model": llm_cfg.llm_model,
            "endpoint": llm_cfg.llm_endpoint,
            "api_version": llm_cfg.llm_api_version,
            "api_key": _mask_key(llm_cfg.llm_api_key),
            "providers": _LLM_PROVIDERS,
            "models": _MODEL_CATALOG,
        },
        "vector_db": {
            "provider": vec_cfg.vector_db_provider,
            "url": vec_cfg.vector_db_url,
            "api_key": _mask_key(vec_cfg.vector_db_key),
            "providers": _VECTOR_PROVIDERS,
        },
    }

    return SettingsDict.model_validate(payload)
