"""Optional Kokoro ONNX speech synthesis for local voice mode."""
from __future__ import annotations
import asyncio
import os
import threading
import numpy as np

_engine = None
_config = None
_lock = threading.Lock()


def _load():
    global _engine, _config
    model = os.getenv("KOKORO_MODEL_PATH", "").strip()
    voices = os.getenv("KOKORO_VOICES_PATH", "").strip()
    if not model or not voices:
        raise RuntimeError("Kokoro model paths are not configured")
    config = (model, voices)
    with _lock:
        if _engine is not None and _config == config:
            return _engine
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise RuntimeError("Install api-gateway/requirements-voice-duplex.txt") from exc
        _engine = Kokoro(model, voices)
        _config = config
        return _engine


def _synthesize(text: str) -> tuple[bytes, int]:
    voice = os.getenv("KOKORO_VOICE", "af_heart")
    speed = float(os.getenv("KOKORO_SPEED", "1.0"))
    samples, sample_rate = _load().create(text, voice=voice, speed=speed, lang="en-us")
    audio = np.clip(np.asarray(samples, dtype=np.float32), -1, 1)
    return (audio * 32767).astype("<i2").tobytes(), int(sample_rate)


async def synthesize_local(text: str) -> tuple[bytes, int]:
    return await asyncio.to_thread(_synthesize, text)

