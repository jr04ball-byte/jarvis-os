# slowapi is preferred in production.  When the package is unavailable (for
# example in an offline bootstrap environment), use a small in-process limiter
# so Jarvis remains protected instead of failing to start.
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from compute_manager import select_mode
from compute_manager import snapshot as compute_snapshot
from deps import (
    AI_API_TOKEN,
    AI_REQUIRE_AUTH,
    APP_VERSION,
    ARTIFACTS_DIR,
    DEEPGRAM_API_KEY,
    DEEPGRAM_STT_MODEL,
    DEEPGRAM_TTS_MODEL,
    GEMINI_API_KEY,
    GEMINI_AUTH_TOKEN_URL,
    GEMINI_LIVE_MODEL,
    GEMINI_LIVE_WS_URL,
    GEMINI_MODEL,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_CLOUD_PROJECT,
    JARVIS_COMPUTE_MODE,
    JARVIS_MAX_CONTEXT_CHARS,
    JARVIS_MAX_RAG_CHARS,
    JARVIS_MODEL_LOCK,
    JARVIS_STARTED_AT,
    MAX_HISTORY_MESSAGES,
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
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    StreamingResponse,
)
from model_lab import benchmark as model_lab_benchmark
from model_lab import hf_search as model_lab_hf_search
from model_lab import lm_load as model_lab_lm_load
from model_lab import lm_unload as model_lab_lm_unload
from model_lab import lmstudio_inventory as model_lab_lmstudio
from model_lab import ollama_inventory as model_lab_ollama
from model_lab import overview as model_lab_overview
from schemas import (
    AgentChatRequest,
    ArtifactPatch,
    ArtifactRequest,
    ChatMessage,
    ChatRequest,
    CompareRequest,
    ConfirmationRequest,
    ConnectionUpdate,
    ConversationCreate,
    ConversationMessage,
    DeepgramSpeakRequest,
    DocumentUpload,
    GeminiChatRequest,
    GeminiLiveTokenRequest,
    OpenCodeTaskRequest,
    OrchestratorAutopilotRequest,
    OrchestratorGoal,
    OrchestratorRunRequest,
    OrchestratorTransition,
    ProjectWorkerAutofixRequest,
    ProjectWorkerImplementRequest,
    ProjectWorkerTargetRequest,
    ProjectWorkerVerifyRequest,
    ResearchRequest,
    ToolRequest,
    VoiceTurnRequest,
)
from services import (
    _PENDING_ACTIONS,
    _PENDING_LOCK,
    _PENDING_TTL_SECONDS,
    CONNECTION_CATALOG,
    _adaptive_options,
    _artifact_path,
    _artifact_read,
    _cached_compute_snapshot,
    _cached_target_health,
    _configuration_snapshot,
    _connection_snapshot,
    _consume_confirmation,
    _load_connection_overrides,
    _pending_snapshot,
    _project_worker_target,
    _run_agent_loop,
    _run_project_autofix_cycle,
    _save_connection_overrides,
    _update_confirmation_resume,
    apply_system_prompt,
    build_tools_list,
    create_artifact,
    execute_tool_core,
    select_agent_model,
    select_model,
    select_voice_path,
    stream_chat,
    web_search,
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

import google_oauth  # Google OAuth2 flow (Gmail + Calendar)
import local_tools
from brains import providers as intelligence_providers
from brains import router as brain_router
from brains import status as brain_status_snapshot
from project_worker import capture_git_diff as project_capture_diff
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import make_worker_prompt
from project_worker import project_health as project_worker_health_snapshot
from project_worker import verify_workspace as project_verify_workspace
from providers import ProviderMessage
from security import policy_snapshot as security_policy_snapshot
from workspace_registry import get_target as workspace_target
from workspace_registry import is_registered_workspace, target_for_workspace
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

app.include_router(brain_router)


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


@app.get("/v1/connections")
async def list_connections():
    return await _connection_snapshot()

@app.post("/v1/connections/{connection_id}/enable")
async def set_connection(connection_id: str, req: ConnectionUpdate):
    if connection_id not in {x["id"] for x in CONNECTION_CATALOG}: raise HTTPException(404,"unknown connection")
    data=_load_connection_overrides(); data[connection_id]={"enabled":req.enabled,"updated_at":time.time()}; _save_connection_overrides(data)
    return await _connection_snapshot()

@app.post("/v1/connections/{connection_id}/test")
async def test_connection(connection_id: str):
    snapshot=await _connection_snapshot(); item=next((x for x in snapshot["connections"] if x["id"]==connection_id),None)
    if not item: raise HTTPException(404,"unknown connection")
    ok=item["status"] in {"connected","configured","installed","ready","available"}
    return {"ok":ok,"connection":item,"tested_at":time.time()}


# ==================== Google Gemini Cloud ====================


@app.get("/v1/gemini/status")
async def gemini_status():
    return {
        "configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "project": GOOGLE_CLOUD_PROJECT or None,
        "location": GOOGLE_CLOUD_LOCATION,
        "backend": "Gemini Developer API via Google Cloud project" if GEMINI_API_KEY else "not_configured",
    }

@app.post("/v1/gemini/chat")
@limiter.limit("20/minute")
async def gemini_chat(request: Request, body: GeminiChatRequest):
    """Optional cloud chat. The API key stays server-side; Qwen/Ollama remains the local default."""
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Google Gemini is not configured. Set GEMINI_API_KEY in .env")
    model = body.model or GEMINI_MODEL
    contents = []
    for m in body.messages[-100:]:
        role = "model" if m.role == "assistant" else "user"
        if m.role == "system":
            role = "user"
        contents.append({"role": role, "parts": [{"text": m.content}]})
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": max(0.0, min(float(body.temperature or 0.7), 2.0)),
            "maxOutputTokens": max(1, min(int(body.max_tokens or 1024), 8192)),
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gemini request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    data = r.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    answer = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return {"message": {"role": "assistant", "content": answer}, "model": model, "provider": "google_gemini"}

# ==================== Gemini Live Browser Token ====================

# Browser clients MUST use Gemini Live's constrained endpoint with a short-lived
# auth token.  Never hand a permanent Gemini API key to JavaScript.


@app.post("/v1/gemini/live-token")
@limiter.limit("10/minute")
async def gemini_live_token(request: Request, body: GeminiLiveTokenRequest = GeminiLiveTokenRequest()):
    """Mint a one-use Gemini Live ephemeral token without exposing the API key.

    Token creation is performed server-side using the long-lived API key.  The
    browser receives only a short-lived, single-use credential for the
    constrained Live WebSocket endpoint.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Google Gemini is not configured. Set GEMINI_API_KEY in .env")

    ttl_minutes = max(1, min(int(body.ttl_minutes or 10), 30))
    now = datetime.now(timezone.utc)
    payload = {
        "uses": 1,
        "expireTime": (now + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z"),
        # Limit the window in which a stolen token could start a new session.
        "newSessionExpireTime": (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                GEMINI_AUTH_TOKEN_URL,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Gemini ephemeral token request failed: {exc}")

    if response.status_code not in (200, 201):
        detail = response.text[:500].replace(GEMINI_API_KEY, "[REDACTED]")
        raise HTTPException(response.status_code, f"Gemini ephemeral token request failed: {detail}")
    data = response.json()
    ephemeral = str(data.get("name") or "").strip()
    if not ephemeral:
        raise HTTPException(502, "Gemini token service returned no ephemeral credential")

    return {
        "token": ephemeral,
        "token_type": "ephemeral",
        "model": GEMINI_LIVE_MODEL,
        "ws_url": GEMINI_LIVE_WS_URL,
        "input_rate": 16000,
        "output_rate": 24000,
        "expires_in": ttl_minutes * 60,
        "new_session_expires_in": 60,
    }

# ==================== OpenCode Go Sub-Agent Relay ====================


@app.post("/v1/opencode/task")
@limiter.limit("10/minute")
async def run_opencode_task(request: Request, body: OpenCodeTaskRequest):
    """Read-only OpenCode relay scoped to an exact registered workspace.

    V23 deliberately removes direct unverified code-writing from this legacy
    endpoint. Code changes must flow through Project Worker/Autopilot so they
    receive baseline capture, verification, retry bounds, and audit evidence.
    """
    if body.mode != "inspect":
        raise HTTPException(409, "direct OpenCode writes are disabled; use /v1/project-worker/autofix or Autopilot")

    workspace = ""
    resolved_target = (body.target or "").strip()
    if resolved_target:
        try:
            target = workspace_target(resolved_target)
        except KeyError:
            raise HTTPException(404, "unknown OpenCode target")
        workspace = (target.get("workspace") or {}).get("tool_path") or (target.get("workspace") or {}).get("path") or ""
    elif body.path:
        workspace = str(body.path).strip()
        resolved_target = target_for_workspace(workspace) or ""
    if not workspace or not is_registered_workspace(workspace):
        raise HTTPException(403, "OpenCode may only inspect an exact workspace registered with Jarvis")

    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")
    try:
        result = await worker.complete(
            [ProviderMessage(role="user", content=body.prompt)],
            temperature=0.1, max_tokens=4096,
            task_context={
                "workspace": workspace,
                "permission": "read_only",
                "target": resolved_target or None,
                "mode": "inspect",
            },
        )
    except Exception as exc:
        logger.exception("read-only OpenCode task failed")
        raise HTTPException(502, f"OpenCode worker failed: {exc}")
    return {
        "status": "ok",
        "mode": "inspect",
        "target": resolved_target or None,
        "provider": result.provider,
        "model": result.model,
        "content": result.content,
        "metadata": result.metadata,
    }

# ==================== Deepgram Voice ====================

@app.post("/v1/deepgram-token")
async def deepgram_token():
    """Mint a short-lived browser token without exposing the Deepgram API key."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://api.deepgram.com/v1/auth/grant",
                headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"},
                json={"ttl_seconds": 300},
            )
        if r.status_code != 200:
            logger.warning("Deepgram token request failed: %s", r.text[:500])
            raise HTTPException(502, "Deepgram token request failed")
        data = r.json()
        return {"access_token": data.get("access_token"), "expires_in": data.get("expires_in", 300)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Deepgram token error")
        raise HTTPException(502, f"Deepgram token error: {e}")


@app.post("/v1/deepgram-speak")
async def deepgram_speak(req: DeepgramSpeakRequest):
    """Synthesize speech server-side so the long-lived API key never reaches the browser."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 6000:
        text = text[:6000]
    speed = max(0.7, min(1.5, float(req.speed or 1.0)))
    model = (req.model or DEEPGRAM_TTS_MODEL).strip()
    url = "https://api.deepgram.com/v1/speak"
    params = {"model": model, "encoding": "linear16", "sample_rate": 24000, "container": "wav", "speed": speed}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, params=params, headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"}, json={"text": text})
        if r.status_code != 200:
            logger.warning("Deepgram TTS failed: %s", r.text[:500])
            raise HTTPException(502, "Deepgram speech synthesis failed")
        return StreamingResponse(iter([r.content]), media_type="audio/wav", headers={"Cache-Control":"no-store"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Deepgram TTS error")
        raise HTTPException(502, f"Deepgram TTS error: {e}")

@app.post("/v1/deepgram-speak-stream")
async def deepgram_speak_stream(req: DeepgramSpeakRequest):
    """Stream raw 24 kHz linear16 PCM from Deepgram so the browser can begin
    speaking before the complete synthesis response has arrived."""
    if not DEEPGRAM_API_KEY:
        raise HTTPException(503, "Deepgram is not configured")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 6000:
        text = text[:6000]
    speed = max(0.7, min(1.5, float(req.speed or 1.0)))
    model = (req.model or DEEPGRAM_TTS_MODEL).strip()
    url = "https://api.deepgram.com/v1/speak"
    params = {"model": model, "encoding": "linear16", "sample_rate": 24000, "container": "none", "speed": speed}
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream = await client.stream("POST", url, params=params, headers={"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"}, json={"text": text}).__aenter__()
        if upstream.status_code != 200:
            detail = (await upstream.aread()).decode("utf-8", "replace")[:500]
            await upstream.aclose(); await client.aclose()
            raise HTTPException(502, f"Deepgram speech synthesis failed: {detail}")
        async def body_iter():
            try:
                async for chunk in upstream.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(body_iter(), media_type="audio/L16;rate=24000", headers={"Cache-Control":"no-store", "X-Audio-Format":"pcm_s16le_24000"})
    except HTTPException:
        raise
    except Exception as e:
        await upstream.aclose() if 'upstream' in locals() else asyncio.sleep(0)
        await client.aclose()
        logger.exception("Deepgram streaming TTS error")
        raise HTTPException(502, f"Deepgram streaming TTS error: {e}")

@app.get("/v1/deepgram-status")
async def deepgram_status():
    return {"configured": bool(DEEPGRAM_API_KEY), "stt_model": DEEPGRAM_STT_MODEL, "tts_model": DEEPGRAM_TTS_MODEL, "mode": "deepgram-stt-tts + local-ollama-brain"}


# ==================== V13 Artifacts / Research ====================


@app.get("/v1/artifacts")
async def artifacts_list(limit: int = 30):
    items=[]
    for path in sorted(ARTIFACTS_DIR.glob("*.json"), key=lambda x:x.stat().st_mtime, reverse=True)[:max(1,min(limit,100))]:
        try: items.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc: logger.debug("skipping unreadable artifact %s: %s", path, exc)
    return {"items":items}

@app.post("/v1/artifacts")
async def artifact_create(body: ArtifactRequest):
    return create_artifact(body.title, body.kind, body.content, body.metadata)

@app.get("/v1/artifacts/{artifact_id}")
async def artifact_get(artifact_id: str): return _artifact_read(artifact_id)

@app.patch("/v1/artifacts/{artifact_id}")
async def artifact_patch(artifact_id: str, body: ArtifactPatch):
    item=_artifact_read(artifact_id)
    patch=body.model_dump(exclude_none=True)
    item.update(patch); item["updated_at"]=datetime.now(timezone.utc).isoformat()
    _artifact_path(artifact_id).write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding="utf-8")
    return item

@app.delete("/v1/artifacts/{artifact_id}")
async def artifact_delete(artifact_id: str):
    path=_artifact_path(artifact_id)
    if not path.exists(): raise HTTPException(404,"artifact not found")
    path.unlink(); return {"ok":True,"id":artifact_id}


@app.post("/v1/research/search")
async def research_search(body: ResearchRequest):
    return await web_search(body.query, body.num_results)

# ==================== Endpoints ====================

@app.get("/")
async def root():
    return {
        "service": "Enhanced AI System",
        "version": "23.0.0",
        "features": [
            "Streaming responses",
            "Conversation memory",
            "RAG (document Q&A)",
            "Performance monitoring",
            "Model comparison",
            "Rate limiting",
            "Provider-switchable voice: free/local Whisper or optional Deepgram realtime STT/TTS",
            "Agentic tool calling with confirmation gates",
            "Gmail, Calendar, Windows files, Home Assistant and TV/media controls",
            "iPhone companion",
            "Core and Sales Machine profiles with SaaS separation",
            "Jarvis-style artifacts, optional web research and opt-in Windows computer mode"
        ],
        "endpoints": {
            "chat": "/v1/chat/completions",
            "chat_rag": "/v1/chat/completions-rag",
            "sales_chat": "/v1/sales/chat",
            "models": "/v1/models",
            "conversations": "/v1/conversations",
            "documents": "/v1/documents",
            "compare": "/v1/compare",
            "performance": "/v1/performance",
            "health": "/health",
            "voice": "/voice",
            "companion": "/companion",
            "tools": "/v1/tools",
            "agent": "/v1/agent/chat",
            "orchestrator_autopilot": "/v1/orchestrator/autopilot",
            "orchestrator_targets": "/v1/orchestrator/targets",
            "brain_status": "/v1/brain/status",
            "brain_policy": "/v1/brain/policy",
            "brain_route": "/v1/brain/route",
            "project_worker_inspect": "/v1/project-worker/inspect",
            "project_worker_verify": "/v1/project-worker/verify",
            "project_worker_implement": "/v1/project-worker/implement",
            "project_worker_autofix": "/v1/project-worker/autofix",
            "project_worker_health": "/v1/project-worker/health",
            "command_center": "/v1/command-center/overview",
            "readiness": "/ready"
        }
    }

@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard.html", include_in_schema=False)
async def dashboard_ui():
    """Primary V23 Jarvis Command Center."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard.html"), media_type="text/html")

