# Jarvis V22.1 — Self-Correcting Project Worker

Latest milestone: bounded/resumable Project Worker self-correction with workspace baselines, targeted verification, failure-evidence repair cycles, and durable worker-run state. See `V22.1-JARVIS-SELF-CORRECTION.md`.

# AI System — V14 Jarvis Experience

This is the local-first AI System control center. V13 adds a Jarvis-style companion experience while preserving the existing Ollama brain, agent/tool router, memory/RAG, approvals, Gmail, Calendar, Home Assistant, iPhone, Sales Machine and Email Agent separation.

### Voice providers
- **Free Local:** browser-local Whisper WASM + browser TTS.
- **Deepgram:** optional realtime Flux STT + Aura TTS; Ollama remains the brain.

### New Jarvis workspace
- Conversational AI companion dashboard.
- Persistent Artifact Workspace for Markdown, tasks, Mermaid, progress and records.
- Optional Exa web research.
- Opt-in Windows computer mode through the authenticated localhost host bridge.

### Run locally on Windows
Use `START-LOCAL-TEST.bat`, then open `http://127.0.0.1:8000/dashboard`.
Voice is at `http://127.0.0.1:8000/voice`.

See `V13-JARVIS-UPDATE.md` for setup and security details.

## Dashboard

Open **http://localhost:8000/dashboard**. If you previously ran an older build, restart with `docker compose up -d --build api-gateway` so the dashboard code is copied into the container.

# Self-Hosted AI System

Complete AI infrastructure running on your local machine. No paywalls, no API costs, full control.

## Architecture

- **Ollama**: Local LLM hosting (Llama, Mistral, Qwen, etc.)
- **ComfyUI**: Stable Diffusion image generation
- **Open WebUI**: ChatGPT-like interface for your local models
- **API Gateway**: OpenAI-compatible REST API + Google OAuth (Gmail + Calendar)
- **Google OAuth**: Scoped Gmail read/send + Calendar access, tokens encrypted at rest

## System Requirements

✅ **Your Hardware:**
- CPU: Intel Core i5-11400F
- GPU: NVIDIA RTX 3070 (8GB VRAM)
- RAM: 16GB
- OS: Windows 11

## Quick Start

### Prerequisites

1. **Install Docker Desktop for Windows**
   - Download from https://www.docker.com/products/docker-desktop
   - Enable WSL2 backend
   - Enable GPU support in Docker settings

2. **Install NVIDIA Container Toolkit**
   ```bash
   # In WSL2 Ubuntu terminal
   distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
   curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
   curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   sudo systemctl restart docker
   ```

### Launch the System

```bash
cd ai-system
docker-compose up -d
```

> **Note:** The API gateway uses a slim CPU-only Python image (it just proxies
> HTTP to Ollama), so only Ollama requests the GPU. Builds take ~2 minutes.

### First Time Setup

1. **Pull your first model (Llama 3.1 8B)**
   ```bash
   docker exec -it ollama ollama pull qwen3.5:9b
   ```

