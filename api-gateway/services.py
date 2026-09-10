"""V24 P2: service layer — shared business logic for route handlers.

Helpers, selection/routing logic, confirmation workflow, connection snapshots,
artifact helpers, status snapshots, the agent loop, and the autofix cycle.
Route modules import from here; services never import routes or main.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

import google_oauth
import httpx
import local_tools
from brains import providers as intelligence_providers
from deps import (
    _COMPUTE_CACHE,
    _PROJECT_HEALTH_CACHE,
    AI_API_TOKEN,
    AI_REQUIRE_AUTH,
    ARTIFACTS_DIR,
    CONNECTIONS_PATH,
    DEEPGRAM_API_KEY,
    EXA_API_KEY,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    JARVIS_COMPUTE_MODE,
    JARVIS_MODEL_LOCK,
    OLLAMA_URL,
    ORCHESTRATOR_DB,
    db,
    project_worker_runs,
)
from fastapi import HTTPException
from project_worker import capture_workspace_baseline as project_capture_baseline
from project_worker import classify_verification_failure as project_classify_failure
from project_worker import compare_workspace_baseline as project_compare_baseline
from project_worker import inspect_workspace as project_inspect_workspace
from project_worker import make_repair_prompt, make_worker_prompt
from project_worker import project_health as project_worker_health_snapshot
from project_worker import verify_workspace as project_verify_workspace
from providers import ProviderMessage
from schemas import (
    DEEP_MODEL,
    FAST_MODEL,
    TOOL_MODEL,
    ChatMessage,
    ChatRequest,
    ProjectWorkerAutofixRequest,
)
from workspace_registry import get_target as workspace_target

import tools

logger = logging.getLogger(__name__)


_PENDING_LOCK = threading.Lock()


_PENDING_ACTIONS: dict[str, dict[str, Any]] = {}


_PENDING_TTL_SECONDS = 600


def _create_confirmation(tool_name: str, arguments: dict, reason: str) -> dict:
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k,v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for k in expired:
            _PENDING_ACTIONS.pop(k, None)
        _PENDING_ACTIONS[ticket] = {"tool": tool_name, "arguments": dict(arguments), "reason": reason, "created_at": now}
    return {"status":"confirmation_required","confirmation_id":ticket,"action":tool_name,"args":dict(arguments),"reason":reason,"expires_in":_PENDING_TTL_SECONDS}


def _consume_confirmation(ticket: str) -> dict:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.pop(ticket, None)
    if not item:
        raise HTTPException(404, "confirmation expired or not found")
    if time.time() - item["created_at"] > _PENDING_TTL_SECONDS:
        raise HTTPException(410, "confirmation expired")
    return item


_DEEP_HINTS = re.compile(r"\b(code|coding|debug|debugging|program|programming|architect|architecture|algorithm|algorithms|refactor|repository|repo|sql|python|javascript|typescript|powershell|bash|docker|api design|system design|reasoning|analyze|analysis|deep|complex|math|prove|proof|research|security|threat model|consensus|raft|paxos|quorum|replicat\w+|leader\s+election|fault\s+toleran\w+|distributed\s+(systems?|databases?|computing|architecture|consensus)|database\s+architecture|consistenc\w*)\b", re.IGNORECASE)


def select_model(requested: str, messages: list | None = None, assistant_profile: str = "general") -> str:
    """Resolve an explicit model or automatically choose fast vs deep local model."""
    requested = (requested or "auto").strip()
    if requested and requested.lower() not in {"auto", "default"}:
        return requested
    # Never route on injected system prompts: they contain deep-hint words
    # (e.g. "code") that would otherwise force every request to deep.
    convo = [m for m in (messages or []) if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) != "system"]
    text = " ".join(str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")) for m in convo)
    profile = (assistant_profile or "general").lower()
    if profile == "sales":
        return FAST_MODEL
    return DEEP_MODEL if _DEEP_HINTS.search(text) else FAST_MODEL


def select_agent_model(requested: str, messages: list | None = None, assistant_profile: str = "general") -> str:
    """Select a model for the tool-calling agent. Gemma 3 4B does not support
    Ollama tool calling, so automatic agent requests must use a tool-capable model."""
    requested = (requested or "auto").strip()
    if requested.lower() in {"auto", "default"}:
        return TOOL_MODEL
    if requested == FAST_MODEL:
        return TOOL_MODEL
    return requested


def system_prompt_for(profile: str) -> str:
    return SALES_SYSTEM_PROMPT if profile.lower() == "sales" else CORE_SYSTEM_PROMPT


def apply_system_prompt(messages: list[ChatMessage], profile: str) -> list[ChatMessage]:
    """Guarantee the core behavior is present without duplicating it on every request."""
    system = system_prompt_for(profile)
    if messages and messages[0].role == "system":
        return [ChatMessage(role="system", content=system + "\n\nAdditional application instructions:\n" + messages[0].content)] + messages[1:]
    return [ChatMessage(role="system", content=system)] + list(messages)


CONNECTION_CATALOG = [
    {"id":"google","name":"Google Workspace","category":"accounts","kind":"oauth","description":"Gmail, Calendar and future Google services","requires_confirmation":False},
    {"id":"home_assistant","name":"Home Assistant","category":"home","kind":"service","description":"Lights, switches, climate, fans and TV/media","requires_confirmation":True},
    {"id":"host_bridge","name":"Windows Host Bridge","category":"computer","kind":"service","description":"Open approved local files through the host bridge","requires_confirmation":False},
    {"id":"ollama","name":"Ollama","category":"ai","kind":"local","description":"Local LLM runtime and models","requires_confirmation":False},
    {"id":"whisper","name":"Local Whisper Voice","category":"ai","kind":"browser","description":"Browser-local speech recognition using WASM/fp32","requires_confirmation":False},
    {"id":"deepgram","name":"Deepgram Voice","category":"ai","kind":"cloud_voice","description":"Deepgram Flux speech recognition and Aura speech synthesis","requires_confirmation":False},
    {"id":"exa","name":"Optional Web Research","category":"ai","kind":"cloud_research","description":"Optional Exa web research; disabled unless EXA_API_KEY is configured","requires_confirmation":False},
    {"id":"comfyui","name":"ComfyUI","category":"creative","kind":"local_tool","description":"Local image generation and workflow execution","requires_confirmation":True},
    {"id":"blender","name":"Blender","category":"creative","kind":"local_tool","description":"3D projects, scenes, animation and rendering","requires_confirmation":True},
    {"id":"unreal","name":"Unreal Engine 5","category":"creative","kind":"local_tool","description":"Projects, levels, Sequencer and rendering","requires_confirmation":True},
    {"id":"ffmpeg","name":"FFmpeg","category":"creative","kind":"local_tool","description":"Video/audio trim, concat, transcode and captions","requires_confirmation":True},
    {"id":"python","name":"Python","category":"computer","kind":"local_tool","description":"Local automation and scripts","requires_confirmation":True},
    {"id":"iphone","name":"iPhone Companion","category":"devices","kind":"companion","description":"PWA companion for voice and AI control","requires_confirmation":False},
    {"id":"sales_machine","name":"Sales Machine","category":"business","kind":"service","description":"Separate sales workspace/service","requires_confirmation":True},
    {"id":"email_agent","name":"Email Agent","category":"business","kind":"service","description":"Separate email automation and invoice service","requires_confirmation":True},
]


def _load_connection_overrides() -> dict:
    if not CONNECTIONS_PATH.exists(): return {}
    try: return json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
    except Exception: return {}


def _save_connection_overrides(data: dict) -> None:
    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _connection_status(c: dict, local: dict, google: dict) -> dict:
    cid=c["id"]; status="available"; detail="Not connected"
    if cid=="google":
        accounts=google.get("accounts",[]); status="connected" if accounts else ("available" if google.get("configured") else "not_configured")
        detail=(accounts[0].get("email") if accounts else ("OAuth configured" if google.get("configured") else "Add Google OAuth credentials"))
    elif cid=="home_assistant":
        status="connected" if tools.HA_URL and tools.HA_TOKEN else "not_configured"; detail="Home Assistant API" if status=="connected" else "Set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN"
    elif cid=="host_bridge":
        status="configured" if tools.HOST_BRIDGE_URL and tools.HOST_BRIDGE_TOKEN else "not_configured"; detail=tools.HOST_BRIDGE_URL if status=="configured" else "Set host bridge URL/token"
    elif cid=="ollama":
        status="connected" if local.get("ollama_online") else "unreachable"; detail=local.get("ollama_model") or "Native Ollama"
    elif cid=="whisper":
        status="ready"; detail="WASM / fp32 browser speech"
    elif cid=="deepgram":
        status="connected" if DEEPGRAM_API_KEY else "not_configured"; detail=("Flux STT + Aura TTS" if DEEPGRAM_API_KEY else "Set DEEPGRAM_API_KEY")
    elif cid=="gemini":
        status="connected" if GEMINI_API_KEY else "not_configured"; detail=(f"{GEMINI_MODEL} • Google Cloud" if GEMINI_API_KEY else "Set GEMINI_API_KEY")
    elif cid=="exa":
        status="connected" if EXA_API_KEY else "not_configured"; detail=("Optional web research" if EXA_API_KEY else "Local-first: no web key configured")
    elif cid in {"comfyui","blender","unreal","ffmpeg","python"}:
        by_id={x.get("id"):x for x in local.get("tools",[])}
        key={"comfyui":"comfyui","blender":"blender","unreal":"unreal_engine_5","ffmpeg":"ffmpeg","python":"python"}[cid]
        item=by_id.get(key,{})
        status="installed" if item.get("installed") else "missing"; detail=item.get("version") or ("Detected" if item.get("installed") else "Install tool to enable")
    elif cid=="iphone": status="available"; detail="/companion"
    elif cid in {"sales_machine","email_agent"}:
        env={"sales_machine":"SALES_MACHINE_URL","email_agent":"EMAIL_AGENT_URL"}[cid]; url=os.getenv(env,"").strip()
        status="configured" if url else "not_configured"; detail=url or f"Set {env} to connect"
    return {**c,"status":status,"detail":detail}


async def _connection_snapshot() -> dict:
    local=local_tools.scan_local_tools()
    ollama_online=False; ollama_model=None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r=await client.get(f"{OLLAMA_URL}/api/tags")
            ollama_online=r.status_code==200
            if ollama_online:
                models=r.json().get("models") or []
                ollama_model=models[0].get("name") if models else None
    except Exception as exc: logger.debug("ollama status probe failed: %s", exc)
    local["ollama_online"]=ollama_online; local["ollama_model"]=ollama_model
    gstore=google_oauth.TokenStore(); ga=[]
    for email in gstore.list_accounts():
        tok=gstore.load_tokens(email) or {}; ga.append({"email":email,"scopes":tok.get("scope","")})
    google={"configured":bool(google_oauth.CLIENT_ID),"accounts":ga}
    overrides=_load_connection_overrides()
    items=[]
    for c in CONNECTION_CATALOG:
        item=_connection_status(c,local,google)
        ov=overrides.get(c["id"],{})
        if ov.get("enabled") is False and item["status"] not in {"not_configured","missing"}: item["status"]="disabled"
        item["enabled"]=ov.get("enabled", True)
        items.append(item)
    return {"connections":items,"categories":["accounts","home","ai","creative","computer","devices","business"]}


def _artifact_path(artifact_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
        raise HTTPException(400, "invalid artifact id")
    return ARTIFACTS_DIR / f"{artifact_id}.json"


def _artifact_read(artifact_id: str) -> dict[str, Any]:
    path = _artifact_path(artifact_id)
    if not path.exists(): raise HTTPException(404, "artifact not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _pending_snapshot() -> dict[str, Any]:
    now = time.time()
    with _PENDING_LOCK:
        expired = [k for k, v in _PENDING_ACTIONS.items() if now - v["created_at"] > _PENDING_TTL_SECONDS]
        for key in expired:
            _PENDING_ACTIONS.pop(key, None)
        items = [
            {
                "confirmation_id": key, "action": value["tool"], "reason": value["reason"],
                "created_at": value["created_at"], "expires_at": value["created_at"] + _PENDING_TTL_SECONDS,
            }
            for key, value in _PENDING_ACTIONS.items()
        ]
    return {"count": len(items), "items": items}


def _configuration_snapshot() -> dict[str, Any]:
    data_dir = ORCHESTRATOR_DB.parent
    warnings: list[str] = []
    if AI_REQUIRE_AUTH and not AI_API_TOKEN:
        warnings.append("AI_REQUIRE_AUTH is enabled but AI_API_TOKEN is empty")
    if not AI_REQUIRE_AUTH:
        warnings.append("API authentication is disabled; keep Jarvis bound to trusted local/LAN interfaces only")
    if not GEMINI_API_KEY:
        warnings.append("Gemini main brain is not configured")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        warnings.append("OpenAI deep-reasoning provider is not configured")
    try:
        writable = data_dir.exists() and os.access(data_dir, os.W_OK)
    except Exception:
        writable = False
    if not writable:
        warnings.append("Jarvis data directory is not writable")
    return {
        "auth_required": AI_REQUIRE_AUTH, "data_directory_writable": writable,
        "gemini_configured": bool(GEMINI_API_KEY),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "deepgram_configured": bool(DEEPGRAM_API_KEY),
        "warnings": warnings,
    }


async def _cached_compute_snapshot(ttl: float = 5.0) -> dict[str, Any]:
    global _COMPUTE_CACHE
    now = time.time()
    if _COMPUTE_CACHE and now - _COMPUTE_CACHE[0] < ttl:
        return _COMPUTE_CACHE[1]
    value = await compute_snapshot()
    _COMPUTE_CACHE = (now, value)
    return value


async def _cached_target_health(target: dict[str, Any], ttl: float = 8.0) -> dict[str, Any]:
    target_id = str(target.get("id") or "")
    now = time.time()
    cached = _PROJECT_HEALTH_CACHE.get(target_id)
    if cached and now - cached[0] < ttl:
        return cached[1]
    health: dict[str, Any] = {"available": False, "status": "not_mounted"}
    if target.get("path_exists") and target.get("path_is_directory"):
        try:
            resolved = _project_worker_target(target_id)["resolved_workspace"]
            h = await asyncio.wait_for(asyncio.to_thread(project_worker_health_snapshot, resolved), timeout=5.0)
            health = {"available": True, "status": "ready" if h.get("ready_for_worker") else "blocked", **h}
        except asyncio.TimeoutError:
            health = {"available": True, "status": "timeout", "blockers": ["project health check timed out"]}
        except Exception as exc:
            health = {"available": True, "status": "error", "blockers": [str(exc)[:500]]}
    _PROJECT_HEALTH_CACHE[target_id] = (now, health)
    return health


def _adaptive_options() -> dict:
    """Choose GPU-first, CPU fallback, or explicit hybrid inference."""
    selected = select_mode(JARVIS_COMPUTE_MODE)
    options = {"temperature": 0.7, "num_predict": 1024}
    options.update(selected.get("options", {}))
    return options


def _project_worker_target(target_id: str) -> dict:
    try:
        target = workspace_target(target_id)
    except KeyError:
        raise HTTPException(404, "unknown project-worker target")
    workspace = (target.get("workspace") or {}).get("tool_path") or (target.get("workspace") or {}).get("path")
    if not workspace:
        raise HTTPException(409, "target has no workspace configured")
    return {**target, "resolved_workspace": workspace}


async def _run_project_autofix_cycle(body: ProjectWorkerAutofixRequest) -> dict:
    """Internal V22.2 worker loop used by both the API and Autopilot."""
    target = _project_worker_target(body.target)
    workspace = target["resolved_workspace"]
    worker = intelligence_providers.get("opencode")
    if worker is None or not worker.configured:
        raise HTTPException(503, "OpenCode worker is not configured")

    # Gather richer read-only health evidence before allowing code edits.
    try:
        health = await asyncio.to_thread(project_worker_health_snapshot, workspace)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))
    if not health.get("ready_for_worker", True):
        return {"status": "blocked_environment", "health": health, "reason": "project environment is not ready for worker execution"}

    try:
        if body.resume_run_id:
            run = project_worker_runs.get(body.resume_run_id)
            if run["target"] != body.target or run["workspace"] != workspace or run["goal"] != body.goal:
                raise HTTPException(409, "resume run does not match target/workspace/goal")
            if run["status"] == "verified":
                return {"run": run, "status": "verified", "resumed": True, "health": health}
            baseline = run["baseline"]
        else:
            baseline = await asyncio.to_thread(project_capture_baseline, workspace)
            run = project_worker_runs.create(body.target, workspace, body.goal, baseline)
    except KeyError:
        raise HTTPException(404, "resume project-worker run not found")
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc))

    attempts_already = len(run.get("attempts") or [])
    total_allowed = 1 + body.max_retries
    if attempts_already >= total_allowed:
        final = project_worker_runs.update(run["id"], status="blocked")
        return {"run": final, "status": "blocked", "reason": "retry budget exhausted", "health": health}

    last_verification = (run.get("attempts") or [{}])[-1].get("verification") if run.get("attempts") else None
    for attempt_no in range(attempts_already + 1, total_allowed + 1):
        inspection = await asyncio.to_thread(project_inspect_workspace, workspace)
        if attempt_no == 1 and not last_verification:
            prompt = make_worker_prompt(body.goal, workspace, inspection, permission="workspace_write", baseline=baseline)
            phase = "implement"
        else:
            current_delta = await asyncio.to_thread(project_compare_baseline, workspace, baseline)
            prompt = make_repair_prompt(body.goal, workspace, last_verification or {}, current_delta, attempt_no)
            phase = "repair"

        started = time.time()
        try:
            result = await worker.complete(
                [ProviderMessage(role="user", content=prompt)],
                temperature=0.15, max_tokens=4096,
                task_context={
                    "workspace": workspace, "permission": "workspace_write", "goal": body.goal,
                    "run_id": run["id"], "attempt": attempt_no, "phase": phase,
                },
            )
        except Exception as exc:
            logger.exception("project worker autofix attempt failed")
            attempt = {
                "attempt": attempt_no, "phase": phase, "ok": False, "error": str(exc),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
            run = project_worker_runs.update(run["id"], status="failed", attempt=attempt)
            return {"run": run, "status": "failed", "error": str(exc), "health": health}

        delta = await asyncio.to_thread(project_compare_baseline, workspace, baseline)
        changed_paths = delta.get("changed_by_worker") or []
        verification = await asyncio.to_thread(
            project_verify_workspace, workspace, run_tests=True, run_build=True, run_lint=body.run_lint,
            changed_paths=changed_paths,
        )
        classification = verification.get("failure_classification") or project_classify_failure(verification)
        attempt = {
            "attempt": attempt_no, "phase": phase,
            "worker": {"provider": result.provider, "model": result.model, "content": result.content, "metadata": result.metadata},
            "delta": delta, "verification": verification, "failure_classification": classification,
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        if verification.get("ok"):
            run = project_worker_runs.update(run["id"], status="verified", attempt=attempt)
            return {"run": run, "status": "verified", "attempts_used": attempt_no, "health": health}

        # V22.2: do not burn repair attempts trying to fix missing runtimes, permissions or network.
        if classification.get("kind") == "environment":
            run = project_worker_runs.update(run["id"], status="blocked_environment", attempt=attempt)
            return {
                "run": run, "status": "blocked_environment", "attempts_used": attempt_no,
                "failure_classification": classification, "health": health,
            }

        last_verification = verification
        run = project_worker_runs.update(run["id"], status="repairing" if attempt_no < total_allowed else "blocked", attempt=attempt)

    return {"run": run, "status": run["status"], "reason": "verification failed after bounded retries", "health": health}


async def stream_chat(request: ChatRequest):
    """Stream Ollama output and persist the assistant reply after completion."""
    accumulated: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            ollama_request = {
                "model": request.model,
                "messages": [{"role": msg.role, "content": msg.content} for msg in request.messages],
                "stream": True,
                "think": False,
                "options": {**_adaptive_options(), "temperature": request.temperature, "num_predict": request.max_tokens or 1024}
            }

            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/api/chat",
                json=ollama_request
            ) as response:
                if response.status_code != 200:
                    error_body = (await response.aread()).decode("utf-8", errors="replace")
                    yield f"data: {json.dumps({'error': f'Ollama returned HTTP {response.status_code}: {error_body}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if "message" in data:
                        content = data["message"].get("content", "")
                        if content:
                            accumulated.append(content)
                            chunk = {
                                "id": f"chatcmpl-{int(time.time())}",
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": request.model,
                                "choices": [{
                                    "delta": {"content": content},
                                    "index": 0,
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"

                    if data.get("done", False):
                        final_chunk = {
                            "id": f"chatcmpl-{int(time.time())}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": request.model,
                            "choices": [{
                                "delta": {},
                                "index": 0,
                                "finish_reason": "stop"
                            }]
                        }
                        yield f"data: {json.dumps(final_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        break

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    finally:
        if request.conversation_id is not None and accumulated:
            try:
                db.add_message(request.conversation_id, "assistant", "".join(accumulated))
            except Exception:
                logger.exception("failed to persist streamed assistant message")


CORE_SYSTEM_PROMPT = """You are the core intelligence of a private local AI system.

