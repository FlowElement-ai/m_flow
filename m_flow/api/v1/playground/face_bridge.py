"""Bridge to fanjing-face-recognition service — fetch persons, manage face↔dataset mapping."""

from __future__ import annotations

import hmac
import os
import time
from uuid import UUID

import httpx
from sqlalchemy import select

from m_flow.shared.logging_utils import get_logger

_log = get_logger(__name__)

def get_face_api_key() -> str:
    """Read FACE_API_KEY lazily from env (supports dotenv loaded after module import)."""
    return os.environ.get("FACE_API_KEY", "")

_IN_DOCKER = os.path.exists("/.dockerenv")


def resolve_backend_url(face_recognition_url: str) -> str:
    """Translate localhost URLs to host.docker.internal when running inside Docker."""
    if not _IN_DOCKER:
        return face_recognition_url
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(face_recognition_url)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        replaced = parsed._replace(netloc=parsed.netloc.replace(parsed.hostname, "host.docker.internal"))
        return urlunparse(replaced)
    return face_recognition_url


# ---------------------------------------------------------------------------
# Fanjing-face-recognition HTTP helpers
# ---------------------------------------------------------------------------


async def fetch_persons(face_recognition_url: str) -> list[dict]:
    """Fetch currently detected persons from the face recognition service."""
    try:
        key = get_face_api_key()
        headers = {"X-API-Key": key} if key else {}
        url = resolve_backend_url(face_recognition_url)
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{url}/api/persons", headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        _log.warning(f"fanjing-face-recognition unreachable: {e}")
    return []


async def check_face_recognition_status(face_recognition_url: str) -> str:
    try:
        url = resolve_backend_url(face_recognition_url)
        key = get_face_api_key()
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {"X-API-Key": key} if key else {}
            resp = await client.get(f"{url}/api/stats", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return "connected" if data.get("running") else "idle"
    except Exception:
        pass
    return "offline"


async def start_face_pipeline(face_recognition_url: str) -> bool:
    key = get_face_api_key()
    if not key:
        _log.warning("FACE_API_KEY not set, cannot start fanjing-face-recognition pipeline")
        return False
    try:
        url = resolve_backend_url(face_recognition_url)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{url}/api/start",
                json={
                    "mode": "camera",
                    "device": 0,
                    "speaking_enabled": True,
                    "embed_enabled": True,
                    "align_enabled": True,
                    "m5_enabled": True,
                },
                headers={"X-API-Key": key},
            )
            return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception as e:
        _log.warning(f"Failed to start fanjing-face-recognition pipeline: {e}")
    return False


def generate_stream_url(face_recognition_url: str) -> str:
    key = get_face_api_key()
    if not key:
        return f"{face_recognition_url}/video_feed"
    ts = str(int(time.time()))
    sig = hmac.new(key.encode(), ts.encode(), "sha256").hexdigest()[:16]
    return f"{face_recognition_url}/video_feed?ts={ts}&sig={sig}"


def identify_speaker(persons: list[dict], speaker_face_id: int | None = None) -> dict | None:
    if not persons:
        return None
    if speaker_face_id is not None:
        for p in persons:
            if p.get("registered_id") == speaker_face_id or p.get("person_id") == speaker_face_id:
                return p
    known = [p for p in persons if p.get("identity") == "KNOWN_STRONG"]
    if len(known) == 1:
        return known[0]
    speaking = [p for p in persons if p.get("mouth") == "speaking"]
    if len(speaking) == 1:
        return speaking[0]
    return known[0] if known else (persons[0] if len(persons) == 1 else None)


# ---------------------------------------------------------------------------
# Persistent face ↔ dataset mapping (Phase 3)
# ---------------------------------------------------------------------------


async def load_face_mappings(owner_id: UUID) -> list[dict]:
    """Load all face-dataset mappings from DB for a given user.

    Returns list of dicts: {face_registered_id, dataset_id, display_name, auto_created}
    """
    from m_flow.adapters.relational import get_async_session
    from .face_mapping_model import PlaygroundFaceMapping

    async with get_async_session(commit=False) as session:
        result = await session.execute(select(PlaygroundFaceMapping).where(PlaygroundFaceMapping.owner_id == owner_id))
        rows = result.scalars().all()
        return [
            {
                "face_registered_id": r.face_registered_id,
                "dataset_id": str(r.dataset_id),
                "display_name": r.display_name,
                "auto_created": r.auto_created,
            }
            for r in rows
        ]