@app.get("/godseye", include_in_schema=False)
@app.get("/godseye.html", include_in_schema=False)
async def godseye_ui():
    """V19 tactical globe view retained as an optional operations surface."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "godseye.html"), media_type="text/html")

@app.get("/dashboard-classic", include_in_schema=False)
@app.get("/dashboard-classic.html", include_in_schema=False)
async def dashboard_classic_ui():
    """Classic chat cockpit retained for compatibility."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "dashboard-classic.html"), media_type="text/html")

@app.get("/orb-loop.webm", include_in_schema=False)
@app.get("/orb-loop.mp4", include_in_schema=False)
@app.get("/orb-poster.png", include_in_schema=False)
@app.get("/bg-loop.webm", include_in_schema=False)
@app.get("/bg-loop.mp4", include_in_schema=False)
@app.get("/bg-poster.png", include_in_schema=False)
@app.get("/command-center-loop.webm", include_in_schema=False)
@app.get("/command-center-loop.mp4", include_in_schema=False)
@app.get("/command-center-poster.png", include_in_schema=False)
async def blender_dashboard_asset(request: Request):
    """Serve Blender-baked dashboard motion assets without exposing arbitrary files."""
    name = request.url.path.lstrip("/")
    allowed = {
        "orb-loop.webm", "orb-loop.mp4", "orb-poster.png",
        "bg-loop.webm", "bg-loop.mp4", "bg-poster.png",
        "command-center-loop.webm", "command-center-loop.mp4", "command-center-poster.png",
    }
    if name not in allowed:
        raise HTTPException(404, "asset not found")
    base = os.path.join(os.path.dirname(__file__), "assets", name)
    if not os.path.isfile(base):
        raise HTTPException(404, "Blender dashboard asset not baked yet")
    media = "video/webm" if name.endswith(".webm") else ("video/mp4" if name.endswith(".mp4") else "image/png")
    return FileResponse(base, media_type=media, headers={"Cache-Control": "public, max-age=3600"})

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

