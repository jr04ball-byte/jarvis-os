"""Lazy, optional Faster Whisper support for bounded push-to-talk clips."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path

_model = None
_model_config = None
_model_lock = threading.Lock()


def _configuration() -> tuple[str, str, str, str | None]:
    model = (os.getenv("WHISPER_MODEL") or "base.en").strip()
    device = (os.getenv("WHISPER_DEVICE") or "auto").strip().lower()
    compute = (os.getenv("WHISPER_COMPUTE_TYPE") or "auto").strip().lower()
    cache = (os.getenv("WHISPER_MODEL_CACHE_DIR") or "").strip() or None
    if device not in {"auto", "cpu", "cuda"}:
        raise RuntimeError("WHISPER_DEVICE must be auto, cpu, or cuda")
    if compute not in {"auto", "int8", "float16", "float32"}:
        raise RuntimeError("unsupported WHISPER_COMPUTE_TYPE")
    return model, device, compute, cache


def _load_model():
    global _model, _model_config
    config = _configuration()
    with _model_lock:
        if _model is not None and _model_config == config:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Local transcription is not installed; install api-gateway/requirements-voice-local.txt"
            ) from exc
        model, device, compute, cache = config
        kwargs = {"device": device, "compute_type": compute}
        if cache:
            kwargs["download_root"] = str(Path(cache).expanduser())
        _model = WhisperModel(model, **kwargs)
        _model_config = config
        return _model


def _transcribe_sync(payload: bytes, suffix: str) -> str:
    model = _load_model()
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as recording:
            recording.write(payload)
            path = recording.name
        segments, _ = model.transcribe(path, beam_size=1, vad_filter=True, language="en")
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


async def transcribe_local(payload: bytes, mime: str) -> str:
    suffixes = {
        "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav",
        "audio/x-wav": ".wav", "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
    }
    return await asyncio.to_thread(_transcribe_sync, payload, suffixes.get(mime, ".audio"))

