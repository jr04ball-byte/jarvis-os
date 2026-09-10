# Jarvis OS — Dependency Graph (generated Cycle 7, AST-measured)

## Local module edges (`A -> [imports]`)
- `api-gateway/main.py` -> `brains`, `compute_manager`, `google_oauth`,
  `local_tools`, `model_lab`, `orchestrator`, `project_worker`, `security`,
  `tools`, `workspace_registry` (also `providers` for `ProviderMessage`)
- `api-gateway/brains.py` -> `providers` (4 provider classes), `intelligence_router`
- `api-gateway/intelligence_router.py` -> `providers` (`ProviderAdapter` typing only)
- `api-gateway/orchestrator.py` -> `workspace_registry` (`context_for`)
- `api-gateway/providers/opencode.py` -> `base`, `workspace_registry`
  (`is_registered_workspace` — workspace boundary check)
- `api-gateway/providers/{gemini,ollama,openai_provider}.py` -> `base`
- `api-gateway/providers/__init__.py` re-exports all four + base types
- Nothing in `api-gateway/` imports `main` (leaf). Only tests import `main`:
  `test_routing_v181`, `test_voice_turn`, `test_sqlite_lifecycle`.

## Reading
- Fan-in hub: `main.py` (10 local modules). Fan-out leaves: `providers/*`,
  `security`, `workspace_registry`, `compute_manager`, `model_lab`.
- `brains.py` is the only non-`main` orchestrator of providers (router + failover).
- `intelligence_router.py` depends on the adapter *type*, not implementations —
  router is provider-agnostic (verified by multibrain tests using fakes).
- `opencode.py` → `workspace_registry` is load-bearing security wiring
  (unregistered workspace ⇒ `RuntimeError`, pinned by contract tests).

## External dependencies
`api-gateway/requirements.txt`: fastapi==0.115.0, uvicorn[standard]==0.30.6,
httpx==0.27.2, pydantic==2.9.2, python-multipart==0.0.12, slowapi==0.1.9,
numpy<2, scikit-learn==1.5.2, cryptography==43.0.1, python-dotenv==1.0.1.
`requirements-launcher.txt`: PyQt6==6.7.0 (desktop launcher only).
Most-imported third-party: `httpx` (8 modules — all network I/O),
`fastapi` (5), `slowapi` (rate limiting), `pydantic` (schemas),
`scikit-learn` (TF-IDF RAG), `cryptography` (Fernet token store).
Dependency footprint is deliberately light (no OpenAI SDK — raw HTTP).

## Risk notes
- `main.py` fan-in (10 modules + all routes) is the V24 split motivation.
- No cycles detected among local modules.
- `httpx` is the single network choke point — mockable in one place
  (pattern already used in `test_v23_command_center.py`).