@app.get("/v1/agent/pending")
async def agent_pending():
    """List redacted pending approvals for the dashboard; arguments are never returned."""
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k,v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for k in expired:
            _PENDING_ACTIONS.pop(k, None)
        return {"items": [{"confirmation_id": k, "action": v["tool"], "reason": v["reason"], "created_at": v["created_at"], "expires_at": v["created_at"] + _PENDING_TTL_SECONDS} for k,v in _PENDING_ACTIONS.items()]}

@app.get("/voice", include_in_schema=False)
async def voice_ui():
    """Browser voice console: headset microphone -> AI -> headset speech."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "voice.html"), media_type="text/html")

@app.get("/voice-live", include_in_schema=False)
@app.get("/voice-live.html", include_in_schema=False)
async def voice_live_ui():
    """Gemini Live full-duplex voice console with tool calling."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "voice-live.html"), media_type="text/html")

@app.get("/voice-engine.js", include_in_schema=False)
async def voice_engine_js():
    """Shared adaptive voice engine (state machine, guards, streaming TTS helpers)."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "voice-engine.js"), media_type="application/javascript")

@app.get("/health")
async def health_check():
    """Fast liveness probe: no external network calls."""
    return {
        "status": "healthy", "version": APP_VERSION,
        "uptime_seconds": int(max(0, time.time() - JARVIS_STARTED_AT)),
    }


@app.get("/ready")
async def readiness_check():
    """Deep readiness probe. 200 means at least one intelligence provider is online."""
    brains = await brain_status_snapshot()
    online = [name for name, info in (brains.get("providers") or {}).items() if info.get("online")]
    config = _configuration_snapshot()
    ready = bool(online) and bool(config.get("data_directory_writable"))
    payload = {"ready": ready, "version": APP_VERSION, "online_providers": online, "configuration": config}
    if not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


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

@app.get("/auth/google/start")
async def auth_google_start():
    """Begin Google OAuth — returns the URL the user should visit."""
    if not google_oauth.CLIENT_ID:
        raise HTTPException(503, "Google OAuth not configured (GOOGLE_CLIENT_ID missing)")
    return {"url": google_oauth.build_auth_url()}

@app.get("/auth/google/callback")
async def auth_google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Google redirects here after user consents. Exchanges code → tokens,
    stores them encrypted, returns the connected account email."""
    if error:
        raise HTTPException(400, f"Google OAuth failed: {error}")
    if not code or not state:
        raise HTTPException(400, "missing code or state (did you deny consent?)")
    if not google_oauth._consume_state(state):
        raise HTTPException(400, "invalid or expired state token")
    try:
        result = await google_oauth.exchange_code(code)
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"token exchange failed: {e.response.text}")
    store = google_oauth.TokenStore()
    store.save_tokens(result["email"], result["tokens"])
    logger.info("Google account connected: %s", result["email"])
    return {"ok": True, "email": result["email"], "scopes": result["tokens"].get("scope", "")}

