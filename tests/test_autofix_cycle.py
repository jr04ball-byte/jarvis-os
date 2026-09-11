"""V24 P7: autofix cycle integration (fixture repo + fake worker)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import services
from fastapi import HTTPException
from project_worker import WorkerRunStore
from providers.base import ProviderResult
from schemas import ProjectWorkerAutofixRequest


class FakeWorker:
    name = "opencode"
    model = "opencode/test"
    configured = True

    async def complete(self, messages, *, temperature=0.15, max_tokens=4096, task_context=None):
        assert task_context["workspace"]
        assert task_context["permission"] == "workspace_write"
        return ProviderResult("did nothing", self.name, self.model)

    async def health(self):
        return {"online": True, "configured": True}


def make_workspace(tmp_path):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok(): assert True\n")
    return tmp_path


def setup(monkeypatch, tmp_path):
    make_workspace(tmp_path)
    monkeypatch.setenv("JARVIS_EMAIL_AGENT_PATH", str(tmp_path))
    monkeypatch.setattr(services, "intelligence_providers", {"opencode": FakeWorker()})
    monkeypatch.setattr(services, "project_worker_runs", WorkerRunStore(str(tmp_path / "pw.db")))


def test_autofix_verified_path(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it", max_retries=0, run_lint=False)
    out = asyncio.run(services._run_project_autofix_cycle(body))
    assert out["status"] == "verified" and out["attempts_used"] == 1
    run = out["run"]
    assert run["status"] == "verified" and len(run["attempts"]) == 1
    assert run["attempts"][0]["worker"]["provider"] == "opencode"


def test_autofix_unconfigured_worker_503(monkeypatch, tmp_path):
    make_workspace(tmp_path)
    monkeypatch.setenv("JARVIS_EMAIL_AGENT_PATH", str(tmp_path))
    monkeypatch.setattr(services, "intelligence_providers", {})
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(services._run_project_autofix_cycle(body))
    assert exc.value.status_code == 503


def test_autofix_unknown_target_404(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    body = ProjectWorkerAutofixRequest(target="no_such_target", goal="fix it")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(services._run_project_autofix_cycle(body))
    assert exc.value.status_code == 404


def test_autofix_resume_mismatch_409(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    store = services.project_worker_runs
    run = store.create("email_agent", str(tmp_path), "other goal", {})
    body = ProjectWorkerAutofixRequest(
        target="email_agent", goal="different goal", resume_run_id=run["id"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(services._run_project_autofix_cycle(body))
    assert exc.value.status_code == 409
