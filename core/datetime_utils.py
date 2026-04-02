from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def coerce_utc(dt: datetime | str | None) -> Optional[datetime]:
    """
    Return a UTC-aware datetime regardless of input type.
    - If None: return None
    - If naive datetime: attach timezone.utc
    - If aware datetime: convert to UTC
    - If string: parse ISO 8601, then coerce
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = dt.rstrip("Z")
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def coerce_utc_from_date_only(d: date | None) -> Optional[datetime]:
    """Midnight UTC for a calendar date (e.g. evidence date_of_event as date)."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return coerce_utc(d)
    return coerce_utc(datetime.combine(d, time.min))
