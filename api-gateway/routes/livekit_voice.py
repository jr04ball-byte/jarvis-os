"""LiveKit Cloud session credentials for optional Gemini voice-to-voice mode."""
from __future__ import annotations

import os
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter()
_SAFE_ID = re.compile(r"[^a-zA-Z0-9_-]+")


class LiveKitTokenRequest(BaseModel):
    display_name: str = Field(default="Jerry", max_length=64)


def livekit_configured() -> bool:
    return all((os.getenv("LIVEKIT_URL", "").strip(), os.getenv("LIVEKIT_API_KEY", "").strip(), os.getenv("LIVEKIT_API_SECRET", "").strip()))


@router.get("/voice-livekit", include_in_schema=False)
async def livekit_voice_ui():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "voice-livekit.html"))


@router.get("/v1/livekit/status")
async def livekit_status():
    return {"configured": livekit_configured(), "enabled": os.getenv("JARVIS_LIVEKIT_ENABLED", "false").lower() in {"1", "true", "yes", "on"}, "agent_name": os.getenv("JARVIS_LIVEKIT_AGENT_NAME", "jarvis-voice"), "model": os.getenv("JARVIS_LIVEKIT_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")}


@router.post("/v1/livekit/token")
async def livekit_token(body: LiveKitTokenRequest = LiveKitTokenRequest()):
    if os.getenv("JARVIS_LIVEKIT_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(503, "Live Conversation is disabled")
    if not livekit_configured():
        raise HTTPException(503, "LiveKit is not configured")
    try:
        from livekit import api
    except ImportError as exc:
        raise HTTPException(503, "LiveKit server SDK is not installed") from exc
    suffix = secrets.token_urlsafe(9).replace("-", "").replace("_", "")
    room = f"jarvis-{suffix.lower()}"
    base = _SAFE_ID.sub("-", body.display_name.strip()).strip("-")[:32] or "owner"
    identity = f"{base.lower()}-{secrets.token_hex(4)}"
    agent_name = os.getenv("JARVIS_LIVEKIT_AGENT_NAME", "jarvis-voice")
    token = (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(identity).with_name(body.display_name.strip() or "Jerry").with_ttl(timedelta(minutes=15))
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True, can_publish_data=True))
        .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=agent_name)])).to_jwt())
    return {"server_url": os.environ["LIVEKIT_URL"], "participant_token": token, "room_name": room, "expires_in": 900}
