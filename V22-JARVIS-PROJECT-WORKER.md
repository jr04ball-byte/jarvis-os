# Jarvis V22 — Repository-Aware Project Worker

V22 turns the V21 multi-brain foundation into a bounded software project worker.

## Added

- `api-gateway/project_worker.py`
  - repository inspection with explicit `.env` / secret-file exclusion
  - Git branch, HEAD, working-tree status and diff evidence
  - automatic test/build/lint command discovery for Python and Node projects
  - evidence-based verification using real command exit codes
  - bounded command timeouts and output caps
  - scoped OpenCode worker prompt with non-destructive boundaries
- Registered-target-only Project Worker API:
  - `POST /v1/project-worker/inspect`
  - `POST /v1/project-worker/verify`
  - `POST /v1/project-worker/implement`
- Autopilot integration:
  - `workspace_inspect` now uses deterministic repository evidence collection
  - registered project `execute` tasks are delegated to OpenCode in the exact workspace
  - final `verify` runs discovered tests/build checks and blocks on failure
- OpenCode hardening:
  - workspace passed as `x-opencode-directory` so the server operates in the real project location
  - optional `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD` support
  - session creation aligned with the current server contract
- FastAPI version bumped to `22.0.0`.

## Safety boundary

V22 only allows the implementation endpoint to target workspaces from Jarvis's configured project registry. The worker is instructed not to touch secrets, deploy, publish, force-reset Git, or perform unrelated external side effects. Jarvis captures the resulting diff and independently runs repository checks.

OpenCode's shell still has host-user authority, so production deployments should additionally enforce a narrow OpenCode permission policy and run the worker under a least-privileged OS account/container.

## Validation

- Python: 43/43 tests passed
- Voice engine: 19/19 tests passed
- `python -m compileall -q api-gateway tests`: passed
- New V22 tests cover Python/Node command discovery, `.env` exclusion, real pytest verification, Git evidence, and worker boundaries.

## Next — V22.1

- bounded diagnose/fix/retest loop
- baseline diff fingerprinting so Jarvis can distinguish pre-existing user changes from worker changes
- task-specific test selection
- stronger OpenCode permission config generation
- resumable worker runs / crash recovery
- project health score and dashboard surfaces
