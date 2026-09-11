"""V24 P2: telemetry routes (moved verbatim from main.py)."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from brains import status as brain_status_snapshot
from compute_manager import snapshot as compute_snapshot
from deps import (
    APP_VERSION,
    ARTIFACTS_DIR,
    JARVIS_STARTED_AT,
    OLLAMA_URL,
    RECENT_REQUESTS,
    db,
    limiter,
    monitor,
    orchestrator,
    project_worker_runs,
    rag,
)
from events import Event
from events import bus as event_bus
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from security import policy_snapshot as security_policy_snapshot
from services import (
    _PENDING_ACTIONS,
    _PENDING_LOCK,
    _PENDING_TTL_SECONDS,
    _activity_core,
    _cached_compute_snapshot,
    _cached_target_health,
    _configuration_snapshot,
    _maintenance_worker,
    _pending_snapshot,
    build_tools_list,
)
from telemetry_collector import collector
from workspace_registry import snapshot as workspace_snapshot

import tools

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get('/v1/activity')
async def activity(request: Request, after: int = 0):
    return _activity_core(request).snapshot(after)


@router.get('/v1/maintenance')
async def maintenance_status(request: Request):
    return dict(_maintenance_worker(request).status)


@router.get("/v1/system/status")
async def system_status():
    """Return safe, dashboard-friendly service health without exposing secrets."""
    out = {
        "api": {"status": "online", "version": APP_VERSION},
        "ollama": {"status": "unknown", "model": None},
        "home_assistant": {"status": "not_configured"},
        "host_bridge": {"status": "not_configured"},
        "tools_count": 0,
        "pending_count": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                data = r.json()
                out["ollama"]["status"] = "online"
                models = data.get("models") or []
                out["ollama"]["model"] = models[0].get("name") if models else None
            else:
                out["ollama"]["status"] = "unhealthy"
    except Exception:
        out["ollama"]["status"] = "unreachable"
    try:
        out["tools_count"] = len(build_tools_list())
    except Exception as exc:
        logger.debug("tools_count probe failed: %s", exc)
    if tools.HA_URL and tools.HA_TOKEN:
        try:
            await tools.ha_states()
            out["home_assistant"]["status"] = "connected"
        except Exception:
            out["home_assistant"]["status"] = "unreachable"
    if tools.HOST_BRIDGE_URL and tools.HOST_BRIDGE_TOKEN:
        out["host_bridge"]["status"] = "configured"
    with _PENDING_LOCK:
        now = time.time()
        out["pending_count"] = sum(1 for v in _PENDING_ACTIONS.values() if now - v["created_at"] <= _PENDING_TTL_SECONDS)
    return out


@router.get("/v1/command-center/overview")
@limiter.limit("30/minute")
async def command_center_overview(request: Request):
    """Single safe telemetry payload for the V23 dashboard. Never returns secrets or approval arguments."""
    brains_task = asyncio.create_task(brain_status_snapshot())
    compute_task = asyncio.create_task(_cached_compute_snapshot())

    targets = workspace_snapshot()
    health_tasks = [asyncio.create_task(_cached_target_health(target)) for target in targets]
    health_values = await asyncio.gather(*health_tasks, return_exceptions=True)
    project_health_items: list[dict[str, Any]] = []
    for target, health in zip(targets, health_values):
        if isinstance(health, Exception):
            health = {"available": bool(target.get("path_exists")), "status": "error", "blockers": [str(health)[:500]]}
        project_health_items.append({**target, "health": health})

    try:
        brains = await brains_task
    except Exception as exc:
        brains = {"providers": {}, "telemetry": {}, "circuits": {}, "error": str(exc)[:500]}
    try:
        compute = await compute_task
    except Exception as exc:
        compute = {"error": str(exc)[:500]}

    projects = orchestrator.list_projects(limit=12)
    worker_runs = project_worker_runs.list(limit=12)
    pending = _pending_snapshot()
    try:
        conversation_count = len(db.list_conversations())
    except Exception:
        conversation_count = 0
    try:
        rag_count = len(rag.documents)
    except Exception:
        rag_count = 0
    try:
        artifact_count = len(list(ARTIFACTS_DIR.glob("*.json")))
    except Exception:
        artifact_count = 0
    telemetry = brains.get("telemetry") or {}
    calls = telemetry.get("calls") or {}
    failures = telemetry.get("failures") or {}
    total_calls = sum(int(v or 0) for v in calls.values())
    total_failures = sum(int(v or 0) for v in failures.values())
    success_rate = round(((total_calls - total_failures) / total_calls) * 100, 1) if total_calls else 100.0

    return {
        "version": APP_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int(max(0, time.time() - JARVIS_STARTED_AT)),
        "configuration": _configuration_snapshot(),
        "brains": brains, "compute": compute, "projects": projects, "worker_runs": worker_runs,
        "targets": project_health_items, "approvals": pending,
        "memory": {"conversations": conversation_count, "rag_documents": rag_count, "artifacts": artifact_count},
        "security": security_policy_snapshot(),
        "requests": list(RECENT_REQUESTS)[-24:],
        "quality": {
            "provider_calls": total_calls, "provider_failures": total_failures, "provider_success_rate": success_rate,
            "verified_worker_runs": sum(1 for run in worker_runs if run.get("status") == "verified"),
            "blocked_worker_runs": sum(1 for run in worker_runs if str(run.get("status", "")).startswith("blocked")),
        },
        "authority": "Jarvis owns routing, memory, permissions, execution and verification.",
    }


@router.get("/v1/system/compute")
async def system_compute():
    return await compute_snapshot()


# WMO weather-code mapping for the keyless Open-Meteo feed.
WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
    96: "Thunderstorm + hail", 99: "Thunderstorm + hail",
}


@router.get("/v1/weather")
async def weather(lat: float, lon: float):
    """Current weather via keyless Open-Meteo. No API key, nothing stored."""
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(422, "latitude must be -90..90 and longitude -180..180")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m",
                        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"},
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"weather lookup failed: {e}")
    if r.status_code != 200:
        raise HTTPException(502, "weather lookup failed")
    cur = r.json().get("current") or {}
    code = cur.get("weather_code")
    temp = cur.get("temperature_2m")
    condition = WEATHER_CODES.get(code, "Unknown")
    display = f"{round(temp)}°F · {condition}" if isinstance(temp, (int, float)) else condition
    return {"temp_f": temp, "condition": condition, "code": code,
            "wind_mph": cur.get("wind_speed_10m"), "display": display}


@router.get("/v1/telemetry/summary")
async def telemetry_summary():
    """Bus-derived outcome counters (V24 P5). Dashboard-safe: ids/kinds only."""
    return collector.snapshot()


@router.get("/v1/telemetry/events")
async def telemetry_events(request: Request):
    """Live, dashboard-safe lifecycle events via Server-Sent Events.

    The event bus carries identifiers/status only; secrets and tool arguments
    never enter this stream. A bounded per-client queue prevents slow clients
    from applying backpressure to Jarvis workers.
    """
    async def stream():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        def enqueue(payload: dict[str, Any]) -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

        def on_event(event: Event) -> None:
            # Export only safe identifiers and outcomes, never free-text errors/payloads.
            payload = {'name': event.name, 'ts': event.ts}
            status = getattr(event, 'status', '')
            if status:
                payload['status'] = status
            loop.call_soon_threadsafe(enqueue, payload)

        event_bus.subscribe(Event, on_event)
        try:
            yield 'event: ready\ndata: {"status":"connected"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ': heartbeat\n\n'
                    continue
                name = str(payload.get("name") or "event").replace("\n", "")
                yield f"event: jarvis\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        finally:
            event_bus.unsubscribe(Event, on_event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/performance")
async def get_performance():
    """Get performance statistics"""
    return monitor.get_stats()


@router.get("/stats")
async def get_stats():
    """Legacy stats endpoint"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        stats = {
            "timestamp": int(time.time()),
            "services": {},
            "performance": monitor.get_stats()
        }

        try:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                stats["services"]["ollama"] = {
                    "status": "online",
                    "models_loaded": len(models),
                    "models": [m["name"] for m in models]
                }
        except Exception:
            stats["services"]["ollama"] = {"status": "offline"}

        return stats
