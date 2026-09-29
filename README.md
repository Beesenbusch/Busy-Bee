# Busy Bee

A small Windows tray app for tracking the time you spend on projects.

- **Tray menu:** choose a project under *Start tracking*, then *Pause* / *Resume* / *Stop*. Double-click the bee to open the window.
- **Window:** a timer bar, **Projects** (add / rename / delete, with totals for this month and all time), **Time entries & export** (filter by date range and project; add, edit or delete entries; *Export CSV...*) and **Settings**.
- **Icon:** grey bee = idle, bee with ▶ = running, bee with ❚❚ = paused. Hover over it to see the elapsed time.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pythonw busybee.pyw
```

On first start, Busy Bee registers itself to **start with Windows** (per-user `HKCU\...\CurrentVersion\Run`, no admin rights needed). You can turn this off under *Settings*. The registered command points at this folder's `.venv\Scripts\pythonw.exe` and `busybee.pyw`. If you move the folder, start the app once by hand and it will update the command.

## Data

Everything is stored in SQLite at `%USERPROFILE%\.busybee\busybee.db` (*Settings -> Open data folder*). Each start, pause and stop is written immediately. The app records a heartbeat every 30 s while a timer runs. After a crash or power loss, an open entry is closed at its last heartbeat, so you lose at most about 30 s. After a restart, a timer that was running comes back as *paused*, or resumes by itself if you turn that on in *Settings*.

Deleting a project that has entries asks whether to delete the entries too, or only hide the project and keep its time for exports. If you add a project with the same name again, it comes back with its history.

## CSV export

Columns: `Project, Start, End, Duration (H:MM:SS), Hours (decimal)`. Times are local. The export contains the entries currently shown in the list, and a running entry is left out. The default format is `;` with a decimal comma, which opens correctly in German-locale Excel. You can switch to standard `,` / `.` under *Settings*.

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -t .
```
