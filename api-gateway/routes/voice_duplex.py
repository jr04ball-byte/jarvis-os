"""Opt-in local duplex voice session; existing PTT remains independent."""
from __future__ import annotations
import asyncio
import io
import json
import math
import os
import secrets
import time
import wave

from brains import BrainRequest, Message, complete
from deps import AI_API_TOKEN, AI_REQUIRE_AUTH
from events import Event, bus as event_bus
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from local_tts import synthesize_local
from local_voice import transcribe_local

router = APIRouter()
_sessions = 0
_sessions_lock = asyncio.Lock()


@router.get("/voice-duplex", include_in_schema=False)
async def duplex_ui():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "voice-duplex.html"))


@router.get("/voice-duplex-worklet.js", include_in_schema=False)
async def duplex_worklet():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "voice-duplex-worklet.js"), media_type="application/javascript")


def _enabled() -> bool:
    return os.getenv("VOICE_DUPLEX_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as recording:
        recording.setnchannels(1); recording.setsampwidth(2); recording.setframerate(16000)
        recording.writeframes(pcm)
    return out.getvalue()


def _level(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    samples = memoryview(pcm[:len(pcm) // 2 * 2]).cast("h")
    return math.sqrt(sum(int(v) * int(v) for v in samples) / len(samples)) / 32768


@router.websocket("/v1/voice/duplex")
async def duplex(websocket: WebSocket):
    global _sessions
    if not _enabled():
        await websocket.close(code=1008, reason="Duplex voice is disabled")
        return
    origin = websocket.headers.get("origin", "")
    allowed = {"http://127.0.0.1:8000", "http://localhost:8000"}
    if origin and origin not in allowed:
        await websocket.close(code=1008, reason="Origin is not allowed")
        return
    supplied = websocket.query_params.get("token", "")
    if AI_REQUIRE_AUTH and (not AI_API_TOKEN or not secrets.compare_digest(supplied, AI_API_TOKEN)):
        await websocket.close(code=1008, reason="Authentication required")
        return
    maximum = max(1, int(os.getenv("VOICE_DUPLEX_MAX_SESSIONS", "1")))
    async with _sessions_lock:
        if _sessions >= maximum:
            await websocket.close(code=1013, reason="Voice session limit reached")
            return
        _sessions += 1
    await websocket.accept()
    send_lock = asyncio.Lock()
    generation = 0
    response_task = None
    cancel = asyncio.Event()
    history: list[Message] = []

    async def send_json(event: str, **fields):
        async with send_lock:
            await websocket.send_text(json.dumps({"event": event, **fields}))

    async def respond(pcm: bytes, current: int, tripwire: asyncio.Event):
        started = time.perf_counter()
        try:
            await send_json("transcribing", generation=current)
            event_bus.emit(Event(name="voice.transcribing"))
            transcript = (await transcribe_local(_wav(pcm), "audio/wav")).strip()
            if tripwire.is_set() or not transcript:
                return
            await send_json("transcript_final", text=transcript, generation=current)
            history.append(Message(role="user", content=transcript))
            brain = os.getenv("VOICE_DUPLEX_BRAIN", "ollama").strip().lower()
            await send_json("thinking", provider=brain, generation=current)
            event_bus.emit(Event(name="voice.thinking"))
            result = await complete(BrainRequest(messages=history[-20:], brain=brain, max_tokens=512))
            text = result["message"]["content"].strip()
            if tripwire.is_set():
                return
            history.append(Message(role="assistant", content=text))
            await send_json("response_text", text=text, final=True, generation=current,
                            provider=result.get("provider"), model=result.get("model"))
            if os.getenv("VOICE_RESPONSE_MODE", "text").lower() == "kokoro" and text:
                event_bus.emit(Event(name="voice.speaking"))
                await send_json("audio_start", generation=current)
                audio, rate = await synthesize_local(text)
                if tripwire.is_set():
                    return
                await send_json("audio_format", sample_rate=rate, generation=current)
                for offset in range(0, len(audio), 24_000):
                    if tripwire.is_set():
                        return
                    async with send_lock:
                        await websocket.send_bytes(audio[offset:offset + 24_000])
                await send_json("audio_end", generation=current)
            event_bus.emit(Event(name="voice.completed"))
            await send_json("completed", generation=current, elapsed_ms=int((time.perf_counter()-started)*1000))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            event_bus.emit(Event(name="voice.failed"))
            await send_json("error", code="voice_turn_failed", message=str(exc)[:240], generation=current)

    try:
        await send_json("ready", mode="local_duplex")
        speech = False
        audio = bytearray()
        silent_frames = 0
        threshold = float(os.getenv("VOICE_VAD_THRESHOLD", "0.018"))
        end_frames = max(3, int(int(os.getenv("VOICE_END_SILENCE_MS", "550")) / 32))
        max_bytes = int(os.getenv("VOICE_MAX_TURN_SECONDS", "30")) * 16000 * 2
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            chunk = message.get("bytes")
            if chunk is None:
                continue
            loud = _level(chunk) >= threshold
            if loud and not speech:
                speech = True; audio.clear(); silent_frames = 0; generation += 1
                if response_task and not response_task.done():
                    cancel.set(); response_task.cancel()
                    await send_json("interrupt", generation=generation-1)
                    event_bus.emit(Event(name="voice.interrupted"))
                cancel = asyncio.Event()
                await send_json("speech_start", generation=generation)
                event_bus.emit(Event(name="voice.listening"))
            if speech:
                audio.extend(chunk)
                silent_frames = 0 if loud else silent_frames + 1
                if len(audio) >= max_bytes or silent_frames >= end_frames:
                    speech = False
                    await send_json("speech_end", generation=generation)
                    response_task = asyncio.create_task(respond(bytes(audio), generation, cancel))
                    audio = bytearray(); silent_frames = 0
    except WebSocketDisconnect:
        pass
    finally:
        cancel.set()
        if response_task and not response_task.done():
            response_task.cancel()
        async with _sessions_lock:
            _sessions -= 1
        event_bus.emit(Event(name="voice.disconnected"))
