"""
Embedding model configuration.

Settings for vector embedding generation including provider,
model selection, dimensions, and API credentials.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings
from m_flow.config.env_compat import MflowSettings, SettingsConfigDict


class EmbeddingConfig(MflowSettings):
    """
    Vector embedding configuration.

    Attributes:
        embedding_provider: Backend (openai, ollama, fastembed).
        embedding_model: Model identifier.
        embedding_dimensions: Output vector size.
        embedding_endpoint: Custom API endpoint.
        embedding_api_key: Authentication key.
        embedding_api_version: API version string.
        embedding_max_completion_tokens: Token limit per request.
        embedding_batch_size: Items per batch.
        huggingface_tokenizer: HF tokenizer for counting.
    """

    embedding_provider: str | None = "openai"
    embedding_model: str | None = "openai/text-embedding-3-large"
    embedding_dimensions: int | None = 3072
    embedding_endpoint: str | None = None
    embedding_api_key: str | None = None
    embedding_api_version: str | None = None
    embedding_max_completion_tokens: int | None = 8191
    embedding_batch_size: int | None = None
    huggingface_tokenizer: str | None = None

    model_config = SettingsConfigDict(env_prefix="MFLOW_", env_file=".env", extra="allow")

    def model_post_init(self, context, /) -> None:
        if self.embedding_batch_size is None:
            self.embedding_batch_size = 36

    def to_dict(self) -> dict:
        """Serialize config to dictionary."""
        return {
            k: getattr(self, k)
            for k in [
                "embedding_provider",
                "embedding_model",
                "embedding_dimensions",
                "embedding_endpoint",
                "embedding_api_key",
                "embedding_api_version",
                "embedding_max_completion_tokens",
                "huggingface_tokenizer",
            ]
        }


@lru_cache
def get_embedding_config() -> EmbeddingConfig:
    """Cached config singleton."""
    return EmbeddingConfig()
