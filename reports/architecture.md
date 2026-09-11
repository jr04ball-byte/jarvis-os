# Jarvis OS — Architecture (V24 release-candidate audit)

## Summary
Jarvis OS is a local-first AI control plane built around FastAPI. The current
release candidate separates HTTP routing, provider inference, deterministic
orchestration, repository work, tools, memory, security, telemetry, and UI
surfaces while preserving compatibility with earlier Jarvis integrations.

Measured repository facts:

- Application version: `24.0.0-rc1`
- `api-gateway/main.py`: 159 lines
- `api-gateway/services.py`: 915 lines
- 34 Python files in `api-gateway/`
- 25 Python test files
- 104 registered FastAPI/UI routes
- no local-module import cycles found by the V24 AST audit

## Runtime topology

### API gateway
`api-gateway/main.py` is now the composition root. It owns:

1. `.env` bootstrap before configuration imports
2. FastAPI app creation
3. CORS and auth/request-log middleware registration
4. router registration
5. application lifespan publication/cleanup

It no longer owns domain business logic or persistence implementations.

### Domain routers
`api-gateway/routes/` contains domain HTTP adapters:

- `auth.py`
- `chat.py`
- `dashboard.py`
- `health.py`
- `projects.py`
- `providers.py`
- `telemetry.py`
- `voice.py`
- `workers.py`

### Intelligence and providers
`brains.py` owns provider failover execution. `intelligence_router.py` decides
which provider/mode to use and tracks routing/circuit state. Provider adapters
live under `providers/` and remain subordinate to Jarvis authority.

### Worker plane
`orchestrator.py` owns durable Goal → Plan → Task state. `project_worker.py`
owns repository inspection, baselines, command discovery, verification,
failure classification, and resumable worker-run evidence.

### Persistence and memory
`store.py` owns short-lived SQLite conversation/RAG connections and the
performance monitor. Orchestrator and Project Worker state use their own SQLite
stores. Runtime data resolves from `API_DATA_DIR`.

### Dependency/lifecycle container
`deps.py` owns configuration plus `AppContainer`. Compatibility names (`db`,
`rag`, `monitor`, `orchestrator`, `project_worker_runs`) are lazy proxies, so
importing the application no longer has to construct SQLite-backed services.
The FastAPI lifespan publishes the container/event bus/telemetry collector via
`app.state` and performs best-effort service shutdown.

### Event and observability plane
`events.py` provides a typed, thread-safe in-process event bus. Producers emit
provider/worker/task/verification lifecycle events. `telemetry_collector.py`
subscribes without adding dependencies back into producers.

`GET /v1/telemetry/events` exposes dashboard-safe lifecycle events as SSE.
`dashboard.html` uses those events to trigger near-immediate refreshes when API
auth is disabled and keeps a 15-second polling safety net.

## Request path — chat

```text
POST /v1/chat/completions
        ↓
routes/chat.py
        ↓
services / brains.complete()
        ↓
IntelligenceRouter.decide()
        ↓
provider fallback chain
        ↓
ProviderAdapter.complete()/stream()
        ↓
response + telemetry/event evidence
```

## Request path — autonomous project work

```text
High-level goal
   ↓
Orchestrator durable plan
   ↓
Project Worker inspection + baseline
   ↓
OpenCode bounded implementation
   ↓
Targeted verification
   ↓
code/environment failure classification
   ↓
bounded repair cycle
   ↓
verification evidence + durable run state
```

## Security model

- Unknown/disallowed tools fail closed.
- Sensitive tool categories require exact confirmation tickets.
- OpenCode is scoped to registered workspaces.
- Project Worker skips `.env`, private keys, credential extensions, `.git`, and
  build/vendor directories.
- Gemini Live returns a short-lived server-minted credential, never the
  permanent API key.
- Runtime token keys and SQLite data are excluded from the release package and
  ignored by Git.

## Command Center
The dashboard remains a thin control surface over backend state. Blender-baked
motion assets are served from `api-gateway/assets/`. Live state comes from the
Command Center overview, telemetry summary, and event stream rather than being
encoded in the visual assets themselves.

## Remaining architectural hotspot
`services.py` is now the largest Python module (915 lines). Its responsibilities
are coherent enough for this release candidate, but the next large structural
refactor should split agent-loop/tool execution, connection/artifact helpers,
and Project Worker orchestration into smaller service modules while retaining
stable route contracts.

## Release-candidate conclusion
The former `main.py` monolith is resolved. The remaining architecture is
layered, cycle-free in the measured local graph, lifecycle-aware, event-capable,
and covered by deterministic worker/provider/orchestration tests. Host-specific
production readiness still depends on real Docker/GPU/cloud/OAuth connectivity.
