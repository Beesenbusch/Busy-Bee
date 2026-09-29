import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from busybee.db import Database
from busybee.export import write_csv
from busybee.timeutil import fmt_duration, from_db, month_bounds, to_db
from busybee.tracker import State, Tracker


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        self.db = Database(self.path)
        self.clock = FakeClock()
        self.tracker = Tracker(self.db, self.clock)
        self.a = self.db.add_project("Alpha")
        self.b = self.db.add_project("Beta")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reopen(self):
        self.db.close()
        self.db = Database(self.path)
        self.tracker = Tracker(self.db, self.clock)


class TimeUtilTests(unittest.TestCase):
    def test_roundtrip_and_format(self):
        dt = datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(from_db(to_db(dt)), dt)
        self.assertEqual(fmt_duration(3725), "1:02:05")
        self.assertEqual(fmt_duration(90000), "25:00:00")

    def test_month_bounds(self):
        from datetime import date
        self.assertEqual(month_bounds(date(2026, 2, 14)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(month_bounds(date(2026, 12, 31)), (date(2026, 12, 1), date(2026, 12, 31)))


class ProjectTests(Base):
    def test_duplicate_names_rejected_case_insensitive(self):
        with self.assertRaises(ValueError):
            self.db.add_project("alpha")
        with self.assertRaises(ValueError):
            self.db.rename_project(self.b, "ALPHA")
        with self.assertRaises(ValueError):
            self.db.add_project("   ")

    def test_delete_keeping_entries_archives_and_readd_restores(self):
        self.tracker.start(self.a)
        self.clock.advance(minutes=30)
        self.tracker.stop()
        self.db.delete_project(self.a, keep_entries=True)
        self.assertEqual([p.name for p in self.db.list_projects()], ["Beta"])
        self.assertEqual(len(self.db.list_entries()), 1)
        self.assertEqual(self.db.add_project("Alpha"), self.a)

    def test_delete_with_entries(self):
        self.tracker.start(self.a)
        self.tracker.stop()
        self.db.delete_project(self.a, keep_entries=False)
        self.assertEqual(self.db.list_entries(), [])


class TrackerTests(Base):
    def test_start_pause_resume_stop_saves_each_segment(self):
        self.tracker.start(self.a)
        self.clock.advance(minutes=20)
        self.assertEqual(self.tracker.elapsed_seconds(), 1200)
        self.tracker.pause()
        self.assertIs(self.tracker.state, State.PAUSED)
        self.clock.advance(minutes=10)
        self.tracker.resume()
        self.clock.advance(minutes=5)
        self.tracker.stop()
        self.assertIs(self.tracker.state, State.IDLE)
        durations = [e.duration_seconds() for e in self.db.list_entries()]
        self.assertEqual(durations, [1200, 300])

    def test_switching_project_closes_previous(self):
        self.tracker.start(self.a)
        self.clock.advance(minutes=15)
        self.tracker.start(self.b)
        self.clock.advance(minutes=5)
        self.tracker.stop()
        entries = self.db.list_entries()
        self.assertEqual([(e.project_name, e.duration_seconds()) for e in entries], [("Alpha", 900), ("Beta", 300)])

    def test_crash_recovery_closes_at_last_heartbeat(self):
        self.tracker.start(self.a)
        self.clock.advance(minutes=10)
        self.tracker.heartbeat()
        self.clock.advance(hours=5)  # app died; time after the last heartbeat is not counted
        self.reopen()
        orphan = self.tracker.recover(resume=False)
        self.assertIsNotNone(orphan)
        (entry,) = self.db.list_entries()
        self.assertEqual(entry.duration_seconds(), 600)
        self.assertIs(self.tracker.state, State.PAUSED)
        self.assertEqual(self.tracker.project_id, self.a)

    def test_clean_quit_then_resume(self):
        self.tracker.start(self.b)
        self.clock.advance(minutes=45)
        self.tracker.shutdown()
        self.reopen()
        self.clock.advance(hours=12)
        self.assertIsNone(self.tracker.recover(resume=True))
        self.assertIs(self.tracker.state, State.RUNNING)
        self.assertEqual(self.tracker.project_id, self.b)
        first = self.db.list_entries()[0]
        self.assertEqual(first.duration_seconds(), 2700)

    def test_recover_idle_after_stop(self):
        self.tracker.start(self.a)
        self.tracker.stop()
        self.reopen()
        self.tracker.recover(resume=True)
        self.assertIs(self.tracker.state, State.IDLE)


class ExportTests(Base):
    def test_csv_skips_running_and_uses_format(self):
        self.tracker.start(self.a)
        self.clock.advance(minutes=90)
        self.tracker.start(self.b)  # still running at export time
        out = Path(self.tmp.name) / "out.csv"
        rows = write_csv(out, self.db.list_entries(), "excel_de")
        self.assertEqual(rows, 1)
        with open(out, encoding="utf-8-sig", newline="") as f:
            data = list(csv.reader(f, delimiter=";"))
        self.assertEqual(data[0], ["Project", "Start", "End", "Duration", "Hours"])
        self.assertEqual(data[1][0], "Alpha")
        self.assertEqual(data[1][3:], ["1:30:00", "1,50"])


if __name__ == "__main__":
    unittest.main()
