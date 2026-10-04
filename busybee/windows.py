"""Read the focused window and the list of open windows (Win32 via ctypes).

Each call is a handful of cheap system calls, so polling it every few seconds costs
practically nothing.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

# Own DLL handles so the signatures below don't leak into ctypes.windll users elsewhere.
_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")
_dwmapi = ctypes.WinDLL("dwmapi")

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

_GA_ROOTOWNER = 3
_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# Desktop, taskbar and Start menu: never a reason to switch projects.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow"}


@dataclass(frozen=True)
class WindowInfo:
    title: str
    exe: str  # program file name such as "Code.exe"; empty if Windows won't tell (e.g. admin processes)

    @property
    def label(self) -> str:
        return f"{self.title}  ({self.exe})" if self.exe else self.title


def foreground_window() -> WindowInfo | None:
    """The focused window, or None for the desktop, taskbar, Busy Bee itself or untitled windows."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    hwnd = _user32.GetAncestor(hwnd, _GA_ROOTOWNER) or hwnd  # a dialog counts as its main window
    return _describe(hwnd)


def open_windows() -> list[WindowInfo]:
    """Visible top-level windows, roughly as Alt+Tab shows them, without duplicates."""
    found: list[WindowInfo] = []

    def callback(hwnd, _):
        if _is_app_window(hwnd):
            info = _describe(hwnd)
            if info and info not in found:
                found.append(info)
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return sorted(found, key=lambda w: (w.exe.lower(), w.title.lower()))


def _describe(hwnd) -> WindowInfo | None:
    if _class_name(hwnd) in _SHELL_CLASSES:
        return None
    pid = _pid(hwnd)
    if pid == os.getpid():
        return None
    title = _text(hwnd)
    if not title:
        return None
    exe = _exe_name(pid)
    if exe.lower() == "applicationframehost.exe":
        # Store apps (Settings, Calculator, ...) live inside a frame; the real app owns a child window.
        exe = _hosted_app_exe(hwnd, pid) or exe
    return WindowInfo(title, exe)


def _is_app_window(hwnd) -> bool:
    if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
        return False
    if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
        return False
    cloaked = wintypes.DWORD()  # hidden Store apps and windows on other virtual desktops
    _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return not cloaked.value


def _text(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_name(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.basename(buf.value)
    finally:
        _kernel32.CloseHandle(handle)


def _hosted_app_exe(hwnd, frame_pid: int) -> str:
    result = ""

    def callback(child, _):
        nonlocal result
        pid = _pid(child)
        if pid != frame_pid:
            result = _exe_name(pid)
            return False
        return True

    _user32.EnumChildWindows(hwnd, _WNDENUMPROC(callback), 0)
    return result