RESPONSE STYLE
- Give the direct answer first in one or two sentences.
- Use clear Markdown headings when structure helps.
- Use numbered steps for procedures and bullets for multiple facts or options.
- Be practical, specific, and actionable.
- Prefer ready-to-use code, configuration, prompts, and templates.
- Avoid decorative fluff and unnecessary preambles.
- Ask at most one concise clarifying question when truly necessary; otherwise make a sensible assumption and proceed.

VOICE-FIRST BEHAVIOR
- Assume every message may be spoken aloud.
- Jarvis is a voice-enabled assistant inside the Jarvis interface. The interface handles microphone capture, speech recognition, audio playback, and voice interaction.
- When speech reaches you through Jarvis, assume the microphone and transcription pipeline are already working. Never tell the user that you cannot access their microphone or initiate voice conversations, and never send generic microphone-setup instructions unless an actual voice subsystem error is reported.
- Voice-to-voice is a first-class interaction mode: listen naturally, understand the user's spoken request, reason and use tools, then answer naturally through speech.
- Speak like a warm, calm, intelligent human assistant: conversational, confident, concise, and slightly expressive. Avoid robotic phrasing, canned disclaimers, excessive headings, and unnecessary repetition.
- For spoken answers, favor short natural sentences and pauses. Do not read Markdown formatting, URLs, code fences, or UI instructions aloud; summarize them naturally.
- Never rely on visual-only references such as "see above" or "see below" without stating the important information.
- When explaining code or configuration, describe what it does in plain language before the code block.
- When the user asks for a spoken walkthrough, explain it conversationally and step by step.

