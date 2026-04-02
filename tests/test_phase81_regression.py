from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from core.datetime_utils import coerce_utc
from models import Signal


def test_coerce_utc_naive() -> None:
    naive = datetime(2025, 12, 9, 14, 0, 0)
    result = coerce_utc(naive)
    assert result is not None
    assert result.tzinfo is not None
    assert result.tzinfo == timezone.utc


def test_coerce_utc_aware() -> None:
    aware = datetime(2025, 12, 9, 14, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    result = coerce_utc(aware)
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_coerce_utc_string() -> None:
    result = coerce_utc("2025-12-09T14:00:00Z")
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_coerce_utc_none() -> None:
    assert coerce_utc(None) is None


def test_coerce_utc_date_object() -> None:
    d = date(2025, 12, 9)
    result = coerce_utc(d)
    assert result is not None
    assert result.year == 2025 and result.month == 12 and result.day == 9
    assert result.tzinfo == timezone.utc


def test_coerce_utc_date_string() -> None:
    result = coerce_utc("2025-12-09")
    assert result is not None
    assert result.year == 2025
    assert result.tzinfo == timezone.utc


def test_datetime_subclass_order() -> None:
    """datetime is a subclass of date — time component must be preserved."""
    dt = datetime(2025, 12, 9, 14, 30, 0)
    result = coerce_utc(dt)
    assert result is not None
    assert result.hour == 14


def test_no_crash_on_subtraction() -> None:
    naive = datetime(2025, 12, 9, 14, 0, 0)
    aware = datetime(2025, 12, 14, 14, 0, 0, tzinfo=timezone.utc)
    a = coerce_utc(aware)
    b = coerce_utc(naive)
    assert a is not None and b is not None
    delta = a - b
    assert delta.days == 5


def test_empty_run_does_not_destroy_prior_signals(client, seeded_case_with_signals):
    case_id = uuid.UUID(seeded_case_with_signals["case_id"])
    prior_count = seeded_case_with_signals["signal_count"]
    engine = seeded_case_with_signals["engine"]

    with patch.multiple(
        "routes.investigate",
        _run_investigation_adapters=AsyncMock(return_value=None),
        _ingest_lda_for_unique_donors=AsyncMock(return_value=None),
        _enrich_fec_evidence_jurisdiction=AsyncMock(return_value=None),
        detect_proximity=lambda *a, **k: [],
        build_signals_from_proximity=lambda *a, **k: [],
        build_signals_from_contract_proximity=lambda *a, **k: [],
        build_signals_from_anomalies=lambda *a, **k: [],
    ):
        response = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json={
                "subject_name": "Test Subject",
                "investigator_handle": seeded_case_with_signals["handle"],
            },
            headers={
                "Authorization": f"Bearer {seeded_case_with_signals['api_key']}",
            },
        )

    assert response.status_code == 422
    assert "Prior signals preserved" in response.json()["detail"]

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        n_after = db.scalar(
            select(func.count()).select_from(Signal).where(Signal.case_file_id == case_id)
        )
    finally:
        db.close()

    assert n_after == prior_count
