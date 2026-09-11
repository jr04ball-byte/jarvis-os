"""Jarvis OS API composition root.

V24 keeps this module intentionally small: environment bootstrap, application
construction, middleware, router registration, and lifecycle management only.
Business logic belongs in services/routes/providers/worker modules.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager

# Load local development environment *before* importing deps/providers.  Those
# modules read configuration at import time, so loading dotenv afterwards would
# silently ignore .env values during native development.
try:
    from dotenv import load_dotenv

    _ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(_ENV_PATH):
        load_dotenv(_ENV_PATH)
except ImportError:
    pass

from brains import router as brain_router  # noqa: E402
from deps import (  # noqa: E402 - env bootstrap must happen first
    AI_API_TOKEN,
    AI_REQUIRE_AUTH,
    APP_VERSION,
    RECENT_REQUESTS,
    RateLimitExceeded,
    container,
    limiter,
)
from events import bus as event_bus  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from routes import (  # noqa: E402
    auth,
    chat,
    dashboard,
    health,
    projects,
    providers,
    telemetry,
    voice,
    workers,
)

# Re-exported for backward compatibility with older integrations/tests.
from store import ConversationDB as ConversationDB  # noqa: E402,F401
from store import DocumentRAG as DocumentRAG  # noqa: E402,F401
from store import PerformanceMonitor as PerformanceMonitor  # noqa: E402,F401
from telemetry_collector import collector  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own process lifecycle without creating hidden service instances.

    Services remain lazy; the app only publishes the composition objects for
    dependencies/diagnostics and guarantees durable services are closed on
    shutdown.  This makes TestClient/uvicorn lifecycle deterministic.
    """
    app.state.container = container
    app.state.event_bus = event_bus
    app.state.telemetry = collector
    logger.info("Jarvis OS %s starting", APP_VERSION)
    try:
        yield
    finally:
        container.close()
        logger.info("Jarvis OS %s stopped", APP_VERSION)


async def api_auth(request: Request, call_next):
    protected = (
        request.url.path.startswith("/v1/")
        or request.url.path.startswith("/gmail/")
        or request.url.path.startswith("/calendar/")
        or request.url.path.startswith("/auth/google/")
    )
    if AI_REQUIRE_AUTH and protected:
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {AI_API_TOKEN}" if AI_API_TOKEN else ""
        if not expected or not secrets.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "AI System authentication required"})
    return await call_next(request)


async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - start
        RECENT_REQUESTS.append({
            "method": request.method,
            "path": request.url.path,
            "status": 500,
            "duration_ms": int(duration * 1000),
            "ts": time.time(),
        })
        logger.exception("%s %s - unhandled error - %.2fs", request.method, request.url.path, duration)
        raise

    duration = time.perf_counter() - start
    RECENT_REQUESTS.append({
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": int(duration * 1000),
        "ts": time.time(),
    })
    logger.info("%s %s - %s - %.2fs", request.method, request.url.path, response.status_code, duration)
    return response


def create_app() -> FastAPI:
    app = FastAPI(title="AI System — Jarvis Experience", version=APP_VERSION, lifespan=lifespan)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):  # noqa: ARG001
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    app.add_middleware(
        CORSMiddleware,
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
    app.include_router(telemetry.router)
    app.include_router(auth.router)
    app.include_router(providers.router)
    app.include_router(projects.router)
    app.include_router(workers.router)
    app.include_router(chat.router)
    app.include_router(voice.router)
    app.include_router(dashboard.router)
    app.include_router(health.router)

    app.middleware("http")(api_auth)
    app.middleware("http")(log_requests)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
