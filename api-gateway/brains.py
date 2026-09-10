from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from intelligence_router import IntelligenceRouter
from providers import GeminiProvider, OllamaProvider, OpenAIProvider, OpenCodeProvider, ProviderMessage

logger = logging.getLogger("brains")

GEMINI = "gemini"
OPENAI = "openai"
OPENCODE = "opencode"
OLLAMA = "ollama"
KNOWN = (GEMINI, OPENAI, OPENCODE, OLLAMA)


class Message(BaseModel):
    role: str
    content: str


class BrainRequest(BaseModel):
    messages: List[Message]
    brain: str = "auto"
    assistant_profile: str = "general"
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    conversation_id: Optional[int] = None
    mode: Optional[str] = None
    workspace: Optional[str] = None
    permission: str = "read_only"


class RouteRequest(BaseModel):
    text: str
    brain: str = "auto"
    mode: Optional[str] = None
    assistant_profile: str = "general"


providers = {
    GEMINI: GeminiProvider(),
    OPENAI: OpenAIProvider(),
    OLLAMA: OllamaProvider(),
    OPENCODE: OpenCodeProvider(),
}
router_engine = IntelligenceRouter(providers)
_STATUS_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_STATUS_LOCK = asyncio.Lock()


def _messages(messages: List[Message]) -> List[ProviderMessage]:
    return [ProviderMessage(role=m.role, content=m.content) for m in messages]