2. **Configure Google OAuth (optional — enables Gmail + Calendar)**
   - Copy `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` into `.env`
   - See [Google OAuth Setup](#google-oauth-gmail--calendar) below

3. **Access the interfaces:**
   - **Open WebUI (Chat Interface)**: http://localhost:3000
   - **ComfyUI (Image Generation)**: http://localhost:8188
   - **API Gateway**: http://localhost:8000
   - **Ollama Direct**: http://localhost:11434

## Available Models

### Recommended LLMs for RTX 3070 (8GB VRAM)

```bash
# Fast and capable (recommended)
docker exec -it ollama ollama pull qwen3.5:9b

# Strong reasoning
docker exec -it ollama ollama pull qwen3.5:9b

# Good all-rounder
docker exec -it ollama ollama pull mistral:7b

# Coding specialist
docker exec -it ollama ollama pull deepseek-coder:6.7b

# Vision capable
docker exec -it ollama ollama pull llava:7b
```

### Larger Models (slower but more capable)

```bash
# 14B parameter models (will be slower)
docker exec -it ollama ollama pull qwen3.5:9b
docker exec -it ollama ollama pull qwen3.5:9b
```

## AI System Dashboard

The primary control-center dashboard is available at:

- **AI System Dashboard:** http://localhost:8000/dashboard

The dashboard is intentionally a thin UI over the existing gateway/tool-router architecture. It includes:
- Jerry-specific greeting, live clock, calendar and schedule widgets
- Local AI assistant / voice launcher
- Gmail + Calendar connection status
- Home Assistant and Windows host-bridge status
- Tasks, focus timer, notes and habit tracker
- Email Agent and Sales Machine workspace cards (kept visually and architecturally separate)
- Recent files and system-health widgets
- Responsive desktop/tablet/mobile layout

Sensitive actions continue to use the existing agent confirmation flow; the dashboard does not bypass tool-router safety gates.

## Using the System

### Web Interface (Easiest)

1. Open http://localhost:3000
2. Create an account (stored locally)
3. Select your model and start chatting

### API (OpenAI Compatible)

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-needed"  # Local system, no auth required
)

response = client.chat.completions.create(
    model="qwen3.5:9b",
    messages=[
        {"role": "user", "content": "Explain quantum computing"}
    ]
)

print(response.choices[0].message.content)
```

### Direct Ollama Usage

```bash
# Interactive chat
docker exec -it ollama ollama run qwen3.5:9b

# API call
curl http://localhost:11434/api/generate -d '{
  "model": "qwen3.5:9b",
  "prompt": "Why is the sky blue?"
}'
```

## Image Generation (ComfyUI)

1. Access ComfyUI at http://localhost:8188
2. First launch will download Stable Diffusion models (~4GB)
3. Use the workflow interface to generate images

## API Endpoints

### Chat Completion
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:9b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Conversation Memory
Create a conversation, then reuse its ID for subsequent chat turns. The
gateway persists the history in SQLite under the `api_data` volume.

```bash
curl -X POST http://localhost:8000/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{"title":"My Chat","model":"qwen3.5:9b"}'
```

Then send the returned `conversation_id` with each chat request:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:9b",
    "conversation_id": 1,
    "messages": [{"role":"user","content":"Remember that my project is called Atlas."}]
  }'
```

### Model Comparison
`/v1/compare` accepts JSON rather than query-string prompts:

```bash
curl -X POST http://localhost:8000/v1/compare \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Explain recursion simply.","models":["qwen3.5:9b"]}'
```

### List Models
```bash
curl http://localhost:8000/v1/models
```

### Health Check
```bash
curl http://localhost:8000/health
```

### System Stats
```bash
curl http://localhost:8000/stats
```

## Google OAuth (Gmail + Calendar)

The API gateway can access your Gmail and Google Calendar through a scoped
OAuth 2.0 flow. Tokens are Fernet-encrypted at rest in the `api_data` volume
and auto-refresh when they expire.

### One-time setup

