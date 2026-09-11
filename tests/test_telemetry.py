"""V24 P5: telemetry collector contracts (Objective 4 first slice)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from events import (
    EventBus,
    ProviderFailed,
    ProviderSelected,
    TaskCompleted,
    TaskQueued,
    WorkerCompleted,
    WorkerStarted,
)
from telemetry_collector import TelemetryCollector


def make_collector():
    bus = EventBus()
    return TelemetryCollector(bus), bus


def test_counts_outcomes_per_key():
    collector, bus = make_collector()
    bus.emit(ProviderSelected(provider="gemini", model="m", reason="r"))
    bus.emit(ProviderSelected(provider="gemini", model="m", reason="r"))
    bus.emit(ProviderFailed(provider="gemini", error="boom", elapsed_ms=120))
    bus.emit(TaskQueued(project_id="p", task_id="t1"))
    bus.emit(TaskCompleted(project_id="p", task_id="t1"))
    bus.emit(WorkerStarted(run_id="r", target="t", workspace="w"))
    bus.emit(WorkerCompleted(run_id="r", status="completed", attempt_count=2))
    snap = collector.snapshot()
    assert snap["counts"]["provider.selected.gemini"] == 2
    assert snap["counts"]["provider.failed.gemini"] == 1
    assert snap["counts"]["task.queued"] == 1
    assert snap["counts"]["task.completed"] == 1
    assert snap["counts"]["worker.started"] == 1
    assert snap["counts"]["worker.completed.completed"] == 1
    assert snap["failure_latency_ms"]["gemini"] == 120


def test_recent_ring_is_bounded_and_dashboard_safe():
    collector, bus = make_collector()
    for i in range(250):
        bus.emit(TaskQueued(project_id="p", task_id=f"t{i}"))
    snap = collector.snapshot()
    assert len(snap["recent"]) == 200
    assert snap["recent"][-1]["task_id"] == "t249"
    blob = str(snap)
    assert "API_KEY" not in blob and "Bearer" not in blob


def test_unattached_collector_stays_empty():
    collector = TelemetryCollector()
    assert collector.snapshot() == {"counts": {}, "failure_latency_ms": {}, "recent": []}


def test_summary_route_serves_collector_snapshot():
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = client.get("/v1/telemetry/summary")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"counts", "failure_latency_ms", "recent"}


def test_verification_health_and_memory_events_are_counted():
    from events import (
        HealthChanged,
        MemoryUpdated,
        VerificationFailed,
        VerificationPassed,
    )

    collector, bus = make_collector()
    bus.emit(VerificationPassed(workspace="repo", detail="pytest"))
    bus.emit(VerificationFailed(workspace="repo", detail="lint"))
    bus.emit(HealthChanged(provider="ollama", online=True))
    bus.emit(MemoryUpdated(scope="project", key="x"))
    counts = collector.snapshot()["counts"]
    assert counts["verification.passed"] == 1
    assert counts["verification.failed"] == 1
    assert counts["health.ollama.online"] == 1
    assert counts["memory.updated.project"] == 1


def test_attach_is_idempotent_and_detach_stops_collection():
    collector, bus = make_collector()
    collector.attach(bus)
    bus.emit(TaskQueued(project_id="p", task_id="one"))
    assert collector.snapshot()["counts"]["task.queued"] == 1
    collector.detach()
    bus.emit(TaskQueued(project_id="p", task_id="two"))
    assert collector.snapshot()["counts"]["task.queued"] == 1


def test_live_event_stream_route_is_registered():
    import main

    assert "/v1/telemetry/events" in main.app.openapi()["paths"]
    source = (Path(__file__).resolve().parents[1] / "api-gateway" / "dashboard.html").read_text(encoding="utf-8")
    assert "EventSource('/v1/telemetry/events')" in source