@app.get("/auth/google/status")
async def auth_google_status():
    """List connected Google accounts and whether their tokens are still valid."""
    store = google_oauth.TokenStore()
    accounts = []
    for email in store.list_accounts():
        tok = store.load_tokens(email)
        if not tok:
            continue
        expires_at = tok.get("saved_at", 0) + int(tok.get("expires_in", 3600))
        accounts.append({
            "email": email,
            "scopes": tok.get("scope", ""),
            "expires_at": expires_at,
            "needs_refresh": time.time() >= expires_at - 60,
            "has_refresh_token": "refresh_token" in tok,
        })
    return {"accounts": accounts, "configured": bool(google_oauth.CLIENT_ID)}

@app.post("/auth/google/disconnect")
async def auth_google_disconnect(email: str):
    """Forget a connected account (revokes locally; doesn't revoke on Google's side)."""
    store = google_oauth.TokenStore()
    if store.delete_account(email):
        return {"ok": True, "email": email}
    raise HTTPException(404, f"no tokens stored for {email}")

@app.get("/gmail/messages")
async def gmail_messages(email: str, max_results: int = 20, query: str = ""):
    """List recent Gmail messages for the connected account. Read-only."""
    store = google_oauth.TokenStore()
    try:
        msgs = await google_oauth.gmail_list_messages(email, store, max_results, query)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    return {"email": email, "count": len(msgs), "messages": msgs}

@app.get("/calendar/events")
async def calendar_events(email: str, max_results: int = 10):
    """List upcoming calendar events for the connected account."""
    time_min = datetime.now(timezone.utc).isoformat()
    store = google_oauth.TokenStore()
    try:
        events = await google_oauth.calendar_list_events(email, store, max_results, time_min)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    return {"email": email, "count": len(events), "events": events}


# ==================== Local Tool / Device Layer ====================


@app.get("/companion", include_in_schema=False)
async def companion_ui():
    return FileResponse(os.path.join(os.path.dirname(__file__), "companion.html"), media_type="text/html")

@app.get("/companion-manifest.json", include_in_schema=False)
async def companion_manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "companion-manifest.json"), media_type="application/manifest+json")

@app.get("/companion-sw.js", include_in_schema=False)
async def companion_sw():
    return FileResponse(os.path.join(os.path.dirname(__file__), "companion-sw.js"), media_type="application/javascript")

@app.get("/v1/tools/local")
async def local_tools_inventory():
    """Discover local applications without granting arbitrary process execution."""
    return local_tools.scan_local_tools()

@app.get("/v1/tools")
async def list_tools():
    return {"tools": build_tools_list()}

@app.post("/v1/tools/execute")
async def execute_tool(req: ToolRequest):
    return await execute_tool_core(req.tool, req.arguments, req.confirmed)

@app.get("/v1/models")
async def list_models():
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                ollama_models = response.json().get("models", [])
                models = [
                    {
                        "id": model["name"],
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "local"
                    }
                    for model in ollama_models
                ]
                return {"object": "list", "data": models}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch models: {e!s}")

    return {"object": "list", "data": []}

# ==================== Adaptive Compute ====================


@app.get("/v1/model-lab/overview")
async def model_lab_overview_route():
    return await model_lab_overview()

@app.get("/v1/model-lab/huggingface")
async def model_lab_huggingface(q: str = "gemma 3 4b gguf", limit: int = 8):
    try:
        return await model_lab_hf_search(q, limit)
    except Exception as e:
        raise HTTPException(502, f"Hugging Face search failed: {e}")

@app.post("/v1/model-lab/lmstudio/load")
async def model_lab_load(payload: dict):
    model = str(payload.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "model is required")
    return await model_lab_lm_load(model, int(payload.get("context_length", 4096)), str(payload.get("gpu", "auto")), int(payload.get("ttl", 1800)))

@app.post("/v1/model-lab/benchmark")
async def model_lab_benchmark_route(payload: dict):
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    prompt = str(payload.get("prompt") or "Explain why a 4B quantized model can be faster and more efficient than a 9B model on an 8GB GPU.")
    if not provider or not model:
        raise HTTPException(400, "provider and model are required")
    try:
        return await model_lab_benchmark(provider, model, prompt[:1200])
    except Exception as e:
        raise HTTPException(502, f"Benchmark failed: {e}")