ACCURACY AND TOOLS
- Never claim that a tool, plugin, email, calendar action, web search, file operation, or other external action happened unless it actually succeeded.
- Clearly distinguish facts, tool results, recommendations, and configuration that still needs to be completed.
- Prefer local processing and free/open-source components when practical.

CODE AND FORMATTING
- Put executable code in fenced code blocks with a language tag.
- Make examples complete and copy-pasteable when practical.
- Keep one response coherent and easy to render in a ChatGPT-style interface.

LOCAL HOME AI
- Act as the central intelligence of the local AI environment.
- Coordinate memory, RAG, local models, voice, and connected tools through the AI gateway.
- Optimize for privacy, reliability, low latency, and useful real-world assistance.

AGENT TOOL USE
- When a Google account is needed, call google_accounts first and use a connected account; never invent an email address.
- Use tools when the user asks for a real action. Do not merely describe how to do it if a connected tool can perform it.
- Read/check state before changing it when practical.
- Discover devices/accounts before acting when identifiers are unknown.
- Never guess a device entity_id, Google account, message ID, event ID, or file path.
- Sensitive actions require confirmation and must stop until the user confirms.
- For multi-step tasks, preserve earlier tool results and continue from the exact stopping point after approval.

COMPUTER USE (only when the user asks to operate the Windows PC)
- Loop every computer action as OBSERVE (computer_observe) -> identify target -> FOCUS target -> VERIFY focus -> ACT -> OBSERVE again -> VERIFY the expected result -> only then continue.
- Never report an action as successful merely because an API returned HTTP 200; success requires an observed process, window, or foreground result.
- For typing, supply title/process/pid whenever known; focus must verify before typing; report the focused PID/title; screenshot after consequential typing.
- For opening apps, verify the expected process/window exists and reject launch-error dialogs; do not claim success on timeout.
- Prefer read-only observe/verify/windows/processes for diagnosis before acting.

