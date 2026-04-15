"""Playground API router — session, chat (streaming), persons, flush, link-face, rename."""

from __future__ import annotations

import json

from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import StreamingResponse

import openai

from m_flow.auth.methods import get_authenticated_user
from m_flow.auth.models import User
from m_flow.llm.config import get_llm_config
from m_flow.shared.logging_utils import get_logger

from ..models import (
    SessionCreateRequest,
    SessionCreateResponse,
    ChatRequest,
    ChatResponse,
    FlushRequest,
    FlushResponse,
    LinkFaceRequest,
    RenamePersonRequest,
    VisionActionRequest,
    SetLlmRequest,
)
from ..session import create_session, get_session
from ..face_bridge import (
    resolve_backend_url,
    get_face_api_key,
    fetch_persons,
    check_face_recognition_status,
    start_face_pipeline,
    identify_speaker,
    generate_stream_url,
    sync_mappings_to_session,
    detect_and_link_new_faces,
    save_face_mapping,
    rename_person,
)
from ..retriever import retrieve_memories

_log = get_logger(__name__)


async def _resolve_coref(query: str, user_id: str, session_id: str):
    """Run coreference resolution, returning (resolved_query, replacements, debug_info).

    Falls back gracefully if the coref module is unavailable.
    debug_info contains the full accumulated context for the coref workflow panel.
    """
    try:
        from m_flow.preprocessing.coreference import preprocess_query_with_coref_async

        result = await preprocess_query_with_coref_async(
            query=query,
            user_id=user_id,
            session_id=session_id,
            new_turn=True,
        )
        debug = _extract_coref_debug(session_id, user_id, result)
        return result.resolved_query, result.replacements, debug
    except ImportError:
        _log.debug("Coreference module not installed, skipping")
        return query, [], None
    except Exception as e:
        _log.warning(f"Coreference resolution failed (non-fatal): {e}")
        return query, [], None


def _extract_coref_debug(session_id: str, user_id: str, result) -> dict | None:
    """Extract the full coref session state for the debug workflow panel."""
    try:
        from m_flow.preprocessing.coreference.preprocessor import _get_session_manager

        manager = _get_session_manager()
        if manager is None:
            return None

        with manager._lock:
            session = manager._sessions.get(session_id or f"user_{user_id}")
            if session is None:
                return None

            tracker = session.stream_session.resolver.tracker

            def _entity_list(stack) -> list[dict]:
                out = []
                for e in stack:
                    out.append(
                        {
                            "text": e.text,
                            "type": getattr(e, "type", ""),
                            "sentence_id": getattr(e, "sentence_id", -1),
                        }
                    )
                return out

            return {
                "turn_count": session.turn_count,
                "original_query": result.original_query,
                "resolved_query": result.resolved_query,
                "replacements": result.replacements,
                "entity_stacks": {
                    "persons": _entity_list(tracker.person_stack),
                    "objects": _entity_list(tracker.object_stack),
                    "locations": _entity_list(tracker.location_stack),
                    "times": _entity_list(tracker.time_stack),
                    "events": _entity_list(tracker.event_stack),
                },
                "sentence_count": tracker.sentence_count,
                "last_speaker": tracker.last_speaker.text if tracker.last_speaker else None,
                "last_listener": tracker.last_listener.text if tracker.last_listener else None,
            }
    except Exception as e:
        _log.debug(f"Failed to extract coref debug info: {e}")
        return None


