# V25 RC1 validation

- Python: 164 passed (one upstream Starlette deprecation warning).
- JavaScript: 22 passed, including three push-to-talk lifecycle tests.
- Python compileall: passed.
- Repository CI Ruff gates: passed, using the repository root as working directory.
- New coverage: activity redaction/history limits/outcomes, SQLite backup and Markdown mirror reconciliation, corrupt database failure, stale adoption plans, protected companion status, disabled duplex/TTS routes, and PTT release/hidden-page/cancellation behavior.

Tests ran on Windows with an isolated Python 3.12 environment and the gateway's pinned requirements. The test runner supplied its virtual environment on PATH and a writable temp directory. Worker execution now resolves the PATH executable explicitly, avoiding Windows parent-interpreter precedence.

No live Docker, GPU/model-provider, OAuth, physical microphone, iPhone Safari, or Xcode validation was performed. Browser PTT depends on browser speech recognition; no local-only recognition guarantee is made. The original installation was not deployed over or modified. Legacy voice-engine tests validate retained library code, not an enabled duplex feature.
