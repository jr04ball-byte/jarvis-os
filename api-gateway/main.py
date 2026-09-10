from compute_manager import select_mode
from compute_manager import snapshot as compute_snapshot
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
    DEEP_MODEL,
    FAST_MODEL,
    TOOL_MODEL,
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

# slowapi is preferred in production.  When the package is unavailable (for
# example in an offline bootstrap environment), use a small in-process limiter
# so Jarvis remains protected instead of failing to start.
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
import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

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
from orchestrator import OrchestratorStore
from project_worker import WorkerRunStore, make_repair_prompt, make_worker_prompt
from project_worker import capture_git_diff as project_capture_diff
from project_worker import capture_workspace_baseline as project_capture_baseline
from project_worker import classify_verification_failure as project_classify_failure
from project_worker import compare_workspace_baseline as project_compare_baseline
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import project_health as project_worker_health_snapshot
from project_worker import verify_workspace as project_verify_workspace
from providers import ProviderMessage
from security import allowed as tool_allowed
from security import policy_snapshot as security_policy_snapshot
from security import requires_confirmation
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

app = FastAPI(title="AI System — Jarvis Experience", version="23.0.0")
JARVIS_STARTED_AT = time.time()
RECENT_REQUESTS = deque(maxlen=120)
_PROJECT_HEALTH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMPUTE_CACHE: tuple[float, dict[str, Any]] | None = None

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
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

# Recent-message window for conversation history. Keeps long chats inside a
# useful context budget; the next iteration should add token budgeting and/or
# automatic summarization (see architecture notes).
MAX_HISTORY_MESSAGES = int(os.getenv("JARVIS_MAX_HISTORY_MESSAGES", "24"))
JARVIS_MAX_CONTEXT_CHARS = int(os.getenv("JARVIS_CONTEXT_CHARS", "14000"))
JARVIS_MAX_RAG_CHARS = int(os.getenv("JARVIS_RAG_CHARS", "6000"))
JARVIS_COMPUTE_MODE = os.getenv("JARVIS_COMPUTE_MODE", "auto").strip().lower()
JARVIS_ONE_MODEL_POLICY = os.getenv("JARVIS_ONE_MODEL_POLICY", "true").lower() == "true"
JARVIS_MODEL_LOCK = asyncio.Lock()

# In-memory confirmation tickets. Tickets are single-use and expire quickly.
# This prevents the client from changing the arguments between proposal and approval.
_PENDING_LOCK = threading.Lock()
_PENDING_ACTIONS: dict[str, dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 600

def _create_confirmation(tool_name: str, arguments: dict, reason: str) -> dict:
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k,v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for k in expired:
            _PENDING_ACTIONS.pop(k, None)
        _PENDING_ACTIONS[ticket] = {"tool": tool_name, "arguments": dict(arguments), "reason": reason, "created_at": now}
    return {"status":"confirmation_required","confirmation_id":ticket,"action":tool_name,"args":dict(arguments),"reason":reason,"expires_in":_PENDING_TTL_SECONDS}

def _consume_confirmation(ticket: str) -> dict:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.pop(ticket, None)
    if not item:
        raise HTTPException(404, "confirmation expired or not found")
    if time.time() - item["created_at"] > _PENDING_TTL_SECONDS:
        raise HTTPException(410, "confirmation expired")
    return item


# ==================== Smart Model Routing ====================

_DEEP_HINTS = re.compile(r"\b(code|coding|debug|debugging|program|programming|architect|architecture|algorithm|algorithms|refactor|repository|repo|sql|python|javascript|typescript|powershell|bash|docker|api design|system design|reasoning|analyze|analysis|deep|complex|math|prove|proof|research|security|threat model|consensus|raft|paxos|quorum|replicat\w+|leader\s+election|fault\s+toleran\w+|distributed\s+(systems?|databases?|computing|architecture|consensus)|database\s+architecture|consistenc\w*)\b", re.IGNORECASE)


def select_model(requested: str, messages: list | None = None, assistant_profile: str = "general") -> str:
    """Resolve an explicit model or automatically choose fast vs deep local model."""
    requested = (requested or "auto").strip()
    if requested and requested.lower() not in {"auto", "default"}:
        return requested
    # Never route on injected system prompts: they contain deep-hint words
    # (e.g. "code") that would otherwise force every request to deep.
    convo = [m for m in (messages or []) if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) != "system"]
    text = " ".join(str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")) for m in convo)
    profile = (assistant_profile or "general").lower()
    if profile == "sales":
        return FAST_MODEL
    return DEEP_MODEL if _DEEP_HINTS.search(text) else FAST_MODEL


def select_agent_model(requested: str, messages: list | None = None, assistant_profile: str = "general") -> str:
    """Select a model for the tool-calling agent. Gemma 3 4B does not support
    Ollama tool calling, so automatic agent requests must use a tool-capable model."""
    requested = (requested or "auto").strip()
    if requested.lower() in {"auto", "default"}:
        return TOOL_MODEL
    if requested == FAST_MODEL:
        return TOOL_MODEL
    return requested


# ==================== Core AI Behavior ====================

CORE_SYSTEM_PROMPT = """You are the core intelligence of a private local AI system.

RESPONSE STYLE
- Give the direct answer first in one or two sentences.
- Use clear Markdown headings when structure helps.
- Use numbered steps for procedures and bullets for multiple facts or options.
- Be practical, specific, and actionable.
- Prefer ready-to-use code, configuration, prompts, and templates.
- Avoid decorative fluff and unnecessary preambles.
- Ask at most one concise clarifying question when truly necessary; otherwise make a sensible assumption and proceed.

VOICE-FIRST BEHAVIOR
- Assume every message may be spoken aloud.
- Jarvis is a voice-enabled assistant inside the Jarvis interface. The interface handles microphone capture, speech recognition, audio playback, and voice interaction.
- When speech reaches you through Jarvis, assume the microphone and transcription pipeline are already working. Never tell the user that you cannot access their microphone or initiate voice conversations, and never send generic microphone-setup instructions unless an actual voice subsystem error is reported.
- Voice-to-voice is a first-class interaction mode: listen naturally, understand the user's spoken request, reason and use tools, then answer naturally through speech.
- Speak like a warm, calm, intelligent human assistant: conversational, confident, concise, and slightly expressive. Avoid robotic phrasing, canned disclaimers, excessive headings, and unnecessary repetition.
- For spoken answers, favor short natural sentences and pauses. Do not read Markdown formatting, URLs, code fences, or UI instructions aloud; summarize them naturally.
- Never rely on visual-only references such as "see above" or "see below" without stating the important information.
- When explaining code or configuration, describe what it does in plain language before the code block.
- When the user asks for a spoken walkthrough, explain it conversationally and step by step.

ACCURACY AND TOOLS
- Never claim that a tool, plugin, email, calendar action, web search, file operation, or other external action happened unless it actually succeeded.
- Clearly distinguish facts, tool results, recommendations, and configuration that still needs to be completed.
- Prefer local processing and free/open-source components when practical.

CODE AND FORMATTING
- Put executable code in fenced code blocks with a language tag.
- Make examples complete and copy-pasteable when practical.
- Keep one response coherent and easy to render in a ChatGPT-style interface.

LOCAL HOME AI
- Act as the central intelligence of the local AI environment.
- Coordinate memory, RAG, local models, voice, and connected tools through the AI gateway.
- Optimize for privacy, reliability, low latency, and useful real-world assistance.

AGENT TOOL USE
- When a Google account is needed, call google_accounts first and use a connected account; never invent an email address.
- Use tools when the user asks for a real action. Do not merely describe how to do it if a connected tool can perform it.
- Read/check state before changing it when practical.
- Discover devices/accounts before acting when identifiers are unknown.
- Never guess a device entity_id, Google account, message ID, event ID, or file path.
- Sensitive actions require confirmation and must stop until the user confirms.
- For multi-step tasks, preserve earlier tool results and continue from the exact stopping point after approval.

SAAS SEPARATION
- The AI core, voice interface, and Sales Machine are separate capabilities.
- Do not assume the Email Agent SaaS is installed, connected, or available.
- Do not mix Email Agent implementation details into general AI or Sales Machine behavior unless a real integration is explicitly connected.
"""

SALES_SYSTEM_PROMPT = """You are the voice-first Sales Machine inside a private local AI system.

Your job is to help run and improve a sales operation through natural conversation. You are separate from the Email Agent SaaS and must never assume that Email Agent code, credentials, inboxes, or customer data are connected.

SALES BEHAVIOR
- Act like a practical sales operator and sales coach.
- Help with lead qualification, prospecting, follow-up strategy, objection handling, appointment setting, pipeline prioritization, scripts, offers, and conversion improvement.
- When given a lead or prospect, identify the next best sales action and explain why briefly.
- Write concise, natural scripts that sound like a real salesperson.
- For voice interactions, keep sentences short and conversational.
- Never invent lead information, CRM records, conversations, appointments, or outcomes.
- If a real sales tool is not connected, provide the exact workflow, API shape, or implementation needed rather than pretending the action occurred.
- Protect customer privacy and avoid exposing secrets or credentials.

VOICE SALES MODE
- Speak naturally and confidently.
- Ask one useful qualification question at a time when role-playing with a prospect.
- Handle objections without being aggressive or deceptive.
- Focus on helping the prospect make an informed decision.

SAAS BOUNDARY
- Sales Machine is an independent capability.
- Email Agent is an optional separate SaaS product.
- Voice is an interface that can control the Sales Machine without coupling the Sales Machine to the Email Agent.
"""

def system_prompt_for(profile: str) -> str:
    return SALES_SYSTEM_PROMPT if profile.lower() == "sales" else CORE_SYSTEM_PROMPT

def apply_system_prompt(messages: list[ChatMessage], profile: str) -> list[ChatMessage]:
    """Guarantee the core behavior is present without duplicating it on every request."""
    system = system_prompt_for(profile)
    if messages and messages[0].role == "system":
        return [ChatMessage(role="system", content=system + "\n\nAdditional application instructions:\n" + messages[0].content)] + messages[1:]
    return [ChatMessage(role="system", content=system)] + list(messages)

# ==================== Conversation Memory ====================

class ConversationDB:
    """Conversation history. Opens a short-lived connection per operation:
    a single shared sqlite3 connection across async workers raises
    'Recursive use of cursors not allowed' under concurrent requests."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")), "conversations.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self.create_tables()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_tables(self):
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    model TEXT,
                    assistant_profile TEXT DEFAULT 'general',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "assistant_profile" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN assistant_profile TEXT DEFAULT 'general'")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                )
            """)

    def create_conversation(self, title: str, model: str, assistant_profile: str = "general"):
        profile = assistant_profile.lower() if assistant_profile else "general"
        if profile not in {"general", "sales"}:
            profile = "general"
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO conversations (title, model, assistant_profile) VALUES (?, ?, ?)",
                (title, model, profile)
            )
            return cursor.lastrowid

    def get_conversation_profile(self, conv_id: int) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(assistant_profile, 'general') FROM conversations WHERE id = ?",
                (conv_id,)
            ).fetchone()
            return (row[0] or "general").lower() if row else "general"

    def add_message(self, conv_id: int, role: str, content: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
                (conv_id, role, content)
            )

    def get_conversation(self, conv_id: int, limit: int | None = None):
        with self._lock, self._connect() as conn:
            if limit is None:
                cursor = conn.execute(
                    "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                    (conv_id,)
                )
                rows = cursor.fetchall()
            else:
                cursor = conn.execute(
                    """SELECT role, content FROM messages
                       WHERE conversation_id = ?
                       ORDER BY id DESC LIMIT ?""",
                    (conv_id, limit)
                )
                rows = list(reversed(cursor.fetchall()))
            return [{"role": row[0], "content": row[1]} for row in rows]

    def conversation_exists(self, conv_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conv_id,)
            )
            return cursor.fetchone() is not None

    def list_conversations(self):
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "SELECT id, title, model, COALESCE(assistant_profile, 'general'), created_at FROM conversations ORDER BY created_at DESC"
            )
            return [
                {"id": row[0], "title": row[1], "model": row[2], "assistant_profile": row[3], "created_at": row[4]}
                for row in cursor.fetchall()
            ]

