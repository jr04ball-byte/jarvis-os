"""V24 P5: bus-derived telemetry collector (Objective 4, first slice).

Subscribes to lifecycle events and keeps queryable counters plus a bounded
recent-event ring. No secrets ever flow through events (ids/kinds/statuses
only), so snapshots are dashboard-safe. Success latency stays in
RouterTelemetry; this collector owns cross-cutting outcome counts.
"""
from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import asdict
from typing import Any

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
from events import (
    bus as event_bus,
)

RECENT_LIMIT = 200


class TelemetryCollector:
    def __init__(self, source: EventBus | None = None) -> None:
        self._lock = threading.RLock()
        self._counts: Counter = Counter()
        self._failure_latency_ms: dict[str, int] = {}
        self._recent: deque = deque(maxlen=RECENT_LIMIT)
        self._source: EventBus | None = None
        if source is not None:
            self.attach(source)

    def attach(self, source: EventBus) -> None:
        if self._source is source:
            return
        self.detach()
        self._source = source
        source.subscribe(ProviderSelected, self._on_selected)
        source.subscribe(ProviderFailed, self._on_failed)
        source.subscribe(TaskQueued, self._on_queued)
        source.subscribe(TaskCompleted, self._on_completed)
        source.subscribe(WorkerStarted, self._on_worker_started)
        source.subscribe(WorkerCompleted, self._on_worker_completed)
        source.subscribe(VerificationPassed, self._on_verification_passed)
        source.subscribe(VerificationFailed, self._on_verification_failed)
        source.subscribe(HealthChanged, self._on_health_changed)
        source.subscribe(MemoryUpdated, self._on_memory_updated)

    def detach(self) -> None:
        source = self._source
        if source is None:
            return
        for event_cls, handler in (
            (ProviderSelected, self._on_selected),
            (ProviderFailed, self._on_failed),
            (TaskQueued, self._on_queued),
            (TaskCompleted, self._on_completed),
            (WorkerStarted, self._on_worker_started),
            (WorkerCompleted, self._on_worker_completed),
            (VerificationPassed, self._on_verification_passed),
            (VerificationFailed, self._on_verification_failed),
            (HealthChanged, self._on_health_changed),
            (MemoryUpdated, self._on_memory_updated),
        ):
            source.unsubscribe(event_cls, handler)
        self._source = None

    def _record(self, key: str, event) -> None:
        with self._lock:
            self._counts[key] += 1
            self._recent.append(asdict(event))

    def _on_selected(self, event: ProviderSelected) -> None:
        self._record(f"provider.selected.{event.provider}", event)

    def _on_failed(self, event: ProviderFailed) -> None:
        self._record(f"provider.failed.{event.provider}", event)
        with self._lock:
            self._failure_latency_ms[event.provider] = (
                self._failure_latency_ms.get(event.provider, 0) + event.elapsed_ms)

    def _on_queued(self, event: TaskQueued) -> None:
        self._record("task.queued", event)

    def _on_completed(self, event: TaskCompleted) -> None:
        self._record("task.completed", event)

    def _on_worker_started(self, event: WorkerStarted) -> None:
        self._record("worker.started", event)

    def _on_worker_completed(self, event: WorkerCompleted) -> None:
        self._record(f"worker.completed.{event.status}", event)

    def _on_verification_passed(self, event: VerificationPassed) -> None:
        self._record("verification.passed", event)

    def _on_verification_failed(self, event: VerificationFailed) -> None:
        self._record("verification.failed", event)

    def _on_health_changed(self, event: HealthChanged) -> None:
        self._record(f"health.{event.provider}.{'online' if event.online else 'offline'}", event)

    def _on_memory_updated(self, event: MemoryUpdated) -> None:
        self._record(f"memory.updated.{event.scope or 'unknown'}", event)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._failure_latency_ms.clear()
            self._recent.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "failure_latency_ms": dict(self._failure_latency_ms),
                "recent": list(self._recent),
            }


collector = TelemetryCollector(event_bus)
"""Process-global collector wired to the global bus (dashboard reads this)."""
