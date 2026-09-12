"""Blender creative worker for Jarvis OS.

Discovers Blender, generates request-driven scene Python scripts,
runs Blender in background mode through a worker process, captures
progress/stdout/exit, verifies .blend and rendered image files exist,
and returns working artifact links.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from deps import ARTIFACTS_DIR
from local_tools import _which, _first_file, _candidates, _version

logger = logging.getLogger(__name__)

_BLENDER_CAPABILITIES = [
    "project", "scene", "camera", "lighting", "animation", "render", "export",
]


def _find_blender() -> Path | None:
    """Discover Blender executable on the system."""
    blender = _which(["blender.exe", "blender"]) or _first_file(
        _candidates(
            ["BLENDER_EXE", "BLENDER_PATH"],
            [
                rf"{os.getenv('ProgramFiles', r'C:\\Program Files')}\\Blender Foundation\\Blender\\blender.exe",
                rf"{os.getenv('ProgramFiles(x86)', r'C:\\Program Files (x86)')}\\Blender Foundation\\Blender\\blender.exe",
                rf"{os.getenv('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))}\\Programs\\Blender Foundation\\Blender\\blender.exe",
                rf"{Path.home()}\\scoop\\apps\\blender\\current\\blender.exe",
            ],
        )
    )
    if not blender:
        for base in [Path(os.getenv('ProgramFiles', r'C:\\Program Files')), Path(os.getenv('ProgramFiles(x86)', r'C:\\Program Files (x86)'))]:
            if base.is_dir():
                cands = sorted((base / "Blender Foundation").glob("Blender */blender.exe"), reverse=True)
                cands += sorted(base.glob("Blender */blender.exe"), reverse=True) + sorted(base.glob("Blender/blender.exe"))
                if cands:
                    blender = cands[0]
                    break
    return blender


def _parse_goal(goal: str) -> dict[str, Any]:
    """Parse a natural-language goal into structured scene parameters."""
    lower = goal.lower().strip()
    scene_type = "general"
    subject = "scene"
    color = "blue"
    lighting = "studio"
    camera_angle = "default"
    animation = None

    if any(w in lower for w in ["landscape", "mountain", "nature", "forest", "mountain"]):
        scene_type = "landscape"
        subject = "mountain landscape"
        color = "green"
        lighting = "golden_hour"
    elif any(w in lower for w in ["city", "urban", "skyline", "building"]):
        scene_type = "city"
        subject = "city skyline"
        color = "gray"
        lighting = "dusk"
    elif any(w in lower for w in ["room", "interior", "living", "kitchen", "bedroom"]):
        scene_type = "interior"
        subject = "interior room"
        color = "warm"
        lighting = "interior"
    elif any(w in lower for w in ["water", "ocean", "sea", "lake", "pool"]):
        scene_type = "water"
        subject = "water surface"
        color = "cyan"
        lighting = "outdoor"
    elif any(w in lower for w in ["abstract", "geometric", "modern"]):
        scene_type = "abstract"
        subject = "abstract geometric form"
        color = "metallic"
        lighting = "dramatic"
    elif any(w in lower for w in ["sunset", "dawn", "dusk", "golden"]):
        scene_type = "outdoor"
        subject = "sunset scene"
        color = "orange"
        lighting = "golden_hour"

    if any(w in lower for w in ["animate", "animation", "moving", "rotate", "spin"]):
        animation = "rotate"
    if any(w in lower for w in ["close up", "macro", "detail", "tight"]):
        camera_angle = "close"
    elif any(w in lower for w in ["wide", "panoramic", "wide angle", "drone"]):
        camera_angle = "wide"

    return {
        "scene_type": scene_type,
        "subject": subject,
        "color": color,
        "lighting": lighting,
        "camera_angle": camera_angle,
        "animation": animation,
    }


def _generate_blender_script(goal: str, params: dict[str, Any], output_path: str) -> str:
    """Generate a Blender Python script based on the user's request and parsed parameters."""
    safe_desc = re.sub(r'[^a-zA-Z0-9_\s]', '', goal).strip()[:80]
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', safe_desc).strip('_')
    subject = params["subject"]
    color = params["color"]
    lighting = params["lighting"]
    camera_angle = params["camera_angle"]
    animation = params["animation"]
    scene_type = params["scene_type"]

    camera_pos = "(5, -5, 3)"
    if camera_angle == "close":
        camera_pos = "(2, -2, 1.5)"
    elif camera_angle == "wide":
        camera_pos = "(15, -15, 8)"

    obj_code = f'''# Clear default objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# Create scene: {subject} based on request: {safe_desc}'''

    if scene_type == "landscape":
        obj_code += '''
# Add terrain-like landscape
for i in range(5):
    bpy.ops.mesh.primitive_plane_add(size=3 + i*0.5, location=(i*2 - 4, 0, 0))
    bpy.ops.object.select_all(action='SELECT')
'''
    elif scene_type == "city":
        obj_code += '''
# Add building-like structures
for i in range(4):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(i*2 - 3, 0, 1))
    bpy.ops.object.select_all(action='SELECT')
'''
    elif scene_type == "water":
        obj_code += '''
# Add water-like plane
bpy.ops.mesh.primitive_plane_add(size=5, location=(0, 0, 0))
bpy.ops.object.select_all(action='SELECT')
'''
    elif scene_type == "abstract":
        obj_code += '''
# Add abstract geometric forms
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.5, location=(0, 0, 1))
bpy.ops.object.select_all(action='SELECT')
bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=3, location=(2, 0, 1.5))
bpy.ops.object.select_all(action='SELECT')
'''
    else:
        obj_code += '''
# Add primary subject
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
bpy.ops.object.select_all(action='SELECT')
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.8, location=(2, 0, 1))
bpy.ops.object.select_all(action='SELECT')
'''

    if animation:
        obj_code += f'''
# Add animation: {animation}
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 100
bpy.ops.object.select_all(action='SELECT')
'''

    script = f'''# Auto-generated Blender scene: {safe_desc}
# Subject: {subject} | Scene type: {scene_type} | Lighting: {lighting}
import bpy
import os
import math

{obj_code}

# Configure lighting: {lighting}
bpy.ops.object.light_add(type='{"SUN" if lighting == "golden_hour" else "POINT"}', location=(3, -3, 5))
light = bpy.context.active_object
light.data.energy = 3.0

bpy.ops.object.light_add(type='AREA', location=(0, 0, 5))
fill = bpy.context.active_object
fill.data.energy = 0.5

# Add camera
bpy.ops.object.camera_add(location={camera_pos}, rotation=(1.1, 0, 0.8))
camera = bpy.context.active_object
bpy.context.scene.camera = camera

# Configure render settings
bpy.context.scene.render.engine = 'CYCLES'
bpy.context.scene.cycles.samples = 64
bpy.context.scene.render.resolution_x = 1920
bpy.context.scene.render.resolution_y = 1080
bpy.context.scene.render.image_settings.file_format = 'PNG'
bpy.context.scene.render.filepath = r'{output_path.replace(os.sep, "/")}_render'

# Save the .blend file
output_path = r'{output_path}'
os.makedirs(os.path.dirname(output_path), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=output_path)

# Render the scene
bpy.ops.render.render(write_still=True)

print('Blender scene generated and rendered successfully.')
print('Blend file:', output_path)
'''
    return script


