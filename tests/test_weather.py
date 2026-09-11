"""Weather widget: keyless backend + dashboard wiring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))



class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeClient:
    payload = {"current": {"temperature_2m": 71.6, "weather_code": 2, "wind_speed_10m": 5.0}}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *, params=None, **kwargs):
        assert "open-meteo.com" in url
        return FakeResponse(payload=dict(FakeClient.payload))


def test_weather_maps_code_and_units(monkeypatch):
    import routes.telemetry as telemetry_route

    monkeypatch.setattr(telemetry_route.httpx, "AsyncClient", FakeClient)
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.get("/v1/weather?lat=40.7&lon=-74.0")
    assert r.status_code == 200
    body = r.json()
    assert body["condition"] == "Partly cloudy" and body["temp_f"] == 71.6
    assert body["display"] == "72°F · Partly cloudy" and body["wind_mph"] == 5.0


def test_weather_rejects_bad_coordinates():
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/v1/weather?lat=999&lon=0").status_code == 422
    assert client.get("/v1/weather?lat=0&lon=999").status_code == 422


def test_weather_upstream_failure_is_502(monkeypatch):
    import routes.telemetry as telemetry_route

    class BadClient(FakeClient):
        async def get(self, url, *, params=None, **kwargs):
            return FakeResponse(status_code=500, payload={})

    monkeypatch.setattr(telemetry_route.httpx, "AsyncClient", BadClient)
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/v1/weather?lat=40&lon=-74").status_code == 502


def test_weather_unreachable_is_502(monkeypatch):
    import httpx
    import routes.telemetry as telemetry_route

    class DeadClient(FakeClient):
        async def get(self, url, *, params=None, **kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(telemetry_route.httpx, "AsyncClient", DeadClient)
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/v1/weather?lat=40&lon=-74").status_code == 502


def test_dashboard_wires_geolocation_widget():
    html = (Path(__file__).resolve().parents[1] / "api-gateway" / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="weatherTemp"' in html and 'id="weatherDesc"' in html
    assert "navigator.geolocation" in html and "/v1/weather?lat=" in html
    assert "Location off" in html
