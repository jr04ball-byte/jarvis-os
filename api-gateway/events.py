"""V24 P4: in-process event bus (Objective 3, first slice).

Subsystems communicate through typed events instead of tight coupling.
Sync handlers run inline; async handlers are awaited from async contexts
(emit_async) or driven to completion from sync contexts (emit). A handler
exception never breaks the emitter — it is logged and recorded.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Event:
    name: str = ""
    ts: float = field(default_factory=_now)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSelected(Event):
    name: str = "provider.selected"
    provider: str = ""
    model: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ProviderFailed(Event):
    name: str = "provider.failed"
    provider: str = ""
    error: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class WorkerStarted(Event):
    name: str = "worker.started"
    run_id: str = ""
    target: str = ""
    workspace: str = ""


@dataclass(frozen=True)
class WorkerCompleted(Event):
    name: str = "worker.completed"
    run_id: str = ""
    status: str = ""
    attempt_count: int = 0


@dataclass(frozen=True)
class TaskQueued(Event):
    name: str = "task.queued"
    project_id: str = ""
    task_id: str = ""
    kind: str = ""


@dataclass(frozen=True)
class TaskCompleted(Event):
    name: str = "task.completed"
    project_id: str = ""
    task_id: str = ""
    kind: str = ""


@dataclass(frozen=True)
class VerificationPassed(Event):
    name: str = "verification.passed"
    workspace: str = ""
    detail: str = ""


@dataclass(frozen=True)
class VerificationFailed(Event):
    name: str = "verification.failed"
    workspace: str = ""
    detail: str = ""


@dataclass(frozen=True)
class HealthChanged(Event):
    name: str = "health.changed"
    provider: str = ""
    online: bool = False


@dataclass(frozen=True)
class MemoryUpdated(Event):
    name: str = "memory.updated"
    scope: str = ""
    key: str = ""


Handler = Callable[[Event], Any]


class EventBus:
    """Tiny typed pub/sub bus. Subscribe by event class."""

    def __init__(self) -> None:
        self._subs: dict[type, list[Handler]] = defaultdict(list)
        self._errors: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def subscribe(self, event_cls: type, handler: Handler) -> Handler:
        with self._lock:
            if handler not in self._subs[event_cls]:
                self._subs[event_cls].append(handler)
        return handler

    def unsubscribe(self, event_cls: type, handler: Handler) -> None:
        with self._lock:
            try:
                self._subs[event_cls].remove(handler)
            except ValueError:
                pass

    def subscribers(self, event_cls: type) -> list[Handler]:
        """Return exact subscribers plus catch-all ``Event`` subscribers.

        Catch-all listeners power telemetry/SSE without forcing producers to
        know about presentation concerns. Duplicate handlers are de-duplicated
        while preserving registration order.
        """
        with self._lock:
            handlers = list(self._subs.get(event_cls, []))
            if event_cls is not Event:
                handlers.extend(self._subs.get(Event, []))
            return list(dict.fromkeys(handlers))

    @property
    def errors(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._errors)

    def _run_handler(self, handler: Handler, event: Event) -> Any:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None:
                    task = loop.create_task(result)
                    task.add_done_callback(self._log_task_error)
                    return task
                return asyncio.run(result)
            return result
        except Exception as exc:  # noqa: BLE001 - a bad listener must not break emitters
            with self._lock:
                self._errors.append({"handler": getattr(handler, "__name__", repr(handler)), "error": str(exc)})
            logger.debug("event handler failed for %s: %s", event.name, exc)
            return None

    @staticmethod
    def _log_task_error(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001 - background listener errors are recorded
            logger.debug("background event handler failed: %s", exc)

    def emit(self, event: Event) -> None:
        """Sync entry point: safe from sync and async code."""
        for handler in self.subscribers(type(event)):
            self._run_handler(handler, event)

    async def emit_async(self, event: Event) -> None:
        """Async entry point: awaits coroutine handlers inline."""
        for handler in self.subscribers(type(event)):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - a bad listener must not break emitters
                with self._lock:
                    self._errors.append({"handler": getattr(handler, "__name__", repr(handler)), "error": str(exc)})
                logger.debug("event handler failed for %s: %s", event.name, exc)


bus = EventBus()
"""Process-global bus. Tests should construct their own EventBus or
monkeypatch this module attribute; production code must only emit/subscribe."""


def reset_bus() -> EventBus:
    """Return the process bus without replacing its identity.

    Older test helpers replaced the module global, which left modules that had
    imported ``bus`` holding a stale object and silently split lifecycle events
    across two buses. Tests needing isolation should instantiate ``EventBus()``
    directly; the production process bus has stable identity by design.
    """
    return bus
