"""V24 P4: event bus mechanics + emission contracts (Objective 3)."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import events
from events import (
    EventBus,
    HealthChanged,
    MemoryUpdated,
    ProviderFailed,
    ProviderSelected,
    TaskCompleted,
    TaskQueued,
    VerificationFailed,
    VerificationPassed,
    WorkerCompleted,
    WorkerStarted,
)


def test_all_directive_events_constructible():
    assert ProviderSelected(provider="gemini").name == "provider.selected"
    assert ProviderFailed(provider="x", error="boom").name == "provider.failed"
    assert WorkerStarted(run_id="r").name == "worker.started"
    assert WorkerCompleted(run_id="r", status="completed").name == "worker.completed"
    assert TaskQueued(project_id="p", task_id="t").name == "task.queued"
    assert TaskCompleted(project_id="p", task_id="t").name == "task.completed"
    assert VerificationPassed(workspace="w").name == "verification.passed"
    assert VerificationFailed(workspace="w").name == "verification.failed"
    assert HealthChanged(provider="ollama", online=True).name == "health.changed"
    assert MemoryUpdated(scope="project").name == "memory.updated"


def test_subscribe_emit_unsubscribe():
    bus = EventBus()
    seen = []
    handler = seen.append
    bus.subscribe(TaskCompleted, handler)
    bus.emit(TaskCompleted(project_id="p", task_id="t"))
    assert len(seen) == 1 and seen[0].task_id == "t"
    bus.unsubscribe(TaskCompleted, handler)
    bus.emit(TaskCompleted(project_id="p", task_id="t"))
    assert len(seen) == 1


def test_failing_handler_does_not_break_emitter():
    bus = EventBus()

    def bad(event):
        raise RuntimeError("listener bug")

    good = []
    bus.subscribe(TaskCompleted, bad)
    bus.subscribe(TaskCompleted, good.append)
    bus.emit(TaskCompleted(project_id="p", task_id="t"))
    assert len(good) == 1
    assert len(bus.errors) == 1


def test_async_handler_via_emit_async():
    async def run():
        bus = EventBus()
        seen = []

        async def handler(event):
            seen.append(event)

        bus.subscribe(TaskQueued, handler)
        await bus.emit_async(TaskQueued(project_id="p", task_id="t"))
        return seen

    seen = asyncio.run(run())
    assert len(seen) == 1 and seen[0].task_id == "t"


def test_orchestrator_emits_queued_then_completed():
    from orchestrator import OrchestratorStore

    with tempfile.TemporaryDirectory() as d:
        store = OrchestratorStore(os.path.join(d, "orchestrator.db"))
        seen: list = []
        events.bus.subscribe(TaskQueued, seen.append)
        events.bus.subscribe(TaskCompleted, seen.append)
        try:
            project = store.create_project("Get Email Agent ready for production")
            queued = [e for e in seen if isinstance(e, TaskQueued)]
            assert len(queued) == 1 and queued[0].project_id == project["id"]
            first = store.next_task(project["id"])
            store.transition(first["id"], "completed", {"ok": True})
            done = [e for e in seen if isinstance(e, TaskCompleted)]
            assert len(done) == 1 and done[0].task_id == first["id"]
            # completing the first task readies the second -> queued again
            assert len([e for e in seen if isinstance(e, TaskQueued)]) == 2
        finally:
            events.bus.unsubscribe(TaskQueued, seen.append)
            events.bus.unsubscribe(TaskCompleted, seen.append)
        store.close()


def test_worker_run_emits_started_and_completed(tmp_path):
    from project_worker import WorkerRunStore

    store = WorkerRunStore(str(tmp_path / "worker_runs.db"))
    seen: list = []
    events.bus.subscribe(WorkerStarted, seen.append)
    events.bus.subscribe(WorkerCompleted, seen.append)
    try:
        run = store.create("email_agent", "email-agent", "lifecycle check", {"branch": "test"})
        started = [e for e in seen if isinstance(e, WorkerStarted)]
        assert len(started) == 1 and started[0].run_id == run["id"]
        store.update(run["id"], status="completed")
        done = [e for e in seen if isinstance(e, WorkerCompleted)]
        assert len(done) == 1 and done[0].status == "completed" and done[0].attempt_count == 0
    finally:
        events.bus.unsubscribe(WorkerStarted, seen.append)
        events.bus.unsubscribe(WorkerCompleted, seen.append)


def test_brains_emits_selected_and_failed(monkeypatch):
    import brains
    from providers.base import ProviderResult

    class FakeProvider:
        name = "fake"
        model = "fake-model"
        configured = True

        def __init__(self, fail=False):
            self.fail = fail

        async def complete(self, messages, *, temperature=0.7, max_tokens=1024, task_context=None):
            if self.fail:
                raise RuntimeError("fake provider down")
            return ProviderResult("ok", self.name, self.model)

        async def health(self):
            return {"online": True, "configured": True}

    class StubDecision:
        selected = "fake"
        requested = "auto"
        reason = "test"
        chain = ["fake"]

        def as_dict(self):
            return {"selected": self.selected}

    class StubRouter:
        def decide(self, *args, **kwargs):
            return StubDecision()

        def eligible_chain(self, chain):
            return list(chain)

        def provider_context(self, *args, **kwargs):
            return {}

        def record_provider_result(self, *args, **kwargs):
            pass

    seen: list = []
    events.bus.subscribe(ProviderSelected, seen.append)
    events.bus.subscribe(ProviderFailed, seen.append)
    try:
        monkeypatch.setattr(brains, "providers", {"fake": FakeProvider()})
        monkeypatch.setattr(brains, "router_engine", StubRouter())
        req = brains.BrainRequest(messages=[brains.Message(role="user", content="hi")])

        async def run_ok():
            return await brains.complete(req)

        out = asyncio.run(run_ok())
        assert out["provider"] == "fake"
        selected = [e for e in seen if isinstance(e, ProviderSelected)]
        assert len(selected) == 1 and selected[0].provider == "fake"

        monkeypatch.setattr(brains, "providers", {"fake": FakeProvider(fail=True)})
        from fastapi import HTTPException

        async def run_fail():
            return await brains.complete(req)

        try:
            asyncio.run(run_fail())
        except HTTPException as exc:
            assert exc.status_code == 502
        else:
            raise AssertionError("expected 502 when all brains fail")
        failed = [e for e in seen if isinstance(e, ProviderFailed)]
        assert len(failed) == 1 and failed[0].provider == "fake"
    finally:
        events.bus.unsubscribe(ProviderSelected, seen.append)
        events.bus.unsubscribe(ProviderFailed, seen.append)


def test_global_bus_identity_is_stable_across_legacy_reset_call():
    first = events.bus
    assert events.reset_bus() is first


def test_base_event_subscription_receives_all_typed_events_once():
    from events import Event

    bus = EventBus()
    seen = []
    bus.subscribe(Event, seen.append)
    bus.emit(TaskQueued(project_id="p", task_id="t"))
    bus.emit(ProviderSelected(provider="gemini", model="m", reason="r"))
    assert [event.name for event in seen] == ["task.queued", "provider.selected"]


def test_worker_verified_is_terminal_lifecycle_event(tmp_path):
    from project_worker import WorkerRunStore

    store = WorkerRunStore(str(tmp_path / "verified_runs.db"))
    seen = []
    events.bus.subscribe(WorkerCompleted, seen.append)
    try:
        run = store.create("email_agent", "repo", "verify terminal event", {})
        store.update(run["id"], status="verified", attempt={"ok": True})
        assert len(seen) == 1
        assert seen[0].status == "verified"
        assert seen[0].attempt_count == 1
    finally:
        events.bus.unsubscribe(WorkerCompleted, seen.append)
