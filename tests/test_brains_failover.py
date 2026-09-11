"""V24 P6: brains.py failover loop coverage (offline fakes)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import brains
from fastapi import HTTPException
from providers.base import ProviderResult


class FakeProvider:
    def __init__(self, name="fake", fail=False, chunks=None):
        self.name = name
        self.model = f"{name}-model"
        self.configured = True
        self.fail = fail
        self.chunks = chunks or ["hello", " world"]

    async def complete(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        return ProviderResult("".join(self.chunks), self.name, self.model)

    async def stream(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        for chunk in self.chunks:
            yield chunk

    async def health(self):
        return {"online": not self.fail, "configured": True}


class StubDecision:
    def __init__(self, chain):
        self.selected = chain[0] if chain else None
        self.requested = "auto"
        self.reason = "test"
        self.chain = chain

    def as_dict(self):
        return {"selected": self.selected}


class StubCircuit:
    def snapshot(self):
        return {}


class StubRouter:
    def __init__(self, chain):
        self.chain = chain
        self.recorded = []
        self.circuit = StubCircuit()

    def decide(self, *args, **kwargs):
        return StubDecision(self.chain)

    def eligible_chain(self, chain):
        return list(chain)

    def provider_context(self, *args, **kwargs):
        return {}

    def record_provider_result(self, name, **kwargs):
        self.recorded.append((name, kwargs.get("ok")))


def make_request(text="hi"):
    return brains.BrainRequest(messages=[brains.Message(role="user", content=text)])


def test_complete_success_records_attempt(monkeypatch):
    monkeypatch.setattr(brains, "providers", {"fake": FakeProvider()})
    monkeypatch.setattr(brains, "router_engine", StubRouter(["fake"]))
    out = asyncio.run(brains.complete(make_request()))
    assert out["provider"] == "fake" and out["message"]["content"] == "hello world"
    assert out["attempts"] == [{"provider": "fake", "ok": True, "elapsed_ms": out["attempts"][0]["elapsed_ms"]}]


def test_complete_fails_over_to_next_provider(monkeypatch):
    monkeypatch.setattr(brains, "providers", {"bad": FakeProvider("bad", fail=True), "good": FakeProvider("good")})
    router = StubRouter(["bad", "good"])
    monkeypatch.setattr(brains, "router_engine", router)
    out = asyncio.run(brains.complete(make_request()))
    assert out["provider"] == "good"
    assert [a["provider"] for a in out["attempts"]] == ["bad", "good"]
    assert router.recorded == [("bad", False), ("good", True)]


def test_complete_all_fail_raises_502(monkeypatch):
    monkeypatch.setattr(brains, "providers", {"bad": FakeProvider("bad", fail=True)})
    monkeypatch.setattr(brains, "router_engine", StubRouter(["bad"]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(brains.complete(make_request()))
    assert exc.value.status_code == 502


def test_complete_empty_chain_raises_503(monkeypatch):
    monkeypatch.setattr(brains, "providers", {})
    monkeypatch.setattr(brains, "router_engine", StubRouter([]))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(brains.complete(make_request()))
    assert exc.value.status_code == 503


def test_complete_skips_unconfigured_provider(monkeypatch):
    off = FakeProvider("off")
    off.configured = False
    monkeypatch.setattr(brains, "providers", {"off": off, "on": FakeProvider("on")})
    monkeypatch.setattr(brains, "router_engine", StubRouter(["off", "on"]))
    out = asyncio.run(brains.complete(make_request()))
    assert out["provider"] == "on"


def test_stream_happy_path_yields_brain_and_done(monkeypatch):
    monkeypatch.setattr(brains, "providers", {"fake": FakeProvider()})
    monkeypatch.setattr(brains, "router_engine", StubRouter(["fake"]))

    async def collect():
        return [chunk async for chunk in brains.stream(make_request())]

    chunks = asyncio.run(collect())
    text = "".join(chunks)
    assert '"brain"' in text and "data: [DONE]" in chunks[-1]


def test_stream_all_fail_yields_error_event(monkeypatch):
    monkeypatch.setattr(brains, "providers", {"bad": FakeProvider("bad", fail=True)})
    monkeypatch.setattr(brains, "router_engine", StubRouter(["bad"]))

    async def collect():
        return [chunk async for chunk in brains.stream(make_request())]

    chunks = asyncio.run(collect())
    assert any("all eligible brains failed" in c for c in chunks)
