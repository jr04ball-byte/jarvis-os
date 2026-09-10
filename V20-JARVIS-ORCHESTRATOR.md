# Jarvis V20 — Autonomous Orchestrator

Jarvis V20 adds a deterministic orchestration layer without replacing the existing Gemini, Ollama/Gemma/Qwen, OpenCode, voice, Google, computer-control, or tool systems.

## Architecture

`Goal -> Plan -> Tasks -> Existing Agent/Tool Executor -> Verification -> Audit`

### New modules

- `api-gateway/orchestrator.py` — durable project/task state machine and deterministic goal planner.
- `api-gateway/security.py` — centralized read/write/unknown tool-risk policy.
- `tests/test_orchestrator.py` — planner, lifecycle, and security regression tests.

### New endpoints

- `GET /v1/orchestrator/policy`
- `POST /v1/orchestrator/plan`
- `GET /v1/orchestrator/projects/{project_id}`
- `GET /v1/orchestrator/projects/{project_id}/next`
- `POST /v1/orchestrator/transition`
- `POST /v1/orchestrator/execute`
- `GET /v1/orchestrator/projects/{project_id}/audit`

## Safety model

The orchestrator does not bypass the existing confirmation boundary. Sensitive actions continue through the existing tool executor and confirmation tickets. Unknown tools default to `unknown` and are not automatically classified as safe.

## Storage

Orchestrator state is stored in `data/orchestrator.db` (or `$API_DATA_DIR/orchestrator.db`). It is separate from the conversation database to keep task state independently recoverable.

## Credentials

Existing `.env` values were intentionally left in place for local testing as requested. Rotate any credentials that have been shared outside the intended machine after testing.

## Test status

- Python syntax check: passed for all Python sources.
- New orchestrator tests: **4/4 passed**.
- Existing JavaScript voice tests in the build environment: **19/19 passed**.
- Full existing Python suite could not be collected in this environment because the environment is missing the project's `slowapi` dependency. The source remains syntactically valid; install `api-gateway/requirements.txt` before running the complete suite on the target Windows machine.