# ==================== RAG System ====================

class DocumentRAG:
    """TF-IDF knowledge base, persisted in SQLite alongside conversations.
    The index rebuilds from disk on startup, so documents survive restarts."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.join(
            os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data")), "conversations.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self.documents = {}
        self.vectorizer = TfidfVectorizer(stop_words='english', max_features=10000)
        self.tfidf_matrix = None
        self.doc_ids = []
        self._init_table()
        self._load_all()
        logger.info("RAG system initialized (TF-IDF)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_table(self):
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _load_all(self):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT doc_id, content FROM rag_documents ORDER BY created_at"
            ).fetchall()
        if rows:
            self.documents = {row[0]: row[1] for row in rows}
            self._rebuild_index()
            logger.info(f"RAG index rebuilt from disk ({len(self.doc_ids)} documents)")

    def _rebuild_index(self):
        self.doc_ids = list(self.documents.keys())
        corpus = [self.documents[did] for did in self.doc_ids]
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)

    def add_document(self, text: str, doc_id: str):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rag_documents (doc_id, content) VALUES (?, ?)",
                (doc_id, text)
            )
        self.documents[doc_id] = text
        self._rebuild_index()
        logger.info(f"Added document: {doc_id} (total: {len(self.doc_ids)})")

    def search(self, query: str, n_results: int = 3):
        if not self.doc_ids or self.tfidf_matrix is None:
            return []

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        top_indices = similarities.argsort()[-n_results:][::-1]

        results = []
        for idx in top_indices:
            if similarities[idx] > 0.05:
                results.append(self.documents[self.doc_ids[idx]])
        return results

    def augment_prompt(self, user_query: str):
        relevant_docs = self.search(user_query)
        if not relevant_docs:
            return user_query

        context = "\n\n".join(relevant_docs)
        return f"""Based on the following context, answer the question.

Context:
{context}

Question: {user_query}

