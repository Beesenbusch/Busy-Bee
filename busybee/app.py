"""Application entry point: wires storage, tracker, tray icon and window together.

Threading model: Tk and all database access run on the main thread. The tray icon
runs in its own thread and submits work through App.post(), which the main thread
drains periodically.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from PIL import ImageTk

from . import APP_ID, APP_NAME, autostart, paths
from .db import Database, Project
from .icons import make_app_icon
from .rules import FOCUS_CHECK_DEFAULT_S, FOCUS_HOLD_DEFAULT_S, PAUSE, STOP, SWITCH, AutoSwitcher, Rule
from .taskbar import set_window_app_id
from .tracker import State, Tracker
from .tray import TrayIcon
from .windows import foreground_window

log = logging.getLogger(__name__)

HEARTBEAT_MS = 30_000
TOOLTIP_MS = 15_000
QUEUE_POLL_MS = 100


class App:
    def __init__(self, db: Database):
        self.db = db
        self.tracker = Tracker(db)
        self.projects: list[Project] = []
        self.rules: list[Rule] = []
        self.auto_switch = db.get_bool("rules.enabled", True)
        self.switcher = AutoSwitcher()
        self._focus_job: str | None = None
        self._queue: queue.Queue[tuple[Callable, tuple]] = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.geometry("820x540")
        self.root.minsize(700, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.protocol("WM_SAVE_YOURSELF", self._on_session_end)  # Windows logoff/shutdown
        self.root.report_callback_exception = self._report_error
        # Several sizes so Windows can pick a sharp one for title bar, taskbar and Alt+Tab.
        self._window_icons = [ImageTk.PhotoImage(make_app_icon(n)) for n in (256, 64, 48, 32, 16)]
        self.root.iconphoto(True, *self._window_icons)
        self._set_taskbar_identity()
        self.window = None

        self.tray = TrayIcon(self)
        self.tracker.add_listener(self._on_tracker_change)

    # --- lifecycle ----------------------------------------------------------------

    def run(self) -> None:
        if self.db.get_setting("first_run_done") is None:
            try:
                autostart.enable()
            except OSError:
                log.exception("Could not enable autostart")
            self.db.set_setting("first_run_done", "1")
        else:
            autostart.refresh()

        self.projects = self.db.list_projects()
        self.rules = self.db.list_rules()
        orphan = self.tracker.recover(resume=self.db.get_bool("resume_on_start", False))
        if orphan:
            log.info("Closed entry %s left open by an unclean exit", orphan.id)

        self.tray.start()
        self.tray.refresh()
        if not self.projects:
            self.show_window("projects")

        self.root.after(QUEUE_POLL_MS, self._drain_queue)
        self.root.after(HEARTBEAT_MS, self._heartbeat)
        self.root.after(TOOLTIP_MS, self._update_tooltip)
        self._schedule_focus_check()
        self.root.mainloop()

    def quit(self) -> None:
        self.tracker.shutdown()
        self.tray.stop()
        self.root.quit()

    def _on_session_end(self) -> None:
        # Only record a final heartbeat: if the shutdown is cancelled we keep running
        # normally, and if it goes ahead the next start closes the entry at this time.
        self.tracker.heartbeat()

    # --- cross-thread dispatch ----------------------------------------------------

    def post(self, fn: Callable, *args) -> None:
        """Run fn(*args) on the Tk main thread. Safe to call from any thread."""
        self._queue.put((fn, args))

    def _drain_queue(self) -> None:
        try:
            while True:
                fn, args = self._queue.get_nowait()
                try:
                    fn(*args)
                except Exception as e:
                    self._report_error(type(e), e, e.__traceback__)
        except queue.Empty:
            pass
        self.root.after(QUEUE_POLL_MS, self._drain_queue)

    def _heartbeat(self) -> None:
        self.tracker.heartbeat()
        self.root.after(HEARTBEAT_MS, self._heartbeat)

    def _update_tooltip(self) -> None:
        if self.tracker.state is State.RUNNING:
            self.tray.refresh()
        self.root.after(TOOLTIP_MS, self._update_tooltip)

    # --- automatic switching ------------------------------------------------------

    def _schedule_focus_check(self) -> None:
        """(Re)start the focus polling with the current interval; no polling while switched off."""
        if self._focus_job is not None:
            self.root.after_cancel(self._focus_job)
            self._focus_job = None
        if self.auto_switch:
            seconds = max(1, self.db.get_int("rules.check_s", FOCUS_CHECK_DEFAULT_S))
            self._focus_job = self.root.after(seconds * 1000, self._check_focus)

    def _check_focus(self) -> None:
        self._focus_job = None
        try:
            if self.rules:
                project_ids = {p.id for p in self.projects}
                usable = [r for r in self.rules if r.action != SWITCH or r.project_id in project_ids]
                hold = self.db.get_int("rules.hold_s", FOCUS_HOLD_DEFAULT_S)
                rule = self.switcher.observe(usable, foreground_window(), hold)
                if rule:
                    self._apply_rule(rule)
        except Exception:
            # Logged, not shown: a dialog every few seconds would be worse than a missed switch.
            log.exception("Automatic switching failed")
        self._schedule_focus_check()

    def _apply_rule(self, rule: Rule) -> None:
        tracker = self.tracker
        # Notified like a tray action: nothing in the window shows that this happened.
        if rule.action == SWITCH and not (tracker.state is State.RUNNING and tracker.project_id == rule.project_id):
            self.start_project(rule.project_id, from_tray=True)
        elif rule.action == PAUSE and tracker.state is State.RUNNING:
            self.pause(from_tray=True)
        elif rule.action == STOP and tracker.state is not State.IDLE:
            self.stop(from_tray=True)

    def set_auto_switch(self, enabled: bool) -> None:
        self.auto_switch = enabled
        self.db.set_bool("rules.enabled", enabled)
        self.switcher.reset()
        self._schedule_focus_check()
        self.tray.refresh()
        if self.window:
            self.window.settings_tab.refresh()

    def focus_timing_changed(self) -> None:
        self._schedule_focus_check()

    def rules_changed(self) -> None:
        self.rules = self.db.list_rules()
        if self.window:
            self.window.rules_tab.refresh()

    # --- actions (main thread) ----------------------------------------------------

    def start_project(self, project_id: int, from_tray: bool = False) -> None:
        self.tracker.start(project_id)
        self._notify(from_tray, f"Tracking {self.tracker.project_name}")

    def pause(self, from_tray: bool = False) -> None:
        self.tracker.pause()
        self._notify(from_tray, f"Paused {self.tracker.project_name}")

    def resume(self, from_tray: bool = False) -> None:
        self.tracker.resume()
        self._notify(from_tray, f"Resumed {self.tracker.project_name}")

    def stop(self, from_tray: bool = False) -> None:
        name = self.tracker.project_name
        self.tracker.stop()
        self._notify(from_tray, f"Stopped {name}")

    def _notify(self, from_tray: bool, message: str) -> None:
        if from_tray and self.db.get_bool("notifications", True):
            self.tray.notify(message)

    def all_projects(self) -> list[Project]:
        return self.db.list_projects(include_archived=True)

    def projects_changed(self) -> None:
        self.projects = self.db.list_projects()
        self.rules = self.db.list_rules()  # deleting a project deletes its rules
        self.tray.refresh()
        if self.window:
            self.window.refresh_all()

    def entries_changed(self) -> None:
        if self.window:
            self.window.projects_tab.refresh()
            self.window.entries_tab.refresh()

    def _on_tracker_change(self) -> None:
        self.tray.refresh()
        if self.window:
            self.window.refresh_all()

    # --- window -------------------------------------------------------------------

    def show_window(self, tab: str | None = None) -> None:
        if self.window is None:
            from .gui import MainWindow  # built lazily on first open

            self.window = MainWindow(self)
        else:
            self.window.refresh_all()
        self.window.show_tab(tab)
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide_window(self) -> None:
        self.root.withdraw()

    def _set_taskbar_identity(self) -> None:
        """Must happen before the window is first shown, or the taskbar keeps a stray Python button.

        Tk only creates the real top-level window when it is first mapped, so map it
        fully transparent, tag it and withdraw it again.
        """
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.update_idletasks()
        try:
            set_window_app_id(int(self.root.wm_frame(), 16), APP_ID)
        except OSError:
            log.exception("Could not set the taskbar app ID")
        self.root.withdraw()
        self.root.attributes("-alpha", 1.0)

    def _report_error(self, exc_type, exc, tb) -> None:
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))
        messagebox.showerror(APP_NAME, f"Something went wrong:\n{exc}\n\nDetails were written to {paths.log_path()}")


# --- process setup ----------------------------------------------------------------

_mutex = None


def _acquire_single_instance() -> bool:
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, f"Local\\{APP_ID}.SingleInstance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    logging.basicConfig(
        filename=paths.log_path(),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    sys.excepthook = lambda *exc: log.error("Uncaught exception", exc_info=exc)

    if not _acquire_single_instance():
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running.\nLook for the bee in the system tray.", APP_NAME, 0x40)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp UI on high-DPI screens
    except (AttributeError, OSError):
        pass

    db = Database(paths.db_path())
    try:
        App(db).run()
    finally:
        db.close()
