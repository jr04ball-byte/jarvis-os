"""V24 P2: workers routes (moved verbatim from main.py)."""
import asyncio
import json
import logging
import time

import local_tools
from brains import providers as intelligence_providers
from deps import limiter, orchestrator, project_worker_runs
from fastapi import APIRouter, HTTPException, Request
from project_worker import capture_git_diff as project_capture_diff
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import make_worker_prompt
from project_worker import project_health as project_worker_health_snapshot
from project_worker import verify_workspace as project_verify_workspace
from providers import ProviderMessage
from schemas import (
    AgentChatRequest,
    ConfirmationRequest,
    OpenCodeTaskRequest,
    ProjectWorkerAutofixRequest,
    ProjectWorkerImplementRequest,
    ProjectWorkerTargetRequest,
    ProjectWorkerVerifyRequest,
    ResearchRequest,
    ToolRequest,
)
from services import (
    _PENDING_ACTIONS,
    _PENDING_LOCK,
    _PENDING_TTL_SECONDS,
    _consume_confirmation,
    _project_worker_target,
    _run_agent_loop,
    _run_project_autofix_cycle,
    apply_system_prompt,
    build_tools_list,
    execute_tool_core,
    select_agent_model,
    web_search,
)
from workspace_registry import get_target as workspace_target
from workspace_registry import is_registered_workspace, target_for_workspace

import tools

logger = logging.getLogger(__name__)


router = APIRouter()


@router.post("/v1/opencode/task")
@limiter.limit("10/minute")
async def run_opencode_task(request: Request, body: OpenCodeTaskRequest):
    """Read-only OpenCode relay scoped to an exact registered workspace.

    V23 deliberately removes direct unverified code-writing from this legacy
    endpoint. Code changes must flow through Project Worker/Autopilot so they
    receive baseline capture, verification, retry bounds, and audit evidence.
    """
    if body.mode != "inspect":
        raise HTTPException(409, "direct OpenCode writes are disabled; use /v1/project-worker/autofix or Autopilot")

    workspace = ""
    resolved_target = (body.target or "").strip()
    if resolved_target:
        try:
            target = workspace_target(resolved_target)
        except KeyError:
            raise HTTPException(404, "unknown OpenCode target")
        workspace = (target.get("workspace") or {}).get("tool_path") or (target.get("workspace") or {}).get("path") or ""
    elif body.path:
        workspace = str(body.path).strip()
        resolved_target = target_for_workspace(workspace) or ""
    if not workspace or not is_registered_workspace(workspace):
        raise HTTPException(403, "OpenCode may only inspect an exact workspace registered with Jarvis")

    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")
    try:
        result = await worker.complete(
            [ProviderMessage(role="user", content=body.prompt)],
            temperature=0.1, max_tokens=4096,
            task_context={
                "workspace": workspace,
                "permission": "read_only",
                "target": resolved_target or None,
                "mode": "inspect",
            },
        )
    except Exception as exc:
        logger.exception("read-only OpenCode task failed")
        raise HTTPException(502, f"OpenCode worker failed: {exc}")
    return {
        "status": "ok",
        "mode": "inspect",
        "target": resolved_target or None,
        "provider": result.provider,
        "model": result.model,
        "content": result.content,
        "metadata": result.metadata,
    }


@router.post("/v1/research/search")
async def research_search(body: ResearchRequest):
    return await web_search(body.query, body.num_results)


@router.get("/v1/agent/pending")
async def agent_pending():
    """List redacted pending approvals for the dashboard; arguments are never returned."""
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k,v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for k in expired:
            _PENDING_ACTIONS.pop(k, None)
        return {"items": [{"confirmation_id": k, "action": v["tool"], "reason": v["reason"], "created_at": v["created_at"], "expires_at": v["created_at"] + _PENDING_TTL_SECONDS} for k,v in _PENDING_ACTIONS.items()]}


@router.get("/v1/tools/local")
async def local_tools_inventory():
    """Discover local applications without granting arbitrary process execution."""
    return local_tools.scan_local_tools()


@router.get("/v1/tools")
async def list_tools():
    return {"tools": build_tools_list()}


@router.post("/v1/tools/execute")
async def execute_tool(req: ToolRequest):
    return await execute_tool_core(req.tool, req.arguments, req.confirmed)


@router.post("/v1/project-worker/inspect")
@limiter.limit("20/minute")
async def project_worker_inspect(request: Request, body: ProjectWorkerTargetRequest):
    target = _project_worker_target(body.target)
    try:
        evidence = await asyncio.to_thread(project_inspect_workspace, target["resolved_workspace"])
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "evidence": evidence}


@router.post("/v1/project-worker/health")
@limiter.limit("20/minute")
async def project_worker_health(request: Request, body: ProjectWorkerTargetRequest):
    target = _project_worker_target(body.target)
    try:
        health = await asyncio.to_thread(project_worker_health_snapshot, target["resolved_workspace"])
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "health": health}


