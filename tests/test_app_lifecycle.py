"""Composition-root regression tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import deps
import main
from fastapi.testclient import TestClient


def test_app_version_is_single_source_of_truth():
    with TestClient(main.create_app()) as client:
        assert client.get("/health").json()["version"] == deps.APP_VERSION
        assert client.get("/").json()["version"] == deps.APP_VERSION


def test_lifespan_exposes_composition_objects():
    app = main.create_app()
    with TestClient(app):
        assert app.state.container is deps.container
        assert app.state.event_bus is not None
        assert app.state.telemetry is not None
