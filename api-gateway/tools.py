"""Local-first tool layer for the AI System.

All tools are explicit, auditable functions. Destructive/security-sensitive
operations are never executed by the LLM directly; callers must request
confirmation first.
"""
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

HOME_ROOTS = [Path(p) for p in os.getenv("AI_ALLOWED_PATHS", str(Path.home())).split(os.pathsep) if p]
HA_URL = os.getenv("HOME_ASSISTANT_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HOME_ASSISTANT_TOKEN", "")
HOST_BRIDGE_URL = os.getenv("AI_HOST_BRIDGE_URL", "http://host.docker.internal:8765").rstrip("/")
HOST_BRIDGE_TOKEN = os.getenv("AI_HOST_BRIDGE_TOKEN", "")


def _allowed(path: Path) -> bool:
    try:
        p = path.resolve()
        return any(p == r.resolve() or r.resolve() in p.parents for r in HOME_ROOTS)
    except Exception:
        return False


def _path(value: str) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(value)))
    if not p.is_absolute():
        p = Path.home() / p
    return p


def file_search(query: str, root: str = "", limit: int = 30) -> list[dict[str, Any]]:
    base = _path(root) if root else HOME_ROOTS[0]
    if not _allowed(base): raise PermissionError("path is outside AI_ALLOWED_PATHS")
    q = query.lower().strip(); out=[]
    for p in base.rglob("*"):
        if len(out) >= max(1, min(limit, 100)): break
        try:
            if q in p.name.lower(): out.append({"path": str(p), "is_dir": p.is_dir(), "size": p.stat().st_size if p.is_file() else None})
        except (OSError, PermissionError): pass
    return out


def file_content_search(query: str, root: str = "", limit: int = 20, max_bytes: int = 200_000) -> list[dict[str, Any]]:
    """Search text files under an allowed root without executing or indexing them."""
    base = _path(root) if root else HOME_ROOTS[0]
    if not _allowed(base): raise PermissionError("path is outside AI_ALLOWED_PATHS")
    q = query.lower().strip()
    if not q: return []
    out=[]
    for p in base.rglob("*"):
        if len(out) >= max(1, min(limit, 50)): break
        try:
            if not p.is_file() or p.stat().st_size > max_bytes: continue
            if p.suffix.lower() not in {".txt",".md",".csv",".json",".log",".py",".js",".ts",".html",".css",".yaml",".yml",".xml"}: continue
            text=p.read_text(encoding="utf-8", errors="replace")
            if q in text.lower():
                idx=text.lower().find(q); start=max(0,idx-120); end=min(len(text),idx+len(q)+220)
                out.append({"path":str(p),"snippet":text[start:end]})
        except (OSError, PermissionError): pass
    return out


def read_file(path: str, max_bytes: int = 200_000) -> dict[str, Any]:
    p=_path(path)
    if not _allowed(p): raise PermissionError("path is outside AI_ALLOWED_PATHS")
    if not p.is_file(): raise FileNotFoundError(str(p))
    data=p.read_bytes()[:max_bytes]
    return {"path":str(p),"content":data.decode("utf-8", errors="replace"),"truncated":p.stat().st_size>max_bytes}


def open_file(path: str) -> dict[str, Any]:
    p=_path(path)
    if not _allowed(p): raise PermissionError("path is outside AI_ALLOWED_PATHS")
    if platform.system()=="Windows": os.startfile(str(p))
    elif platform.system()=="Darwin": subprocess.Popen(["open",str(p)])
    else: subprocess.Popen(["xdg-open",str(p)])
    return {"ok":True,"path":str(p)}


def write_text_file(path: str, content: str, overwrite: bool=False) -> dict[str, Any]:
    p=_path(path)
    if not _allowed(p): raise PermissionError("path is outside AI_ALLOWED_PATHS")
    if p.exists() and not overwrite: raise FileExistsError(str(p))
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding="utf-8")
    return {"ok":True,"path":str(p),"bytes":len(content.encode('utf-8'))}