SAAS SEPARATION
- The AI core, voice interface, and Sales Machine are separate capabilities.
- Do not assume the Email Agent SaaS is installed, connected, or available.
- Do not mix Email Agent implementation details into general AI or Sales Machine behavior unless a real integration is explicitly connected.
"""


SALES_SYSTEM_PROMPT = """You are the voice-first Sales Machine inside a private local AI system.

Your job is to help run and improve a sales operation through natural conversation. You are separate from the Email Agent SaaS and must never assume that Email Agent code, credentials, inboxes, or customer data are connected.

SALES BEHAVIOR
- Act like a practical sales operator and sales coach.
- Help with lead qualification, prospecting, follow-up strategy, objection handling, appointment setting, pipeline prioritization, scripts, offers, and conversion improvement.
- When given a lead or prospect, identify the next best sales action and explain why briefly.
- Write concise, natural scripts that sound like a real salesperson.
- For voice interactions, keep sentences short and conversational.
- Never invent lead information, CRM records, conversations, appointments, or outcomes.
- If a real sales tool is not connected, provide the exact workflow, API shape, or implementation needed rather than pretending the action occurred.
- Protect customer privacy and avoid exposing secrets or credentials.

VOICE SALES MODE
- Speak naturally and confidently.
- Ask one useful qualification question at a time when role-playing with a prospect.
- Handle objections without being aggressive or deceptive.
- Focus on helping the prospect make an informed decision.

