# V15 Jarvis Dashboard Update

- Reworked the main dashboard into a light, polished glassmorphism command-center layout inspired by the supplied visual reference.
- Kept Jarvis functionality and existing API routes intact while replacing the dashboard presentation with Jarvis-specific information.
- Added a live local time/date display.
- Removed dashboard navigation that sends the user to the separate `/voice` screen.
- Embedded the voice assistant directly into the main dashboard.
- Voice defaults to Deepgram when configured, with local browser TTS fallback.
- Dashboard attempts to start voice mode automatically on load; browser microphone/autoplay policies may require the user to allow microphone/audio once.
- Added Local Qwen, Gemini Cloud, Auto brain controls and status cards.
- Added live connections, artifacts, computer mode, quick actions, and system status panels without leaving the main screen.
- Existing backend, tool router, artifacts, Gemini, Deepgram, Home Assistant, Google Workspace, Sales Machine and Email Agent separation remain unchanged.
