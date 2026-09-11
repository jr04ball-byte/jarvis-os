# V24 — main.py Decomposition Proposal (COMPLETED)

Status: **COMPLETED / HISTORICAL PLAN**. The measurements below describe the
pre-refactor V23 baseline. V24 completed the decomposition: the current
`api-gateway/main.py` is a 159-line composition root, domain handlers live in
`routes/`, persistence moved to `store.py`, request models to `schemas.py`, and
services/configuration were extracted. See `V24-RELEASE-CANDIDATE.md` and
`reports/architecture.md` for the current measured state.

## 1. Current state (measured, not estimated)

- `api-gateway/main.py`: **2975 lines, ~40% of all Python (7336 LOC / 33 files)**
- **97 route handlers**: 65× `/v1/*`, 26× UI/static, 4× `/auth/*`, 1× `/gmail`, 1× `/calendar`
- Embedded persistence layer living inside the web layer:
  - `ConversationDB` (lines 375–481, 107 LOC)
  - `DocumentRAG` (lines 485–574, 90 LOC)
  - `PerformanceMonitor` (lines 578–601, 24 LOC)
- 30 Pydantic request models scattered across the file
- Largest handlers: `_run_project_autofix_cycle` (97), `stream_chat` (73),
  `_run_agent_loop` (68)
- Module-level singletons created at import: `db`, `rag`, `orchestrator`,
  `project_worker_runs`, `router_engine` (via `brains`), `CONNECTIONS_PATH`
- Only 3 test files import `main` (`test_routing_v181`, `test_voice_turn`,
  `test_sqlite_lifecycle`) — refactor blast radius is small **if** `main`
  keeps working as an importable shim
- No production module imports `main` (leaf node). `main` imports 12 local
  modules. Cycle risk is low **provided** new route modules never import
  each other (rule below)

Why this is the top debt: every feature since V13 landed in one file, so
ownership, review, and testing all bottleneck on `main.py`. Nothing is
broken — this is about the next 10 features, not the last 10.

## 2. Target layout (new files only; nothing deleted until Phase 4)

```text
api-gateway/
  main.py              # thin shim: `from app import app` + uvicorn entry (kept)
  app.py               # NEW: create_app() factory, middleware, lifespan, router registration
  deps.py              # NEW: singletons (db, rag, orchestrator, worker runs, paths)
  schemas.py           # NEW: all ~30 Pydantic request models, moved verbatim
  store.py             # NEW: ConversationDB, DocumentRAG, PerformanceMonitor, moved verbatim
  routes/
    __init__.py        # NEW: helper to register all routers
    chat.py            # NEW: chat/completions, completions-rag, compare, sales/chat, gemini/*
    conversations.py   # NEW: conversations, messages, documents, research/search
    artifacts.py       # NEW: artifacts CRUD
    voice.py           # NEW: voice/*, deepgram-*
    google.py          # NEW: auth/google/*, gmail/*, calendar/*
    system.py          # NEW: /, /health, /ready, /stats, /v1/system/*, /v1/performance
    orchestrator.py    # NEW: /v1/orchestrator/*, /v1/agent/pending
    worker.py          # NEW: /v1/project-worker/*, /v1/tools*, /v1/computer/*
    connections.py     # NEW: /v1/connections/*, /v1/model-lab/*, /v1/models
    agent.py           # NEW: /v1/agent/chat, /v1/agent/confirm, agent loop helpers
    ui.py              # NEW: dashboard/voice/companion pages + blender assets
```

## 3. Dependency rules (enforced by review + a future import-lint gate)

1. Routes import from `deps`, `schemas`, `store`, services — never from each other.
2. Stores import stdlib only (`sqlite3`, `threading`, `pathlib`).
3. Providers own no business logic (already true — Cycle 3 contracts pin this).
4. `main.py` stays importable until Phase 4 and keeps exporting `app`
   (uvicorn entry `main:app` and the 3 test importers keep working).

## 4. Phased execution (one branch + PR + green CI per phase)

- **P0 — scaffolding, zero behavior change**: add `app.py` (`create_app`
  building the identical app), `deps.py` (singletons moved), `schemas.py`
  (models moved). `main.py` imports from them. Verify: suite + TestClient
  smoke (`/health`, `/ready`, one chat route).