@app.post("/v1/model-lab/lmstudio/unload")
async def model_lab_unload(payload: dict):
    instance_id = str(payload.get("instance_id") or "").strip()
    if not instance_id:
        raise HTTPException(400, "instance_id is required")
    return await model_lab_lm_unload(instance_id)

@app.get("/v1/model-lab/runtimes")
async def model_lab_runtimes():
    return {"ollama": await model_lab_ollama(), "lmstudio": await model_lab_lmstudio(), "compute": await compute_snapshot()}

@app.get("/v1/system/compute")
async def system_compute():
    return await compute_snapshot()


# ==================== Jarvis Orchestrator ====================


@app.get("/v1/orchestrator/policy")
async def orchestrator_policy():
    """Expose the action policy without exposing secrets."""
    return security_policy_snapshot()

@app.get("/v1/orchestrator/targets")
async def orchestrator_targets():
    """Return configured project workspaces without exposing credentials."""
    return {"targets": workspace_snapshot()}


@app.post("/v1/project-worker/inspect")
@limiter.limit("20/minute")
async def project_worker_inspect(request: Request, body: ProjectWorkerTargetRequest):
    target = _project_worker_target(body.target)
    try:
        evidence = await asyncio.to_thread(project_inspect_workspace, target["resolved_workspace"])
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "evidence": evidence}


@app.post("/v1/project-worker/health")
@limiter.limit("20/minute")
async def project_worker_health(request: Request, body: ProjectWorkerTargetRequest):
    target = _project_worker_target(body.target)
    try:
        health = await asyncio.to_thread(project_worker_health_snapshot, target["resolved_workspace"])
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "health": health}


@app.post("/v1/project-worker/verify")
@limiter.limit("10/minute")
async def project_worker_verify(request: Request, body: ProjectWorkerVerifyRequest):
    target = _project_worker_target(body.target)
    try:
        result = await asyncio.to_thread(
            project_verify_workspace, target["resolved_workspace"],
            run_tests=body.run_tests, run_build=body.run_build, run_lint=body.run_lint
        )
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "verification": result}


@app.post("/v1/project-worker/implement")
@limiter.limit("5/minute")
async def project_worker_implement(request: Request, body: ProjectWorkerImplementRequest):
    """Run one bounded OpenCode implementation cycle inside a registered workspace."""
    target = _project_worker_target(body.target)
    workspace = target["resolved_workspace"]
    try:
        before = await asyncio.to_thread(project_inspect_workspace, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))

    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")
    prompt = make_worker_prompt(body.goal, workspace, before, permission="workspace_write")
    started = time.time()
    try:
        result = await worker.complete(
            [ProviderMessage(role="user", content=prompt)],
            temperature=0.2, max_tokens=4096,
            task_context={"workspace": workspace, "permission": "workspace_write", "goal": body.goal},
        )
    except Exception as exc:
        logger.exception("project worker implementation failed")
        raise HTTPException(502, f"OpenCode worker failed: {exc}")
    elapsed_ms = int((time.time() - started) * 1000)
    diff = await asyncio.to_thread(project_capture_diff, workspace)
    verification = None
    if body.verify:
        verification = await asyncio.to_thread(project_verify_workspace, workspace, run_tests=True, run_build=True, run_lint=False)
    return {
        "target": target["target"], "workspace": target["workspace"], "goal": body.goal,
        "worker": {"provider": result.provider, "model": result.model, "content": result.content, "metadata": result.metadata},
        "diff": diff, "verification": verification, "elapsed_ms": elapsed_ms,
        "status": "verified" if verification and verification.get("ok") else ("implemented_unverified" if not body.verify else "needs_attention"),
    }


@app.get("/v1/project-worker/runs/{run_id}")
async def project_worker_run(run_id: str):
    try:
        return project_worker_runs.get(run_id)
    except KeyError:
        raise HTTPException(404, "project-worker run not found")


@app.post("/v1/project-worker/autofix")
@limiter.limit("3/minute")
async def project_worker_autofix(request: Request, body: ProjectWorkerAutofixRequest):
    """Bounded diagnose -> implement -> verify -> repair loop with durable run state."""
    return await _run_project_autofix_cycle(body)


# legacy implementation body removed by V22.2 refactor marker


@app.post("/v1/orchestrator/plan")
@limiter.limit("20/minute")
async def orchestrator_plan(request: Request, body: OrchestratorGoal):
    """Create a durable Goal -> Plan -> Tasks project."""
    try:
        return orchestrator.create_project(body.goal)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get("/v1/orchestrator/projects/{project_id}")
async def orchestrator_project(project_id: str):
    try:
        return orchestrator.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "project not found")

@app.get("/v1/orchestrator/projects/{project_id}/next")
async def orchestrator_next(project_id: str):
    try:
        task = orchestrator.next_task(project_id)
        return {"task": task}
    except Exception as exc:
        raise HTTPException(404, str(exc))

