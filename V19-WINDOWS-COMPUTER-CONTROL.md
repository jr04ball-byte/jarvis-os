# Jarvis V19 — Windows Computer Control

V19 turns the existing Windows host bridge into an opt-in computer-control layer while preserving the 8GB RTX 3070 one-model policy.

## What was added
- Mouse move, click, scroll and keyboard control.
- Screenshot capture for live observation.
- Windows UI Automation window discovery through `pywinauto` when available.
- Process inventory through `psutil`.
- PowerShell host execution for administrator/system tasks (confirmation required by the Jarvis agent).
- PC capability discovery: NVIDIA GPU/VRAM/temperature, NVIDIA Broadcast, Power Automate Desktop, UI automation libraries, and Turtle Beach/Xbox audio devices.
- `computer_capabilities` is exposed to the agent so Jarvis can choose the best local control path.
- Existing Gemma 3 4B remains available as the lightweight vision observer; no new large vision model is installed automatically.
- Existing Qwen 3.5 9B remains the tool-capable planner.

## Hardware fit
Designed for Windows 11 + i5-11400F + RTX 3070 8GB + 16GB RAM. The build does not keep a second large vision model resident. Screen capture and computer control run on the CPU/Windows host; Gemma 3 4B can be used for visual observation when needed.

## Microsoft/NVIDIA integration opportunities
Windows Graphics Capture can provide secure display/window frames. Microsoft Power Automate Desktop can interact with Windows UI elements using UI Automation and can automate elevated apps when UI Access is configured. NVIDIA RTX 3070 supports NVIDIA Broadcast and dedicated NVENC/Tensor hardware. See the project research notes for official links.

## Turtle Beach/Xbox headset
The host capability scan looks for Turtle Beach/Xbox audio devices. The headset remains the microphone/headset input for Jarvis; NVIDIA Broadcast can optionally provide AI noise/echo cleanup without changing the Jarvis voice architecture.

## Enable
1. Set `AI_COMPUTER_CONTROL=true` in `.env`.
2. Keep `AI_COMPUTER_REQUIRE_CONFIRM=true` for the first tests.
3. Start the host bridge. For elevated Windows tasks, run `START-HOST-BRIDGE-ADMIN.bat`.
4. Restart Jarvis.
5. Ask Jarvis: `Check my PC computer-control capabilities.`

Do not enable LAN binding for the computer bridge. Keep it on `127.0.0.1`.
