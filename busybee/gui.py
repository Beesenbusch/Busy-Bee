"""Main window: timer controls, projects, time entries / CSV export, rules and settings."""

from __future__ import annotations

import os
import tkinter as tk
from datetime import date, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from . import APP_NAME, __version__, autostart, paths
from .db import Entry, Project
from .export import CSV_FORMATS, DEFAULT_FORMAT, write_csv
from .rules import ACTION_LABELS, FOCUS_CHECK_DEFAULT_S, FOCUS_HOLD_DEFAULT_S, SWITCH, Rule, clean_rule
from .timeutil import (
    DATE_FORMAT,
    fmt_duration,
    fmt_local,
    fmt_time,
    local_midnight,
    month_bounds,
    now,
    parse_date,
    parse_local,
    previous_month_bounds,
)
from .tracker import State
from .windows import WindowInfo, open_windows

if TYPE_CHECKING:
    from .app import App

PAD = 8


class MainWindow(ttk.Frame):
    """Lives inside the (normally withdrawn) Tk root, so it gets a real taskbar button."""

    def __init__(self, app: App):
        super().__init__(app.root, padding=PAD)
        self.app = app
        self.db = app.db
        self.pack(fill="both", expand=True)

        self._build_timer_bar()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, pady=(PAD, 0))
        self.projects_tab = ProjectsTab(self.notebook, self)
        self.entries_tab = EntriesTab(self.notebook, self)
        self.rules_tab = RulesTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.notebook.add(self.projects_tab, text="Projects")
        self.notebook.add(self.entries_tab, text="Time entries & export")
        self.notebook.add(self.rules_tab, text="Rules")
        self.notebook.add(self.settings_tab, text="Settings")
        self.tabs = {"projects": self.projects_tab, "entries": self.entries_tab, "rules": self.rules_tab, "settings": self.settings_tab}

        self.refresh_all()
        self._tick()

    # --- timer bar ----------------------------------------------------------------

    def _build_timer_bar(self) -> None:
        bar = ttk.LabelFrame(self, text="Timer", padding=PAD)
        bar.pack(fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(side="left")

        self.stop_btn = ttk.Button(bar, text="Stop", command=lambda: self.app.stop())
        self.stop_btn.pack(side="right")
        self.pause_btn = ttk.Button(bar, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=(0, 4))
        self.start_btn = ttk.Button(bar, text="Start", command=self._start_selected)
        self.start_btn.pack(side="right", padx=(0, 4))
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(bar, textvariable=self.project_var, state="readonly", width=28)
        self.project_combo.pack(side="right", padx=(0, 4))
        self.project_combo.bind("<<ComboboxSelected>>", lambda e: self._update_timer_buttons())

    def _start_selected(self) -> None:
        project = self._selected_timer_project()
        if project:
            self.app.start_project(project.id)

    def _toggle_pause(self) -> None:
        if self.app.tracker.state is State.RUNNING:
            self.app.pause()
        else:
            self.app.resume()

    def _selected_timer_project(self) -> Project | None:
        name = self.project_var.get()
        return next((p for p in self.app.projects if p.name == name), None)

    def refresh_timer(self) -> None:
        tracker = self.app.tracker
        names = [p.name for p in self.app.projects]
        self.project_combo["values"] = names
        if tracker.project_name in names:
            self.project_var.set(tracker.project_name)
        elif self.project_var.get() not in names:
            self.project_var.set(names[0] if names else "")
        self._update_status_text()
        self._update_timer_buttons()

    def _update_status_text(self) -> None:
        tracker = self.app.tracker
        if tracker.state is State.RUNNING:
            self.status_var.set(
                f"● {tracker.project_name}  {fmt_duration(tracker.elapsed_seconds())}  (since {fmt_time(tracker.entry_start)})"
            )
        elif tracker.state is State.PAUSED:
            self.status_var.set(f"❚❚ {tracker.project_name} (paused)")
        else:
            self.status_var.set("Not tracking")

    def _update_timer_buttons(self) -> None:
        tracker = self.app.tracker
        selected = self._selected_timer_project()
        running_selected = tracker.state is State.RUNNING and selected and selected.id == tracker.project_id
        self.start_btn.configure(
            state="normal" if selected and not running_selected else "disabled",
            text="Switch" if tracker.state is State.RUNNING and not running_selected else "Start",
        )
        self.pause_btn.configure(
            text="Resume" if tracker.state is State.PAUSED else "Pause",
            state="disabled" if tracker.state is State.IDLE else "normal",
        )
        self.stop_btn.configure(state="disabled" if tracker.state is State.IDLE else "normal")

    def _tick(self) -> None:
        if self.app.tracker.state is State.RUNNING:
            self._update_status_text()
        self.after(1000, self._tick)

    # --- refresh ------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_timer()
        self.projects_tab.refresh()
        self.entries_tab.refresh()
        self.rules_tab.refresh()

    def show_tab(self, name: str | None) -> None:
        if name in self.tabs:
            self.notebook.select(self.tabs[name])


class ProjectsTab(ttk.Frame):
    def __init__(self, parent, window: MainWindow):
        super().__init__(parent, padding=PAD)
        self.window = window
        self.app = window.app

        buttons = ttk.Frame(self)
        buttons.pack(side="right", fill="y", padx=(PAD, 0))
        ttk.Button(buttons, text="Add...", command=self.add).pack(fill="x")
        ttk.Button(buttons, text="Rename...", command=self.rename).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Delete", command=self.delete).pack(fill="x")

        self.tree = _make_tree(self, [("name", "Project", 280, "w"), ("month", "This month", 110, "e"), ("total", "All time", 110, "e")])
        self.tree.bind("<Double-1>", lambda e: self.rename())
        self.tree.bind("<Delete>", lambda e: self.delete())

    def refresh(self) -> None:
        first, _ = month_bounds(date.today())
        month = self.app.db.totals_by_project(local_midnight(first))
        total = self.app.db.totals_by_project()
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for p in self.app.projects:
            self.tree.insert("", "end", iid=str(p.id), values=(p.name, fmt_duration(month.get(p.id, 0)), fmt_duration(total.get(p.id, 0))))
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])

    def _selected(self) -> Project | None:
        sel = self.tree.selection()
        return self.app.db.get_project(int(sel[0])) if sel else None

    def add(self) -> None:
        name = simpledialog.askstring("Add project", "Project name:", parent=self)
        if name is None:
            return
        try:
            self.app.db.add_project(name)
        except ValueError as e:
            messagebox.showerror("Add project", str(e), parent=self)
            return
        self.app.projects_changed()

    def rename(self) -> None:
        project = self._selected()
        if not project:
            return
        name = simpledialog.askstring("Rename project", "New name:", initialvalue=project.name, parent=self)
        if name is None or name.strip() == project.name:
            return
        try:
            self.app.db.rename_project(project.id, name)
        except ValueError as e:
            messagebox.showerror("Rename project", str(e), parent=self)
            return
        self.app.projects_changed()

    def delete(self) -> None:
        project = self._selected()
        if not project:
            return
        count = self.app.db.count_entries(project.id)
        if count == 0:
            if not messagebox.askyesno("Delete project", f'Delete project "{project.name}"?', parent=self):
                return
            keep = False
        else:
            answer = messagebox.askyesnocancel(
                "Delete project",
                f'"{project.name}" has {count} time entries.\n\n'
                "Yes: delete the project AND all its time entries.\n"
                "No: remove the project but keep its entries (they stay in exports).\n"
                "Cancel: do nothing.",
                icon="warning",
                parent=self,
            )
            if answer is None:
                return
            keep = not answer
        if self.app.tracker.project_id == project.id:
            self.app.stop()
        self.app.db.delete_project(project.id, keep_entries=keep)
        self.app.projects_changed()