@app.post("/v1/orchestrator/transition")
@limiter.limit("60/minute")
async def orchestrator_transition(request: Request, body: OrchestratorTransition):
    try:
        return orchestrator.transition(body.task_id, body.status, body.result, body.error)
    except KeyError:
        raise HTTPException(404, "task not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@app.get("/v1/orchestrator/projects/{project_id}/audit")
async def orchestrator_audit(project_id: str, limit: int = 100):
    try:
        orchestrator.get_project(project_id)
        return {"events": orchestrator.audit(project_id, limit)}
    except KeyError:
        raise HTTPException(404, "project not found")


# ==================== Streaming Chat ====================


@app.post("/v1/chat/completions")
@limiter.limit("30/minute")
async def chat_completion(request: Request, chat_request: ChatRequest):
    """OpenAI-compatible chat completion with optional persistent memory and RAG."""
    model_messages = apply_system_prompt(list(chat_request.messages), chat_request.assistant_profile)

    if chat_request.conversation_id is not None:
        if not db.conversation_exists(chat_request.conversation_id):
            raise HTTPException(
                status_code=404,
                detail=f"conversation {chat_request.conversation_id} not found"
            )

        conversation_profile = db.get_conversation_profile(chat_request.conversation_id)
        requested_profile = chat_request.assistant_profile.lower()
        if requested_profile not in {"general", "sales"}:
            requested_profile = "general"
        if conversation_profile != requested_profile:
            raise HTTPException(
                status_code=409,
                detail=f"conversation {chat_request.conversation_id} belongs to the '{conversation_profile}' profile; start a new conversation for '{requested_profile}'"
            )

        history = db.get_conversation(
            chat_request.conversation_id,
            limit=MAX_HISTORY_MESSAGES
        )

        incoming = [{"role": m.role, "content": m.content} for m in chat_request.messages]
        if history and incoming[:len(history)] == history:
            new_messages = incoming[len(history):]
            model_messages = apply_system_prompt([ChatMessage(**msg) for msg in incoming], chat_request.assistant_profile)
        else:
            new_messages = incoming
            model_messages = apply_system_prompt(
                [ChatMessage(role=h["role"], content=h["content"]) for h in history] + chat_request.messages,
                chat_request.assistant_profile
            )

        for msg in new_messages:
            if msg["role"] == "user":
                db.add_message(chat_request.conversation_id, "user", msg["content"])

    # Keep the newest system message plus the most recent messages within a
    # character budget. This protects VRAM without reducing the model itself.
    if sum(len(m.content) for m in model_messages) > JARVIS_MAX_CONTEXT_CHARS:
        system_msgs = [m for m in model_messages if m.role == "system"][:1]
        non_system = [m for m in model_messages if m.role != "system"]
        kept = []
        total = sum(len(m.content) for m in system_msgs)
        for msg in reversed(non_system):
            if total + len(msg.content) > JARVIS_MAX_CONTEXT_CHARS and kept:
                break
            kept.append(msg); total += len(msg.content)
        model_messages = system_msgs + list(reversed(kept))

    if chat_request.use_rag and model_messages:
        last_user = next(
            (msg for msg in reversed(model_messages) if msg.role == "user"),
            None
        )
        if last_user is not None:
            last_user.content = rag.augment_prompt(last_user.content)[:JARVIS_MAX_RAG_CHARS]

    selected_model = select_model(chat_request.model, [m.model_dump() for m in model_messages if m.role != "system"], chat_request.assistant_profile)

    if chat_request.stream:
        stream_request = chat_request.model_copy(update={
            "model": selected_model,
            "messages": model_messages,
            "use_rag": False
        })
        return StreamingResponse(
            stream_chat(stream_request),
            media_type="text/event-stream"
        )

    start_time = time.time()
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            async with JARVIS_MODEL_LOCK:
                response = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                    "model": selected_model,
                    "messages": [{"role": msg.role, "content": msg.content} for msg in model_messages],
                    "stream": False,
                    "think": False,
                    "options": {**_adaptive_options(), "temperature": chat_request.temperature, "num_predict": chat_request.max_tokens or 1024}
                }
            )

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            ollama_response = response.json()
            assistant_content = ollama_response.get("message", {}).get("content", "")
            duration = time.time() - start_time
            tokens = (
                ollama_response.get("eval_count", 0)
                + ollama_response.get("prompt_eval_count", 0)
            )
            monitor.record(selected_model, tokens, duration)

            if chat_request.conversation_id is not None and assistant_content:
                db.add_message(
                    chat_request.conversation_id, "assistant", assistant_content
                )

            result_compute = select_mode(JARVIS_COMPUTE_MODE)
            return {
                "compute": {"mode": result_compute["mode"], "reason": result_compute["reason"]},
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": selected_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": assistant_content},
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": ollama_response.get("prompt_eval_count", 0),
                    "completion_tokens": ollama_response.get("eval_count", 0),
                    "total_tokens": tokens
                }
            }

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama service unavailable: {e!s}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Chat completion failed: {e!s}"
            )

# ==================== Sales Machine ====================

@app.post("/v1/sales/chat")
@limiter.limit("30/minute")
async def sales_chat(request: Request, chat_request: ChatRequest):
    """Sales Machine endpoint. Independent from the Email Agent SaaS."""
    sales_request = chat_request.model_copy(update={"assistant_profile": "sales"})
    return await chat_completion(request, sales_request)

# ==================== RAG Chat ====================

@app.post("/v1/chat/completions-rag")
@limiter.limit("20/minute")
async def chat_with_rag(request: Request, chat_request: ChatRequest):
    """Chat with RAG context augmentation without polluting stored memory."""
    chat_request.use_rag = True
    return await chat_completion(request, chat_request)


@app.post("/v1/conversations")
async def create_conversation(conv: ConversationCreate):
    profile = conv.assistant_profile.lower() if conv.assistant_profile else "general"
    if profile not in {"general", "sales"}:
        raise HTTPException(status_code=400, detail="assistant_profile must be 'general' or 'sales'")
    conv_id = db.create_conversation(conv.title, conv.model, profile)
    return {"conversation_id": conv_id, "title": conv.title, "assistant_profile": profile}

@app.get("/v1/conversations")
async def list_conversations():
    return db.list_conversations()

@app.get("/v1/conversations/{conv_id}")
async def get_conversation(conv_id: int):
    if not db.conversation_exists(conv_id):
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} not found")
    messages = db.get_conversation(conv_id)
    return {"conversation_id": conv_id, "messages": messages}

@app.post("/v1/conversations/{conv_id}/messages")
async def add_conversation_message(conv_id: int, message: ConversationMessage):
    if not db.conversation_exists(conv_id):
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} not found")
    db.add_message(conv_id, message.role, message.content)
    return {"status": "added"}

# ==================== RAG Document Management ====================

@app.post("/v1/documents")
async def upload_document(doc: DocumentUpload):
    """Upload a document to the RAG knowledge base"""
    try:
        rag.add_document(doc.content, doc.doc_id)
        return {"status": "added", "doc_id": doc.doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/documents/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """Upload a text file to RAG"""
    try:
        content = await file.read()
        text = content.decode('utf-8', errors='replace')
        doc_id = f"{file.filename}-{int(time.time())}"
        rag.add_document(text, doc_id)
        return {"status": "added", "doc_id": doc_id, "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== Model Comparison ====================

@app.post("/v1/compare")
@limiter.limit("5/minute")
async def compare_models(
    request: Request,
    body: CompareRequest,
):
    """Compare responses from multiple models"""
    results = {}

    async with httpx.AsyncClient(timeout=300.0) as client:
        for model in body.models:
            start = time.time()

            try:
                response = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": body.prompt}],
                    "stream": False,
                    "think": False,
                        "options": {"num_predict": 1024}
                    }
                )

                duration = time.time() - start

                if response.status_code == 200:
                    result = response.json()

                    results[model] = {
                        "response": result["message"]["content"],
                        "tokens": result.get("eval_count", 0),
                        "time_seconds": round(duration, 2),
                        "tokens_per_second": round(result.get("eval_count", 0) / duration, 1) if duration > 0 else 0,
                        "status": "success"
                    }
                else:
                    results[model] = {
                        "status": "error",
                        "error": f"HTTP {response.status_code}"
                    }
            except Exception as e:
                results[model] = {
                    "status": "error",
                    "error": str(e)
                }

    return results