async def save_face_mapping(
    owner_id: UUID,
    face_registered_id: int,
    dataset_id: UUID,
    display_name: str,
    auto_created: bool = True,
) -> None:
    """Add a face-dataset mapping. Skips if the exact (owner, face, dataset) link exists."""
    from m_flow.adapters.relational import get_async_session
    from .face_mapping_model import PlaygroundFaceMapping

    async with get_async_session(commit=True) as session:
        result = await session.execute(
            select(PlaygroundFaceMapping).where(
                PlaygroundFaceMapping.owner_id == owner_id,
                PlaygroundFaceMapping.face_registered_id == face_registered_id,
                PlaygroundFaceMapping.dataset_id == dataset_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.display_name = display_name
            existing.auto_created = auto_created
        else:
            session.add(
                PlaygroundFaceMapping(
                    owner_id=owner_id,
                    face_registered_id=face_registered_id,
                    dataset_id=dataset_id,
                    display_name=display_name,
                    auto_created=auto_created,
                )
            )


async def ensure_dataset_for_face(
    face_registered_id: int,
    display_name: str,
    owner_id: UUID,
    user,
) -> str:
    """If the face has no linked dataset, auto-create one and persist the mapping.

    Returns the dataset_id (as string) for the face.
    """
    from m_flow.data.methods import create_authorized_dataset

    safe_name = (display_name or f"User#{face_registered_id}").replace(" ", "_").replace(".", "_")
    ds_name = f"{safe_name}_memory"
    dataset = await create_authorized_dataset(ds_name, user)
    ds_id = dataset.id

    await save_face_mapping(
        owner_id=owner_id,
        face_registered_id=face_registered_id,
        dataset_id=ds_id,
        display_name=display_name or f"User#{face_registered_id}",
        auto_created=True,
    )

    _log.info(f"Auto-created dataset '{ds_name}' ({ds_id}) for face #{face_registered_id}")
    return str(ds_id)


async def sync_mappings_to_session(session, owner_id: UUID) -> list[dict]:
    """Load persistent mappings from DB and apply to the in-memory session.

    Builds a list of dataset_ids per face (one face can have multiple datasets).
    """
    mappings = await load_face_mappings(owner_id)
    session.face_dataset_mapping.clear()
    for m in mappings:
        fid = m["face_registered_id"]
        ds_list = session.face_dataset_mapping.setdefault(fid, [])
        if m["dataset_id"] not in ds_list:
            ds_list.append(m["dataset_id"])
        session.face_name_mapping[fid] = m["display_name"]
    return mappings


async def rename_person(
    face_recognition_url: str,
    face_registered_id: int,
    new_name: str,
    owner_id: UUID,
    user,
) -> bool:
    """Rename a person across fanjing-face-recognition and the persistent mapping table.

    1. Rename in fanjing-face-recognition via /api/person/rename
    2. Update display_name in all persistent mapping rows for this face
    """
    # 1. Rename in fanjing-face-recognition
    key = get_face_api_key()
    if key:
        try:
            url = resolve_backend_url(face_recognition_url)
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{url}/api/person/rename",
                    json={"registered_id": face_registered_id, "name": new_name},
                    headers={"X-API-Key": key},
                )
                if resp.status_code != 200:
                    _log.warning(f"fanjing-face-recognition rename failed: {resp.status_code}")
        except Exception as e:
            _log.warning(f"fanjing-face-recognition rename request failed: {e}")

    # 2. Update ALL persistent mappings for this face
    mappings = await load_face_mappings(owner_id)
    face_mappings = [m for m in mappings if m["face_registered_id"] == face_registered_id]
    for target in face_mappings:
        await save_face_mapping(
            owner_id=owner_id,
            face_registered_id=face_registered_id,
            dataset_id=UUID(target["dataset_id"]),
            display_name=new_name,
            auto_created=target["auto_created"],
        )

    return True


async def detect_and_link_new_faces(
    persons: list[dict],
    session,
    owner_id: UUID,
    user,
) -> list[dict]:
    """Detect new registered faces and auto-create datasets for them.

    Returns list of newly created mappings: [{face_registered_id, dataset_id, display_name}]
    """
    new_links: list[dict] = []

    for p in persons:
        rid = p.get("registered_id")
        identity = p.get("identity", "")
        if rid is None:
            continue
        if session.face_dataset_mapping.get(rid):
            continue
        if identity not in ("KNOWN_STRONG", "UNKNOWN_STRONG"):
            continue

        display_name = p.get("name", "") or f"User#{rid}"
        ds_id = await ensure_dataset_for_face(rid, display_name, owner_id, user)

        session.face_dataset_mapping.setdefault(rid, []).append(ds_id)
        session.face_name_mapping[rid] = display_name

        new_links.append(
            {
                "face_registered_id": rid,
                "dataset_id": ds_id,
                "display_name": display_name,
            }
        )

    return new_links
