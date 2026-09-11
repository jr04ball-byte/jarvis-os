"""V24 P9: deeper services coverage (repair loop, persist paths, tool branches)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))

import services
from fastapi import HTTPException
from store import ConversationDB


def test_confirmation_expiry_and_pruning():
    ticket = services._create_confirmation("computer_type", {"text": "hi"}, "why")
    tid = ticket["confirmation_id"]
    services._PENDING_ACTIONS[tid]["created_at"] -= 700
    with pytest.raises(HTTPException) as exc:
        services._consume_confirmation(tid)
    assert exc.value.status_code == 410
    snap = services._pending_snapshot()
    assert all(i["confirmation_id"] != tid for i in snap["items"])


def test_agent_loop_initial_tool_result_and_memory(monkeypatch, tmp_path):
    from test_agent_loop import chat_response, patch_post

    patch_post(monkeypatch, [chat_response({"content": "done"})])
    db = ConversationDB(str(tmp_path / "c.db"))
    monkeypatch.setattr(services, "db", db)
    cid = db.create_conversation("t", "m")
    out = asyncio.run(services._run_agent_loop(
        "m", [{"role": "user", "content": "hi"}], "general", cid, 3,
        {"summary": "prior tool output"}))
    assert out["message"]["content"] == "done"
    history = db.get_conversation(cid)
    assert history and history[-1]["content"] == "done"
    db.close() if hasattr(db, "close") else None


def test_stream_chat_persists_assistant_message(monkeypatch, tmp_path):
    import json

    from test_agent_loop import FakeStreamClient

    FakeStreamClient.LINES = [json.dumps({"message": {"content": "yo"}}), json.dumps({"done": True})]
    monkeypatch.setattr(services.httpx, "AsyncClient", FakeStreamClient)
    db = ConversationDB(str(tmp_path / "c.db"))
    monkeypatch.setattr(services, "db", db)
    from schemas import ChatMessage, ChatRequest

    req = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")], conversation_id=None)
    cid = db.create_conversation("t", "m")
    req.conversation_id = cid

    async def collect():
        return [c async for c in services.stream_chat(req)]

    chunks = asyncio.run(collect())
    assert chunks[-1] == "data: [DONE]\n\n"
    assert db.get_conversation(cid)[-1]["content"] == "yo"


def test_execute_tool_core_missing_arg_and_branches(monkeypatch, tmp_path):
    import tools

    async def run():
        with pytest.raises(HTTPException) as exc:
            await services.execute_tool_core("read_file", {}, True)
        assert exc.value.status_code == 400
        async def fake_status():
            return {"bridge": "up"}

        monkeypatch.setattr(tools, "computer_status", fake_status)
        out = await services.execute_tool_core("computer_status", {}, True)
        assert out == {"result": {"bridge": "up"}}
        out = await services.execute_tool_core("local_tools_inventory", {}, True)
        assert isinstance(out["result"]["tools"], list)
        monkeypatch.setattr(services, "ARTIFACTS_DIR", tmp_path)
        out = await services.execute_tool_core(
            "artifact_create", {"title": "t", "kind": "markdown", "content": "c"}, True)
        assert out["result"]["content"] == "c"

    asyncio.run(run())


def test_agent_tool_unknown_and_inventory(monkeypatch):
    async def run():
        with pytest.raises(HTTPException):
            await services._agent_tool("nope_tool", {}, False)
        out = await services._agent_tool("local_tools_inventory", {}, False)
        assert isinstance(out["result"]["tools"], list)

    asyncio.run(run())


def test_normalize_tool_args_rejects_garbage():
    with pytest.raises(HTTPException):
        services._normalize_tool_args("{oops", "computer_type")
    assert services._normalize_tool_args({"a": 1}, "x") == {"a": 1}


def _autofix_setup(monkeypatch, tmp_path, failing=False):
    (tmp_path / "tests").mkdir(exist_ok=True)
    body = "def test_ok(): assert True\n" if not failing else "def test_bad(): assert False\n"
    (tmp_path / "tests" / "test_x.py").write_text(body)
    monkeypatch.setenv("JARVIS_EMAIL_AGENT_PATH", str(tmp_path))
    from project_worker import WorkerRunStore
    from providers.base import ProviderResult

    class FakeWorker:
        name = "opencode"
        model = "opencode/test"
        configured = True

        async def complete(self, messages, **kwargs):
            return ProviderResult("did nothing", self.name, self.model)

        async def health(self):
            return {"online": True, "configured": True}

    monkeypatch.setattr(services, "intelligence_providers", {"opencode": FakeWorker()})
    monkeypatch.setattr(services, "project_worker_runs", WorkerRunStore(str(tmp_path / "pw.db")))


def test_autofix_repair_then_blocked(monkeypatch, tmp_path):
    from schemas import ProjectWorkerAutofixRequest

    _autofix_setup(monkeypatch, tmp_path, failing=True)
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it", max_retries=1, run_lint=False)
    out = asyncio.run(services._run_project_autofix_cycle(body))
    assert out["status"] in {"blocked", "blocked_environment"}
    assert len(out["run"]["attempts"]) == 2


def test_autofix_unready_environment_blocked(monkeypatch, tmp_path):
    from schemas import ProjectWorkerAutofixRequest

    _autofix_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(services, "project_worker_health_snapshot", lambda ws: {"ready_for_worker": False})
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it")
    out = asyncio.run(services._run_project_autofix_cycle(body))
    assert out["status"] == "blocked_environment"


def test_autofix_resume_verified_short_circuits(monkeypatch, tmp_path):
    from schemas import ProjectWorkerAutofixRequest

    _autofix_setup(monkeypatch, tmp_path)
    store = services.project_worker_runs
    run = store.create("email_agent", str(tmp_path), "fix it", {})
    store.update(run["id"], status="verified")
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it", resume_run_id=run["id"])
    out = asyncio.run(services._run_project_autofix_cycle(body))
    assert out["status"] == "verified" and out["resumed"] is True


def test_autofix_retry_budget_exhausted(monkeypatch, tmp_path):
    from schemas import ProjectWorkerAutofixRequest

    _autofix_setup(monkeypatch, tmp_path)
    store = services.project_worker_runs
    run = store.create("email_agent", str(tmp_path), "fix it", {})
    store.update(run["id"], attempt={"attempt": 1})
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it", max_retries=0, resume_run_id=run["id"])
    out = asyncio.run(services._run_project_autofix_cycle(body))
    assert out["status"] == "blocked" and out["reason"] == "retry budget exhausted"


def test_autofix_resume_unknown_id_404(monkeypatch, tmp_path):
    from schemas import ProjectWorkerAutofixRequest

    _autofix_setup(monkeypatch, tmp_path)
    body = ProjectWorkerAutofixRequest(target="email_agent", goal="fix it", resume_run_id="nope")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(services._run_project_autofix_cycle(body))
    assert exc.value.status_code == 404