async def host_open_file(path: str) -> dict[str, Any]:
    if not HOST_BRIDGE_URL or not HOST_BRIDGE_TOKEN:
        raise RuntimeError("Windows host bridge is not configured")
    headers={"Authorization":f"Bearer {HOST_BRIDGE_TOKEN}"}
    async with httpx.AsyncClient(timeout=10) as c:
        r=await c.post(f"{HOST_BRIDGE_URL}/open",json={"path":path},headers=headers)
        r.raise_for_status(); return r.json()


async def ha_call(domain: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
    if not HA_URL or not HA_TOKEN: raise RuntimeError("Home Assistant is not configured")
    if not re.fullmatch(r"[a-z_]+", domain) or not re.fullmatch(r"[a-z_]+", service): raise ValueError("invalid Home Assistant service")
    headers={"Authorization":f"Bearer {HA_TOKEN}","Content-Type":"application/json"}
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.post(f"{HA_URL}/api/services/{domain}/{service}",json=data,headers=headers)
        r.raise_for_status(); return {"ok":True,"domain":domain,"service":service,"result":r.json()}

async def ha_entities(domains: list[str] | None = None) -> list[dict[str, Any]]:
    """Return a compact, safe device inventory for agent selection."""
    states = await ha_states()
    allowed_domains = set(domains or ["light","switch","fan","climate","media_player"])
    out=[]
    for item in states:
        entity_id=item.get("entity_id","")
        domain=entity_id.split(".",1)[0] if "." in entity_id else ""
        if domain in allowed_domains:
            attrs=item.get("attributes") or {}
            out.append({"entity_id":entity_id,"state":item.get("state"),"name":attrs.get("friendly_name",entity_id),"supported_features":attrs.get("supported_features",0),"volume_level":attrs.get("volume_level"),"temperature":attrs.get("temperature")})
    return out

async def ha_states() -> list[dict[str,Any]]:
    if not HA_URL or not HA_TOKEN: raise RuntimeError("Home Assistant is not configured")
    headers={"Authorization":f"Bearer {HA_TOKEN}"}
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.get(f"{HA_URL}/api/states",headers=headers); r.raise_for_status(); return r.json()

async def ha_device(entity_id: str, action: str, **kwargs) -> dict[str,Any]:
    """Perform a narrowly allow-listed Home Assistant action."""
    if not re.fullmatch(r"[a-z_]+\.[a-zA-Z0-9_]+", entity_id or ""):
        raise ValueError("invalid Home Assistant entity_id")
    domain = entity_id.split(".",1)[0]
    allowed = {
        "light":{"on":"turn_on","off":"turn_off"},
        "switch":{"on":"turn_on","off":"turn_off"},
        "fan":{"on":"turn_on","off":"turn_off"},
        "media_player":{"on":"turn_on","off":"turn_off","pause":"media_pause","play":"media_play","stop":"media_stop","volume":"volume_set"},
        "climate":{"temperature":"set_temperature"},
    }
    if domain not in allowed or action not in allowed[domain]:
        raise ValueError("unsupported device/action")
    permitted_args = {
        "light":{"on":{"brightness","brightness_pct"},"off":set()},
        "switch":{"on":set(),"off":set()},
        "fan":{"on":{"percentage"},"off":set()},
        "media_player":{"on":set(),"off":set(),"pause":set(),"play":set(),"stop":set(),"volume":{"volume_level"}},
        "climate":{"temperature":{"temperature"}},
    }
    allowed_args = permitted_args[domain][action]
    unknown = set(kwargs) - allowed_args
    if unknown:
        raise ValueError(f"unsupported arguments: {', '.join(sorted(unknown))}")
    if "volume_level" in kwargs and not 0 <= float(kwargs["volume_level"]) <= 1:
        raise ValueError("volume_level must be between 0 and 1")
    if "brightness_pct" in kwargs and not 0 <= float(kwargs["brightness_pct"]) <= 100:
        raise ValueError("brightness_pct must be between 0 and 100")
    if "brightness" in kwargs and not 1 <= int(kwargs["brightness"]) <= 255:
        raise ValueError("brightness must be between 1 and 255")
    if "percentage" in kwargs and not 0 <= int(kwargs["percentage"]) <= 100:
        raise ValueError("percentage must be between 0 and 100")
    if "temperature" in kwargs and not -50 <= float(kwargs["temperature"]) <= 50:
        raise ValueError("temperature is outside the supported safety range")
    service=allowed[domain][action]
    data={"entity_id":entity_id}; data.update(kwargs)
    return await ha_call(domain,service,data)


def pending_action(action: str, args: dict[str,Any], reason: str) -> dict[str,Any]:
    return {"status":"confirmation_required","action":action,"args":args,"reason":reason}

# Bridge read-only endpoints are GET; actions are POST.
_COMPUTER_GET_ENDPOINTS = {"/computer/capabilities", "/computer/processes", "/computer/windows"}

async def _computer_call(endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not HOST_BRIDGE_URL or not HOST_BRIDGE_TOKEN:
        raise RuntimeError("Windows computer bridge is not configured")
    headers={"Authorization":f"Bearer {HOST_BRIDGE_TOKEN}","Content-Type":"application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        if endpoint in _COMPUTER_GET_ENDPOINTS:
            r=await c.get(f"{HOST_BRIDGE_URL}{endpoint}",headers=headers)
        else:
            r=await c.post(f"{HOST_BRIDGE_URL}{endpoint}",json=payload or {},headers=headers)
        r.raise_for_status(); return r.json()

async def computer_status() -> dict[str, Any]:
    return await _computer_call("/computer/status")

async def computer_open(target: str) -> dict[str, Any]:
    if not target.strip(): raise ValueError("target is required")
    return await _computer_call("/computer/open", {"target":target})

async def computer_type(text: str, title: str = "", process: str = "", pid: int = 0) -> dict[str, Any]:
    if not text: raise ValueError("text is required")
    return await _computer_call("/computer/type", {"text":text,"title":title or "","process":process or "","pid":int(pid or 0)})

async def computer_focus(title: str = "", process: str = "", pid: int = 0) -> dict[str, Any]:
    if not (str(title or "").strip() or str(process or "").strip() or pid):
        raise ValueError("title, process, or pid is required")
    return await _computer_call("/computer/focus", {"title":title or "","process":process or "","pid":int(pid or 0)})

async def computer_verify(title: str = "", process: str = "", pid: int = 0) -> dict[str, Any]:
    return await _computer_call("/computer/verify", {"title":title or "","process":process or "","pid":int(pid or 0)})

async def computer_observe(max_width: int = 0, include_windows: bool = True, include_processes: bool = False) -> dict[str, Any]:
    return await _computer_call("/computer/observe", {"max_width":int(max_width or 0),"include_windows":bool(include_windows),"include_processes":bool(include_processes)})

async def computer_key(key: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_+\- ]{1,80}", key or ""): raise ValueError("invalid key/shortcut")
    return await _computer_call("/computer/key", {"key":key})

async def computer_screenshot() -> dict[str, Any]:
    return await _computer_call("/computer/screenshot")

async def computer_move(x: int, y: int, duration: float = 0.15) -> dict[str, Any]:
    return await _computer_call("/computer/move", {"x": int(x), "y": int(y), "duration": float(duration)})

async def computer_click(x: int, y: int, clicks: int = 1, button: str = "left") -> dict[str, Any]:
    return await _computer_call("/computer/click", {"x": int(x), "y": int(y), "clicks": int(clicks), "button": button})

async def computer_scroll(amount: int) -> dict[str, Any]:
    return await _computer_call("/computer/scroll", {"amount": int(amount)})

async def computer_capabilities() -> dict[str, Any]:
    return await _computer_call("/computer/capabilities")

async def computer_processes() -> dict[str, Any]:
    return await _computer_call("/computer/processes")

async def computer_windows() -> dict[str, Any]:
    return await _computer_call("/computer/windows")

async def computer_shell(command: str, timeout: int = 30) -> dict[str, Any]:
    if not command.strip(): raise ValueError("command is required")
    return await _computer_call("/computer/shell", {"command": command, "timeout": int(timeout)})

