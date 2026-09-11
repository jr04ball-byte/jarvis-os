# Jarvis OS V25 RC1

See [V25 release and adoption guide](V25-RELEASE.md) for current behavior, setup, and validation. Earlier version notes below describe the supplied V24 baseline.

# Jarvis OS V24 Release Candidate

Jarvis OS is a local-first AI operating system and automation control plane. It
routes work across Gemini, OpenAI, Ollama, and OpenCode while keeping authority,
memory, permissions, verification, and project state inside Jarvis.

**Current application version:** `24.0.0-rc1`

## What is included

- Multi-brain intelligence routing with provider health, fallbacks, telemetry,
  and circuit breakers.
- Deterministic Goal → Plan → Task orchestration with durable SQLite state.
- Repository-aware Project Worker with baselines, targeted verification,
  bounded repair cycles, and evidence collection.
- Explicit approval gates for sensitive tools and computer-control actions.
- Persistent conversations and TF-IDF RAG.
- Gmail/Calendar OAuth, Home Assistant, Windows host bridge, local tools, and
  optional voice/research integrations.
- Jarvis Command Center with Blender-baked motion assets and live telemetry.
- Server-Sent Events (`/v1/telemetry/events`) so the dashboard can react to
  worker/provider/verification activity instead of relying on tight polling.
- Desktop, iOS, browser companion, and voice surfaces.

## Architecture

```text
Clients / Command Center / Voice
             │
             ▼
      FastAPI composition root
             │
     ┌───────┼────────┐
     ▼       ▼        ▼
  Routes   Brains   Services
             │        │
             ▼        ▼
   Intelligence    Workers / Tools
      Router           │
             │         │
     ┌───────┼─────────┘
     ▼       ▼       ▼       ▼
  Gemini   OpenAI   Ollama  OpenCode

Shared control plane:
Memory • Security • Event Bus • Telemetry • Verification • Durable State
```

`api-gateway/main.py` is intentionally a composition root only. Domain HTTP
handlers live in `api-gateway/routes/`; provider implementations live in
`api-gateway/providers/`; cross-cutting business logic lives in `services.py`,
with durable worker/orchestrator state in dedicated modules.

See `reports/architecture.md` and `reports/dependency-graph.md` for the measured
repository view.

## Quick start — native development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r api-gateway/requirements.txt pytest
python -m uvicorn main:app --app-dir api-gateway --host 127.0.0.1 --port 8000
```

Open:

- Command Center: `http://127.0.0.1:8000/dashboard`
- API docs: `http://127.0.0.1:8000/docs`
- Voice: `http://127.0.0.1:8000/voice-live`
- Liveness: `http://127.0.0.1:8000/health`
- Readiness: `http://127.0.0.1:8000/ready`

Windows helper scripts remain available at the repository root, including
`START-LOCAL-TEST.bat`, `START-JARVIS.bat`, and the host-bridge launchers.

## Docker

```bash
docker compose up -d --build
```

The gateway talks to Ollama over the compose network. GPU ownership belongs to
the model runtime; the FastAPI gateway itself does not need CUDA.

## Configuration

Copy `.env.example` to `.env` and set only the integrations you use. Jarvis can
run locally with Ollama even when cloud providers are not configured.

Common optional settings:

- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `DEEPGRAM_API_KEY`
- Google OAuth client settings
- `AI_API_TOKEN` + `AI_REQUIRE_AUTH=true`
- Registered project workspace paths
- Host bridge / Home Assistant settings

**Never commit `.env`, OAuth token files, encryption keys, or runtime SQLite
state.** Runtime data belongs under `API_DATA_DIR`; `api-gateway/data/*` is
ignored except for its placeholder.

## Intelligence roles

| Provider | Intended role |
|---|---|
| Gemini | Primary/general reasoning and orchestration support |
| OpenAI | Deep reasoning, architecture, difficult debugging |
| Ollama | Private/local/high-volume tasks and offline fallback |
| OpenCode | Bounded repository implementation worker |

Jarvis—not any provider—owns execution authority, memory, approvals, and final
verification.

## Project Worker

The worker pipeline is evidence-driven:

```text
inspect → baseline → implement → verify → classify failure
       → bounded repair → re-verify → evidence
```

The test suite contains a deterministic fixture repository under
`tests/fixtures/project_worker_repo/` that starts with an intentional code bug
and exercises failure → repair → successful re-verification.

## Command Center

The Command Center serves Blender-baked WebM/MP4/poster assets from
`api-gateway/assets/` and consumes:

- `/v1/command-center/overview` for safe aggregate state
- `/v1/telemetry/summary` for event counters
- `/v1/telemetry/events` for live SSE lifecycle events

If API authentication is enabled, the dashboard continues to use authenticated
polling because the native browser `EventSource` API cannot attach a bearer
header. Without API auth, live events trigger near-immediate dashboard refreshes
with a slower polling safety net.

## Verification

Run the full local verification stack:

```bash
python -m compileall -q api-gateway tests
python -m pytest -q
node --test tests/voice-engine.test.js
python tools/generate_repo_reports.py --check
```

Or:

```bash
npm run verify
```

GitHub Actions also runs Ruff gates, Python tests, voice tests, compile checks,
and generated-report drift checks.

## Current measured baseline

At this release-candidate pass:

- 144 Python tests passing
- 19 Node voice tests passing
- 65% total Python coverage in the local measured run
- 95% coverage on the telemetry collector
- 95% on the orchestrator
- 87% on the Project Worker
- 89% on the intelligence router
- 104 registered FastAPI/UI routes
- no local-module import cycles detected by the audit

Cloud/live integration branches are intentionally not forced through offline CI;
the core routing/orchestration/security contracts are covered with mocks and
fixtures.

## Repository reports

`reports/` contains the current architecture and engineering baseline:

- `architecture.md`
- `dependency-graph.md`
- `api-map.md` — generated
- `repo-tree.txt` — generated
- `repo-metrics.json` — generated
- `providers.md`
- `worker-engine.md`
- `test-report.md`

Run `python tools/generate_repo_reports.py` after structural/API changes. CI
uses `--check` to prevent generated reports from silently becoming stale.

## Security boundaries

- Unknown tools fail closed.
- Sensitive actions require explicit confirmation tickets.
- Project Worker is constrained to registered workspaces and avoids credential
  files by design.
- Gemini Live uses server-minted short-lived credentials rather than exposing
  the long-lived Gemini API key to the browser.
- Runtime OAuth encryption keys and SQLite databases are not distribution
  artifacts.

## Release status

`24.0.0-rc1` is a release candidate: the architecture has been hardened and the
full local automated suite passes, but production deployment still requires
host-specific checks such as Docker/GPU availability, configured cloud
credentials, OAuth redirects, and real external-service connectivity.

See `V24-RELEASE-CANDIDATE.md` for the final refinement-pass changes and
verification evidence.