Answer:"""

# ==================== Performance Monitor ====================

class PerformanceMonitor:
    def __init__(self):
        self.stats = defaultdict(lambda: {
            "requests": 0,
            "tokens": 0,
            "total_time": 0.0
        })

    def record(self, model: str, tokens: int, duration: float):
        self.stats[model]["requests"] += 1
        self.stats[model]["tokens"] += tokens
        self.stats[model]["total_time"] += duration

    def get_stats(self):
        result = {}
        for model, data in self.stats.items():
            result[model] = {
                "requests": data["requests"],
                "total_tokens": data["tokens"],
                "avg_tokens_per_request": data["tokens"] / data["requests"] if data["requests"] > 0 else 0,
                "avg_time_seconds": data["total_time"] / data["requests"] if data["requests"] > 0 else 0,
                "tokens_per_second": data["tokens"] / data["total_time"] if data["total_time"] > 0 else 0
            }
        return result

# Initialize components
db = ConversationDB()
rag = DocumentRAG()
monitor = PerformanceMonitor()
ORCHESTRATOR_DB = Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "orchestrator.db"
orchestrator = OrchestratorStore(ORCHESTRATOR_DB)
PROJECT_WORKER_DB = ORCHESTRATOR_DB.parent / "project_worker.db"
project_worker_runs = WorkerRunStore(str(PROJECT_WORKER_DB))

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

CONNECTIONS_PATH = Path(os.getenv("API_DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "connections.json"
_CONNECTIONS_LOCK = threading.Lock()

CONNECTION_CATALOG = [
    {"id":"google","name":"Google Workspace","category":"accounts","kind":"oauth","description":"Gmail, Calendar and future Google services","requires_confirmation":False},
    {"id":"home_assistant","name":"Home Assistant","category":"home","kind":"service","description":"Lights, switches, climate, fans and TV/media","requires_confirmation":True},
    {"id":"host_bridge","name":"Windows Host Bridge","category":"computer","kind":"service","description":"Open approved local files through the host bridge","requires_confirmation":False},
    {"id":"ollama","name":"Ollama","category":"ai","kind":"local","description":"Local LLM runtime and models","requires_confirmation":False},
    {"id":"whisper","name":"Local Whisper Voice","category":"ai","kind":"browser","description":"Browser-local speech recognition using WASM/fp32","requires_confirmation":False},
    {"id":"deepgram","name":"Deepgram Voice","category":"ai","kind":"cloud_voice","description":"Deepgram Flux speech recognition and Aura speech synthesis","requires_confirmation":False},
    {"id":"exa","name":"Optional Web Research","category":"ai","kind":"cloud_research","description":"Optional Exa web research; disabled unless EXA_API_KEY is configured","requires_confirmation":False},
    {"id":"comfyui","name":"ComfyUI","category":"creative","kind":"local_tool","description":"Local image generation and workflow execution","requires_confirmation":True},
    {"id":"blender","name":"Blender","category":"creative","kind":"local_tool","description":"3D projects, scenes, animation and rendering","requires_confirmation":True},
    {"id":"unreal","name":"Unreal Engine 5","category":"creative","kind":"local_tool","description":"Projects, levels, Sequencer and rendering","requires_confirmation":True},
    {"id":"ffmpeg","name":"FFmpeg","category":"creative","kind":"local_tool","description":"Video/audio trim, concat, transcode and captions","requires_confirmation":True},
    {"id":"python","name":"Python","category":"computer","kind":"local_tool","description":"Local automation and scripts","requires_confirmation":True},
    {"id":"iphone","name":"iPhone Companion","category":"devices","kind":"companion","description":"PWA companion for voice and AI control","requires_confirmation":False},
    {"id":"sales_machine","name":"Sales Machine","category":"business","kind":"service","description":"Separate sales workspace/service","requires_confirmation":True},
    {"id":"email_agent","name":"Email Agent","category":"business","kind":"service","description":"Separate email automation and invoice service","requires_confirmation":True},
]

def _load_connection_overrides() -> dict:
    if not CONNECTIONS_PATH.exists(): return {}
    try: return json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
    except Exception: return {}

def _save_connection_overrides(data: dict) -> None:
    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

def _connection_status(c: dict, local: dict, google: dict) -> dict:
    cid=c["id"]; status="available"; detail="Not connected"
    if cid=="google":
        accounts=google.get("accounts",[]); status="connected" if accounts else ("available" if google.get("configured") else "not_configured")
        detail=(accounts[0].get("email") if accounts else ("OAuth configured" if google.get("configured") else "Add Google OAuth credentials"))
    elif cid=="home_assistant":
        status="connected" if tools.HA_URL and tools.HA_TOKEN else "not_configured"; detail="Home Assistant API" if status=="connected" else "Set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN"
    elif cid=="host_bridge":
        status="configured" if tools.HOST_BRIDGE_URL and tools.HOST_BRIDGE_TOKEN else "not_configured"; detail=tools.HOST_BRIDGE_URL if status=="configured" else "Set host bridge URL/token"
    elif cid=="ollama":
        status="connected" if local.get("ollama_online") else "unreachable"; detail=local.get("ollama_model") or "Native Ollama"
    elif cid=="whisper":
        status="ready"; detail="WASM / fp32 browser speech"
    elif cid=="deepgram":
        status="connected" if DEEPGRAM_API_KEY else "not_configured"; detail=("Flux STT + Aura TTS" if DEEPGRAM_API_KEY else "Set DEEPGRAM_API_KEY")
    elif cid=="gemini":
        status="connected" if GEMINI_API_KEY else "not_configured"; detail=(f"{GEMINI_MODEL} • Google Cloud" if GEMINI_API_KEY else "Set GEMINI_API_KEY")
    elif cid=="exa":
        status="connected" if EXA_API_KEY else "not_configured"; detail=("Optional web research" if EXA_API_KEY else "Local-first: no web key configured")
    elif cid in {"comfyui","blender","unreal","ffmpeg","python"}:
        by_id={x.get("id"):x for x in local.get("tools",[])}
        key={"comfyui":"comfyui","blender":"blender","unreal":"unreal_engine_5","ffmpeg":"ffmpeg","python":"python"}[cid]
        item=by_id.get(key,{})
        status="installed" if item.get("installed") else "missing"; detail=item.get("version") or ("Detected" if item.get("installed") else "Install tool to enable")
    elif cid=="iphone": status="available"; detail="/companion"
    elif cid in {"sales_machine","email_agent"}:
        env={"sales_machine":"SALES_MACHINE_URL","email_agent":"EMAIL_AGENT_URL"}[cid]; url=os.getenv(env,"").strip()
        status="configured" if url else "not_configured"; detail=url or f"Set {env} to connect"
    return {**c,"status":status,"detail":detail}

async def _connection_snapshot() -> dict:
    local=local_tools.scan_local_tools()
    ollama_online=False; ollama_model=None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r=await client.get(f"{OLLAMA_URL}/api/tags")
            ollama_online=r.status_code==200
            if ollama_online:
                models=r.json().get("models") or []
                ollama_model=models[0].get("name") if models else None
    except Exception as exc: logger.debug("ollama status probe failed: %s", exc)
    local["ollama_online"]=ollama_online; local["ollama_model"]=ollama_model
    gstore=google_oauth.TokenStore(); ga=[]
    for email in gstore.list_accounts():
        tok=gstore.load_tokens(email) or {}; ga.append({"email":email,"scopes":tok.get("scope","")})
    google={"configured":bool(google_oauth.CLIENT_ID),"accounts":ga}
    overrides=_load_connection_overrides()
    items=[]
    for c in CONNECTION_CATALOG:
        item=_connection_status(c,local,google)
        ov=overrides.get(c["id"],{})
        if ov.get("enabled") is False and item["status"] not in {"not_configured","missing"}: item["status"]="disabled"
        item["enabled"]=ov.get("enabled", True)
        items.append(item)
    return {"connections":items,"categories":["accounts","home","ai","creative","computer","devices","business"]}


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

GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
# Browser clients MUST use Gemini Live's constrained endpoint with a short-lived
# auth token.  Never hand a permanent Gemini API key to JavaScript.
GEMINI_LIVE_WS_URL = os.getenv(
    "GEMINI_LIVE_WS_URL",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained",
)
GEMINI_AUTH_TOKEN_URL = os.getenv(
    "GEMINI_AUTH_TOKEN_URL",
    "https://generativelanguage.googleapis.com/v1alpha/auth_tokens",
)


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

OPENCODE_SERVER_URL = os.getenv("OPENCODE_SERVER_URL", "http://127.0.0.1:4096")


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


def _artifact_path(artifact_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
        raise HTTPException(400, "invalid artifact id")
    return ARTIFACTS_DIR / f"{artifact_id}.json"

def _artifact_read(artifact_id: str) -> dict[str, Any]:
    path = _artifact_path(artifact_id)
    if not path.exists(): raise HTTPException(404, "artifact not found")
    return json.loads(path.read_text(encoding="utf-8"))

@app.get("/v1/artifacts")
async def artifacts_list(limit: int = 30):
    items=[]
    for path in sorted(ARTIFACTS_DIR.glob("*.json"), key=lambda x:x.stat().st_mtime, reverse=True)[:max(1,min(limit,100))]:
        try: items.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc: logger.debug("skipping unreadable artifact %s: %s", path, exc)
    return {"items":items}

@app.post("/v1/artifacts")
async def artifact_create(body: ArtifactRequest):
    artifact_id=secrets.token_hex(16)
    item={"id":artifact_id,"title":body.title,"kind":body.kind,"content":body.content,"metadata":body.metadata,"created_at":datetime.now(timezone.utc).isoformat(),"updated_at":datetime.now(timezone.utc).isoformat()}
    _artifact_path(artifact_id).write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding="utf-8")
    return item

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
    if not EXA_API_KEY:
        return {"configured":False,"message":"EXA_API_KEY is not configured. The AI System remains fully local; web research is an optional add-on."}
    headers={"x-api-key":EXA_API_KEY,"Content-Type":"application/json"}
    payload={"query":body.query,"numResults":body.num_results,"contents":{"highlights":{"maxCharacters":1200}}}
    async with httpx.AsyncClient(timeout=25) as client:
        r=await client.post("https://api.exa.ai/search",headers=headers,json=payload)
        if r.status_code >= 400: raise HTTPException(r.status_code,r.text)
        data=r.json()
    return {"configured":True,"query":body.query,"results":data.get("results",[]) }

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
        "api": {"status": "online", "version": app.version},
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
        out["tools_count"] = len((await list_tools()).get("tools", []))
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
        "status": "healthy", "version": app.version,
        "uptime_seconds": int(max(0, time.time() - JARVIS_STARTED_AT)),
    }


def _pending_snapshot() -> dict[str, Any]:
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k, v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for key in expired:
            _PENDING_ACTIONS.pop(key, None)
        items = [
            {
                "confirmation_id": key, "action": value["tool"], "reason": value["reason"],
                "created_at": value["created_at"], "expires_at": value["created_at"] + _PENDING_TTL_SECONDS,
            }
            for key, value in _PENDING_ACTIONS.items()
        ]
    return {"count": len(items), "items": items}


def _configuration_snapshot() -> dict[str, Any]:
    data_dir = ORCHESTRATOR_DB.parent
    warnings: list[str] = []
    if AI_REQUIRE_AUTH and not AI_API_TOKEN:
        warnings.append("AI_REQUIRE_AUTH is enabled but AI_API_TOKEN is empty")
    if not AI_REQUIRE_AUTH:
        warnings.append("API authentication is disabled; keep Jarvis bound to trusted local/LAN interfaces only")
    if not GEMINI_API_KEY:
        warnings.append("Gemini main brain is not configured")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        warnings.append("OpenAI deep-reasoning provider is not configured")
    try:
        writable = data_dir.exists() and os.access(data_dir, os.W_OK)
    except Exception:
        writable = False
    if not writable:
        warnings.append("Jarvis data directory is not writable")
    return {
        "auth_required": AI_REQUIRE_AUTH, "data_directory_writable": writable,
        "gemini_configured": bool(GEMINI_API_KEY),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "deepgram_configured": bool(DEEPGRAM_API_KEY),
        "warnings": warnings,
    }


@app.get("/ready")
async def readiness_check():
    """Deep readiness probe. 200 means at least one intelligence provider is online."""
    brains = await brain_status_snapshot()
    online = [name for name, info in (brains.get("providers") or {}).items() if info.get("online")]
    config = _configuration_snapshot()
    ready = bool(online) and bool(config.get("data_directory_writable"))
    payload = {"ready": ready, "version": app.version, "online_providers": online, "configuration": config}
    if not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


async def _cached_compute_snapshot(ttl: float = 5.0) -> dict[str, Any]:
    global _COMPUTE_CACHE
    now = time.time()
    if _COMPUTE_CACHE and now - _COMPUTE_CACHE[0] < ttl:
        return _COMPUTE_CACHE[1]
    value = await compute_snapshot()
    _COMPUTE_CACHE = (now, value)
    return value


async def _cached_target_health(target: dict[str, Any], ttl: float = 8.0) -> dict[str, Any]:
    target_id = str(target.get("id") or "")
    now = time.time()
    cached = _PROJECT_HEALTH_CACHE.get(target_id)
    if cached and now - cached[0] < ttl:
        return cached[1]
    health: dict[str, Any] = {"available": False, "status": "not_mounted"}
    if target.get("path_exists") and target.get("path_is_directory"):
        try:
            resolved = _project_worker_target(target_id)["resolved_workspace"]
            h = await asyncio.wait_for(asyncio.to_thread(project_worker_health_snapshot, resolved), timeout=5.0)
            health = {"available": True, "status": "ready" if h.get("ready_for_worker") else "blocked", **h}
        except asyncio.TimeoutError:
            health = {"available": True, "status": "timeout", "blockers": ["project health check timed out"]}
        except Exception as exc:
            health = {"available": True, "status": "error", "blockers": [str(exc)[:500]]}
    _PROJECT_HEALTH_CACHE[target_id] = (now, health)
    return health


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
        "version": app.version, "generated_at": datetime.now(timezone.utc).isoformat(),
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
    catalog = [
        ("file_search", "Search allowed Windows files by name"),
        ("file_content_search", "Search text content in allowed files"),
        ("read_file", "Read a text file from an allowed path"),
        ("open_file", "Open a file on the Windows host"),
        ("write_file", "Create or overwrite a text file"),
        ("gmail_search", "Search Gmail"), ("gmail_read", "Read a Gmail message"),
        ("gmail_send", "Send an email"),
        ("calendar_list", "List upcoming calendar events"),
        ("calendar_create", "Create a calendar event"), ("calendar_update", "Update a calendar event"),
        ("calendar_delete", "Delete a calendar event"),
        ("home_states", "Read Home Assistant device states"),
        ("home_entities", "Discover controllable Home Assistant entities"),
        ("home_device", "Control a supported Home Assistant device"),
        ("local_tools_inventory", "Discover installed local tools and approved capabilities"),
        ("connections_inventory", "Discover safe connection status for Jarvis services"),
        ("artifact_create", "Create a persistent Jarvis workspace artifact"),
        ("research_search", "Optional configured web research"),
        ("computer_status", "Check whether opt-in Windows computer control is enabled"),
        ("computer_capabilities", "Inspect Windows computer-control capabilities"),
        ("computer_open", "Open an application or file through the Windows host bridge"),
        ("computer_move", "Move the mouse cursor"), ("computer_click", "Click the Windows desktop"),
        ("computer_type", "Type text into the focused Windows application"),
        ("computer_focus", "Bring a Windows application window to the foreground"),
        ("computer_verify", "Verify the foreground Windows window/process"),
        ("computer_observe", "Capture screen plus foreground state"),
        ("computer_key", "Press a keyboard key or shortcut"),
        ("computer_scroll", "Scroll the active Windows application"),
        ("computer_windows", "List visible Windows application windows"),
        ("computer_processes", "List running Windows processes"),
        ("computer_shell", "Run a bounded PowerShell command on the Windows host bridge"),
        ("computer_screenshot", "Capture the current Windows screen"),
    ]
    return {"tools": [
        {"name": name, "description": description, "confirmation": requires_confirmation(name)}
        for name, description in catalog if tool_allowed(name)
    ]}

@app.post("/v1/tools/execute")
async def execute_tool(req: ToolRequest):
    if not tool_allowed(req.tool):
        raise HTTPException(404, "unknown or disallowed tool")
    if requires_confirmation(req.tool) and not req.confirmed:
        return _create_confirmation(req.tool, req.arguments, "This action changes data, sends a message, or controls a device. Explicit confirmation is required.")
    a=req.arguments
    store=google_oauth.TokenStore()
    # Never let the model select an unconnected Google identity.
    if req.tool.startswith("gmail_") or req.tool.startswith("calendar_"):
        email = str(a.get("email", "")).strip().lower()
        accounts = {x.lower() for x in store.list_accounts()}
        if not email:
            raise HTTPException(400, "email account is required; call google_accounts first")
        if email not in accounts:
            raise HTTPException(403, "Google account is not connected to this AI System")
        a["email"] = email
    try:
        if req.tool=="file_search": return {"result":tools.file_search(a.get("query",""),a.get("root",""),a.get("limit",30))}
        if req.tool=="file_content_search": return {"result":tools.file_content_search(a.get("query",""),a.get("root",""),a.get("limit",20))}
        if req.tool=="read_file": return {"result":tools.read_file(a["path"])}
        if req.tool=="open_file": return {"result":await tools.host_open_file(a["path"])}
        if req.tool=="write_file": return {"result":tools.write_text_file(a["path"],a.get("content",""),a.get("overwrite",False))}
        if req.tool=="gmail_search": return {"result":await google_oauth.gmail_list_messages(email,store,a.get("max_results",20),a.get("query",""))}
        if req.tool=="gmail_read": return {"result":await google_oauth.gmail_get_message(email,store,a["message_id"])}
        if req.tool=="gmail_send": return {"result":await google_oauth.gmail_send_message(email,store,a["to"],a["subject"],a["body"])}
        if req.tool=="calendar_list": return {"result":await google_oauth.calendar_list_events(email,store,a.get("max_results",20),a.get("time_min"))}
        if req.tool=="calendar_create": return {"result":await google_oauth.calendar_create_event(email,store,a["event"])}
        if req.tool=="calendar_update": return {"result":await google_oauth.calendar_update_event(email,store,a["event_id"],a["event"])}
        if req.tool=="calendar_delete": return {"result":await google_oauth.calendar_delete_event(email,store,a["event_id"])}
        if req.tool=="home_states": return {"result":await tools.ha_states()}
        if req.tool=="home_entities": return {"result":await tools.ha_entities(a.get("domains"))}
        if req.tool=="home_device":
            args=dict(a); entity=args.pop("entity_id"); action=args.pop("action"); return {"result":await tools.ha_device(entity,action,**args)}
        if req.tool=="artifact_create":
            body=ArtifactRequest(title=a.get("title","Untitled"),kind=a.get("kind","markdown"),content=a.get("content",""),metadata=a.get("metadata") or {})
            return {"result":await artifact_create(body)}
        if req.tool=="research_search":
            body=ResearchRequest(query=a.get("query",""),num_results=a.get("num_results",5))
            return {"result":await research_search(body)}
        if req.tool.startswith("computer_"):
            if req.tool=="computer_status": return {"result":await tools.computer_status()}
            if req.tool=="computer_capabilities": return {"result":await tools.computer_capabilities()}
            if req.tool=="computer_open": return {"result":await tools.computer_open(a.get("target",""))}
            if req.tool=="computer_move": return {"result":await tools.computer_move(a.get("x",0),a.get("y",0),a.get("duration",0.15))}
            if req.tool=="computer_click": return {"result":await tools.computer_click(a.get("x",0),a.get("y",0),a.get("clicks",1),a.get("button","left"))}
            if req.tool=="computer_type": return {"result":await tools.computer_type(a.get("text",""),a.get("title",""),a.get("process",""),a.get("pid",0))}
            if req.tool=="computer_focus": return {"result":await tools.computer_focus(a.get("title",""),a.get("process",""),a.get("pid",0))}
            if req.tool=="computer_verify": return {"result":await tools.computer_verify(a.get("title",""),a.get("process",""),a.get("pid",0))}
            if req.tool=="computer_observe": return {"result":await tools.computer_observe(a.get("max_width",0),a.get("include_windows",True),a.get("include_processes",False))}
            if req.tool=="computer_key": return {"result":await tools.computer_key(a.get("key",""))}
            if req.tool=="computer_scroll": return {"result":await tools.computer_scroll(a.get("amount",0))}
            if req.tool=="computer_windows": return {"result":await tools.computer_windows()}
            if req.tool=="computer_processes": return {"result":await tools.computer_processes()}
            if req.tool=="computer_shell": return {"result":await tools.computer_shell(a.get("command",""),a.get("timeout",30))}
            if req.tool=="computer_screenshot": return {"result":await tools.computer_screenshot()}
        if req.tool=="local_tools_inventory": return {"result":local_tools.scan_local_tools()}
        if req.tool=="connections_inventory": return {"result":await _connection_snapshot()}
        raise HTTPException(404,"unknown tool")
    except KeyError as e: raise HTTPException(400,f"missing argument: {e.args[0]}")
    except PermissionError as e: raise HTTPException(401,str(e))
    except FileNotFoundError as e: raise HTTPException(404,str(e))
    except httpx.HTTPStatusError as e: raise HTTPException(e.response.status_code,e.response.text)
    except Exception as e:
        logger.exception("tool execution failed")
        raise HTTPException(500,str(e))

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

def _adaptive_options() -> dict:
    """Choose GPU-first, CPU fallback, or explicit hybrid inference."""
    selected = select_mode(JARVIS_COMPUTE_MODE)
    options = {"temperature": 0.7, "num_predict": 1024}
    options.update(selected.get("options", {}))
    return options

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


def _project_worker_target(target_id: str) -> dict:
    try:
        target = workspace_target(target_id)
    except KeyError:
        raise HTTPException(404, "unknown project-worker target")
    workspace = (target.get("workspace") or {}).get("tool_path") or (target.get("workspace") or {}).get("path")
    if not workspace:
        raise HTTPException(409, "target has no workspace configured")
    return {**target, "resolved_workspace": workspace}


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


async def _run_project_autofix_cycle(body: ProjectWorkerAutofixRequest) -> dict:
    """Internal V22.2 worker loop used by both the API and Autopilot."""
    target = _project_worker_target(body.target)
    workspace = target["resolved_workspace"]
    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")

    # Gather richer read-only health evidence before allowing code edits.
    try:
        health = await asyncio.to_thread(project_worker_health_snapshot, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    if not health.get("ready_for_worker", True):
        return {"status": "blocked_environment", "health": health, "reason": "project environment is not ready for worker execution"}

    try:
        if body.resume_run_id:
            run = project_worker_runs.get(body.resume_run_id)
            if run["target"] != body.target or run["workspace"] != workspace or run["goal"] != body.goal:
                raise HTTPException(409, "resume run does not match target/workspace/goal")
            if run["status"] == "verified":
                return {"run": run, "status": "verified", "resumed": True, "health": health}
            baseline = run["baseline"]
        else:
            baseline = await asyncio.to_thread(project_capture_baseline, workspace)
            run = project_worker_runs.create(body.target, workspace, body.goal, baseline)
    except KeyError:
        raise HTTPException(404, "resume project-worker run not found")
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))

    attempts_already = len(run.get("attempts") or [])
    total_allowed = 1 + body.max_retries
    if attempts_already >= total_allowed:
        final = project_worker_runs.update(run["id"], status="blocked")
        return {"run": final, "status": "blocked", "reason": "retry budget exhausted", "health": health}

    last_verification = (run.get("attempts") or [{}])[-1].get("verification") if run.get("attempts") else None
    for attempt_no in range(attempts_already + 1, total_allowed + 1):
        inspection = await asyncio.to_thread(project_inspect_workspace, workspace)
        if attempt_no == 1 and not last_verification:
            prompt = make_worker_prompt(body.goal, workspace, inspection, permission="workspace_write", baseline=baseline)
            phase = "implement"
        else:
            current_delta = await asyncio.to_thread(project_compare_baseline, workspace, baseline)
            prompt = make_repair_prompt(body.goal, workspace, last_verification or {}, current_delta, attempt_no)
            phase = "repair"

        started = time.time()
        try:
            result = await worker.complete(
                [ProviderMessage(role="user", content=prompt)],
                temperature=0.15, max_tokens=4096,
                task_context={
                    "workspace": workspace, "permission": "workspace_write", "goal": body.goal,
                    "run_id": run["id"], "attempt": attempt_no, "phase": phase,
                },
            )
        except Exception as exc:
            logger.exception("project worker autofix attempt failed")
            attempt = {
                "attempt": attempt_no, "phase": phase, "ok": False, "error": str(exc),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
            run = project_worker_runs.update(run["id"], status="failed", attempt=attempt)
            return {"run": run, "status": "failed", "error": str(exc), "health": health}

        delta = await asyncio.to_thread(project_compare_baseline, workspace, baseline)
        changed_paths = delta.get("changed_by_worker") or []
        verification = await asyncio.to_thread(
            project_verify_workspace, workspace, run_tests=True, run_build=True, run_lint=body.run_lint,
            changed_paths=changed_paths,
        )
        classification = verification.get("failure_classification") or project_classify_failure(verification)
        attempt = {
            "attempt": attempt_no, "phase": phase,
            "worker": {"provider": result.provider, "model": result.model, "content": result.content, "metadata": result.metadata},
            "delta": delta, "verification": verification, "failure_classification": classification,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        if verification.get("ok"):
            run = project_worker_runs.update(run["id"], status="verified", attempt=attempt)
            return {"run": run, "status": "verified", "attempts_used": attempt_no, "health": health}

        # V22.2: do not burn repair attempts trying to fix missing runtimes, permissions or network.
        if classification.get("kind") == "environment":
            run = project_worker_runs.update(run["id"], status="blocked_environment", attempt=attempt)
            return {
                "run": run, "status": "blocked_environment", "attempts_used": attempt_no,
                "failure_classification": classification, "health": health,
            }

        last_verification = verification
        run = project_worker_runs.update(run["id"], status="repairing" if attempt_no < total_allowed else "blocked", attempt=attempt)

    return {"run": run, "status": run["status"], "reason": "verification failed after bounded retries", "health": health}


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

async def stream_chat(request: ChatRequest):
    """Stream Ollama output and persist the assistant reply after completion."""
    accumulated: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            ollama_request = {
                "model": request.model,
                "messages": [{"role": msg.role, "content": msg.content} for msg in request.messages],
                "stream": True,
                "think": False,
                "options": {**_adaptive_options(), "temperature": request.temperature, "num_predict": request.max_tokens or 1024}
            }

            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/api/chat",
                json=ollama_request
            ) as response:
                if response.status_code != 200:
                    error_body = (await response.aread()).decode("utf-8", errors="replace")
                    yield f"data: {json.dumps({'error': f'Ollama returned HTTP {response.status_code}: {error_body}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if "message" in data:
                        content = data["message"].get("content", "")
                        if content:
                            accumulated.append(content)
                            chunk = {
                                "id": f"chatcmpl-{int(time.time())}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": request.model,
                                "choices": [{
                                    "delta": {"content": content},
                                    "index": 0,
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"

                    if data.get("done", False):
                        final_chunk = {
                            "id": f"chatcmpl-{int(time.time())}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": request.model,
                            "choices": [{
                                "delta": {},
                                "index": 0,
                                "finish_reason": "stop"
                            }]
                        }
                        yield f"data: {json.dumps(final_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        break

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    finally:
        if request.conversation_id is not None and accumulated:
            try:
                db.add_message(request.conversation_id, "assistant", "".join(accumulated))
            except Exception:
                logger.exception("failed to persist streamed assistant message")

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

# ==================== Core AI Behavior ====================

CORE_SYSTEM_PROMPT = """You are the core intelligence of a private local AI system.

