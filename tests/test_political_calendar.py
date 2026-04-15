"""Political calendar lookup, ghost vote-context gate, and baseline split thresholds."""

from __future__ import annotations

import json
import uuid
from datetime import date

from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.pattern_engine import (
    BASELINE_ANOMALY_CALENDAR_ADJACENT_MIN_MULTIPLIER,
    BASELINE_ANOMALY_MIN_MULTIPLIER,
    RULE_BASELINE_ANOMALY,
    _case_ids_with_current_congress_votes,
    run_pattern_engine,
)
from engines.political_calendar import get_calendar_discount
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, SubjectProfile


def _seed_investigator(db) -> None:
    raw_key = generate_raw_key()
    db.add(
        Investigator(
            handle="cal_tester",
            hashed_api_key=hash_key(raw_key),
            public_key="",
        )
    )
    db.commit()


def _case(db, slug: str, subject: str) -> CaseFile:
    c = CaseFile(
        slug=slug,
        title=f"Case {subject}",
        subject_name=subject,
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="cal_tester",
        summary="",
    )
    db.add(c)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=c.id,
            investigator_handle="cal_tester",
            role="field",
        )
    )
    return c


def _vote119(db, case_id: uuid.UUID, day: date, *, bioguide_id: str | None = None) -> None:
    raw: dict = {"congress": 119, "question": "On Passage"}
    if bioguide_id:
        raw["bioguide_id"] = bioguide_id
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="vote_record",
            title="Vote",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="cal_tester",
            confidence="confirmed",
            date_of_event=day,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _fec_hist(db, case_id: uuid.UUID, amount: float, receipt_date: str) -> None:
    raw = {
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "fec_cycle": 2024,
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="fec_historical",
            title="FEC",
            body="test",
            source_url="https://www.fec.gov/",
            entered_by="cal_tester",
            confidence="confirmed",
            amount=amount,
            date_of_event=date.fromisoformat(receipt_date[:10]),
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def test_get_calendar_discount_fec_deadline(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2026, 12, 25), date(2026, 12, 31)
    disc, et, en = get_calendar_discount(db, d0, d1, None)
    db.close()
    assert disc == 0.3
    assert et == "FEC_DEADLINE"
    assert en and "2026" in en and "FEC" in en


def test_get_calendar_discount_election_day(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2022, 11, 5), date(2022, 11, 10)
    disc, et, en = get_calendar_discount(db, d0, d1, None)
    db.close()
    assert disc == 0.2
    assert et == "GENERAL_ELECTION"


def test_get_calendar_discount_state_primary(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2022, 6, 1), date(2022, 6, 10)
    disc, et, en = get_calendar_discount(db, d0, d1, "IA")
    db.close()
    assert disc == 0.4
    assert et == "PRIMARY"


def test_get_calendar_discount_no_overlap(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2026, 2, 8), date(2026, 2, 11)
    disc, et, en = get_calendar_discount(db, d0, d1, "AR")
    db.close()
    assert disc == 1.0 and et is None and en is None


def test_get_calendar_discount_national_only_ignores_other_state_primary(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2022, 6, 1), date(2022, 6, 10)
    disc_ia, _, _ = get_calendar_discount(db, d0, d1, "IA")
    disc_ar, _, _ = get_calendar_discount(db, d0, d1, "AR")
    db.close()
    assert disc_ia == 0.4
    assert disc_ar == 1.0


def test_get_calendar_discount_legislative_session_with_committees(test_engine) -> None:
    """Approximate Senate session spans apply only when committee assignments are known."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2023, 4, 10), date(2023, 4, 10)
    disc_none, _, _ = get_calendar_discount(db, d0, d1, None)
    disc_ctx, et, en = get_calendar_discount(
        db, d0, d1, None, committee_codes=["SSFI"]
    )
    db.close()
    assert disc_none == 1.0
    assert disc_ctx == 0.9
    assert et == "LEGISLATIVE_SESSION"
    assert en and "2023" in en


def test_get_calendar_discount_chair_session_stronger_discount(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2023, 4, 10), date(2023, 4, 10)
    disc, et, _ = get_calendar_discount(
        db,
        d0,
        d1,
        None,
        committee_codes=["SSFI"],
        chair_committee_codes=["SSFI"],
    )
    db.close()
    assert disc == 0.78
    assert et == "COMMITTEE_CHAIR_SESSION"


def test_get_calendar_discount_legislative_spans_skipped_when_env_set(
    test_engine, monkeypatch
) -> None:
    """OPEN_CASE_DISABLE_LEGISLATIVE_CALENDAR_SPANS drops chair/member session spans only."""
    monkeypatch.setenv("OPEN_CASE_DISABLE_LEGISLATIVE_CALENDAR_SPANS", "1")
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    d0, d1 = date(2023, 4, 10), date(2023, 4, 10)
    disc_ctx, et, _ = get_calendar_discount(
        db, d0, d1, None, committee_codes=["SSFI"]
    )
    db.close()
    assert disc_ctx == 1.0
    assert et is None


def test_case_has_current_congress_votes_true(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"cv-t-{uuid.uuid4().hex[:8]}", "Sen Votes")
    db.flush()
    _vote119(db, c.id, date(2026, 3, 1))
    db.commit()
    case_id = c.id
    have = _case_ids_with_current_congress_votes(db)
    db.close()
    assert case_id in have


def test_case_has_current_congress_votes_false_no_votes(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"cv-f-{uuid.uuid4().hex[:8]}", "Sen Ghost")
    db.flush()
    db.commit()
    case_id = c.id
    have = _case_ids_with_current_congress_votes(db)
    db.close()
    assert case_id not in have


def test_case_current_congress_votes_rejects_mismatched_bioguide(test_engine) -> None:
    """Roll calls for a different member must not open the vote-context gate (Shaheen-class)."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"cv-mis-{uuid.uuid4().hex[:8]}", "Sen Jeanne")
    db.flush()
    db.add(
        SubjectProfile(
            case_file_id=c.id,
            subject_name="Sen Jeanne",
            subject_type="public_official",
            bioguide_id="S001181",
        )
    )
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="vote_record",
            title="Vote",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="cal_tester",
            confidence="confirmed",
            date_of_event=date(2026, 3, 1),
            raw_data_json=json.dumps(
                {"congress": 119, "bioguide_id": "S000033", "question": "On Passage"},
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    case_id = c.id
    have = _case_ids_with_current_congress_votes(db)
    db.close()
    assert case_id not in have


def test_case_has_current_congress_votes_false_old_congress_only(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"cv-o-{uuid.uuid4().hex[:8]}", "Sen Old")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="vote_record",
            title="Vote",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="cal_tester",
            confidence="confirmed",
            date_of_event=date(2020, 1, 1),
            raw_data_json=json.dumps({"congress": 116}, separators=(",", ":")),
        )
    )
    db.commit()
    case_id = c.id
    have = _case_ids_with_current_congress_votes(db)
    db.close()
    assert case_id not in have


