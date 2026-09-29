"""SQLite persistence for projects, time entries and settings."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .timeutil import from_db, now, to_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    start_time  TEXT NOT NULL,
    end_time    TEXT,             -- NULL while the timer is running
    last_seen   TEXT              -- heartbeat of a running entry, used for crash recovery
);
CREATE INDEX IF NOT EXISTS idx_entries_start ON entries(start_time);
CREATE INDEX IF NOT EXISTS idx_entries_project ON entries(project_id);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    archived: bool = False


@dataclass(frozen=True)
class Entry:
    id: int
    project_id: int
    project_name: str
    start: datetime
    end: datetime | None
    last_seen: datetime | None = None

    @property
    def is_running(self) -> bool:
        return self.end is None

    def duration_seconds(self, until: datetime | None = None) -> float:
        end = self.end or until or now()
        return max(0.0, (end - self.start).total_seconds())


class Database:
    def __init__(self, path: Path | str):
        # Autocommit mode: every write is on disk as soon as the call returns.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # --- projects -----------------------------------------------------------------

    def list_projects(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT id, name, archived FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY name COLLATE NOCASE"
        return [Project(r["id"], r["name"], bool(r["archived"])) for r in self._query(sql)]

    def get_project(self, project_id: int) -> Project | None:
        rows = self._query("SELECT id, name, archived FROM projects WHERE id = ?", (project_id,))
        return Project(rows[0]["id"], rows[0]["name"], bool(rows[0]["archived"])) if rows else None

    def add_project(self, name: str) -> int:
        name = _clean_name(name)
        with self._lock:
            rows = self._query("SELECT id, archived FROM projects WHERE name = ?", (name,))
            if rows:
                if not rows[0]["archived"]:
                    raise ValueError(f'A project named "{name}" already exists.')
                # Re-adding a removed project brings it (and its history) back.
                self._execute("UPDATE projects SET archived = 0, name = ? WHERE id = ?", (name, rows[0]["id"]))
                return rows[0]["id"]
            cur = self._execute(
                "INSERT INTO projects (name, created_at) VALUES (?, ?)", (name, to_db(now()))
            )
            return cur.lastrowid

    def rename_project(self, project_id: int, name: str) -> None:
        name = _clean_name(name)
        try:
            self._execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))
        except sqlite3.IntegrityError:
            raise ValueError(f'A project named "{name}" already exists.') from None

    def delete_project(self, project_id: int, keep_entries: bool) -> None:
        """Remove a project. With keep_entries it is only hidden, so its time stays exportable."""
        if keep_entries:
            self._execute("UPDATE projects SET archived = 1 WHERE id = ?", (project_id,))
        else:
            self._execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def count_entries(self, project_id: int) -> int:
        return self._query("SELECT COUNT(*) AS n FROM entries WHERE project_id = ?", (project_id,))[0]["n"]

    # --- entries ------------------------------------------------------------------

    def start_entry(self, project_id: int, start: datetime) -> int:
        ts = to_db(start)
        cur = self._execute(
            "INSERT INTO entries (project_id, start_time, last_seen) VALUES (?, ?, ?)",
            (project_id, ts, ts),
        )
        return cur.lastrowid

    def close_entry(self, entry_id: int, end: datetime) -> None:
        self._execute(
            "UPDATE entries SET end_time = ?, last_seen = NULL WHERE id = ?", (to_db(end), entry_id)
        )

    def touch_entry(self, entry_id: int, when: datetime) -> None:
        self._execute(
            "UPDATE entries SET last_seen = ? WHERE id = ? AND end_time IS NULL", (to_db(when), entry_id)
        )

    def add_entry(self, project_id: int, start: datetime, end: datetime) -> int:
        cur = self._execute(
            "INSERT INTO entries (project_id, start_time, end_time) VALUES (?, ?, ?)",
            (project_id, to_db(start), to_db(end)),
        )
        return cur.lastrowid

    def update_entry(self, entry_id: int, project_id: int, start: datetime, end: datetime) -> None:
        self._execute(
            "UPDATE entries SET project_id = ?, start_time = ?, end_time = ? WHERE id = ?",
            (project_id, to_db(start), to_db(end), entry_id),
        )

    def delete_entry(self, entry_id: int) -> None:
        self._execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    def get_entry(self, entry_id: int) -> Entry | None:
        rows = self._query(_ENTRY_SELECT + " WHERE e.id = ?", (entry_id,))
        return _row_to_entry(rows[0]) if rows else None

    def open_entries(self) -> list[Entry]:
        return [_row_to_entry(r) for r in self._query(_ENTRY_SELECT + " WHERE e.end_time IS NULL ORDER BY e.start_time")]

    def list_entries(
        self,
        start_from: datetime | None = None,
        start_before: datetime | None = None,
        project_id: int | None = None,
        include_open: bool = True,
    ) -> list[Entry]:
        """Entries whose start lies in [start_from, start_before), oldest first."""
        clauses, params = [], []
        if start_from is not None:
            clauses.append("e.start_time >= ?")
            params.append(to_db(start_from))
        if start_before is not None:
            clauses.append("e.start_time < ?")
            params.append(to_db(start_before))
        if project_id is not None:
            clauses.append("e.project_id = ?")
            params.append(project_id)
        if not include_open:
            clauses.append("e.end_time IS NOT NULL")
        sql = _ENTRY_SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.start_time, e.id"
        return [_row_to_entry(r) for r in self._query(sql, tuple(params))]

    def totals_by_project(self, start_from: datetime | None = None, start_before: datetime | None = None) -> dict[int, float]:
        """Tracked seconds per project id (running entries count up to now)."""
        totals: dict[int, float] = {}
        current = now()
        for e in self.list_entries(start_from, start_before):
            totals[e.project_id] = totals.get(e.project_id, 0.0) + e.duration_seconds(current)
        return totals

    # --- settings -----------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self._execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self._execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_bool(self, key: str, default: bool) -> bool:
        value = self.get_setting(key)
        return default if value is None else value == "1"

    def set_bool(self, key: str, value: bool) -> None:
        self.set_setting(key, "1" if value else "0")


_ENTRY_SELECT = (
    "SELECT e.id, e.project_id, p.name AS project_name, e.start_time, e.end_time, e.last_seen "
    "FROM entries e JOIN projects p ON p.id = e.project_id"
)


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        start=from_db(row["start_time"]),
        end=from_db(row["end_time"]),
        last_seen=from_db(row["last_seen"]),
    )


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ValueError("The project name must not be empty.")
    return name
