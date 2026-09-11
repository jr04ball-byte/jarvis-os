# Jarvis OS — Provider Layer (V24 release candidate)

Jarvis uses four specialized engines behind a common provider adapter contract.
Providers reason or implement; Jarvis owns authority, routing, memory,
verification, and safety policy.

## Contract
`api-gateway/providers/base.py` defines `ProviderAdapter` and `ProviderResult`.
The contract supports:

- provider identity/capabilities
- `complete(...)`
- streaming fallback
- `health()`
- `describe()`

Offline provider-contract tests ensure each implementation preserves the common
surface without requiring real API keys.

## Matrix

| Provider | Role | Default source | Cloud/local | Notes |
|---|---|---|---|---|
| Gemini | primary/general | `GEMINI_MODEL` | cloud | streaming + tools; Live uses ephemeral credential endpoint |
| OpenAI | deep specialist | `OPENAI_MODEL` | cloud | Responses API; reasoning-oriented escalation |
| Ollama | private/local | Jarvis fast/deep model env | local | offline-capable, NDJSON streaming, local health |
| OpenCode | coding worker | OpenCode provider/model env | local/worker | registered-workspace enforcement and bounded permissions |

## Routing
`IntelligenceRouter` scores mode/complexity/coding/privacy/speed needs and
produces a provider chain. `brains.py` executes that chain, skips unconfigured
providers, records attempts, emits provider lifecycle events, and applies the
circuit-breaker/fallback policy.

## Security properties

- Provider API keys remain server-side.
- Gemini browser voice receives an ephemeral, one-use token instead of the
  long-lived key.
- OpenCode cannot operate on arbitrary unregistered workspaces.
- Providers do not directly bypass Jarvis confirmation or tool policy.

## Test status
Core provider contracts and routing/failover paths are covered offline. The
live HTTP success/error branches remain intentionally dependent on configured
external services and therefore are not a required offline CI precondition.
