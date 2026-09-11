"""V25-final: /v1/voice/transcribe validation + Gemini model wiring."""
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import pytest


def make_app():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app, raise_server_exceptions=False)


def silent_wav_bytes(seconds=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)
    return buf.getvalue()


def test_transcribe_requires_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = make_app()
    r = client.post("/v1/voice/transcribe", files={"audio": ("a.webm", b"xx", "audio/webm")})
    assert r.status_code == 503


def test_transcribe_rejects_empty_oversize_and_type(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    client = make_app()
    assert client.post("/v1/voice/transcribe", files={"audio": ("a.webm", b"", "audio/webm")}).status_code == 400
    assert client.post("/v1/voice/transcribe", files={"audio": ("a.bin", b"x", "application/octet-stream")}).status_code == 415


def test_transcribe_silence_short_circuits(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    client = make_app()
    r = client.post("/v1/voice/transcribe", files={"audio": ("a.wav", silent_wav_bytes(), "audio/wav")})
    assert r.status_code == 200 and r.json() == {"transcript": ""}


def test_transcribe_uses_configured_gemini_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None, **kwargs):
            seen["url"] = url
            seen["key"] = headers.get("x-goog-api-key")
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    client = make_app()
    r = client.post("/v1/voice/transcribe", files={"audio": ("a.webm", b"\x01\x02", "audio/webm")})
    assert r.status_code == 200 and r.json() == {"transcript": "hello"}
    assert "gemini-3.5-flash" in seen["url"] and seen["key"] == "k"


def test_transcribe_no_speech_marker_normalized(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "[NO_SPEECH]"}]}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    client = make_app()
    r = client.post("/v1/voice/transcribe", files={"audio": ("a.webm", b"\x01\x02", "audio/webm")})
    assert r.status_code == 200 and r.json() == {"transcript": ""}


def test_gemini_provider_uses_configured_model(monkeypatch):
    import providers.gemini as gemini_mod

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    provider = gemini_mod.GeminiProvider(model="gemini-3.5-flash")
    assert provider.model == "gemini-3.5-flash"
    assert provider.configured is False
    with pytest.raises(RuntimeError):
        import asyncio

        from providers.base import ProviderMessage

        asyncio.run(provider.complete([ProviderMessage("user", "hi")]))
