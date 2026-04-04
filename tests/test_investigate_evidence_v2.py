"""Investigate pipeline — fec_historical, amendment_vote, fec_jfc_donor, govinfo_hearings."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from adapters.base import AdapterResponse, AdapterResult
from adapters.congress_votes import CongressVotesAdapter
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.usa_spending import USASpendingAdapter
from models import EvidenceEntry, SubjectProfile


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


def _hist_result(cycle: int, amt: float = 50.0) -> AdapterResult:
    return AdapterResult(
        source_name="FEC",
        source_url="https://www.fec.gov/data/receipts/",
        entry_type="financial_connection",
        title=f"Historical {cycle}",
        body="h",
        date_of_event="2024-03-15",
        amount=amt,
        matched_name="Hist Donor",
        raw_data={
            "contribution_receipt_date": "2024-03-15",
            "contribution_receipt_amount": amt,
            "contributor_name": "Hist Donor",
            "sub_id": f"sub-{cycle}-{amt}",
            "entity_type": "IND",
            "committee": {"name": "C", "committee_type": "O"},
        },
    )


def test_investigate_stores_fec_historical_and_source_status(
    client, seeded_public_official_case, test_engine
) -> None:
    case_id = seeded_public_official_case["case_id"]
    api_key = seeded_public_official_case["api_key"]
    handle = seeded_public_official_case["handle"]

    # SubjectProfile with bioguide so amendment/hearing blocks have bg (still mocked).
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        db.add(
            SubjectProfile(
                case_file_id=uuid.UUID(case_id),
                subject_name="Test",
                subject_type="official",
                bioguide_id="Y000064",
            )
        )
        db.commit()
    finally:
        db.close()

    async def fec_search(query: str, query_type: str = "person", **kw):
        if kw.get("two_year_transaction_period") == 2024:
            return AdapterResponse(
                source_name="FEC",
                query=query,
                results=[_hist_result(2024)],
                found=True,
                credential_mode="ok",
            )
        if kw.get("two_year_transaction_period") == 2022:
            return AdapterResponse(
                source_name="FEC",
                query=query,
                results=[_hist_result(2022, amt=75.0)],
                found=True,
                credential_mode="ok",
            )
        assert query_type == "committee"
        return AdapterResponse(
            source_name="FEC",
            query=query,
            results=[
                AdapterResult(
                    source_name="FEC",
                    source_url="https://www.fec.gov/data/receipts/",
                    entry_type="financial_connection",
                    title="Current",
                    body="c",
                    date_of_event="2025-06-15",
                    amount=500.0,
                    matched_name="ACME PAC",
                    raw_data={
                        "contribution_receipt_date": "2025-06-15",
                        "committee": {"name": "Friends"},
                    },
                )
            ],
            found=True,
            credential_mode="ok",
        )

    async def fake_amend(*_a, **_kw):
        return []

    def _cred(name: str):
        return {"congress": "x", "govinfo": None}.get(name)

    with (
        patch(
            "routes.investigate.resolve_principal_committee_id_for_official",
            new_callable=AsyncMock,
            return_value="C00459255",
        ),
        patch.object(FECAdapter, "search", new_callable=AsyncMock, side_effect=fec_search),
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
            return_value=_stub_empty("Indiana"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty(CongressVotesAdapter.source_name),
        ),
        patch(
            "routes.investigate.fetch_amendment_votes_for_member",
            new_callable=AsyncMock,
            side_effect=fake_amend,
        ),
        patch(
            "routes.investigate.CredentialRegistry.get_credential",
            side_effect=_cred,
        ),
        patch(
            "routes.investigate.list_committee_hearing_witness_records",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "routes.investigate.get_or_refresh_senator_committees",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        r = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json={
                "investigator_handle": handle,
                "subject_name": "Test Subject",
                "proximity_days": 30,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert r.status_code == 200
    data = r.json()
    adapters = {s.get("adapter"): s for s in data.get("source_statuses", [])}
    assert "fec_historical" in adapters
    assert "2024" in (adapters["fec_historical"].get("detail") or "")

    db2 = Session()
    try:
        hist = db2.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == uuid.UUID(case_id),
                EvidenceEntry.entry_type == "fec_historical",
            )
        ).all()
        assert len(hist) == 2
        raw = json.loads(hist[0].raw_data_json or "{}")
        assert raw.get("fec_cycle") in (2024, 2022)
        amd = [s for s in data.get("source_statuses", []) if s.get("adapter") == "congress_amendments"]
        assert amd and amd[0].get("status") == "clean"
    finally:
        db2.close()


def test_investigate_stores_amendment_votes_when_mocked(client, seeded_public_official_case, test_engine) -> None:
    case_id = seeded_public_official_case["case_id"]
    api_key = seeded_public_official_case["api_key"]
    handle = seeded_public_official_case["handle"]

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        db.add(
            SubjectProfile(
                case_file_id=uuid.UUID(case_id),
                subject_name="Test",
                subject_type="official",
                bioguide_id="Y000064",
            )
        )
        db.commit()
    finally:
        db.close()

    async def fec_search(query: str, query_type: str = "person", **kw):
        if kw.get("two_year_transaction_period"):
            return AdapterResponse(
                source_name="FEC",
                query=query,
                results=[],
                found=True,
                credential_mode="ok",
                parse_warning="0 hist",
            )
        return AdapterResponse(
            source_name="FEC",
            query=query,
            results=[],
            found=True,
            empty_success=True,
            credential_mode="ok",
            parse_warning="0",
        )

    fake_row = {
        "vote_date": "2026-04-20",
        "congress": 119,
        "amendment_number": "142",
        "bill_number": "S. 50",
        "amendment_description": "To delay enforcement of section 100",
        "vote_position": "Yea",
        "source_url": "https://www.congress.gov/test-vote",
        "entry_type": "amendment_vote",
    }

    def _cred(name: str):
        if name == "congress":
            return "fake-congress-key"
        if name == "govinfo":
            return None
        return None

    with (
        patch(
            "routes.investigate.resolve_principal_committee_id_for_official",
            new_callable=AsyncMock,
            return_value="C00999000",
        ),
        patch.object(FECAdapter, "search", new_callable=AsyncMock, side_effect=fec_search),
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
            return_value=_stub_empty("Indiana"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty(CongressVotesAdapter.source_name),
        ),
        patch(
            "routes.investigate.fetch_amendment_votes_for_member",
            new_callable=AsyncMock,
            return_value=[fake_row],
        ),
        patch(
            "routes.investigate.CredentialRegistry.get_credential",
            side_effect=_cred,
        ),
        patch(
            "routes.investigate.list_committee_hearing_witness_records",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "routes.investigate.get_or_refresh_senator_committees",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        r = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json={
                "investigator_handle": handle,
                "subject_name": "Test Subject",
                "proximity_days": 30,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert r.status_code == 200
    db2 = Session()
    try:
        rows = db2.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == uuid.UUID(case_id),
                EvidenceEntry.entry_type == "amendment_vote",
            )
        ).all()
        assert len(rows) == 1
        raw = json.loads(rows[0].raw_data_json or "{}")
        assert raw.get("bill_number") == "S. 50"
    finally:
        db2.close()