# ==================== Performance Stats ====================

@app.get("/v1/performance")
async def get_performance():
    """Get performance statistics"""
    return monitor.get_stats()

@app.get("/v1/computer/capabilities")
async def computer_capabilities_route():
    return await tools.computer_capabilities()

@app.get("/v1/computer/screenshot")
async def computer_screenshot_route():
    return await tools.computer_screenshot()

@app.get("/v1/computer/processes")
async def computer_processes_route():
    return await tools.computer_processes()

@app.get("/v1/computer/windows")
async def computer_windows_route():
    return await tools.computer_windows()

@app.get("/v1/computer/observe")
async def computer_observe_route():
    return await tools.computer_observe()

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


@app.post("/v1/agent/chat")
@limiter.limit("20/minute")
async def agent_chat(request: Request, body: AgentChatRequest):
    """Multi-step local agent. Read-only tools run immediately; sensitive actions pause for approval."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    if any(m.role == "system" for m in body.messages):
        raise HTTPException(400, "system messages are not accepted by the agent endpoint")
    messages = [m.model_dump() for m in apply_system_prompt(list(body.messages), body.assistant_profile)]
    selected_model = select_agent_model(body.model, messages, body.assistant_profile)
    return await _run_agent_loop(selected_model, messages, body.assistant_profile,
                                 body.conversation_id, body.max_tool_rounds)


@app.post("/v1/orchestrator/execute")
@limiter.limit("10/minute")
async def orchestrator_execute(request: Request, body: OrchestratorRunRequest):
    """Run a durable Jarvis plan through the existing agent/tool authority.

    Read-only planning/inspection/verification tasks can run autonomously. Any
    sensitive tool call still stops at the existing exact-action confirmation
    ticket, then resumes the same task after approval.
    """
    try:
        project = orchestrator.get_project(body.project_id)
    except KeyError:
        raise HTTPException(404, "project not found")

    completed = []
    for _ in range(body.max_steps):
        task = orchestrator.next_task(body.project_id)
        if not task:
            return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "waiting_or_complete"}
        try:
            orchestrator.transition(task["id"], "running")
            project = orchestrator.get_project(body.project_id)
            # Target context is stored inside the durable plan so the worker
            # always resumes against the same workspace after a restart.
            target = (project.get("plan") or {}).get("target") or {}
            workspace = target.get("workspace") or {}
            workspace_text = json.dumps(workspace, default=str) if workspace else "No specific workspace target was detected."

            if task["kind"] == "workspace_inspect" and workspace:
                try:
                    evidence = await asyncio.to_thread(project_inspect_workspace, workspace.get("tool_path") or workspace.get("path"))
                except FileNotFoundError as exc:
                    orchestrator.transition(task["id"], "blocked", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "error": str(exc)}
                result = {"ok": True, "handled_by": "project_worker", "evidence": evidence}
                orchestrator.transition(task["id"], "completed", result=result)
                completed.append(task["id"])
                continue

            if task["kind"] == "plan_actions":
                result = {
                    "ok": True,
                    "handled_by": "orchestrator",
                    "task": task["kind"],
                    "execution_boundary": "registered_workspace_project_worker_or_existing_agent_confirmation_gate",
                    "target": target.get("target"),
                    "workspace": workspace,
                }
                orchestrator.transition(task["id"], "completed", result=result)
                completed.append(task["id"])
                continue

            if task["kind"] == "execute" and target.get("target") and workspace:
                try:
                    autofix = await _run_project_autofix_cycle(ProjectWorkerAutofixRequest(
                        target=target["target"], goal=project["goal"], max_retries=2, run_lint=False
                    ))
                    status = autofix.get("status")
                    if status == "verified":
                        orchestrator.transition(task["id"], "completed", result={"handled_by": "self_correcting_project_worker", **autofix})
                        completed.append(task["id"])
                        continue
                    block_reason = autofix.get("reason") or status or "project worker did not verify"
                    orchestrator.transition(task["id"], "blocked", result=autofix, error=str(block_reason))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": status or "blocked", "worker": autofix}
                except Exception as exc:
                    logger.exception("project-worker execute task failed")
                    orchestrator.transition(task["id"], "failed", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "failed", "error": str(exc)}

            if task["kind"] == "verify" and target.get("target") and workspace:
                worker_path = workspace.get("tool_path") or workspace.get("path")
                try:
                    verification = await asyncio.to_thread(project_verify_workspace, worker_path, run_tests=True, run_build=True, run_lint=False)
                except FileNotFoundError as exc:
                    orchestrator.transition(task["id"], "blocked", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "error": str(exc)}
                if not verification.get("ok"):
                    orchestrator.transition(task["id"], "blocked", result=verification, error=f"verification status: {verification.get('status')}")
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "verification": verification}
                orchestrator.transition(task["id"], "completed", result=verification)
                completed.append(task["id"])
                continue

            read_only = task["kind"] in {"reason", "inspect", "workspace_inspect", "verify"}
            if read_only:
                instruction = {
                    "reason": "Analyze the goal and produce a concise understanding. Do not modify files, send messages, change devices, or take external actions.",
                    "inspect": "Inspect available context using read-only tools only. Do not modify anything. Report useful files, services, configuration, blockers, and missing access.",
                    "workspace_inspect": "Inspect the target workspace using read-only tools only. Check its structure, README/configuration, tests, and obvious blockers. Do not modify anything.",
                    "verify": "Verify the work completed in this project using read-only checks. Do not modify anything. Start your response with VERIFIED: YES if the goal appears satisfied, otherwise VERIFIED: NO and list blockers.",
                }[task["kind"]]
            else:
                instruction = "Execute the current task toward the project goal. Use available tools when appropriate. Sensitive actions must go through the existing confirmation mechanism. Do not claim an action happened unless the tool returned success."

            prompt = f"""You are the execution worker inside Jarvis Autopilot.

Project goal: {project['goal']}
Target: {target.get('target') or 'general'}
Workspace context: {workspace_text}
Current task: {task['title']}
Task capability: {task.get('capability', task['kind'])}

