"""Current weather for the PC dashboard using Open-Meteo's public API."""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_SECONDS = 600
_WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy",
    45: "Fog", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
    82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Severe thunderstorm",
}


async def _get_json(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


@router.get("/v1/weather/current")
async def current_weather(location: str = Query(default="Spokane, WA", min_length=2, max_length=100)):
    normalized = " ".join(location.split())
    key = normalized.casefold()
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        places = await _get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": normalized, "count": 1, "language": "en", "format": "json"},
        )
        matches = places.get("results") or []
        if not matches:
            raise HTTPException(404, f"Weather location not found: {normalized}")
        place = matches[0]
        forecast = await _get_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,is_day",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
            },
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise HTTPException(502, "Weather provider is temporarily unavailable") from error

    current = forecast.get("current") or {}
    code = int(current.get("weather_code", -1))
    region = place.get("admin1") or place.get("country") or ""
    result = {
        "location": place.get("name", normalized),
        "region": region,
        "temperature_f": round(float(current["temperature_2m"])),
        "apparent_f": round(float(current.get("apparent_temperature", current["temperature_2m"]))),
        "condition": _WMO.get(code, "Current conditions"),
        "weather_code": code,
        "is_day": bool(current.get("is_day", 1)),
        "observed_at": current.get("time"),
        "source": "Open-Meteo",
    }
    _CACHE[key] = (time.monotonic(), result)
    return result