RESPONSE STYLE
- Give the direct answer first in one or two sentences.
- Use clear Markdown headings when structure helps.
- Use numbered steps for procedures and bullets for multiple facts or options.
- Be practical, specific, and actionable.
- Prefer ready-to-use code, configuration, prompts, and templates.
- Avoid decorative fluff and unnecessary preambles.
- Ask at most one concise clarifying question when truly necessary; otherwise make a sensible assumption and proceed.

VOICE-FIRST BEHAVIOR
- Assume every message may be spoken aloud.
- Jarvis is a voice-enabled assistant inside the Jarvis interface. The interface handles microphone capture, speech recognition, audio playback, and voice interaction.
- When speech reaches you through Jarvis, assume the microphone and transcription pipeline are already working. Never tell the user that you cannot access their microphone or initiate voice conversations, and never send generic microphone-setup instructions unless an actual voice subsystem error is reported.
- Voice-to-voice is a first-class interaction mode: listen naturally, understand the user's spoken request, reason and use tools, then answer naturally through speech.
- Speak like a warm, calm, intelligent human assistant: conversational, confident, concise, and slightly expressive. Avoid robotic phrasing, canned disclaimers, excessive headings, and unnecessary repetition.
- For spoken answers, favor short natural sentences and pauses. Do not read Markdown formatting, URLs, code fences, or UI instructions aloud; summarize them naturally.
- Never rely on visual-only references such as "see above" or "see below" without stating the important information.
- When explaining code or configuration, describe what it does in plain language before the code block.
- When the user asks for a spoken walkthrough, explain it conversationally and step by step.

