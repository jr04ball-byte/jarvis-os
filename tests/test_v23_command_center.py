import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api-gateway"))

from intelligence_router import IntelligenceRouter
from providers.base import ProviderAdapter, ProviderResult
from providers.opencode import OpenCodeProvider
from security import allowed, requires_confirmation, risk
from workspace_registry import is_registered_workspace, target_for_workspace


class _Provider(ProviderAdapter):
    def __init__(self, name: str):
        self.name = name
        self.kind = "local" if name in {"ollama", "opencode"} else "cloud"
        self.supports_local = name in {"ollama", "opencode"}
        self.supports_stream = False
        super().__init__(f"{name}-model")

    async def complete(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
        return ProviderResult("ok", self.name, self.model)

    async def health(self):
        return {"online": True, "configured": True}


def _router(monkeypatch):
    monkeypatch.setenv("JARVIS_CIRCUIT_FAILURES", "2")
    monkeypatch.setenv("JARVIS_CIRCUIT_COOLDOWN_SECONDS", "60")
    return IntelligenceRouter({name: _Provider(name) for name in ("gemini", "openai", "ollama", "opencode")})


def test_v23_circuit_breaker_opens_and_filters(monkeypatch):
    router = _router(monkeypatch)
    router.record_provider_result("gemini", ok=False, elapsed_ms=10, route_reason="test")
    router.record_provider_result("gemini", ok=False, elapsed_ms=12, route_reason="test")
    snapshot = router.snapshot()["circuits"]["gemini"]
    assert snapshot["state"] == "open"
    assert "gemini" not in router.eligible_chain(["gemini", "openai"])
    assert router.eligible_chain(["gemini", "openai"])[0] == "openai"


def test_workspace_guard_is_exact_and_resolves_target(monkeypatch, tmp_path):
    email = tmp_path / "email-agent"
    email.mkdir()
    monkeypatch.setenv("JARVIS_EMAIL_AGENT_PATH", str(email))
    assert is_registered_workspace(str(email)) is True
    assert target_for_workspace(str(email)) == "email_agent"
    assert is_registered_workspace(str(email / "nested")) is False
    assert target_for_workspace(str(email / "nested")) is None


def test_opencode_rejects_bad_permissions_before_network(monkeypatch, tmp_path):
    email = tmp_path / "email-agent"
    email.mkdir()
    monkeypatch.setenv("JARVIS_EMAIL_AGENT_PATH", str(email))
    provider = OpenCodeProvider()
    with pytest.raises(RuntimeError, match="unsupported opencode permission"):
        asyncio.run(provider.complete([], task_context={"workspace": str(email), "permission": "root"}))


def test_security_policy_fails_closed_and_matches_confirmation_boundary():
    assert allowed("computer_status") is True
    assert risk("computer_status") == "read"
    assert requires_confirmation("computer_type") is True
    assert requires_confirmation("computer_move") is False
    assert allowed("totally_unknown_tool") is False
    assert risk("totally_unknown_tool") == "unknown"


def test_dashboard_is_self_contained_and_has_all_command_views():
    html = (ROOT / "api-gateway" / "dashboard.html").read_text(encoding="utf-8")
    for view in ("view-overview", "view-projects", "view-router", "view-autopilot", "view-approvals", "view-memory"):
        assert f'id="{view}"' in html
    assert "/command-center-loop.webm" in html
    assert "JARVIS V23" in html
    assert "https://" not in html


def test_blender_command_center_sources_are_shipped():
    script = ROOT / "tools" / "blender_command_center_bake.py"
    batch = ROOT / "BAKE-JARVIS-DASHBOARD.bat"
    assert script.is_file()
    assert batch.is_file()
    source = script.read_text(encoding="utf-8")
    assert "command-center-loop.webm" in source
    assert "jarvis-command-center.blend" in source


def test_gemini_live_never_returns_permanent_api_key_in_source():
    source = (ROOT / "api-gateway" / "main.py").read_text(encoding="utf-8")
    assert '"token": GEMINI_API_KEY' not in source
    # V24 P2: endpoint config lives in deps.py; the constrained-endpoint
    # pinning moves with it.
    config = (ROOT / "api-gateway" / "deps.py").read_text(encoding="utf-8")
    assert "auth_tokens" in config
    assert "BidiGenerateContentConstrained" in config
    voice = (ROOT / "api-gateway" / "voice-live.html").read_text(encoding="utf-8")
    assert "access_token=" in voice
    assert "run_project_worker" in voice
    assert "/v1/project-worker/autofix" in voice


def test_fastapi_v23_smoke_and_dashboard_assets(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    main = importlib.import_module("main")
    client = TestClient(main.app)

    live = client.get("/health")
    assert live.status_code == 200
    assert live.json()["version"] == "23.0.0"

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "JARVIS V23" in dashboard.text

    godseye = client.get("/godseye")
    assert godseye.status_code == 200

    poster = client.get("/command-center-poster.png")
    assert poster.status_code == 200
    assert poster.headers["content-type"].startswith("image/png")


def test_command_center_overview_is_redacted(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    main = importlib.import_module("main")
    secret = "SERVER_SIDE_SECRET_SENTINEL"
    monkeypatch.setattr(main, "GEMINI_API_KEY", secret)

    async def fake_brains():
        return {
            "providers": {"gemini": {"online": True, "configured": True, "model": "fake"}},
            "telemetry": {"calls": {"gemini": 4}, "failures": {"gemini": 1}},
            "circuits": {},
            "policy": {"primary": "gemini", "authority": "Jarvis owns authority"},
        }

    async def fake_compute(ttl=5.0):
        return {"mode": "test", "gpu": {"available": False}}

    monkeypatch.setattr(main, "brain_status_snapshot", fake_brains)
    monkeypatch.setattr(main, "_cached_compute_snapshot", fake_compute)
    client = TestClient(main.app)
    response = client.get("/v1/command-center/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "23.0.0"
    assert payload["quality"]["provider_success_rate"] == 75.0
    assert "Jarvis owns" in payload["authority"]
    assert secret not in response.text
    assert all("arguments" not in item and "args" not in item for item in payload["approvals"]["items"])


def test_live_token_endpoint_returns_only_ephemeral_credential(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    main = importlib.import_module("main")
    permanent = "PERMANENT_GEMINI_KEY_SENTINEL"
    monkeypatch.setattr(main, "GEMINI_API_KEY", permanent)

    class FakeResponse:
        status_code = 200
        text = '{"name":"ephemeral-test-token"}'
        def json(self):
            return {"name": "ephemeral-test-token", "expireTime": "2099-01-01T00:00:00Z"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, *, headers=None, json=None, **kwargs):
            assert url.endswith("/auth_tokens")
            assert headers.get("x-goog-api-key") == permanent
            assert json["uses"] == 1
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(main.app)
    response = client.post("/v1/gemini/live-token", json={"ttl_minutes": 10})
    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "ephemeral-test-token"
    assert payload["token"] != permanent
    assert payload["token_type"] == "ephemeral"
    assert "BidiGenerateContentConstrained" in payload["ws_url"]
    assert permanent not in response.text


def test_legacy_opencode_write_endpoint_fails_closed():
    import importlib

    from fastapi.testclient import TestClient

    main = importlib.import_module("main")
    client = TestClient(main.app)
    response = client.post("/v1/opencode/task", json={"prompt": "edit code", "mode": "build"})
    assert response.status_code == 409
    assert "project-worker/autofix" in response.json()["detail"]


def test_all_declared_agent_tools_exist_in_security_policy():
    import importlib
    main = importlib.import_module("main")
    names = {
        item.get("function", {}).get("name")
        for item in main.AGENT_TOOLS
        if isinstance(item, dict)
    }
    assert names
    unknown = sorted(name for name in names if name and not allowed(name))
    assert unknown == []
