"""Senator dossier pipeline, staff network, signing, cache, and PDF endpoint."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from models import (
    CaseContributor,
    CaseFile,
    EvidenceEntry,
    Investigator,
    SenatorDossier,
    Signal,
    SubjectProfile,
)
from services.enrichment_service import validate_narrative
from services.senator_dossier import build_senator_dossier
from signing import generate_keypair, pack_signed_hash, verify_signed_hash_string
from adapters.staff_network import (
    _donor_overlap_for_clients,
    extract_subject_meta_from_congress_gov_member,
    parse_staff_from_sonar_assistant_text,
)
from adapters.amendment_fingerprint import analyze_amendment_votes
from adapters.senator_deep_research import (
    fetch_senator_deep_research_category,
    fetch_all_senator_deep_research,
)
from adapters.stock_trade_proximity import flag_trades_against_hearings


@pytest.fixture(autouse=True)
def _dossier_signing_keys():
    priv, pub = generate_keypair()
    old_p = os.environ.get("OPEN_CASE_PRIVATE_KEY")
    old_u = os.environ.get("OPEN_CASE_PUBLIC_KEY")
    old_b = os.environ.get("BASE_URL")
    os.environ["OPEN_CASE_PRIVATE_KEY"] = priv
    os.environ["OPEN_CASE_PUBLIC_KEY"] = pub
    os.environ["BASE_URL"] = "http://testserver"
    yield
    if old_p is None:
        os.environ.pop("OPEN_CASE_PRIVATE_KEY", None)
    else:
        os.environ["OPEN_CASE_PRIVATE_KEY"] = old_p
    if old_u is None:
        os.environ.pop("OPEN_CASE_PUBLIC_KEY", None)
    else:
        os.environ["OPEN_CASE_PUBLIC_KEY"] = old_u
    if old_b is None:
        os.environ.pop("BASE_URL", None)
    else:
        os.environ["BASE_URL"] = old_b


def _investigator_and_case_with_profile(test_engine, *, bioguide_id: str = "T000099") -> tuple[str, str, uuid.UUID]:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "dossier_tester"
    inv = Investigator(
        handle=handle,
        hashed_api_key=hash_key(raw_key),
        public_key="",
    )
    db.add(inv)
    case = CaseFile(
        slug=f"dossier-case-{uuid.uuid4().hex[:10]}",
        title="Senator Dossier Test",
        subject_name="Senator Dossier Test",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="",
    )
    db.add(case)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=handle,
            role="field",
        )
    )
    db.add(
        SubjectProfile(
            case_file_id=case.id,
            subject_name=case.subject_name,
            subject_type="public_official",
            bioguide_id=bioguide_id,
        )
    )
    db.commit()
    cid = case.id
    db.close()
    return raw_key, handle, cid


def _fec_and_vote_gap_fixture(db, case_id: uuid.UUID, handle: str) -> None:
    import hashlib

    raw_f = {
        "contributor_name": "Proximity Donor Inc",
        "contribution_receipt_amount": 2500.0,
        "contribution_receipt_date": "2024-04-01",
    }
    fec = EvidenceEntry(
        case_file_id=case_id,
        entry_type="financial_connection",
        title="FEC",
        body="t",
        source_url="https://www.fec.gov/data/receipts/test/",
        source_name="FEC",
        date_of_event=date.fromisoformat("2024-04-01"),
        entered_by=handle,
        confidence="confirmed",
        amount=2500.0,
        matched_name="Proximity Donor Inc",
        raw_data_json=json.dumps(raw_f, separators=(",", ":")),
    )
    db.add(fec)
    db.flush()
    raw_v = {"question": "Test Bill", "member_vote": "YEA", "result": "PASSED"}
    vote = EvidenceEntry(
        case_file_id=case_id,
        entry_type="vote_record",
        title="vote",
        body="t",
        source_url="https://www.senate.gov/",
        entered_by=handle,
        confidence="confirmed",
        date_of_event=date.fromisoformat("2024-04-10"),
        raw_data_json=json.dumps(raw_v, separators=(",", ":")),
    )
    db.add(vote)
    db.flush()
    bd = {
        "kind": "donor_cluster",
        "donor": "Proximity Donor Inc",
        "official": "Senator Dossier Test",
        "receipt_date": "2024-04-01",
        "total_amount": 2500.0,
        "donation_count": 1,
        "vote_count": 1,
        "pair_count": 1,
        "min_gap_days": 9,
        "median_gap_days": 9.0,
        "exemplar_vote": "Test",
        "exemplar_gap": 9,
        "exemplar_direction": "before",
        "exemplar_position": "YEA",
        "exemplar_financial_date": "2024-04-01",
    }
    ident = hashlib.sha256(f"{case_id}-{fec.id}-{vote.id}".encode()).hexdigest()
    sig = Signal(
        case_file_id=case_id,
        signal_identity_hash=ident,
        signal_type="temporal_proximity",
        weight=0.75,
        description="dossier gap test",
        evidence_ids=json.dumps([str(fec.id), str(vote.id)], separators=(",", ":")),
        actor_a="Proximity Donor Inc",
        actor_b="Senator Dossier Test",
        event_date_a="2024-04-01",
        event_date_b="2024-04-10",
        days_between=9,
        amount=2500.0,
        exposure_state="internal",
        weight_breakdown=json.dumps(bd, separators=(",", ":")),
    )
    db.add(sig)
    db.commit()


def test_staff_network_subject_meta_from_congress_gov_shape() -> None:
    member = {
        "firstName": "Test",
        "lastName": "Senator",
        "directOrderName": "Test Senator",
        "state": "Texas",
        "partyHistory": [{"partyAbbreviation": "D", "partyName": "Democratic"}],
        "terms": [
            {"chamber": "Senate", "startYear": 2015, "stateName": "Texas"},
        ],
    }
    meta = extract_subject_meta_from_congress_gov_member(member)
    assert meta["name"] == "Test Senator"
    assert meta["state"] == "Texas"
    assert meta["party"] == "D"
    assert meta["years_in_office"] >= 1


def test_parse_staff_from_sonar_assistant_text_json() -> None:
    text = '[{"name": "Alex Aide", "role": "Chief of Staff"}]'
    staff = parse_staff_from_sonar_assistant_text(text)
    assert len(staff) == 1
    assert staff[0]["name"] == "Alex Aide"
    assert staff[0]["role_at_office"] == "Chief of Staff"


def test_donor_overlap_lobbying_client_matches_fec_entity() -> None:
    fec = {"Acme Corporation", "Jane Smith"}
    overlap, entities = _donor_overlap_for_clients(["Acme Corp"], fec)
    assert overlap is True
    assert entities


def test_gap_analysis_proximity_within_180_days(test_engine) -> None:
    from services.gap_analysis import generate_gap_sentences

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key, handle, case_id = _investigator_and_case_with_profile(test_engine)
    _fec_and_vote_gap_fixture(db, case_id, handle)
    gaps = generate_gap_sentences(str(case_id), db)
    db.close()
    prox = [g for g in gaps if g["type"] == "donation_vote_proximity"]
    assert prox
    assert "Proximity Donor Inc" in prox[0]["sentence"]


def test_banned_language_not_in_clean_narrative() -> None:
    text = (
        "Public records document filings. Records show no disposition stated. "
        "These findings document public records only. They do not prove causation "
        "or wrongdoing. All findings are for further human review."
    )
    _, flags = validate_narrative(text)
    assert not flags


def test_needs_human_review_when_banned_phrase_in_narrative() -> None:
    text = "Records show allegedly corrupt conduct per reporting."
    _, flags = validate_narrative(text)
    assert flags


def test_deep_research_cache_skips_second_http(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    calls = {"n": 0}

    def fake_http(method, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "[]"}}]},
        )

    with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
        with patch("adapters.senator_deep_research.http_request_with_retry", side_effect=fake_http):
            fetch_senator_deep_research_category(db, "X000001", "Senator X", "recent_news")
            fetch_senator_deep_research_category(db, "X000001", "Senator X", "recent_news")
    db.close()
    assert calls["n"] == 1


def test_dossier_signature_verifies(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _, _, case_id = _investigator_and_case_with_profile(test_engine, bioguide_id="Z000088")
    handle = db.scalar(select(Investigator.handle).where(Investigator.handle == "dossier_tester"))
    assert handle
    _fec_and_vote_gap_fixture(db, case_id, str(handle))

    row = SenatorDossier(
        bioguide_id="Z000088",
        senator_name="Senator Dossier Test",
        dossier_json="{}",
        signature="",
        share_token="abc12def",
        version=1,
        previous_version_id=None,
        status="building",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    deep_bundle = {
        "categories": {
            "recent_news": {
                "claims": [],
                "narrative": (
                    "Public records document activity. Records show filings. "
                    "These findings document public records only. They do not prove causation "
                    "or wrongdoing. All findings are for further human review."
                ),
                "query_errors": [],
                "narrative_validation_flags": [],
                "needs_human_review": False,
            }
        },
        "needs_human_review": False,
        "narrative_validation_flags": [],
    }

    async def fake_staff(*_a, **_k):
        return {
            "staff": [],
            "subject_meta": {
                "name": "Senator Dossier Test",
                "state": "KY",
                "party": "I",
                "committees": [],
                "years_in_office": 5,
            },
            "retrieved_at": "",
            "source_urls": [],
            "from_cache": False,
        }

    with patch("services.senator_dossier.maybe_auto_ingest_case", new_callable=AsyncMock):
        with patch(
            "services.senator_dossier.fetch_all_senator_deep_research",
            return_value=deep_bundle,
        ):
            with patch("services.senator_dossier.fetch_staff_network", new_callable=AsyncMock) as fs:
                fs.side_effect = fake_staff
                with patch(
                    "services.senator_dossier.get_or_refresh_senator_committees",
                    new_callable=AsyncMock,
                    return_value=[],
                ):
                    with patch("services.senator_dossier.run_pattern_engine", return_value=[]):
                        with patch(
                            "services.senator_dossier.fetch_stock_trade_proximity_all_years",
                            new_callable=AsyncMock,
                            return_value=[],
                        ):
                            with patch(
                                "services.senator_dossier.fetch_amendment_fingerprint",
                                new_callable=AsyncMock,
                                return_value={"bioguide_id": "Z000088", "total_amendment_votes": 0},
                            ):
                                with patch(
                                    "services.senator_dossier.fetch_stock_act_trades_all_years",
                                    new_callable=AsyncMock,
                                    return_value=[],
                                ):
                                    with patch(
                                        "services.senator_dossier.fetch_dark_money",
                                        new_callable=AsyncMock,
                                        return_value=[],
                                    ):
                                        with patch(
                                            "services.senator_dossier.fetch_ethics_travel",
                                            new_callable=AsyncMock,
                                            return_value=[],
                                        ):
                                            with patch(
                                                "services.senator_dossier.fetch_committee_witnesses",
                                                new_callable=AsyncMock,
                                                return_value=[],
                                            ):
                                                asyncio.run(build_senator_dossier("Z000088", db))

    db.refresh(row)
    assert row.status == "completed"
    data = json.loads(row.dossier_json)
    body = {k: v for k, v in data.items() if k not in ("content_hash", "signature", "public_key")}
    vr = verify_signed_hash_string(row.signature, body)
    assert vr["ok"], vr
    assert data.get("schema_version") == "2.0"
    assert isinstance(data.get("stock_act_trades"), list)
    assert isinstance(data.get("dark_money"), list)
    assert isinstance(data.get("ethics_travel"), list)
    assert isinstance(data.get("committee_witnesses"), list)
    db.close()


def test_pdf_endpoint_returns_pdf(client, test_engine) -> None:
    """API returns application/pdf when pdfkit is stubbed."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key, _, case_id = _investigator_and_case_with_profile(test_engine, bioguide_id="P000077")
    handle = "dossier_tester"
    _fec_and_vote_gap_fixture(db, case_id, handle)

    did = uuid.uuid4()
    body = {
        "dossier_id": str(did),
        "version": 1,
        "previous_version_id": None,
        "subject": {"name": "T", "bioguide_id": "P000077", "state": "", "party": "", "committees": [], "years_in_office": 0},
        "generated_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:00Z",
        "deep_research": {"categories": {}, "needs_human_review": False, "narrative_validation_flags": []},
        "staff_network": [],
        "gap_analysis": [],
        "pattern_alerts": [],
        "stock_trade_proximity": [],
        "schema_version": "2.0",
        "stock_act_trades": [],
        "dark_money": [],
        "ethics_travel": [],
        "committee_witnesses": [],
        "amendment_fingerprint": {},
        "share_token": "x1y2z3a4",
        "disclaimer": "d",
        "pdf_url": "",
        "public_url": "",
        "verify_url": "",
    }
    from signing import sign_payload

    signed = sign_payload(body)
    packed = pack_signed_hash(signed["content_hash"], signed["signature"], body)
    row = SenatorDossier(
        id=did,
        bioguide_id="P000077",
        senator_name="T",
        dossier_json=json.dumps(signed, separators=(",", ":"), default=str),
        signature=packed,
        share_token="x1y2z3a4",
        version=1,
        previous_version_id=None,
        status="completed",
    )
    db.add(row)
    db.commit()
    db.close()

    with patch(
        "routes.investigate.dossier_to_pdf_bytes",
        return_value=b"%PDF-1.4\n%open-case\n",
    ):
        r = client.get(
            f"/api/v1/dossiers/{did}/pdf",
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/pdf"


def test_fetch_all_marks_review_when_category_narrative_banned(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()

    def fake_cat(db_sess, bg, name, cat):
        nar = "Neutral summary."
        if cat == "recent_news":
            nar = "This is corrupt according to records."
        _, flags = validate_narrative(nar)
        return {
            "claims": [],
            "narrative": nar,
            "query_errors": [],
            "retrieved_at": "",
            "narrative_validation_flags": flags,
            "needs_human_review": bool(flags),
            "from_cache": False,
        }

    with patch("adapters.senator_deep_research.fetch_senator_deep_research_category", side_effect=fake_cat):
        out = fetch_all_senator_deep_research(db, "B1", "Senator B")
    db.close()
    assert out["needs_human_review"] is True


def test_stock_trade_flagged_when_sector_matches_hearing_within_30_days() -> None:
    trades = [
        {
            "trade_date": "2024-06-01",
            "company_name": "Big Pharma Holdings",
            "ticker": "BPH",
            "trade_type": "purchase",
            "amount_range": "$1,001 - $15,000",
            "disclosure_url": "https://efts.senate.gov/disclosure/x",
        }
    ]
    hearings = [
        {
            "committee": "Senate Health Committee",
            "topic": "FDA drug approval process and Medicare prescription rules",
            "date": "2024-06-10",
            "url": "https://www.senate.gov/hearing/1",
        }
    ]
    committees = ["Senate Health Committee"]
    flagged = flag_trades_against_hearings(trades, hearings, committees)
    assert flagged
    assert flagged[0]["sector_match"] is True
    assert abs(flagged[0]["days_between"]) <= 30


def test_stock_trade_not_flagged_when_hearing_beyond_30_days() -> None:
    trades = [
        {
            "trade_date": "2024-06-01",
            "company_name": "Big Pharma Holdings",
            "ticker": "BPH",
            "trade_type": "purchase",
            "amount_range": "$1,001 - $15,000",
            "disclosure_url": "https://efts.senate.gov/disclosure/x",
        }
    ]
    hearings = [
        {
            "committee": "Senate Health Committee",
            "topic": "FDA drug approval process",
            "date": "2024-08-25",
            "url": "https://www.senate.gov/hearing/2",
        }
    ]
    committees = ["Senate Health Committee"]
    flagged = flag_trades_against_hearings(trades, hearings, committees)
    assert not flagged


def test_amendment_alignment_rate_from_mock_votes() -> None:
    votes = [
        {
            "amendment_description": "Bank capital requirements amendment",
            "vote_position": "Yea",
            "bill_number": "S.100",
            "vote_date": "2024-03-01",
            "source_url": "https://www.congress.gov/vote/1",
        },
        {
            "amendment_description": "Corn subsidy pilot",
            "vote_position": "Nay",
            "bill_number": "S.200",
            "vote_date": "2024-03-02",
            "source_url": "https://www.congress.gov/vote/2",
        },
    ]
    out = analyze_amendment_votes(votes, ["finance"])
    assert out["total_amendment_votes"] == 2
    assert out["donor_aligned_votes"] == 1
    assert out["alignment_rate"] == 0.5


def test_enforcement_stripping_vote_detected() -> None:
    votes = [
        {
            "amendment_description": (
                "To reduce civil penalties for reporting violations under the underlying bill"
            ),
            "vote_position": "Yea",
            "bill_number": "S.300",
            "vote_date": "2024-05-01",
            "source_url": "https://www.congress.gov/vote/3",
        }
    ]
    out = analyze_amendment_votes(votes, [])
    assert out["enforcement_stripping_count"] == 1