ACCURACY AND TOOLS
- Never claim that a tool, plugin, email, calendar action, web search, file operation, or other external action happened unless it actually succeeded.
- Clearly distinguish facts, tool results, recommendations, and configuration that still needs to be completed.
- Prefer local processing and free/open-source components when practical.

CODE AND FORMATTING
- Put executable code in fenced code blocks with a language tag.
- Make examples complete and copy-pasteable when practical.
- Keep one response coherent and easy to render in a ChatGPT-style interface.

LOCAL HOME AI
- Act as the central intelligence of the local AI environment.
- Coordinate memory, RAG, local models, voice, and connected tools through the AI gateway.
- Optimize for privacy, reliability, low latency, and useful real-world assistance.

AGENT TOOL USE
- When a Google account is needed, call google_accounts first and use a connected account; never invent an email address.
- Use tools when the user asks for a real action. Do not merely describe how to do it if a connected tool can perform it.
- Read/check state before changing it when practical.
- Discover devices/accounts before acting when identifiers are unknown.
- Never guess a device entity_id, Google account, message ID, event ID, or file path.
- Sensitive actions require confirmation and must stop until the user confirms.
- For multi-step tasks, preserve earlier tool results and continue from the exact stopping point after approval.

COMPUTER USE (only when the user asks to operate the Windows PC)
- Loop every computer action as OBSERVE (computer_observe) -> identify target -> FOCUS target -> VERIFY focus -> ACT -> OBSERVE again -> VERIFY the expected result -> only then continue.
- Never report an action as successful merely because an API returned HTTP 200; success requires an observed process, window, or foreground result.
- For typing, supply title/process/pid whenever known; focus must verify before typing; report the focused PID/title; screenshot after consequential typing.
- For opening apps, verify the expected process/window exists and reject launch-error dialogs; do not claim success on timeout.
- Prefer read-only observe/verify/windows/processes for diagnosis before acting.

