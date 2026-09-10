# slowapi is preferred in production.  When the package is unavailable (for
# example in an offline bootstrap environment), use a small in-process limiter
# so Jarvis remains protected instead of failing to start.
import asyncio
import json
import logging
import os
import re
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
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
)
from model_lab import benchmark as model_lab_benchmark
from model_lab import hf_search as model_lab_hf_search
from model_lab import lm_load as model_lab_lm_load
from model_lab import lm_unload as model_lab_lm_unload
from model_lab import lmstudio_inventory as model_lab_lmstudio
from model_lab import ollama_inventory as model_lab_ollama
from model_lab import overview as model_lab_overview
from schemas import (
    ArtifactPatch,
    ArtifactRequest,
    ChatMessage,
    ConnectionUpdate,
    ConversationCreate,
    ConversationMessage,
    DocumentUpload,
    OrchestratorAutopilotRequest,
    OrchestratorGoal,
    OrchestratorRunRequest,
    OrchestratorTransition,
    ProjectWorkerAutofixRequest,
)
from services import (
    _PENDING_ACTIONS,
    _PENDING_LOCK,
    _PENDING_TTL_SECONDS,
    CONNECTION_CATALOG,
    _artifact_path,
    _artifact_read,
    _cached_compute_snapshot,
    _cached_target_health,
    _configuration_snapshot,
    _connection_snapshot,
    _load_connection_overrides,
    _pending_snapshot,
    _run_agent_loop,
    _run_project_autofix_cycle,
    _save_connection_overrides,
    _update_confirmation_resume,
    apply_system_prompt,
    build_tools_list,
    create_artifact,
    select_agent_model,
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
from brains import router as brain_router
from brains import status as brain_status_snapshot
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import verify_workspace as project_verify_workspace
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

app.include_router(brain_router)
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


# ==================== Gemini Live Browser Token ====================

# Browser clients MUST use Gemini Live's constrained endpoint with a short-lived
# auth token.  Never hand a permanent Gemini API key to JavaScript.


# ==================== OpenCode Go Sub-Agent Relay ====================


# ==================== Deepgram Voice ====================


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


# ==================== Sales Machine ====================


# ==================== RAG Chat ====================


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
