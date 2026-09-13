"""Regression tests for confirmation/resume state in the agent service layer."""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
API = ROOT / "api-gateway"
sys.path.insert(0, str(API))


def test_agent_service_module_parses_and_keeps_resume_contract():
    source = (API / "services.py").read_text(encoding="utf-8")
    ast.parse(source)
    for needle in ("_save_confirmation_state", "_run_agent_loop", "resume", "assistant_profile"):
        assert needle in source


def test_agent_confirm_resume_tool_limit_is_graceful(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from routes import workers
    import main

    def fake_consume(confirmation_id):
        return {
            "tool": "computer_open",
            "arguments": {"target": "calculator"},
            "resume": {
                "model": "m",
                "messages": [{"role": "user", "content": "open calculator"}],
                "assistant_profile": "general",
                "conversation_id": None,
                "max_tool_rounds": 5,
                "tool_rounds": 0,
                "remaining_calls": [],
                "tool_call_id": "call-1",
            },
        }

    async def fake_execute_core(tool, arguments, confirmed):
        return {"ok": True, "tool": tool, "confirmed": confirmed}

    async def boom(*args, **kwargs):
        raise HTTPException(500, "agent reached its tool-call limit")

    monkeypatch.setattr(workers, "_consume_confirmation", fake_consume)
    monkeypatch.setattr(workers, "execute_tool_core", fake_execute_core)
    monkeypatch.setattr(workers, "_run_agent_loop", boom)
    monkeypatch.setattr(main, "AI_REQUIRE_AUTH", False)
    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.post("/v1/agent/confirm", json={"confirmation_id": "x" * 24, "confirmed": True})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert data["approval_executed"] is True
