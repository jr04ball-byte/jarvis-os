# Jarvis OS — Test Report (generated Cycle 7)

## Headline (this cycle, local run)
- **77 passed, 0 failed** (`python -m pytest -q`, ~4–6 s, Python 3.12.10 win32)
- CI (`.github/workflows/main.yml`): ruff gates
  (F401,I001,UP006,UP045,UP035,FURB167,RUF010,RUF022 + S110 for api-gateway/tests)
  then `pytest -q` with `API_DATA_DIR=./data`. CI green on `main`.
- Coverage (informational, `pytest-cov`, not gated): **52% total** over `api-gateway`.

## Coverage by module
| Module | Cover | Note |
|---|---|---|
| orchestrator.py | 95% | state machine well pinned |
| providers/base.py | 95% | contract surface pinned |
| security.py | 95% | |
| intelligence_router.py | 89% | routing/telemetry covered |
| project_worker.py | 87% | discovery/verify covered |
| local_tools.py | 81% | |
| workspace_registry.py | 90% | |
| compute_manager.py | 57% | GPU paths need hardware |
| tools.py | 49% | HA/bridge need live services |
| providers/ollama,openai,opencode | 47–51% | live HTTP paths uncovered (by design: offline CI) |
| providers/gemini.py | 33% | same |
| brains.py | 37% | failover loop partially covered |
| main.py | 38% | 97 routes; smoke-covered only |
| google_oauth.py | 28% | needs OAuth fixtures |
| model_lab.py | 19% | needs runtimes (Ollama/LM Studio) |

## Inventory (14 files, 77 tests)
- `test_orchestrator.py` (4), `test_v201_autopilot.py` (4) — plan/state machine
- `test_v21_multibrain.py` (7) — routing with fake providers
- `test_routing_v181.py` (7) — fast-path guards via real `main`
- `test_voice_turn.py` (9), `test_v18_voice.py` (2) — voice path selection
- `test_provider_contracts.py` (10, Cycle 3) — offline interface contracts
- `test_sqlite_lifecycle.py` (3, Cycle 1) — Windows file-lock regression
- `test_tool_router.py` (5), `test_agent_state.py` (0 test fns — helpers only)
- `test_v22_project_worker.py` (6), `test_v222_project_worker.py` (4),
  `test_v221_self_correction.py` (4) — worker/discovery/verify
- `test_v23_command_center.py` (12) — FastAPI smoke via TestClient + httpx mocks
- `voice-engine.test.js` (1, node — not run by pytest/CI)

## Gaps (proposed next coverage work, no gate yet)
1. `main.py` route smoke: extend TestClient coverage beyond command-center.
2. `brains.py` failover loop with mocked providers (offline).
3. `google_oauth.py` with fixture keys (Fernet round-trip, no network).
4. `voice-engine.test.js` is not wired into CI (needs node job or removal decision).
5. `test_agent_state.py` collects 0 tests — rename to helper or add tests.

## Reproduce
`pip install -r api-gateway/requirements.txt pytest pytest-cov`
`python -m pytest -q` · `python -m pytest -q --cov=api-gateway --cov-report=term-missing`