1. Open [Google Cloud Console](https://console.cloud.google.com) and select
   the project that owns your OAuth client.
2. **APIs & Services → Library** → enable **Gmail API** and **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** → add scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `openid`
4. **APIs & Services → Credentials** → edit your OAuth client → add redirect URI:
   - `http://localhost:8000/auth/google/callback`
5. Put credentials in `.env` (see `.env.example` for the template).

> **Cost:** Gmail and Calendar APIs are free for personal use. No billing
> account or credit card is required.

### Connect your account

```bash
# Get the authorization URL
curl http://localhost:8000/auth/google/start
# → {"url": "https://accounts.google.com/o/oauth2/v2/auth?..."}
```

Open the returned URL in your browser, sign in, and click **Allow**.

### OAuth + Gmail + Calendar endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/auth/google/start` | Begin OAuth — returns the URL to visit |
| GET | `/auth/google/callback` | Google redirects here; exchanges code → tokens |
| GET | `/auth/google/status` | List connected accounts + token validity |
| POST | `/auth/google/disconnect` | Forget a connected account |
| GET | `/gmail/messages` | List recent Gmail messages (read-only) |
| GET | `/calendar/events` | List upcoming calendar events |

Example:

```bash
curl "http://localhost:8000/gmail/messages?email=you@gmail.com&max_results=10"
curl "http://localhost:8000/calendar/events?email=you@gmail.com&max_results=10"
```

See `OAUTH_README.md` for full details on the flow, token storage, security,
and troubleshooting.

## Management Commands

### View logs
```bash
docker-compose logs -f ollama
docker-compose logs -f open-webui
docker-compose logs -f comfyui
```

### Stop the system
```bash
docker-compose stop
```

### Start the system
```bash
docker-compose start
```

### Restart a service
```bash
docker-compose restart ollama
```

### Remove everything (models will be preserved)
```bash
docker-compose down
```

### Remove everything including models
```bash
docker-compose down -v
```

## Performance Optimization

### RTX 3070 (8GB) Tips

1. **Use quantized models** - 8B models run fast, 14B models are slower
2. **One model at a time** - Keep 1-2 models loaded for best performance
3. **Adjust context window** - Reduce if running out of VRAM
4. **Monitor GPU usage** - Use `nvidia-smi` to watch VRAM

### Speed up responses

```bash
# Use smaller context window
docker exec -it ollama ollama run qwen3.5:9b --num-ctx 2048
```

## Troubleshooting

### GPU not detected
```bash
# Check NVIDIA drivers
nvidia-smi

# Restart Docker
docker-compose down
docker-compose up -d
```

### Out of memory errors
- Use smaller models (7B instead of 13B)
- Close other GPU applications
- Reduce context window size

### Model download fails
- Check internet connection
- Try pulling manually: `docker exec -it ollama ollama pull <model>`

### Services won't start
```bash
# Check what's using the ports
netstat -ano | findstr "11434"
netstat -ano | findstr "8188"
netstat -ano | findstr "3000"
```

## File Structure

```
ai-system/
├── docker-compose.yml          # Main orchestration
├── .env                        # Real credentials (gitignored — never commit)
├── .env.example                # Template with blank values (safe to commit)
├── .gitignore                  # Protects .env, tokens, data/
├── api-gateway/
│   ├── main.py                 # FastAPI gateway + OAuth routes
│   ├── google_oauth.py         # OAuth flow + token storage + Gmail/Calendar helpers
│   ├── requirements.txt
│   └── Dockerfile              # Slim CPU-only image (no GPU needed)
├── OAUTH_README.md             # Full OAuth integration docs
└── README.md
```

## Data Persistence

All data is stored in Docker volumes:
- `ollama_data` - Downloaded models (~10-50GB)
- `open-webui_data` - Chat history and settings
- `comfyui_data` - Workflows and generated images
- `api_data` - Google OAuth tokens (Fernet-encrypted) + encryption key

## Resource Usage

**Typical VRAM usage:**
- Llama 3.1 8B: ~5-6GB
- Qwen 2.5 7B: ~5GB
- Mistral 7B: ~5GB
- Llama 3.1 13B: ~8GB (will max out)

**Storage:**
- Base system: ~5GB
- Per 8B model: ~5GB
- Per 13B model: ~8GB
- Stable Diffusion: ~4GB

## Cost Comparison

**This System: $0/month**

**vs Cloud Services:**
- ChatGPT Plus: $20/month
- Claude Pro: $20/month
- Midjourney: $10-60/month
- **Total savings: $50+/month**

## Next Steps

1. Try different models to find your favorite
2. Install custom ComfyUI workflows
3. Build applications using the API
4. Add authentication if exposing to network

## Support

Check component health:
```bash
curl http://localhost:8000/health
curl http://localhost:8000/stats
```

## Security Notes

⚠️ **This setup has no authentication.** 
- Only expose on localhost (127.0.0.1)
- Do not expose ports to public internet
- Add auth layer if needed for network access

**Google OAuth tokens:**
- Encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)
- Encryption key stored at `api_data/.token_key` (mode 0600) or via `TOKEN_ENCRYPTION_KEY`
- The OAuth flow is CSRF-protected via the `state` parameter (single-use, 10-min TTL)
- Never commit `.env` — it contains the OAuth client secret

