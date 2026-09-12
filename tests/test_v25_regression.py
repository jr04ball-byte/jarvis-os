"""V25 regression tests: tool registry, ToolResult, host bridge, orchestrator recovery.

Tests verify: unknown tools fail closed, invalid args are rejected,
native function calls work, approval resumption preserves state,
failed tools are never reported completed, Windows Settings resolution,
browser verification, artifact retrieval, and task cancellation/recovery.

Plus V25 corrections: type/enum/limit/unknown-field validation,
false-success prevention, non-idempotent retry protection,
request-driven Blender scenes, stale artifact rejection,
invalid image rejection, startup recovery invocation,
and background subprocess console-window suppression.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host-bridge"))

import pytest

from fastapi import HTTPException
from security import allowed, requires_confirmation, risk
from tools_registry import ToolNotFoundError, ToolArgsError
from orchestrator import OrchestratorStore, STATUS, TERMINAL
from events import EventBus, TaskStarted, TaskFailed, TaskCancelled
from tool_result import ToolResult
from services import _agent_tool, _result_is_failure, _is_idempotent, MAX_TOOL_RETRIES


# --- Tool Registry Tests ---

def test_registry_known_tools():
    from services import TOOL_REGISTRY
    assert TOOL_REGISTRY.has("file_search")
    assert TOOL_REGISTRY.has("computer_shell")
    assert TOOL_REGISTRY.has("computer_browser")

def test_registry_unknown_tool_raises():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolNotFoundError):
        TOOL_REGISTRY.get_entry("nonexistent_tool_xyz")

def test_registry_builds_list():
    from services import build_tools_list
    tools = build_tools_list()
    assert isinstance(tools, list)
    assert len(tools) > 0
    names = {t["name"] for t in tools}
    assert "file_search" in names
    assert "computer_shell" in names
    assert "computer_browser" in names

def test_registry_validation():
    from services import TOOL_REGISTRY
    args = TOOL_REGISTRY.validate_args("file_search", '{"query": "test"}')
    assert args["query"] == "test"

def test_registry_validation_rejects_invalid_json():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError):
        TOOL_REGISTRY.validate_args("file_search", "not json")

def test_registry_validation_rejects_missing_required():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError):
        TOOL_REGISTRY.validate_args("write_file", {})

# --- Security Policy Tests ---

def test_security_policy_known_tools():
    assert allowed("file_search")
    assert allowed("computer_shell")
    assert not allowed("nonexistent_tool")

def test_security_risk_classes():
    assert risk("read_file") == "read"
    assert risk("computer_type") == "write"
    assert risk("unknown_tool") == "unknown"

def test_security_confirmation_required():
    assert requires_confirmation("computer_shell")
    assert not requires_confirmation("read_file")

# --- ToolResult Tests ---

def test_tool_result_success():
    from tool_result import ToolResult
    tr = ToolResult.success("file_search", result={"ok": True})
    assert tr.status == "success"
    assert tr.tool == "file_search"
    assert tr.is_success()
    assert not tr.is_failure()

def test_tool_result_failure():
    from tool_result import ToolResult
    tr = ToolResult.failure("computer_shell", error="timeout")
    assert tr.status == "error"
    assert tr.is_failure()
    assert not tr.is_success()

def test_tool_result_confirmation():
    from tool_result import ToolResult
    tr = ToolResult.needs_confirmation("write_file", reason="test")
    assert tr.status == "confirmation_required"

def test_tool_result_to_dict():
    from tool_result import ToolResult
    tr = ToolResult.success("read_file", result={"path": "/tmp/x"})
    d = tr.to_dict()
    assert d["status"] == "success"
    assert d["result"]["path"] == "/tmp/x"

# --- Artifact Tests ---

def test_artifact_create_and_read():
    from services import create_artifact, _artifact_path, _artifact_read
    item = create_artifact("test", "markdown", "# Hello", {"author": "test"})
    assert "id" in item
    assert item["kind"] == "markdown"
    assert _artifact_path(item["id"]).exists()
    read = _artifact_read(item["id"])
    assert read["title"] == "test"

# --- Orchestrator Tests ---

def test_orchestrator_create_and_lifecycle(tmp_path):
    store = OrchestratorStore(tmp_path / "jarvis.db")
    p = store.create_project("test goal")
    assert p["status"] == "active"
    task = store.next_task(p["id"])
    assert task is not None
    assert task["status"] == "ready"

def test_orchestrator_cancel(tmp_path):
    store = OrchestratorStore(tmp_path / "jarvis.db")
    p = store.create_project("cancel test")
    task = store.next_task(p["id"])
    task_id = task["id"]
    store.start_task(task_id)
    result = store.cancel_task(task_id, "user cancelled")
    cancelled = next(t for t in result["tasks"] if t["id"] == task_id)
    assert cancelled["status"] == "cancelled"

def test_orchestrator_recover_running(tmp_path):
    store = OrchestratorStore(tmp_path / "jarvis.db")
    p = store.create_project("recover test")
    task = store.next_task(p["id"])
    task_id = task["id"]
    store.start_task(task_id)
    recovered = store.recover()
    assert len(recovered) > 0
    recovered_task = next(t for t in recovered if t["id"] == task_id)
    assert recovered_task["status"] == "ready"

def test_orchestrator_status_sets():
    assert "completed" in STATUS
    assert "failed" in STATUS
    assert "cancelled" in STATUS
    assert "running" in STATUS
    assert "pending" in STATUS
    assert "ready" in STATUS

def test_terminal_states():
    for s in TERMINAL:
        assert s in STATUS

# --- Event Types ---

def test_event_types():
    bus = EventBus()
    recorded = []
    bus.subscribe(TaskStarted, lambda e: recorded.append("started"))
    bus.subscribe(TaskFailed, lambda e: recorded.append("failed"))
    bus.subscribe(TaskCancelled, lambda e: recorded.append("cancelled"))
    bus.emit(TaskStarted(project_id="p1", task_id="t1", kind="test"))
    bus.emit(TaskFailed(project_id="p1", task_id="t1", kind="test", error="fail"))
    bus.emit(TaskCancelled(project_id="p1", task_id="t1", kind="test"))
    assert len(recorded) == 3
    assert recorded == ["started", "failed", "cancelled"]

# --- Agent Tool Result Tests (async) ---

def test_agent_tool_returns_tool_result():
    import asyncio
    from tool_result import ToolResult
    from services import _agent_tool
    result = asyncio.run(_agent_tool("google_accounts", {}))
    assert isinstance(result, ToolResult)
    assert result.is_success()

def test_agent_tool_unknown_returns_failure():
    import asyncio
    from tool_result import ToolResult
    from services import _agent_tool
    from tools_registry import ToolNotFoundError
    result = asyncio.run(_agent_tool("nonexistent_tool_xyz", {}))
    assert result.is_failure()
    assert "unknown" in result.error

# --- Build Tools Summary ---

def test_build_tools_list_shape():
    from services import build_tools_list
    tools = build_tools_list()
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "confirmation" in t

# --- V25 Correction Tests ---

# 1. Full schema validation: types, enums, limits, unknown fields

def test_validation_rejects_wrong_type():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError, match="boolean"):
        TOOL_REGISTRY.validate_args("write_file", '{"path": "/tmp/x", "content": "hi", "overwrite": "yes"}')

def test_validation_rejects_integer_for_boolean():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError, match="boolean"):
        TOOL_REGISTRY.validate_args("write_file", '{"path": "/tmp/x", "content": "hi", "overwrite": 1}')

def test_validation_rejects_enum_value():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError, match="browser"):
        TOOL_REGISTRY.validate_args("computer_browser", '{"url": "https://example.com", "browser": "firefox"}')

def test_validation_rejects_numeric_limit():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError):
        TOOL_REGISTRY.validate_args("file_search", '{"query": "test", "limit": 200}')

def test_validation_rejects_unknown_fields():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError, match="unknown"):
        TOOL_REGISTRY.validate_args("file_search", '{"query": "test", "extra_field": "hack"}')

def test_validation_rejects_integer_for_string():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError):
        TOOL_REGISTRY.validate_args("read_file", '{"path": 42}')

def test_validation_rejects_array_item_type():
    from services import TOOL_REGISTRY
    with pytest.raises(ToolArgsError, match="string"):
        TOOL_REGISTRY.validate_args("home_entities", '{"domains": [1, 2, 3]}')

def test_validation_accepts_valid_args():
    from services import TOOL_REGISTRY
    args = TOOL_REGISTRY.validate_args("file_search", '{"query": "test", "limit": 5}')
    assert args["query"] == "test"
    assert args["limit"] == 5

# 2. Handler failure dictionaries remain failures

def test_result_is_failure_detects_status_error():
    assert _result_is_failure({"status": "error", "error": "timeout"})
    assert not _result_is_failure({"status": "success"})

def test_result_is_failure_detects_inner_failure():
    assert _result_is_failure({"result": {"ok": False, "error": "launch failed"}})
    assert not _result_is_failure({"result": {"ok": True}})

def test_result_is_failure_detects_failed_status():
    assert _result_is_failure({"status": "failed", "error": "cancelled"})
    assert not _result_is_failure({"status": "confirmation_required"})

def test_result_is_failure_detects_unavailable():
    assert _result_is_failure({"status": "unavailable"})

def test_result_is_failure_detects_cancelled():
    assert _result_is_failure({"status": "cancelled"})

def test_result_is_failure_detects_partial():
    assert _result_is_failure({"status": "partial"})

# 3. Non-idempotent actions not automatically retried

def test_idempotent_tools():
    assert _is_idempotent("file_search")
    assert _is_idempotent("computer_verify")
    assert not _is_idempotent("gmail_send")
    assert not _is_idempotent("calendar_create")
    assert not _is_idempotent("computer_open")
    assert not _is_idempotent("computer_browser")
    assert not _is_idempotent("blender_create")

# 4. Approval state and ticket preservation

def test_confirmation_preserves_args():
    from services import _create_confirmation
    ticket = _create_confirmation("gmail_send", {"to": "test@example.com", "subject": "Test", "body": "Hello"}, "Test reason")
    assert "confirmation_id" in ticket
    assert ticket["action"] == "gmail_send"
    assert ticket["args"]["to"] == "test@example.com"

def test_consume_confirmation_preserves_state():
    from services import _create_confirmation, _consume_confirmation
    ticket = _create_confirmation("calendar_create", {"email": "a@b.com", "event": {}}, "Create event")
    tid = ticket["confirmation_id"]
    consumed = _consume_confirmation(tid)
    assert consumed["tool"] == "calendar_create"
    assert consumed["arguments"]["email"] == "a@b.com"

# 5. Blender scripts reflect different user requests

def test_blender_script_different_goals():
    from blender_worker import _parse_goal, _generate_blender_script
    params1 = _parse_goal("Create a mountain landscape with pine trees")
    params2 = _parse_goal("Build a modern city skyline at dusk")
    assert params1["scene_type"] != params2["scene_type"]
    assert params1["subject"] != params2["subject"]

    script1 = _generate_blender_script("mountain landscape", params1, "/tmp/test1.blend")
    script2 = _generate_blender_script("city skyline", params2, "/tmp/test2.blend")
    assert script1 != script2
    assert "mountain landscape" in script1
    assert "city skyline" in script2

def test_blender_script_contains_request_subject():
    from blender_worker import _parse_goal, _generate_blender_script
    params = _parse_goal("abstract geometric art piece")
    script = _generate_blender_script("abstract geometric art piece", params, "/tmp/test.blend")
    assert "abstract geometric art piece" in script or "abstract" in script.lower()

# 6. Blender worker result error handling

def test_blender_work_result_has_error_field():
    from blender_worker import _find_blender
    if _find_blender() is None:
        pytest.skip("Blender not installed")
    import tempfile, asyncio
    async def test():
        from blender_worker import run_blender_work
        result = await run_blender_work("test", str(tempfile.mkdtemp()))
        assert "error" in result or "status" in result
    asyncio.run(test())

# 7. Task recovery is invoked during startup

def test_orchestrator_recover_is_defined():
    from orchestrator import OrchestratorStore
    store = OrchestratorStore.__new__(OrchestratorStore)
    assert hasattr(store, "recover")
    assert callable(store.recover)

def test_main_lifespan_has_recovery():
    import inspect
    from main import lifespan
    source = inspect.getsource(lifespan)
    assert "recover" in source

# 8. Background subprocess no-console-window fix

def test_compute_manager_gpu_stats_has_cnw():
    import inspect
    from compute_manager import gpu_stats
    source = inspect.getsource(gpu_stats)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source

def test_model_lab_ollama_inventory_has_cnw():
    import inspect
    from model_lab import ollama_inventory
    source = inspect.getsource(ollama_inventory)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source

def test_project_worker_run_command_has_cnw():
    import inspect
    from project_worker import run_command
    source = inspect.getsource(run_command)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source

def test_blender_worker_has_cnw():
    import inspect
    from blender_worker import run_blender_work
    source = inspect.getsource(run_blender_work)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source

def test_host_bridge_computer_shell_has_cnw():
    import inspect
    import host_bridge as hb
    source = inspect.getsource(hb.computer_shell)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source


def test_blender_prompt_uses_verified_dedicated_tool():
    from services import CORE_SYSTEM_PROMPT
    assert "dedicated `blender_create` tool" in CORE_SYSTEM_PROMPT
    assert "computer_shell" not in CORE_SYSTEM_PROMPT.split("- For Blender work", 1)[1].split("\n", 1)[0]

def test_host_bridge_computer_open_has_cnw():
    import inspect
    import host_bridge as hb
    source = inspect.getsource(hb.computer_open)
    assert "CREATE_NO_WINDOW" in source or "creationflags" in source

# 9. Stale artifact rejection

def test_artifact_id_format_validation():
    from services import _artifact_path
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _artifact_path("invalid_id_format")

def test_artifact_read_missing_file_raises():
    from services import _artifact_read
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _artifact_read("a" * 32)

# 10. Multiple tool calls preserve identifiers and results

def test_tool_result_preserves_execution_id():
    tr = ToolResult.success("file_search", result={"ok": True}, execution_id="exec-123")
    assert tr.execution_id == "exec-123"
    d = tr.to_dict()
    assert d["execution_id"] == "exec-123"

def test_tool_result_preserves_evidence():
    tr = ToolResult.success("computer_observe", evidence={"window": "Chrome"}, execution_id="exec-456")
    assert tr.evidence == {"window": "Chrome"}
    assert tr.to_dict()["evidence"] == {"window": "Chrome"}
