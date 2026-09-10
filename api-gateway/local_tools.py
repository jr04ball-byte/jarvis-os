"""Safe Windows local-tool discovery for the AI System.

Discovery only: this module never executes a discovered creative tool. It reports
installed applications and the capabilities that the AI System can use once a
corresponding plugin is enabled.
"""
import os
import platform
import re
import shutil
import subprocess
import time
import json
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("AI_SYSTEM_ROOT", Path(__file__).resolve().parents[1]))


def _candidates(env_names: list[str], paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for name in env_names:
        value = os.getenv(name, "").strip().strip('"')
        if value:
            out.append(Path(value))
    for raw in paths:
        out.append(Path(os.path.expandvars(raw)))
    return out


def _first_file(paths: list[Path], names: list[str] | None = None) -> Path | None:
    for p in paths:
        if p.is_file():
            return p
        if p.is_dir() and names:
            for name in names:
                candidate = p / name
                if candidate.is_file():
                    return candidate
    return None


def _which(names: list[str]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _ue_version_from_build_file(executable: Path) -> str | None:
    """Read UE version from Engine/Build/Build.version without executing the editor.

    Running `UnrealEditor.exe --version` boots the full editor (~50s, heavy GPU/
    shader/Zen init) and must never be used for discovery probing.
    """
    try:
        # .../Engine/Binaries/Win64/UnrealEditor.exe -> .../Engine/Build/Build.version
        engine_dir = executable.parents[2]  # Binaries/Win64 -> Engine
        build_version = engine_dir / "Build" / "Build.version"
        if build_version.is_file():
            data = json.loads(build_version.read_text(encoding="utf-8-sig"))
            major = data.get("MajorVersion")
            minor = data.get("MinorVersion")
            patch = data.get("PatchVersion")
            if major is not None and minor is not None:
                return f"{major}.{minor}.{patch}" if patch is not None else f"{major}.{minor}"
        # Fallback: folder name like UE_5.8
        m = re.search(r"UE_(\d+\.\d+)", str(executable))
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _version(executable: Path, args: list[str] | None = None) -> str | None:
    """Read a version string from an allowlisted executable with a short timeout.

    NEVER executes UnrealEditor.exe or .bat/.cmd launchers: UE boots the full
    editor even for `--version`, and batch files start servers.
    """
    try:
        name = executable.name.lower()
        if name in {"unrealeditor.exe", "unrealeditor"}:
            return _ue_version_from_build_file(executable)
        if executable.suffix.lower() in {".bat", ".cmd", ".ps1"}:
            return None
        cp = subprocess.run([str(executable), *(args or ["--version"])], capture_output=True,
                            text=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (cp.stdout or cp.stderr or "").strip().splitlines()
        if not text:
            return None
        line = text[0].strip()
        return line[:160] if line else None
    except Exception:
        return None


def _tool(name: str, executable: Path | None, kind: str, capabilities: list[str], notes: str = "") -> dict[str, Any]:
    item = {
        "name": name,
        "kind": kind,
        "installed": bool(executable),
        "status": "ready" if executable else "not_found",
        "executable": str(executable) if executable else None,
        "version": _version(executable) if executable else None,
        "capabilities": capabilities,
    }
    if notes:
        item["notes"] = notes
    return item


def _scan_uncached() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {"platform": platform.system(), "tools": [], "message": "Local tool discovery is currently Windows-focused."}

    pf = os.getenv("ProgramFiles", r"C:\\Program Files")
    pfx86 = os.getenv("ProgramFiles(x86)", r"C:\\Program Files (x86)")
    local = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    appdata = os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    user = str(Path.home())

    blender = _which(["blender.exe", "blender"]) or _first_file(_candidates(
        ["BLENDER_EXE", "BLENDER_PATH"], [
            rf"{pf}\\Blender Foundation\\Blender\\blender.exe",
            rf"{pfx86}\\Blender Foundation\\Blender\\blender.exe",
            rf"{local}\\Programs\\Blender Foundation\\Blender\\blender.exe",
            rf"{user}\\scoop\\apps\\blender\\current\\blender.exe",
        ]))
    if not blender:
        # Blender 4.x/5.x installs versioned subfolders, e.g. Blender Foundation\Blender 5.2\blender.exe
        for base in [Path(pf) / "Blender Foundation", Path(pfx86) / "Blender Foundation"]:
            try:
                if base.is_dir():
                    cands = sorted(base.glob("Blender */blender.exe"), reverse=True) + sorted(base.glob("Blender/blender.exe"))
                    if cands:
                        blender = cands[0]
                        break
            except OSError:
                pass

    # Epic Launcher installs side-by-side UE versions under Program Files/Epic Games.
    ue_paths: list[Path] = []
    for base in [Path(pf) / "Epic Games", Path(pfx86) / "Epic Games", Path("C:/Epic Games")]:
        if base.is_dir():
            try:
                ue_paths.extend(sorted(base.glob("UE_*\\Engine\\Binaries\\Win64\\UnrealEditor.exe"), reverse=True))
            except OSError:
                pass
    ue = _which(["UnrealEditor.exe", "UnrealEditor"]) or _first_file(_candidates(
        ["UNREAL_EDITOR_EXE", "UNREAL_ENGINE_EXE"], [])) or (ue_paths[0] if ue_paths else None)

    ffmpeg = _which(["ffmpeg.exe", "ffmpeg"]) or _first_file(_candidates(
        ["FFMPEG_EXE", "FFMPEG_PATH"], [rf"{user}\\ffmpeg\\bin\\ffmpeg.exe", rf"{pf}\\ffmpeg\\bin\\ffmpeg.exe"]))

    ollama = _which(["ollama.exe", "ollama"]) or _first_file(_candidates(
        ["OLLAMA_EXE"], [rf"{local}\\Programs\\Ollama\\ollama.exe", rf"{pf}\\Ollama\\ollama.exe"]))

    comfy = _which(["ComfyUI", "ComfyUI.exe"]) or _first_file(_candidates(
        ["COMFYUI_EXE"], [rf"{user}\\ComfyUI\\ComfyUI.exe", rf"{user}\\ComfyUI\\run_nvidia_gpu.bat"]))

    python = _which(["python.exe", "python"])

    tools = [
        _tool("Blender", blender, "creative", ["project", "scene", "camera", "lighting", "animation", "render", "export"],
              "Plugin automation will use Blender's background Python API; no arbitrary command execution is exposed to the model."),
        _tool("Unreal Engine 5", ue, "creative", ["project", "level", "assets", "camera", "lighting", "sequencer", "movie_render_queue", "render", "export"],
              "Plugin automation will use approved Unreal Editor/Python/Movie Render Queue workflows."),
        _tool("ComfyUI", comfy, "creative", ["image_generation", "image_assets", "workflow_execution"],
              "Detected as an optional local image-generation backend."),
        _tool("FFmpeg", ffmpeg, "media", ["trim", "concat", "transcode", "mux", "audio", "captions"],
              "Used by the media plugin for deterministic video assembly."),
        _tool("Ollama", ollama, "ai", ["local_llm", "chat", "tool_planning"],
              "Core local model runtime."),
        _tool("Python", python, "runtime", ["automation", "scripts"],
              "Local automation runtime."),
    ]
    installed = [t for t in tools if t["installed"]]
    return {
        "platform": "Windows",
        "tool_count": len(tools),
        "installed_count": len(installed),
        "tools": tools,
        "orchestration": {
            "mode": "capability-first",
            "policy": "The AI chooses tools from discovered capabilities; it does not require the user to name the application.",
            "example": "create a video using all the tools available on this PC",
        },
    }

_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL = 300.0  # Dashboard polls frequently; don't respawn probes each time.

def scan_local_tools() -> dict[str, Any]:
    now = time.monotonic()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]
    data = _scan_uncached()
    _CACHE["ts"] = now
    _CACHE["data"] = data
    return data
