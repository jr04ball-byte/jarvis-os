"""V24 P2: projects routes (moved verbatim from main.py)."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

from deps import ARTIFACTS_DIR, db, limiter, orchestrator, rag
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import verify_workspace as project_verify_workspace
from schemas import (
    ArtifactPatch,
    ArtifactRequest,
    ChatMessage,
    ConversationCreate,
    ConversationMessage,
    DocumentUpload,
    OrchestratorAutopilotRequest,
    OrchestratorGoal,
    OrchestratorRunRequest,
    OrchestratorTransition,
    ProjectWorkerAutofixRequest,
)
from security import policy_snapshot as security_policy_snapshot
from services import (
    _artifact_path,
    _artifact_read,
    _run_agent_loop,
    _run_project_autofix_cycle,
    _update_confirmation_resume,
    apply_system_prompt,
    create_artifact,
    select_agent_model,
)
from workspace_registry import snapshot as workspace_snapshot

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/v1/artifacts")
async def artifacts_list(limit: int = 30):
    items=[]
    for path in sorted(ARTIFACTS_DIR.glob("*.json"), key=lambda x:x.stat().st_mtime, reverse=True)[:max(1,min(limit,100))]:
        try: items.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc: logger.debug("skipping unreadable artifact %s: %s", path, exc)
    return {"items":items}


@router.post("/v1/artifacts")
async def artifact_create(body: ArtifactRequest):
    return create_artifact(body.title, body.kind, body.content, body.metadata)


@router.get("/v1/artifacts/{artifact_id}")
async def artifact_get(artifact_id: str): return _artifact_read(artifact_id)


@router.patch("/v1/artifacts/{artifact_id}")
async def artifact_patch(artifact_id: str, body: ArtifactPatch):
    item=_artifact_read(artifact_id)
    patch=body.model_dump(exclude_none=True)
    item.update(patch); item["updated_at"]=datetime.now(timezone.utc).isoformat()
    _artifact_path(artifact_id).write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding="utf-8")
    return item


@router.delete("/v1/artifacts/{artifact_id}")
async def artifact_delete(artifact_id: str):
    path=_artifact_path(artifact_id)
    if not path.exists(): raise HTTPException(404,"artifact not found")
    path.unlink(); return {"ok":True,"id":artifact_id}


@router.get("/v1/orchestrator/policy")
async def orchestrator_policy():
    """Expose the action policy without exposing secrets."""
    return security_policy_snapshot()


@router.get("/v1/orchestrator/targets")
async def orchestrator_targets():
    """Return configured project workspaces without exposing credentials."""
    return {"targets": workspace_snapshot()}


@router.post("/v1/orchestrator/plan")
@limiter.limit("20/minute")
async def orchestrator_plan(request: Request, body: OrchestratorGoal):
    """Create a durable Goal -> Plan -> Tasks project."""
    try:
        return orchestrator.create_project(body.goal)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/v1/orchestrator/projects/{project_id}")
async def orchestrator_project(project_id: str):
    try:
        return orchestrator.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "project not found")


@router.get("/v1/orchestrator/projects/{project_id}/next")
async def orchestrator_next(project_id: str):
    try:
        task = orchestrator.next_task(project_id)
        return {"task": task}
    except Exception as exc:
        raise HTTPException(404, str(exc))


@router.post("/v1/orchestrator/transition")
@limiter.limit("60/minute")
async def orchestrator_transition(request: Request, body: OrchestratorTransition):
    try:
        return orchestrator.transition(body.task_id, body.status, body.result, body.error)
    except KeyError:
        raise HTTPException(404, "task not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/v1/orchestrator/projects/{project_id}/audit")
async def orchestrator_audit(project_id: str, limit: int = 100):
    try:
        orchestrator.get_project(project_id)
        return {"events": orchestrator.audit(project_id, limit)}
    except KeyError:
        raise HTTPException(404, "project not found")


@router.post("/v1/conversations")
async def create_conversation(conv: ConversationCreate):
    profile = conv.assistant_profile.lower() if conv.assistant_profile else "general"
    if profile not in {"general", "sales"}:
        raise HTTPException(status_code=400, detail="assistant_profile must be 'general' or 'sales'")
    conv_id = db.create_conversation(conv.title, conv.model, profile)
    return {"conversation_id": conv_id, "title": conv.title, "assistant_profile": profile}


@router.get("/v1/conversations")
async def list_conversations():
    return db.list_conversations()


@router.get("/v1/conversations/{conv_id}")
async def get_conversation(conv_id: int):
    if not db.conversation_exists(conv_id):
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} not found")
    messages = db.get_conversation(conv_id)
    return {"conversation_id": conv_id, "messages": messages}


@router.post("/v1/conversations/{conv_id}/messages")
async def add_conversation_message(conv_id: int, message: ConversationMessage):
    if not db.conversation_exists(conv_id):
        raise HTTPException(status_code=404, detail=f"conversation {conv_id} not found")
    db.add_message(conv_id, message.role, message.content)
    return {"status": "added"}


@router.post("/v1/documents")
async def upload_document(doc: DocumentUpload):
    """Upload a document to the RAG knowledge base"""
    try:
        rag.add_document(doc.content, doc.doc_id)
        return {"status": "added", "doc_id": doc.doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/documents/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """Upload a text file to RAG"""
    try:
        content = await file.read()
        text = content.decode('utf-8', errors='replace')
        doc_id = f"{file.filename}-{int(time.time())}"
        rag.add_document(text, doc_id)
        return {"status": "added", "doc_id": doc_id, "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/orchestrator/execute")
@limiter.limit("10/minute")
async def orchestrator_execute(request: Request, body: OrchestratorRunRequest):
    """Run a durable Jarvis plan through the existing agent/tool authority.

    Read-only planning/inspection/verification tasks can run autonomously. Any
    sensitive tool call still stops at the existing exact-action confirmation
    ticket, then resumes the same task after approval.
    """
    try:
        project = orchestrator.get_project(body.project_id)
    except KeyError:
        raise HTTPException(404, "project not found")

    completed = []
    for _ in range(body.max_steps):
        task = orchestrator.next_task(body.project_id)
        if not task:
            return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "waiting_or_complete"}
        try:
            orchestrator.transition(task["id"], "running")
            project = orchestrator.get_project(body.project_id)
            # Target context is stored inside the durable plan so the worker
            # always resumes against the same workspace after a restart.
            target = (project.get("plan") or {}).get("target") or {}
            workspace = target.get("workspace") or {}
            workspace_text = json.dumps(workspace, default=str) if workspace else "No specific workspace target was detected."

            if task["kind"] == "workspace_inspect" and workspace:
                try:
                    evidence = await asyncio.to_thread(project_inspect_workspace, workspace.get("tool_path") or workspace.get("path"))
                except FileNotFoundError as exc:
                    orchestrator.transition(task["id"], "blocked", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "error": str(exc)}
                result = {"ok": True, "handled_by": "project_worker", "evidence": evidence}
                orchestrator.transition(task["id"], "completed", result=result)
                completed.append(task["id"])
                continue

            if task["kind"] == "plan_actions":
                result = {
                    "ok": True,
                    "handled_by": "orchestrator",
                    "task": task["kind"],
                    "execution_boundary": "registered_workspace_project_worker_or_existing_agent_confirmation_gate",
                    "target": target.get("target"),
                    "workspace": workspace,
                }
                orchestrator.transition(task["id"], "completed", result=result)
                completed.append(task["id"])
                continue

            if task["kind"] == "execute" and target.get("target") and workspace:
                try:
                    autofix = await _run_project_autofix_cycle(ProjectWorkerAutofixRequest(
                        target=target["target"], goal=project["goal"], max_retries=2, run_lint=False
                    ))
                    status = autofix.get("status")
                    if status == "verified":
                        orchestrator.transition(task["id"], "completed", result={"handled_by": "self_correcting_project_worker", **autofix})
                        completed.append(task["id"])
                        continue
                    block_reason = autofix.get("reason") or status or "project worker did not verify"
                    orchestrator.transition(task["id"], "blocked", result=autofix, error=str(block_reason))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": status or "blocked", "worker": autofix}
                except Exception as exc:
                    logger.exception("project-worker execute task failed")
                    orchestrator.transition(task["id"], "failed", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "failed", "error": str(exc)}

            if task["kind"] == "verify" and target.get("target") and workspace:
                worker_path = workspace.get("tool_path") or workspace.get("path")
                try:
                    verification = await asyncio.to_thread(project_verify_workspace, worker_path, run_tests=True, run_build=True, run_lint=False)
                except FileNotFoundError as exc:
                    orchestrator.transition(task["id"], "blocked", error=str(exc))
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "error": str(exc)}
                if not verification.get("ok"):
                    orchestrator.transition(task["id"], "blocked", result=verification, error=f"verification status: {verification.get('status')}")
                    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "verification": verification}
                orchestrator.transition(task["id"], "completed", result=verification)
                completed.append(task["id"])
                continue

            read_only = task["kind"] in {"reason", "inspect", "workspace_inspect", "verify"}
            if read_only:
                instruction = {
                    "reason": "Analyze the goal and produce a concise understanding. Do not modify files, send messages, change devices, or take external actions.",
                    "inspect": "Inspect available context using read-only tools only. Do not modify anything. Report useful files, services, configuration, blockers, and missing access.",
                    "workspace_inspect": "Inspect the target workspace using read-only tools only. Check its structure, README/configuration, tests, and obvious blockers. Do not modify anything.",
                    "verify": "Verify the work completed in this project using read-only checks. Do not modify anything. Start your response with VERIFIED: YES if the goal appears satisfied, otherwise VERIFIED: NO and list blockers.",
                }[task["kind"]]
            else:
                instruction = "Execute the current task toward the project goal. Use available tools when appropriate. Sensitive actions must go through the existing confirmation mechanism. Do not claim an action happened unless the tool returned success."

            prompt = f"""You are the execution worker inside Jarvis Autopilot.

