"""Timer state machine: idle -> running <-> paused -> idle.

Every transition is written to the database immediately. Pausing closes the current
entry (so the time is saved) and remembers the project; resuming opens a new entry.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Callable

from . import timeutil
from .db import Database, Entry


class State(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"


_STATE_KEY = "tracker.state"
_PROJECT_KEY = "tracker.project_id"


class Tracker:
    def __init__(self, db: Database, clock: Callable[[], datetime] = timeutil.now):
        self.db = db
        self._clock = clock
        self.state = State.IDLE
        self.project_id: int | None = None
        self.entry_id: int | None = None
        self.entry_start: datetime | None = None
        self._listeners: list[Callable[[], None]] = []

    # --- observers ----------------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _changed(self) -> None:
        self.db.set_setting(_STATE_KEY, self.state.value)
        self.db.set_setting(_PROJECT_KEY, None if self.project_id is None else str(self.project_id))
        for callback in self._listeners:
            callback()

    # --- queries ------------------------------------------------------------------

    @property
    def project_name(self) -> str | None:
        if self.project_id is None:
            return None
        project = self.db.get_project(self.project_id)
        return project.name if project else None

    def elapsed_seconds(self) -> float:
        if self.state is not State.RUNNING or self.entry_start is None:
            return 0.0
        return max(0.0, (self._clock() - self.entry_start).total_seconds())

    # --- transitions --------------------------------------------------------------

    def start(self, project_id: int) -> None:
        if self.state is State.RUNNING and self.project_id == project_id:
            return
        now = self._clock()
        if self.state is State.RUNNING:
            self._close_current(now)
        self.entry_id = self.db.start_entry(project_id, now)
        self.entry_start = now
        self.project_id = project_id
        self.state = State.RUNNING
        self._changed()

    def pause(self) -> None:
        if self.state is not State.RUNNING:
            return
        self._close_current(self._clock())
        self.state = State.PAUSED
        self._changed()

    def resume(self) -> None:
        if self.state is State.PAUSED and self.project_id is not None:
            self.start(self.project_id)

    def toggle_pause(self) -> None:
        if self.state is State.RUNNING:
            self.pause()
        else:
            self.resume()

    def stop(self) -> None:
        if self.state is State.IDLE:
            return
        if self.state is State.RUNNING:
            self._close_current(self._clock())
        self.state = State.IDLE
        self.project_id = None
        self._changed()

    def heartbeat(self) -> None:
        """Record that the running entry is still alive (bounds data loss after a crash)."""
        if self.entry_id is not None:
            self.db.touch_entry(self.entry_id, self._clock())

    def shutdown(self) -> None:
        """Save the running entry on exit, but remember it was running so it can resume."""
        if self.state is State.RUNNING:
            self._close_current(self._clock())
            # Persisted state stays "running" on purpose; see recover().

    def recover(self, resume: bool) -> Entry | None:
        """Restore state after a restart.

        Entries left open by a crash or power loss are closed at their last heartbeat.
        If the timer was running when the app went away it is restarted when `resume`
        is set, otherwise the project is left paused so one click continues it.
        Returns the entry that had to be closed after an unclean exit, if any.
        """
        orphan = None
        for entry in self.db.open_entries():
            self.db.close_entry(entry.id, entry.last_seen or entry.start)
            orphan = entry

        saved_state = self.db.get_setting(_STATE_KEY, State.IDLE.value)
        saved_project = self.db.get_setting(_PROJECT_KEY)
        project_id = orphan.project_id if orphan else (int(saved_project) if saved_project else None)
        project = self.db.get_project(project_id) if project_id is not None else None

        if project is None or project.archived or saved_state == State.IDLE.value:
            self.state, self.project_id = State.IDLE, None
            self._changed()
        elif resume and saved_state == State.RUNNING.value:
            self.start(project.id)
        else:
            self.state, self.project_id = State.PAUSED, project.id
            self._changed()
        return orphan

    def _close_current(self, when: datetime) -> None:
        if self.entry_id is not None:
            self.db.close_entry(self.entry_id, when)
        self.entry_id = None
        self.entry_start = None
