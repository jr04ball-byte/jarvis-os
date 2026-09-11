# Jarvis OS — Test Report (V24 release candidate)

## Headline
Final local refinement-pass run:

- **145 Python tests passed, 0 failed** (`python -m pytest -q`)
- **19 Node voice tests passed, 0 failed** (`node --test tests/voice-engine.test.js`)
- `python -m compileall -q api-gateway tests` passed
- generated report drift check passed
- YAML parsing passed for all compose files and GitHub Actions workflow
- total measured Python coverage: **65%**

Environment used for this pass: Linux container, Python 3.13.5, Node 22.16.0.
GitHub Actions targets Python 3.12 and Node 22.

## Coverage snapshot

| Module | Coverage | Notes |
|---|---:|---|
| orchestrator.py | 95% | deterministic state machine strongly pinned |
| providers/base.py | 95% | adapter contract |
| telemetry_collector.py | 95% | lifecycle counters/recent-event ring |
| deps.py | 93% | container/config compatibility layer |
| workspace_registry.py | 93% | registered target safety |
| intelligence_router.py | 89% | selection/fallback/circuit logic |
| project_worker.py | 87% | inspect/verify/baseline/fixture flow |
| events.py | 87% | typed bus, wildcard listeners, async handlers |
| main.py | 82% | composition/lifecycle/middleware |
| brains.py | 77% | provider execution/failover |
| store.py | 72% | short-lived SQLite + RAG |
| services.py | 69% | broad service layer; live/tool branches remain |
| compute_manager.py | 67% | hardware-dependent branches |
| model_lab.py | 67% | runtime-dependent branches |
| live provider implementations | 33–52% | real network paths intentionally offline in CI |
| route adapters | 21–88% | core smoke/contract paths covered; live integrations vary |

**Overall:** 65% over `api-gateway` (4,183 statements, 1,460 missed in this run).
Coverage is strongest around orchestration, routing, security boundaries,
telemetry, and Project Worker—the areas where deterministic behavior matters
most. Lower totals are dominated by live cloud/device/OAuth branches that are
not safe or deterministic to require in offline CI.

## New release-candidate regression coverage

### Composition/lifecycle
- `.env` bootstrap occurs before config imports.
- app version is a single source of truth.
- FastAPI lifespan publishes the container/event bus/telemetry objects.
- lazy compatibility proxies defer durable service construction.
- container shutdown evicts instances for a clean restart.

### Event/telemetry plane
- catch-all lifecycle event subscription.
- provider/task/worker counters.
- verification, health, and memory event counters.
- idempotent telemetry attachment/detachment.
- stable process bus identity (prevents stale imported bus references).
- verified worker runs now emit terminal `WorkerCompleted` events.
- live SSE telemetry route is registered and wired into the dashboard.

### Project Worker fixture
A deterministic fixture repository now executes the whole local verification
story in one test:

`inspect → baseline → failing verification → code classification → minimal
repair → diff evidence → targeted re-verification → pass`.

This closes the prior gap where those primitives were individually tested but
not exercised as one repository workflow.

## CI (`.github/workflows/main.yml`)

CI now runs:

1. Python 3.12 setup
2. Node 22 setup
3. pinned gateway dependencies + pytest + Ruff
4. Ruff import/upgrade/style gates
5. Ruff silent-exception gate
6. all Python tests
7. Node voice-engine tests
8. Python compileall
9. generated-report drift check

## Offline limitations / production checks

The following are not truthfully validated by this container and must be
verified on the target Windows host before production release:

- Docker Compose runtime (`docker` is not installed in this build environment)
- NVIDIA GPU / Ollama real inference
- configured Gemini/OpenAI/Deepgram calls
- Google OAuth redirect/consent flow
- Windows Host Bridge computer control
- Home Assistant device connectivity
- real OpenCode server execution

These are host/integration acceptance checks, not hidden test failures.

## Dependency/environment note
`python -m pip check` reported a Pillow/MoviePy conflict in the shared build
container. Neither MoviePy nor Pillow is part of `api-gateway/requirements.txt`,
so this is not a Jarvis dependency conflict. Jarvis gateway dependencies remain
explicitly pinned in their own requirements file.

## Reproduce

```bash
python -m pytest -q
python -m pytest -q --cov=api-gateway --cov-report=term-missing
node --test tests/voice-engine.test.js
python -m compileall -q api-gateway tests
python tools/generate_repo_reports.py --check
```