def _last_user(messages: List[Message]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content
    return ""


def _requested(req: BrainRequest) -> str:
    # mode takes precedence when supplied, while preserving the old `brain`
    # field for backward compatibility with V17-V20 clients.
    return (req.mode or req.brain or "auto").strip().lower()


# Backward-compatible helper used by older code/tests.
def classify(text: str, mode: str = "auto", profile: str = "general") -> str:
    return router_engine.decide(text, mode, profile).selected


async def complete(req: BrainRequest) -> Dict[str, Any]:
    text = _last_user(req.messages)
    decision = router_engine.decide(text, _requested(req), req.assistant_profile)
    messages = _messages(req.messages)
    last_error: Optional[str] = None
    attempts: List[Dict[str, Any]] = []

    chain = router_engine.eligible_chain(decision.chain)
    if not chain:
        raise HTTPException(503, detail={"message": "all eligible brains are temporarily unavailable", "route": decision.as_dict(), "circuits": router_engine.circuit.snapshot()})

    for name in chain:
        provider = providers[name]
        if not provider.configured:
            last_error = f"{name} not configured"
            attempts.append({"provider": name, "ok": False, "error": last_error, "elapsed_ms": 0})
            continue

        started = time.time()
        try:
            result = await provider.complete(
                messages,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens or 1024,
                task_context=router_engine.provider_context(
                    decision,
                    workspace=req.workspace,
                    permission=req.permission,
                ),
            )
            elapsed = int((time.time() - started) * 1000)
            router_engine.record_provider_result(name, ok=True, elapsed_ms=elapsed, route_reason=decision.reason)
            attempts.append({"provider": name, "ok": True, "elapsed_ms": elapsed})
            return {
                "message": {"role": "assistant", "content": result.content},
                "brain": name,
                "provider": name,
                "model": result.model,
                "requested": decision.requested,
                "route": decision.as_dict(),
                "elapsed_ms": elapsed,
                "attempts": attempts,
                "usage": result.usage,
                "metadata": result.metadata,
                "conversation_id": req.conversation_id,
            }
        except Exception as exc:
            elapsed = int((time.time() - started) * 1000)
            last_error = str(exc)
            router_engine.record_provider_result(name, ok=False, elapsed_ms=elapsed, route_reason=decision.reason)
            attempts.append({"provider": name, "ok": False, "error": last_error, "elapsed_ms": elapsed})
            logger.warning("provider %s failed: %s", name, exc)

    raise HTTPException(502, detail={"message": "all eligible brains failed", "attempts": attempts, "last_error": last_error})


async def stream(req: BrainRequest) -> AsyncGenerator[str, None]:
    text = _last_user(req.messages)
    decision = router_engine.decide(text, _requested(req), req.assistant_profile)
    messages = _messages(req.messages)

    chain = router_engine.eligible_chain(decision.chain)
    if not chain:
        yield f"event: error\ndata: {json.dumps({'error': 'all eligible brains are temporarily unavailable', 'circuits': router_engine.circuit.snapshot()})}\n\n"
        yield "data: [DONE]\n\n"
        return

    for name in chain:
        provider = providers[name]
        if not provider.configured:
            continue
        started = time.time()
        yielded = False
        try:
            yield f"event: route\ndata: {json.dumps(decision.as_dict())}\n\n"
            yield f"event: brain\ndata: {json.dumps({'brain': name, 'model': provider.model})}\n\n"
            async for chunk in provider.stream(
                messages,
                temperature=req.temperature or 0.7,
                max_tokens=req.max_tokens or 1024,
                task_context=router_engine.provider_context(
                    decision,
                    workspace=req.workspace,
                    permission=req.permission,
                ),
            ):
                yielded = True
                yield f"data: {json.dumps({'choices': [{'delta': {'content': chunk}}]})}\n\n"
            elapsed = int((time.time() - started) * 1000)
            router_engine.record_provider_result(name, ok=True, elapsed_ms=elapsed, route_reason=decision.reason)
            yield "data: [DONE]\n\n"
            return
        except Exception as exc:
            elapsed = int((time.time() - started) * 1000)
            router_engine.record_provider_result(name, ok=False, elapsed_ms=elapsed, route_reason=decision.reason)
            logger.warning("brain stream %s failed: %s", name, exc)
            # Once a provider has emitted user-visible tokens, switching models
            # mid-answer can create contradictory output. Fail closed instead.
            if yielded:
                yield f"event: error\ndata: {json.dumps({'error': str(exc), 'provider': name})}\n\n"
                yield "data: [DONE]\n\n"
                return

    yield f"event: error\ndata: {json.dumps({'error': 'all eligible brains failed'})}\n\n"
    yield "data: [DONE]\n\n"


async def status(force: bool = False, ttl_seconds: float = 10.0) -> Dict[str, Any]:
    now = time.time()
    cached = _STATUS_CACHE.get("data")
    if not force and cached is not None and now - float(_STATUS_CACHE.get("ts") or 0) < max(1.0, ttl_seconds):
        return cached

    async with _STATUS_LOCK:
        now = time.time()
        cached = _STATUS_CACHE.get("data")
        if not force and cached is not None and now - float(_STATUS_CACHE.get("ts") or 0) < max(1.0, ttl_seconds):
            return cached

        async def one(name: str, provider: Any) -> tuple[str, Dict[str, Any]]:
            try:
                health = await asyncio.wait_for(provider.health(), timeout=6.0)
            except asyncio.TimeoutError:
                health = {"online": False, "configured": provider.configured, "error": "health check timed out"}
            except Exception as exc:
                health = {"online": False, "configured": provider.configured, "error": str(exc)}
            return name, {**provider.describe(), **health}

        pairs = await asyncio.gather(*(one(name, provider) for name, provider in providers.items()))
        provider_status = dict(pairs)
        result = {
            **router_engine.snapshot(),
            "providers": provider_status,
            "health_cached_at": now,
        }
        _STATUS_CACHE["ts"] = now
        _STATUS_CACHE["data"] = result
        return result


router = APIRouter()


@router.get("/v1/brain/status")
async def brain_status():
    return await status()


@router.get("/v1/brain/policy")
async def brain_policy():
    return router_engine.snapshot()


@router.post("/v1/brain/route")
async def brain_route(request: RouteRequest):
    requested = request.mode or request.brain
    return router_engine.decide(request.text, requested, request.assistant_profile).as_dict()


@router.post("/v1/brain/chat")
async def brain_chat(request: BrainRequest):
    return await complete(request)


@router.post("/v1/brain/stream")
async def brain_stream(request: BrainRequest):
    return StreamingResponse(stream(request), media_type="text/event-stream")
