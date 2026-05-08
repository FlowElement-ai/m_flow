"""
LLM (Large Language Model) provider configuration.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from m_flow.config.env_compat import MflowSettings

try:
    from baml_py import ClientRegistry
except ImportError:
    ClientRegistry = None  # type: ignore[misc]

# Fields that may contain spurious quotes from env files
_QUOTE_STRIP_FIELDS: List[str] = [
    "llm_api_key",
    "llm_endpoint",
    "llm_api_version",
    "baml_llm_api_key",
    "baml_llm_endpoint",
    "baml_llm_api_version",
    "fallback_api_key",
    "fallback_endpoint",
    "fallback_model",
    "llm_provider",
    "llm_model",
    "baml_llm_provider",
    "baml_llm_model",
]


class LLMConfig(MflowSettings):
    """
    Configuration for LLM providers, rate limits, and structured output.

    Loads from environment variables and ``.env`` file.
    """

    # Structured output framework
    backends: str = "instructor"
    llm_instructor_mode: str = ""

    # Primary LLM settings
    # Default: gpt-5-nano - OpenAI's fastest & cheapest model for knowledge graph ingestion
    # Benchmark recommendation for memorize tasks
    llm_provider: str = "openai"
    llm_model: str = "gpt-5-nano"
    llm_endpoint: str = ""
    llm_api_key: Optional[str] = None
    llm_api_version: Optional[str] = None
    llm_temperature: float = 0.0
    llm_streaming: bool = False
    llm_max_completion_tokens: int = 16384

    # BAML settings
    baml_llm_provider: str = "openai"
    baml_llm_model: str = "gpt-5-nano"
    baml_llm_endpoint: str = ""
    baml_llm_api_key: Optional[str] = None
    baml_llm_temperature: float = 0.0
    baml_llm_api_version: str = ""

    # Audio transcription
    transcription_model: str = "whisper-1"

    # Prompt templates
    graph_prompt_path: str = "knowledge_graph_extractor.txt"

    # LLM rate limits
    llm_rate_limit_enabled: bool = False
    llm_rate_limit_requests: int = 60
    llm_rate_limit_interval: int = 60

    # Embedding rate limits
    embedding_rate_limit_enabled: bool = False
    embedding_rate_limit_requests: int = 60
    embedding_rate_limit_interval: int = 60

    # Fallback LLM
    fallback_api_key: str = ""
    fallback_endpoint: str = ""
    fallback_model: str = ""

    # Runtime BAML registry
    baml_registry: Optional[Any] = None

    model_config = SettingsConfigDict(env_prefix="MFLOW_", env_file=".env", extra="allow")

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @model_validator(mode="after")
    def _strip_quotes(self) -> "LLMConfig":
        """Remove surrounding quotes from string fields (Docker env-file artefact)."""
        for fname in _QUOTE_STRIP_FIELDS:
            if fname not in self.__class__.model_fields:
                continue
            val = getattr(self, fname, None)
            if isinstance(val, str) and len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                setattr(self, fname, val[1:-1])
        return self

    @model_validator(mode="after")
    def _validate_ollama_env(self) -> "LLMConfig":
        """Ensure Ollama-related env vars are set consistently."""
        if self.llm_provider != "ollama":
            return self

        llm_vars = ["LLM_MODEL", "LLM_ENDPOINT", "LLM_API_KEY"]
        embed_vars = [
            "EMBEDDING_PROVIDER",
            "EMBEDDING_MODEL",
            "EMBEDDING_DIMENSIONS",
            "HUGGINGFACE_TOKENIZER",
        ]

        _check_env_group(llm_vars, "LLM")
        _check_env_group(embed_vars, "embedding")

        return self

    def model_post_init(self, context: Any, /) -> None:
        """Initialise BAML registry when selected."""
        if self.backends.lower() != "baml":
            return

        if ClientRegistry is None:
            raise ImportError("BAML selected but not installed. Run: pip install 'm_flow[baml]'")

        self.baml_registry = ClientRegistry()
        opts = {
            "model": self.baml_llm_model,
            "temperature": self.baml_llm_temperature,
            "api_key": self.baml_llm_api_key,
            "base_url": self.baml_llm_endpoint,
            "api_version": self.baml_llm_api_version,
        }
        opts = {k: v for k, v in opts.items() if v not in (None, "")}
        self.baml_registry.add_llm_client(
            name=self.baml_llm_provider,
            provider=self.baml_llm_provider,
            options=opts,
        )
        self.baml_registry.set_primary(self.baml_llm_provider)

    # -------------------------------------------------------------------------
    # Serialisation
    # -------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Export config as a plain dictionary."""
        return {
            "llm_instructor_mode": self.llm_instructor_mode.lower(),
            "provider": self.llm_provider,
            "model": self.llm_model,
            "endpoint": self.llm_endpoint,
            "api_key": self.llm_api_key,
            "api_version": self.llm_api_version,
            "temperature": self.llm_temperature,
            "streaming": self.llm_streaming,
            "max_completion_tokens": self.llm_max_completion_tokens,
            "transcription_model": self.transcription_model,
            "graph_prompt_path": self.graph_prompt_path,
            "rate_limit_enabled": self.llm_rate_limit_enabled,
            "rate_limit_requests": self.llm_rate_limit_requests,
            "rate_limit_interval": self.llm_rate_limit_interval,
            "embedding_rate_limit_enabled": self.embedding_rate_limit_enabled,
            "embedding_rate_limit_requests": self.embedding_rate_limit_requests,
            "embedding_rate_limit_interval": self.embedding_rate_limit_interval,
            "fallback_api_key": self.fallback_api_key,
            "fallback_endpoint": self.fallback_endpoint,
            "fallback_model": self.fallback_model,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_is_set(name: str) -> bool:
    val = os.environ.get(name)
    return val is not None and val.strip() != ""


def _check_env_group(names: List[str], label: str) -> None:
    flags = {n: _env_is_set(n) for n in names}
    if any(flags.values()) and not all(flags.values()):
        missing = [n for n, ok in flags.items() if not ok]
        raise ValueError(f"Partial {label} environment: set all of {names} or none. Missing: {missing}")


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_llm_config() -> LLMConfig:
    """Return the cached LLM configuration instance."""
    return LLMConfig()