def test_baseline_anomaly_skipped_shaheen_class_vote_missing_member_bioguide(test_engine) -> None:
    """Vote rows without member bioguide must not open vote-context for profiled senators."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sha-{uuid.uuid4().hex[:8]}", "Sen Jeanne")
    db.flush()
    db.add(
        SubjectProfile(
            case_file_id=c.id,
            subject_name="Sen Jeanne",
            subject_type="public_official",
            bioguide_id="S001181",
        )
    )
    # Current congress vote but no bioguide_id / memberVote in raw — must not qualify.
    _vote119(db, c.id, date(2026, 3, 1))
    for i in range(25):
        _fec_hist(db, c.id, 200.0, f"2024-03-{i + 1:02d}")
    for j in range(5):
        _fec_hist(db, c.id, 8000.0, f"2024-04-{j + 1:02d}")
    db.commit()
    assert c.id not in _case_ids_with_current_congress_votes(db)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert not any(str(c.id) in a.matched_case_ids for a in hits)


def test_baseline_anomaly_skipped_for_ghost_case(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"gh-{uuid.uuid4().hex[:8]}", "Sen GhostBase")
    db.flush()
    db.add(
        SubjectProfile(
            case_file_id=c.id,
            subject_name="Sen GhostBase",
            subject_type="public_official",
            bioguide_id="SGHOST01",
        )
    )
    for i in range(25):
        _fec_hist(db, c.id, 200.0, f"2024-03-{i + 1:02d}")
    for j in range(5):
        _fec_hist(db, c.id, 8000.0, f"2024-04-{j + 1:02d}")
    db.commit()
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert not any(str(c.id) in a.matched_case_ids for a in hits)


def test_baseline_anomaly_calendar_threshold_higher(test_engine) -> None:
    """8× spike on a year-end calendar window must not fire (needs 10×)."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"bcal-{uuid.uuid4().hex[:8]}", "Sen CalBase")
    db.flush()
    db.add(
        SubjectProfile(
            case_file_id=c.id,
            subject_name="Sen CalBase",
            subject_type="public_official",
            bioguide_id="SCALBASE1",
            state="DC",
        )
    )
    _vote119(db, c.id, date(2026, 6, 1), bioguide_id="SCALBASE1")
    for i in range(25):
        _fec_hist(db, c.id, 200.0, f"2024-03-{i + 1:02d}")
    for j in range(5):
        _fec_hist(db, c.id, 2240.0, f"2026-12-{27 + j:02d}")
    db.commit()
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert BASELINE_ANOMALY_CALENDAR_ADJACENT_MIN_MULTIPLIER == 10.0
    assert not any(str(c.id) in a.matched_case_ids for a in hits)


def test_baseline_anomaly_non_calendar_threshold(test_engine) -> None:
    """~6.86× spike mid-February (no calendar overlap) fires above 6× floor."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"bncc-{uuid.uuid4().hex[:8]}", "Sen FebBase")
    db.flush()
    db.add(
        SubjectProfile(
            case_file_id=c.id,
            subject_name="Sen FebBase",
            subject_type="public_official",
            bioguide_id="SFEBBASE1",
            state="AR",
        )
    )
    _vote119(db, c.id, date(2026, 2, 1), bioguide_id="SFEBBASE1")
    for i in range(25):
        _fec_hist(db, c.id, 200.0, f"2024-03-{i + 1:02d}")
    for j in range(5):
        _fec_hist(db, c.id, 1920.0, f"2026-02-{8 + j:02d}")
    db.commit()
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert BASELINE_ANOMALY_MIN_MULTIPLIER == 6.0
    assert any(str(c.id) in a.matched_case_ids for a in hits)
