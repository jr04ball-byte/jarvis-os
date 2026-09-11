"""V25 budget guards: daily caps keep cloud spend predictable."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import budgets
from budgets import DailyBudget, normalize_usage
from fastapi import HTTPException


def make_budget(tmp_path, monkeypatch, limit="1000"):
    monkeypatch.setenv("JARVIS_DAILY_TOKEN_BUDGET", limit)
    return DailyBudget(db_path=str(tmp_path / "budgets.db"))


def test_normalize_usage_shapes():
    assert normalize_usage({"promptTokenCount": 10, "candidatesTokenCount": 5}) == (10, 5)
    assert normalize_usage({"prompt_tokens": 7, "completion_tokens": 3}) == (7, 3)
    assert normalize_usage(None) == (0, 0)
    assert normalize_usage({"prompt_tokens": "x"}) == (0, 0)


def test_record_check_remaining_cycle(tmp_path, monkeypatch):
    budget = make_budget(tmp_path, monkeypatch)
    assert budget.remaining() == 1000
    budget.record("gemini", "cloud", {"promptTokenCount": 600, "candidatesTokenCount": 400})
    assert budget.spent_today() == 1000
    assert budget.remaining() == 0
    with pytest.raises(HTTPException) as exc:
        budget.check("gemini", "cloud")
    assert exc.value.status_code == 429


def test_local_kinds_never_gated(tmp_path, monkeypatch):
    budget = make_budget(tmp_path, monkeypatch, limit="1")
    budget.record("gemini", "cloud", {"promptTokenCount": 1, "candidatesTokenCount": 0})
    # local + worker kinds are free: no raise, no ledger write
    budget.check("ollama", "local")
    budget.record("ollama", "local", {"prompt_tokens": 999999, "completion_tokens": 0})
    assert budget.spent_today() == 1


def test_zero_budget_disables_enforcement(tmp_path, monkeypatch):
    budget = make_budget(tmp_path, monkeypatch, limit="0")
    assert budget.remaining() is None
    budget.check("gemini", "cloud")


def test_exhausted_budget_blocks_before_provider_call(monkeypatch, tmp_path):
    import brains
    from providers.base import ProviderResult

    budget = make_budget(tmp_path, monkeypatch, limit="10")
    budget.record("gemini", "cloud", {"promptTokenCount": 10, "candidatesTokenCount": 0})
    monkeypatch.setattr(brains, "token_budget", budget)

    called = []

    class FakeProvider:
        name = "gemini"
        kind = "cloud"
        model = "gemini-3.5-flash"
        configured = True

        async def complete(self, messages, **kwargs):
            called.append(True)
            return ProviderResult("x", self.name, self.model)

        async def health(self):
            return {"online": True, "configured": True}

    class StubDecision:
        selected = "gemini"
        requested = "auto"
        reason = "test"
        chain = ["gemini"]

        def as_dict(self):
            return {"selected": self.selected}

    class StubRouter:
        def decide(self, *a, **k):
            return StubDecision()

        def eligible_chain(self, chain):
            return list(chain)

        def provider_context(self, *a, **k):
            return {}

        def record_provider_result(self, *a, **k):
            pass

    monkeypatch.setattr(brains, "providers", {"gemini": FakeProvider()})
    monkeypatch.setattr(brains, "router_engine", StubRouter())
    req = brains.BrainRequest(messages=[brains.Message(role="user", content="hi")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(brains.complete(req))
    assert exc.value.status_code == 429
    assert called == []


def test_successful_call_records_usage(monkeypatch, tmp_path):
    import brains
    from providers.base import ProviderResult

    budget = make_budget(tmp_path, monkeypatch)
    monkeypatch.setattr(brains, "token_budget", budget)

    class FakeProvider:
        name = "gemini"
        kind = "cloud"
        model = "gemini-3.5-flash"
        configured = True

        async def complete(self, messages, **kwargs):
            return ProviderResult("hi", self.name, self.model,
                                  usage={"promptTokenCount": 20, "candidatesTokenCount": 5})

        async def health(self):
            return {"online": True, "configured": True}

    class StubDecision:
        selected = "gemini"
        requested = "auto"
        reason = "test"
        chain = ["gemini"]

        def as_dict(self):
            return {"selected": self.selected}

    class StubRouter:
        def decide(self, *a, **k):
            return StubDecision()

        def eligible_chain(self, chain):
            return list(chain)

        def provider_context(self, *a, **k):
            return {}

        def record_provider_result(self, *a, **k):
            pass

    monkeypatch.setattr(brains, "providers", {"gemini": FakeProvider()})
    monkeypatch.setattr(brains, "router_engine", StubRouter())
    req = brains.BrainRequest(messages=[brains.Message(role="user", content="hi")])
    out = asyncio.run(brains.complete(req))
    assert out["provider"] == "gemini"
    assert budget.spent_today() == 25


def test_transcribe_enforces_budget(monkeypatch, tmp_path):
    import main
    from fastapi.testclient import TestClient
    from routes import voice as voice_route

    budget = DailyBudget(db_path=str(tmp_path / "budgets.db"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("JARVIS_DAILY_TOKEN_BUDGET", "10")
    monkeypatch.setattr(voice_route, "token_budget", budget)
    budget.record("gemini", "cloud", {"promptTokenCount": 10, "candidatesTokenCount": 0})
    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.post("/v1/voice/transcribe", files={"audio": ("a.webm", b"\x01", "audio/webm")})
    assert r.status_code == 429


def test_budget_resets_each_utc_day(monkeypatch, tmp_path):
    budget = make_budget(tmp_path, monkeypatch)
    budget.record("gemini", "cloud", {"promptTokenCount": 1000, "candidatesTokenCount": 0})
    assert budget.remaining() == 0
    monkeypatch.setattr(budgets, "_today", lambda: "2999-01-01")
    assert budget.remaining() == 1000
