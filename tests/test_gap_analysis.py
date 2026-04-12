"""Gap analysis sentence generator (FEC + vote templates)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, Signal
from services.gap_analysis import generate_gap_sentences


def _case_and_inv(db, slug: str, subject: str) -> CaseFile:
    if db.scalar(select(Investigator).where(Investigator.handle == "gap_tester")) is None:
        inv = Investigator(
            handle="gap_tester",
            hashed_api_key=hash_key(generate_raw_key()),
            public_key="",
        )
        db.add(inv)
    c = CaseFile(
        slug=slug,
        title=subject,
        subject_name=subject,
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="gap_tester",
        summary="",
    )
    db.add(c)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=c.id,
            investigator_handle="gap_tester",
            role="field",
        )
    )
    return c


def _fec_row(
    db,
    case_id: uuid.UUID,
    *,
    amount: float,
    receipt_date: str,
    contributor: str,
    url: str = "https://www.fec.gov/data/receipts/",
) -> uuid.UUID:
    raw = {
        "contributor_name": contributor,
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "fec_cycle": 2024,
    }
    ent = EvidenceEntry(
        case_file_id=case_id,
        entry_type="financial_connection",
        title="FEC",
        body="test",
        source_url=url,
        source_name="FEC",
        date_of_event=date.fromisoformat(receipt_date[:10]),
        entered_by="gap_tester",
        confidence="confirmed",
        amount=amount,
        matched_name=contributor,
        raw_data_json=json.dumps(raw, separators=(",", ":")),
    )
    db.add(ent)
    db.flush()
    return ent.id


def _vote_row(
    db,
    case_id: uuid.UUID,
    *,
    vote_day: str,
    question: str = "Test Measure",
    position: str = "YEA",
) -> uuid.UUID:
    raw = {
        "question": question,
        "member_vote": position,
        "result": "PASSED",
    }
    ent = EvidenceEntry(
        case_file_id=case_id,
        entry_type="vote_record",
        title="Roll call",
        body="test",
        source_url="https://www.senate.gov/",
        entered_by="gap_tester",
        confidence="confirmed",
        date_of_event=date.fromisoformat(vote_day[:10]),
        raw_data_json=json.dumps(raw, separators=(",", ":")),
    )
    db.add(ent)
    db.flush()
    return ent.id


def _add_signal(
    db,
    case_id: uuid.UUID,
    *,
    days_between: int,
    amount: float,
    fec_id: uuid.UUID,
    vote_id: uuid.UUID,
    donor: str = "Example Donor LLC",
) -> None:
    bd = {
        "kind": "donor_cluster",
        "donor": donor,
        "official": "Senator Example",
        "receipt_date": "2024-04-01",
        "total_amount": amount,
        "donation_count": 1,
        "vote_count": 1,
        "pair_count": 1,
        "min_gap_days": abs(days_between),
        "median_gap_days": float(abs(days_between)),
        "exemplar_vote": "Test vote",
        "exemplar_gap": days_between,
        "exemplar_direction": "before",
        "exemplar_position": "YEA",
        "exemplar_financial_date": "2024-04-01",
    }
    ident = hashlib.sha256(f"{case_id}-{fec_id}-{vote_id}".encode()).hexdigest()
    sig = Signal(
        case_file_id=case_id,
        signal_identity_hash=ident,
        signal_type="temporal_proximity",
        weight=0.75,
        description="gap test",
        evidence_ids=json.dumps([str(fec_id), str(vote_id)], separators=(",", ":")),
        actor_a=donor,
        actor_b="Senator Example",
        event_date_a="2024-04-01",
        event_date_b="2024-04-15",
        days_between=days_between,
        amount=amount,
        exposure_state="internal",
        weight_breakdown=json.dumps(bd, separators=(",", ":")),
    )
    db.add(sig)


def test_proximity_sentence_within_30_days(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    c = _case_and_inv(db, f"gap-a-{uuid.uuid4().hex[:8]}", "Senator Example")
    db.flush()
    fid = _fec_row(db, c.id, amount=10_000.0, receipt_date="2024-04-01", contributor="Example Donor LLC")
    vid = _vote_row(db, c.id, vote_day="2024-04-10")
    _add_signal(db, c.id, days_between=9, amount=10_000.0, fec_id=fid, vote_id=vid)
    db.commit()
    gaps = generate_gap_sentences(str(c.id), db)
    db.close()
    prox = [g for g in gaps if g["type"] == "donation_vote_proximity"]
    assert prox
    assert "Public records show" in prox[0]["sentence"]
    assert "Example Donor LLC" in prox[0]["sentence"]
    assert "Senator Example" in prox[0]["sentence"]


def test_no_proximity_when_days_exceed_180(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    c = _case_and_inv(db, f"gap-b-{uuid.uuid4().hex[:8]}", "Senator Far")
    db.flush()
    fid = _fec_row(db, c.id, amount=500.0, receipt_date="2024-04-01", contributor="Donor X")
    vid = _vote_row(db, c.id, vote_day="2024-12-15")
    _add_signal(db, c.id, days_between=258, amount=500.0, fec_id=fid, vote_id=vid, donor="Donor X")
    db.commit()
    gaps = generate_gap_sentences(str(c.id), db)
    db.close()
    assert not any(g["type"] == "donation_vote_proximity" for g in gaps)


def test_high_confidence_when_within_30_days(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    c = _case_and_inv(db, f"gap-c-{uuid.uuid4().hex[:8]}", "Senator Near")
    db.flush()
    fid = _fec_row(db, c.id, amount=1000.0, receipt_date="2024-05-01", contributor="Near Donor")
    vid = _vote_row(db, c.id, vote_day="2024-05-05")
    _add_signal(db, c.id, days_between=4, amount=1000.0, fec_id=fid, vote_id=vid, donor="Near Donor")
    db.commit()
    gaps = generate_gap_sentences(str(c.id), db)
    db.close()
    prox = next(g for g in gaps if g["type"] == "donation_vote_proximity")
    assert prox["confidence"] == "high"


def test_needs_human_review_when_amount_over_50k(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    c = _case_and_inv(db, f"gap-d-{uuid.uuid4().hex[:8]}", "Senator Big")
    db.flush()
    fid = _fec_row(db, c.id, amount=75_000.0, receipt_date="2024-06-01", contributor="Heavy Donor")
    vid = _vote_row(db, c.id, vote_day="2024-06-10")
    _add_signal(db, c.id, days_between=9, amount=75_000.0, fec_id=fid, vote_id=vid, donor="Heavy Donor")
    db.commit()
    gaps = generate_gap_sentences(str(c.id), db)
    db.close()
    prox = next(g for g in gaps if g["type"] == "donation_vote_proximity")
    assert prox["needs_human_review"] is True


def test_sources_list_not_empty(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    c = _case_and_inv(db, f"gap-e-{uuid.uuid4().hex[:8]}", "Senator Src")
    db.flush()
    fid = _fec_row(
        db,
        c.id,
        amount=2000.0,
        receipt_date="2024-07-01",
        contributor="Source Donor",
        url="https://www.fec.gov/data/receipts/abc/",
    )
    vid = _vote_row(db, c.id, vote_day="2024-07-08")
    _add_signal(db, c.id, days_between=7, amount=2000.0, fec_id=fid, vote_id=vid, donor="Source Donor")
    db.commit()
    gaps = generate_gap_sentences(str(c.id), db)
    db.close()
    prox = next(g for g in gaps if g["type"] == "donation_vote_proximity")
    assert prox["sources"]
    assert any("fec.gov" in u.lower() for u in prox["sources"])

