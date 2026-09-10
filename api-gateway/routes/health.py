"""V24 P2: health routes (moved verbatim from main.py)."""

import time

from brains import status as brain_status_snapshot
from deps import APP_VERSION, JARVIS_STARTED_AT
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from services import _configuration_snapshot

router = APIRouter()


@router.get("/")
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


@router.get("/health")
async def health_check():
    """Fast liveness probe: no external network calls."""
    return {
        "status": "healthy", "version": APP_VERSION,
        "uptime_seconds": int(max(0, time.time() - JARVIS_STARTED_AT)),
    }


@router.get("/ready")
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