---

Built for: Intel i5-11400F + RTX 3070 + 16GB RAM


### Voice Chat

Open `http://localhost:8000/voice` in Chrome or Edge to talk to the local AI using your computer/headset microphone and hear responses through your selected audio output. The voice console uses local Whisper speech-to-text in the browser and browser speech synthesis for output; it does not require a paid voice API key. The first Whisper run downloads and caches the model, then transcription runs locally.

The voice console has two independent profiles:
- **General AI** — the core local assistant with memory and optional RAG.
- **Sales Machine** — a separate voice-first sales operator for qualification, follow-up, objection handling, appointment setting, scripts, and pipeline decisions. It is intentionally independent of the Email Agent SaaS.

The Email Agent and Sales Machine are separate product boundaries. Voice is an interface that can operate the Sales Machine; it does not make the Email Agent a dependency. The gateway exposes `/v1/sales/chat` for applications that want to use the Sales Machine directly.

**Requirements:** allow microphone access in the browser and select your headset as the Windows input/output device.

## AI System integrations

The gateway now includes a local-first tool/action layer at `/v1/tools` and an agent endpoint at `/v1/agent/chat`.

### Windows files
Docker cannot see arbitrary Windows folders unless they are mounted. Set `AI_HOST_FILES` in `.env` to the Windows folder you want the assistant to access. The compose file mounts it as `/host-files` and the default allowed path is `/host-files`.

Example:
`AI_HOST_FILES=C:\\Users\\YOUR_USER`

For safer operation, point it to a narrower folder such as Documents.

### Home Assistant / TVs
Set `HOME_ASSISTANT_URL` and `HOME_ASSISTANT_TOKEN`. The AI can read states and control supported Home Assistant entities. TVs that appear as `media_player.*` entities can be controlled through the same device layer (power, play/pause/stop, volume where supported).

### iPhone companion
`/companion` is a lightweight mobile web companion that talks to the same gateway. For LAN access, use `docker-compose -f docker-compose.yml -f docker-compose.lan.yml up -d --build` and access `http://<PC-LAN-IP>:8000/companion` from the iPhone. Only enable the LAN override on a trusted home network and configure Windows Firewall accordingly.

### Confirmation model
The agent can automatically use read-only tools. Sending mail, creating/updating/deleting calendar events, creating files, and controlling home devices return `confirmation_required` instead of executing. The client then POSTs the returned tool + arguments to `/v1/agent/confirm` with `confirmed: true`.


## Multi-step agent behavior
The agent preserves its in-flight tool-call state when a sensitive action requires confirmation. Approving the confirmation resumes the same task with the exact approved arguments and prior tool results. A rejected confirmation is discarded. The router limits tool rounds to eight per request.


## Regression fixes in this build
- Home Assistant tools no longer read a Gmail account before their branches.
- iOS companion sends real `ChatMessage` objects, creates/persists a conversation ID, and supports the API token.
- Windows host bridge is jailed to `/host-files` and defaults to `127.0.0.1`; set `AI_HOST_BRIDGE_BIND=0.0.0.0` only for an explicitly secured LAN deployment.
- Voice page defines `apiHeaders` and captures the entire recording continuously with a zero-gain output node (no 50ms snapshot and no mic feedback).
- Confirmation endpoint is rate limited.
- `verify-local-ai.py` uses Windows-safe temporary paths and UTF-8.

### Fast local test
Run `TEST-DASHBOARD.bat`. It rebuilds the gateway against native Ollama, starts it, and opens `/dashboard`.

## Windows Core Application (v9)

AI System can run as a Windows desktop/tray application instead of requiring a terminal every time.

### Install once

