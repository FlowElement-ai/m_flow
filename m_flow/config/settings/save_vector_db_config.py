# ---------------------------------------------------------------------------
# m_flow.config.settings.save_vector_db_config
#
# Persist changes to the vector-database adapter configuration.
# ---------------------------------------------------------------------------
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from m_flow.adapters.vector import get_vectordb_config
from m_flow.shared.logging_utils import get_logger

_MASK_CHARS = "*****"
logger = get_logger("config.save_vector_db")


class VectorDBConfigDTO(BaseModel):
    """
    Transfer object for vector-database configuration updates.

    ``api_key`` may contain the masked placeholder returned by
    :func:`get_settings`; in that case the stored key is preserved.
    """

    url: str = Field(default="", description="Connection URL for the vector store")
    api_key: str = Field(default="", description="API key (or masked placeholder)")
    provider: Literal["lancedb", "pgvector", "chromadb", "qdrant"] = Field(..., description="Backend provider slug")


async def save_vector_db_config(dto: VectorDBConfigDTO, persist: bool = True) -> None:
    """
    Apply *dto* to the active vector-DB configuration singleton.

    * ``url`` and ``provider`` are always overwritten.
    * ``api_key`` is written **only** when the caller supplies a
      non-empty, non-masked value.

    Args:
        dto: Configuration update data.
        persist: If True, also write changes to .env file for persistence.
    """
    active = get_vectordb_config()
    active.vector_db_url = dto.url
    active.vector_db_provider = dto.provider

    key_is_real = _MASK_CHARS not in dto.api_key and len(dto.api_key.strip()) > 0
    if key_is_real:
        active.vector_db_key = dto.api_key

    # Persist to .env file
    if persist:
        try:
            from m_flow.config.settings.persist_env import persist_vector_db_config as _persist

            await _persist(
                provider=dto.provider,
                url=dto.url if dto.url else None,
                api_key=dto.api_key if key_is_real else None,
            )
            logger.info(f"VectorDB config persisted: provider={dto.provider}")
        except Exception as e:
            logger.warning(f"Failed to persist VectorDB config to .env: {e}")
            # Don't raise - in-memory config is still updated


# Alias
VectorDBConfig = VectorDBConfigDTO
