import os, re, secrets, subprocess, sys, time, typing
from typing import Any
import ctypes
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn


# V19 Windows Computer Control additions.
def _load_local_env():
    env_path = Path(__file__).resolve().parents[1] / '.env'
    if not env_path.exists(): return
    for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); k=k.strip(); v=v.strip().strip('\"').strip("'")
        os.environ.setdefault(k,v)
_load_local_env()
COMPUTER_ADMIN = os.getenv('AI_COMPUTER_ADMIN','false').lower() == 'true'

TOKEN = os.getenv('AI_HOST_BRIDGE_TOKEN', '')
PORT = int(os.getenv('AI_HOST_BRIDGE_PORT', '8765'))
HOST_FILES = Path(os.getenv('AI_HOST_FILES', '')).expanduser().resolve() if os.getenv('AI_HOST_FILES') else None
BIND_HOST = os.getenv('AI_HOST_BRIDGE_BIND', '127.0.0.1')
AUDIT_PATH = Path(os.getenv('AI_HOST_BRIDGE_AUDIT_PATH', str(Path(__file__).resolve().parent / 'data' / 'host-bridge-audit.jsonl')))
_DESKTOP_LOCK = threading.Lock()
_ACTION_LOCK = threading.Lock()
_PENDING_ACTIONS: dict[str, Any] = {}
_ACTION_TTL_SECONDS = 300


class ActionSerialization:
    """Serialize computer actions with timeout and cancellation support."""

    @staticmethod
    def submit(action: str, params: dict, timeout: int = 60) -> str:
        import uuid
        serial_id = uuid.uuid4().hex[:16]
        with _ACTION_LOCK:
            _PENDING_ACTIONS[serial_id] = {
                "action": action, "params": params, "timeout": timeout,
                "created_at": time.time(), "status": "queued",
                "result": None, "error": None, "cancelled": False,
            }
        return serial_id

    @staticmethod
    def status(serial_id: str) -> dict | None:
        with _ACTION_LOCK:
            item = _PENDING_ACTIONS.get(serial_id)
            if not item:
                return None
            return dict(item)

    @staticmethod
    def cancel(serial_id: str) -> bool:
        with _ACTION_LOCK:
            item = _PENDING_ACTIONS.get(serial_id)
            if not item or item["status"] in ("completed", "failed", "cancelled"):
                return False
            if item["cancelled"]:
                return True
            item["cancelled"] = True
            item["status"] = "cancelled"
            return True

    @staticmethod
    def complete(serial_id: str, result: dict, error: str | None = None) -> None:
        with _ACTION_LOCK:
            item = _PENDING_ACTIONS.get(serial_id)
            if not item:
                return
            item["status"] = "failed" if error else "completed"
            item["result"] = result
            item["error"] = error
            item["completed_at"] = time.time()

    @staticmethod
    def reap_expired() -> int:
        now = time.time()
        with _ACTION_LOCK:
            expired = [k for k, v in _PENDING_ACTIONS.items()
                       if now - v["created_at"] > _ACTION_TTL_SECONDS]
            for k in expired:
                _PENDING_ACTIONS.pop(k, None)
            return len(expired)


_ACTION_SERIALIZER = ActionSerialization()

app = FastAPI(title='AI System Windows Host Bridge', version='1.0.0')

class OpenRequest(BaseModel):
    path: str

def auth(value: str | None):
    if not TOKEN or not value or not secrets.compare_digest(value, f'Bearer {TOKEN}'):
        raise HTTPException(401, 'host bridge authentication required')

@app.get('/health')
def health():
    return {'ok': True, 'platform': sys.platform}

@app.post('/open')
def open_file(req: OpenRequest, authorization: str | None = Header(default=None)):
    auth(authorization)
    raw = os.path.expandvars(os.path.expanduser(req.path))
    if not HOST_FILES:
        raise HTTPException(503, 'AI_HOST_FILES is not configured')
    if raw == '/host-files':
        p = HOST_FILES
    elif raw.startswith('/host-files/'):
        p = (HOST_FILES / raw[len('/host-files/'):]).resolve()
    else:
        # Deny arbitrary host paths. Callers must use the /host-files namespace.
        raise HTTPException(403, 'only /host-files paths are allowed')
    if HOST_FILES not in p.parents and p != HOST_FILES:
        raise HTTPException(403, 'path outside configured host files')
    if not p.exists():
        raise HTTPException(404, str(p))
    if sys.platform != 'win32':
        raise HTTPException(400, 'Windows host bridge must run on Windows')
    os.startfile(str(p))
    return {'ok': True, 'path': str(p)}



