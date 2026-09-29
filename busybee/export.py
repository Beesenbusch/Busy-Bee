"""CSV export of time entries."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .db import Entry
from .timeutil import fmt_duration, fmt_local

# name -> (label, field delimiter, decimal separator)
CSV_FORMATS = {
    "excel_de": ("Excel, German locale (; and decimal comma)", ";", ","),
    "standard": ("Standard (, and decimal point)", ",", "."),
}
DEFAULT_FORMAT = "excel_de"

HEADER = ["Project", "Start", "End", "Duration", "Hours"]


def write_csv(path: Path | str, entries: Iterable[Entry], fmt: str = DEFAULT_FORMAT) -> int:
    """Write finished entries to `path` and return the number of rows written.

    Entries that are still running are skipped, since they have no end time yet.
    UTF-8 with BOM so Excel shows umlauts in project names correctly.
    """
    _, delimiter, decimal = CSV_FORMATS.get(fmt, CSV_FORMATS[DEFAULT_FORMAT])
    rows = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(HEADER)
        for entry in entries:
            if entry.end is None:
                continue
            seconds = entry.duration_seconds()
            writer.writerow([
                entry.project_name,
                fmt_local(entry.start, seconds=True),
                fmt_local(entry.end, seconds=True),
                fmt_duration(seconds),
                f"{seconds / 3600:.2f}".replace(".", decimal),
            ])
            rows += 1
    return rows
