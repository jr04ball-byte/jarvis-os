"""V24 P6: services.py unit coverage (offline, no network/servers)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import services
from fastapi import HTTPException
from schemas import DEEP_MODEL, FAST_MODEL, TOOL_MODEL
from services import (
    _artifact_read,
    _configuration_snapshot,
    _consume_confirmation,
    _create_confirmation,
    _pending_snapshot,
    apply_system_prompt,
    build_tools_list,
    create_artifact,
    execute_tool_core,
    select_agent_model,
    select_model,
    web_search,
)


def test_select_model_explicit_passthrough():
    assert select_model("qwen3.5:9b", [{"role": "user", "content": "hi"}], "general") == "qwen3.5:9b"


def test_select_model_auto_fast_and_deep():
    assert select_model("auto", [{"role": "user", "content": "Tell me a joke"}], "general") == FAST_MODEL
    assert select_model("auto", [{"role": "user", "content": "Explain Raft consensus vs Paxos"}], "general") == DEEP_MODEL


def test_select_model_ignores_injected_system_prompt_and_sales():
    injected = [{"role": "system", "content": "code code code"}, {"role": "user", "content": "hi"}]
    assert select_model("auto", injected, "general") == FAST_MODEL
    assert select_model("auto", [{"role": "user", "content": "hi"}], "sales") == FAST_MODEL


def test_select_agent_model_prefers_tool_capable():
    assert select_agent_model("auto") == TOOL_MODEL
    assert select_agent_model("gemma3:4b") == TOOL_MODEL
    assert select_agent_model("qwen3:3b") == "qwen3:3b"


def test_apply_system_prompt_injects_once():
    from schemas import ChatMessage

    msgs = apply_system_prompt([ChatMessage(role="user", content="hi")], "general")
    assert msgs[0].role == "system" and msgs[1].content == "hi"
    again = apply_system_prompt([ChatMessage(role="system", content="x"), ChatMessage(role="user", content="hi")], "general")
    assert again[0].content.startswith("Jarvis") or "application instructions" in again[0].content


def test_build_tools_list_shape():
    items = build_tools_list()
    assert items
    assert all({"name", "description", "confirmation"} <= set(i) for i in items)
    assert all(isinstance(i["confirmation"], bool) for i in items)


def test_create_and_read_artifact_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(services, "ARTIFACTS_DIR", tmp_path)
    item = create_artifact("t", "markdown", "hello", {"a": 1})
    assert item["id"] and item["content"] == "hello"
    assert _artifact_read(item["id"])["metadata"] == {"a": 1}
    with pytest.raises(HTTPException):
        _artifact_read("does-not-exist")


def test_confirmation_single_use_and_redacted_snapshot():
    ticket = _create_confirmation("computer_type", {"text": "hi"}, "why")
    assert ticket["status"] == "confirmation_required"
    item = _consume_confirmation(ticket["confirmation_id"])
    assert item["tool"] == "computer_type"
    with pytest.raises(HTTPException):
        _consume_confirmation(ticket["confirmation_id"])
    snap = _pending_snapshot()
    assert "arguments" not in str(snap["items"])


def test_connection_overrides_round_trip(monkeypatch, tmp_path):
    from services import _load_connection_overrides, _save_connection_overrides

    monkeypatch.setattr(services, "CONNECTIONS_PATH", tmp_path / "connections.json")
    _save_connection_overrides({"ollama": {"enabled": False}})
    assert _load_connection_overrides() == {"ollama": {"enabled": False}}


def test_execute_tool_core_validation_paths():
    import asyncio

    async def run():
        with pytest.raises(HTTPException):
            await execute_tool_core("nope_nothing", {}, True)
        # unconfirmed risky tool -> confirmation payload, not execution
        pending = await execute_tool_core("computer_type", {"text": "hi"}, False)
        assert pending["status"] == "confirmation_required"
        # gmail without account
        with pytest.raises(HTTPException):
            await execute_tool_core("gmail_search", {}, True)

    asyncio.run(run())


def test_execute_tool_core_rejects_unconnected_account(monkeypatch):
    import asyncio

    class EmptyStore:
        def list_accounts(self):
            return []

    monkeypatch.setattr(services.google_oauth, "TokenStore", lambda: EmptyStore())

    async def run():
        with pytest.raises(HTTPException):
            await execute_tool_core("gmail_search", {"email": "a@b.c"}, True)

    asyncio.run(run())


def test_web_search_unconfigured(monkeypatch):
    import asyncio

    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(services, "EXA_API_KEY", "")

    async def run():
        out = await web_search("hello")
        assert out["configured"] is False

    asyncio.run(run())


def test_configuration_snapshot_reports_warnings():
    snap = _configuration_snapshot()
    assert isinstance(snap, dict) and isinstance(snap["warnings"], list)


def test_connection_snapshot_returns_mapping():
    import asyncio

    from services import _connection_snapshot

    async def run():
        return await _connection_snapshot()

    snap = asyncio.run(run())
    assert isinstance(snap, dict)
