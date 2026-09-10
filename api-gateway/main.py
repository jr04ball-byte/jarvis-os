# slowapi is preferred in production.  When the package is unavailable (for
# example in an offline bootstrap environment), use a small in-process limiter
# so Jarvis remains protected instead of failing to start.
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from compute_manager import snapshot as compute_snapshot
from deps import (
    AI_API_TOKEN,
    AI_REQUIRE_AUTH,
    APP_VERSION,
    ARTIFACTS_DIR,
    JARVIS_STARTED_AT,
    OLLAMA_URL,
    RECENT_REQUESTS,
    RateLimitExceeded,
    db,
    limiter,
    monitor,
    orchestrator,
    project_worker_runs,
    rag,
)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
)
from services import (
    _PENDING_ACTIONS,
    _PENDING_LOCK,
    _PENDING_TTL_SECONDS,
    _cached_compute_snapshot,
    _cached_target_health,
    _configuration_snapshot,
    _pending_snapshot,
    build_tools_list,
)

# Re-exported for backward compatibility (V24 P1: implementations live in store.py).
from store import ConversationDB as ConversationDB
from store import DocumentRAG as DocumentRAG
from store import PerformanceMonitor as PerformanceMonitor

# Load .env if present (local dev). In Docker, env is set by docker-compose.
try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(_ENV_PATH):
        load_dotenv(_ENV_PATH)
except ImportError:
    pass

from brains import router as brain_router
from brains import status as brain_status_snapshot
from routes import chat, dashboard, health, voice, workers
from security import policy_snapshot as security_policy_snapshot
from workspace_registry import snapshot as workspace_snapshot

import tools

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI System — Jarvis Experience", version=APP_VERSION)

# Rate limiting
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

app.add_middleware(
    CORSMiddleware,
    # Explicit localhost origins: browsers reject wildcard ("*") origins
    # when credentials are allowed, so "*" + allow_credentials=True silently
    # breaks cookie/authenticated cross-origin calls.
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8188",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8188",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routes import auth, projects, providers

app.include_router(brain_router)
app.include_router(auth.router)
app.include_router(providers.router)
app.include_router(projects.router)
app.include_router(workers.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(dashboard.router)
app.include_router(health.router)


# Recent-message window for conversation history. Keeps long chats inside a
# useful context budget; the next iteration should add token budgeting and/or
# automatic summarization (see architecture notes).

# In-memory confirmation tickets. Tickets are single-use and expire quickly.
# This prevents the client from changing the arguments between proposal and approval.


# ==================== Smart Model Routing ====================


# ==================== Core AI Behavior ====================


# ==================== Request Logging Middleware ====================

@app.middleware("http")
async def api_auth(request: Request, call_next):
    if AI_REQUIRE_AUTH and (request.url.path.startswith("/v1/") or request.url.path.startswith("/gmail/") or request.url.path.startswith("/calendar/") or request.url.path.startswith("/auth/google/")):
        supplied = request.headers.get("authorization", "")
        if not AI_API_TOKEN or supplied != f"Bearer {AI_API_TOKEN}":
            return JSONResponse(status_code=401, content={"detail":"AI System authentication required"})
    return await call_next(request)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    RECENT_REQUESTS.append({
        "method": request.method, "path": request.url.path, "status": response.status_code,
        "duration_ms": int(duration * 1000), "ts": time.time(),
    })
    logger.info(f"{request.method} {request.url.path} - {response.status_code} - {duration:.2f}s")
    return response

# ==================== Connection Registry ====================


# ==================== Google Gemini Cloud ====================


# ==================== Gemini Live Browser Token ====================

# Browser clients MUST use Gemini Live's constrained endpoint with a short-lived
# auth token.  Never hand a permanent Gemini API key to JavaScript.


# ==================== OpenCode Go Sub-Agent Relay ====================


# ==================== Deepgram Voice ====================


# ==================== V13 Artifacts / Research ====================


# ==================== Endpoints ====================


@app.get("/v1/system/status")
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


@app.get("/v1/command-center/overview")
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


# ==================== Google OAuth (Gmail + Calendar) ====================
# Reuses GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from email-agent-saas .env
# On first run: visit http://localhost:8000/auth/google/start
# Tokens are encrypted at rest in api_data volume.


# ==================== Local Tool / Device Layer ====================


# ==================== Adaptive Compute ====================


@app.get("/v1/system/compute")
async def system_compute():
    return await compute_snapshot()


# ==================== Jarvis Orchestrator ====================


# legacy implementation body removed by V22.2 refactor marker


# ==================== Streaming Chat ====================


# ==================== Sales Machine ====================


# ==================== RAG Chat ====================


# ==================== RAG Document Management ====================


# ==================== Model Comparison ====================


# ==================== Performance Stats ====================

@app.get("/v1/performance")
async def get_performance():
    """Get performance statistics"""
    return monitor.get_stats()


@app.get("/stats")
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

# ==================== Agentic Tool Calling ====================


# ==================== Adaptive Voice Turn ====================


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
