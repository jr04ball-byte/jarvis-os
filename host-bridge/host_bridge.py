import os, secrets, subprocess, sys, time
import ctypes
from pathlib import Path
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
        r=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.used,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
        if r.returncode==0:
            for line in r.stdout.splitlines():
                a=[x.strip() for x in line.split(',')]
                if len(a)>=4: nvidia.append({'name':a[0],'vram_mb':a[1],'used_mb':a[2],'temperature_c':a[3]})
    except Exception: pass
    turtle=[]
    try:
        r=subprocess.run(['powershell','-NoProfile','-Command',"Get-CimInstance Win32_SoundDevice | Select-Object Name,Status | ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=8)
        import json
        data=json.loads(r.stdout) if r.stdout.strip() else []
        if isinstance(data,dict): data=[data]
        turtle=[x for x in data if 'turtle' in str(x.get('Name','')).lower() or 'xbox' in str(x.get('Name','')).lower()]
    except Exception: pass
    broadcast=any(Path(x).exists() for x in [Path(os.getenv('ProgramFiles','C:/Program Files'))/'NVIDIA Corporation/NVIDIA Broadcast/NVIDIA Broadcast.exe', Path(os.getenv('ProgramFiles(x86)','C:/Program Files (x86)'))/'NVIDIA Corporation/NVIDIA Broadcast/NVIDIA Broadcast.exe'])
    pa=False
    try:
        r=subprocess.run(['powershell','-NoProfile','-Command',"Get-AppxPackage -Name Microsoft.PowerAutomateDesktop | Select-Object Name,Version | ConvertTo-Json -Compress"],capture_output=True,text=True,timeout=8)
        pa='Microsoft.PowerAutomateDesktop' in r.stdout
    except Exception: pass
    return {'ok':True,'admin':COMPUTER_ADMIN,'pyautogui':_pyautogui_available(),'psutil':True,'pywinauto':_module_available('pywinauto'),'mss':_module_available('mss'),'nvidia':nvidia,'turtle_beach_or_xbox_audio':turtle,'nvidia_broadcast':broadcast,'power_automate_desktop':pa,'cpu_percent':p.cpu_percent(interval=0.05),'ram_percent':p.virtual_memory().percent}

def _module_available(name):
    try:
        __import__(name); return True
    except Exception: return False

@app.post('/computer/move')
def computer_move(req: MoveRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui(); pyautogui.moveTo(req.x,req.y,duration=max(0,min(req.duration,2))); return {'ok':True,'x':req.x,'y':req.y}

@app.post('/computer/click')
def computer_click(req: ClickRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui();
    if req.button not in {'left','right','middle'}: raise HTTPException(400,'invalid mouse button')
    pyautogui.click(req.x,req.y,clicks=max(1,min(req.clicks,3)),button=req.button,interval=0.08); return {'ok':True,'x':req.x,'y':req.y,'clicks':req.clicks,'button':req.button}

@app.post('/computer/scroll')
def computer_scroll(req: ScrollRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization); pyautogui=_pyautogui(); pyautogui.scroll(max(-20,min(req.amount,20))); return {'ok':True,'amount':req.amount}

@app.post('/computer/shell')
def computer_shell(req: ShellRequest, authorization: str | None = Header(default=None)):
    _computer_common(authorization)
    if len(req.command)>4000: raise HTTPException(400,'command too long')
    # Shell is deliberately opt-in and runs under the bridge account/elevation.
    r=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-Command',req.command],capture_output=True,text=True,timeout=max(1,min(req.timeout,120)))
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
        from pywinauto import Desktop
        out=[]
        for w in Desktop(backend='uia').windows():
            try:
                title=w.window_text(); rect=w.rectangle()
                if title.strip(): out.append({'title':title,'left':rect.left,'top':rect.top,'right':rect.right,'bottom':rect.bottom,'handle':int(w.handle)})
            except Exception: pass
        return {'ok':True,'windows':out[:200]}
    except Exception as e:
        return {'ok':False,'windows':[],'error':str(e)}

# Optional Jarvis-style Windows computer control. Disabled unless explicitly enabled.
COMPUTER_ENABLED = os.getenv('AI_COMPUTER_CONTROL', 'false').lower() == 'true'
COMPUTER_REQUIRE_CONFIRM = os.getenv('AI_COMPUTER_REQUIRE_CONFIRM', 'true').lower() == 'true'

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
    target=req.target.strip()
    if not target: raise HTTPException(400,'target is required')
    # Explicitly avoid shell parsing: Windows start is invoked with a single argument.
    if sys.platform != 'win32': raise HTTPException(400,'Windows computer control requires Windows')
    import psutil
    before_pids = {p.pid for p in psutil.process_iter(['pid'])}
    before_titles = {w['title'] for w in _iter_windows()}
    subprocess.Popen(['cmd','/c','start','','%s' % target], shell=False)
    expected = _expected_process_names(target)
    deadline = time.time() + 7.0
    while time.time() < deadline:
        time.sleep(0.5)
        for w in _iter_windows():
            if w['title'] not in before_titles and _is_error_dialog(w['title']):
                try:
                    _pyautogui().press('esc')
                except Exception:
                    pass
                return {'ok': False, 'target': target,
                        'error': 'launch failed: ' + w['title']}
        if expected:
            try:
                for p in psutil.process_iter(['pid', 'name', 'create_time']):
                    name = (p.info.get('name') or '').lower()
                    if name in expected and p.info.get('pid') not in before_pids:
                        return {'ok': True, 'target': target, 'pid': p.info['pid'],
                                'process': p.info['name'], 'verified_via': 'process'}
            except Exception:
                pass
        else:
            for w in _iter_windows():
                if w['title'] not in before_titles and not _is_error_dialog(w['title']):
                    return {'ok': True, 'target': target, 'pid': w['pid'],
                            'window_title': w['title'], 'verified_via': 'window'}
    return {'ok': False, 'target': target,
            'error': 'launched but no new application window was detected'}

@app.post('/computer/type')
def computer_type(req: ComputerTypeRequest, authorization: str | None = Header(default=None)):
    computer_auth(authorization)
    if len(req.text) > 5000: raise HTTPException(400,'text is limited to 5000 characters')
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
    if len(parts)>1: pyautogui.hotkey(*parts)
    else: pyautogui.press(parts[0])
    return {'ok':True,'key':req.key}

@app.post('/computer/screenshot')
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
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {'handle': 0, 'title': '', 'pid': 0}
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {'handle': int(hwnd), 'title': buf.value, 'pid': int(pid.value)}
    except Exception:
        return {'handle': 0, 'title': '', 'pid': 0}


def _iter_windows() -> list:
    try:
        from pywinauto import Desktop
        out = []
        for w in Desktop(backend='uia').windows():
            try:
                title = w.window_text()
                if not title.strip():
                    continue
                rect = w.rectangle()
                out.append({'title': title, 'pid': int(w.process_id()), 'handle': int(w.handle),
                            'rect': [rect.left, rect.top, rect.right, rect.bottom], '_el': w})
            except Exception:
                pass
        return out
    except Exception:
        return []


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
        el = w.get('_el')
        if el is None:
            return False
        try:
            el.restore()
        except Exception:
            pass
        el.set_focus()
        return True
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
    if t.endswith('.txt'):
        return {'notepad.exe'}
    aliases = {'notepad': {'notepad.exe'}, 'edge': {'msedge.exe'}, 'msedge': {'msedge.exe'},
               'chrome': {'chrome.exe'}, 'firefox': {'firefox.exe'}, 'mspaint': {'mspaint.exe'},
               'paint': {'mspaint.exe'}, 'calc': {'calculatorapp.exe', 'calc.exe'},
               'cmd': {'cmd.exe'}, 'powershell': {'powershell.exe', 'pwsh.exe'}}
    if t in aliases:
        return aliases[t]
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
