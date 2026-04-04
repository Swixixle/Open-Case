from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select

from core.datetime_utils import coerce_utc
from models import CaseFile, EvidenceEntry


def test_coerce_utc_from_orm_evidence_row(db_session):
    """
    Write an evidence row with a date-only date_of_event.
    Read it back through the ORM.
    Feed the returned field value into coerce_utc.
    """
    case = CaseFile(
        slug=f"orm-date-{uuid.uuid4().hex[:10]}",
        title="ORM coercion test",
        subject_name="S",
        subject_type="organization",
        jurisdiction="US",
        status="open",
        created_by="tester",
        summary="",
    )
    db_session.add(case)
    db_session.flush()

    row = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="TEST DONOR row",
        body="",
        date_of_event=date(2025, 12, 9),
        entered_by="tester",
        confidence="confirmed",
        amount=2500.0,
        matched_name="TEST DONOR",
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.scalar(
        select(EvidenceEntry).where(EvidenceEntry.matched_name == "TEST DONOR")
    )
    assert fetched is not None
    raw_val = fetched.date_of_event

    result = coerce_utc(raw_val)

    assert result is not None, (
        f"coerce_utc returned None for ORM-returned value: {repr(raw_val)} "
        f"(type: {type(raw_val).__name__})"
    )
    assert isinstance(result, datetime)
    assert result.tzinfo == timezone.utc
    assert result.year == 2025
    assert result.month == 12
    assert result.day == 9
