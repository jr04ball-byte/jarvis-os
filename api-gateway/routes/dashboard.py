"""V24 P2: dashboard routes (moved verbatim from main.py)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

GATEWAY_DIR = Path(__file__).resolve().parent.parent


router = APIRouter()


@router.get("/dashboard", include_in_schema=False)
@router.get("/dashboard.html", include_in_schema=False)
async def dashboard_ui():
    """Primary V23 Jarvis Command Center."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "dashboard.html"), media_type="text/html")


@router.get("/godseye", include_in_schema=False)
@router.get("/godseye.html", include_in_schema=False)
async def godseye_ui():
    """V19 tactical globe view retained as an optional operations surface."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "godseye.html"), media_type="text/html")


@router.get("/dashboard-classic", include_in_schema=False)
@router.get("/dashboard-classic.html", include_in_schema=False)
async def dashboard_classic_ui():
    """Classic chat cockpit retained for compatibility."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "dashboard-classic.html"), media_type="text/html")


@router.get("/orb-loop.webm", include_in_schema=False)
@router.get("/orb-loop.mp4", include_in_schema=False)
@router.get("/orb-poster.png", include_in_schema=False)
@router.get("/bg-loop.webm", include_in_schema=False)
@router.get("/bg-loop.mp4", include_in_schema=False)
@router.get("/bg-poster.png", include_in_schema=False)
@router.get("/command-center-loop.webm", include_in_schema=False)
@router.get("/command-center-loop.mp4", include_in_schema=False)
@router.get("/command-center-poster.png", include_in_schema=False)
async def blender_dashboard_asset(request: Request):
    """Serve Blender-baked dashboard motion assets without exposing arbitrary files."""
    name = request.url.path.lstrip("/")
    allowed = {
        "orb-loop.webm", "orb-loop.mp4", "orb-poster.png",
        "bg-loop.webm", "bg-loop.mp4", "bg-poster.png",
        "command-center-loop.webm", "command-center-loop.mp4", "command-center-poster.png",
    }
    if name not in allowed:
        raise HTTPException(404, "asset not found")
    base = os.path.join(str(GATEWAY_DIR), "assets", name)
    if not os.path.isfile(base):
        raise HTTPException(404, "Blender dashboard asset not baked yet")
    media = "video/webm" if name.endswith(".webm") else ("video/mp4" if name.endswith(".mp4") else "image/png")
    return FileResponse(base, media_type=media, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/companion", include_in_schema=False)
async def companion_ui():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion.html"), media_type="text/html")


@router.get("/companion-manifest.json", include_in_schema=False)
async def companion_manifest():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion-manifest.json"), media_type="application/manifest+json")


@router.get("/companion-sw.js", include_in_schema=False)
async def companion_sw():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion-sw.js"), media_type="application/javascript")
