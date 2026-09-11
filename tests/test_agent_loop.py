"""V24 P7: agent loop / stream / autofix integration coverage (fakes + fixtures)."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import services
from fastapi import HTTPException
from schemas import ChatMessage, ChatRequest


def tool_call(name, args=None):
    return {"function": {"name": name, "arguments": json.dumps(args or {})}}


def chat_response(message):
    return {"message": message}


class FakePostClient:
    """Canned /api/chat POST responses in order."""

    def __init__(self, responses, status_code=200):
        self.responses = list(responses)
        self.status_code = status_code
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, json=None, **kwargs):
        self.posts.append(json)
        assert url.endswith("/api/chat")
        data = self.responses.pop(0) if self.responses else {"content": "done"}

        class FakeResponse:
            status_code = self.status_code

            def json(self):
                return data

            @property
            def text(self):
                return "err"

        return FakeResponse()


def patch_post(monkeypatch, responses):
    class FakeClientFactory:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return FakePostClient(responses)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClientFactory)


def test_agent_loop_plain_answer(monkeypatch):
    patch_post(monkeypatch, [chat_response({"content": "hello"})])
    out = asyncio.run(services._run_agent_loop("m", [{"role": "user", "content": "hi"}], "general", None, 3, None))
    assert out["message"]["content"] == "hello" and out["tool_rounds"] == 0


def test_agent_loop_executes_safe_tool_then_answers(monkeypatch):
    patch_post(monkeypatch, [
        chat_response({"content": "", "tool_calls": [tool_call("connections_inventory")]}),
        chat_response({"content": "final"}),
    ])
    out = asyncio.run(services._run_agent_loop("m", [{"role": "user", "content": "hi"}], "general", None, 3, None))
    assert out["message"]["content"] == "final" and out["tool_rounds"] == 1


def test_agent_loop_sensitive_action_needs_confirmation(monkeypatch):
    patch_post(monkeypatch, [
        chat_response({"content": "", "tool_calls": [tool_call("computer_type", {"text": "hi"})]}),
    ])
    out = asyncio.run(services._run_agent_loop("m", [{"role": "user", "content": "hi"}], "general", None, 3, None))
    assert "confirmation" in out and out["confirmation"]["status"] == "confirmation_required"
    # saved resume state round-trips through the ticket
    state = services._consume_confirmation(out["confirmation"]["confirmation_id"])
    assert state["tool"] == "computer_type"
    assert state["resume"]["model"] == "m" and state["resume"]["tool_rounds"] == 0


def test_agent_loop_tool_limit_raises(monkeypatch):
    patch_post(monkeypatch, [chat_response({"content": "", "tool_calls": [tool_call("connections_inventory")]})] * 4)
    with pytest.raises(HTTPException):
        asyncio.run(services._run_agent_loop("m", [{"role": "user", "content": "hi"}], "general", None, 1, None))


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStreamClient:
    LINES = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        outer = self

        class StreamCtx:
            async def __aenter__(self):
                return FakeStreamResponse(outer.LINES)

            async def __aexit__(self, *args):
                return False

        return StreamCtx()


def test_stream_chat_happy_path(monkeypatch):
    FakeStreamClient.LINES = [
        json.dumps({"message": {"content": "hel"}}),
        json.dumps({"message": {"content": "lo"}}),
        json.dumps({"done": True}),
    ]
    monkeypatch.setattr(services.httpx, "AsyncClient", FakeStreamClient)
    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])

    async def collect():
        return [chunk async for chunk in services.stream_chat(req)]

    chunks = asyncio.run(collect())
    assert any('"content": "hel"' in c for c in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_chat_non_200_yields_error(monkeypatch):
    class BadResponse(FakeStreamResponse):
        status_code = 500

    class BadClient(FakeStreamClient):
        def stream(self, *args, **kwargs):
            outer = self

            class StreamCtx:
                async def __aenter__(self):
                    return BadResponse([])

                async def __aexit__(self, *args):
                    return False

            return StreamCtx()

    monkeypatch.setattr(services.httpx, "AsyncClient", BadClient)
    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])

    async def collect():
        return [chunk async for chunk in services.stream_chat(req)]

    chunks = asyncio.run(collect())
    assert any("error" in c for c in chunks) and chunks[-1] == "data: [DONE]\n\n"
