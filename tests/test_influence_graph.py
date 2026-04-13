"""Phase 13 influence-graph adapters (structured data only, no Perplexity)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from adapters.committee_witnesses import affiliation_from_granule
from adapters.dark_money import (
    _org_type_from_payload,
    pass_through_entities_from_filing,
    top_fec_organization_donors,
)
from adapters.ethics_travel import parse_senate_travel_html
from adapters.stock_act_trades import _parse_trades_enriched
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, SubjectProfile
from auth import generate_raw_key, hash_key


def test_pass_through_flagged_when_grant_text_names_other_donor() -> None:
    blob = "Grants paid to Shadow Pass Through Org for advocacy programs."
    out = pass_through_entities_from_filing(
        5000.0,
        ["Shadow Pass Through Org", "Other Donor"],
        blob,
    )
    assert "Shadow Pass Through Org" in out


def test_pass_through_empty_without_grants() -> None:
    assert (
        pass_through_entities_from_filing(
            0.0,
            ["Child Org"],
            "Child Org appears",
        )
        == []
    )


def test_dark_money_org_not_in_allowed_types_skipped() -> None:
    payload = {"organization": {"name": "Generic Charity", "subsection": "501(c)(3)"}}
    assert _org_type_from_payload(payload) is None


def test_dark_money_org_type_501c4() -> None:
    payload = {"organization": {"subsection": "501(c)(4)", "name": "Issue Org"}}
    assert _org_type_from_payload(payload) == "501c4"


def test_ethics_travel_parse_finds_chunk_with_amount_and_lastname() -> None:
    html = (
        "<html><body>Sponsor: Acme Corp paid for travel $2,500 "
        "Senator Smith (D) attended conference.</body></html>"
    )
    rows = parse_senate_travel_html(html, "Smith")
    assert rows
    assert rows[0]["value"] >= 2500.0


def test_committee_witness_affiliation_from_granule() -> None:
    title = "Statement of Jane Doe, Big Bank NA"
    assert "Big Bank NA" in affiliation_from_granule(title, "Jane Doe")


def test_stock_act_trades_enriched_parse_exchange() -> None:
    raw = (
        '[{"trade_date":"2024-03-01","filedDate":"2024-04-01",'
        '"company_name":"Gamma LLC","transactionType":"exchange",'
        '"assetType":"stock"}]'
    )
    rows = _parse_trades_enriched(raw)
    assert rows[0]["transaction_type"] == "exchange"
    assert rows[0]["filed_date"] == "2024-04-01"


def test_dark_money_propublica_search_404_returns_empty_entities(test_engine) -> None:
    from adapters.dark_money import fetch_dark_money

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    inv = Investigator(handle="dm_t", hashed_api_key=hash_key(raw_key), public_key="")
    db.add(inv)
    case = CaseFile(
        slug=f"dm-{uuid4().hex[:8]}",
        title="t",
        subject_name="Senator DM",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="dm_t",
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(CaseContributor(case_file_id=case.id, investigator_handle="dm_t", role="field"))
    db.add(
        SubjectProfile(
            case_file_id=case.id,
            subject_name=case.subject_name,
            subject_type="public_official",
            bioguide_id="D000099",
        )
    )
    db.add(
        EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title="fec",
            body="b",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            entered_by="dm_t",
            confidence="confirmed",
            amount=100.0,
            matched_name="Test Org PAC",
            raw_data_json='{"contributor_name":"Test Org PAC","contribution_receipt_amount":100}',
        )
    )
    db.commit()
    case_id = case.id
    db.close()

    mock_client = MagicMock()
    mock_instance = MagicMock()
    mock_client.return_value = mock_instance
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=None)
    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status = MagicMock()
    mock_instance.get = AsyncMock(return_value=resp)

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()

    async def _run() -> list:
        with patch("adapters.dark_money.httpx.AsyncClient", mock_client):
            return await fetch_dark_money(db, "D000099", case_id, "KY")

    out = asyncio.run(_run())
    db.close()
    assert out == []


def test_ethics_sponsor_matches_fec_donor_overlap() -> None:
    from adapters.staff_network import _entities_overlap

    assert _entities_overlap("Acme Corporation", "Acme Corp")


def test_committee_witness_lda_match_when_filings_exist(test_engine) -> None:
    from adapters.committee_witnesses import fetch_committee_witnesses
    from models import SenatorCommittee

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    inv = Investigator(handle="cw_t", hashed_api_key=hash_key(raw_key), public_key="")
    db.add(inv)
    case = CaseFile(
        slug=f"cw-{uuid4().hex[:8]}",
        title="t",
        subject_name="Senator CW",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="cw_t",
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title="fec",
            body="b",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            entered_by="cw_t",
            confidence="confirmed",
            amount=50.0,
            matched_name="Other",
            raw_data_json='{"contributor_name":"Other","contribution_receipt_amount":50}',
        )
    )
    db.commit()
    cid = case.id
    db.close()

    committees = [
        SenatorCommittee(
            bioguide_id="C000099",
            committee_name="Test Committee",
            committee_code="SSHR",
        )
    ]
    fake_recs = [
        {
            "committee_code": "SSHR",
            "hearing_title": "Oversight Hearing",
            "date_issued": "2024-06-01",
            "matched_name": "Pat Witness",
            "hearing_granule_title": "Testimony of Pat Witness, LobbyShop Industries",
            "source_url": "https://www.govinfo.gov/app/details/CHRG-118shrg99999",
        }
    ]

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()

    async def _run():
        with patch.dict("os.environ", {"GOVINFO_API_KEY": "fake"}):
            with patch(
                "adapters.committee_witnesses.list_committee_hearing_witness_records",
                new_callable=AsyncMock,
                return_value=fake_recs,
            ):
                with patch(
                    "adapters.committee_witnesses.fetch_lda_filings",
                    new_callable=AsyncMock,
                    return_value=[{"filing_uuid": "u1"}],
                ):
                    return await fetch_committee_witnesses(db, "C000099", committees, cid)

    rows = asyncio.run(_run())
    db.close()
    assert rows
    assert rows[0]["lda_match"] is True


def test_stock_act_trades_empty_when_eftds_fetch_returns_empty(test_engine) -> None:
    from adapters.stock_act_trades import fetch_stock_act_trades_for_year

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    inv = Investigator(handle="st_t", hashed_api_key=hash_key(raw_key), public_key="")
    db.add(inv)
    case = CaseFile(
        slug=f"st-{uuid4().hex[:8]}",
        title="t",
        subject_name="Senator ST",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="st_t",
        summary="",
    )
    db.add(case)
    db.flush()
    db.commit()
    cid = case.id

    async def _run():
        with patch(
            "adapters.stock_act_trades._fetch_eftds_text",
            new_callable=AsyncMock,
            return_value="",
        ):
            with patch(
                "adapters.stock_act_trades._fetch_pp_hearings",
                new_callable=AsyncMock,
                return_value=[],
            ):
                return await fetch_stock_act_trades_for_year(
                    db,
                    "S000088",
                    "Senator ST",
                    [],
                    cid,
                    2024,
                )

    assert asyncio.run(_run()) == []
    db.close()


def test_top_fec_donors_orders_by_total_amount(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    inv = Investigator(handle="td_t", hashed_api_key=hash_key(raw_key), public_key="")
    db.add(inv)
    case = CaseFile(
        slug=f"td-{uuid4().hex[:8]}",
        title="t",
        subject_name="Senator TD",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="td_t",
        summary="",
    )
    db.add(case)
    db.flush()
    for name, amt in [("Small Co", 100.0), ("Big Co", 900.0)]:
        db.add(
            EvidenceEntry(
                case_file_id=case.id,
                entry_type="financial_connection",
                title="fec",
                body="b",
                source_url="https://www.fec.gov/",
                source_name="FEC",
                entered_by="td_t",
                confidence="confirmed",
                amount=amt,
                matched_name=name,
                raw_data_json=(
                    '{"contributor_name":"'
                    + name
                    + '","contribution_receipt_amount":'
                    + str(int(amt))
                    + "}"
                ),
            )
        )
    db.commit()
    cid = case.id
    db.close()

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    ranked = top_fec_organization_donors(db, cid, limit=5)
    db.close()
    assert ranked[0] == "Big Co"