SAAS SEPARATION
- The AI core, voice interface, and Sales Machine are separate capabilities.
- Do not assume the Email Agent SaaS is installed, connected, or available.
- Do not mix Email Agent implementation details into general AI or Sales Machine behavior unless a real integration is explicitly connected.
"""

SALES_SYSTEM_PROMPT = """You are the voice-first Sales Machine inside a private local AI system.

Your job is to help run and improve a sales operation through natural conversation. You are separate from the Email Agent SaaS and must never assume that Email Agent code, credentials, inboxes, or customer data are connected.

SALES BEHAVIOR
- Act like a practical sales operator and sales coach.
- Help with lead qualification, prospecting, follow-up strategy, objection handling, appointment setting, pipeline prioritization, scripts, offers, and conversion improvement.
- When given a lead or prospect, identify the next best sales action and explain why briefly.
- Write concise, natural scripts that sound like a real salesperson.
- For voice interactions, keep sentences short and conversational.
- Never invent lead information, CRM records, conversations, appointments, or outcomes.
- If a real sales tool is not connected, provide the exact workflow, API shape, or implementation needed rather than pretending the action occurred.
- Protect customer privacy and avoid exposing secrets or credentials.

VOICE SALES MODE
- Speak naturally and confidently.
- Ask one useful qualification question at a time when role-playing with a prospect.
- Handle objections without being aggressive or deceptive.
- Focus on helping the prospect make an informed decision.