class EntriesTab(ttk.Frame):
    ALL = "(all projects)"

    def __init__(self, parent, window: MainWindow):
        super().__init__(parent, padding=PAD)
        self.window = window
        self.app = window.app
        self.entries: list[Entry] = []

        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(0, PAD))
        first, last = month_bounds(date.today())
        self.from_var = tk.StringVar(value=first.strftime(DATE_FORMAT))
        self.to_var = tk.StringVar(value=last.strftime(DATE_FORMAT))
        self.filter_project_var = tk.StringVar(value=self.ALL)
        ttk.Label(filters, text="From").pack(side="left")
        ttk.Entry(filters, textvariable=self.from_var, width=11).pack(side="left", padx=4)
        ttk.Label(filters, text="to").pack(side="left")
        ttk.Entry(filters, textvariable=self.to_var, width=11).pack(side="left", padx=4)
        ttk.Button(filters, text="This month", command=lambda: self._set_range(*month_bounds(date.today()))).pack(side="left", padx=(4, 0))
        ttk.Button(filters, text="Last month", command=lambda: self._set_range(*previous_month_bounds(date.today()))).pack(side="left", padx=4)
        self.filter_combo = ttk.Combobox(filters, textvariable=self.filter_project_var, state="readonly", width=22)
        self.filter_combo.pack(side="left", padx=4)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(filters, text="Apply", command=self.refresh).pack(side="left")

        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x", pady=(PAD, 0))
        self.total_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.total_var).pack(side="left")
        ttk.Button(bottom, text="Export CSV...", command=self.export).pack(side="right")
        ttk.Button(bottom, text="Delete", command=self.delete).pack(side="right", padx=(0, PAD * 2))
        ttk.Button(bottom, text="Edit...", command=self.edit).pack(side="right", padx=4)
        ttk.Button(bottom, text="Add...", command=self.add).pack(side="right")

        self.tree = _make_tree(
            self,
            [("project", "Project", 220, "w"), ("start", "Start", 140, "w"), ("end", "End", 140, "w"), ("duration", "Duration", 90, "e")],
            selectmode="extended",
        )
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.tree.bind("<Delete>", lambda e: self.delete())

    def _set_range(self, first: date, last: date) -> None:
        self.from_var.set(first.strftime(DATE_FORMAT))
        self.to_var.set(last.strftime(DATE_FORMAT))
        self.refresh()

    def _filters(self):
        start, end = parse_date(self.from_var.get()), parse_date(self.to_var.get())
        if end < start:
            raise ValueError("The end date is before the start date.")
        project = next((p for p in self.app.all_projects() if p.name == self.filter_project_var.get()), None)
        return local_midnight(start), local_midnight(end + timedelta(days=1)), project

    def refresh(self) -> None:
        names = [p.name for p in self.app.all_projects()]
        self.filter_combo["values"] = [self.ALL, *names]
        if self.filter_project_var.get() not in names:
            self.filter_project_var.set(self.ALL)
        try:
            start, before, project = self._filters()
        except ValueError as e:
            self.total_var.set(str(e))
            return
        self.entries = self.app.db.list_entries(start, before, project.id if project else None)
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        current = now()
        total = 0.0
        for e in self.entries:
            seconds = e.duration_seconds(current)
            total += seconds
            end = "running..." if e.is_running else fmt_local(e.end)
            self.tree.insert("", "end", iid=str(e.id), values=(e.project_name, fmt_local(e.start), end, fmt_duration(seconds)))
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])
        if self.entries:
            self.tree.see(str(self.entries[-1].id))
        self.total_var.set(f"{len(self.entries)} entries, total {fmt_duration(total)} ({total / 3600:.2f} h)")

    def _selected_entries(self) -> list[Entry]:
        ids = {int(i) for i in self.tree.selection()}
        return [e for e in self.entries if e.id in ids]

    def add(self) -> None:
        if not self.app.projects:
            messagebox.showinfo("Add entry", "Add a project first.", parent=self)
            return
        result = EntryDialog.ask(self, self.app, None)
        if result:
            self.app.db.add_entry(*result)
            self.app.entries_changed()

    def edit(self) -> None:
        selected = self._selected_entries()
        if len(selected) != 1:
            return
        entry = selected[0]
        if entry.is_running:
            messagebox.showinfo("Edit entry", "This entry is still running. Pause or stop the timer first.", parent=self)
            return
        result = EntryDialog.ask(self, self.app, entry)
        if result:
            self.app.db.update_entry(entry.id, *result)
            self.app.entries_changed()

    def delete(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        if any(e.is_running for e in selected):
            messagebox.showinfo("Delete entries", "A selected entry is still running. Pause or stop the timer first.", parent=self)
            return
        if not messagebox.askyesno("Delete entries", f"Delete {len(selected)} selected entr{'y' if len(selected) == 1 else 'ies'}?", parent=self):
            return
        for e in selected:
            self.app.db.delete_entry(e.id)
        self.app.entries_changed()

    def export(self) -> None:
        self.refresh()
        try:
            start, before, project = self._filters()
        except ValueError as e:
            messagebox.showerror("Export CSV", str(e), parent=self)
            return
        suffix = f"_{_safe_filename(project.name)}" if project else ""
        default = f"busybee_{start.strftime(DATE_FORMAT)}_to_{(before - timedelta(days=1)).strftime(DATE_FORMAT)}{suffix}.csv"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export time entries",
            defaultextension=".csv",
            initialfile=default,
            initialdir=self.app.db.get_setting("export.last_dir") or os.path.expanduser("~"),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        fmt = self.app.db.get_setting("export.format", DEFAULT_FORMAT)
        try:
            rows = write_csv(path, self.entries, fmt)
        except OSError as e:
            messagebox.showerror("Export CSV", f"Could not write the file:\n{e}", parent=self)
            return
        self.app.db.set_setting("export.last_dir", os.path.dirname(path))
        skipped = sum(1 for e in self.entries if e.is_running)
        note = "\n\nThe running entry was not included (it has no end time yet)." if skipped else ""
        messagebox.showinfo("Export CSV", f"Exported {rows} entries to\n{path}{note}", parent=self)


class RulesTab(ttk.Frame):
    def __init__(self, parent, window: MainWindow):
        super().__init__(parent, padding=PAD)
        self.window = window
        self.app = window.app

        ttk.Label(
            self,
            text="When a window that matches a rule keeps focus long enough, Busy Bee carries out the rule's action. "
            "Windows without a rule change nothing. If several rules match, the most specific one wins. "
            "Timing and the on/off switch are under Settings.",
            foreground="gray",
            wraplength=740,
            justify="left",
        ).pack(side="top", anchor="w", pady=(0, PAD))

        buttons = ttk.Frame(self)
        buttons.pack(side="right", fill="y", padx=(PAD, 0))
        ttk.Button(buttons, text="Add...", command=self.add).pack(fill="x")
        ttk.Button(buttons, text="Edit...", command=self.edit).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Delete", command=self.delete).pack(fill="x")

        self.tree = _make_tree(self, [("title", "Window title contains", 280, "w"), ("exe", "Program", 150, "w"), ("action", "Action", 220, "w")])
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.tree.bind("<Delete>", lambda e: self.delete())

    def refresh(self) -> None:
        projects = {p.id: p for p in self.app.all_projects()}
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for r in self.app.rules:
            self.tree.insert("", "end", iid=str(r.id), values=(r.title_contains or "(any)", r.exe or "(any)", _rule_action_text(r, projects)))
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])

    def _selected(self) -> Rule | None:
        sel = self.tree.selection()
        return next((r for r in self.app.rules if str(r.id) == sel[0]), None) if sel else None

    def add(self) -> None:
        result = RuleDialog.ask(self, self.app, None)
        if result:
            self.app.db.add_rule(*result)
            self.app.rules_changed()

    def edit(self) -> None:
        rule = self._selected()
        if not rule:
            return
        result = RuleDialog.ask(self, self.app, rule)
        if result:
            self.app.db.update_rule(rule.id, *result)
            self.app.rules_changed()

    def delete(self) -> None:
        rule = self._selected()
        if rule and messagebox.askyesno("Delete rule", "Delete the selected rule?", parent=self):
            self.app.db.delete_rule(rule.id)
            self.app.rules_changed()


