# V25 RC1 validation

- Python: 165 passed (one upstream Starlette deprecation warning).
- JavaScript: 22 passed, including three push-to-talk lifecycle tests.
- Python compileall: passed.
- Repository CI Ruff gates: passed, using the repository root as working directory.
- New coverage: activity redaction/history limits/outcomes, SQLite backup and Markdown mirror reconciliation, corrupt database failure, stale adoption plans, protected companion status, disabled duplex/TTS routes, and PTT release/hidden-page/cancellation behavior.
- Dashboard smoke checks: `/dashboard` visibly loads the neon reference layout with sidebar, provider cards, active task, calendar, lower operational panels, command bar, animated centerpiece, and motion toggle. The legacy overhead dashboard routes and `command-center`, `orb`, and `bg` media return 404. Regression assertions cover the V25 layout, removed legacy routes and assets, and activity polling.
- Blender 5.2.1 rendered a 120-frame centerpiece loop at 960×640. The editable scene is `api-gateway/assets/jarvis-neon-v25.blend`; the browser loop is `api-gateway/assets/neon-neural-v25.mp4` with the PNG fallback `api-gateway/assets/neon-neural-v25.png`.

Tests ran on Windows with an isolated Python 3.12 environment and the gateway's pinned requirements. The test runner supplied its virtual environment on PATH and a writable temp directory. Worker execution now resolves the PATH executable explicitly, avoiding Windows parent-interpreter precedence.

No live Docker, GPU/model-provider, OAuth, physical microphone, iPhone Safari, or Xcode validation was performed. Browser PTT depends on browser speech recognition; no local-only recognition guarantee is made. The original installation was not deployed over or modified. Legacy voice-engine tests validate retained library code, not an enabled duplex feature.