1. Make sure Docker Desktop and native Ollama are installed/running on Windows.
2. Run `install-core-app.bat`.
3. The installer builds `dist\\AI-System.exe`, creates an `AI System` desktop shortcut, and adds the launcher to the current user's Windows Startup folder.
4. Launch **AI System** from the desktop or Start Menu.

The tray launcher checks the local API and Ollama health, starts Docker Desktop when necessary, starts the API gateway with the local compose override, and opens the dashboard when the gateway is ready.

### Daily use

After the one-time install, use the **AI System** desktop shortcut. The launcher can start automatically with Windows. Right-click the tray icon for Start, Stop, Restart, Dashboard, Settings, and Exit.

The v9 launcher intentionally starts only `api-gateway` because this Windows build uses native Ollama on `127.0.0.1:11434`. The existing `docker-compose.override.yml` routes the gateway to native Ollama through `host.docker.internal`.

## Local Tools / Creative Plugins

The Windows build includes a read-only local capability scanner at
`/v1/tools/local`. It detects Blender, Unreal Engine 5, ComfyUI, FFmpeg, Ollama,
and Python and exposes their approved capabilities to the agent. The scanner is
**not** an arbitrary command runner; future creative plugins will add narrowly
scoped, auditable actions for each application.

## Optional Google Gemini Cloud

Qwen 3.5 9B remains the local default. V14 also supports optional Gemini cloud chat using a Gemini API key associated with your Google Cloud project. Run `SET-GEMINI-CLOUD.bat`, then restart the local gateway. The dashboard includes a Local Qwen / Gemini Cloud selector. The Gemini key is kept server-side in `.env` and is never sent to the browser.


## One-click Jarvis

After extraction, double-click `OPEN-JARVIS.bat`. It checks whether the API is already running, starts the local API when needed, waits for readiness, and opens the main dashboard. Run `CREATE-JARVIS-DESKTOP-SHORTCUT.bat` once to create a desktop shortcut named `Jarvis AI`.


## V19 — Windows Computer Control

The Windows host bridge can now provide opt-in mouse, keyboard, screen, process, UI Automation, and PowerShell control. See `V19-WINDOWS-COMPUTER-CONTROL.md`. Keep `AI_COMPUTER_CONTROL=false` until you are ready to test it.

## V22.2 — Autopilot-integrated Project Worker
Jarvis Autopilot now delegates repository execute tasks to the bounded self-correcting Project Worker. Project health evidence, environment/code failure classification, and targeted JavaScript/TypeScript verification reduce false repair loops and improve completion evidence. See `V22.2-JARVIS-AUTOPILOT-INTEGRATION.md`.

## V23 — Jarvis Command Center + Stability Hardening

V23 makes `/dashboard` the live Jarvis Command Center and hardens the execution/control plane beneath it. The six integrated views cover Command Center, Project Worker, AI Router, Autopilot, Approvals, and Memory & Logs. Dashboard data comes from the actual orchestrator, Project Worker, router, approval, memory, security, and telemetry state rather than a decorative mock layer.

The build also adds provider circuit breakers, concurrent/cached provider health, exact registered-workspace enforcement for OpenCode, fail-closed tool policy coverage, a redacted command-center overview endpoint, and a fast `/health` plus deeper `/ready` split.

Gemini Live browser voice is now server-authenticated with short-lived ephemeral credentials; the permanent Gemini API key is kept server-side. Voice project-development requests route through the bounded self-correcting Project Worker rather than directly granting OpenCode write authority.

The animated dashboard is Blender-ready. `tools/blender_command_center_bake.py` builds the richer 3D command-center core and `BAKE-JARVIS-DASHBOARD.bat` runs it on Windows. Browser-ready fallback motion assets are already included, so the dashboard is animated even before a local Blender bake.

See `V23-JARVIS-COMMAND-CENTER-STABILITY.md` for architecture, validation, security, Blender, and upgrade details.

On Windows, run `TEST-JARVIS-V23.bat` for the V23 release/stability gate. It runs the Python suite, compile checks, voice-engine tests when Node is present, media probes when FFprobe is present, and optional Docker/Blender availability checks.
