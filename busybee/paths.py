"""Locations of the app's persistent files."""

import os
from pathlib import Path

from . import APP_ID


def data_dir() -> Path:
    """Per-user data folder (%USERPROFILE%\\.busybee), overridable via BUSYBEE_DATA_DIR.

    Deliberately not %APPDATA%: the Microsoft Store build of Python silently redirects
    AppData writes into its package cache, which is invisible in Explorer and deleted
    when that Python is uninstalled.
    """
    override = os.environ.get("BUSYBEE_DATA_DIR")
    base = Path(override) if override else Path.home() / f".{APP_ID.lower()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "busybee.db"


def log_path() -> Path:
    return data_dir() / "busybee.log"
