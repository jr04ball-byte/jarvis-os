# Jarvis V23 — Command Center + Stability Hardening

V23 turns the V22.2 self-correcting Project Worker into a more production-oriented control plane and adds a live, animated Jarvis Command Center. The goal of this release is not decorative UI alone: the dashboard is wired to the same orchestrator, project-worker, provider-router, approval, memory, telemetry, and verification state that actually controls Jarvis.

## What changed

### One command center, six operating views

`/dashboard` is now the primary Jarvis Command Center and includes six integrated views:

1. **Command Center** — live neural-core visualization, provider fabric, project fleet, health, active work, verification, and request activity.
2. **Project Worker** — inspect or verify registered projects and review durable worker runs.
3. **AI Router** — inspect provider health, routing policy, circuit-breaker state, and simulate routing modes.
4. **Autopilot** — submit bounded goals, select a target, set a step budget, and review durable project history.
5. **Approvals** — review redacted sensitive actions and explicitly approve or cancel them.
6. **Memory & Logs** — inspect memory counts, conversation history, and safe request telemetry without exposing credentials.

The classic cockpit remains available at `/dashboard-classic`, and the earlier God’s Eye interface remains available at `/godseye`.

### Animated Blender command-center pipeline

V23 includes `tools/blender_command_center_bake.py`, a Blender background-build script for the dashboard centerpiece. It builds a nested emissive AI core, three mechanical rotating rings, provider nodes, energy spines, lighting, camera movement, and glow/compositing, then exports browser-ready motion assets.

Run `BAKE-JARVIS-DASHBOARD.bat` on a Windows machine with Blender installed. The batch file discovers common Blender installations and invokes the bake script. Generated assets are written into `api-gateway/assets/` and the dashboard automatically uses them.

The release already contains validated animated WebM/MP4/poster fallback assets so the Command Center moves immediately. The richer Blender scene itself was not rendered in the build environment because a Blender binary / `bpy` runtime is not installed there; the bake script is syntax-checked and is included for the Windows build.

### Multi-brain reliability

The V21 provider abstraction is hardened with a provider circuit breaker. Repeated provider failures temporarily remove the unhealthy provider from the eligible route chain, while Jarvis can continue through another allowed provider. Provider-health checks run concurrently, are bounded by timeouts, and are cached briefly to avoid turning dashboard refreshes into excessive provider traffic.

Key environment controls:

```env
JARVIS_CIRCUIT_FAILURES=3
JARVIS_CIRCUIT_COOLDOWN_SECONDS=30
```

### OpenCode authority hardening

OpenCode can now work only against an exact Jarvis-registered project root. Arbitrary filesystem paths are rejected before any OpenCode network/server call. Its provider permission is fail-closed: only `read_only` or `workspace_write` are accepted.

The legacy `/v1/opencode/task` route is intentionally limited to read-only inspection. Write/refactor/fix work must go through the Project Worker or Autopilot so the baseline, bounded retry, diff, test, verification, and audit controls remain intact.

### Centralized tool security

Every declared agent tool is now covered by the centralized security policy. Tool classes are explicitly separated into read-only, low-impact, internal-write, and sensitive operations. Unknown tools are denied by default. Sensitive/external actions still require the existing exact confirmation workflow.

This is an important architecture rule in V23: **models recommend; Jarvis authorizes and executes**.

### Secure Gemini Live browser authentication

The browser voice console no longer receives the permanent `GEMINI_API_KEY`. `/v1/gemini/live-token` now mints a short-lived, one-use Gemini Live credential on the server and returns only that ephemeral credential to the browser. The browser then connects to the constrained Gemini Live WebSocket endpoint.

The Live console also waits for `setupComplete` before starting microphone streaming, uses the current realtime `audio`/`text` fields, enables input/output transcription, and routes coding/project requests to the bounded Project Worker instead of the old direct OpenCode write path.

### Observability without secret leakage

`/v1/command-center/overview` exposes the state needed by the dashboard while deliberately redacting secrets and tool arguments. It includes:

- provider availability and circuit state
- compute status
- registered project health
- recent orchestrator projects
- recent Project Worker runs
- redacted pending approvals
- request latency/status telemetry
- memory counts
- centralized security policy
- quality/success metrics

`/health` remains a fast local liveness endpoint with no provider network calls. `/ready` performs the deeper readiness check.

## V23 validation strategy

The release test pass covers:

- Python unit/integration tests
- Node voice-engine tests
- Python bytecode compilation
- dashboard and Live Voice JavaScript syntax
- HTML parsing and duplicate-ID checks
- FastAPI route smoke tests
- centralized security fail-closed checks
- OpenCode workspace/permission boundaries
- provider circuit-breaker behavior
- Gemini Live ephemeral-token response shape
- command-center secret redaction
- baked motion asset integrity
- ZIP integrity after packaging

External runtime checks such as Docker Compose and the full Blender render are only executed when those binaries are present in the build environment. Their absence is reported rather than treated as a fake pass.

`TEST-JARVIS-V23.bat` provides the same core stability gate on the Windows host and adds Docker/Blender/media checks when those tools are installed.

## Windows upgrade notes

1. Preserve your existing `.env`; V23 does not require replacing it just to open the dashboard.
2. Add the new optional V23 values from `.env.example` when you want to override their defaults.
3. Extract V23 over a clean application directory or merge it into the existing Jarvis install while keeping the local `.env` and runtime databases.
4. Start Jarvis and open `/dashboard`.
5. If Blender is installed, run `BAKE-JARVIS-DASHBOARD.bat` once to generate the richer animated command-center scene.
6. Run your normal Windows/Docker smoke checks before treating the machine as production-ready.

## Security note

The downloadable release archive intentionally does **not** need to contain your live `.env`. Keep credentials on the target machine. If credentials have previously been shared in an archive or chat, rotate them after development/testing is complete.
