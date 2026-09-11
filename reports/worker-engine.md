# Jarvis OS — Worker Engine (V24 release candidate)

The worker plane has two distinct responsibilities:

1. **Orchestrator** — durable deterministic Goal → Plan → Task state.
2. **Project Worker** — repository evidence, implementation boundaries,
   verification, repair classification, and resumable run history.

## Orchestrator
`api-gateway/orchestrator.py` stores projects, tasks, and audit events in SQLite.
It does not require an LLM to build the base plan. Task dependencies and
terminal-state rules are deterministic and heavily covered by tests.

Lifecycle events are emitted when tasks become queued/completed so telemetry
and the dashboard can observe work without coupling to the store.

## Project Worker
`api-gateway/project_worker.py` provides:

- workspace path validation
- safe file/read candidates and secret exclusions
- git status/diff evidence
- workspace fingerprint baselines
- test/build/lint command discovery
- targeted JS/TS and Python verification selection
- bounded subprocess execution with timeouts/tail capture
- code-vs-environment failure classification
- worker/repair prompt construction
- durable `WorkerRunStore`
- verification lifecycle events

`inspect_workspace()` now reports `git.available=true` explicitly on healthy Git
repositories, and `capture_git_diff()` includes the same availability signal.

## Deterministic fixture repository
`tests/fixtures/project_worker_repo/` is a real miniature Git repository fixture
copied into a temporary workspace during tests. It starts with a deliberate
calculator defect. `test_project_worker_fixture_e2e.py` executes:

```text
copy fixture
→ initialize Git baseline
→ inspect
→ capture baseline
→ verify (expected failure)
→ classify as code failure
→ apply minimal deterministic repair
→ compare baseline/diff evidence
→ targeted re-verification (expected pass)
```

This closes the previous gap where worker primitives were tested separately but
no deterministic repository exercised failure → repair → successful verify in
one flow.

## Safety boundary
Worker prompts forbid secret access, destructive Git operations, deployments,
publishing, purchasing, messaging, or unrelated changes. OpenCode receives the
specific registered workspace and permission scope. Sensitive side effects
remain owned by Jarvis confirmation policy rather than the coding worker.

## Next scaling consideration
The current in-process worker/event model is appropriate for a single Jarvis
host. If autonomous jobs later move to multiple processes/machines, worker
leases, event transport, and durable queues should be externalized rather than
adding more global process state.
