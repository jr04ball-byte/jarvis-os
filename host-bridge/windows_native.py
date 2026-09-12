"""Top-level desktop windows using pointer-safe Win32 APIs, without COM."""
import ctypes
from ctypes import wintypes


def _api():
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    return api


def _info(api, hwnd):
    text = ctypes.create_unicode_buffer(api.GetWindowTextLengthW(hwnd) + 1)
    api.GetWindowTextW(hwnd, text, len(text))
    pid, rect = wintypes.DWORD(), wintypes.RECT()
    api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    api.GetWindowRect(hwnd, ctypes.byref(rect))
    return {'handle': int(hwnd), 'title': text.value, 'pid': pid.value,
            'rect': [rect.left, rect.top, rect.right, rect.bottom]}


def windows():
    api, result = _api(), []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if api.IsWindowVisible(hwnd):
            row = _info(api, hwnd)
            if row['title'].strip():
                result.append(row)
        return True
    api.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not api.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def foreground():
    api = _api()
    hwnd = api.GetForegroundWindow()
    return _info(api, hwnd) if hwnd else {'handle': 0, 'title': '', 'pid': 0}


def focus(hwnd):
    api = _api()
    api.ShowWindow(hwnd, 9)
    return bool(api.SetForegroundWindow(hwnd))
