import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1] / "api-gateway"
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_REQUIRE_AUTH", "false")
    monkeypatch.setenv("LIVEKIT_URL", "")
    monkeypatch.setenv("LIVEKIT_API_KEY", "")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "")
    monkeypatch.setenv("JARVIS_LIVEKIT_ENABLED", "false")
    for name in ["main", "routes.livekit_voice"]:
        sys.modules.pop(name, None)
    app = importlib.import_module("main").create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_livekit_status_redacts_credentials(client):
    response = client.get("/v1/livekit/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert "secret" not in response.text.lower()


def test_livekit_token_disabled(client):
    response = client.post("/v1/livekit/token", json={"display_name": "Jerry"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Live Conversation is disabled"


def test_livekit_page_is_served(client):
    response = client.get("/voice-livekit")
    assert response.status_code == 200
    assert "Start Live Conversation" in response.text
    assert "camera" not in response.text.lower()
