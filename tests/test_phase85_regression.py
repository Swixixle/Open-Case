"""Phase 8.5 — FEC financial rows must parse and pair (date + entry_type + absence)."""
from __future__ import annotations

import uuid
from datetime import date

from adapters.dedup import make_evidence_hash
from sqlalchemy import select
from engines.temporal_proximity import FINANCIAL_ENTRY_TYPES, _entry_event_dt
from models import CaseFile, EvidenceEntry
from routes.investigate import _parse_event_date


def test_parse_event_date_accepts_fec_style_us_dates() -> None:
    """OpenFEC may return contribution_receipt_date as MM/DD/YYYY or MM-DD-YYYY."""
    assert _parse_event_date("12/13/2025") == date(2025, 12, 13)
    assert _parse_event_date("12-13-2025") == date(2025, 12, 13)
    assert _parse_event_date("2025-12-13") == date(2025, 12, 13)


def test_fec_donation_row_is_pairable(db_session) -> None:
    """
    ORM row shaped like a committed FEC donation (financial_connection, date, not absence)
    must yield a non-None _entry_event_dt — the temporal bucket gate.
    """
    handle = "phase85inv"
    case = CaseFile(
        slug=f"slug-{uuid.uuid4().hex[:12]}",
        title="Phase 8.5 case",
        subject_name="Test Subject",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="",
    )
    db_session.add(case)
    db_session.flush()

    receipt = "12/13/2025"
    d_committed = _parse_event_date(receipt)
    assert d_committed is not None

    eh = make_evidence_hash(
        case.id,
        "FEC",
        "https://www.fec.gov/data/receipts/?test=1",
        receipt,
        12336.0,
        "MASS MUTUAL",
    )
    row = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="FEC Donation: $12,336 to Example",
        body="MASS MUTUAL donated to Example.",
        source_url="https://www.fec.gov/data/receipts/?test=1",
        source_name="FEC",
        adapter_name="FEC",
        date_of_event=d_committed,
        entered_by=handle,
        confidence="confirmed",
        is_absence=False,
        amount=12336.0,
        matched_name="MASS MUTUAL",
        evidence_hash=eh,
    )
    db_session.add(row)
    db_session.commit()

    fetched = db_session.scalar(
        select(EvidenceEntry).where(EvidenceEntry.matched_name == "MASS MUTUAL")
    )
    assert fetched is not None
    assert fetched.entry_type in FINANCIAL_ENTRY_TYPES
    assert fetched.is_absence is False

    result = _entry_event_dt(fetched)
    assert result is not None, (
        f"_entry_event_dt returned None for FEC row. "
        f"date_of_event={repr(fetched.date_of_event)} ({type(fetched.date_of_event).__name__}), "
        f"entry_type={repr(fetched.entry_type)}, is_absence={fetched.is_absence}"
    )
