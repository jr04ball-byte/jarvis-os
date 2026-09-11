# Jarvis OS V24 — Release Candidate Refinement Pass

Version: **24.0.0-rc1**

This pass was performed against the final uploaded project tree and focused on
closing the remaining architecture/documentation/testing gaps without rewriting
working provider/orchestrator logic.

## Completed

### Composition and lifecycle
- Kept `api-gateway/main.py` as a small composition root (159 lines).
- Fixed `.env` loading order so local environment values are loaded **before**
  `deps.py`/providers read configuration.
- Added a FastAPI lifespan that publishes the container, event bus, and
  telemetry collector and performs deterministic best-effort service shutdown.
- Added `create_app()` for cleaner test/application construction.
- Preserved top-level auth/request-logging middleware callables for compatibility.
- Removed hardcoded V23 root-version drift; routes now use `APP_VERSION`.

### Dependency/container hardening
- Bumped app/package version to `24.0.0-rc1`.
- Added thread-safe container access/overrides.
- Added container shutdown/instance eviction.
- Replaced eager compatibility singletons (`db`, `rag`, `monitor`,
  `orchestrator`, `project_worker_runs`) with lazy container proxies, removing
  the largest import-time persistence side effect while preserving old imports.

### Event and telemetry plane
- Made EventBus subscriber/error bookkeeping thread-safe.
- Added catch-all `Event` subscribers for observability consumers.
- Preserved stable process bus identity so imported producers cannot silently
  split onto stale buses.
- Expanded telemetry to verification, provider, task, worker, health, and memory
  lifecycle events.
- Added safe attach/detach/reset behavior to the telemetry collector.
- Added `/v1/telemetry/events` Server-Sent Events for live Command Center state.
- Verification now emits pass/fail events.
- `verified`, `blocked`, and `blocked_environment` worker terminal states now
  emit `WorkerCompleted` lifecycle events (previously only completed/failed/
  cancelled did).

### Project Worker fixture and evidence
- Added explicit `git.available=true` evidence on healthy inspections/diffs.
- Added deterministic fixture repository under
  `tests/fixtures/project_worker_repo/`.
- Added an end-to-end fixture regression covering:
  inspect → baseline → fail → classify → repair → diff → re-verify → pass.

### Command Center
- Updated branding to V24.
- Connected the existing Blender-backed dashboard to live SSE lifecycle events.
- Reduced safety-net polling from 3.5 seconds to 15 seconds when live events are
  available, improving responsiveness while reducing idle API load.
- Auth-enabled browser sessions retain authenticated polling because native
  EventSource cannot attach bearer headers.

### CI and documentation
- Added Node 22 voice tests to GitHub Actions.
- Added compileall to CI.
- Added deterministic report generation/checking to prevent another stale-report
  incident (`tools/generate_repo_reports.py`).
- Rebuilt README, architecture, dependency graph, provider, worker, API map,
  repository tree, metrics, and test reports around the actual V24 code.
- Converted the old zero-collected `test_agent_state.py` helper into a real test.
- Updated local test instructions.

### Distribution security cleanup
- Removed runtime SQLite databases and the OAuth token-encryption key from the
  release tree.
- Hardened `.gitignore` for `api-gateway/data/*` while retaining `.gitkeep`.
- Secret-pattern scan found no embedded Google/OpenAI/GitHub bearer/API keys in
  the source distribution.

## Verification evidence

- **145/145 Python tests passed**
- **19/19 Node voice tests passed**
- Python compileall passed
- Coverage run passed: **65% overall**
- Orchestrator 95%, Project Worker 87%, Router 89%, Telemetry 95%, main 82%
- 104 registered FastAPI/UI routes
- generated report drift check passed
- compose/workflow YAML parsing passed
- no local-module import cycles found by the V24 AST audit

## Known production-host checks

Docker/GPU/cloud/OAuth/Windows-device integrations cannot be truthfully certified
inside this Linux build container. Before promoting RC1 to a production release,
run the target-host acceptance checklist in `TEST-INSTRUCTIONS.txt`.

## Next structural hotspot (not a release blocker)

`api-gateway/services.py` is now the largest Python module (915 lines). It is no
longer an application entry-point monolith, but future feature growth should
extract agent-loop/tool execution, connection/artifact helpers, and worker-cycle
orchestration into smaller service modules rather than letting this become the
next `main.py`.
