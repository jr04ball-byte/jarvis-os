"""V24 P8: auth-gate matrix (Objective 11). Timing-safe compare included."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))


def test_protected_paths_require_bearer_token(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "AI_REQUIRE_AUTH", True)
    monkeypatch.setattr(main, "AI_API_TOKEN", "s3cret")
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/v1/tools").status_code == 401
    assert client.get("/v1/tools", headers={"authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/tools", headers={"authorization": "Bearer s3cret"}).status_code == 200


def test_health_and_dashboard_stay_public(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "AI_REQUIRE_AUTH", True)
    monkeypatch.setattr(main, "AI_API_TOKEN", "s3cret")
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/health").status_code == 200
    assert client.get("/dashboard").status_code == 200


def test_auth_open_by_default(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "AI_REQUIRE_AUTH", False)
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/v1/tools").status_code == 200
