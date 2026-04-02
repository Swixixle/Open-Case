"""Phase 8.4 — Congress status honesty and core-run durability."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from adapters.base import AdapterResponse
from adapters.congress_votes import CongressVotesAdapter
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.usa_spending import USASpendingAdapter
from models import EvidenceEntry


def _stub_empty(source_name: str) -> AdapterResponse:
    return AdapterResponse(
        source_name=source_name,
        query="q",
        results=[],
        found=True,
        empty_success=True,
        parse_warning="stub",
        credential_mode="ok",
    )


def _evidence_count(engine, case_id: uuid.UUID) -> int:
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        return (
            db.scalar(
                select(func.count()).select_from(EvidenceEntry).where(
                    EvidenceEntry.case_file_id == case_id
                )
            )
            or 0
        )
    finally:
        db.close()


@pytest.fixture
def mock_congress_network_error() -> httpx.ConnectTimeout:
    """Simulated transport failure against Senate LIS (httpx has no NetworkError)."""
    return httpx.ConnectTimeout("timeout")


def test_congress_failure_does_not_report_clean(
    client, seeded_public_official_case, mock_congress_network_error
) -> None:
    """
    When Congress/LIS raises a transport error, source_statuses must not report 'clean'.
    """
    case_id = seeded_public_official_case["case_id"]
    handle = seeded_public_official_case["handle"]
    api_key = seeded_public_official_case["api_key"]

    with (
        patch.object(
            FECAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("FEC"),
        ),
        patch.object(
            USASpendingAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("USASpending"),
        ),
        patch.object(
            IndianaCFAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("Indiana Campaign Finance"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new_callable=AsyncMock,
            side_effect=mock_congress_network_error,
        ),
    ):
        response = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json={
                "subject_name": "Test Subject",
                "investigator_handle": handle,
                "bioguide_id": "Y000064",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

    data = response.json()
    congress_status = next(
        (s for s in data["source_statuses"] if s["adapter"] == "congress"), None
    )
    assert congress_status is not None
    assert congress_status["status"] != "clean"
    assert response.status_code == 422


def test_congress_failure_does_not_promote_partial_evidence(
    client, seeded_case_with_evidence, mock_congress_network_error
) -> None:
    """
    If Congress fails, new evidence must not be committed; prior rows stay.
    """
    case_uuid = uuid.UUID(seeded_case_with_evidence["case_id"])
    prior_evidence_count = seeded_case_with_evidence["evidence_count"]
    handle = seeded_case_with_evidence["handle"]
    api_key = seeded_case_with_evidence["api_key"]
    engine = seeded_case_with_evidence["engine"]

    with (
        patch.object(
            FECAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("FEC"),
        ),
        patch.object(
            USASpendingAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("USASpending"),
        ),
        patch.object(
            IndianaCFAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("Indiana Campaign Finance"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new_callable=AsyncMock,
            side_effect=mock_congress_network_error,
        ),
    ):
        response = client.post(
            f"/api/v1/cases/{case_uuid}/investigate",
            json={
                "subject_name": "Test Subject",
                "investigator_handle": handle,
                "bioguide_id": "Y000064",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 422
    assert "Required adapters failed" in response.json()["detail"]

    evidence_after = _evidence_count(engine, case_uuid)
    assert evidence_after == prior_evidence_count
