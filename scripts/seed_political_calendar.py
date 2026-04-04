"""
Seed political_events with known high-donation dates.

Run after migration:
  PYTHONPATH=. python scripts/seed_political_calendar.py

Safe to re-run: skips rows that already match (event_date, event_type, state_code).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import PoliticalEvent

EVENTS: list[dict] = [
    {
        "event_name": "2025 Q1 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2025, 3, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2025 Q2 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2025, 6, 30),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2025 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2025, 9, 30),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2025 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2025, 12, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2026 Q1 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2026, 3, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2026 Q2 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2026, 6, 30),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2026 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2026, 9, 30),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2026 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2026, 12, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2024 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2024, 12, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2022 Year-End FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2022, 12, 31),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2022 Q3 FEC Deadline",
        "event_type": "FEC_DEADLINE",
        "event_date": date(2022, 9, 30),
        "state_code": None,
        "buffer_days_pre": 7,
        "buffer_days_post": 3,
        "discount_factor": 0.3,
        "congress": None,
    },
    {
        "event_name": "2022 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": date(2022, 11, 8),
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
        "congress": None,
    },
    {
        "event_name": "2024 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": date(2024, 11, 5),
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
        "congress": None,
    },
    {
        "event_name": "2026 General Election",
        "event_type": "GENERAL_ELECTION",
        "event_date": date(2026, 11, 3),
        "state_code": None,
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.2,
        "congress": None,
    },
    {
        "event_name": "2026 Alaska Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 8, 18),
        "state_code": "AK",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Arkansas Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 3, 3),
        "state_code": "AR",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Iowa Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 6, 2),
        "state_code": "IA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Idaho Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 5, 19),
        "state_code": "ID",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Indiana Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 5, 5),
        "state_code": "IN",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Oregon Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 5, 19),
        "state_code": "OR",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2026 Washington Primary",
        "event_type": "PRIMARY",
        "event_date": date(2026, 8, 4),
        "state_code": "WA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2022 Iowa Primary",
        "event_type": "PRIMARY",
        "event_date": date(2022, 6, 7),
        "state_code": "IA",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2022 Idaho Primary",
        "event_type": "PRIMARY",
        "event_date": date(2022, 5, 17),
        "state_code": "ID",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
    {
        "event_name": "2022 Alaska Primary",
        "event_type": "PRIMARY",
        "event_date": date(2022, 8, 16),
        "state_code": "AK",
        "buffer_days_pre": 14,
        "buffer_days_post": 3,
        "discount_factor": 0.4,
        "congress": None,
    },
]


def main() -> None:
    import os

    url = os.environ.get("DATABASE_URL", "sqlite:///./open_case.db")
    eng = create_engine(url)
    with Session(eng) as db:
        inserted = 0
        for row in EVENTS:
            st = row["state_code"]
            existing = db.scalar(
                select(PoliticalEvent.id).where(
                    PoliticalEvent.event_date == row["event_date"],
                    PoliticalEvent.event_type == row["event_type"],
                    PoliticalEvent.state_code.is_(None) if st is None else PoliticalEvent.state_code == st,
                )
            )
            if existing is not None:
                continue
            db.add(
                PoliticalEvent(
                    event_name=row["event_name"],
                    event_type=row["event_type"],
                    event_date=row["event_date"],
                    state_code=st,
                    buffer_days_pre=row["buffer_days_pre"],
                    buffer_days_post=row["buffer_days_post"],
                    discount_factor=row["discount_factor"],
                    congress=row.get("congress"),
                )
            )
            inserted += 1
        db.commit()
        print(f"seed_political_calendar: inserted {inserted} row(s)")


if __name__ == "__main__":
    main()
