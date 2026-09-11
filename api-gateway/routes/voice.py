"""V24 P2: voice routes (moved verbatim from main.py)."""

import asyncio
import logging
import os
from pathlib import Path

import httpx
from deps import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_STT_MODEL,
    DEEPGRAM_TTS_MODEL,
    JARVIS_MAX_CONTEXT_CHARS,
    MAX_HISTORY_MESSAGES,
    db,
    limiter,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from schemas import ChatRequest, DeepgramSpeakRequest, VoiceTurnRequest
from services import (
    _run_agent_loop,
    apply_system_prompt,
    select_agent_model,
    select_model,
    select_voice_path,
    stream_chat,
)

logger = logging.getLogger(__name__)

GATEWAY_DIR = Path(__file__).resolve().parent.parent


router = APIRouter()


@router.post("/v1/deepgram-token")
async def deepgram_token():
    """Mint a short-lived browser token without exposing the Deepgram API key."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://api.deepgram.com/v1/auth/grant",
                headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"},
                json={"ttl_seconds": 300},
            )
        if r.status_code != 200:
            logger.warning("Deepgram token request failed: %s", r.text[:500])
            raise HTTPException(502, "Deepgram token request failed")
        data = r.json()
        return {"access_token": data.get("access_token"), "expires_in": data.get("expires_in", 300)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Deepgram token error")
        raise HTTPException(502, f"Deepgram token error: {e}")


@router.post("/v1/deepgram-speak")
async def deepgram_speak(req: DeepgramSpeakRequest):
    """Synthesize speech server-side so the long-lived API key never reaches the browser."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 6000:
        text = text[:6000]
    speed = max(0.7, min(1.5, float(req.speed or 1.0)))
    model = (req.model or DEEPGRAM_TTS_MODEL).strip()
    url = "https://api.deepgram.com/v1/speak"
    params = {"model": model, "encoding": "linear16", "sample_rate": 24000, "container": "wav", "speed": speed}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, params=params, headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"}, json={"text": text})
        if r.status_code != 200:
            logger.warning("Deepgram TTS failed: %s", r.text[:500])
            raise HTTPException(502, "Deepgram speech synthesis failed")
        return StreamingResponse(iter([r.content]), media_type="audio/wav", headers={"Cache-Control":"no-store"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Deepgram TTS error")
        raise HTTPException(502, f"Deepgram TTS error: {e}")


@router.post("/v1/deepgram-speak-stream")
async def deepgram_speak_stream(req: DeepgramSpeakRequest):
    """Stream raw 24 kHz linear16 PCM from Deepgram so the browser can begin
    speaking before the complete synthesis response has arrived."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 6000:
        text = text[:6000]
    speed = max(0.7, min(1.5, float(req.speed or 1.0)))
    model = (req.model or DEEPGRAM_TTS_MODEL).strip()
    url = "https://api.deepgram.com/v1/speak"
    params = {"model": model, "encoding": "linear16", "sample_rate": 24000, "container": "none", "speed": speed}
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream = await client.stream("POST", url, params=params, headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"}, json={"text": text}).__aenter__()
        if upstream.status_code != 200:
            detail = (await upstream.aread()).decode("utf-8", "replace")[:500]
            await upstream.aclose(); await client.aclose()
            raise HTTPException(502, f"Deepgram speech synthesis failed: {detail}")
        async def body_iter():
            try:
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(body_iter(), media_type="audio/L16;rate=24000", headers={"Cache-Control":"no-store", "X-Audio-Format":"pcm_s16le_24000"})
    except HTTPException:
        raise
    except Exception as e:
        await upstream.aclose() if 'upstream' in locals() else asyncio.sleep(0)
        await client.aclose()
        logger.exception("Deepgram streaming TTS error")
        raise HTTPException(502, f"Deepgram streaming TTS error: {e}")


@router.get("/v1/deepgram-status")
async def deepgram_status():
    return {"configured": bool(DEEPGRAM_API_KEY), "stt_model": DEEPGRAM_STT_MODEL, "audio_output": False, "mode": "push_to_talk"}


@router.get("/voice", include_in_schema=False)
async def voice_ui():
    """Browser voice console: headset microphone -> AI -> headset speech."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "ptt.html"), media_type="text/html")


@router.get("/voice-live", include_in_schema=False)
@router.get("/voice-live.html", include_in_schema=False)
async def voice_live_ui():
    """Gemini Live full-duplex voice console with tool calling."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "ptt.html"), media_type="text/html")


@router.get("/voice-engine.js", include_in_schema=False)
async def voice_engine_js():
    """Shared adaptive voice engine (state machine, guards, streaming TTS helpers)."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "voice-engine.js"), media_type="application/javascript")


@router.post("/v1/voice/turn")
@limiter.limit("30/minute")
async def voice_turn(request: Request, body: VoiceTurnRequest):
    """Unified voice turn: tool-intent speech runs the agent (JSON), everything
    else streams auto-routed chat (SSE) so TTS can start on the first sentence."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    if any(m.role == "system" for m in body.messages):
        raise HTTPException(400, "system messages are not accepted by the voice endpoint")
    if not any(m.role == "user" and m.content.strip() for m in body.messages):
        raise HTTPException(400, "at least one user message is required")
    last_text = next(m.content for m in reversed(body.messages) if m.role == "user")

    model_messages = apply_system_prompt(list(body.messages), body.assistant_profile)

    if body.conversation_id is not None:
        if not db.conversation_exists(body.conversation_id):
            raise HTTPException(status_code=404, detail=f"conversation {body.conversation_id} not found")
        conversation_profile = db.get_conversation_profile(body.conversation_id)
        requested_profile = body.assistant_profile.lower()
        if conversation_profile != requested_profile:
            raise HTTPException(status_code=409, detail="conversation belongs to a different profile")
        history = db.get_conversation(body.conversation_id, limit=MAX_HISTORY_MESSAGES)
        incoming = [{"role": m.role, "content": m.content} for m in body.messages]
        if history and incoming[:len(history)] == history:
            new_messages = incoming[len(history):]
        else:
            new_messages = incoming
        for msg in new_messages:
            if msg["role"] == "user":
                db.add_message(body.conversation_id, "user", msg["content"])

    if sum(len(m.content) for m in model_messages) > JARVIS_MAX_CONTEXT_CHARS:
        system_msgs = [m for m in model_messages if m.role == "system"][:1]
        non_system = [m for m in model_messages if m.role != "system"]
        kept = []
        total = sum(len(m.content) for m in system_msgs)
        for msg in reversed(non_system):
            if total + len(msg.content) > JARVIS_MAX_CONTEXT_CHARS and kept:
                break
            kept.append(msg); total += len(msg.content)
        model_messages = system_msgs + list(reversed(kept))

    if select_voice_path(last_text, body.assistant_profile) == "agent":
        selected_model = select_agent_model(body.model, [m.model_dump() for m in model_messages], body.assistant_profile)
        result = await _run_agent_loop(selected_model, [m.model_dump() for m in model_messages],
                                       body.assistant_profile, body.conversation_id, 8)
        result["kind"] = "agent"
        result["model"] = selected_model
        return result

    selected_model = select_model(body.model, [m.model_dump() for m in model_messages if m.role != "system"], body.assistant_profile)
    stream_request = ChatRequest(
        model=selected_model,
        messages=model_messages,
        stream=True,
        temperature=body.temperature,
        max_tokens=body.max_tokens or 512,
        conversation_id=body.conversation_id,
        use_rag=False,
        assistant_profile=body.assistant_profile,
    )
    return StreamingResponse(
        stream_chat(stream_request),
        media_type="text/event-stream",
        headers={"X-Voice-Model": selected_model, "X-Voice-Path": "stream"},
    )
