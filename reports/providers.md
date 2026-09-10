# Jarvis OS — Providers (generated Cycle 7, source-verified)

Contract: `providers/base.py` — `ProviderAdapter` ABC. Providers reason only;
Jarvis owns routing, permissions, memory, verification (Cycle 3 contract tests
in `tests/test_provider_contracts.py` pin this offline: 10 tests).

## Base surface
`__init__(model)`, class attrs `name/kind/supports_stream/supports_tools/supports_local`,
`configured: bool` (default True), abstract `complete(messages, *, temperature=0.7,
max_tokens=1024, task_context=None) -> ProviderResult`, concrete `stream()`
fallback (yields `complete()` content), abstract `health() -> dict`, concrete
`describe()` (name/kind/model/configured/supports_*).

## Matrix (all verified by reading each provider)

| Provider | File | kind | Default model (env) | Key gate (`configured`) | stream | tools | local |
|---|---|---|---|---|---|---|---|
| gemini (primary) | `gemini.py` | cloud | `GEMINI_MODEL` → gemini-3.5-flash | `GEMINI_API_KEY` | ✅ SSE | ✅ | ❌ |
| openai (deep reasoning) | `openai_provider.py` | cloud | `OPENAI_MODEL` → gpt-5.6 | `OPENAI_API_KEY` | ❌ (base fallback) | ✅ | ❌ |
| ollama (local/private) | `ollama.py` | local | `JARVIS_DEEP_MODEL` → qwen3.5:9b (+`JARVIS_FAST_MODEL` → gemma3:4b) | always True | ✅ NDJSON | ✅ | ✅ |
| opencode (coding worker) | `opencode.py` | worker | `OPENCODE_PROVIDER/OPENCODE_MODEL` → opencode-go/deepseek-v4-flash | always True | ❌ (base fallback) | ✅ | ✅ |

Construction is uniform since Cycle 3: `Cls()` (env-driven) or `Cls(model=...)`
override on all four; all 5 call sites (`brains.py`) use no-arg form.

## Provider specifics worth knowing
- **gemini**: system messages folded into first user turn; temp clamped 0–2,
  tokens capped 32768; `streamGenerateContent?alt=sse`; unconfigured `complete()`
  raises before network.
- **openai**: Responses API (`/responses`), reasoning effort
  (`OPENAI_REASONING_EFFORT`, default medium); temperature sent only when
  `OPENAI_SEND_TEMPERATURE` is truthy (reasoning families reject it); empty
  output raises.
- **ollama**: `select_model()` picks fast model on `speed_priority>=7`/`mode==fast`;
  `think: False`; usage from `prompt_eval_count`/`eval_count`; health never
  raises (3 s timeout).
- **opencode**: hard workspace boundary — requires registered workspace +
  `read_only|workspace_write` permission or raises before network; sends
  `x-opencode-directory` header, optional Basic auth + agent; session→message
  two-step; `health()` true when status < 500.
- `health()` never raises on any provider (offline-safe); shapes vary slightly
  (`status_code` / `reason` / `error` keys) — contract only pins `online: bool`.

## Routing (consumer)
`IntelligenceRouter` (`intelligence_router.py`, 89% covered): assessment
(complexity/coding/privacy/speed), named modes incl. explicit requests,
per-provider fallback chains, `RouterTelemetry` counts, `ProviderCircuitBreaker`
with cooldown. `brains.py` executes the chain with `configured` skip +
failover + attempt log. Router is provider-agnostic (multibrain tests use fakes).
