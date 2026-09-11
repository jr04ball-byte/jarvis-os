"""V24 P2: dashboard routes (moved verbatim from main.py)."""

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
    """Primary V25 neon dashboard matching the user-supplied visual reference."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "dashboard.html"), media_type="text/html")


@router.get("/dashboard-classic", include_in_schema=False)
@router.get("/dashboard-classic.html", include_in_schema=False)
async def dashboard_classic_ui():
    """Classic chat cockpit retained for compatibility."""
    return FileResponse(os.path.join(str(GATEWAY_DIR), "dashboard-classic.html"), media_type="text/html")


@router.get('/dashboard-activity.js', include_in_schema=False)
async def dashboard_activity_js():
    return FileResponse(GATEWAY_DIR / 'dashboard-activity.js', media_type='application/javascript')


@router.get('/neon-neural-v25.png', include_in_schema=False)
@router.get('/neon-neural-v25.mp4', include_in_schema=False)
async def neon_dashboard_asset(request: Request):
    name = request.url.path.lstrip('/')
    if name not in {'neon-neural-v25.png', 'neon-neural-v25.mp4'}:
        raise HTTPException(404, 'Asset not found')
    path = GATEWAY_DIR / 'assets' / name
    if not path.is_file():
        raise HTTPException(404, 'Asset not rendered')
    return FileResponse(path, media_type='video/mp4' if name.endswith('.mp4') else 'image/png')


@router.get("/companion", include_in_schema=False)
async def companion_ui():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion.html"), media_type="text/html")


@router.get("/companion-manifest.json", include_in_schema=False)
async def companion_manifest():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion-manifest.json"), media_type="application/manifest+json")


@router.get("/companion-sw.js", include_in_schema=False)
async def companion_sw():
    return FileResponse(os.path.join(str(GATEWAY_DIR), "companion-sw.js"), media_type="application/javascript")
