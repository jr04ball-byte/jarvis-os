# Jarvis OS — Worker Engine (generated Cycle 7, source-verified)

Two halves: **Orchestrator** (durable goal→plan→tasks state machine) and
**Project Worker** (repo inspection → implementation support → verification).
Pipeline: inspect → understand → plan → implement → test → diagnose → repair
→ verify → evidence. Nothing completes without verification (callers enforce;
stores are dumb durable state).

## Orchestrator (`api-gateway/orchestrator.py`, 95% covered)
Pure-Python, no LLM dependency. `build_plan(goal)` decomposes via regex
classifiers (`DEEP_RE`/`TOOL_RE`) + workspace target matching
(`workspace_registry.context_for`).

`OrchestratorStore` (SQLite, per-operation short-lived connections):
- `jarvis_projects(id, goal, plan_json, status, created_at, updated_at)`
- `jarvis_tasks(id, project_id, position, title, kind, risk, brain, capability,
  status, depends_on, result_json, error, created_at, updated_at)`
- `jarvis_audit(id, project_id, task_id, event, detail_json, created_at)`
- Statuses: pending → ready → running/blocked/awaiting_approval →
  completed/failed/cancelled. Completing a task readies its successor;
  all-complete flips the project. Terminal tasks are immutable.
- Key methods: `create_project`, `get_project`, `next_task` (dependency-aware),
  `transition` (audited), `list_projects` (dashboard summaries), `close()`.

## Project Worker (`api-gateway/project_worker.py`, 87% covered)
Sandbox-aware repo intelligence (never reads `.env`/keys; skips
`.git/node_modules/.venv/dist/...`; `run_command` allowlists safe commands
with timeouts and `CommandEvidence` tails):
- `inspect_workspace` → files + fingerprints + baselines; `capture/compare_workspace_baseline`,
  `capture_git_diff`
- `discover_commands` → pytest/npm/pnpm/yarn/bun test/build/lint commands
  (pnpm lint fix landed Cycle 4)
- `select_verification_commands`, `verify_workspace` (tests/build/lint),
  `classify_verification_failure`, `make_worker_prompt` / `make_repair_prompt`
  (bounded prompts: scope, permission, no-secrets rules)
- `WorkerRunStore` (`worker_runs` table): `create/get/list/update` with
  attempt history — resumable runs.

## How they connect
`main.py` exposes both (`/v1/orchestrator/*`, `/v1/project-worker/*`);
`brains.py` routes coding goals to the opencode worker; the orchestrator owns
task state while the worker owns repo evidence. Audit table + worker-run
attempts are the evidence trail the directive demands.

## Gaps
- No end-to-end test drives a full inspect→verify loop against a fixture repo
  (unit-covered per step; proposed in test-report gaps).
- `_run_project_autofix_cycle` (97 LOC in `main.py`) orchestrates from the web
  layer — candidate to move behind the worker engine in V24.
