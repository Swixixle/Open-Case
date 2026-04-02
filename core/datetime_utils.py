from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional


def coerce_utc(dt: datetime | date | str | None) -> Optional[datetime]:
    """
    Return a UTC-aware datetime regardless of input type.

    Handles:
    - None                  → None
    - naive datetime        → attach timezone.utc
    - aware datetime        → convert to UTC
    - date (no time)        → treat as midnight UTC
    - ISO datetime string   → parse then coerce ("2025-12-09T14:00:00Z" etc.)
    - ISO date string       → parse as date then coerce ("2025-12-09")
    """
    if dt is None:
        return None

    if isinstance(dt, str):
        s = dt.rstrip("Z")
        try:
            parsed = datetime.fromisoformat(s)
            return coerce_utc(parsed)
        except ValueError:
            try:
                d = date.fromisoformat(s)
                return coerce_utc(d)
            except ValueError:
                return None

    # datetime check MUST come before date check — datetime is a subclass of date
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)

    return None


def coerce_utc_from_date_only(d: date | None) -> Optional[datetime]:
    """Midnight UTC for a calendar date (e.g. evidence date_of_event as date)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return coerce_utc(d)
    return coerce_utc(datetime.combine(d, time.min))