class ClickRequest(BaseModel):
    x: int
    y: int
    clicks: int = 1
    button: str = 'left'

class MoveRequest(BaseModel):
    x: int
    y: int
    duration: float = 0.15

class ScrollRequest(BaseModel):
    amount: int

class ShellRequest(BaseModel):
    command: str
    timeout: int = 30

class ProcessRequest(BaseModel):
    name: str = ''

class BrowserRequest(BaseModel):
    url: str = ''
    query: str = ''
    browser: str = 'chrome'
    visible: bool = True


def _audit(action: str, *, ok: bool, details: dict | None = None):
    """Append a bounded local action record without command text, page queries, or secrets."""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {'ts': time.time(), 'action': action, 'ok': bool(ok), **(details or {})}
        with AUDIT_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + '\n')
    except Exception:
        pass



def _audit_sensitive(action: str, *, ok: bool, target: str = '', started: float | None = None):
    """Audit a sensitive computer action with timing metadata (no secrets/commands)."""
    details: dict[str, Any] = {}
    if target:
        details['target'] = str(target)[:200]
    if started is not None:
        details['duration_ms'] = int((time.time() - started) * 1000)
    _audit(action, ok=ok, details=details)


@contextmanager
def _desktop_action(action: str):
    if not _DESKTOP_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'another visible desktop action is already running')
    try:
        yield
    finally:
        _DESKTOP_LOCK.release()


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        raise HTTPException(503, 'psutil is not installed')


def _computer_common(authorization):
    computer_auth(authorization)
    if sys.platform != 'win32':
        raise HTTPException(400, 'Windows computer control requires Windows')

