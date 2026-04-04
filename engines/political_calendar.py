"""
Political fundraising calendar — overlap-based discount for known spike windows.

Uses `political_events` when populated; otherwise falls back to an embedded default
calendar so detectors work before seeding (tests, fresh DBs).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import PoliticalEvent

_EMBEDDED_EVENTS: list[dict[str, Any]] = [
    {
        "event_name": "2025 Q1 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2025-03-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2025 Q2 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2025-06-30",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2025 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2025-09-30",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2025 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2025-12-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2026 Q1 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2026-03-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2026 Q2 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2026-06-30",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2026 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2026-09-30",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2026 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2026-12-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2024 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2024-12-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2022 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2022-12-31",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2022 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": "2022-09-30",
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
    },
    {
        "event_name": "2022 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": "2022-11-08",
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
    },
    {
        "event_name": "2024 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": "2024-11-05",
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
    },
    {
        "event_name": "2026 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": "2026-11-03",
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
    },
    {
        "event_name": "2026 Alaska Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-08-18",
        "state_code": "AK",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Arkansas Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-03-03",
        "state_code": "AR",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Iowa Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-06-02",
        "state_code": "IA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Idaho Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-05-19",
        "state_code": "ID",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Indiana Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-05-05",
        "state_code": "IN",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Oregon Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-05-19",
        "state_code": "OR",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2026 Washington Primary",
        "event_type": "PRIMARY",
        "event_date": "2026-08-04",
        "state_code": "WA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2022 Iowa Primary",
        "event_type": "PRIMARY",
        "event_date": "2022-06-07",
        "state_code": "IA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2022 Idaho Primary",
        "event_type": "PRIMARY",
        "event_date": "2022-05-17",
        "state_code": "ID",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
    {
        "event_name": "2022 Alaska Primary",
        "event_type": "PRIMARY",
        "event_date": "2022-08-16",
        "state_code": "AK",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
    },
]


@dataclass(frozen=True)
class _CalSpan:
    discount_factor: float
    event_type: str
    event_name: str
    state_code: str | None
    eff_start: date
    eff_end: date


def _norm_state(code: str | None) -> str | None:
    st = (code or "").strip().upper()[:2]
    return st if len(st) == 2 else None


def _embedded_spans() -> list[_CalSpan]:
    out: list[_CalSpan] = []
    for row in _EMBEDDED_EVENTS:
        ds = str(row["event_date"]).strip()[:10]
        ed = date.fromisoformat(ds)
        pre = int(row.get("buffer_days_pre") or 0)
        post = int(row.get("buffer_days_post") or 0)
        out.append(
            _CalSpan(
                discount_factor=float(row["discount_factor"]),
                event_type=str(row["event_type"]),
                event_name=str(row["event_name"]),
                state_code=_norm_state(row.get("state_code")),
                eff_start=ed - timedelta(days=pre),
                eff_end=ed + timedelta(days=post),
            )
        )
    return out


def _db_spans(db: Session) -> list[_CalSpan]:
    out: list[_CalSpan] = []
    for ev in db.scalars(select(PoliticalEvent)).all():
        pre = int(ev.buffer_days_pre or 0)
        post = int(ev.buffer_days_post or 0)
        out.append(
            _CalSpan(
                discount_factor=float(ev.discount_factor),
                event_type=str(ev.event_type),
                event_name=str(ev.event_name),
                state_code=_norm_state(ev.state_code),
                eff_start=ev.event_date - timedelta(days=pre),
                eff_end=ev.event_date + timedelta(days=post),
            )
        )
    return out


def _active_spans(db: Session) -> list[_CalSpan]:
    n = db.scalar(select(func.count()).select_from(PoliticalEvent)) or 0
    if n == 0:
        return _embedded_spans()
    return _db_spans(db)


def _applies_to_state(span: _CalSpan, filter_state: str | None) -> bool:
    if span.state_code is None:
        return True
    return span.state_code == filter_state


def get_calendar_discount(
    db: Session,
    window_start: date,
    window_end: date,
    state_code: str | None = None,
) -> tuple[float, str | None, str | None]:
    """
    Strongest (lowest) discount_factor for any political event overlapping
    [window_start, window_end]. National events always apply; state events apply
    when state_code matches.

    Returns (discount_factor, event_type, event_name), or (1.0, None, None) when none apply.
    """
    if window_start > window_end:
        window_start, window_end = window_end, window_start
    st = _norm_state(state_code)

    best_disc = 1.0
    best_type: str | None = None
    best_name: str | None = None

    for span in _active_spans(db):
        if not _applies_to_state(span, st):
            continue
        if not (window_end >= span.eff_start and window_start <= span.eff_end):
            continue
        if span.discount_factor < best_disc:
            best_disc = span.discount_factor
            best_type = span.event_type
            best_name = span.event_name

    if best_disc >= 1.0:
        return (1.0, None, None)
    return (best_disc, best_type, best_name)
