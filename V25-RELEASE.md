# Jarvis OS V25 RC1

This release implements the supplied V25 scope on top of the uploaded V24 RC1.

## Features

- The dashboard neural core responds to actual event sequence changes. No core video or idle animation simulates work. Activity distinguishes queued, active, completed, blocked, failed, awaiting approval, online, offline and idle states. Disconnection is shown explicitly. The last event remains visible after its transient activity expires.
- `/v1/activity?after=ID` provides a bounded 200-event history, counters, a sequence cursor and gap indication. Events contain names, timestamps and outcomes; arbitrary event payloads, paths, prompts and error text are excluded. The existing SSE stream is similarly redacted.
- `/voice` and legacy live-voice URLs open hold-to-dictate input with review-before-send and text replies. Pointer release, cancellation, focus loss and page hiding stop capture. There is no automatic restart. Browser speech recognition may use the browser vendor's service and requires browser support; typed input remains available.
- Live Gemini token and Deepgram speech-output endpoints return HTTP 410. Existing legacy voice source files remain for historical compatibility tests but are not served by the voice routes. No camera, Kinect, gestures or God's-eye feature is added.
- A lifespan-managed maintenance worker runs at startup and every five minutes. It checks free space and SQLite integrity, refreshes one backup per database under `API_DATA_DIR/maintenance-backups`, and regenerates a Markdown mirror under `API_DATA_DIR/memory-mirror`. The last successful backup is replaced only after a new backup closes successfully. Failures emit events and are logged locally. Shutdown waits for the active cycle.
- The mirror exports conversations and RAG documents. SQLite remains authoritative; Markdown edits are not imported. Filenames use numeric IDs or hashes. Writes are atomic per file, and a manifest identifies generated files. This is an eventually consistent mirror, not a multi-file atomic snapshot. It contains the same private content as the database and should remain local.
- `/v1/maintenance` reports the last cycle. Maintenance is restricted to internal database checks, backup refresh and mirror reconciliation; it does not self-rewrite source or install dependencies.
- The local iPhone backend exposes `/v1/companion/status`, `/chat`, and `/confirm`. All three require a configured `AI_API_TOKEN`, even if desktop auth is disabled. Chat and confirmation reuse the existing agent policy and rate limits. `/companion` uses these routes and stores its token for the browser session. The native Swift sample uses the chat route and an in-memory token; it remains a sample requiring Xcode/device validation.

## Local companion setup

Set a long random `AI_API_TOKEN` in your existing `.env`. For LAN access use the existing `docker-compose.lan.yml`, which also requires authentication for the other API routes. Open `/companion` on the gateway from your iPhone and enter that token. Use trusted HTTPS or a private VPN; do not expose this server publicly. Browser microphone support depends on secure-context and device permissions. The backend does not provide Apple push notifications or a signed iOS application.

## Review and stage an upgrade

Keep your running installation intact. Extract this release into a separate folder. With Python available, generate a review file:

```text
python tools/adopt_v25.py CURRENT_FOLDER RELEASE_FOLDER --plan review.json
```

Review changed and removed files, then stage the same reviewed contents into a new folder:

```text
python tools/adopt_v25.py CURRENT_FOLDER RELEASE_FOLDER --plan review.json --stage NEW_CANDIDATE_FOLDER
```

The tool checks both trees against the reviewed hashes, refuses existing destinations and linked paths, and verifies staged hashes. `.env`, runtime `data`, Git metadata and dependency/cache directories are excluded. It never activates the candidate or changes the running installation. This is explicit local adoption, not unattended upgrading.

Stop the old gateway before copying its `.env` and complete data directory into the candidate. Keep the original data untouched for rollback. Install dependencies, test the candidate on the target host, then switch your launcher to the new folder. To roll back, stop the candidate and launch the original folder; post-upgrade writes remain in the candidate and are not merged back automatically.

## Validation

See `reports/v25-validation.md` for results from this build. Target-host acceptance still includes Docker startup, real model calls, microphone release/cancel behavior, iPhone authentication and chat, maintenance with your actual databases, and offline candidate adoption/rollback. Earlier release reports are historical evidence, not certification of these integrations.
