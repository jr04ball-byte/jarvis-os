# slowapi is preferred in production.  When the package is unavailable (for
# example in an offline bootstrap environment), use a small in-process limiter
# so Jarvis remains protected instead of failing to start.
import logging
import os
import time

from deps import (
    AI_API_TOKEN,
    AI_REQUIRE_AUTH,
    APP_VERSION,
    RECENT_REQUESTS,
    RateLimitExceeded,
    limiter,
)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
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
from routes import chat, dashboard, health, voice, workers

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

from routes import auth, projects, providers, telemetry

app.include_router(brain_router)
app.include_router(telemetry.router)
app.include_router(auth.router)
app.include_router(providers.router)
app.include_router(projects.router)
app.include_router(workers.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(dashboard.router)
app.include_router(health.router)


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
