import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from intelligence_router import IntelligenceRouter
from providers.base import ProviderAdapter, ProviderResult


class FakeProvider(ProviderAdapter):
    def __init__(self, name: str, *, fail: bool = False):
        self.name = name
        self.kind = "local" if name == "ollama" else "cloud"
        self.supports_local = name in {"ollama", "opencode"}
        self.supports_stream = False
        self.fail = fail
        super().__init__(f"{name}-model")

    async def complete(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return ProviderResult("ok", self.name, self.model)

    async def health(self):
        return {"online": not self.fail, "configured": True}


def make_router():
    providers = {name: FakeProvider(name) for name in ("gemini", "openai", "ollama", "opencode")}
    return IntelligenceRouter(providers)


def test_normal_tasks_stay_on_gemini_main_brain(monkeypatch):
    monkeypatch.setenv("JARVIS_PRIMARY_BRAIN", "gemini")
    router = make_router()
    decision = router.decide("What meetings do I have today?")
    assert decision.selected == "gemini"
    assert decision.chain[0] == "gemini"


def test_deep_reasoning_escalates_to_openai(monkeypatch):
    monkeypatch.setenv("JARVIS_DEEP_BRAIN", "openai")
    router = make_router()
    decision = router.decide("Do a deep architecture review of this distributed system")
    assert decision.selected == "openai"
    assert decision.assessment.complexity >= 8


def test_coding_routes_to_opencode_worker(monkeypatch):
    monkeypatch.setenv("JARVIS_CODING_BRAIN", "opencode")
    router = make_router()
    decision = router.decide("Fix the repository and add integration tests")
    assert decision.selected == "opencode"
    assert decision.assessment.coding >= 8


def test_private_mode_is_local_only(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_BRAIN", "ollama")
    router = make_router()
    decision = router.decide("Analyze this confidential file", requested="private")
    assert decision.selected == "ollama"
    assert decision.chain == ["ollama"]
    assert decision.assessment.privacy == 10


def test_fast_mode_prefers_ollama(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_BRAIN", "ollama")
    router = make_router()
    decision = router.decide("Quickly summarize this", requested="fast")
    assert decision.selected == "ollama"
    assert decision.assessment.speed_priority == 10


def test_explicit_provider_request_is_respected():
    router = make_router()
    decision = router.decide("hello", requested="openai")
    assert decision.selected == "openai"
    assert decision.chain[0] == "openai"


def test_router_snapshot_declares_jarvis_authority():
    router = make_router()
    snapshot = router.snapshot()
    assert "Jarvis owns" in snapshot["policy"]["authority"]
    assert set(snapshot["providers"]) == {"gemini", "openai", "ollama", "opencode"}
