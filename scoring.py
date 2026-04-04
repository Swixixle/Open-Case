from __future__ import annotations

"""Investigator credibility increments — used by routes after evidence actions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Investigator


def add_credibility(db: Session, handle: str, points: int, reason: str) -> int:
    """Increment credibility; caller should commit."""
    row = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if not row:
        row = Investigator(handle=handle, public_key="", credibility_score=0)
        db.add(row)
        db.flush()
    row.credibility_score = (row.credibility_score or 0) + points
    _ = reason
    db.flush()
    return row.credibility_score or 0
