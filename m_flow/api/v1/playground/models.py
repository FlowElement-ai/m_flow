"""Playground API request/response models."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


_ALLOWED_FACE_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


class SessionCreateRequest(BaseModel):
    face_recognition_url: str = Field(default="http://localhost:5001")

    @classmethod
    def validate_face_recognition_url(cls, v: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if parsed.hostname not in _ALLOWED_FACE_HOSTS:
            raise ValueError(f"face_recognition_url must be localhost, got {parsed.hostname}")
        return v

    def __init__(self, **data):
        super().__init__(**data)
        self.face_recognition_url = self.validate_face_recognition_url(self.face_recognition_url)


class SessionCreateResponse(BaseModel):
    session_id: str
    face_recognition_status: str  # "connected" | "pipeline_started" | "offline"
    config: dict


class ChatRequest(BaseModel):
    session_id: str
    message: str
    speaker_face_id: Optional[int] = None


class ChatResponse(BaseModel):
    reply: str
    speaker: Optional[dict] = None
    persons_in_frame: list[dict] = []
    memory_status: dict = {}
    new_face_links: Optional[list[dict]] = None
    has_memory_context: bool = False
    coref_resolutions: Optional[list[dict]] = None


class FlushRequest(BaseModel):
    session_id: str


class FlushResponse(BaseModel):
    ok: bool
    episodes_created: int = 0
    datasets_affected: list[str] = []
    tokens_flushed: int = 0
    turns_flushed: int = 0
    error: Optional[str] = None


class LinkFaceRequest(BaseModel):
    face_registered_id: int
    dataset_id: Optional[str] = None
    display_name: str = ""


class RenamePersonRequest(BaseModel):
    face_registered_id: int
    new_name: str
    session_id: str


class VisionActionRequest(BaseModel):
    session_id: str
    speaking_enabled: bool = True
    embed_enabled: bool = True
    align_enabled: bool = True
    m5_enabled: bool = True


class SetLlmRequest(BaseModel):
    session_id: str
    model: str = ""
    endpoint: str = ""
    api_key: str = ""
