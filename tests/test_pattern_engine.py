"""Phase 9B — pattern engine and patterns API (cross-official detection)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from adapters.fec import fec_schedule_a_row_exclusion_reason
from engines.pattern_engine import (
    COMMITTEE_SWEEP_MAX_WINDOW_DAYS,
    FINGERPRINT_BLOOM_MIN_RELEVANCE,
    RULE_COMMITTEE_SWEEP,
    RULE_FINGERPRINT_BLOOM,
    RULE_SOFT_BUNDLE,
    SOFT_BUNDLE_MAX_SPAN_DAYS,
    is_deadline_adjacent,
    pattern_alert_to_payload,
    proximity_to_vote_score_from_days,
    run_pattern_engine,
)
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
    EvidenceEntry,
    Investigator,
    SenatorCommittee,
    Signal,
)
from payloads import apply_case_file_signature, full_case_signing_payload


def _breakdown_json(donor: str, official: str, **extra: Any) -> str:
    base = {
        "kind": "donor_cluster",
        "donor": donor,
        "official": official,
        "total_amount": 1000.0,
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
        "committee_label": "Friends of X",
        "has_collision": False,
        "has_jurisdictional_match": False,
        "has_lda_filing": False,
        "relevance_score": extra.get("relevance_score", 0.5),
    }
    base.update(extra)
    return json.dumps(base, separators=(",", ":"))


def _seed_investigator(db) -> None:
    raw_key = generate_raw_key()
    handle = "pat_eng_tester"
    db.add(
        Investigator(
            handle=handle,
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
        created_by="pat_eng_tester",
        summary="",
    )
    db.add(c)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=c.id,
            investigator_handle="pat_eng_tester",
            role="field",
        )
    )
    return c


def _signal(
    db,
    case_id: uuid.UUID,
    donor: str,
    official: str,
    fin_date: str,
    relevance: float,
    *,
    total_amount: float | None = None,
    committee_label: str | None = None,
    store_date_as_receipt_only: bool = False,
) -> Signal:
    ident = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    bd_extra: dict[str, Any] = {"relevance_score": relevance}
    if total_amount is not None:
        bd_extra["total_amount"] = float(total_amount)
    if committee_label is not None:
        bd_extra["committee_label"] = committee_label
    if store_date_as_receipt_only:
        bd_extra["receipt_date"] = fin_date.strip()[:10]
        ev_a: str | None = None
    else:
        ev_a = fin_date
    s = Signal(
        case_file_id=case_id,
        signal_identity_hash=ident,
        signal_type="temporal_proximity",
        weight=0.6,
        description="test",
        evidence_ids="[]",
        exposure_state="internal",
        actor_a=donor,
        actor_b=official,
        event_date_a=ev_a,
        event_date_b="2025-06-01",
        days_between=-5,
        relevance_score=relevance,
        weight_breakdown=_breakdown_json(donor, official, **bd_extra),
    )
    db.add(s)
    db.flush()
    return s


def _vote_record(
    db,
    case_id: uuid.UUID,
    vote_day: date,
    *,
    raw_data: dict[str, Any] | None = None,
) -> EvidenceEntry:
    rd = json.dumps(raw_data, sort_keys=True, default=str) if raw_data is not None else ""
    ev = EvidenceEntry(
        case_file_id=case_id,
        entry_type="vote_record",
        title="Vote: Yea on S. 1 (119th Congress)",
        body="Test roll call",
        source_url="https://www.senate.gov/legislative/LIS/roll_call_votes/test.xml",
        source_name="congress_votes",
        date_of_event=vote_day,
        entered_by="pat_eng_tester",
        confidence="confirmed",
        raw_data_json=rd,
    )
    db.add(ev)
    return ev


def _fingerprint(
    db,
    donor_key: str,
    case_id: uuid.UUID,
    signal_id: uuid.UUID,
    official: str,
    bioguide: str,
) -> None:
    db.add(
        DonorFingerprint(
            normalized_donor_key=donor_key,
            case_file_id=case_id,
            signal_id=signal_id,
            weight=0.6,
            official_name=official,
            bioguide_id=bioguide,
        )
    )


def _seed_finance_committee(db, *bioguides: str) -> None:
    for bg in bioguides:
        db.add(
            SenatorCommittee(
                bioguide_id=bg,
                committee_name="Senate Finance",
                committee_code=bg,
            )
        )


def test_committee_sweep_fires_at_threshold(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    c3 = _case(db, f"c3-{uuid.uuid4().hex[:8]}", "Senator Three")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222", "S33333333")
    donor_key = "megacorp pac"
    s1 = _signal(db, c1.id, "MEGACORP PAC", "Senator One", "2025-03-01", 0.5)
    s2 = _signal(db, c2.id, "MEGACORP PAC", "Senator Two", "2025-03-05", 0.5)
    s3 = _signal(db, c3.id, "MEGACORP PAC", "Senator Three", "2025-03-08", 0.5)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    _fingerprint(db, donor_key, c3.id, s3.id, "Senator Three", "S33333333")
    db.commit()

    alerts = run_pattern_engine(db)
    db.close()
    sweep = [a for a in alerts if a.rule_id == RULE_COMMITTEE_SWEEP]
    assert len(sweep) == 1
    assert sweep[0].committee == "Senate Finance"
    assert sweep[0].window_days <= COMMITTEE_SWEEP_MAX_WINDOW_DAYS
    assert len(sweep[0].matched_officials) == 3


def test_committee_sweep_does_not_fire_below_threshold(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222")
    donor_key = "smallcorp"
    s1 = _signal(db, c1.id, "SMALLCORP", "Senator One", "2025-03-01", 0.5)
    s2 = _signal(db, c2.id, "SMALLCORP", "Senator Two", "2025-03-02", 0.5)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_COMMITTEE_SWEEP for a in alerts)


def test_committee_sweep_does_not_fire_outside_window(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    c3 = _case(db, f"c3-{uuid.uuid4().hex[:8]}", "Senator Three")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222", "S33333333")
    donor_key = "widecorp"
    s1 = _signal(db, c1.id, "WIDECORP", "Senator One", "2025-01-01", 0.5)
    s2 = _signal(db, c2.id, "WIDECORP", "Senator Two", "2025-01-10", 0.5)
    s3 = _signal(db, c3.id, "WIDECORP", "Senator Three", "2025-02-15", 0.5)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    _fingerprint(db, donor_key, c3.id, s3.id, "Senator Three", "S33333333")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_COMMITTEE_SWEEP for a in alerts)


def test_fingerprint_bloom_fires_at_threshold(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    cases = [_case(db, f"cx-{i}-{uuid.uuid4().hex[:6]}", f"Member {i}") for i in range(4)]
    db.flush()
    donor_key = "bloom donor"
    rel = max(FINGERPRINT_BLOOM_MIN_RELEVANCE, 0.3)
    for i, c in enumerate(cases):
        s = _signal(db, c.id, "BLOOM DONOR", f"Member {i}", "2025-04-01", rel)
        _fingerprint(db, donor_key, c.id, s.id, f"Member {i}", f"B{i:07d}")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    bloom = [a for a in alerts if a.rule_id == RULE_FINGERPRINT_BLOOM]
    assert len(bloom) == 1
    assert len(bloom[0].matched_case_ids) >= 4


def test_fingerprint_bloom_does_not_fire_below_relevance(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    cases = [_case(db, f"low-{i}-{uuid.uuid4().hex[:6]}", f"Low {i}") for i in range(4)]
    db.flush()
    donor_key = "low rel donor"
    rel = FINGERPRINT_BLOOM_MIN_RELEVANCE - 0.05
    for i, c in enumerate(cases):
        s = _signal(db, c.id, "LOW REL DONOR", f"Low {i}", "2025-04-01", rel)
        _fingerprint(db, donor_key, c.id, s.id, f"Low {i}", f"L{i:07d}")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_FINGERPRINT_BLOOM for a in alerts)


def test_pattern_alert_disclaimer_always_present(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    c3 = _case(db, f"c3-{uuid.uuid4().hex[:8]}", "Senator Three")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222", "S33333333")
    donor_key = "megacorp pac2"
    s1 = _signal(db, c1.id, "MEGACORP PAC", "Senator One", "2025-03-01", 0.5)
    s2 = _signal(db, c2.id, "MEGACORP PAC", "Senator Two", "2025-03-05", 0.5)
    s3 = _signal(db, c3.id, "MEGACORP PAC", "Senator Three", "2025-03-08", 0.5)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    _fingerprint(db, donor_key, c3.id, s3.id, "Senator Three", "S33333333")
    db.commit()
    for a in run_pattern_engine(db):
        assert "does not assert coordination" in a.disclaimer
    db.close()


def test_pattern_alert_is_not_folded_into_relevance_score(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    c3 = _case(db, f"c3-{uuid.uuid4().hex[:8]}", "Senator Three")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222", "S33333333")
    donor_key = "immutable rel"
    s1 = _signal(db, c1.id, "IM", "Senator One", "2025-03-01", 0.42)
    s2 = _signal(db, c2.id, "IM", "Senator Two", "2025-03-05", 0.42)
    s3 = _signal(db, c3.id, "IM", "Senator Three", "2025-03-08", 0.42)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    _fingerprint(db, donor_key, c3.id, s3.id, "Senator Three", "S33333333")
    db.commit()
    before = {str(r.id): float(r.relevance_score) for r in db.scalars(select(Signal)).all()}
    run_pattern_engine(db)
    db.expire_all()
    after = {str(r.id): float(r.relevance_score) for r in db.scalars(select(Signal)).all()}
    assert before == after
    db.close()


def test_get_patterns_endpoint_returns_200(client) -> None:
    r = client.get("/api/v1/patterns")
    assert r.status_code == 200
    data = r.json()
    assert "alerts" in data
    assert data.get("total") == len(data["alerts"])
    assert data.get("pattern_engine_version") == "1.0"
    assert "run_at" in data


def test_get_patterns_filter_by_case_id(test_engine, client) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c1 = _case(db, f"c1-{uuid.uuid4().hex[:8]}", "Senator One")
    c2 = _case(db, f"c2-{uuid.uuid4().hex[:8]}", "Senator Two")
    c3 = _case(db, f"c3-{uuid.uuid4().hex[:8]}", "Senator Three")
    cy = _case(db, f"cy-{uuid.uuid4().hex[:8]}", "Other")
    db.flush()
    _seed_finance_committee(db, "S11111111", "S22222222", "S33333333")
    donor_key = "filtercorp"
    s1 = _signal(db, c1.id, "FILTERCORP", "Senator One", "2025-03-01", 0.5)
    s2 = _signal(db, c2.id, "FILTERCORP", "Senator Two", "2025-03-05", 0.5)
    s3 = _signal(db, c3.id, "FILTERCORP", "Senator Three", "2025-03-08", 0.5)
    _fingerprint(db, donor_key, c1.id, s1.id, "Senator One", "S11111111")
    _fingerprint(db, donor_key, c2.id, s2.id, "Senator Two", "S22222222")
    _fingerprint(db, donor_key, c3.id, s3.id, "Senator Three", "S33333333")
    c1_id = c1.id
    cy_id = cy.id
    db.commit()
    db.close()

    r = client.get(f"/api/v1/patterns?case_id={c1_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    for a in data["alerts"]:
        assert str(c1_id) in a["matched_case_ids"]

    r2 = client.get(f"/api/v1/patterns?case_id={cy_id}")
    assert r2.json()["total"] == 0


def test_full_case_payload_includes_pattern_alerts_array(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sig-{uuid.uuid4().hex[:8]}", "Lonely Senator")
    db.commit()
    ents: list = []
    from engines.pattern_engine import pattern_alerts_for_signing, run_pattern_engine

    pal = pattern_alerts_for_signing(run_pattern_engine(db))
    payload = full_case_signing_payload(c, ents, pal)
    assert payload.get("schema_version") == "open-case-full-2"
    assert "pattern_alerts" in payload
    assert isinstance(payload["pattern_alerts"], list)
    apply_case_file_signature(c, [], db=db)
    db.commit()
    db.refresh(c)
    packed = json.loads(c.signed_hash)
    assert "content_hash" in packed
    assert isinstance(packed.get("payload"), dict)
    assert packed["payload"].get("schema_version") == "open-case-full-3"
    assert packed["payload"].get("pattern_alerts") == []
    assert "methodology_note" in packed["payload"]
    db.close()


def test_soft_bundle_fires_at_threshold(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-{uuid.uuid4().hex[:8]}", "Senator Softbundle")
    db.flush()
    committee = "Friends of X"
    specs = [
        ("donor a", "DONOR A", "2025-03-01", 400.0),
        ("donor b", "DONOR B", "2025-03-02", 400.0),
        ("donor c", "DONOR C", "2025-03-03", 400.0),
        ("donor d", "DONOR D", "2025-03-04", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator Softbundle",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Softbundle", "S90000001")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    sb = [a for a in alerts if a.rule_id == RULE_SOFT_BUNDLE]
    assert len(sb) == 1
    assert sb[0].cluster_size == 4
    assert sb[0].aggregate_amount == 1600.0
    assert sb[0].window_days <= SOFT_BUNDLE_MAX_SPAN_DAYS


def test_soft_bundle_insufficient_donors(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb2-{uuid.uuid4().hex[:8]}", "Senator Two")
    db.flush()
    committee = "Friends of X"
    for dk, ddisplay, fd, amt in [
        ("x1", "PERSON ONE", "2025-03-01", 600.0),
        ("x2", "PERSON TWO", "2025-03-02", 600.0),
    ]:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator Two",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Two", "S90000002")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_SOFT_BUNDLE for a in alerts)


def test_soft_bundle_insufficient_aggregate(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb3-{uuid.uuid4().hex[:8]}", "Senator Low")
    db.flush()
    committee = "Friends of X"
    for i, dk in enumerate(["u1", "u2", "u3", "u4"]):
        s = _signal(
            db,
            c.id,
            f"DONOR {i}",
            "Senator Low",
            f"2025-04-{i+1:02d}",
            0.5,
            total_amount=200.0,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Low", "S90000003")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_SOFT_BUNDLE for a in alerts)


def test_soft_bundle_does_not_fire_when_spread_exceeds_window(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb4-{uuid.uuid4().hex[:8]}", "Senator Wide")
    db.flush()
    committee = "Friends of X"
    specs = [
        ("w0", "W0", "2025-03-01", 400.0),
        ("w5", "W5", "2025-03-06", 400.0),
        ("w10", "W10", "2025-03-11", 400.0),
        ("w15", "W15", "2025-03-16", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator Wide",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Wide", "S90000004")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    assert not any(a.rule_id == RULE_SOFT_BUNDLE for a in alerts)


def test_soft_bundle_fires_when_only_receipt_date_in_breakdown(test_engine) -> None:
    """Calendar clustering uses weight_breakdown.receipt_date when event_date_a is absent."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sbrec-{uuid.uuid4().hex[:8]}", "Senator ReceiptOnly")
    db.flush()
    committee = "Friends of X"
    specs = [
        ("r1", "R1", "2025-03-01", 400.0),
        ("r2", "R2", "2025-03-02", 400.0),
        ("r3", "R3", "2025-03-03", 400.0),
        ("r4", "R4", "2025-03-04", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator ReceiptOnly",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
            store_date_as_receipt_only=True,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator ReceiptOnly", "S91000001")
    db.commit()
    alerts = run_pattern_engine(db)
    db.close()
    sb = [a for a in alerts if a.rule_id == RULE_SOFT_BUNDLE]
    assert len(sb) == 1


def test_deadline_discount_applied(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-dl-{uuid.uuid4().hex[:8]}", "Senator YearEnd")
    db.flush()
    committee = "Friends of YearEnd"
    specs = [
        ("y1", "Y1", "2026-12-26", 400.0),
        ("y2", "Y2", "2026-12-27", 400.0),
        ("y3", "Y3", "2026-12-28", 400.0),
        ("y4", "Y4", "2026-12-31", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator YearEnd",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator YearEnd", "S92000001")
    db.commit()
    sb = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SOFT_BUNDLE]
    db.close()
    assert len(sb) == 1
    assert sb[0].deadline_adjacent is True
    assert sb[0].deadline_discount == 0.6
    assert sb[0].deadline_note == "Bundle window overlaps FEC quarterly deadline — reduced weight"
    pl = pattern_alert_to_payload(sb[0])
    assert pl["deadline_adjacent"] is True
    assert pl["deadline_discount"] == 0.6


def test_no_deadline_discount_february(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-feb-{uuid.uuid4().hex[:8]}", "Senator CottonFeb")
    db.flush()
    committee = "Cotton Committee"
    specs = [
        ("f1", "F1", "2026-02-08", 400.0),
        ("f2", "F2", "2026-02-09", 400.0),
        ("f3", "F3", "2026-02-10", 400.0),
        ("f4", "F4", "2026-02-11", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator CottonFeb",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator CottonFeb", "S92000002")
    db.commit()
    sb = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SOFT_BUNDLE]
    db.close()
    assert len(sb) == 1
    assert sb[0].deadline_adjacent is False
    assert sb[0].deadline_discount == 1.0
    assert sb[0].deadline_note is None


def test_proximity_to_vote_score_tiers() -> None:
    assert proximity_to_vote_score_from_days(None) == 0.1
    assert proximity_to_vote_score_from_days(0) == 1.0
    assert proximity_to_vote_score_from_days(7) == 1.0
    assert proximity_to_vote_score_from_days(8) == 0.75
    assert proximity_to_vote_score_from_days(14) == 0.75
    assert proximity_to_vote_score_from_days(15) == 0.5
    assert proximity_to_vote_score_from_days(30) == 0.5
    assert proximity_to_vote_score_from_days(31) == 0.25
    assert proximity_to_vote_score_from_days(60) == 0.25
    assert proximity_to_vote_score_from_days(61) == 0.1
    assert is_deadline_adjacent(date(2026, 12, 31)) is True
    assert is_deadline_adjacent(date(2026, 2, 11)) is False


def test_suspicion_score_computed(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-sus-{uuid.uuid4().hex[:8]}", "Senator Suspect")
    db.flush()
    committee = "Friends of Score"
    # Cluster midpoint for 2026-02-08 .. 2026-02-11 is 2026-02-09 (ordinal average).
    _vote_record(db, c.id, date(2026, 2, 9))
    specs = [
        ("s1", "S1", "2026-02-08", 400.0),
        ("s2", "S2", "2026-02-09", 400.0),
        ("s3", "S3", "2026-02-10", 400.0),
        ("s4", "S4", "2026-02-11", 400.0),
    ]
    for dk, ddisplay, fd, amt in specs:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator Suspect",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Suspect", "S92000003")
    db.commit()
    sb = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SOFT_BUNDLE]
    db.close()
    assert len(sb) == 1
    a = sb[0]
    assert a.proximity_to_vote_score == 1.0
    assert a.deadline_discount == 1.0
    assert a.days_to_nearest_vote == 0
    assert a.amount_diversification is not None
    div = float(a.amount_diversification)
    expected = div * 1.0 * 1.0 * min(4 / 10.0, 1.0)
    assert abs(float(a.suspicion_score or 0.0) - expected) < 1e-9
    pl = pattern_alert_to_payload(a)
    assert abs(float(pl["suspicion_score"] or 0.0) - expected) < 1e-9
    assert pl["proximity_to_vote_score"] == 1.0
    assert pl["deadline_discount"] == 1.0


def test_nearest_vote_description_extracted(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-vdesc-{uuid.uuid4().hex[:8]}", "Senator VoteDesc")
    db.flush()
    long_title = (
        "A joint resolution providing for congressional disapproval of the rule "
        "submitted by the Internal Revenue Service relating to the Corporate AMT."
    )
    _vote_record(
        db,
        c.id,
        date(2026, 2, 9),
        raw_data={
            "congress": "119",
            "question": "On the Motion to Proceed",
            "result": "Motion to Proceed Rejected",
            "bill": {"number": "S.J.Res. 95", "title": long_title},
        },
    )
    committee = "Friends of VoteDesc"
    for dk, ddisplay, fd, amt in [
        ("v1", "V1", "2026-02-08", 400.0),
        ("v2", "V2", "2026-02-09", 400.0),
        ("v3", "V3", "2026-02-10", 400.0),
        ("v4", "V4", "2026-02-11", 400.0),
    ]:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator VoteDesc",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator VoteDesc", "S92000004")
    db.commit()
    sb = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SOFT_BUNDLE]
    db.close()
    assert len(sb) == 1
    a = sb[0]
    assert a.nearest_vote_question == "On the Motion to Proceed"
    assert a.nearest_vote_result == "Motion to Proceed Rejected"
    assert a.nearest_vote_description == "On the Motion to Proceed"
    pl = pattern_alert_to_payload(a)
    assert pl["nearest_vote_question"] == "On the Motion to Proceed"
    assert pl["nearest_vote_result"] == "Motion to Proceed Rejected"
    assert pl["nearest_vote_description"] == "On the Motion to Proceed"

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db2 = Session()
    c2 = _case(db2, f"sb-vdesc2-{uuid.uuid4().hex[:8]}", "Senator BillTitleOnly")
    db2.flush()
    _vote_record(
        db2,
        c2.id,
        date(2026, 3, 9),
        raw_data={
            "congress": "119",
            "result": "Rejected",
            "bill": {"number": "S.J.Res. 95", "title": long_title},
        },
    )
    committee2 = "Friends of BillTitle"
    for dk, ddisplay, fd, amt in [
        ("b1", "B1", "2026-03-08", 400.0),
        ("b2", "B2", "2026-03-09", 400.0),
        ("b3", "B3", "2026-03-10", 400.0),
        ("b4", "B4", "2026-03-11", 400.0),
    ]:
        s = _signal(
            db2,
            c2.id,
            ddisplay,
            "Senator BillTitleOnly",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee2,
        )
        _fingerprint(db2, dk, c2.id, s.id, "Senator BillTitleOnly", "S92000006")
    db2.commit()
    c2_id = str(c2.id)
    sb2 = [
        a
        for a in run_pattern_engine(db2)
        if a.rule_id == RULE_SOFT_BUNDLE and c2_id in a.matched_case_ids
    ]
    db2.close()
    assert len(sb2) == 1
    assert sb2[0].nearest_vote_description == long_title
    assert sb2[0].nearest_vote_result == "Rejected"
    assert sb2[0].nearest_vote_question is None


def test_nearest_vote_description_none_when_missing(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-vnone-{uuid.uuid4().hex[:8]}", "Senator Vnone")
    db.flush()
    _vote_record(db, c.id, date(2026, 2, 9), raw_data={})
    committee = "Friends of Vnone"
    for dk, ddisplay, fd, amt in [
        ("n1", "N1", "2026-02-08", 400.0),
        ("n2", "N2", "2026-02-09", 400.0),
        ("n3", "N3", "2026-02-10", 400.0),
        ("n4", "N4", "2026-02-11", 400.0),
    ]:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator Vnone",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Vnone", "S92000005")
    db.commit()
    sb = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SOFT_BUNDLE]
    db.close()
    assert len(sb) == 1
    a = sb[0]
    assert a.nearest_vote_id is not None
    assert a.nearest_vote_description is None
    assert a.nearest_vote_result is None
    assert a.nearest_vote_question is None
    pl = pattern_alert_to_payload(a)
    assert pl["nearest_vote_description"] is None
    assert pl["nearest_vote_result"] is None
    assert pl["nearest_vote_question"] is None


def test_refund_rows_are_not_ingested_from_fec_schedule_a() -> None:
    assert (
        fec_schedule_a_row_exclusion_reason(
            {"transaction_tp": "22Z", "contribution_receipt_amount": -2400}
        )
        == "fec_refund_transaction_type"
    )
    assert (
        fec_schedule_a_row_exclusion_reason(
            {"transaction_tp": "20Z", "contribution_receipt_amount": "-100"}
        )
        == "fec_refund_transaction_type"
    )
    assert (
        fec_schedule_a_row_exclusion_reason(
            {"transaction_tp": "17Z", "contribution_receipt_amount": 50}
        )
        == "fec_refund_transaction_type"
    )
    assert (
        fec_schedule_a_row_exclusion_reason(
            {"transaction_tp": "15Z", "contribution_receipt_amount": 250}
        )
        is None
    )
    assert (
        fec_schedule_a_row_exclusion_reason(
            {"transaction_tp": "15Z", "contribution_receipt_amount": -88}
        )
        == "negative_amount"
    )
