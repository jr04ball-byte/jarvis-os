# Jarvis OS — Dependency Graph (V24 AST audit)

## Local module edges
The release-candidate AST scan found the following direct local dependencies:

```text
brains -> events, intelligence_router
deps -> orchestrator, project_worker, store
main -> brains, deps, events, store, telemetry_collector
orchestrator -> events, workspace_registry
project_worker -> events
providers.opencode -> workspace_registry
routes.auth -> google_oauth
routes.chat -> compute_manager, deps, schemas, services
routes.health -> brains, deps, services
routes.projects -> deps, project_worker, schemas, security, services, workspace_registry
routes.providers -> compute_manager, deps, model_lab, schemas, services
routes.telemetry -> brains, compute_manager, deps, events, security, services,
                    telemetry_collector, tools, workspace_registry
routes.voice -> deps, schemas, services
routes.workers -> brains, deps, local_tools, project_worker, schemas, services,
                  tools, workspace_registry
services -> brains, compute_manager, deps, google_oauth, local_tools,
            project_worker, schemas, security, tools, workspace_registry
telemetry_collector -> events
```

**Detected local import cycles:** none.

## Direction
The dominant dependency direction is:

```text
main (composition)
   ↓
routes (HTTP adapters)
   ↓
services / brains / workers
   ↓
routing + provider contracts + tools + persistence
   ↓
infrastructure/external services
```

The event bus inverts cross-cutting observability dependencies: producers emit
lifecycle events; telemetry/dashboard listeners consume them without workers
importing presentation modules.

## Important boundaries

- `intelligence_router.py` depends on provider contract concepts, not concrete
  provider implementations.
- `brains.py` owns failover execution; providers do not route themselves.
- `providers/opencode.py` depends on `workspace_registry` because the workspace
  boundary is a security requirement, not presentation coupling.
- `main.py` imports the composition objects and routers but domain modules do
  not import `main`.
- `telemetry_collector.py` depends only on `events`.

## External dependencies
`api-gateway/requirements.txt` intentionally stays small:

- FastAPI / Uvicorn
- httpx
- Pydantic
- python-multipart
- slowapi
- NumPy / scikit-learn (TF-IDF RAG)
- cryptography (OAuth token encryption)
- python-dotenv

Provider HTTP calls use `httpx` directly; no provider SDK is required.

## Risk notes

1. `services.py` is the current fan-in hotspot and next extraction candidate.
2. Live-provider branches depend on network credentials and are mocked in
   offline CI rather than treated as deterministic tests.
3. The dashboard event stream is intentionally in-process; multi-process API
   deployment would require a shared pub/sub transport (Redis/NATS/etc.) if
   clients must receive events across workers.
