"""
Factory for LLM client adapters.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from m_flow.llm import get_llm_config
from m_flow.llm.exceptions import LLMAPIKeyNotSetError, UnsupportedLLMProviderError


class LLMProvider(str, Enum):
    """Supported LLM backend identifiers."""

    OPENAI = "openai"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"
    GEMINI = "gemini"
    MISTRAL = "mistral"
    BEDROCK = "bedrock"
    MINIMAX = "minimax"
    ATLASCLOUD = "atlascloud"


ATLASCLOUD_DEFAULT_ENDPOINT = "https://api.atlascloud.ai/v1"
ATLASCLOUD_DEFAULT_MODEL = "deepseek-ai/deepseek-v4-pro"


def create_llm_backend(raise_api_key_error: bool = True) -> Any:
    """
    Build the appropriate LLM adapter based on current configuration.

    Parameters
    ----------
    raise_api_key_error
        When True (default), raises :class:`LLMAPIKeyNotSetError` if the
        configured provider requires an API key that is not set.

    Returns
    -------
    LLMAdapter
        An instance of the provider-specific adapter.

    Raises
    ------
    LLMAPIKeyNotSetError
        If the key is missing and *raise_api_key_error* is True.
    UnsupportedLLMProviderError
        If the configured provider is not recognised.
    """
    cfg = get_llm_config()
    provider = LLMProvider(cfg.llm_provider)

    # Determine max tokens
    from m_flow.llm.utils import get_model_max_completion_tokens

    model_max = get_model_max_completion_tokens(cfg.llm_model)
    max_tokens = model_max if model_max else cfg.llm_max_completion_tokens
    mode = cfg.llm_instructor_mode.lower()

    def _require_key() -> None:
        if cfg.llm_api_key is None and raise_api_key_error:
            raise LLMAPIKeyNotSetError()

    if provider == LLMProvider.OPENAI:
        _require_key()
        from .openai.adapter import OpenAIAdapter

        return OpenAIAdapter(
            api_key=cfg.llm_api_key,
            endpoint=cfg.llm_endpoint,
            api_version=cfg.llm_api_version,
            model=cfg.llm_model,
            transcription_model=cfg.transcription_model,
            max_completion_tokens=max_tokens,
            instructor_mode=mode,
            streaming=cfg.llm_streaming,
            fallback_api_key=cfg.fallback_api_key,
            fallback_endpoint=cfg.fallback_endpoint,
            fallback_model=cfg.fallback_model,
        )

    if provider == LLMProvider.OLLAMA:
        _require_key()
        from .ollama.adapter import OllamaAPIAdapter

        return OllamaAPIAdapter(
            cfg.llm_endpoint,
            cfg.llm_api_key,
            cfg.llm_model,
            "Ollama",
            max_completion_tokens=max_tokens,
            instructor_mode=mode,
        )

    if provider == LLMProvider.ANTHROPIC:
        from .anthropic.adapter import AnthropicAdapter

        return AnthropicAdapter(
            max_completion_tokens=max_tokens,
            model=cfg.llm_model,
            instructor_mode=mode,
        )

    if provider == LLMProvider.CUSTOM:
        _require_key()
        from .generic_llm_api.adapter import GenericAPIAdapter

        return GenericAPIAdapter(
            cfg.llm_endpoint,
            cfg.llm_api_key,
            cfg.llm_model,
            "Custom",
            max_completion_tokens=max_tokens,
            instructor_mode=mode,
            fallback_api_key=cfg.fallback_api_key,
            fallback_endpoint=cfg.fallback_endpoint,
            fallback_model=cfg.fallback_model,
        )

    if provider == LLMProvider.ATLASCLOUD:
        _require_key()
        from .generic_llm_api.adapter import GenericAPIAdapter

        return GenericAPIAdapter(
            cfg.llm_endpoint or ATLASCLOUD_DEFAULT_ENDPOINT,
            cfg.llm_api_key,
            cfg.llm_model or ATLASCLOUD_DEFAULT_MODEL,
            "Atlas Cloud",
            max_completion_tokens=max_tokens,
            instructor_mode=mode,
            fallback_api_key=cfg.fallback_api_key,
            fallback_endpoint=cfg.fallback_endpoint,
            fallback_model=cfg.fallback_model,
        )

    if provider == LLMProvider.GEMINI:
        _require_key()
        from .gemini.adapter import GeminiAdapter

        return GeminiAdapter(
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            max_completion_tokens=max_tokens,
            endpoint=cfg.llm_endpoint,
            api_version=cfg.llm_api_version,
            instructor_mode=mode,
        )

    if provider == LLMProvider.MISTRAL:
        _require_key()
        from .mistral.adapter import MistralAdapter

        return MistralAdapter(
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            max_completion_tokens=max_tokens,
            endpoint=cfg.llm_endpoint,
            instructor_mode=mode,
        )

    if provider == LLMProvider.BEDROCK:
        from .bedrock.adapter import BedrockAdapter

        return BedrockAdapter(
            model=cfg.llm_model,
            api_key=cfg.llm_api_key,
            max_completion_tokens=max_tokens,
            streaming=cfg.llm_streaming,
            instructor_mode=mode,
        )

    if provider == LLMProvider.MINIMAX:
        _require_key()
        from .minimax.adapter import MiniMaxAdapter

        return MiniMaxAdapter(
            max_completion_tokens=max_tokens,
            model=cfg.llm_model,
            instructor_mode=mode,
        )

    raise UnsupportedLLMProviderError(provider)