- **P1 — stores**: move `ConversationDB`/`DocumentRAG`/`PerformanceMonitor`
  to `store.py`; `main` re-exports. Verify: suite + new
  `test_sqlite_lifecycle.py` (already pins Windows file-lock behavior).
- **P2 — routes in 3 PRs**: (a) system + ui + connections, (b) chat +
  conversations + artifacts + voice, (c) orchestrator + worker + agent +
  google. Each PR moves handlers verbatim (formatting only via ruff).
  Verify per PR: suite + route smoke list.
- **P3 — cleanup**: `main.py` becomes `from app import app` + `__main__`
  block. Add import-lint gate (no route-to-route imports). Close this proposal.

Estimated: 6 PRs total. No phase changes behavior; diffs are moves +
import rewrites.

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Circular imports via shared singletons | Singletons live only in `deps.py`; routes never import each other |
| Behavior drift during moves | Verbatim moves; per-phase smoke list; suite must stay 77+ green |
| Lifespan/startup ordering (lifespan vs import-time DB creation) | P0 keeps import-time semantics identical; lifespan migration is a separate proposal |
| Merge conflicts with parallel feature work | Phases are small, ordered, and rebased one at a time |

## 6. Approval requested

Reply with one of:

- **APPROVE** — execution starts next cycle at P0, one PR per phase
- **APPROVE WITH CHANGES** — list module-boundary changes, proposal updated first
- **DEFER** — proposal stays on `main` as the recorded plan; cycles continue elsewhere
  (next: per-site PLW1510 subprocess review)

## 7. Approval record (V24 Principal Engineering Directive — APPROVE WITH CHANGES)

- Execution **approved**; the directive's 9-router list
  (`chat, providers, workers, projects, auth, dashboard, voice, telemetry, health`)
  **supersedes** the 12-module sketch in §2, and `main.py` target is **< 400 lines**.
- Added scope beyond this proposal: DI container (`AppContainer`), event bus,
  telemetry platform, coverage > 90%. These land as later phases after the
  route split; singletons move in P1 (not P0 — avoids a `main`↔`deps` cycle).
## 8. As-built record (P0–P2 merged; proposal superseded where noted)

Executed on branches `v24/p0-schemas`, `v24/p1-stores`, `v24/p2-routes`
(PRs #8–#10, all CI-green). Final layout differs from §2 in these deliberate ways:

- `services.py` (~900 lines) holds shared business logic (selection, confirmations,
  connections, snapshots, artifacts, agent loop, autofix cycle, tool cores).
  Route-to-route calls were eliminated by extracting cores
  (`execute_tool_core`, `create_artifact`, `web_search`, `build_tools_list`)
  instead of cross-importing routers.
- `deps.py` holds config, limiter, paths, caches, and (P3) the `AppContainer`.
- Route files: `health, dashboard, voice, chat, workers, projects, providers,
  auth, telemetry` (+ `brains` router untouched).
- `main.py`: 2975 → ~110 lines (app assembly only). Target <400 MET.
- Deduped along the way: duplicate prompt/helper definitions, dead CORE prompt
  copy, pnpm lint loop-var bug, `__file__`-relative assets anchored per module.
- Tests were updated to import from new homes (no disabled tests); 2 new tests
  added during the split (middleware presence, pnpm discovery).

## Appendix — evidence (Cycle 6 AST audit, abbreviated)

- Classes: `ConversationDB` 375–481, `DocumentRAG` 485–574,
  `PerformanceMonitor` 578–601; 27 request-model classes; 97 routes
  (65 `/v1`, 26 `/`, 4 `/auth`, 1 `/gmail`, 1 `/calendar`)
- `main` local imports: `brains`, `compute_manager`, `model_lab`,
  `orchestrator`, `project_worker`, `providers`, `security`,
  `workspace_registry`, `google_oauth`, `tools`, `local_tools`
- Importers of `main`: only `tests/test_routing_v181.py`,
  `tests/test_voice_turn.py`, `tests/test_sqlite_lifecycle.py`
- Full machine-readable map: Cycle 6 work notes (audit script output,
  retained in session history)
