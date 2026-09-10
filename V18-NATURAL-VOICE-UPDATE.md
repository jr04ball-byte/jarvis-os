# Jarvis V18 — Natural Voice-to-Voice

V18 keeps the V17.3 smart model router as the known-good foundation and makes voice-to-voice the primary interaction experience.

## Goals
- Continuous hands-free conversation on the main dashboard.
- Deepgram Flux STT + Aura TTS when configured.
- Local Whisper + browser TTS remains available without cloud voice.
- Natural conversational voice style rather than robotic read-aloud behavior.
- Barge-in: speaking while Jarvis is talking stops the current response and returns to listening.
- Voice responses are treated as spoken conversation, not screen-oriented prose.
- Jarvis no longer claims it cannot access the microphone when the Jarvis voice interface is active.

## Voice behavior
The core system prompt now explicitly identifies Jarvis as a voice-enabled assistant whose interface owns microphone capture, transcription, playback, and voice interaction. Generic microphone troubleshooting is reserved for actual subsystem failures.

## Recommended voice provider
For the most human-sounding output, use Deepgram Aura when `DEEPGRAM_API_KEY` is configured. The reasoning/tool brain remains local Ollama.

## Important baseline
V17.3 remains the rollback baseline. V18 changes the voice experience and voice behavior only; it does not intentionally change the smart model routing, tool safety, adaptive compute, Resource Center, or Model Lab architecture.
