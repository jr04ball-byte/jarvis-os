# AI System V14 — Jarvis Desktop Experience

V14 builds on V13 and uses the supplied RileyJarvis project as the UX reference and implementation source.

## Added
- Optional Windows Electron desktop companion with Riley-inspired companion face and mood states.
- Local AI System HTTP API remains the brain: Ollama + FastAPI + existing tool router.
- Persistent artifact workspace surfaced directly in the desktop companion.
- Artifact list and selection for markdown, task, research, code, image and progress outputs.
- Computer-mode status surface with explicit opt-in messaging.
- Voice and Dashboard launchers into the existing AI System web surfaces.
- Quick actions for plans, architecture diagrams and connected devices.
- No OpenAI API dependency added to the core.
- No permanent cloud voice credential exposed to the desktop renderer.

## RileyJarvis concepts intentionally adapted
- Companion-first UI.
- Animated listening/thinking/speaking/working/error states.
- Dedicated artifact workspace.
- Compact, distraction-free control surface.
- Clear separation between display and computer capabilities.

## Windows/local-first design
The Electron shell is a UI client. It does not replace the FastAPI gateway, Ollama, memory/RAG, Home Assistant, Gmail/Calendar, Sales Machine, Email Agent, or host bridge.

## Install
From the extracted AI System root:

1. Run `START-LOCAL-TEST.bat`.
2. Confirm `http://127.0.0.1:8000/dashboard` works.
3. In a second terminal:
   `cd C:\Users\jr04b\ai-system-v14\desktop-companion`
4. Run `npm install` once.
5. Run `npx electron .` or `START-DESKTOP-COMPANION.bat`.

The desktop shell defaults to `http://127.0.0.1:8000`. Set `AI_SYSTEM_URL` if the gateway runs elsewhere.

## Verification performed in build environment
- Python compilation: main.py, tools.py, host_bridge.py.
- Existing V13 verification suite.
- Existing pytest suite.
- Electron renderer JavaScript syntax check.
- Desktop companion package structure and launcher checked.

A live Electron/Windows GUI launch cannot be executed in this Linux build environment; Windows-local testing is still required.


## Qwen 3.5 9B default
The current release defaults to Ollama `llama3.1:8b`. Run `SET-LLAMA31-8B.bat` once on Windows to pull it. The model remains local through Ollama.

