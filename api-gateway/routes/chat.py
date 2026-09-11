"""V24 P2: chat routes (moved verbatim from main.py)."""
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from budgets import budget as token_budget
from compute_manager import select_mode
from deps import (
    GEMINI_API_KEY,
    GEMINI_AUTH_TOKEN_URL,
    GEMINI_LIVE_MODEL,
    GEMINI_LIVE_WS_URL,
    GEMINI_MODEL,
    GOOGLE_CLOUD_LOCATION,
    GOOGLE_CLOUD_PROJECT,
    JARVIS_COMPUTE_MODE,
    JARVIS_MAX_CONTEXT_CHARS,
    JARVIS_MAX_RAG_CHARS,
    JARVIS_MODEL_LOCK,
    MAX_HISTORY_MESSAGES,
    OLLAMA_URL,
    db,
    limiter,
    monitor,
    rag,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from schemas import (
    ChatMessage,
    ChatRequest,
    CompareRequest,
    GeminiChatRequest,
    GeminiLiveTokenRequest,
)
from services import _adaptive_options, apply_system_prompt, select_model, stream_chat

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/v1/gemini/status")
async def gemini_status():
    return {
        "configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "project": GOOGLE_CLOUD_PROJECT or None,
        "location": GOOGLE_CLOUD_LOCATION,
        "backend": "Gemini Developer API via Google Cloud project" if GEMINI_API_KEY else "not_configured",
    }


@router.post("/v1/gemini/chat")
@limiter.limit("20/minute")
async def gemini_chat(request: Request, body: GeminiChatRequest):
    """Optional cloud chat. The API key stays server-side; Qwen/Ollama remains the local default."""
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Google Gemini is not configured. Set GEMINI_API_KEY in .env")
    token_budget.check("gemini", "cloud")
    model = body.model or GEMINI_MODEL
    contents = []
    for m in body.messages[-100:]:
        role = "model" if m.role == "assistant" else "user"
        if m.role == "system":
            role = "user"
        contents.append({"role": role, "parts": [{"text": m.content}]})
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": max(0.0, min(float(body.temperature or 0.7), 2.0)),
            "maxOutputTokens": max(1, min(int(body.max_tokens or 1024), 8192)),
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(url, headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}, json=payload)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gemini request failed: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    data = r.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    answer = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    token_budget.record("gemini", "cloud", data.get("usageMetadata"))
    return {"message": {"role": "assistant", "content": answer}, "model": model, "provider": "google_gemini"}


