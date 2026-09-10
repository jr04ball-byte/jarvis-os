# Jarvis OS — Architecture (generated Cycle 7, measured)

## What it is
Local-first AI control center ("AI Operating System"): a FastAPI gateway
(`api-gateway/main.py`, 2975 lines) in front of a multi-brain provider layer,
durable worker/orchestrator state in SQLite, and passe-partout UIs
(dashboard, voice, companion, Godseye) plus desktop/iOS companions.

## Runtime topology
- **Docker (production-like)**: `ollama` (LLM runtime) + `open-webui`
  (chat UI) + `api-gateway` (FastAPI on `127.0.0.1:8000`, `API_DATA_DIR=/app/data`
  on the `api_data` volume). Network `ai-network`. GPU via NVIDIA toolkit.
- **Native Windows dev**: `START-LOCAL-TEST.bat` → Python/uvicorn, dashboard at
  `http://127.0.0.1:8000/dashboard`, voice at `/voice`. Data dir defaults to
  `api-gateway/data/` unless `API_DATA_DIR` is set.
- **Host bridge** (`host-bridge/`): opt-in localhost service letting the
  browser UI open approved local files (`AI_HOST_BRIDGE_URL`, default
  `http://host.docker.internal:8765` in Docker).
- Entry point: uvicorn serving `main:app`. No lifespan handlers — singletons
  (`db`, `rag`, `orchestrator`, providers) are created at import time.

## Request path (chat)
`POST /v1/chat/completions` → `brains.complete()` → `IntelligenceRouter.decide()`
(assessment → selected brain + fallback chain + circuit breaker) → provider
`complete()` with failover across the chain → result + attempt log.
`brains.py` owns the loop; providers only reason (Cycle 3 contracts pin this).

## Layers
| Layer | Modules | Owns |
|---|---|---|
| HTTP/adapters | `main.py` (97 routes), `brains.py` | routes, app assembly, failover loop |
| Routing | `intelligence_router.py` | assessment, mode chains, telemetry, circuit breaker |
| Providers | `providers/` (gemini, openai, ollama, opencode + `base`) | LLM/worker calls only |
| Worker engine | `orchestrator.py`, `project_worker.py` | durable plans, inspection, verification |
| Memory/RAG | `DocumentRAG` (in `main.py`), `workspace_registry.py` | TF-IDF docs, workspace allowlist |
| Tools/execution | `tools.py`, `local_tools.py`, `security.py` | tool router, HA/bridge calls, confirmation policy |
| Auth/integrations | `google_oauth.py` | Gmail/Calendar OAuth, encrypted token store |
| Ops | `compute_manager.py`, `model_lab.py` | GPU/VRAM mode, model inventory/bench |
| Clients | `desktop-companion/` (Electron), `ios-companion/` (Swift), `librechat/` | UIs |

## Data
SQLite files under `API_DATA_DIR`: `conversations.db` (chats, messages,
RAG docs), `orchestrator.db` (projects/tasks/audit), `project_worker.db`
(worker runs), `google_tokens.enc` (Fernet-encrypted OAuth tokens),
`connections.json` overrides. All connections are short-lived per-operation
(Cycles 1/4 fixes); stores never share a connection across threads.

## UIs
`dashboard.html` (+ classic), `voice.html`/`voice-live.html` + `voice-engine.js`,
`companion.html` (+ manifest/sw), `godseye.html`; Blender-baked loops in
`assets/` (`tools/blender_*.py` sources). Electron desktop companion,
Swift iOS companion.

## Biggest structural risk
`main.py` holds ~40% of Python including routes, stores, and UI serving —
see `V24-MAIN-DECOMPOSITION-PROPOSAL.md` (awaiting approval) for the split plan.