Instruction: {instruction}

Rules:
- Preserve the user's exact goal; do not invent requirements.
- For read-only tasks, absolutely no writes or external side effects.
- For execute tasks, use the existing tool router and confirmation gates.
- Report concrete evidence, blockers, and required approvals.
"""
            messages = [m.model_dump() for m in apply_system_prompt([ChatMessage(role="user", content=prompt)], body.assistant_profile)]
            selected = select_agent_model(body.model, messages, body.assistant_profile)
            result = await _run_agent_loop(selected, messages, body.assistant_profile, None, 8)
            if result.get("confirmation"):
                ticket = result["confirmation"].get("confirmation_id")
                if ticket:
                    _update_confirmation_resume(ticket, project_id=body.project_id, task_id=task["id"])
                orchestrator.transition(task["id"], "awaiting_approval", result=result)
                return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "awaiting_approval", "result": result}

            answer = ((result.get("message") or {}).get("content") or "")
            if task["kind"] == "verify" and re.search(r"VERIFIED\s*:\s*NO", answer, re.IGNORECASE):
                orchestrator.transition(task["id"], "blocked", result=result, error=answer[:2000])
                return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "result": result}
            orchestrator.transition(task["id"], "completed", result=result)
            completed.append(task["id"])
        except Exception as exc:
            logger.exception("orchestrator task failed")
            orchestrator.transition(task["id"], "failed", error=str(exc))
            raise
    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "step_limit"}


@app.post("/v1/orchestrator/autopilot")
@limiter.limit("10/minute")
async def orchestrator_autopilot(request: Request, body: OrchestratorAutopilotRequest):
    """Create a project and immediately run its safe steps."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    project = orchestrator.create_project(body.goal)
    result = await orchestrator_execute(request, OrchestratorRunRequest(
        project_id=project["id"], max_steps=body.max_steps,
        model=body.model, assistant_profile=body.assistant_profile,
    ))
    result["created_project_id"] = project["id"]
    return result


# ==================== Adaptive Voice Turn ====================


@app.post("/v1/voice/turn")
@limiter.limit("30/minute")
async def voice_turn(request: Request, body: VoiceTurnRequest):
    """Unified voice turn: tool-intent speech runs the agent (JSON), everything
    else streams auto-routed chat (SSE) so TTS can start on the first sentence."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    if any(m.role == "system" for m in body.messages):
        raise HTTPException(400, "system messages are not accepted by the voice endpoint")
    if not any(m.role == "user" and m.content.strip() for m in body.messages):
        raise HTTPException(400, "at least one user message is required")
    last_text = next(m.content for m in reversed(body.messages) if m.role == "user")

    model_messages = apply_system_prompt(list(body.messages), body.assistant_profile)

    if body.conversation_id is not None:
        if not db.conversation_exists(body.conversation_id):
            raise HTTPException(status_code=404, detail=f"conversation {body.conversation_id} not found")
        conversation_profile = db.get_conversation_profile(body.conversation_id)
        requested_profile = body.assistant_profile.lower()
        if conversation_profile != requested_profile:
            raise HTTPException(status_code=409, detail="conversation belongs to a different profile")
        history = db.get_conversation(body.conversation_id, limit=MAX_HISTORY_MESSAGES)
        incoming = [{"role": m.role, "content": m.content} for m in body.messages]
        if history and incoming[:len(history)] == history:
            new_messages = incoming[len(history):]
        else:
            new_messages = incoming
        for msg in new_messages:
            if msg["role"] == "user":
                db.add_message(body.conversation_id, "user", msg["content"])

    if sum(len(m.content) for m in model_messages) > JARVIS_MAX_CONTEXT_CHARS:
        system_msgs = [m for m in model_messages if m.role == "system"][:1]
        non_system = [m for m in model_messages if m.role != "system"]
        kept = []
        total = sum(len(m.content) for m in system_msgs)
        for msg in reversed(non_system):
            if total + len(msg.content) > JARVIS_MAX_CONTEXT_CHARS and kept:
                break
            kept.append(msg); total += len(msg.content)
        model_messages = system_msgs + list(reversed(kept))

    if select_voice_path(last_text, body.assistant_profile) == "agent":
        selected_model = select_agent_model(body.model, [m.model_dump() for m in model_messages], body.assistant_profile)
        result = await _run_agent_loop(selected_model, [m.model_dump() for m in model_messages],
                                       body.assistant_profile, body.conversation_id, 8)
        result["kind"] = "agent"
        result["model"] = selected_model
        return result

    selected_model = select_model(body.model, [m.model_dump() for m in model_messages if m.role != "system"], body.assistant_profile)
    stream_request = ChatRequest(
        model=selected_model,
        messages=model_messages,
        stream=True,
        temperature=body.temperature,
        max_tokens=body.max_tokens or 512,
        conversation_id=body.conversation_id,
        use_rag=False,
        assistant_profile=body.assistant_profile,
    )
    return StreamingResponse(
        stream_chat(stream_request),
        media_type="text/event-stream",
        headers={"X-Voice-Model": selected_model, "X-Voice-Path": "stream"},
    )


@app.post("/v1/agent/confirm")
@limiter.limit("30/minute")
async def agent_confirm(request: Request, body: ConfirmationRequest):
    """Approve one exact pending action and resume its original multi-step task."""
    if not body.confirmed:
        with _PENDING_LOCK:
            _PENDING_ACTIONS.pop(body.confirmation_id, None)
        return {"status": "cancelled"}

    item = _consume_confirmation(body.confirmation_id)
    result = await execute_tool(ToolRequest(tool=item["tool"], arguments=item["arguments"], confirmed=True))
    resume = item.get("resume")
    if not resume:
        return result

    messages = list(resume["messages"])
    messages.append({"role": "tool", "content": json.dumps(result, default=str)})
    # Resume from the exact point at which approval interrupted the task.
    resumed_result = await _run_agent_loop(
        resume["model"], messages, resume["assistant_profile"],
        resume.get("conversation_id"), resume["max_tool_rounds"],
        initial_tool_result=None,
    )
    project_id = resume.get("project_id")
    task_id = resume.get("task_id")
    if project_id and task_id:
        if resumed_result.get("confirmation"):
            orchestrator.transition(task_id, "awaiting_approval", result=resumed_result)
        else:
            orchestrator.transition(task_id, "completed", result=resumed_result)
        resumed_result["project"] = orchestrator.get_project(project_id)
    return resumed_result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
