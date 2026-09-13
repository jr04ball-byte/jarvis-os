# Jarvis September 12 repair handoff

This is a repair of the supplied ai-system-update.zip, not a replacement architecture.

## Changes

- Native, pointer-safe Win32 top-level window enumeration replaces UIA enumeration in server worker threads. Foreground/focus helpers use the same native module.
- Settings aliases launch ms-settings:. Verification matches SystemSettings or its visible ApplicationFrameHost Settings window, including a new window in an existing process and repeated launches. It no longer accepts Explorer as evidence of Settings.
- Blender discovery recognizes versioned Blender Foundation installation folders. Artifact paths are absolute. Python exceptions now produce nonzero Blender exits; success requires a zero exit and verified scene/image artifacts.
- Pillow is now declared in gateway requirements, checked before generation, and installed in the live gateway Python. Previously valid rendered images were rejected because that dependency was absent.
- Scene generation requests complete compact code, checks completion and Gemini finish reason, and retries incomplete generation once. Runtime traceback errors receive one bounded script repair. Each failed attempt's artifacts are removed before rerunning.
- The two historical deep/fast routing tests now assert the already implemented Gemini-primary policy and preserve checks for fallback providers.
- Added regression tests and regenerated repository reports from the clean source tree.
- Expanded the iPhone companion into an installable Safari PWA with chat, approval handling, multi-file uploads, recent-file browsing, and authenticated downloads. Phone files stream into a configurable folder on the Windows host with traversal-safe names, collision handling, size limits, atomic completion, timestamps, and SHA-256 hashes.
- Updated the Swift companion sample with a configurable gateway URL and Files picker uploads. It remains a source sample that requires Xcode on macOS for signing and device validation.
- Replaced the PC dashboard's static “Weather not connected” placeholder with a cached live Open-Meteo feed and a saved weather-location setting. No weather API key is required.
- The PC dashboard now discovers the connected Google OAuth account from the gateway on every refresh. Its account field is read-only and its Connect Google action starts the real OAuth flow, preventing the old API-key/email-field confusion.
- Time-sensitive questions now route through the agent path. The system prompt injects the authoritative host date/time and forbids alternate-timeline or knowledge-cutoff excuses. `research_search` uses Exa when configured and otherwise uses the existing Gemini key with Google Search grounding, returning its grounded answer and source links.
- The PC dashboard has local spoken replies enabled by default through the browser speech engine, with a persistent Voice On/Off control. Starting push-to-talk cancels current speech for immediate interruption. This adds no speech API cost and keeps text-to-speech local.
- Jarvis now receives explicit policy to save durable, non-sensitive user preferences and goals through its existing fact-memory tool, recall them for personalization, and never silently store credentials or other sensitive data.

## Verification

- Python: 264 passed, 1 skipped. The skipped test requires an interactive desktop; native window enumeration was separately exercised from a worker thread on this Windows desktop.
- Node: 21 passed, 0 failed.
- Python compilation and undefined-name lint (F821) passed. This is not a claim that every broad lint rule passes across legacy code.
- Gateway health is 25.0.0-rc1; /dashboard-inline-v25 returned HTTP 200 and 77,775 bytes.
- Live bridge returned 21 windows. Settings returned verified success on repeated calls, including an already running Settings window.
- Live Gemini-to-Blender tool execution succeeded for task a820ddc7 using Blender 5.2.1 LTS. Blender exited 0; the PNG (659,971 bytes) and .blend (143,616 bytes) both returned successfully through /v1/creative-files/a820ddc7/. These are operational smoke-test artifacts, not a claim of finished artwork quality.

## Deployment and maintenance

The changed runtime files were deployed to the existing Windows installation with backups; gateway and bridge were restarted with hidden windows and their existing environments. Preserve local .env, data, OAuth tokens and logs. They are not included in this ZIP. Do not replace credentials or enable unrestricted shell execution when adopting this package.

The inline dashboard source is unchanged by this repair. Gemini remains primary. No new God's Eye assets, camera features, gestures or automatic microphone listening were added.

The companion is served at `/companion`. Set `AI_REQUIRE_AUTH=true` and a strong `AI_API_TOKEN`, bind the gateway to the LAN interface, and enter that token once in the companion. Uploads default to `phone-uploads/Inbox`; override the root with `JARVIS_PHONE_UPLOAD_DIR` and the per-file limit with `JARVIS_PHONE_UPLOAD_MAX_MB` (default 250). Plain LAN HTTP supports chat and files, but iPhone browsers generally require HTTPS for microphone capture.

On this Windows host, Ethernet remains classified as Public. Run `scripts/enable-iphone-companion-firewall.ps1` once to request elevation and create one inbound TCP 8000 exception limited to the Public profile and `LocalSubnet`. Jarvis still requires its API token. Remove or disable the rule before using an untrusted network.

The input ZIP is not a Git checkout. Local tests and report generation were performed; no GitHub merge or CI run is claimed. Review and commit the focused diff in the real repository, then run its CI.

Known limits: a generated Blender script is still code executed under the Jarvis account; its lint is not an operating-system sandbox. Model-generated scenes may still fail, and failures must remain visible. This repair does not establish that every PC action or every generated scene succeeds. No system clock changes were made.

To reproduce the Python tests, activate an environment with the gateway test dependencies and ensure that environment's Python is first on PATH. Worker verification launches Python subprocesses; an unrelated PATH Python without pytest produces environment failures. Run pytest with a writable --basetemp on restricted hosts.