def get_playground_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # POST /session
    # ------------------------------------------------------------------
    @router.post("/session", response_model=SessionCreateResponse)
    async def create_playground_session(
        req: SessionCreateRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Create a new playground session and connect to the face recognition service."""
        status = await check_face_recognition_status(req.face_recognition_url)

        if status == "idle":
            started = await start_face_pipeline(req.face_recognition_url)
            status = "pipeline_started" if started else "offline"

        session = create_session(face_recognition_url=req.face_recognition_url, user_id=str(user.id))
        stream_url = generate_stream_url(req.face_recognition_url)
        await sync_mappings_to_session(session, owner_id=user.id)

        return SessionCreateResponse(
            session_id=session.session_id,
            face_recognition_status=status,
            config={
                "flush_token_threshold": session.flush_token_threshold,
                "flush_turn_threshold": session.flush_turn_threshold,
                "face_recognition_url": req.face_recognition_url,
                "video_feed_url": stream_url,
            },
        )

    # ------------------------------------------------------------------
    # Shared chat preprocessing (used by both streaming and non-streaming)
    # ------------------------------------------------------------------
    async def _prepare_chat_context(req: ChatRequest, user: User):
        """Prepare all context needed for a chat response."""
        import time as _t

        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return None

        t0 = _t.perf_counter()
        persons = await fetch_persons(session.face_recognition_url)
        t1 = _t.perf_counter()

        new_links = await detect_and_link_new_faces(
            persons,
            session,
            owner_id=user.id,
            user=user,
        )
        t2 = _t.perf_counter()

        for p in persons:
            rid = p.get("registered_id")
            name = p.get("name", "")
            if rid is not None:
                session.all_seen_faces.add(rid)
                if name and rid not in session.face_name_mapping:
                    session.face_name_mapping[rid] = name

        speaker = identify_speaker(persons, req.speaker_face_id)
        speaker_name = (session.face_name_mapping.get(speaker.get("registered_id")) if speaker else None) or (
            speaker.get("name", "") if speaker else ""
        )
        speaker_label = speaker_name or "User"

        coref_input = f"[{speaker_label}] {req.message}" if speaker_label != "User" else req.message
        resolved_query, coref_replacements, coref_debug = await _resolve_coref(
            query=coref_input,
            user_id=str(user.id),
            session_id=req.session_id,
        )
        if resolved_query.startswith(f"[{speaker_label}] "):
            resolved_query = resolved_query[len(f"[{speaker_label}] ") :]
        t3 = _t.perf_counter()

        session.add_message("user", req.message, speaker_face_id=req.speaker_face_id)

        persons_lines: list[str] = []
        for p in persons:
            name = p.get("name") or f"Track#{p.get('track_id', '?')}"
            rid = p.get("registered_id")
            ds_ids = session.face_dataset_mapping.get(rid, []) if rid else []
            n_ds = len(ds_ids)
            status = f"{n_ds} dataset(s) linked" if n_ds else "no memory linked"
            persons_lines.append(f"- {name} ({status})")
        persons_desc = "\n".join(persons_lines) if persons_lines else "no one present"

        all_dataset_ids: list[str] = []
        for p in persons:
            rid = p.get("registered_id")
            if rid:
                for ds_id in session.face_dataset_mapping.get(rid, []):
                    if ds_id not in all_dataset_ids:
                        all_dataset_ids.append(ds_id)
        retrieval = await retrieve_memories(resolved_query, all_dataset_ids, user=user)
        t4 = _t.perf_counter()

        _log.info(
            f"[TIMING] fetch_persons={int((t1 - t0) * 1000)}ms "
            f"detect_link={int((t2 - t1) * 1000)}ms "
            f"coref={int((t3 - t2) * 1000)}ms "
            f"retrieval={int((t4 - t3) * 1000)}ms "
            f"total_prep={int((t4 - t0) * 1000)}ms"
        )

        memory_section = ""
        if not retrieval.empty:
            memory_section = f"\nRelevant memories:\n{retrieval.context}\n"

        system_prompt = (
            f"You are the M-Flow Playground AI assistant.\n\n"
            f"People present:\n{persons_desc}\n\n"
            f"Current speaker: {speaker_label}\n"
            f"{memory_section}"
        )

        recent = session.get_recent_messages(max_turns=20)
        llm_messages = [{"role": "system", "content": system_prompt}]
        for m in recent:
            llm_messages.append({"role": m["role"], "content": m["content"]})

        speaker_info = None
        if speaker:
            speaker_info = {
                "face_registered_id": speaker.get("registered_id"),
                "display_name": speaker.get("name", ""),
            }

        coref_info = None
        if coref_replacements and resolved_query != req.message:
            coref_info = [
                {"original": r.get("pronoun", ""), "resolved": r.get("replacement", "")}
                for r in coref_replacements
                if r.get("pronoun") and r.get("replacement") and r.get("pronoun") != r.get("replacement")
            ]

        persons_in_frame = [
            {
                "face_registered_id": p.get("registered_id"),
                "name": session.face_name_mapping.get(p.get("registered_id")) or p.get("name", ""),
                "mouth": p.get("mouth", ""),
                "identity": p.get("identity", ""),
                "dataset_ids": session.face_dataset_mapping.get(p.get("registered_id"), []),
                "avatar": p.get("avatar"),
            }
            for p in persons
        ]

        return {
            "session": session,
            "llm_messages": llm_messages,
            "speaker_info": speaker_info,
            "persons_in_frame": persons_in_frame,
            "new_links": new_links,
            "has_memory_context": not retrieval.empty,
            "coref_info": coref_info,
            "coref_debug": coref_debug,
        }

    def _build_done_payload(
        session,
        speaker_info,
        persons_in_frame,
        new_links,
        has_memory_context,
        coref_info,
        coref_debug,
        full_reply,
        flush_result,
    ):
        return {
            "full_reply": full_reply,
            "speaker": speaker_info,
            "persons_in_frame": persons_in_frame,
            "memory_status": {
                "buffer_tokens": session.buffer_tokens,
                "buffer_turns": session.buffer_turns,
                "threshold_tokens": session.flush_token_threshold,
                "threshold_turns": session.flush_turn_threshold,
                "flushed": flush_result.ok if flush_result else False,
                "flush_details": {
                    "tokens_flushed": flush_result.tokens_flushed,
                    "turns_flushed": flush_result.turns_flushed,
                    "datasets_affected": flush_result.datasets_affected,
                }
                if flush_result and flush_result.ok
                else None,
            },
            "new_face_links": new_links if new_links else None,
            "has_memory_context": has_memory_context,
            "coref_resolutions": coref_info,
            "coref_debug": coref_debug,
        }

    # ------------------------------------------------------------------
    # POST /chat — SSE streaming chat endpoint
    # ------------------------------------------------------------------
    @router.post("/chat")
    async def playground_chat(
        req: ChatRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Send a message and receive an SSE-streamed AI reply."""
        ctx = await _prepare_chat_context(req, user)
        if ctx is None:
            return ChatResponse(reply="Session not found. Please create a new session.")

        session = ctx["session"]

        async def event_stream():
            full_reply = ""
            try:
                cfg = get_llm_config()
                # Session override > global .env config
                model_name = session.llm_model_override or cfg.llm_model or "gpt-5-mini"
                if "/" in model_name:
                    model_name = model_name.split("/", 1)[1]
                # is not None: distinguish None (use global) from "" (explicitly no key)
                api_key = session.llm_api_key_override if session.llm_api_key_override is not None else cfg.llm_api_key
                endpoint = (
                    session.llm_endpoint_override
                    if session.llm_endpoint_override is not None
                    else (cfg.llm_endpoint or None)
                )
                client_kwargs: dict = {"api_key": api_key}
                if endpoint:
                    client_kwargs["base_url"] = endpoint
                client = openai.AsyncOpenAI(**client_kwargs)
                create_kwargs: dict = {
                    "model": model_name,
                    "messages": ctx["llm_messages"],
                    "stream": True,
                }
                # Newer models (gpt-5-*) use max_completion_tokens; older use max_tokens
                if "gpt-5" in model_name or "o1" in model_name or "o3" in model_name:
                    create_kwargs["max_completion_tokens"] = 1024
                else:
                    create_kwargs["max_tokens"] = 1024
                    create_kwargs["temperature"] = 0.7
                stream = await client.chat.completions.create(**create_kwargs)
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        full_reply += delta.content
                        yield f"event: token\ndata: {json.dumps({'text': delta.content})}\n\n"
            except Exception as e:
                _log.error(f"LLM streaming failed: {e}")
                full_reply = "AI service is temporarily unavailable."
                yield f"event: token\ndata: {json.dumps({'text': full_reply})}\n\n"

            session.add_message("assistant", full_reply)

            flush_result = None
            if session.should_flush():
                flush_result = await session.flush_to_long_term(user=user)

            done_data = _build_done_payload(
                session=session,
                speaker_info=ctx["speaker_info"],
                persons_in_frame=ctx["persons_in_frame"],
                new_links=ctx["new_links"],
                has_memory_context=ctx["has_memory_context"],
                coref_info=ctx["coref_info"],
                coref_debug=ctx["coref_debug"],
                full_reply=full_reply,
                flush_result=flush_result,
            )
            yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # POST /flush
    # ------------------------------------------------------------------
    @router.post("/flush", response_model=FlushResponse)
    async def manual_flush(
        req: FlushRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Manually trigger short-term to long-term memory flush."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return FlushResponse(ok=False, error="Session not found")

        result = await session.flush_to_long_term(user=user)
        return FlushResponse(
            ok=result.ok,
            episodes_created=result.episodes_created,
            datasets_affected=result.datasets_affected,
            tokens_flushed=result.tokens_flushed,
            turns_flushed=result.turns_flushed,
            error=result.error,
        )

    # ------------------------------------------------------------------
    # GET /persons
    # ------------------------------------------------------------------
    @router.get("/persons")
    async def get_persons(
        session_id: str,
        user: User = Depends(get_authenticated_user),
    ):
        """Get current persons in frame with dataset associations."""
        session = get_session(session_id, user_id=str(user.id))
        if session is None:
            return []

        # Check service health and fetch persons in one call
        face_rec_status = await check_face_recognition_status(session.face_recognition_url)
        persons = await fetch_persons(session.face_recognition_url) if face_rec_status != "offline" else []

        for p in persons:
            rid = p.get("registered_id")
            name = p.get("name", "")
            if rid is not None:
                session.all_seen_faces.add(rid)
                if name and rid not in session.face_name_mapping:
                    session.face_name_mapping[rid] = name

        result = [
            {
                "face_registered_id": p.get("registered_id"),
                "display_name": session.face_name_mapping.get(p.get("registered_id")) or p.get("name", ""),
                "mouth": p.get("mouth", ""),
                "identity": p.get("identity", ""),
                "track_id": p.get("track_id"),
                "person_id": p.get("person_id"),
                "dataset_ids": session.face_dataset_mapping.get(p.get("registered_id"), []),
                "avatar": p.get("avatar"),
            }
            for p in persons
        ]

        return {
            "persons": result,
            "_face_rec_status": face_rec_status,
            "_has_any_mapping": bool(session.face_dataset_mapping),
        }

    # ------------------------------------------------------------------
    # POST /link-face — manually link face to dataset (persisted)
    # ------------------------------------------------------------------
    @router.post("/link-face")
    async def link_face(
        req: LinkFaceRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Manually link a face to a dataset (persisted to DB)."""
        ds_uuid = UUID(req.dataset_id) if req.dataset_id else None
        if ds_uuid:
            await save_face_mapping(
                owner_id=user.id,
                face_registered_id=req.face_registered_id,
                dataset_id=ds_uuid,
                display_name=req.display_name,
                auto_created=False,
            )

        for session in _get_user_sessions(str(user.id)):
            if req.dataset_id:
                ds_list = session.face_dataset_mapping.setdefault(req.face_registered_id, [])
                if req.dataset_id not in ds_list:
                    ds_list.append(req.dataset_id)
            if req.display_name:
                session.face_name_mapping[req.face_registered_id] = req.display_name
        return {"ok": True, "created_new_dataset": False}

    # ------------------------------------------------------------------
    # POST /rename-person — rename across fanjing-face-recognition + M-Flow dataset
    # ------------------------------------------------------------------
    @router.post("/rename-person")
    async def rename_person_endpoint(
        req: RenamePersonRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Rename a person across face recognition and memory systems."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return {"ok": False, "error": "Session not found"}

        await rename_person(
            face_recognition_url=session.face_recognition_url,
            face_registered_id=req.face_registered_id,
            new_name=req.new_name,
            owner_id=user.id,
            user=user,
        )

        for s in _get_user_sessions(str(user.id)):
            s.face_name_mapping[req.face_registered_id] = req.new_name

        return {"ok": True}

    # ------------------------------------------------------------------
    # GET /vision-status — proxy face recognition stats through backend
    # ------------------------------------------------------------------
    @router.get("/vision-status")
    async def vision_status(
        session_id: str,
        user: User = Depends(get_authenticated_user),
    ):
        """Proxy face recognition service stats (avoids CORS)."""
        session = get_session(session_id, user_id=str(user.id))
        if session is None:
            return {"error": "Session not found"}

        import httpx

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                api_key = get_face_api_key()
                hdrs = {"X-API-Key": api_key} if api_key else {}
                resp = await client.get(
                    f"{resolve_backend_url(session.face_recognition_url)}/api/stats",
                    headers=hdrs,
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return {"running": False, "error": "Service unreachable"}

    # ------------------------------------------------------------------
    # POST /start-vision — start face recognition pipeline
    # ------------------------------------------------------------------
    @router.post("/start-vision")
    async def start_vision(
        req: VisionActionRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Start the face recognition pipeline with specified features."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return {"ok": False, "error": "Session not found"}

        api_key = get_face_api_key()
        if not api_key:
            return {"ok": False, "error": "FACE_API_KEY not set"}

        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{resolve_backend_url(session.face_recognition_url)}/api/start",
                    json={
                        "mode": "camera",
                        "device": 0,
                        "speaking_enabled": req.speaking_enabled,
                        "embed_enabled": req.embed_enabled,
                        "align_enabled": req.align_enabled,
                        "m5_enabled": req.m5_enabled,
                    },
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    return {"ok": True, **resp.json()}
                return {"ok": False, "error": f"Start failed: {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # POST /stop-vision — stop face recognition pipeline
    # ------------------------------------------------------------------
    @router.post("/stop-vision")
    async def stop_vision(
        req: VisionActionRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Stop the face recognition pipeline."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return {"ok": False, "error": "Session not found"}

        api_key = get_face_api_key()
        if not api_key:
            return {"ok": False, "error": "FACE_API_KEY not set"}

        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{resolve_backend_url(session.face_recognition_url)}/api/stop",
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    return {"ok": True}
                return {"ok": False, "error": f"Stop failed: {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # POST /restart-vision — restart face recognition pipeline with config
    # ------------------------------------------------------------------
    @router.post("/restart-vision")
    async def restart_vision(
        req: VisionActionRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Stop and restart the face recognition pipeline with new settings."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return {"ok": False, "error": "Session not found"}

        face_url = resolve_backend_url(session.face_recognition_url)
        api_key = get_face_api_key()
        if not api_key:
            return {"ok": False, "error": "FACE_API_KEY not set"}

        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{face_url}/api/stop",
                    headers={"X-API-Key": api_key},
                )
        except Exception:
            pass

        import asyncio

        await asyncio.sleep(1)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{face_url}/api/start",
                    json={
                        "mode": "camera",
                        "device": 0,
                        "speaking_enabled": req.speaking_enabled,
                        "embed_enabled": req.embed_enabled,
                        "align_enabled": req.align_enabled,
                        "m5_enabled": req.m5_enabled,
                    },
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    return {"ok": True, **resp.json()}
                return {"ok": False, "error": f"Start failed: {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # POST /asr — transcribe audio via OpenAI Whisper
    # ------------------------------------------------------------------
    @router.post("/asr")
    async def asr_endpoint(
        file: UploadFile,
        user: User = Depends(get_authenticated_user),
    ):
        """Transcribe audio using OpenAI Whisper API."""
        cfg = get_llm_config()
        try:
            client = openai.AsyncOpenAI(
                api_key=cfg.llm_api_key,
                **({"base_url": cfg.llm_endpoint} if cfg.llm_endpoint else {}),
            )
            content = await file.read()
            import io

            audio = io.BytesIO(content)
            audio.name = file.filename or "audio.webm"
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
            )
            return {"ok": True, "text": transcript.text}
        except Exception as e:
            _log.error(f"ASR failed: {e}")
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # POST /set-llm — switch LLM provider per session
    # ------------------------------------------------------------------
    @router.post("/set-llm")
    async def set_llm(
        req: SetLlmRequest,
        user: User = Depends(get_authenticated_user),
    ):
        """Switch the LLM provider for this session."""
        session = get_session(req.session_id, user_id=str(user.id))
        if session is None:
            return {"ok": False, "error": "Session not found"}

        model = req.model or None
        endpoint = req.endpoint or None

        api_key: str | None = req.api_key if req.api_key else None
        if not endpoint:
            api_key = None

        session.llm_model_override = model
        session.llm_endpoint_override = endpoint
        session.llm_api_key_override = api_key

        _log.info(
            f"Session {session.session_id}: LLM switched to "
            f"model={model or '(default)'} endpoint={endpoint or '(default)'}"
        )
        return {"ok": True, "model": model, "endpoint": endpoint}

    def _get_user_sessions(user_id: str):
        """Return only sessions belonging to this user."""
        from ..session import _sessions

        return [s for s in _sessions.values() if s.user_id == user_id]

    return router