SAAS BOUNDARY
- Sales Machine is an independent capability.
- Email Agent is an optional separate SaaS product.
- Voice is an interface that can control the Sales Machine without coupling the Sales Machine to the Email Agent.
"""


AGENT_TOOLS = [
    {"type":"function","function":{"name":"google_accounts","description":"List connected Google accounts so the assistant can select the user's account.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"file_search","description":"Search allowed Windows files by name.","parameters":{"type":"object","properties":{"query":{"type":"string"},"root":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["query"]}}},
    {"type":"function","function":{"name":"file_content_search","description":"Search text content in allowed files.","parameters":{"type":"object","properties":{"query":{"type":"string"},"root":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50}},"required":["query"]}}},
    {"type":"function","function":{"name":"read_file","description":"Read a text file from an allowed Windows path.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"write_file","description":"Create or overwrite a text file. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"},"overwrite":{"type":"boolean"}},"required":["path","content"]}}},
    {"type":"function","function":{"name":"open_file","description":"Open a file using the Windows host bridge.","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
    {"type":"function","function":{"name":"gmail_search","description":"Search Gmail. Use a connected account email.","parameters":{"type":"object","properties":{"email":{"type":"string"},"query":{"type":"string"},"max_results":{"type":"integer"}},"required":["email","query"]}}},
    {"type":"function","function":{"name":"gmail_read","description":"Read a Gmail message by message id.","parameters":{"type":"object","properties":{"email":{"type":"string"},"message_id":{"type":"string"}},"required":["email","message_id"]}}},
    {"type":"function","function":{"name":"gmail_send","description":"Send an email. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"to":{"type":"string"},"subject":{"type":"string"},"body":{"type":"string"}},"required":["email","to","subject","body"]}}},
    {"type":"function","function":{"name":"calendar_list","description":"List upcoming Google Calendar events.","parameters":{"type":"object","properties":{"email":{"type":"string"},"max_results":{"type":"integer"}},"required":["email"]}}},
    {"type":"function","function":{"name":"calendar_create","description":"Create a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event":{"type":"object"}},"required":["email","event"]}}},
    {"type":"function","function":{"name":"calendar_update","description":"Update a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event_id":{"type":"string"},"event":{"type":"object"}},"required":["email","event_id","event"]}}},
    {"type":"function","function":{"name":"calendar_delete","description":"Delete a Google Calendar event. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"email":{"type":"string"},"event_id":{"type":"string"}},"required":["email","event_id"]}}},
    {"type":"function","function":{"name":"home_states","description":"Read Home Assistant states for lights, thermostats, TVs and other devices.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"home_entities","description":"Discover controllable lights, thermostats, switches, fans and TVs/media players. Use this before selecting a device when the user names a room or TV but not an entity id.","parameters":{"type":"object","properties":{"domains":{"type":"array","items":{"type":"string"}}}}}},
    {"type":"function","function":{"name":"home_device","description":"Control a Home Assistant device such as a light, thermostat, switch, fan, or TV/media player. This always returns a confirmation request first.","parameters":{"type":"object","properties":{"entity_id":{"type":"string"},"action":{"type":"string"},"temperature":{"type":"number"},"volume_level":{"type":"number"}},"required":["entity_id","action"]}}},
    {"type":"function","function":{"name":"local_tools_inventory","description":"Discover installed local tools and approved capabilities. Use this before planning creative work such as making a video; choose tools by capability instead of requiring the user to name Blender, Unreal, ComfyUI, or FFmpeg.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"connections_inventory","description":"Discover the live connection registry and status of accounts, home services, AI, creative tools, computer services, iPhone and separate business services.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"artifact_create","description":"Create a persistent workspace artifact for the user. Use kinds markdown, tasks, mermaid, image, record, or progress.","parameters":{"type":"object","properties":{"title":{"type":"string"},"kind":{"type":"string"},"content":{"type":"string"},"metadata":{"type":"object"}},"required":["title","content"]}}},
    {"type":"function","function":{"name":"research_search","description":"Search the web only when optional Exa research is configured. If unavailable, continue with local knowledge and say web research is not configured.","parameters":{"type":"object","properties":{"query":{"type":"string"},"num_results":{"type":"integer","minimum":1,"maximum":10}},"required":["query"]}}},
    {"type":"function","function":{"name":"computer_status","description":"Check if opt-in Windows computer control is enabled.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_open","description":"Open an application or file using the Windows host bridge. Requires confirmation.","parameters":{"type":"object","properties":{"target":{"type":"string"}},"required":["target"]}}},
    {"type":"function","function":{"name":"computer_type","description":"Type text into a Windows application. Optional title/process/pid focuses and verifies that window first; without a target the current foreground window is reported.","parameters":{"type":"object","properties":{"text":{"type":"string"},"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}},"required":["text"]}}},
    {"type":"function","function":{"name":"computer_focus","description":"Bring a Windows window to the foreground by title, process name, or PID. Verified before returning.","parameters":{"type":"object","properties":{"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"computer_verify","description":"Report the current foreground window and whether a target title/process/PID is actually focused. Basis of observe-act-observe-verify.","parameters":{"type":"object","properties":{"title":{"type":"string"},"process":{"type":"string"},"pid":{"type":"integer"}}}}},
    {"type":"function","function":{"name":"computer_observe","description":"Capture the current screen (resized for efficient observation) plus foreground window and top windows. Always call before acting on the PC and again after acting to verify the result.","parameters":{"type":"object","properties":{"max_width":{"type":"integer"},"include_windows":{"type":"boolean"},"include_processes":{"type":"boolean"}}}}},
    {"type":"function","function":{"name":"computer_key","description":"Press a keyboard key or shortcut such as ENTER or CTRL+L when computer mode is enabled.","parameters":{"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}}},
    {"type":"function","function":{"name":"computer_screenshot","description":"Capture the current Windows screen when computer mode is enabled.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_capabilities","description":"Inspect the Windows PC capabilities relevant to Jarvis computer control, including NVIDIA GPU, NVIDIA Broadcast, Power Automate Desktop, pywinauto, Turtle Beach/Xbox audio devices, RAM and GPU telemetry.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_move","description":"Move the mouse cursor to a screen coordinate.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"duration":{"type":"number"}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"computer_click","description":"Click the Windows desktop at a screen coordinate. Requires confirmation.","parameters":{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"clicks":{"type":"integer"},"button":{"type":"string"}},"required":["x","y"]}}},
    {"type":"function","function":{"name":"computer_scroll","description":"Scroll the active Windows application.","parameters":{"type":"object","properties":{"amount":{"type":"integer"}},"required":["amount"]}}},
    {"type":"function","function":{"name":"computer_windows","description":"List visible Windows application windows using Windows UI Automation when available.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_processes","description":"List running Windows processes for diagnosis and computer-use planning.","parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{"name":"computer_shell","description":"Run a PowerShell command on the Windows host bridge. Requires confirmation and is intended for administrator/system tasks.","parameters":{"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer"}},"required":["command"]}}},
]


async def _agent_tool(name: str, args: dict, confirmed: bool=False):
    # Tool failures should become structured agent results instead of aborting the
    # entire conversation. This is especially useful for optional integrations
    # such as Home Assistant that may not be configured yet.
    try:
        return await _agent_tool_impl(name, args, confirmed)
    except RuntimeError as exc:
        return {"ok": False, "status": "not_configured", "error": str(exc)}
    except HTTPException as exc:
        if exc.status_code >= 500:
            return {"ok": False, "status": "tool_error", "error": str(exc.detail)}
        raise


async def _agent_tool_impl(name: str, args: dict, confirmed: bool=False):
    if name == "google_accounts": return {"accounts": google_oauth.TokenStore().list_accounts()}
    if name in {"file_search","file_content_search","read_file","write_file","open_file"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name in {"gmail_search","gmail_read","gmail_send","calendar_list","calendar_create","calendar_update","calendar_delete"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name in {"home_states","home_entities","home_device"}: return await execute_tool(ToolRequest(tool=name,arguments=args,confirmed=confirmed))
    if name == "local_tools_inventory": return {"result":local_tools.scan_local_tools()}
    if name == "connections_inventory": return {"result":await _connection_snapshot()}
    if name == "artifact_create": return await execute_tool(ToolRequest(tool="artifact_create",arguments=args,confirmed=confirmed))
    if name == "research_search": return await execute_tool(ToolRequest(tool="research_search",arguments=args,confirmed=confirmed))
    if name == "computer_status": return await execute_tool(ToolRequest(tool="computer_status",arguments=args,confirmed=confirmed))
    if name == "computer_open": return await execute_tool(ToolRequest(tool="computer_open",arguments=args,confirmed=confirmed))
    if name == "computer_type": return await execute_tool(ToolRequest(tool="computer_type",arguments=args,confirmed=confirmed))
    if name == "computer_focus": return await execute_tool(ToolRequest(tool="computer_focus",arguments=args,confirmed=confirmed))
    if name == "computer_verify": return await execute_tool(ToolRequest(tool="computer_verify",arguments=args,confirmed=confirmed))
    if name == "computer_observe": return await execute_tool(ToolRequest(tool="computer_observe",arguments=args,confirmed=confirmed))
    if name == "computer_key": return await execute_tool(ToolRequest(tool="computer_key",arguments=args,confirmed=confirmed))
    if name == "computer_screenshot": return await execute_tool(ToolRequest(tool="computer_screenshot",arguments=args,confirmed=confirmed))
    if name == "computer_capabilities": return await execute_tool(ToolRequest(tool="computer_capabilities",arguments=args,confirmed=confirmed))
    if name == "computer_move": return await execute_tool(ToolRequest(tool="computer_move",arguments=args,confirmed=confirmed))
    if name == "computer_click": return await execute_tool(ToolRequest(tool="computer_click",arguments=args,confirmed=confirmed))
    if name == "computer_scroll": return await execute_tool(ToolRequest(tool="computer_scroll",arguments=args,confirmed=confirmed))
    if name == "computer_windows": return await execute_tool(ToolRequest(tool="computer_windows",arguments=args,confirmed=confirmed))
    if name == "computer_processes": return await execute_tool(ToolRequest(tool="computer_processes",arguments=args,confirmed=confirmed))
    if name == "computer_shell": return await execute_tool(ToolRequest(tool="computer_shell",arguments=args,confirmed=confirmed))
    raise HTTPException(404,"unknown agent tool")


def _normalize_tool_args(raw: Any, name: str) -> dict:
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(400, f"invalid tool arguments for {name}")
    if not isinstance(args, dict):
        raise HTTPException(400, f"tool arguments for {name} must be an object")
    return args


def _save_confirmation_state(ticket: str, state: dict) -> None:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.get(ticket)
        if item is not None:
            item["resume"] = state


def _update_confirmation_resume(ticket: str, **updates) -> None:
    with _PENDING_LOCK:
        item = _PENDING_ACTIONS.get(ticket)
        if item is not None:
            resume = dict(item.get("resume") or {})
            resume.update(updates)
            item["resume"] = resume


async def _run_agent_loop(model: str, messages: list, assistant_profile: str,
                          conversation_id: int | None, max_tool_rounds: int,
                          initial_tool_result: dict | None = None) -> dict:
    """Run the agent until it has a final answer or one sensitive action needs approval.

    The full in-flight state is stored on confirmation tickets, so approving a tool
    resumes the same multi-step task instead of losing the earlier reads/tool calls.
    """
    tool_rounds = 0
    if initial_tool_result is not None:
        messages.append({"role": "tool", "content": json.dumps(initial_tool_result, default=str)})

    async with httpx.AsyncClient(timeout=300.0) as client:
        while tool_rounds < max(1, min(max_tool_rounds, 8)):
            payload = {
                "model": model,
                "messages": messages,
                "tools": AGENT_TOOLS,
                "stream": False,
                "think": False,
                "options": {**_adaptive_options(), "temperature": 0.2, "num_predict": 1024},
            }
            async with JARVIS_MODEL_LOCK:
                r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            if r.status_code != 200:
                raise HTTPException(r.status_code, r.text)
            data = r.json()
            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                answer = msg.get("content", "")
                if conversation_id is not None:
                    try:
                        db.add_message(conversation_id, "assistant", answer)
                    except Exception:
                        logger.exception("agent memory save failed")
                return {"message": {"role": "assistant", "content": answer}, "conversation_id": conversation_id, "tool_rounds": tool_rounds}

            messages.append(msg)
            # Process tool calls in order. If a sensitive action is encountered,
            # preserve the entire conversation state and resume after approval.
            for index, call in enumerate(tool_calls):
                fn = call.get("function", {})
                name = fn.get("name")
                args = _normalize_tool_args(fn.get("arguments", {}), name)
                result = await _agent_tool(name, args, False)
                if isinstance(result, dict) and result.get("status") == "confirmation_required":
                    ticket = result.get("confirmation_id")
                    if ticket:
                        _save_confirmation_state(ticket, {
                            "model": model,
                            "messages": messages,
                            "assistant_profile": assistant_profile,
                            "conversation_id": conversation_id,
                            "max_tool_rounds": max_tool_rounds,
                            "tool_rounds": tool_rounds,
                            "call_index": index,
                            "total_calls": len(tool_calls),
                        })
                    return {
                        "message": {"role": "assistant", "content": f"I need your confirmation before I do that. {result['reason']}"},
                        "confirmation": result,
                        "conversation_id": conversation_id,
                        "tool_rounds": tool_rounds + 1,
                    }
                messages.append({"role": "tool", "content": json.dumps(result, default=str)})
            tool_rounds += 1
    raise HTTPException(500, "agent reached its tool-call limit")


_VOICE_TOOL_HINTS = re.compile(r"\b(send|email|e-mail|gmail|inbox|calendar|invite|meeting|schedule|remind|home assistant|turn on|turn off|switch on|switch off|open|launch|create|delete|remove|cancel|control|device|lights?|thermostat|lock|unlock|play|pause|volume|file|folder|search my|check my|sales machine|email agent|approve|confirm)\b", re.IGNORECASE)


def select_voice_path(text: str, assistant_profile: str = "general") -> str:
    """Choose the voice turn transport: 'agent' (tool-capable Qwen, JSON) or
    'stream' (auto-routed chat SSE for low-latency speech)."""
    profile = (assistant_profile or "general").lower()
    if profile == "sales":
        return "agent"
    if _VOICE_TOOL_HINTS.search(text or ""):
        return "agent"
    return "stream"
