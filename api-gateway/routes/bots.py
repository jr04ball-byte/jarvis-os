"""Named bots, scoped memory, routines, skills, and direct handoffs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from deps import bots, db, orchestrator, routines, skills
from fact_memory import facts
from schemas import (
    BotChatRequest, BotCreateRequest, BotDirectMessageRequest, BotUpdateRequest,
    MemoryRememberRequest, RoutineCreateRequest, RoutineUpdateRequest,
    SkillRecordStartRequest, SkillRunRequest, ChatMessage,
)
from security import allowed
from services import _run_agent_loop, apply_system_prompt, execute_tool_core, select_agent_model

router = APIRouter()


def _bot(bot_id):
    try: return bots.get_bot(bot_id)
    except KeyError: raise HTTPException(404, "bot not found") from None


@router.post("/v1/bots")
async def create_bot(body: BotCreateRequest):
    invalid = [tool for tool in body.tool_allowlist if not allowed(tool)]
    if invalid: raise HTTPException(400, f"unknown tools: {', '.join(invalid)}")
    return bots.create_bot(**body.model_dump())


@router.get("/v1/bots")
async def list_bots(status: str | None = None):
    return {"bots": bots.list_bots(status)}


@router.get("/v1/bots/{bot_id}")
async def get_bot(bot_id: str): return _bot(bot_id)


@router.patch("/v1/bots/{bot_id}")
async def update_bot(bot_id: str, body: BotUpdateRequest):
    changes = body.model_dump(exclude_none=True); status = changes.pop("status", None)
    invalid = [tool for tool in changes.get("tool_allowlist", []) if not allowed(tool)]
    if invalid: raise HTTPException(400, f"unknown tools: {', '.join(invalid)}")
    try:
        if changes: bots.update_bot(bot_id, **changes)
        return bots.set_status(bot_id, status) if status else bots.get_bot(bot_id)
    except (KeyError, ValueError) as error: raise HTTPException(404 if isinstance(error, KeyError) else 400, str(error)) from None


@router.delete("/v1/bots/{bot_id}")
async def delete_bot(bot_id: str):
    _bot(bot_id); routines.delete_for_bot(bot_id); skills.delete_for_bot(bot_id); facts(db, "forget_owner", owner=bot_id); bots.delete_bot(bot_id)
    return {"status": "deleted", "bot_id": bot_id}


@router.post("/v1/bots/{bot_id}/chat")
async def bot_chat(bot_id: str, body: BotChatRequest):
    bot = _bot(bot_id)
    if bot["status"] != "active": raise HTTPException(409, f"bot is {bot['status']}")
    conversation_id = bots.conversation_id(bot_id, db)
    history = db.get_conversation(conversation_id, limit=40)
    incoming = [message.model_dump() for message in body.messages]
    for message in incoming:
        if message["role"] == "user": db.add_message(conversation_id, "user", message["content"])
    combined = history + incoming
    model_messages = [message.model_dump() for message in apply_system_prompt(
        [ChatMessage(**message) for message in combined],
        "general", override_prompt=bot["system_prompt"],
    )]
    requested = "auto" if bot["brain"] == "gemini" else bot["brain"]
    model = select_agent_model(requested, model_messages, "general")
    return await _run_agent_loop(model, model_messages, "general", conversation_id, body.max_tool_rounds,
                                 bot_id=bot_id, tool_allowlist=bot["tool_allowlist"])


@router.post("/v1/bots/{bot_id}/message")
async def bot_send_direct(bot_id: str, body: BotDirectMessageRequest):
    _bot(bot_id)
    prefix = f"Message from bot {body.from_bot_id}: " if body.from_bot_id else "Direct handoff: "
    conversation_id = bots.append_message(bot_id, db, "user", prefix + body.content)
    return {"status": "queued", "bot_id": bot_id, "conversation_id": conversation_id}


@router.get("/v1/bots/{bot_id}/memory")
async def bot_memory_recall(bot_id: str): _bot(bot_id); return facts(db, "recall_facts", owner=bot_id)


@router.post("/v1/bots/{bot_id}/memory")
async def bot_memory_remember(bot_id: str, body: MemoryRememberRequest): _bot(bot_id); return facts(db, "remember_fact", fact=body.fact, owner=bot_id)


@router.delete("/v1/bots/{bot_id}/memory/{fact_id}")
async def bot_memory_forget(bot_id: str, fact_id: str): _bot(bot_id); return facts(db, "forget_fact", fact_id=fact_id, owner=bot_id)


@router.get("/v1/memory/global")
async def global_memory_recall(): return facts(db, "recall_facts", owner="global")


@router.post("/v1/memory/global")
async def global_memory_remember(body: MemoryRememberRequest): return facts(db, "remember_fact", fact=body.fact, owner="global")


@router.post("/v1/bots/{bot_id}/routines")
async def create_routine(bot_id: str, body: RoutineCreateRequest):
    _bot(bot_id)
    try: return routines.create(bot_id, **body.model_dump())
    except ValueError as error: raise HTTPException(400, str(error)) from None


@router.get("/v1/bots/{bot_id}/routines")
async def list_routines(bot_id: str): _bot(bot_id); return {"routines": routines.list(bot_id)}


@router.patch("/v1/bots/{bot_id}/routines/{routine_id}")
async def update_routine(bot_id: str, routine_id: str, body: RoutineUpdateRequest):
    _bot(bot_id)
    try:
        item = routines.get(routine_id)
        if item["bot_id"] != bot_id: raise KeyError(routine_id)
        return routines.update(routine_id, **body.model_dump(exclude_none=True))
    except KeyError: raise HTTPException(404, "routine not found") from None
    except ValueError as error: raise HTTPException(400, str(error)) from None


@router.delete("/v1/bots/{bot_id}/routines/{routine_id}")
async def delete_routine(bot_id: str, routine_id: str):
    try: item = routines.get(routine_id)
    except KeyError: raise HTTPException(404, "routine not found") from None
    if item["bot_id"] != bot_id: raise HTTPException(404, "routine not found")
    routines.delete(routine_id); return {"status": "deleted"}


@router.post("/v1/bots/{bot_id}/routines/{routine_id}/run")
async def run_routine_now(bot_id: str, routine_id: str):
    _bot(bot_id)
    try: item = routines.get(routine_id)
    except KeyError: raise HTTPException(404, "routine not found") from None
    if item["bot_id"] != bot_id: raise HTTPException(404, "routine not found")
    project = orchestrator.create_project(item["goal_template"]); routines.mark_run(routine_id, project["id"])
    return {"routine": routines.get(routine_id), "project": project}


@router.post("/v1/bots/{bot_id}/skills/record/start")
async def start_skill_recording(bot_id: str, body: SkillRecordStartRequest): _bot(bot_id); return skills.start_recording(bot_id, body.name, body.description)


@router.post("/v1/bots/{bot_id}/skills/record/stop")
async def stop_skill_recording(bot_id: str):
    _bot(bot_id)
    try: return skills.stop_recording(bot_id)
    except KeyError: raise HTTPException(409, "no active recording") from None


@router.get("/v1/bots/{bot_id}/skills")
async def list_skills(bot_id: str): _bot(bot_id); return {"skills": skills.list(bot_id)}


@router.post("/v1/skills/{skill_id}/run")
async def run_skill(skill_id: str, body: SkillRunRequest | None = None, bot_id: str | None = Query(default=None)):
    try: skill = skills.get(skill_id)
    except KeyError: raise HTTPException(404, "skill not found") from None
    acting_bot = bot_id or (body.bot_id if body else None) or skill["bot_id"]
    if acting_bot: _bot(acting_bot)
    results = []
    for index, step in enumerate(skill["steps"]):
        result = await execute_tool_core(step["tool"], step.get("arguments") or {}, confirmed=False)
        results.append(result)
        if isinstance(result, dict) and result.get("status") == "confirmation_required":
            return {"status": "awaiting_approval", "step": index, "confirmation": result, "results": results}
    skills.replayed(skill_id, acting_bot); return {"status": "completed", "skill_id": skill_id, "results": results}
