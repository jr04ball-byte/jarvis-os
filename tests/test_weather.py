import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

GATEWAY = Path(__file__).resolve().parents[1] / "api-gateway"
if str(GATEWAY) not in sys.path:
    sys.path.insert(0, str(GATEWAY))


def _client(monkeypatch):
    monkeypatch.setenv("AI_REQUIRE_AUTH", "false")
    for name in ["main", "deps"]:
        sys.modules.pop(name, None)
    return TestClient(importlib.import_module("main").create_app())


def test_weather_current_maps_provider_response(monkeypatch):
    weather = importlib.import_module("routes.weather")
    weather._CACHE.clear()
    replies = iter([
        {"results": [{"name": "Spokane", "admin1": "Washington", "latitude": 47.66, "longitude": -117.43}]},
        {"current": {"temperature_2m": 42.4, "apparent_temperature": 39.2, "weather_code": 2, "is_day": 0, "time": "2026-09-12T17:00"}},
    ])

    async def fake_get(url, params):  # noqa: ARG001
        return next(replies)

    monkeypatch.setattr(weather, "_get_json", fake_get)
    with _client(monkeypatch) as client:
        response = client.get("/v1/weather/current?location=Spokane%2C%20WA")
    assert response.status_code == 200
    assert response.json() == {
        "location": "Spokane", "region": "Washington", "temperature_f": 42,
        "apparent_f": 39, "condition": "Partly cloudy", "weather_code": 2,
        "is_day": False, "observed_at": "2026-09-12T17:00", "source": "Open-Meteo",
    }


def test_weather_location_not_found(monkeypatch):
    weather = importlib.import_module("routes.weather")
    weather._CACHE.clear()

    async def fake_get(url, params):  # noqa: ARG001
        return {"results": []}

    monkeypatch.setattr(weather, "_get_json", fake_get)
    with _client(monkeypatch) as client:
        response = client.get("/v1/weather/current?location=Nowhereville")
    assert response.status_code == 404
