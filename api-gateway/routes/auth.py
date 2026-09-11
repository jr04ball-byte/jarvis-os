"""V24 P2: auth routes (moved verbatim from main.py)."""
import asyncio
import logging
import time
from datetime import datetime, timezone

import google_oauth
import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/auth/google/start")
async def auth_google_start():
    """Begin Google OAuth — returns the URL the user should visit."""
    if not google_oauth.CLIENT_ID:
        raise HTTPException(503, "Google OAuth not configured (GOOGLE_CLIENT_ID missing)")
    return {"url": google_oauth.build_auth_url()}


@router.get("/auth/google/callback")
async def auth_google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Google redirects here after user consents. Exchanges code → tokens,
    stores them encrypted, returns the connected account email."""
    if error:
        raise HTTPException(400, f"Google OAuth failed: {error}")
    if not code or not state:
        raise HTTPException(400, "missing code or state (did you deny consent?)")
    if not google_oauth._consume_state(state):
        raise HTTPException(400, "invalid or expired state token")
    try:
        result = await google_oauth.exchange_code(code)
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"token exchange failed: {e.response.text}")
    store = google_oauth.TokenStore()
    store.save_tokens(result["email"], result["tokens"])
    logger.info("Google account connected: %s", result["email"])
    return {"ok": True, "email": result["email"], "scopes": result["tokens"].get("scope", "")}


@router.get("/auth/google/status")
async def auth_google_status():
    """List connected Google accounts and whether their tokens are still valid."""
    store = google_oauth.TokenStore()
    accounts = []
    for email in store.list_accounts():
        tok = store.load_tokens(email)
        if not tok:
            continue
        expires_at = tok.get("saved_at", 0) + int(tok.get("expires_in", 3600))
        accounts.append({
            "email": email,
            "scopes": tok.get("scope", ""),
            "expires_at": expires_at,
            "needs_refresh": time.time() >= expires_at - 60,
            "has_refresh_token": "refresh_token" in tok,
        })
    return {"accounts": accounts, "configured": bool(google_oauth.CLIENT_ID)}


@router.post("/auth/google/disconnect")
async def auth_google_disconnect(email: str):
    """Forget a connected account (revokes locally; doesn't revoke on Google's side)."""
    store = google_oauth.TokenStore()
    if store.delete_account(email):
        return {"ok": True, "email": email}
    raise HTTPException(404, f"no tokens stored for {email}")


@router.get("/gmail/messages")
async def gmail_messages(email: str, max_results: int = Query(default=20, ge=1, le=50), query: str = "", include_headers: bool = False):
    """List recent Gmail messages for the connected account. Read-only."""
    store = google_oauth.TokenStore()
    try:
        msgs = await google_oauth.gmail_list_messages(email, store, max_results, query)
        if include_headers:
            # The dashboard requests only five rows. Bound metadata fan-out and
            # never return bodies, access tokens, or arbitrary provider fields.
            selected = msgs[:5]
            details = await asyncio.gather(*[
                google_oauth.gmail_get_message(email, store, msg['id']) for msg in selected
            ], return_exceptions=True)
            for msg, detail in zip(selected, details):
                if isinstance(detail, Exception):
                    continue
                headers = {h.get('name', '').lower(): h.get('value', '') for h in detail.get('payload', {}).get('headers', [])}
                msg.update(sender=headers.get('from', ''), subject=headers.get('subject', ''))
            msgs = selected
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    return {"email": email, "count": len(msgs), "messages": msgs}


@router.get("/calendar/events")
async def calendar_events(email: str, max_results: int = 10):
    """List upcoming calendar events for the connected account."""
    time_min = datetime.now(timezone.utc).isoformat()
    store = google_oauth.TokenStore()
    try:
        events = await google_oauth.calendar_list_events(email, store, max_results, time_min)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    return {"email": email, "count": len(events), "events": events}
