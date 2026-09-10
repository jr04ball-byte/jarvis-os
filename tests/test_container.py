"""V24 P3: AppContainer mechanics (Objective 2 stepping stone)."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

from deps import AppContainer, container


def test_get_returns_singleton_per_name():
    c = AppContainer()
    assert c.get("monitor") is c.get("monitor")
    assert c.get("db") is not c.get("rag")


def test_unknown_service_raises_key_error():
    c = AppContainer()
    try:
        c.get("nope")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")
    try:
        c.override("nope", object())
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_override_and_reset_are_isolated():
    c = AppContainer()
    real = c.get("monitor")
    fake = object()
    c.override("monitor", fake)
    assert c.get("monitor") is fake
    c.reset("monitor")
    assert c.get("monitor") is real
    c.override("monitor", fake)
    c.reset()
    assert c.get("monitor") is real


def test_default_container_exposes_known_services():
    for name in ("db", "rag", "monitor", "orchestrator", "project_worker_runs"):
        assert container.get(name) is not None


def test_concurrent_get_returns_singleton():
    c = AppContainer()
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(c.get("monitor"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 8 and all(s is seen[0] for s in seen)
