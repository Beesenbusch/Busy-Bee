"""Start with Windows via the per-user Run registry key (no admin rights needed)."""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from . import APP_ID

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def launch_command() -> str:
    if getattr(sys, "frozen", False):  # packaged executable (e.g. PyInstaller)
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{PROJECT_ROOT / "busybee.pyw"}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except FileNotFoundError:
        return False


def enable() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, launch_command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_ID)
    except FileNotFoundError:
        pass


def set_enabled(enabled: bool) -> None:
    enable() if enabled else disable()


def refresh() -> None:
    """Keep the registered command current if the project folder or venv moved."""
    if is_enabled():
        enable()
