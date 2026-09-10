# AI System V13 — Jarvis Experience

V13 upgrades the existing local-first AI System instead of replacing its core.

## New
- Jarvis-style AI companion dashboard with conversation + Artifact Workspace.
- Persistent artifacts stored locally under `data/artifacts` (markdown, tasks, Mermaid, image records, progress, research records).
- Provider-switchable voice: **FREE LOCAL** (Whisper WASM + browser TTS) or **OPTIONAL DEEPGRAM** (Flux STT + Aura TTS).
- Ollama remains the reasoning/agent brain in both voice modes.
- Optional Exa web research endpoint; absent by default, so the system stays local-first.
- Opt-in Windows computer-control bridge: open, type, keyboard shortcuts, screenshots.
- `computer_open` requires an AI confirmation ticket. Typing/keys are allowed only after the host bridge is explicitly enabled.
- Existing Gmail, Calendar, Home Assistant, iPhone, Sales Machine and Email Agent integrations remain separate.

## Free/local mode
No Deepgram or Exa key is required. Local Whisper downloads/caches its browser model on first use; subsequent speech recognition can run locally in the browser. Ollama remains local.

## Deepgram mode
Set `DEEPGRAM_API_KEY` in `.env`. The permanent key stays server-side. The browser receives a short-lived Deepgram token. Deepgram handles realtime STT and Aura TTS while Ollama handles reasoning and tools.

## Windows computer mode
1. Start the host bridge from `host-bridge`.
2. Install its requirements.
3. Set `AI_HOST_BRIDGE_TOKEN` and `AI_HOST_FILES`.
4. Keep `AI_COMPUTER_CONTROL=false` until you explicitly want computer control.
5. Set `AI_COMPUTER_CONTROL=true` only on your own Windows machine.

The bridge binds to `127.0.0.1` by default and never exposes arbitrary filesystem paths through the `/host-files` file-open route.

## Verification
- `python -m py_compile api-gateway/main.py api-gateway/tools.py host-bridge/host_bridge.py`
- `python verify-local-ai.py`
- `python -m pytest -q`

The build was syntax-checked and the deterministic test suite passes. A live FastAPI smoke test could not be run in the build environment because external package installation is network-disabled; run `START-LOCAL-TEST.bat` on Windows for the real local smoke test.