async def run_blender_work(goal: str, artifacts_dir: str | None = None) -> dict[str, Any]:
    """Run a Blender creative task end-to-end.

    1. Discovers Blender executable
    2. Generates a scene script from the goal using parsed parameters
    3. Runs Blender in background mode as a worker process
    4. Captures progress, stdout, and exit code
    5. Verifies .blend and rendered image files exist
    6. Returns working artifact links
    """
    task_id = uuid.uuid4().hex[:8]
    result: dict[str, Any] = {
        "status": "failed",
        "goal": goal,
        "task_id": task_id,
        "blender_found": False,
        "blender_version": None,
        "blend_path": None,
        "render_path": None,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "elapsed_ms": 0,
        "progress": 0,
        "error": None,
    }

    # Step 1: Discover Blender
    blender = _find_blender()
    if not blender:
        result["error"] = "Blender executable not found on this system"
        return result
    result["blender_found"] = True
    result["blender_version"] = _version(blender)
    try:
        from PIL import Image
    except ImportError:
        result["error"] = "Pillow is missing from the gateway Python; install api-gateway/requirements.txt"
        return result

    # Step 2: Prepare unique per-task output directory
    artifacts_dir = Path(artifacts_dir or str(ARTIFACTS_DIR)).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    task_dir = artifacts_dir / f"task_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)
    blend_path = task_dir / "scene.blend"
    render_path = task_dir / "render.png"
    script_path = task_dir / "scene.py"
    result["blend_path"] = str(blend_path)
    result["render_path"] = str(render_path)

    # Step 3: Generate request-driven script
    from creative_script import generate
    try:
        script = await generate(goal, blend_path, render_path)
    except Exception as exc:
        result["error"] = f"Scene generation failed: {exc}"
        return result
    script_path.write_text(script, encoding='utf-8')

    # Step 4: Run Blender in background mode
    started = time.time()
    process = None
    try:
        cmd = [str(blender), "--background", "--python-exit-code", "1", "--python", str(script_path)]
        result["attempts"] = []
        for attempt in range(2):
            cp = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process = cp
            stdout_bytes, stderr_bytes = await asyncio.wait_for(cp.communicate(), timeout=300)
            result["stdout"] = stdout_bytes.decode("utf-8", errors="replace")
            result["stderr"] = stderr_bytes.decode("utf-8", errors="replace")
            result["exit_code"] = cp.returncode
            result["attempts"].append({"exit_code": cp.returncode, "stderr": result["stderr"][-3000:]})
            if cp.returncode == 0 or attempt or 'Traceback' not in result["stderr"]:
                break
            # One bounded repair of this task's generated script; never rerun
            # unrelated computer actions or reuse artifacts from a failed attempt.
            repair_goal = (goal + '\nRepair the following Blender script using the actual runtime error. '
                           'Return the entire corrected scene.\nScript:\n' + script +
                           '\nRuntime error:\n' + result["stderr"][-3000:])
            script = await generate(repair_goal, blend_path, render_path)
            script_path.write_text(script, encoding='utf-8')
            blend_path.unlink(missing_ok=True)
            render_path.unlink(missing_ok=True)
        result["elapsed_ms"] = int((time.time() - started) * 1000)
    except asyncio.CancelledError:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise
    except asyncio.TimeoutError:
        result["error"] = "Blender rendering timed out after 5 minutes"
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=10)
            except Exception:
                try:
                    process.kill()
                    await process.wait()
                except Exception as exc:
                    logger.debug("blender kill during timeout cleanup failed: %s", exc)
        return result
    except Exception as exc:
        result["error"] = f"Blender process failed: {exc}"
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        return result

    # Step 6: Verify artifacts exist and are valid
    blend_exists = blend_path.exists() and blend_path.stat().st_size > 0
    render_exists = render_path.exists() and render_path.stat().st_size > 0

    # Validate render image is readable
    render_valid = False
    if render_exists:
        try:
            img = Image.open(str(render_path))
            img.verify()
            render_valid = True
        except Exception:
            render_valid = False

    if blend_exists and render_valid and result["exit_code"] == 0:
        result["status"] = "success"
        result["progress"] = 100
    elif blend_exists and not render_valid:
        result["status"] = "partial"
        result["error"] = "Blender created .blend file but render image is invalid or missing"
    elif result["exit_code"] != 0:
        result["error"] = f"Blender exited with code {result['exit_code']}: {result.get('stderr', '')[:500]}"
        result["status"] = "failed"
    else:
        result["error"] = "Blender process completed but artifacts were not verified"
        result["status"] = "partial"

    # Clean up stale artifacts: if this run failed, don't let old files satisfy verification
    if result["status"] != "success":
        for stale in task_dir.glob("*.png"):
            if stale != render_path:
                stale.unlink(missing_ok=True)

    if result["status"] == "success":
        result["artifacts"] = [
            {"name": "render.png", "url": f"/v1/creative-files/{task_id}/render.png"},
            {"name": "scene.blend", "url": f"/v1/creative-files/{task_id}/scene.blend"},
        ]
    logger.info("Blender work completed: %s", result["status"])
    return result


async def blender_inventory() -> dict[str, Any]:
    """Return Blender discovery status and capabilities."""
    blender = _find_blender()
    return {
        "installed": blender is not None,
        "executable": str(blender) if blender else None,
        "version": _version(blender) if blender else None,
        "capabilities": _BLENDER_CAPABILITIES,
        "status": "ready" if blender else "not_found",
    }