@router.post("/v1/gemini/live-token")
@limiter.limit("10/minute")
async def gemini_live_token(request: Request, body: GeminiLiveTokenRequest = GeminiLiveTokenRequest()):
    """Mint a one-use Gemini Live ephemeral token without exposing the API key.

    Token creation is performed server-side using the long-lived API key.  The
    browser receives only a short-lived, single-use credential for the
    constrained Live WebSocket endpoint.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Google Gemini is not configured. Set GEMINI_API_KEY in .env")

    ttl_minutes = max(1, min(int(body.ttl_minutes or 10), 30))
    now = datetime.now(timezone.utc)
    payload = {
        "uses": 1,
        "expireTime": (now + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z"),
        # Limit the window in which a stolen token could start a new session.
        "newSessionExpireTime": (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                GEMINI_AUTH_TOKEN_URL,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Gemini ephemeral token request failed: {exc}")

    if response.status_code not in (200, 201):
        detail = response.text[:500].replace(GEMINI_API_KEY, "[REDACTED]")
        raise HTTPException(response.status_code, f"Gemini ephemeral token request failed: {detail}")
    data = response.json()
    ephemeral = str(data.get("name") or "").strip()
    if not ephemeral:
        raise HTTPException(502, "Gemini token service returned no ephemeral credential")

    return {
        "token": ephemeral,
        "token_type": "ephemeral",
        "model": GEMINI_LIVE_MODEL,
        "ws_url": GEMINI_LIVE_WS_URL,
        "input_rate": 16000,
        "output_rate": 24000,
        "expires_in": ttl_minutes * 60,
        "new_session_expires_in": 60,
    }


@router.post("/v1/chat/completions")
@limiter.limit("30/minute")
async def chat_completion(request: Request, chat_request: ChatRequest):
    """OpenAI-compatible chat completion with optional persistent memory and RAG."""
    model_messages = apply_system_prompt(list(chat_request.messages), chat_request.assistant_profile)

    if chat_request.conversation_id is not None:
        if not db.conversation_exists(chat_request.conversation_id):
            raise HTTPException(
                status_code=404,
                detail=f"conversation {chat_request.conversation_id} not found"
            )

        conversation_profile = db.get_conversation_profile(chat_request.conversation_id)
        requested_profile = chat_request.assistant_profile.lower()
        if requested_profile not in {"general", "sales"}:
            requested_profile = "general"
        if conversation_profile != requested_profile:
            raise HTTPException(
                status_code=409,
                detail=f"conversation {chat_request.conversation_id} belongs to the '{conversation_profile}' profile; start a new conversation for '{requested_profile}'"
            )

        history = db.get_conversation(
            chat_request.conversation_id,
            limit=MAX_HISTORY_MESSAGES
        )

        incoming = [{"role": m.role, "content": m.content} for m in chat_request.messages]
        if history and incoming[:len(history)] == history:
            new_messages = incoming[len(history):]
            model_messages = apply_system_prompt([ChatMessage(**msg) for msg in incoming], chat_request.assistant_profile)
        else:
            new_messages = incoming
            model_messages = apply_system_prompt(
                [ChatMessage(role=h["role"], content=h["content"]) for h in history] + chat_request.messages,
                chat_request.assistant_profile
            )

        for msg in new_messages:
            if msg["role"] == "user":
                db.add_message(chat_request.conversation_id, "user", msg["content"])

    # Keep the newest system message plus the most recent messages within a
    # character budget. This protects VRAM without reducing the model itself.
    if sum(len(m.content) for m in model_messages) > JARVIS_MAX_CONTEXT_CHARS:
        system_msgs = [m for m in model_messages if m.role == "system"][:1]
        non_system = [m for m in model_messages if m.role != "system"]
        kept = []
        total = sum(len(m.content) for m in system_msgs)
        for msg in reversed(non_system):
            if total + len(msg.content) > JARVIS_MAX_CONTEXT_CHARS and kept:
                break
            kept.append(msg); total += len(msg.content)
        model_messages = system_msgs + list(reversed(kept))

    if chat_request.use_rag and model_messages:
        last_user = next(
            (msg for msg in reversed(model_messages) if msg.role == "user"),
            None
        )
        if last_user is not None:
            last_user.content = rag.augment_prompt(last_user.content)[:JARVIS_MAX_RAG_CHARS]

    selected_model = select_model(chat_request.model, [m.model_dump() for m in model_messages if m.role != "system"], chat_request.assistant_profile)

    if chat_request.stream:
        stream_request = chat_request.model_copy(update={
            "model": selected_model,
            "messages": model_messages,
            "use_rag": False
        })
        return StreamingResponse(
            stream_chat(stream_request),
            media_type="text/event-stream"
        )

    start_time = time.time()
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            async with JARVIS_MODEL_LOCK:
                response = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                    "model": selected_model,
                    "messages": [{"role": msg.role, "content": msg.content} for msg in model_messages],
                    "stream": False,
                    "think": False,
                    "options": {**_adaptive_options(), "temperature": chat_request.temperature, "num_predict": chat_request.max_tokens or 1024}
                }
            )

            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            ollama_response = response.json()
            assistant_content = ollama_response.get("message", {}).get("content", "")
            duration = time.time() - start_time
            tokens = (
                ollama_response.get("eval_count", 0)
                + ollama_response.get("prompt_eval_count", 0)
            )
            monitor.record(selected_model, tokens, duration)

            if chat_request.conversation_id is not None and assistant_content:
                db.add_message(
                    chat_request.conversation_id, "assistant", assistant_content
                )

            result_compute = select_mode(JARVIS_COMPUTE_MODE)
            return {
                "compute": {"mode": result_compute["mode"], "reason": result_compute["reason"]},
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": selected_model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": assistant_content},
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": ollama_response.get("prompt_eval_count", 0),
                    "completion_tokens": ollama_response.get("eval_count", 0),
                    "total_tokens": tokens
                }
            }

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama service unavailable: {e!s}"
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Chat completion failed: {e!s}"
            )


@router.post("/v1/sales/chat")
@limiter.limit("30/minute")
async def sales_chat(request: Request, chat_request: ChatRequest):
    """Sales Machine endpoint. Independent from the Email Agent SaaS."""
    sales_request = chat_request.model_copy(update={"assistant_profile": "sales"})
    return await chat_completion(request, sales_request)


@router.post("/v1/chat/completions-rag")
@limiter.limit("20/minute")
async def chat_with_rag(request: Request, chat_request: ChatRequest):
    """Chat with RAG context augmentation without polluting stored memory."""
    chat_request.use_rag = True
    return await chat_completion(request, chat_request)


@router.post("/v1/compare")
@limiter.limit("5/minute")
async def compare_models(
    request: Request,
    body: CompareRequest,
):
    """Compare responses from multiple models"""
    results = {}

    async with httpx.AsyncClient(timeout=300.0) as client:
        for model in body.models:
            start = time.time()

            try:
                response = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": body.prompt}],
                    "stream": False,
                    "think": False,
                        "options": {"num_predict": 1024}
                    }
                )

                duration = time.time() - start

                if response.status_code == 200:
                    result = response.json()

                    results[model] = {
                        "response": result["message"]["content"],
                        "tokens": result.get("eval_count", 0),
                        "time_seconds": round(duration, 2),
                        "tokens_per_second": round(result.get("eval_count", 0) / duration, 1) if duration > 0 else 0,
                        "status": "success"
                    }
                else:
                    results[model] = {
                        "status": "error",
                        "error": f"HTTP {response.status_code}"
                    }
            except Exception as e:
                results[model] = {
                    "status": "error",
                    "error": str(e)
                }

    return results
