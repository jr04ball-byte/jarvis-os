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

## Verification

- Python: 258 passed, 1 skipped. The skipped test requires an interactive desktop; native window enumeration was separately exercised from a worker thread on this Windows desktop.
- Node: 21 passed, 0 failed.
- Python compilation and undefined-name lint (F821) passed. This is not a claim that every broad lint rule passes across legacy code.
- Gateway health is 25.0.0-rc1; /dashboard-inline-v25 returned HTTP 200 and 77,775 bytes.
- Live bridge returned 21 windows. Settings returned verified success on repeated calls, including an already running Settings window.
- Live Gemini-to-Blender tool execution succeeded for task a820ddc7 using Blender 5.2.1 LTS. Blender exited 0; the PNG (659,971 bytes) and .blend (143,616 bytes) both returned successfully through /v1/creative-files/a820ddc7/. These are operational smoke-test artifacts, not a claim of finished artwork quality.

## Deployment and maintenance

The changed runtime files were deployed to the existing Windows installation with backups; gateway and bridge were restarted with hidden windows and their existing environments. Preserve local .env, data, OAuth tokens and logs. They are not included in this ZIP. Do not replace credentials or enable unrestricted shell execution when adopting this package.

The inline dashboard source is unchanged by this repair. Gemini remains primary. No new God's Eye assets, camera features, gestures or automatic microphone listening were added.

The input ZIP is not a Git checkout. Local tests and report generation were performed; no GitHub merge or CI run is claimed. Review and commit the focused diff in the real repository, then run its CI.

Known limits: a generated Blender script is still code executed under the Jarvis account; its lint is not an operating-system sandbox. Model-generated scenes may still fail, and failures must remain visible. This repair does not establish that every PC action or every generated scene succeeds. No system clock changes were made.

To reproduce the Python tests, activate an environment with the gateway test dependencies and ensure that environment's Python is first on PATH. Worker verification launches Python subprocesses; an unrelated PATH Python without pytest produces environment failures. Run pytest with a writable --basetemp on restricted hosts.
