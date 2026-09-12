import io
import sys
import wave
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api-gateway"))


def client():
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_duplex_disabled_by_default(monkeypatch):
    monkeypatch.setenv("VOICE_DUPLEX_ENABLED", "false")
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect("/v1/voice/duplex"):
            pass


def test_duplex_handshake_and_cleanup(monkeypatch):
    monkeypatch.setenv("VOICE_DUPLEX_ENABLED", "true")
    with client().websocket_connect("/v1/voice/duplex", headers={"origin": "http://127.0.0.1:8000"}) as ws:
        assert ws.receive_json()["event"] == "ready"


def test_duplex_rejects_foreign_origin(monkeypatch):
    monkeypatch.setenv("VOICE_DUPLEX_ENABLED", "true")
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect("/v1/voice/duplex", headers={"origin": "https://evil.example"}):
            pass


def test_pcm_helpers():
    from routes.voice_duplex import _level, _wav
    pcm = (b"\xff\x7f" * 512)
    assert _level(pcm) > 0.9
    with wave.open(io.BytesIO(_wav(pcm)), "rb") as recording:
        assert recording.getframerate() == 16000
        assert recording.getnchannels() == 1