@router.post("/v1/project-worker/verify")
@limiter.limit("10/minute")
async def project_worker_verify(request: Request, body: ProjectWorkerVerifyRequest):
    target = _project_worker_target(body.target)
    try:
        result = await asyncio.to_thread(
            project_verify_workspace, target["resolved_workspace"],
            run_tests=body.run_tests, run_build=body.run_build, run_lint=body.run_lint
        )
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    return {"target": target["target"], "workspace": target["workspace"], "verification": result}


@router.post("/v1/project-worker/implement")
@limiter.limit("5/minute")
async def project_worker_implement(request: Request, body: ProjectWorkerImplementRequest):
    """Run one bounded OpenCode implementation cycle inside a registered workspace."""
    target = _project_worker_target(body.target)
    workspace = target["resolved_workspace"]
    try:
        before = await asyncio.to_thread(project_inspect_workspace, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))

    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")
    prompt = make_worker_prompt(body.goal, workspace, before, permission="workspace_write")
    started = time.time()
    try:
        result = await worker.complete(
            [ProviderMessage(role="user", content=prompt)],
            temperature=0.2, max_tokens=4096,
            task_context={"workspace": workspace, "permission": "workspace_write", "goal": body.goal},
        )
    except Exception as exc:
        logger.exception("project worker implementation failed")
        raise HTTPException(502, f"OpenCode worker failed: {exc}")
    elapsed_ms = int((time.time() - started) * 1000)
    diff = await asyncio.to_thread(project_capture_diff, workspace)
    verification = None
    if body.verify:
        verification = await asyncio.to_thread(project_verify_workspace, workspace, run_tests=True, run_build=True, run_lint=False)
    return {
        "target": target["target"], "workspace": target["workspace"], "goal": body.goal,
        "worker": {"provider": result.provider, "model": result.model, "content": result.content, "metadata": result.metadata},
        "diff": diff, "verification": verification, "elapsed_ms": elapsed_ms,
        "status": "verified" if verification and verification.get("ok") else ("implemented_unverified" if not body.verify else "needs_attention"),
    }


@router.get("/v1/project-worker/runs/{run_id}")
async def project_worker_run(run_id: str):
    try:
        return project_worker_runs.get(run_id)
    except KeyError:
        raise HTTPException(404, "project-worker run not found")


@router.post("/v1/project-worker/autofix")
@limiter.limit("3/minute")
async def project_worker_autofix(request: Request, body: ProjectWorkerAutofixRequest):
    """Bounded diagnose -> implement -> verify -> repair loop with durable run state."""
    return await _run_project_autofix_cycle(body)


@router.get("/v1/computer/capabilities")
async def computer_capabilities_route():
    return await tools.computer_capabilities()


@router.get("/v1/computer/screenshot")
async def computer_screenshot_route():
    return await tools.computer_screenshot()


@router.get("/v1/computer/processes")
async def computer_processes_route():
    return await tools.computer_processes()


@router.get("/v1/computer/windows")
async def computer_windows_route():
    return await tools.computer_windows()


@router.get("/v1/computer/observe")
async def computer_observe_route():
    return await tools.computer_observe()


@router.post("/v1/agent/chat")
@limiter.limit("20/minute")
async def agent_chat(request: Request, body: AgentChatRequest):
    """Multi-step local agent. Read-only tools run immediately; sensitive actions pause for approval."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    if any(m.role == "system" for m in body.messages):
        raise HTTPException(400, "system messages are not accepted by the agent endpoint")
    messages = [m.model_dump() for m in apply_system_prompt(list(body.messages), body.assistant_profile)]
    selected_model = select_agent_model(body.model, messages, body.assistant_profile)
    return await _run_agent_loop(selected_model, messages, body.assistant_profile,
                                 body.conversation_id, body.max_tool_rounds)


@router.post("/v1/agent/confirm")
@limiter.limit("30/minute")
async def agent_confirm(request: Request, body: ConfirmationRequest):
    """Approve one exact pending action and resume its original multi-step task."""
    if not body.confirmed:
        with _PENDING_LOCK:
            _PENDING_ACTIONS.pop(body.confirmation_id, None)
        return {"status": "cancelled"}

    item = _consume_confirmation(body.confirmation_id)
    result = await execute_tool(ToolRequest(tool=item["tool"], arguments=item["arguments"], confirmed=True))
    resume = item.get("resume")
    if not resume:
        return result

    messages = list(resume["messages"])
    messages.append({"role": "tool", "content": json.dumps(result, default=str)})
    # Resume from the exact point at which approval interrupted the task.
    resumed_result = await _run_agent_loop(
        resume["model"], messages, resume["assistant_profile"],
        resume.get("conversation_id"), resume["max_tool_rounds"],
        initial_tool_result=None,
    )
    project_id = resume.get("project_id")
    task_id = resume.get("task_id")
    if project_id and task_id:
        if resumed_result.get("confirmation"):
            orchestrator.transition(task_id, "awaiting_approval", result=resumed_result)
        else:
            orchestrator.transition(task_id, "completed", result=resumed_result)
        resumed_result["project"] = orchestrator.get_project(project_id)
    return resumed_result