@app.get('/computer/capabilities')
def computer_capabilities(authorization: str | None = Header(default=None)):
    _computer_common(authorization)
    import shutil, subprocess
    p=_psutil()
    nvidia=[]
    try:
        r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.used,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode==0:
            for line in r.stdout.splitlines():
                a=[x.strip() for x in line.split(',')]
                if len(a)>=4: nvidia.append({'name':a[0],'vram_mb':a[1],'used_mb':a[2],'temperature_c':a[3]})
    except Exception: pass
    turtle=[]
    try:
        r=subprocess.run(['powershell','-NoProfile','-Command',"Get-CimInstance Win32_SoundDevice | Select-Object Name,Status | ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import json
        data=json.loads(r.stdout) if r.stdout.strip() else []
        if isinstance(data,dict): data=[data]
        turtle=[x for x in data if 'turtle' in str(x.get('Name','')).lower() or 'xbox' in str(x.get('Name','')).lower()]
    except Exception: pass
    broadcast=any(Path(x).exists() for x in [Path(os.getenv('ProgramFiles','C:/Program Files'))/'NVIDIA Corporation/NVIDIA Broadcast/NVIDIA Broadcast.exe', Path(os.getenv('ProgramFiles(x86)','C:/Program Files (x86)'))/'NVIDIA Corporation/NVIDIA Broadcast/NVIDIA Broadcast.exe'])
    pa=False
    try:
        r=subprocess.run(['powershell','-NoProfile','-Command',"Get-AppxPackage -Name Microsoft.PowerAutomateDesktop | Select-Object Name,Version | ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pa='Microsoft.PowerAutomateDesktop' in r.stdout
    except Exception: pass
    return {'ok':True,'admin':COMPUTER_ADMIN,'pyautogui':_pyautogui_available(),'psutil':True,'pywinauto':_module_available('pywinauto'),'mss':_module_available('mss'),'nvidia':nvidia,'turtle_beach_or_xbox_audio':turtle,'nvidia_broadcast':broadcast,'power_automate_desktop':pa,'cpu_percent':p.cpu_percent(interval=0.05),'ram_percent':p.virtual_memory().percent}

def _module_available(name):
    try:
        __import__(name); return True
    except Exception: return False

@app.post('/computer/move')
def computer_move(req: MoveRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui(); started=time.time()
    pyautogui.moveTo(req.x,req.y,duration=max(0,min(req.duration,2)))
    _audit_sensitive('computer_move', ok=True, target=f'{req.x},{req.y}', started=started)
    return {'ok':True,'x':req.x,'y':req.y}

@app.post('/computer/click')
def computer_click(req: ClickRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui(); started=time.time()
    if req.button not in {'left','right','middle'}: raise HTTPException(400,'invalid mouse button')
    pyautogui.click(req.x,req.y,clicks=max(1,min(req.clicks,3)),button=req.button,interval=0.08)
    _audit_sensitive('computer_click', ok=True, target=f'{req.x},{req.y}:{req.button}', started=started)
    return {'ok':True,'x':req.x,'y':req.y,'clicks':req.clicks,'button':req.button}

@app.post('/computer/scroll')
def computer_scroll(req: ScrollRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui(); started=time.time()
    pyautogui.scroll(max(-20,min(req.amount,20)))
    _audit_sensitive('computer_scroll', ok=True, target=str(req.amount), started=started)
    return {'ok':True,'amount':req.amount}

@app.post('/computer/shell')
def computer_shell(req: ShellRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization)
    if not SHELL_ENABLED:
        raise HTTPException(403, 'computer shell is disabled; set AI_COMPUTER_SHELL_ENABLED=true to opt in')
    if len(req.command)>4000: raise HTTPException(400,'command too long')
    # Shell is deliberately opt-in and runs under the bridge account/elevation.
    started = time.time()
    r=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-Command',req.command],capture_output=True,text=True,timeout=max(1,min(req.timeout,120)), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _audit('computer_shell', ok=r.returncode==0,
           details={'returncode': r.returncode, 'duration_ms': int((time.time()-started)*1000)})
    return {'ok':r.returncode==0,'returncode':r.returncode,'stdout':r.stdout[-20000:],'stderr':r.stderr[-10000:]}

@app.get('/computer/processes')
def computer_processes(authorization: str | None = Header(default=None)):
    _computer_common(authorization); p=_psutil(); out=[]
    for x in p.process_iter(['pid','name','username','memory_info']):
        try: out.append({'pid':x.info['pid'],'name':x.info['name'],'username':x.info['username'],'rss_mb':round((x.info['memory_info'].rss if x.info['memory_info'] else 0)/1048576,1)})
        except Exception: pass
    return {'ok':True,'processes':out[:300]}

@app.get('/computer/windows')
def computer_windows(authorization: str | None = Header(default=None)):
    _computer_common(authorization)
    try:
        out = _iter_windows()
        for w in out:
            w.update(zip(('left', 'top', 'right', 'bottom'), w['rect']))
        return {'ok':True,'windows':out[:200]}
    except Exception as e:
        return {'ok':False,'windows':[],'error':str(e)}

# Optional Jarvis-style Windows computer control. Disabled unless explicitly enabled.
COMPUTER_ENABLED = os.getenv('AI_COMPUTER_CONTROL', 'false').lower() == 'true'
COMPUTER_REQUIRE_CONFIRM = os.getenv('AI_COMPUTER_REQUIRE_CONFIRM', 'true').lower() == 'true'
# Arbitrary PowerShell is the most dangerous computer action; it is opt-in only.
SHELL_ENABLED = os.getenv('AI_COMPUTER_SHELL_ENABLED', 'false').lower() == 'true'

def computer_auth(value: str | None):
    auth(value)
    if not COMPUTER_ENABLED:
        raise HTTPException(403, 'Windows computer control is disabled; set AI_COMPUTER_CONTROL=true to opt in')

def _pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        raise HTTPException(503, 'pyautogui is not installed on the Windows host bridge')

def _artifact_scene(task_id: str) -> str | None:
    artifacts = Path(__file__).resolve().parents[1] / 'api-gateway' / 'data' / 'artifacts'
    base = artifacts / f'task_{task_id}'
    if (base / 'scene.blend').is_file():
        return str(base / 'scene.blend')
    blends = sorted((b for b in base.rglob('*.blend') if b.is_file())) if base.is_dir() else []
    return str(blends[0]) if blends else None


def _resolve_open_target(target: str):
    """Resolve an open target to a local, existing file when possible.

    Jarvis may describe a rendered scene as a creative-files URL, a task id, or
    a bare filename. Rewriting those references to the on-disk artifact prevents
    desktop apps (notably Blender) from ever being handed remote or partial
    content. Returns None when empty, or a ('local'|'remote'|'unknown', value)
    tuple otherwise.
    """
    t = (target or '').strip().strip('"').strip("'")
    if not t:
        return None
    low = t.lower()
    if low.startswith(('http://', 'https://', 'www.')):
        parsed = urlparse(t if not low.startswith('www.') else 'http://' + t)
        m = re.search(r'/v1/creative-files/(?:task_)?([0-9a-fA-F]{8})/?', parsed.path)
        if m:
            local = _artifact_scene(m.group(1))
            if local:
                return ('local', local)
        return ('remote', t)
    m = re.search(r'(?:task_)?([0-9a-fA-F]{8})', t)
    if m:
        local = _artifact_scene(m.group(1))
        if local:
            return ('local', local)
    p = Path(os.path.expandvars(os.path.expanduser(t)))
    if p.is_absolute() and p.is_file():
        return ('local', str(p))
    return ('unknown', t)


class ComputerOpenRequest(BaseModel):
    target: str
class ComputerTypeRequest(BaseModel):
    text: str
    title: str = ''
    process: str = ''
    pid: int = 0
class ComputerKeyRequest(BaseModel):
    key: str

@app.post('/computer/status')
def computer_status_endpoint(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {'enabled': COMPUTER_ENABLED, 'platform': sys.platform, 'pyautogui_available': _pyautogui_available(), 'confirmation_required_for_open': COMPUTER_REQUIRE_CONFIRM}

def _pyautogui_available():
    try:
        import pyautogui  # noqa: F401
        return True
    except ImportError:
        return False

@app.post('/computer/open')
def computer_open(req: ComputerOpenRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    aliases = {"settings": "ms-settings:", "computer settings": "ms-settings:",
               "system settings": "ms-settings:", "windows settings": "ms-settings:",
               "calculator": "calc.exe", "notepad": "notepad.exe",
               "file explorer": "explorer.exe", "task manager": "taskmgr.exe"}
    target=aliases.get(req.target.strip().lower(), req.target.strip())
    if not target: raise HTTPException(400,'target is required')
    resolved = _resolve_open_target(target)
    if resolved is not None:
        kind, value = resolved
        if kind == 'remote':
            raise HTTPException(400, 'refusing to open a remote URL in a desktop app: ' + value
                                + '. Download the scene, or ask Jarvis to open the local rendered scene instead.')
        if kind == 'local':
            target = value
        elif kind == 'unknown' and target.lower().endswith('.blend'):
            artifacts = Path(__file__).resolve().parents[1] / 'api-gateway' / 'data' / 'artifacts'
            available = sorted(str(b) for b in artifacts.glob('task_*/scene.blend')) if artifacts.is_dir() else []
            raise HTTPException(400, 'no local Blender scene found for ' + target
                                + ('; available scenes: ' + ', '.join(available) if available else ' (none found)'))
    # Explicitly avoid shell parsing: Windows start is invoked with a single argument.
    if sys.platform != 'win32': raise HTTPException(400,'Windows computer control requires Windows')
    import psutil
    before_pids = {p.pid for p in psutil.process_iter(['pid'])}
    before_windows = _iter_windows()
    before_titles = {w['title'] for w in before_windows}
    before_handles = {w['handle'] for w in before_windows}
    started = time.time()
    # os.startfile launches the visible Windows app directly; it does not create
    # a console window (the CREATE_NO_WINDOW policy still applies to diagnostics).
    try:
        os.startfile(target)
    except OSError as exc:
        _audit('computer_open', ok=False, details={'duration_ms': 0, 'target_len': len(target)})
        return {'ok': False, 'error': f'Application launch failed: {exc}', 'target': target}
    expected = _expected_process_names(target)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        time.sleep(0.5)
        foreground = _fg_info()
        for window in _iter_windows():
            try:
                process_name = psutil.Process(window['pid']).name().lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            matches = process_name in expected
            if target.startswith('ms-settings:') and process_name == 'applicationframehost.exe':
                matches = window['title'].strip().lower() == 'settings'
            if matches and window['handle'] != foreground['handle']:
                _focus_element(window)
                foreground = _fg_info()
            if matches and (window['handle'] == foreground['handle'] or window['handle'] not in before_handles or window['pid'] not in before_pids):
                _audit('computer_open', ok=True, details={'process': process_name, 'pid': window['pid'], 'duration_ms': int((time.time()-started)*1000)})
                return {'ok': True, 'target': target, 'pid': window['pid'], 'process': process_name,
                        'window_title': window['title'], 'handle': window['handle'], 'verified_via': 'visible_window'}
        for w in _iter_windows():
            if w['title'] not in before_titles and _is_error_dialog(w['title']):
                try:
                    _pyautogui().press('esc')
                except Exception:
                    pass
                _audit('computer_open', ok=False, details={'target': target, 'duration_ms': int((time.time()-started)*1000)})
                return {'ok': False, 'target': target,
                        'error': 'launch failed: ' + w['title']}
        if not expected:
            for w in _iter_windows():
                if w['title'] not in before_titles and not _is_error_dialog(w['title']):
                    _audit('computer_open', ok=True, details={'target': target, 'window_title': w['title'], 'pid': w['pid'], 'duration_ms': int((time.time()-started)*1000)})
                    return {'ok': True, 'target': target, 'pid': w['pid'],
                            'window_title': w['title'], 'verified_via': 'window'}
    _audit('computer_open', ok=False, details={'target': target, 'duration_ms': int((time.time()-started)*1000)})
    return {'ok': False, 'target': target,
            'error': 'launch requested but a matching application window could not be verified'}

@app.post('/computer/type')
def computer_type(req: ComputerTypeRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    if len(req.text) > 5000: raise HTTPException(400,'text is limited to 5000 characters')
    started = time.time()
    if req.title.strip() or req.process.strip() or req.pid:
        focused = _do_focus(title=req.title, process=req.process, pid=int(req.pid or 0))
        if not focused.get('focused'):
            raise HTTPException(409, 'could not focus target window: ' + focused.get('error', 'unknown'))
        fg_title, fg_pid = focused['title'], focused['pid']
    else:
        fg = _fg_info()
        fg_title, fg_pid = fg['title'], fg['pid']
    pyautogui=_pyautogui()
    pyautogui.write(req.text, interval=0.005)
    _audit_sensitive('computer_type', ok=True, target=fg_title, started=started)
    return {'ok':True,'chars':len(req.text),'focused_title':fg_title,'focused_pid':fg_pid}

@app.post('/computer/key')
def computer_key(req: ComputerKeyRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    key=req.key.strip().lower()
    if not key or len(key)>80: raise HTTPException(400,'invalid key')
    pyautogui=_pyautogui()
    aliases={'ctrl':'ctrl','control':'ctrl','cmd':'win','command':'win','win':'win','option':'alt','esc':'esc','return':'enter'}
    parts=[aliases.get(x,x) for x in key.replace('-','+').split('+')]
    allowed=set('abcdefghijklmnopqrstuvwxyz0123456789') | {'enter','esc','escape','tab','space','backspace','delete','home','end','pageup','pagedown','up','down','left','right','shift','ctrl','alt','win','f1','f2','f3','f4','f5','f6','f7','f8','f9','f10','f11','f12'}
    if any(x not in allowed for x in parts): raise HTTPException(400,'unsupported key or shortcut')
    started = time.time()
    if len(parts)>1: pyautogui.hotkey(*parts)
    else: pyautogui.press(parts[0])
    _audit_sensitive('computer_key', ok=True, target=req.key, started=started)
    return {'ok':True,'key':req.key}


def _chrome_path() -> Path | None:
    roots = [os.getenv('ProgramFiles', ''), os.getenv('ProgramFiles(x86)', ''), os.getenv('LOCALAPPDATA', '')]
    relatives = ['Google/Chrome/Application/chrome.exe', 'Chromium/Application/chrome.exe']
    for root in roots:
        if not root:
            continue
        for relative in relatives:
            candidate = Path(root) / relative
            if candidate.is_file():
                return candidate
    return None


def _browser_destination(req: BrowserRequest) -> str:
    raw_url, query = req.url.strip(), req.query.strip()
    if bool(raw_url) == bool(query):
        raise HTTPException(400, 'provide exactly one of url or query')
    destination = raw_url or ('https://www.google.com/search?q=' + quote_plus(query))
    parsed = urlparse(destination)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise HTTPException(400, 'only complete http:// or https:// URLs are supported')
    if not req.visible:
        raise HTTPException(400, 'browser automation must remain visible')
    if req.browser.strip().lower() not in {'chrome', 'google chrome'}:
        raise HTTPException(400, 'only Chrome is currently supported')
    return destination


@app.post('/computer/browser')
def computer_browser(req: BrowserRequest, authorization: str | None = Header(default=None)):
    """Open a visible Chrome destination following observe→act→verify."""
    _computer_common(authorization)
    destination = _browser_destination(req)
    chrome = _chrome_path()
    if not chrome:
        raise HTTPException(503, 'Google Chrome is not installed or could not be located')
    parsed = urlparse(destination)
    with _desktop_action('computer_browser'):
        before_windows = {(w['handle'], w['title']) for w in _iter_windows()}
        observe_result = _observe_public(req.max_width, req.include_windows, req.include_processes)
        serial_id = _ACTION_SERIALIZER.submit('computer_browser', {
            'url': req.url, 'query': req.query, 'browser': req.browser,
        }, timeout=15)
    try:
        process = subprocess.Popen([str(chrome), destination])
        deadline = time.time() + 12.0
        matched = None
        while time.time() < deadline:
            time.sleep(0.4)
            status = _ACTION_SERIALIZER.status(serial_id)
            if status and status.get('cancelled'):
                try: process.terminate()
                except Exception: pass
                return {'ok': False, 'browser': 'chrome', 'host': parsed.hostname,
                        'error': 'cancelled by user', 'serial_id': serial_id}
            for window in _iter_windows():
                try:
                    import psutil
                    name = (psutil.Process(window['pid']).name() or '').lower()
                except Exception:
                    name = ''
                if name == 'chrome.exe':
                    matched = window
                    if (window['handle'], window['title']) not in before_windows:
                        break
            if matched:
                focused = _do_focus(pid=matched['pid'])
                if focused.get('focused'):
                    fg = _fg_info()
                    result = {'ok': True, 'browser': 'chrome', 'host': parsed.hostname,
                              'window_title': fg['title'] or matched['title'], 'pid': matched['pid'],
                              'visible': True, 'verified_via': 'chrome_window_and_foreground',
                              'observe': observe_result, 'serial_id': serial_id}
                    _ACTION_SERIALIZER.complete(serial_id, result)
                    _audit('computer_browser', ok=True, details={'host': parsed.hostname, 'pid': matched['pid']})
                    return result
        result = {'ok': False, 'browser': 'chrome', 'host': parsed.hostname, 'pid': process.pid,
                  'visible': True, 'error': 'Chrome launched but its visible window could not be verified',
                  'serial_id': serial_id}
        _ACTION_SERIALIZER.complete(serial_id, result, error=result['error'])
        _audit('computer_browser', ok=False, details={'host': parsed.hostname, 'pid': process.pid})
        return result
    except Exception as exc:
        _ACTION_SERIALIZER.complete(serial_id, {}, error=str(exc))
        raise


@app.post('/computer/action/serialize')
def computer_action_serialize(req: ComputerOpenRequest, authorization: str | None = Header(default=None)):
    """Serialize a computer action for tracking and cancellation."""
    computer_auth(authorization)
    serial_id = _ACTION_SERIALIZER.submit(req.target, {}, timeout=60)
    return {"serial_id": serial_id, "status": "queued", "action": req.target}


@app.get('/computer/action/{serial_id}')
def computer_action_status(serial_id: str, authorization: str | None = Header(default=None)):
    """Check the status of a serialized computer action."""
    computer_auth(authorization)
    status = _ACTION_SERIALIZER.status(serial_id)
    if status is None:
        raise HTTPException(404, "action not found")
    return status


@app.post('/computer/action/{serial_id}/cancel')
def computer_action_cancel(serial_id: str, authorization: str | None = Header(default=None)):
    """Cancel a serialized computer action before completion."""
    computer_auth(authorization)
    if not _ACTION_SERIALIZER.cancel(serial_id):
        raise HTTPException(404, "action not found or already completed")
    return {"serial_id": serial_id, "status": "cancelled"}


@app.post('/computer/observe')
def computer_screenshot(authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    pyautogui=_pyautogui()
    from io import BytesIO
    import base64
    image=pyautogui.screenshot()
    buf=BytesIO(); image.save(buf,format='PNG')
    return {'ok':True,'mime':'image/png','data_base64':base64.b64encode(buf.getvalue()).decode('ascii')}

class FocusRequest(BaseModel):
    title: str = ''
    process: str = ''
    pid: int = 0

class VerifyRequest(BaseModel):
    title: str = ''
    process: str = ''
    pid: int = 0

_ERROR_DIALOG_HINTS = ('cannot find', 'cannot open', 'error opening', 'failed to open', 'not found')


def _fg_info() -> dict:
    """Foreground window: handle, title, pid. Empty values when unavailable."""
    try:
        from windows_native import foreground
        return foreground()
    except Exception:
        return {'handle': 0, 'title': '', 'pid': 0}


def _iter_windows() -> list:
    from windows_native import windows
    return windows()


def _match_window(title: str = '', process: str = '', pid: int = 0):
    title = (title or '').strip().lower()
    process = (process or '').strip().lower()
    cands = _iter_windows()
    if pid:
        for w in cands:
            if w['pid'] == int(pid):
                return w
        return None
    if process:
        base = process[:-4] if process.endswith('.exe') else process
        for w in cands:
            try:
                import psutil
                name = (psutil.Process(w['pid']).name() or '').lower()
            except Exception:
                name = ''
            if base and base in name:
                return w
        return None
    if title:
        for w in cands:
            if title in w['title'].lower():
                return w
        return None
    return None


def _focus_element(w) -> bool:
    try:
        from windows_native import focus
        return focus(w['handle'])
    except Exception:
        return False


def _do_focus(title: str = '', process: str = '', pid: int = 0) -> dict:
    w = _match_window(title=title, process=process, pid=pid)
    if not w:
        return {'ok': False, 'focused': False, 'error': 'no matching window found'}
    for _ in range(2):
        _focus_element(w)
        time.sleep(0.5)
        fg = _fg_info()
        if (fg['handle'] and fg['handle'] == w['handle']) or (fg['pid'] and fg['pid'] == w['pid']):
            return {'ok': True, 'focused': True, 'title': w['title'], 'pid': w['pid'], 'handle': w['handle']}
    return {'ok': False, 'focused': False, 'title': w['title'], 'pid': w['pid'],
            'error': 'foreground window could not be established', 'foreground': _fg_info()}


@app.post('/computer/focus')
def computer_focus(req: FocusRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    if not (req.title.strip() or req.process.strip() or req.pid):
        raise HTTPException(400, 'title, process, or pid is required')
    return _do_focus(title=req.title, process=req.process, pid=int(req.pid or 0))


@app.post('/computer/verify')
def computer_verify(req: VerifyRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    fg = _fg_info()
    out: dict = {'ok': True, 'foreground': fg, 'target': None}
    if req.title.strip() or req.process.strip() or req.pid:
        target = _match_window(title=req.title, process=req.process, pid=int(req.pid or 0))
        if target:
            out['target'] = {'title': target['title'], 'pid': target['pid'], 'handle': target['handle'],
                             'is_focused': bool((fg['handle'] and fg['handle'] == target['handle']) or
                                                (fg['pid'] and fg['pid'] == target['pid']))}
    return out


def _expected_process_names(target: str) -> set:
    t = (target or '').strip().lower()
    if t.startswith(('http://', 'https://', 'www.')) or t.endswith(('.html', '.htm')):
        return {'msedge.exe', 'chrome.exe', 'firefox.exe'}
    if t.startswith('ms-settings:') or t == 'settings':
        return {'systemsettings.exe'}
    if t.endswith('.txt'):
        return {'notepad.exe'}
    if t.endswith('.blend'):
        return {'blender.exe'}
    aliases = {'notepad': {'notepad.exe'}, 'edge': {'msedge.exe'}, 'msedge': {'msedge.exe'},
               'chrome': {'chrome.exe'}, 'firefox': {'firefox.exe'}, 'mspaint': {'mspaint.exe'},
               'paint': {'mspaint.exe'}, 'calc': {'calculatorapp.exe', 'calc.exe'},
               'cmd': {'cmd.exe'}, 'powershell': {'powershell.exe', 'pwsh.exe'},
               'settings': {'systemsettings.exe'}, 'control panel': {'explorer.exe'},
               'device manager': {'explorer.exe'}, 'task manager': {'taskmgr.exe'}}
    if t in aliases:
        return aliases[t]
    if t in {'settings', 'control panel', 'device manager', 'task manager', 'services', 'event viewer'}:
        return {'explorer.exe'}
    if t.endswith('.exe'):
        return {t.split('\\')[-1].split('/')[-1]}
    return set()


def _is_error_dialog(title: str) -> bool:
    low = (title or '').lower()
    return any(h in low for h in _ERROR_DIALOG_HINTS)


class ObserveRequest(BaseModel):
    max_width: int = 0
    include_windows: bool = True
    include_processes: bool = False


def _capture_png(max_width: int = 0) -> dict:
    pyautogui = _pyautogui()
    from io import BytesIO
    import base64
    from PIL import Image
    image = pyautogui.screenshot()
    native = (image.width, image.height)
    try:
        mw = int(max_width or os.getenv('AI_COMPUTER_SCREEN_MAX_WIDTH', '1280') or 1280)
    except ValueError:
        mw = 1280
    if mw > 0 and image.width > mw:
        image = image.resize((mw, int(image.height * mw / image.width)), Image.LANCZOS)
    buf = BytesIO()
    image.save(buf, format='PNG')
    return {'mime': 'image/png', 'data_base64': base64.b64encode(buf.getvalue()).decode('ascii'),
            'width': image.width, 'height': image.height,
            'native_width': native[0], 'native_height': native[1], 'max_width_applied': mw}


def _observe_public(max_width: int = 0, include_windows: bool = True, include_processes: bool = False) -> dict:
    """Public observe helper without auth gate, for the browser verify pattern."""
    shot = _capture_png(max_width)
    fg = _fg_info()
    out: dict = {'ok': True, 'screenshot': shot, 'foreground': fg}
    if include_windows:
        out['windows'] = [{'title': w['title'], 'pid': w['pid'], 'handle': w['handle']}
                          for w in _iter_windows()][:60]
    else:
        out['windows'] = []
    return out


@app.post('/computer/observe')
def computer_observe(req: ObserveRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    shot = _capture_png(req.max_width)
    fg = _fg_info()
    out: dict = {'ok': True, 'screenshot': shot, 'foreground': fg}
    if req.include_windows:
        out['windows'] = [{'title': w['title'], 'pid': w['pid'], 'handle': w['handle']}
                          for w in _iter_windows()][:60]
    else:
        out['windows'] = []
    if req.include_processes:
        import psutil
        procs = []
        for x in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                rss = (x.info['memory_info'].rss if x.info['memory_info'] else 0) / 1048576
                procs.append({'pid': x.info['pid'], 'name': x.info['name'], 'rss_mb': round(rss, 1)})
            except Exception:
                pass
        procs.sort(key=lambda d: d['rss_mb'], reverse=True)
        out['processes_count'] = len(procs)
        out['top_processes'] = procs[:15]
    return out


if __name__ == '__main__':
    if not TOKEN:
        raise SystemExit('Set AI_HOST_BRIDGE_TOKEN before starting the host bridge.')
    uvicorn.run(app, host=BIND_HOST, port=PORT)
