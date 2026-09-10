# Jarvis V21 — Multi-Brain Intelligence Router

V21 turns Jarvis's existing brain switcher into a provider-neutral intelligence control plane. The goal is one Jarvis system with specialized engines, not multiple assistants bolted together.

## Roles

- **Gemini** — default main brain for everyday reasoning, conversation and orchestration.
- **OpenAI** — deep-reasoning escalation provider for difficult architecture, debugging, security and high-complexity analysis.
- **Ollama** — private/local/low-cost brain. Fast mode uses `JARVIS_FAST_MODEL`; deeper local work uses `JARVIS_DEEP_MODEL`.
- **OpenCode** — bounded software-engineering worker for repository/code tasks.
- **Jarvis** — owns routing, memory, project state, permissions, execution authority and verification.

## New architecture

`api-gateway/providers/` now defines a common provider contract and adapters for Gemini, OpenAI, Ollama and OpenCode. `api-gateway/intelligence_router.py` performs deterministic task assessment and provider selection. `api-gateway/brains.py` is now a compatibility API over the provider registry and router.

## Routing modes

- `auto` — Gemini default, with escalation based on task type.
- `fast` — Ollama local/fast model first.
- `normal` — Gemini first.
- `deep` — OpenAI first, then Gemini, then Ollama.
- `private` — Ollama only; no cloud fallback.
- `coding` — OpenCode first, then OpenAI, Gemini and Ollama.
- `autopilot` — higher-complexity/risk assessment while preserving Jarvis authority boundaries.

## New endpoints

- `GET /v1/brain/status` — provider configuration, health and telemetry.
- `GET /v1/brain/policy` — routing policy, provider capabilities and recent telemetry.
- `POST /v1/brain/route` — explain the route Jarvis would choose without running a model.
- `POST /v1/brain/chat` — execute through the V21 router with bounded failover.
- `POST /v1/brain/stream` — streaming route; if a provider fails before emitting tokens Jarvis may fail over. Once output has started, Jarvis fails closed rather than splicing two models into one answer.

## New environment settings

```env
JARVIS_PRIMARY_BRAIN=gemini
JARVIS_DEEP_BRAIN=openai
JARVIS_LOCAL_BRAIN=ollama
JARVIS_CODING_BRAIN=opencode

OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_REASONING_EFFORT=medium
OPENAI_SEND_TEMPERATURE=false
```

Existing V20 `JARVIS_SECONDARY_BRAIN` and `JARVIS_FALLBACK_BRAIN` values can remain in existing `.env` files, but V21 no longer requires them.

## Security properties

- Providers do not receive unrestricted authority simply because they recommend a tool action.
- Private mode is local-only and does not silently fall back to cloud providers.
- OpenCode receives an explicit workspace and permission scope from Jarvis.
- OpenCode's worker prompt prohibits secret/.env changes unless the task explicitly grants that authority.
- Existing V20 confirmation-ticket and orchestrator security boundaries remain in place.
- API keys remain server-side in these adapters. Existing Gemini Live browser-token architecture is still a separate hardening item for a later security pass.

## Validation

- Python compile: passed.
- Python tests: **38 passed**.
- V21 routing regression coverage includes Gemini default, OpenAI deep escalation, OpenCode coding routing, Ollama private/fast routing, explicit provider routing, and Jarvis authority declaration.

## Next build

V22 should connect this router to the repository-aware Project Worker: read-only repo evidence collection, scoped OpenCode implementation, targeted test discovery/execution, diff collection, independent verification and bounded repair loops.
