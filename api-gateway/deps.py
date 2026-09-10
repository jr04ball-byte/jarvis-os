"""V24 P1: application singletons (first step of the DI container).

Data paths resolve identically to the old main.py import-time code:
`API_DATA_DIR` env or `<api-gateway>/data`. main.py re-exports every name
for backward compatibility (uvicorn entry, tests).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import Request
from orchestrator import OrchestratorStore
from project_worker import WorkerRunStore
from store import ConversationDB, DocumentRAG, PerformanceMonitor

logger = logging.getLogger(__name__)


def data_dir() -> Path:
    return Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")))


ORCHESTRATOR_DB = data_dir() / "orchestrator.db"
PROJECT_WORKER_DB = ORCHESTRATOR_DB.parent / "project_worker.db"

db = ConversationDB()
rag = DocumentRAG()
monitor = PerformanceMonitor()
orchestrator = OrchestratorStore(ORCHESTRATOR_DB)
project_worker_runs = WorkerRunStore(str(PROJECT_WORKER_DB))


# ---- Configuration + rate limiting (moved from main.py in V24 P2) ----
try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
except ImportError:
    from collections import defaultdict, deque
    from functools import wraps

    class RateLimitExceeded(Exception):
        pass

    def get_remote_address(request: Request) -> str:
        client = getattr(request, "client", None)
        return getattr(client, "host", "unknown") or "unknown"

    class Limiter:
        def __init__(self, key_func=get_remote_address):
            self.key_func = key_func
            self._hits = defaultdict(deque)
            self._lock = threading.RLock()

        @staticmethod
        def _parse(spec: str):
            amount, unit = spec.split("/", 1)
            amount = int(amount.strip())
            unit = unit.strip().lower()
            windows = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
            if unit not in windows:
                raise ValueError(f"unsupported rate-limit unit: {unit}")
            return amount, windows[unit]

        def limit(self, spec: str):
            max_hits, window = self._parse(spec)
            def decorator(func):
                @wraps(func)
                async def wrapped(*args, **kwargs):
                    request = kwargs.get("request") or next((a for a in args if isinstance(a, Request)), None)
                    key = self.key_func(request) if request is not None else "unknown"
                    bucket = (func.__module__, func.__qualname__, key, spec)
                    now = time.monotonic()
                    with self._lock:
                        hits = self._hits[bucket]
                        cutoff = now - window
                        while hits and hits[0] <= cutoff:
                            hits.popleft()
                        if len(hits) >= max_hits:
                            raise RateLimitExceeded()
                        hits.append(now)
                    return await func(*args, **kwargs)
                return wrapped
            return decorator


JARVIS_STARTED_AT = time.time()


RECENT_REQUESTS = deque(maxlen=120)


_PROJECT_HEALTH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


_COMPUTE_CACHE: tuple[float, dict[str, Any]] | None = None


limiter = Limiter(key_func=get_remote_address)


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")


OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "auto")


AI_API_TOKEN = os.getenv("AI_API_TOKEN", "")


AI_REQUIRE_AUTH = os.getenv("AI_REQUIRE_AUTH", "false").lower() == "true"


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "").strip()


DEEPGRAM_TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-asteria-en")


DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "flux-general-en")


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()


GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global").strip() or "global"


EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()


ARTIFACTS_DIR = Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


MAX_HISTORY_MESSAGES = int(os.getenv("JARVIS_MAX_HISTORY_MESSAGES", "24"))


JARVIS_MAX_CONTEXT_CHARS = int(os.getenv("JARVIS_CONTEXT_CHARS", "14000"))


JARVIS_MAX_RAG_CHARS = int(os.getenv("JARVIS_RAG_CHARS", "6000"))


JARVIS_COMPUTE_MODE = os.getenv("JARVIS_COMPUTE_MODE", "auto").strip().lower()


JARVIS_ONE_MODEL_POLICY = os.getenv("JARVIS_ONE_MODEL_POLICY", "true").lower() == "true"


JARVIS_MODEL_LOCK = asyncio.Lock()


CONNECTIONS_PATH = Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "connections.json"


_CONNECTIONS_LOCK = threading.Lock()


GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")


GEMINI_LIVE_WS_URL = os.getenv(
    "GEMINI_LIVE_WS_URL",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained",
)


GEMINI_AUTH_TOKEN_URL = os.getenv(
    "GEMINI_AUTH_TOKEN_URL",
    "https://generativelanguage.googleapis.com/v1alpha/auth_tokens",
)


OPENCODE_SERVER_URL = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")
