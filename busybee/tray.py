"""System tray icon and menu.

pystray runs its own message loop in a background thread. Menu callbacks never touch
the database or Tk directly; they hand work to the Tk main thread via App.post().
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pystray
from pystray import Menu, MenuItem as Item

from . import APP_ID, APP_NAME
from .icons import make_icon
from .timeutil import fmt_duration, fmt_time
from .tracker import State

if TYPE_CHECKING:
    from .app import App

_TOOLTIP_MAX = 127  # Windows limit for tray tooltips


class TrayIcon:
    def __init__(self, app: App):
        self.app = app
        self.icon = pystray.Icon(APP_ID, make_icon(State.IDLE), APP_NAME, menu=Menu(self._items))
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.icon.stop()

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:  # notifications are best effort
            pass

    def refresh(self) -> None:
        """Re-read tracker state; call from the main thread after any change."""
        tracker = self.app.tracker
        self.icon.icon = make_icon(tracker.state)
        self.icon.title = self._tooltip()[:_TOOLTIP_MAX]
        self.icon.update_menu()

    def _tooltip(self) -> str:
        tracker = self.app.tracker
        name = _shorten(tracker.project_name or "", 60)
        if tracker.state is State.RUNNING:
            return f"{APP_NAME} - {name}: {fmt_duration(tracker.elapsed_seconds())}"
        if tracker.state is State.PAUSED:
            return f"{APP_NAME} - {name} (paused)"
        return f"{APP_NAME} - not tracking"

    # --- menu ---------------------------------------------------------------------

    def _items(self):
        app, tracker = self.app, self.app.tracker
        name = _shorten(tracker.project_name or "", 40)

        if tracker.state is State.RUNNING:
            status = f"Tracking: {name} (since {fmt_time(tracker.entry_start)})"
        elif tracker.state is State.PAUSED:
            status = f"Paused: {name}"
        else:
            status = "Not tracking"
        yield Item(status, None, enabled=False)
        yield Menu.SEPARATOR

        projects = app.projects
        if projects:
            yield Item(
                "Start tracking",
                Menu(*[
                    Item(_shorten(p.name, 50), self._start_action(p.id), checked=self._is_current(p.id), radio=True)
                    for p in projects
                ]),
            )
        else:
            yield Item("Add a project...", lambda: app.post(app.show_window, "projects"))

        if tracker.state is State.PAUSED:
            yield Item(f"Resume {name}", lambda: app.post(app.resume, True))
        else:
            yield Item("Pause", lambda: app.post(app.pause, True), enabled=tracker.state is State.RUNNING)
        yield Item("Stop", lambda: app.post(app.stop, True), enabled=tracker.state is not State.IDLE)
        yield Menu.SEPARATOR
        yield Item(f"Open {APP_NAME}...", lambda: app.post(app.show_window), default=True)
        yield Item("Quit", lambda: app.post(app.quit))

    # Factories keep pystray's argument-count detection happy (no default args).
    def _start_action(self, project_id: int):
        return lambda: self.app.post(self.app.start_project, project_id, True)

    def _is_current(self, project_id: int):
        tracker = self.app.tracker
        return lambda item: tracker.state is State.RUNNING and tracker.project_id == project_id


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