SAAS BOUNDARY
- Sales Machine is an independent capability.
- Email Agent is an optional separate SaaS product.
- Voice is an interface that can control the Sales Machine without coupling the Sales Machine to the Email Agent.
"""

def system_prompt_for(profile: str) -> str:
    return SALES_SYSTEM_PROMPT if profile.lower() == "sales" else CORE_SYSTEM_PROMPT

def apply_system_prompt(messages: list[ChatMessage], profile: str) -> list[ChatMessage]:
    """Guarantee the core behavior is present without duplicating it on every request."""
    system = system_prompt_for(profile)
    if messages and messages[0].role == "system":
        return [ChatMessage(role="system", content=system + "\n\nAdditional application instructions:\n" + messages[0].content)] + messages[1:]
    return [ChatMessage(role="system", content=system)] + list(messages)

# ==================== Conversation Memory ====================

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
AGENT_TOOLS = [
    {"type":"function","function":{"name":"google_accounts","description":"List connected Google accounts so the assistant can select the user's account.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"file_search","description":"Search allowed Windows files by name.","parameters":{"type":"object","properties":{"query":{"type":"string"},"root":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["query"]}}},
    {"type":"function","function":{"name":"file_content_search","description":"Search text content in allowed files.","parameters":{"type":"object","properties":{"query":{"type":"string"},"root":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50}},"required":["query"]}}},
    {"type":"function","function":{"name":"read_file","description":"Read a text file from an allowed Windows path.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"write_file","description":"Create or overwrite a text file. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"overwrite":{"type":"boolean"}},"required":["path","content"]}}},
    {"type":"function","function":{"name":"open_file","description":"Open a file using the Windows host bridge.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"gmail_search","description":"Search Gmail. Use a connected account email.","parameters":{"type":"object","properties":{"email":{"type":"string"},"query":{"type":"string"},"max_results":{"type":"integer"}},"required":["email","query"]}}},
    {"type":"function","function":{"name":"gmail_read","description":"Read a Gmail message by message id.","parameters":{"type":"object","properties":{"email":{"type":"string"},"message_id":{"type":"string"}},"required":["email","message_id"]}}},
    {"type":"function","function":{"name":"gmail_send","description":"Send an email. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["email","to","subject","body"]}}},
    {"type":"function","function":{"name":"calendar_list","description":"List upcoming Google Calendar events.","parameters":{"type":"object","properties":{"email":{"type":"string"},"max_results":{"type":"integer"}},"required":["email"]}}},
    {"type":"function","function":{"name":"calendar_create","description":"Create a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event":{"type":"object"}},"required":["email","event"]}}},
    {"type":"function","function":{"name":"calendar_update","description":"Update a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event_id":{"type":"string"},"event":{"type":"object"}},"required":["email","event_id","event"]}}},
    {"type":"function","function":{"name":"calendar_delete","description":"Delete a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event_id":{"type":"string"}},"required":["email","event_id"]}}},
    {"type":"function","function":{"name":"home_states","description":"Read Home Assistant states for lights, thermostats, TVs and other devices.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"home_entities","description":"Discover controllable lights, thermostats, switches, fans and TVs/media players. Use this before selecting a device when the user names a room or TV but not an entity id.","parameters":{"type":"object","properties":{"domains":{"type":"array","items":{"type":"string"}}}}}},
    {"type":"function","function":{"name":"home_device","description":"Control a Home Assistant device such as a light, thermostat, switch, fan, or TV/media player. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"entity_id":{"type":"string"},"action":{"type":"string"},"temperature":{"type":"number"},"volume_level":{"type":"number"}},"required":["entity_id","action"]}}},
    {"type":"function","function":{"name":"local_tools_inventory","description":"Discover installed local tools and approved capabilities. Use this before planning creative work such as making a video; choose tools by capability instead of requiring the user to name Blender, Unreal, ComfyUI, or FFmpeg.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"connections_inventory","description":"Discover the live connection registry and status of accounts, home services, AI, creative tools, computer services, iPhone and separate business services.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"artifact_create","description":"Create a persistent workspace artifact for the user. Use kinds markdown, tasks, mermaid, image, record, or progress.","parameters":{"type":"object","properties":{"title":{"type":"string"},"kind":{"type":"string"},"content":{"type":"string"},"metadata":{"type":"object"}},"required":["title","content"]}}},
    {"type":"function","function":{"name":"research_search","description":"Search the web only when optional Exa research is configured. If unavailable, continue with local knowledge and say web research is not configured.","parameters":{"type":"object","properties":{"query":{"type":"string"},"num_results":{"type":"integer","minimum":1,"maximum":10}},"required":["query"]}}},
    {"type":"function","function":{"name":"computer_status","description":"Check if opt-in Windows computer control is enabled.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_open","description":"Open an application or file using the Windows host bridge. Requires confirmation.","parameters":{"type":"object","properties":{"target":{"type":"string"}},"required":["target"]}}},
    {"type":"function","function":{"name":"computer_type","description":"Type text into a Windows application. Optional title/process/pid focuses and verifies that window first; without a target the current foreground window is reported.","parameters":{"type":"object","properties":{"text":{"type":"string"},"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}},"required":["text"]}}},
    {"type":"function","function":{"name":"computer_focus","description":"Bring a Windows window to the foreground by title, process name, or PID. Verified before returning.","parameters":{"type":"object","properties":{"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"computer_verify","description":"Report the current foreground window and whether a target title/process/PID is actually focused. Basis of observe-act-observe-verify.","parameters":{"type":"object","properties":{"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"computer_observe","description":"Capture the current screen (resized for efficient observation) plus foreground window and top windows. Always call before acting on the PC and again after acting to verify the result.","parameters":{"type":"object","properties":{"max_width":{"type":"integer"},"include_windows":{"type":"boolean"},"include_processes":{"type":"boolean"}}}}},
    {"type":"function","function":{"name":"computer_key","description":"Press a keyboard key or shortcut such as ENTER or CTRL+L when computer mode is enabled.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"computer_screenshot","description":"Capture the current Windows screen when computer mode is enabled.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_capabilities","description":"Inspect the Windows PC capabilities relevant to Jarvis computer control, including NVIDIA GPU, NVIDIA Broadcast, Power Automate Desktop, pywinauto, Turtle Beach/Xbox audio devices, RAM and GPU telemetry.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_move","description":"Move the mouse cursor to a screen coordinate.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"duration":{"type":"number"}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"computer_click","description":"Click the Windows desktop at a screen coordinate. Requires confirmation.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"clicks":{"type":"integer"},"button":{"type":"string"}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"computer_scroll","description":"Scroll the active Windows application.","parameters":{"type":"object","properties":{"amount":{"type":"integer"}},"required":["amount"]}}},
    {"type":"function","function":{"name":"computer_windows","description":"List visible Windows application windows using Windows UI Automation when available.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_processes","description":"List running Windows processes for diagnosis and computer-use planning.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_shell","description":"Run a PowerShell command on the Windows host bridge. Requires confirmation and is intended for administrator/system tasks.","parameters":{"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer"}},"required":["command"]}}},
]

async def _agent_tool(name: str, args: dict, confirmed: bool=False):
    # Tool failures should become structured agent results instead of aborting the
    # entire conversation. This is especially useful for optional integrations
    # such as Home Assistant that may not be configured yet.
    try:
        return await _agent_tool_impl(name, args, confirmed)
    except RuntimeError as exc:
        return {"ok": False, "status": "not_configured", "error": str(exc)}
    except HTTPException as exc:
        if exc.status_code >= 500:
            return {"ok": False, "status": "tool_error", "error": str(exc.detail)}
        raise


async def _agent_tool_impl(name: str, args: dict, confirmed: bool=False):
    if name == "google_accounts": return {"accounts": google_oauth.TokenStore().list_accounts()}
    if name in {"file_search","file_content_search","read_file","write_file","open_file"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name in {"gmail_search","gmail_read","gmail_send","calendar_list","calendar_create","calendar_update","calendar_delete"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name in {"home_states","home_entities","home_device"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name == "local_tools_inventory": return {"result":local_tools.scan_local_tools()}
    if name == "connections_inventory": return {"result":await _connection_snapshot()}
    if name == "artifact_create": return await execute_tool(ToolRequest(tool="artifact_create",arguments=args,confirmed=confirmed))
    if name == "research_search": return await execute_tool(ToolRequest(tool="research_search",arguments=args,confirmed=confirmed))
    if name == "computer_status": return await execute_tool(ToolRequest(tool="computer_status",arguments=args,confirmed=confirmed))
    if name == "computer_open": return await execute_tool(ToolRequest(tool="computer_open",arguments=args,confirmed=confirmed))
    if name == "computer_type": return await execute_tool(ToolRequest(tool="computer_type",arguments=args,confirmed=confirmed))
    if name == "computer_focus": return await execute_tool(ToolRequest(tool="computer_focus",arguments=args,confirmed=confirmed))
    if name == "computer_verify": return await execute_tool(ToolRequest(tool="computer_verify",arguments=args,confirmed=confirmed))
    if name == "computer_observe": return await execute_tool(ToolRequest(tool="computer_observe",arguments=args,confirmed=confirmed))
    if name == "computer_key": return await execute_tool(ToolRequest(tool="computer_key",arguments=args,confirmed=confirmed))
    if name == "computer_screenshot": return await execute_tool(ToolRequest(tool="computer_screenshot",arguments=args,confirmed=confirmed))
    if name == "computer_capabilities": return await execute_tool(ToolRequest(tool="computer_capabilities",arguments=args,confirmed=confirmed))
    if name == "computer_move": return await execute_tool(ToolRequest(tool="computer_move",arguments=args,confirmed=confirmed))
    if name == "computer_click": return await execute_tool(ToolRequest(tool="computer_click",arguments=args,confirmed=confirmed))
    if name == "computer_scroll": return await execute_tool(ToolRequest(tool="computer_scroll",arguments=args,confirmed=confirmed))
    if name == "computer_windows": return await execute_tool(ToolRequest(tool="computer_windows",arguments=args,confirmed=confirmed))
    if name == "computer_processes": return await execute_tool(ToolRequest(tool="computer_processes",arguments=args,confirmed=confirmed))
    if name == "computer_shell": return await execute_tool(ToolRequest(tool="computer_shell",arguments=args,confirmed=confirmed))
    raise HTTPException(404,"unknown agent tool")


def _normalize_tool_args(raw: Any, name: str) -> dict:
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(400, f"invalid tool arguments for {name}")
    if not isinstance(args, dict):
        raise HTTPException(400, f"tool arguments for {name} must be an object")
    return args


def _save_confirmation_state(ticket: str, state: dict) -> None:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.get(ticket)
        if item is not None:
            item["resume"] = state

def _update_confirmation_resume(ticket: str, **updates) -> None:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.get(ticket)
        if item is not None:
            resume = dict(item.get("resume") or {})
            resume.update(updates)
            item["resume"] = resume


async def _run_agent_loop(model: str, messages: list, assistant_profile: str,
                          conversation_id: int | None, max_tool_rounds: int,
                          initial_tool_result: dict | None = None) -> dict:
    """Run the agent until it has a final answer or one sensitive action needs approval.

    The full in-flight state is stored on confirmation tickets, so approving a tool
    resumes the same multi-step task instead of losing the earlier reads/tool calls.
    """
    tool_rounds = 0
    if initial_tool_result is not None:
        messages.append({"role": "tool", "content": json.dumps(initial_tool_result, default=str)})

    async with httpx.AsyncClient(timeout=300.0) as client:
        while tool_rounds < max(1, min(max_tool_rounds, 8)):
            payload = {
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "stream": False,
                "think": False,
                "options": {**_adaptive_options(), "temperature": 0.2, "num_predict": 1024},
            }
            async with JARVIS_MODEL_LOCK:
                r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            if r.status_code != 200:
                raise HTTPException(r.status_code, r.text)
            data = r.json()
            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                answer = msg.get("content", "")
                if conversation_id is not None:
                    try:
                        db.add_message(conversation_id, "assistant", answer)
                    except Exception:
                        logger.exception("agent memory save failed")
                return {"message": {"role": "assistant", "content": answer}, "conversation_id": conversation_id, "tool_rounds": tool_rounds}

            messages.append(msg)
            # Process tool calls in order. If a sensitive action is encountered,
            # preserve the entire conversation state and resume after approval.
            for index, call in enumerate(tool_calls):
                fn = call.get("function", {})
                name = fn.get("name")
                args = _normalize_tool_args(fn.get("arguments", {}), name)
                result = await _agent_tool(name, args, False)
                if isinstance(result, dict) and result.get("status") == "confirmation_required":
                    ticket = result.get("confirmation_id")
                    if ticket:
                        _save_confirmation_state(ticket, {
                            "model": model,
                            "messages": messages,
                            "assistant_profile": assistant_profile,
                            "conversation_id": conversation_id,
                            "max_tool_rounds": max_tool_rounds,
                            "tool_rounds": tool_rounds,
                            "call_index": index,
                            "total_calls": len(tool_calls),
                        })
                    return {
                        "message": {"role": "assistant", "content": f"I need your confirmation before I do that. {result['reason']}"},
                        "confirmation": result,
                        "conversation_id": conversation_id,
                        "tool_rounds": tool_rounds + 1,
                    }
                messages.append({"role": "tool", "content": json.dumps(result, default=str)})
            tool_rounds += 1
    raise HTTPException(500, "agent reached its tool-call limit")


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

_VOICE_TOOL_HINTS = re.compile(r"\b(send|email|e-mail|gmail|inbox|calendar|invite|meeting|schedule|remind|home assistant|turn on|turn off|switch on|switch off|open|launch|create|delete|remove|cancel|control|device|lights?|thermostat|lock|unlock|play|pause|volume|file|folder|search my|check my|sales machine|email agent|approve|confirm)\b", re.IGNORECASE)


def select_voice_path(text: str, assistant_profile: str = "general") -> str:
    """Choose the voice turn transport: 'agent' (tool-capable Qwen, JSON) or
    'stream' (auto-routed chat SSE for low-latency speech)."""
    profile = (assistant_profile or "general").lower()
    if profile == "sales":
        return "agent"
    if _VOICE_TOOL_HINTS.search(text or ""):
        return "agent"
    return "stream"


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