def _rule_action_text(rule: Rule, projects: dict[int, Project]) -> str:
    if rule.action != SWITCH:
        return ACTION_LABELS[rule.action]
    project = projects.get(rule.project_id)
    if project is None or project.archived:
        name = project.name if project else "?"
        return f"Switch to {name} (project removed, rule inactive)"
    return f"Switch to {project.name}"


class SettingsTab(ttk.Frame):
    def __init__(self, parent, window: MainWindow):
        super().__init__(parent, padding=PAD * 2)
        self.app = window.app
        db = self.app.db

        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self.resume_var = tk.BooleanVar(value=db.get_bool("resume_on_start", False))
        self.notify_var = tk.BooleanVar(value=db.get_bool("notifications", True))

        ttk.Checkbutton(self, text="Start Busy Bee when I sign in to Windows", variable=self.autostart_var, command=self._save_autostart).pack(anchor="w")
        ttk.Checkbutton(
            self,
            text="Resume a running timer automatically after a restart\n(otherwise it is restored as paused)",
            variable=self.resume_var,
            command=lambda: db.set_bool("resume_on_start", self.resume_var.get()),
        ).pack(anchor="w", pady=(PAD, 0))
        ttk.Checkbutton(
            self,
            text="Show a notification when the timer is started, paused or stopped from the tray or by a rule",
            variable=self.notify_var,
            command=lambda: db.set_bool("notifications", self.notify_var.get()),
        ).pack(anchor="w", pady=(PAD, 0))

        auto = ttk.LabelFrame(self, text="Automatic switching", padding=PAD)
        auto.pack(anchor="w", fill="x", pady=(PAD * 2, 0))
        self.auto_var = tk.BooleanVar(value=self.app.auto_switch)
        ttk.Checkbutton(
            auto,
            text="Switch projects automatically based on the focused window (see the Rules tab)",
            variable=self.auto_var,
            command=lambda: self.app.set_auto_switch(self.auto_var.get()),
        ).pack(anchor="w")
        self._seconds_row(auto, "Check the focused window every", "rules.check_s", FOCUS_CHECK_DEFAULT_S, 1)
        self._seconds_row(auto, "Carry out a rule once its window has had focus for", "rules.hold_s", FOCUS_HOLD_DEFAULT_S, 0)

        fmt_row = ttk.Frame(self)
        fmt_row.pack(anchor="w", pady=(PAD * 2, 0))
        ttk.Label(fmt_row, text="CSV format:").pack(side="left")
        self._fmt_labels = {label: key for key, (label, _, _) in CSV_FORMATS.items()}
        current = CSV_FORMATS.get(db.get_setting("export.format", DEFAULT_FORMAT), CSV_FORMATS[DEFAULT_FORMAT])[0]
        self.fmt_var = tk.StringVar(value=current)
        combo = ttk.Combobox(fmt_row, textvariable=self.fmt_var, values=list(self._fmt_labels), state="readonly", width=42)
        combo.pack(side="left", padx=PAD)
        combo.bind("<<ComboboxSelected>>", lambda e: db.set_setting("export.format", self._fmt_labels[self.fmt_var.get()]))

        data_row = ttk.Frame(self)
        data_row.pack(anchor="w", fill="x", pady=(PAD * 2, 0))
        ttk.Button(data_row, text="Open data folder", command=lambda: os.startfile(paths.data_dir())).pack(side="left")
        ttk.Label(data_row, text=str(paths.data_dir()), foreground="gray").pack(side="left", padx=PAD)

        ttk.Label(self, text=f"{APP_NAME} {__version__}", foreground="gray").pack(side="bottom", anchor="w")

    def refresh(self) -> None:
        self.auto_var.set(self.app.auto_switch)

    def _seconds_row(self, parent, label: str, key: str, default: int, minimum: int) -> None:
        row = ttk.Frame(parent)
        row.pack(anchor="w", pady=(PAD // 2, 0))
        ttk.Label(row, text=label).pack(side="left")
        var = tk.StringVar(value=str(self.app.db.get_int(key, default)))
        ttk.Spinbox(row, from_=minimum, to=3600, increment=5, textvariable=var, width=6).pack(side="left", padx=4)
        ttk.Label(row, text="seconds").pack(side="left")

        def save(*_):
            text = var.get().strip()
            if text.isdigit() and minimum <= int(text) <= 3600:  # ignore half-typed values
                self.app.db.set_setting(key, text)
                self.app.focus_timing_changed()

        var.trace_add("write", save)

    def _save_autostart(self) -> None:
        try:
            autostart.set_enabled(self.autostart_var.get())
        except OSError as e:
            messagebox.showerror("Settings", f"Could not change the autostart setting:\n{e}", parent=self)
            self.autostart_var.set(autostart.is_enabled())


class EntryDialog(tk.Toplevel):
    """Modal dialog to add or edit a finished time entry."""

    def __init__(self, parent, app: App, entry: Entry | None):
        super().__init__(parent)
        self.result = None
        self.title("Edit entry" if entry else "Add entry")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)

        self.projects = app.projects
        if entry and not any(p.id == entry.project_id for p in self.projects):
            self.projects = [*self.projects, Project(entry.project_id, entry.project_name, True)]

        if entry:
            start, end, project_name = fmt_local(entry.start), fmt_local(entry.end), entry.project_name
        else:
            end_dt = now()
            start, end = fmt_local(end_dt - timedelta(hours=1)), fmt_local(end_dt)
            project_name = app.tracker.project_name or self.projects[0].name

        body = ttk.Frame(self, padding=PAD * 2)
        body.pack(fill="both")
        self.project_var = tk.StringVar(value=project_name)
        self.start_var = tk.StringVar(value=start)
        self.end_var = tk.StringVar(value=end)
        ttk.Label(body, text="Project").grid(row=0, column=0, sticky="w")
        ttk.Combobox(body, textvariable=self.project_var, values=[p.name for p in self.projects], state="readonly", width=30).grid(row=0, column=1, pady=2)
        ttk.Label(body, text="Start (YYYY-MM-DD HH:MM)").grid(row=1, column=0, sticky="w", padx=(0, PAD))
        start_entry = ttk.Entry(body, textvariable=self.start_var, width=32)
        start_entry.grid(row=1, column=1, pady=2)
        ttk.Label(body, text="End (YYYY-MM-DD HH:MM)").grid(row=2, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.end_var, width=32).grid(row=2, column=1, pady=2)

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(PAD, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        _center_over(self, parent.winfo_toplevel())
        self.grab_set()
        start_entry.focus_set()

    def _ok(self) -> None:
        try:
            start, end = parse_local(self.start_var.get()), parse_local(self.end_var.get())
        except ValueError as e:
            messagebox.showerror(self.title(), str(e), parent=self)
            return
        if end <= start:
            messagebox.showerror(self.title(), "The end must be after the start.", parent=self)
            return
        project = next(p for p in self.projects if p.name == self.project_var.get())
        self.result = (project.id, start, end)
        self.destroy()

    @classmethod
    def ask(cls, parent, app: App, entry: Entry | None):
        dialog = cls(parent, app, entry)
        parent.wait_window(dialog)
        return dialog.result


class RuleDialog(tk.Toplevel):
    """Modal dialog to add or edit a rule; picking an open window fills in the fields."""

    def __init__(self, parent, app: App, rule: Rule | None):
        super().__init__(parent)
        self.result = None
        self.title("Edit rule" if rule else "Add rule")
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.projects = app.projects
        self.windows: list[WindowInfo] = []

        current = next((p.name for p in self.projects if rule and p.id == rule.project_id), None)
        default_project = current or app.tracker.project_name or (self.projects[0].name if self.projects else "")
        self.window_var = tk.StringVar()
        self.exe_var = tk.StringVar(value=rule.exe if rule else "")
        self.title_var = tk.StringVar(value=rule.title_contains if rule else "")
        self.action_var = tk.StringVar(value=ACTION_LABELS[rule.action if rule else SWITCH])
        self.project_var = tk.StringVar(value=default_project)

        body = ttk.Frame(self, padding=PAD * 2)
        body.pack(fill="both")
        ttk.Label(body, text="Pick an open window").grid(row=0, column=0, sticky="w", padx=(0, PAD))
        picker = ttk.Frame(body)
        picker.grid(row=0, column=1, sticky="w", pady=2)
        self.window_combo = ttk.Combobox(picker, textvariable=self.window_var, state="readonly", width=52)
        self.window_combo.pack(side="left")
        self.window_combo.bind("<<ComboboxSelected>>", lambda e: self._window_picked())
        ttk.Button(picker, text="Refresh", command=self._load_windows).pack(side="left", padx=(4, 0))

        ttk.Label(body, text="Program").grid(row=1, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.exe_var, width=30).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(body, text="Window title contains").grid(row=2, column=0, sticky="w")
        title_entry = ttk.Entry(body, textvariable=self.title_var, width=62)
        title_entry.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(
            body,
            text="Leave a field empty to match anything. Titles often change (open file, browser tab), "
            "so keep only the part that stays the same, e.g. the project folder name.",
            foreground="gray",
            wraplength=440,
            justify="left",
        ).grid(row=3, column=1, sticky="w", pady=(0, PAD))

        ttk.Label(body, text="Action").grid(row=4, column=0, sticky="w")
        action_combo = ttk.Combobox(body, textvariable=self.action_var, values=list(ACTION_LABELS.values()), state="readonly", width=28)
        action_combo.grid(row=4, column=1, sticky="w", pady=2)
        action_combo.bind("<<ComboboxSelected>>", lambda e: self._update_project_state())
        ttk.Label(body, text="Project").grid(row=5, column=0, sticky="w")
        self.project_combo = ttk.Combobox(body, textvariable=self.project_var, values=[p.name for p in self.projects], width=28)
        self.project_combo.grid(row=5, column=1, sticky="w", pady=2)

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(PAD, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self._load_windows()
        self._update_project_state()
        _center_over(self, parent.winfo_toplevel())
        self.grab_set()
        title_entry.focus_set()

    def _load_windows(self) -> None:
        self.windows = open_windows()
        self.window_combo["values"] = [w.label for w in self.windows]
        self.window_var.set("")

    def _window_picked(self) -> None:
        window = self.windows[self.window_combo.current()]
        self.exe_var.set(window.exe)
        self.title_var.set(window.title)

    def _action(self) -> str:
        return next(key for key, label in ACTION_LABELS.items() if label == self.action_var.get())

    def _update_project_state(self) -> None:
        self.project_combo.configure(state="readonly" if self._action() == SWITCH else "disabled")

    def _ok(self) -> None:
        project = next((p for p in self.projects if p.name == self.project_var.get()), None)
        try:
            self.result = clean_rule(self.exe_var.get(), self.title_var.get(), self._action(), project.id if project else None)
        except ValueError as e:
            messagebox.showerror(self.title(), str(e), parent=self)
            return
        self.destroy()

    @classmethod
    def ask(cls, parent, app: App, rule: Rule | None):
        dialog = cls(parent, app, rule)
        parent.wait_window(dialog)
        return dialog.result


def _make_tree(parent, columns, selectmode="browse") -> ttk.Treeview:
    frame = ttk.Frame(parent)
    frame.pack(side="left", fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode=selectmode)
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading, anchor=anchor)
        tree.column(key, width=width, anchor=anchor, stretch=key == columns[0][0])
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return tree


def _center_over(dialog: tk.Toplevel, owner: tk.Misc) -> None:
    """Place dialog in the middle of owner, kept on the owner's screen."""
    dialog.withdraw()  # avoid a flash at the default position
    dialog.update_idletasks()
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    # geometry() positions the outer frame; subtract the border/title bar size
    # (taken from the owner) so the dialog's content is what ends up centred.
    border_x = owner.winfo_rootx() - owner.winfo_x()
    border_y = owner.winfo_rooty() - owner.winfo_y()
    x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2 - border_x
    y = owner.winfo_rooty() + (owner.winfo_height() - height) // 2 - border_y
    x = max(owner.winfo_vrootx(), x)
    y = max(owner.winfo_vrooty(), y)
    dialog.geometry(f"+{x}+{y}")
    dialog.deiconify()


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