Project goal: {project['goal']}
Target: {target.get('target') or 'general'}
Workspace context: {workspace_text}
Current task: {task['title']}
Task capability: {task.get('capability', task['kind'])}

Instruction: {instruction}

Rules:
- Preserve the user's exact goal; do not invent requirements.
- For read-only tasks, absolutely no writes or external side effects.
- For execute tasks, use the existing tool router and confirmation gates.
- Report concrete evidence, blockers, and required approvals.
"""
            messages = [m.model_dump() for m in apply_system_prompt([ChatMessage(role="user", content=prompt)], body.assistant_profile)]
            selected = select_agent_model(body.model, messages, body.assistant_profile)
            result = await _run_agent_loop(selected, messages, body.assistant_profile, None, 8)
            if result.get("confirmation"):
                ticket = result["confirmation"].get("confirmation_id")
                if ticket:
                    _update_confirmation_resume(ticket, project_id=body.project_id, task_id=task["id"])
                orchestrator.transition(task["id"], "awaiting_approval", result=result)
                return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "awaiting_approval", "result": result}

            answer = ((result.get("message") or {}).get("content") or "")
            if task["kind"] == "verify" and re.search(r"VERIFIED\s*:\s*NO", answer, re.IGNORECASE):
                orchestrator.transition(task["id"], "blocked", result=result, error=answer[:2000])
                return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "blocked", "result": result}
            orchestrator.transition(task["id"], "completed", result=result)
            completed.append(task["id"])
        except Exception as exc:
            logger.exception("orchestrator task failed")
            orchestrator.transition(task["id"], "failed", error=str(exc))
            raise
    return {"project": orchestrator.get_project(body.project_id), "completed_this_run": completed, "status": "step_limit"}


@router.post("/v1/orchestrator/autopilot")
@limiter.limit("10/minute")
async def orchestrator_autopilot(request: Request, body: OrchestratorAutopilotRequest):
    """Create a project and immediately run its safe steps."""
    if body.assistant_profile.lower() not in {"general", "sales"}:
        raise HTTPException(400, "assistant_profile must be 'general' or 'sales'")
    project = orchestrator.create_project(body.goal)
    result = await orchestrator_execute(request, OrchestratorRunRequest(
        project_id=project["id"], max_steps=body.max_steps,
        model=body.model, assistant_profile=body.assistant_profile,
    ))
    result["created_project_id"] = project["id"]
    return result
