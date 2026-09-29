"""Time helpers.

Timestamps are stored in the database as UTC strings in a fixed format, which keeps
them lexically sortable and makes durations correct across DST changes. Everything
shown to the user is converted to local time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

DB_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%Y-%m-%d %H:%M"
DISPLAY_FORMAT_SECONDS = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_db(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive values are interpreted as local time
    return dt.astimezone(timezone.utc).strftime(DB_FORMAT)


def from_db(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, DB_FORMAT).replace(tzinfo=timezone.utc)


def fmt_local(dt: datetime, seconds: bool = False) -> str:
    return dt.astimezone().strftime(DISPLAY_FORMAT_SECONDS if seconds else DISPLAY_FORMAT)


def fmt_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def parse_local(text: str) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM[:SS]' as local time and return an aware datetime."""
    text = text.strip()
    for fmt in (DISPLAY_FORMAT_SECONDS, DISPLAY_FORMAT):
        try:
            return datetime.strptime(text, fmt).astimezone()
        except ValueError:
            pass
    raise ValueError(f"'{text}' is not a valid date/time (expected YYYY-MM-DD HH:MM)")


def parse_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), DATE_FORMAT).date()
    except ValueError:
        raise ValueError(f"'{text}' is not a valid date (expected YYYY-MM-DD)") from None


def local_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day).astimezone()


def month_bounds(d: date) -> tuple[date, date]:
    """First and last day of the month containing d."""
    first = d.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


def previous_month_bounds(d: date) -> tuple[date, date]:
    return month_bounds(d.replace(day=1) - timedelta(days=1))


def fmt_duration(seconds: float) -> str:
    """Format as H:MM:SS; hours are not wrapped at 24."""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
