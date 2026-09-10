"""Local project/workspace registry used by Jarvis Autopilot.

No secrets live here. Paths/URLs are supplied by environment variables so the
same build can run on the user's Windows machine, Docker, or another host.
"""
from __future__ import annotations

import ntpath
import os
from pathlib import Path
from typing import Any

PROJECTS = {
    "email_agent": {
        "label": "Email Agent",
        "path_env": "JARVIS_EMAIL_AGENT_PATH",
        "url_env": "EMAIL_AGENT_URL",
        "default_path": r"C:\Users\jr04b\email-agent-saas",
        "keywords": ("email agent", "email-agent", "email automation", "invoice"),
    },
    "ai_workforce": {
        "label": "AI Workforce",
        "path_env": "JARVIS_AI_WORKFORCE_PATH",
        "url_env": "AI_WORKFORCE_URL",
        "default_path": r"C:\Users\jr04b\ai-workforce",
        "keywords": ("ai workforce", "ai-workforce", "overseer", "workforce"),
    },
    "outbound_ai": {
        "label": "Outbound AI",
        "path_env": "JARVIS_OUTBOUND_AI_PATH",
        "url_env": "OUTBOUND_AI_URL",
        "default_path": r"C:\Users\jr04b\outbound-ai",
        "keywords": ("outbound ai", "outbound-ai", "call bot", "voice bot"),
    },
}


def _path_value(spec: dict[str, Any]) -> str:
    return os.getenv(spec["path_env"], spec["default_path"]).strip()


def match_target(goal: str) -> str | None:
    text = (goal or "").lower()
    for key, spec in PROJECTS.items():
        if any(k in text for k in spec["keywords"]):
            return key
    return None


def snapshot() -> list[dict[str, Any]]:
    result = []
    for key, spec in PROJECTS.items():
        raw = _path_value(spec)
        path = Path(raw)
        result.append({
            "id": key,
            "label": spec["label"],
            "path": raw,
            "path_exists": path.exists(),
            "path_is_directory": path.is_dir(),
            "url": os.getenv(spec["url_env"], "").strip() or None,
            "path_env": spec["path_env"],
            "url_env": spec["url_env"],
        })
    return result


def _tool_path(spec: dict[str, Any]) -> str:
    """Translate the standard Windows workspace into the Docker host-files mount when present."""
    raw = _path_value(spec)
    allowed = os.getenv("AI_ALLOWED_PATHS", "")
    if "/host-files" in allowed.replace(";", os.pathsep).split(os.pathsep):
        # Map C:\Users\<user>\project -> /host-files/project when the parent
        # Windows home is mounted by docker-compose.
        parts = raw.replace("/", "\\").split("\\")
        if len(parts) >= 4 and parts[0].endswith(":") and parts[1].lower() == "users":
            return "/host-files/" + "/".join(parts[3:])
    return raw


def context_for(goal: str) -> dict[str, Any]:
    target = match_target(goal)
    if not target:
        return {"target": None, "workspace": None}
    spec = PROJECTS[target]
    return {
        "target": target,
        "workspace": {
            "label": spec["label"],
            "path": _path_value(spec),
            "tool_path": _tool_path(spec),
            "url": os.getenv(spec["url_env"], "").strip() or None,
        },
    }


def get_target(target_id: str) -> dict[str, Any]:
    spec = PROJECTS.get((target_id or "").strip())
    if not spec:
        raise KeyError(target_id)
    return {
        "target": target_id,
        "workspace": {
            "label": spec["label"],
            "path": _path_value(spec),
            "tool_path": _tool_path(spec),
            "url": os.getenv(spec["url_env"], "").strip() or None,
        },
    }


def _canonical_workspace(path: str) -> str:
    """Normalize Windows and POSIX workspace paths without requiring existence."""
    raw = (path or "").strip()
    if not raw:
        return ""
    if re_windows_path(raw):
        return ntpath.normcase(ntpath.normpath(raw.replace("/", "\\")))
    return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))


def re_windows_path(path: str) -> bool:
    return len(path) >= 3 and path[1:3] in {":\\", ":/"}


def registered_workspace_paths() -> list[str]:
    """Return canonical registered roots. No secrets or file contents are exposed."""
    out: list[str] = []
    for spec in PROJECTS.values():
        for candidate in (_path_value(spec), _tool_path(spec)):
            canon = _canonical_workspace(candidate)
            if canon and canon not in out:
                out.append(canon)
    return out


def is_registered_workspace(path: str) -> bool:
    """Fail closed: OpenCode may only attach to an explicitly registered project root."""
    candidate = _canonical_workspace(path)
    return bool(candidate) and candidate in set(registered_workspace_paths())


def target_for_workspace(path: str) -> str | None:
    """Resolve an exact registered workspace root back to its target id."""
    candidate = _canonical_workspace(path)
    if not candidate:
        return None
    for target_id, spec in PROJECTS.items():
        for registered in (_path_value(spec), _tool_path(spec)):
            if candidate == _canonical_workspace(registered):
                return target_id
    return None
