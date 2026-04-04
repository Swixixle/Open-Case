"""Tests for pattern engine v2 rules (JFC, baseline, alignment, amendment, hearing)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.pattern_engine import (
    PATTERN_ENGINE_VERSION,
    RULE_ALIGNMENT_ANOMALY,
    RULE_AMENDMENT_TELL,
    RULE_BASELINE_ANOMALY,
    RULE_HEARING_TESTIMONY,
    RULE_JOINT_FUNDRAISING,
    _median_seven_day_intake_for_bioguide,
    pattern_alert_to_payload,
    run_pattern_engine,
)
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
    EvidenceEntry,
    Investigator,
    Signal,
    SubjectProfile,
)


def _seed_investigator(db) -> None:
    raw_key = generate_raw_key()
    db.add(
        Investigator(
            handle="v2_tester",
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
        created_by="v2_tester",
        summary="",
    )
    db.add(c)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=c.id,
            investigator_handle="v2_tester",
            role="field",
        )
    )
    return c


def _subject(db, case_id: uuid.UUID, bioguide: str, name: str) -> None:
    db.add(
        SubjectProfile(
            case_file_id=case_id,
            subject_name=name,
            subject_type="public_official",
            bioguide_id=bioguide,
        )
    )


def _fec_with_committee(
    db,
    case_id: uuid.UUID,
    *,
    committee_id: str,
    amount: float,
    receipt_date: str,
    contributor_name: str = "Donor",
) -> None:
    raw = {
        "contributor_name": contributor_name,
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "committee": {"committee_id": committee_id},
        "entity_type": "IND",
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="financial_connection",
            title="FEC",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=date.fromisoformat(receipt_date[:10]),
            entered_by="v2_tester",
            confidence="confirmed",
            amount=amount,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _fec_historical(
    db, case_id: uuid.UUID, *, amount: float, receipt_date: str
) -> None:
    raw = {
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "fec_cycle": 2024,
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="fec_historical",
            title="Historical FEC",
            body="test",
            source_url="https://www.fec.gov/",
            entered_by="v2_tester",
            confidence="confirmed",
            amount=amount,
            date_of_event=date.fromisoformat(receipt_date[:10]),
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _jfc_donor(db, case_id: uuid.UUID, jfc_id: str, name: str, amt: float) -> None:
    raw = {
        "jfc_committee_id": jfc_id,
        "contributor_name": name,
        "contribution_receipt_amount": amt,
        "contribution_receipt_date": "2024-06-01",
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="fec_jfc_donor",
            title="JFC",
            body="test",
            source_url="https://www.fec.gov/",
            entered_by="v2_tester",
            confidence="confirmed",
            amount=amt,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _disbursement_to_principal(
    db,
    case_id: uuid.UUID,
    *,
    recipient_id: str,
    spender_id: str,
    amount: float,
    disbursement_date: str,
) -> None:
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="fec_disbursement",
            title="Sched B",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=date.fromisoformat(disbursement_date[:10]),
            entered_by="v2_tester",
            confidence="confirmed",
            amount=amount,
            raw_data_json=json.dumps(
                {
                    "disbursement_amount": amount,
                    "disbursement_date": disbursement_date,
                    "recipient_committee_id": recipient_id,
                    "committee_id": spender_id,
                },
                separators=(",", ":"),
            ),
        )
    )


def _lobbying(db, case_id: uuid.UUID, filing_year: int = 2026) -> None:
    raw = {"issue_codes": ["HCR"], "filing_year": filing_year}
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="lobbying_filing",
            title="LDA",
            body="test",
            source_url="https://lda.senate.gov/",
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _vote_pharma_day(db, case_id: uuid.UUID, day: date, pos: str) -> None:
    raw = {
        "member_vote": pos,
        "question": "Motion on pharmaceutical pricing reform bill",
        "congress": 119,
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="vote_record",
            title="Vote",
            body="test",
            source_url="https://www.senate.gov/",
            source_name="congress_votes",
            date_of_event=day,
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _breakdown_json(donor: str, official: str, **extra) -> str:
    base = {
        "kind": "donor_cluster",
        "donor": donor,
        "official": official,
        "total_amount": 5000.0,
        "donation_count": 1,
        "vote_count": 1,
        "pair_count": 1,
        "min_gap_days": -5,
        "median_gap_days": -5.0,
        "exemplar_vote": "S.1",
        "exemplar_gap": -5,
        "exemplar_direction": "after",
        "exemplar_position": "Yea",
        "proximity_score": 0.5,
        "amount_multiplier": 1.0,
        "committee_label": "Test PAC",
        "has_collision": False,
        "has_jurisdictional_match": False,
        "has_lda_filing": False,
        "relevance_score": 0.5,
    }
    base.update(extra)
    return json.dumps(base, separators=(",", ":"))


def _signal_amendment(
    db,
    case_id: uuid.UUID,
    donor: str,
    official: str,
    fin_date: str,
) -> Signal:
    ident = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    s = Signal(
        case_file_id=case_id,
        signal_identity_hash=ident,
        signal_type="temporal_proximity",
        weight=0.5,
        description="test",
        evidence_ids="[]",
        exposure_state="internal",
        actor_a=donor,
        actor_b=official,
        event_date_a=fin_date,
        event_date_b="2026-06-01",
        days_between=-5,
        relevance_score=0.5,
        weight_breakdown=_breakdown_json(donor, official, total_amount=8000.0, receipt_date=fin_date),
    )
    db.add(s)
    db.flush()
    return s


def test_joint_fundraising_fires_with_transfer_and_jfc_donors(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"jfc-{uuid.uuid4().hex[:8]}", "Senator JFC")
    db.flush()
    principal = "C000PRIN"
    jfc = "C000JFC00"
    _subject(db, c.id, "S77777777", "Senator JFC")
    _fec_with_committee(db, c.id, committee_id=principal, amount=100.0, receipt_date="2024-01-05")
    _jfc_donor(db, c.id, jfc, "Upstream One", 5000.0)
    _disbursement_to_principal(
        db,
        c.id,
        recipient_id=principal,
        spender_id=jfc,
        amount=8000.0,
        disbursement_date="2024-06-15",
    )
    db.commit()
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_JOINT_FUNDRAISING]
    db.close()
    assert any(str(c.id) in a.matched_case_ids for a in hits)
    j = next(a for a in hits if str(c.id) in a.matched_case_ids)
    pl = pattern_alert_to_payload(j)
    assert pl["pattern_version"] == PATTERN_ENGINE_VERSION
    assert pl.get("payload_extra", {}).get("jfc_committee_id") == jfc


def test_median_seven_day_intake_uses_fec_not_soft_bundle_only(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"med-{uuid.uuid4().hex[:8]}", "Senator Median")
    db.flush()
    _subject(db, c.id, "S88888888", "Senator Median")
    for i in range(25):
        _fec_historical(
            db,
            c.id,
            amount=200.0,
            receipt_date=f"2024-01-{i + 1:02d}",
        )
    db.commit()
    med = _median_seven_day_intake_for_bioguide(db, "S88888888")
    db.close()
    assert med is not None
    assert 1000.0 <= med <= 2000.0


def test_baseline_anomaly_fires_on_spike(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"bl-{uuid.uuid4().hex[:8]}", "Senator Baseline")
    db.flush()
    _subject(db, c.id, "S99999999", "Senator Baseline")
    for i in range(25):
        _fec_historical(
            db,
            c.id,
            amount=200.0,
            receipt_date=f"2024-03-{i + 1:02d}",
        )
    for j in range(5):
        _fec_historical(
            db,
            c.id,
            amount=5000.0,
            receipt_date=f"2024-04-{j + 1:02d}",
        )
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="vote_record",
            title="Roll",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="v2_tester",
            confidence="confirmed",
            date_of_event=date(2024, 4, 10),
            raw_data_json=json.dumps({"congress": 119}, separators=(",", ":")),
        )
    )
    db.commit()
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert any(str(c.id) in a.matched_case_ids for a in hits)
    b = next(a for a in hits if str(c.id) in a.matched_case_ids)
    assert b.payload_extra and b.payload_extra.get("baseline_multiplier", 0) >= 6.0


def test_alignment_anomaly_high_pharma_yea_rate(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    patterns_yeas = [
        ("SAAA00001", "Sen A1", ["NAY"] * 5),
        ("SAAA00002", "Sen A2", ["NAY"] * 5),
        ("SAAA00003", "Sen A3", ["NAY"] * 5),
    ]
    cases = []
    for bg, name, positions in patterns_yeas:
        c = _case(db, f"al-{bg}-{uuid.uuid4().hex[:6]}", name)
        db.flush()
        _subject(db, c.id, bg, name)
        _lobbying(db, c.id, 2026)
        for i, pos in enumerate(positions):
            _vote_pharma_day(db, c.id, date(2026, 2, 1 + i), pos)
        cases.append(c)
    ct = _case(db, f"al-t-{uuid.uuid4().hex[:8]}", "Senator Target")
    db.flush()
    _subject(db, ct.id, "STARGET01", "Senator Target")
    _lobbying(db, ct.id, 2026)
    for i in range(5):
        _vote_pharma_day(db, ct.id, date(2026, 2, 10 + i), "YEA")
    db.commit()
    target_uuid = str(ct.id)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_ALIGNMENT_ANOMALY]
    db.close()
    target_hits = [a for a in hits if target_uuid in a.matched_case_ids]
    assert target_hits
    assert target_hits[0].payload_extra and "z_score" in target_hits[0].payload_extra


def test_amendment_tell_weakening_yea_vs_final_passage_nay(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"amd-{uuid.uuid4().hex[:8]}", "Senator Amend")
    db.flush()
    _lobbying(db, c.id, 2026)
    av_raw = {
        "amendment_description": "Amendment to delay FDA enforcement deadlines",
        "vote_position": "YEA",
        "bill_number": "S. 100",
    }
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="amendment_vote",
            title="Amendment",
            body="test",
            source_url="https://www.senate.gov/",
            date_of_event=date(2026, 5, 15),
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(av_raw, separators=(",", ":")),
        )
    )
    fp_raw = {
        "bill_number": "S. 100",
        "question": "On Passage of the Bill",
        "member_vote": "NAY",
        "congress": 119,
    }
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="vote_record",
            title="Passage",
            body="test",
            source_url="https://www.senate.gov/",
            date_of_event=date(2026, 5, 20),
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(fp_raw, separators=(",", ":")),
        )
    )
    s = _signal_amendment(db, c.id, "Pharma Donor LLC", "Senator Amend", "2026-05-14")
    db.add(
        DonorFingerprint(
            normalized_donor_key="pharmad",
            case_file_id=c.id,
            signal_id=s.id,
            weight=0.5,
            official_name="Senator Amend",
            bioguide_id="SAMEND001",
        )
    )
    db.commit()
    case_uuid = str(c.id)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_AMENDMENT_TELL]
    db.close()
    assert any(case_uuid in a.matched_case_ids for a in hits)
    am = next(a for a in hits if case_uuid in a.matched_case_ids)
    assert am.payload_extra and am.payload_extra.get("inconsistent_record") is True
    pl = pattern_alert_to_payload(am)
    assert "payload_extra" in pl
    assert pl["payload_extra"].get("amendment_number") == ""


def test_hearing_testimony_fires_when_govinfo_configured(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"ht-{uuid.uuid4().hex[:8]}", "Senator Hear")
    db.flush()
    witness_raw = {
        "matched_name": "PersonName",
        "hearing_title": "Oversight hearing",
        "package_id": "CHRG-118",
        "date_issued": "2026-03-01",
    }
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="hearing_witness",
            title="Witness",
            body="test",
            source_url="https://www.govinfo.gov/",
            date_of_event=date(2026, 3, 1),
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(witness_raw, separators=(",", ":")),
        )
    )
    fe = EvidenceEntry(
        case_file_id=c.id,
        entry_type="financial_connection",
        title="FEC",
        body="test",
        source_url="https://www.fec.gov/",
        source_name="FEC",
        adapter_name="FEC",
        date_of_event=date(2026, 3, 5),
        entered_by="v2_tester",
        confidence="confirmed",
        amount=2000.0,
        raw_data_json=json.dumps(
            {
                "contributor_name": "PersonName",
                "contribution_receipt_amount": 2000,
                "contribution_receipt_date": "2026-03-05",
                "contributor_employer": "AcmeCorp Industries",
                "entity_type": "IND",
            },
            separators=(",", ":"),
        ),
    )
    db.add(fe)
    db.flush()
    s = Signal(
        case_file_id=c.id,
        signal_identity_hash=(uuid.uuid4().hex + uuid.uuid4().hex)[:64],
        signal_type="temporal_proximity",
        weight=0.5,
        description="test",
        evidence_ids=json.dumps([str(fe.id)], separators=(",", ":")),
        exposure_state="internal",
        actor_a="PersonName",
        actor_b="Senator Hear",
        event_date_a="2026-03-05",
        event_date_b="2026-01-01",
        days_between=-5,
        relevance_score=0.5,
        weight_breakdown=_breakdown_json(
            "PersonName", "Senator Hear", total_amount=2000.0, receipt_date="2026-03-05"
        ),
    )
    db.add(s)
    db.flush()
    db.add(
        DonorFingerprint(
            normalized_donor_key="pn1",
            case_file_id=c.id,
            signal_id=s.id,
            weight=0.5,
            official_name="Senator Hear",
            bioguide_id="SHEAR0001",
        )
    )
    db.commit()
    case_uuid = str(c.id)
    with patch(
        "core.credentials.CredentialRegistry.get_credential",
        return_value="test-key",
    ):
        hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_HEARING_TESTIMONY]
    db.close()
    assert any(case_uuid in a.matched_case_ids for a in hits)


def test_hearing_testimony_skipped_without_govinfo(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"htn-{uuid.uuid4().hex[:8]}", "Senator NoGov")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="hearing_witness",
            title="Witness",
            body="test",
            source_url="https://www.govinfo.gov/",
            date_of_event=date(2026, 4, 1),
            entered_by="v2_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {"matched_name": "Someone", "date_issued": "2026-04-01"},
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    with patch("core.credentials.CredentialRegistry.get_credential", return_value=None):
        hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_HEARING_TESTIMONY]
    db.close()
    assert hits == []


def test_pattern_engine_version_is_2_2() -> None:
    assert PATTERN_ENGINE_VERSION == "2.2"


def test_joint_fundraising_not_fired_without_principal_committee(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"jfc0-{uuid.uuid4().hex[:8]}", "Senator NoPrin")
    db.flush()
    _subject(db, c.id, "S66666666", "Senator NoPrin")
    _fec_receipt_no_committee(db, c.id, 100.0, "2024-01-05")
    _jfc_donor(db, c.id, "C_JFC_ABC", "Donor", 100.0)
    _disbursement_to_principal(
        db,
        c.id,
        recipient_id="C_RECIPIENT",
        spender_id="C_JFC_ABC",
        amount=9000.0,
        disbursement_date="2024-06-10",
    )
    db.commit()
    case_uuid = str(c.id)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_JOINT_FUNDRAISING]
    db.close()
    assert not any(case_uuid in a.matched_case_ids for a in hits)


def _fec_receipt_no_committee(
    db, case_id: uuid.UUID, amount: float, receipt_date: str
) -> None:
    raw = {
        "contributor_name": "X",
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "entity_type": "IND",
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="financial_connection",
            title="FEC",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=date.fromisoformat(receipt_date[:10]),
            entered_by="v2_tester",
            confidence="confirmed",
            amount=amount,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def test_baseline_anomaly_not_fired_when_spike_below_multiplier(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"bllo-{uuid.uuid4().hex[:8]}", "Senator LowSpike")
    db.flush()
    _subject(db, c.id, "S55555555", "Senator LowSpike")
    for i in range(25):
        _fec_historical(db, c.id, amount=250.0, receipt_date=f"2024-05-{i + 1:02d}")
    _fec_historical(db, c.id, amount=2000.0, receipt_date="2024-06-01")
    _fec_historical(db, c.id, amount=2000.0, receipt_date="2024-06-02")
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="vote_record",
            title="Roll",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="v2_tester",
            confidence="confirmed",
            date_of_event=date(2024, 6, 3),
            raw_data_json=json.dumps({"congress": 119}, separators=(",", ":")),
        )
    )
    db.commit()
    case_uuid = str(c.id)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_BASELINE_ANOMALY]
    db.close()
    assert not any(case_uuid in a.matched_case_ids for a in hits)


def test_alignment_not_fired_with_only_two_baseline_senators(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    for idx in range(2):
        c = _case(db, f"al2-{idx}-{uuid.uuid4().hex[:6]}", f"Sen B{idx}")
        db.flush()
        _subject(db, c.id, f"SBL2{idx:03d}", f"Sen B{idx}")
        _lobbying(db, c.id, 2026)
        for i in range(5):
            _vote_pharma_day(db, c.id, date(2026, 3, 1 + i), "NAY")
    ct = _case(db, f"al2t-{uuid.uuid4().hex[:8]}", "Sen Target2")
    db.flush()
    _subject(db, ct.id, "STARG2", "Sen Target2")
    _lobbying(db, ct.id, 2026)
    for i in range(5):
        _vote_pharma_day(db, ct.id, date(2026, 4, 1 + i), "YEA")
    db.commit()
    target_uuid = str(ct.id)
    hits = [a for a in run_pattern_engine(db) if a.rule_id == RULE_ALIGNMENT_ANOMALY]
    db.close()
    assert not any(target_uuid in a.matched_case_ids for a in hits)


def test_median_seven_day_returns_none_without_enough_fec_points(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"med0-{uuid.uuid4().hex[:8]}", "Sen FewPts")
    db.flush()
    _subject(db, c.id, "S44444444", "Sen FewPts")
    for i in range(10):
        _fec_historical(db, c.id, amount=50.0, receipt_date=f"2024-07-{i + 1:02d}")
    db.commit()
    med = _median_seven_day_intake_for_bioguide(db, "S44444444")
    db.close()
    assert med is None
