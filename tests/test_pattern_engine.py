"""Phase 9B — pattern engine and patterns API (cross-official detection)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from adapters.fec import classify_donor_type, fec_schedule_a_row_exclusion_reason
from engines.pattern_engine import (
    COMMITTEE_SWEEP_MAX_WINDOW_DAYS,
    FINGERPRINT_BLOOM_MIN_RELEVANCE,
    PATTERN_ENGINE_VERSION,
    RULE_COMMITTEE_SWEEP,
    RULE_DISBURSEMENT_LOOP,
    RULE_FINGERPRINT_BLOOM,
    RULE_GEO_MISMATCH,
    RULE_REVOLVING_DOOR,
    RULE_SECTOR_CONVERGENCE,
    RULE_SOFT_BUNDLE,
    RULE_SOFT_BUNDLE_V2,
    SOFT_BUNDLE_MAX_SPAN_DAYS,
    _is_individual_donor,
    classify_donor_sector,
    is_deadline_adjacent,
    pattern_alert_to_payload,
    proximity_to_vote_score_from_days,
    match_donor_to_lda,
    run_pattern_engine,
    vote_matches_sector,
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


def _fec_receipt_entry(
    db,
    case_id: uuid.UUID,
    *,
    contributor_name: str,
    amount: float,
    receipt_date: str,
    contributor_state: str | None = None,
    contributor_employer: str = "",
    contributor_occupation: str = "",
    contributor_committee_id: str | None = None,
    donor_type: str | None = None,
) -> EvidenceEntry:
    raw: dict[str, Any] = {
        "contributor_name": contributor_name,
        "contribution_receipt_amount": amount,
        "contribution_receipt_date": receipt_date,
        "entity_type": "IND",
    }
    if contributor_state:
        raw["contributor_state"] = contributor_state
    if contributor_employer:
        raw["contributor_employer"] = contributor_employer
    if contributor_occupation:
        raw["contributor_occupation"] = contributor_occupation
    if contributor_committee_id:
        raw["contributor_committee_id"] = contributor_committee_id
    if donor_type:
        raw["donor_type"] = donor_type
    ev = EvidenceEntry(
        case_file_id=case_id,
        entry_type="financial_connection",
        title=f"FEC: {contributor_name}",
        body="test",
        source_url="https://www.fec.gov/",
        source_name="FEC",
        adapter_name="FEC",
        date_of_event=date.fromisoformat(receipt_date[:10]),
        entered_by="pat_eng_tester",
        confidence="confirmed",
        amount=amount,
        matched_name=contributor_name,
        donor_type=donor_type,
        raw_data_json=json.dumps(raw, separators=(",", ":")),
    )
    db.add(ev)
    db.flush()
    return ev


def _signal_with_fec(
    db,
    case_id: uuid.UUID,
    donor: str,
    official: str,
    fin_date: str,
    relevance: float,
    fec_entry: EvidenceEntry,
    *,
    total_amount: float,
    committee_label: str,
    donor_key: str,
    **bd_extra: Any,
) -> Signal:
    ident = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    bd = {
        "kind": "donor_cluster",
        "donor": donor,
        "official": official,
        "total_amount": total_amount,
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
        "committee_label": committee_label,
        "has_collision": False,
        "has_jurisdictional_match": False,
        "has_lda_filing": False,
        "relevance_score": relevance,
    }
    bd.update(bd_extra)
    s = Signal(
        case_file_id=case_id,
        signal_identity_hash=ident,
        signal_type="temporal_proximity",
        weight=0.6,
        description="test",
        evidence_ids=json.dumps([str(fec_entry.id)], separators=(",", ":")),
        exposure_state="internal",
        actor_a=donor,
        actor_b=official,
        event_date_a=fin_date,
        event_date_b="2025-06-01",
        days_between=-5,
        relevance_score=relevance,
        weight_breakdown=json.dumps(bd, separators=(",", ":")),
    )
    db.add(s)
    db.flush()
    return s


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
    assert data.get("pattern_engine_version") == PATTERN_ENGINE_VERSION
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
    # Year boundary: window end early Jan still within 5 days of prior Dec 31 deadline.
    assert is_deadline_adjacent(date(2023, 1, 2)) is True


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
    assert a.nearest_vote_description == long_title
    pl = pattern_alert_to_payload(a)
    assert pl["nearest_vote_question"] == "On the Motion to Proceed"
    assert pl["nearest_vote_result"] == "Motion to Proceed Rejected"
    assert pl["nearest_vote_description"] == long_title

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


def test_nearest_vote_result_accepts_vote_result_text_key(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-vrtx-{uuid.uuid4().hex[:8]}", "Senator VoteResultText")
    db.flush()
    _vote_record(
        db,
        c.id,
        date(2026, 5, 9),
        raw_data={
            "congress": "119",
            "vote_result_text": "Motion to Proceed Rejected",
            "bill": {"number": "S.J.Res. 95", "title": "Corporate AMT disapproval"},
        },
    )
    committee = "Friends of Vrtx"
    for dk, ddisplay, fd, amt in [
        ("t1", "T1", "2026-05-08", 400.0),
        ("t2", "T2", "2026-05-09", 400.0),
        ("t3", "T3", "2026-05-10", 400.0),
        ("t4", "T4", "2026-05-11", 400.0),
    ]:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator VoteResultText",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator VoteResultText", "S92000008")
    db.commit()
    sb = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_SOFT_BUNDLE and str(c.id) in a.matched_case_ids
    ]
    db.close()
    assert len(sb) == 1
    assert sb[0].nearest_vote_result == "Motion to Proceed Rejected"


def test_nearest_vote_result_accepts_voteResult_key(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb-vres-{uuid.uuid4().hex[:8]}", "Senator VoteResultKey")
    db.flush()
    _vote_record(
        db,
        c.id,
        date(2026, 4, 9),
        raw_data={
            "congress": "119",
            "question": "On Passage",
            "voteResult": "Agreed to",
            "bill": {"number": "S. 1", "title": "An Act to Test"},
        },
    )
    committee = "Friends of VoteResult"
    for dk, ddisplay, fd, amt in [
        ("r1", "R1", "2026-04-08", 400.0),
        ("r2", "R2", "2026-04-09", 400.0),
        ("r3", "R3", "2026-04-10", 400.0),
        ("r4", "R4", "2026-04-11", 400.0),
    ]:
        s = _signal(
            db,
            c.id,
            ddisplay,
            "Senator VoteResultKey",
            fd,
            0.5,
            total_amount=amt,
            committee_label=committee,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator VoteResultKey", "S92000007")
    db.commit()
    sb = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_SOFT_BUNDLE and str(c.id) in a.matched_case_ids
    ]
    db.close()
    assert len(sb) == 1
    assert sb[0].nearest_vote_result == "Agreed to"


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


def test_classify_donor_sector_pharma() -> None:
    assert classify_donor_sector("PHARMACEUTICAL RESEARCH PAC", "", "") == "pharma"


def test_classify_donor_sector_finance() -> None:
    assert classify_donor_sector("AMERICAN BANKERS ASSOCIATION PAC", "", "") == "finance"


def test_classify_donor_sector_none() -> None:
    assert classify_donor_sector("SMITH, JOHN", "", "") is None


def test_vote_matches_sector_true() -> None:
    assert vote_matches_sector("Corporate Alternative Minimum Tax", "finance")


def test_vote_matches_sector_false() -> None:
    assert not vote_matches_sector("Corporate Alternative Minimum Tax", "pharma")


def test_sector_convergence_fires(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sec-conv-{uuid.uuid4().hex[:8]}", "Senator Sector")
    db.flush()
    _vote_record(
        db,
        c.id,
        date(2026, 2, 10),
        raw_data={
            "bill": {"title": "Corporate Alternative Minimum Tax repeal"},
            "question": "On Passage",
            "result": "Rejected",
        },
    )
    comm = "Friends of Sector"
    specs = [
        ("bk1", "FIRST BANK PAC", "2026-02-01", 2000.0),
        ("bk2", "CAPITAL MARKETS PAC", "2026-02-03", 2000.0),
        ("bk3", "SECURITIES GROUP PAC", "2026-02-05", 2000.0),
        ("bk4", "LENDING ALL INC PAC", "2026-02-07", 2000.0),
    ]
    for dk, disp, fd, amt in specs:
        fe = _fec_receipt_entry(db, c.id, contributor_name=disp, amount=amt, receipt_date=fd[:10])
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sector",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sector", "C001095")
    db.commit()
    case_id_str = str(c.id)
    sc = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SECTOR_CONVERGENCE]
    db.close()
    assert len(sc) >= 1
    hit = next(x for x in sc if case_id_str in x.matched_case_ids)
    assert hit.sector == "finance"
    assert hit.sector_vote_match is True
    assert hit.sector_donor_count == 4


def test_sector_convergence_no_match_still_fires(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sec-nm-{uuid.uuid4().hex[:8]}", "Senator Ag")
    db.flush()
    _vote_record(
        db,
        c.id,
        date(2026, 3, 10),
        raw_data={"bill": {"title": "Corporate Alternative Minimum Tax"}},
    )
    comm = "Ag Committee Friends"
    specs = [
        ("ag1", "CORN GROWERS PAC", "2026-03-01", 2000.0),
        ("ag2", "SOYBEAN FARMERS PAC", "2026-03-03", 2000.0),
        ("ag3", "RURAL GRAIN PAC", "2026-03-05", 2000.0),
        ("ag4", "WHEAT PRODUCERS PAC", "2026-03-07", 2000.0),
    ]
    for dk, disp, fd, amt in specs:
        fe = _fec_receipt_entry(db, c.id, contributor_name=disp, amount=amt, receipt_date=fd[:10])
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Ag",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Ag", "C001095")
    db.commit()
    case_id_str = str(c.id)
    sc = [a for a in run_pattern_engine(db) if a.rule_id == RULE_SECTOR_CONVERGENCE]
    db.close()
    hit = next(x for x in sc if case_id_str in x.matched_case_ids)
    assert hit.sector == "agriculture"
    assert hit.sector_vote_match is False


def test_is_individual_donor_flags_orgs() -> None:
    assert _is_individual_donor("Samuel Okonkwo")
    assert _is_individual_donor("Vincent Price")
    assert not _is_individual_donor("ACME INDUSTRIAL LLC")
    assert not _is_individual_donor("MIDSTATE BANK")


def test_geo_mismatch_fires(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-{uuid.uuid4().hex[:8]}", "Senator Sullivan")
    db.flush()
    comm = "Alaska PAC"
    states = ["TX", "FL", "NY", "CA", "WA"]
    donor_names = [
        "Elena Marks",
        "Frank O Brien",
        "Gina Parekh",
        "Henry Quist",
        "Iris Romero",
    ]
    for i, st in enumerate(states):
        dk = f"out{i}"
        disp = donor_names[i]
        fd = f"2026-06-{i+1:02d}"
        amt = 500.0
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=amt, receipt_date=fd[:10], contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sullivan",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sullivan", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH]
    db.close()
    assert any(case_id_str in a.matched_case_ids for a in geo)
    hit = next(a for a in geo if case_id_str in a.matched_case_ids)
    assert hit.senator_state == "AK"
    assert hit.out_of_state_ratio >= 0.75
    assert len(hit.top_donor_states or []) >= 1
    assert (hit.individual_donor_count or 0) >= 5
    assert hit.org_donor_count == 0


def test_geo_mismatch_no_fire_org_only_pacs(test_engine) -> None:
    """PAC/org-only window: no classifiable individuals — must not fire GEO_MISMATCH."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-onlypac-{uuid.uuid4().hex[:8]}", "Senator PACOnly")
    db.flush()
    comm = "Alaska Org Only"
    for i, st in enumerate(["TX", "FL", "NY", "CA", "OH"]):
        dk = f"po{i}"
        disp = f"VOTERS BLUE PAC {i}"
        fd = f"2026-09-{i+1:02d}"
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=800.0, receipt_date=fd, contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator PACOnly",
            fd,
            0.5,
            fe,
            total_amount=800.0,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator PACOnly", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    assert not geo


def test_geo_mismatch_no_fire_mostly_instate(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-in-{uuid.uuid4().hex[:8]}", "Senator Sul In")
    db.flush()
    comm = "Alaska In"
    for i, st in enumerate(["AK", "AK", "AK", "AK", "TX"]):
        dk = f"ix{i}"
        disp = f"DONOR {i}"
        fd = f"2026-07-{i+1:02d}"
        amt = 300.0
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=amt, receipt_date=fd[:10], contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sul In",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sul In", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    assert not geo


def test_geo_mismatch_dc_pac_excluded(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-dc-{uuid.uuid4().hex[:8]}", "Senator Sul DC")
    db.flush()
    comm = "Alaska DC"
    for i, (disp, st) in enumerate(
        [
            ("TRADE ASSOCIATION PAC", "DC"),
            ("LOBBY COMMITTEE PAC", "DC"),
            ("Mara Okoye", "TX"),
            ("Niles Perry", "FL"),
            ("Oona Quade", "NV"),
            ("Pete Rhodes", "CA"),
            ("Quinn Santos", "WA"),
        ]
    ):
        dk = f"dc{i}"
        fd = f"2026-08-{i+1:02d}"
        amt = 400.0
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=amt, receipt_date=fd[:10], contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sul DC",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sul DC", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    assert any(a.out_of_state_ratio >= 0.75 for a in geo)


def test_geo_mismatch_mixed_includes_org_counts(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-mix-{uuid.uuid4().hex[:8]}", "Senator Mix")
    db.flush()
    comm = "Alaska Mix"
    rows = [
        ("HEAVY PAC ONE", "TX", "m0"),
        ("Elena Marks Solo", "FL", "m1"),
        ("Frank O Brien Solo", "NY", "m2"),
        ("Gina Parekh Solo", "CA", "m3"),
        ("Henry Quist Solo", "WA", "m4"),
        ("Iris Romero Solo", "OH", "m5"),
    ]
    for i, (disp, st, dk) in enumerate(rows):
        fd = f"2026-11-{i + 1:02d}"
        fe = _fec_receipt_entry(db, c.id, contributor_name=disp, amount=500.0, receipt_date=fd, contributor_state=st)
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Mix",
            fd,
            0.5,
            fe,
            total_amount=500.0,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Mix", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    assert geo
    assert (geo[0].individual_donor_count or 0) >= 5
    assert (geo[0].org_donor_count or 0) >= 1


def test_disbursement_loop_fires(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"dis-loop-{uuid.uuid4().hex[:8]}", "Senator Loop")
    db.flush()
    _vote_record(db, c.id, date(2026, 4, 15))
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="TRANSFER PAC",
        amount=100.0,
        receipt_date="2026-04-10",
        contributor_committee_id="C00987654",
    )
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="fec_disbursement",
            title="Disbursement test",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=date(2026, 4, 12),
            entered_by="pat_eng_tester",
            confidence="confirmed",
            amount=6000.0,
            raw_data_json=json.dumps(
                {
                    "disbursement_amount": 6000,
                    "disbursement_date": "2026-04-12",
                    "recipient_committee_id": "C00987654",
                    "recipient_name": "TRANSFER PAC",
                    "committee_id": "C00112233",
                },
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    case_id_str = str(c.id)
    loops = [a for a in run_pattern_engine(db) if a.rule_id == RULE_DISBURSEMENT_LOOP]
    db.close()
    assert any(case_id_str in x.matched_case_ids and x.loop_confirmed for x in loops)


def test_disbursement_loop_no_fire_no_loop(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"dis-nl-{uuid.uuid4().hex[:8]}", "Senator Noloop")
    db.flush()
    _vote_record(db, c.id, date(2026, 5, 15))
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="OTHER PAC",
        amount=100.0,
        receipt_date="2026-05-10",
        contributor_committee_id="C11111111",
    )
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="fec_disbursement",
            title="Disbursement out",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=date(2026, 5, 12),
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "disbursement_amount": 8000,
                    "disbursement_date": "2026-05-12",
                    "recipient_committee_id": "C00999999",
                    "committee_id": "C00112233",
                },
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    case_id_str = str(c.id)
    loops = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_DISBURSEMENT_LOOP and case_id_str in a.matched_case_ids
    ]
    db.close()
    assert loops and all(not x.loop_confirmed for x in loops)


def test_revolving_door_fires(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-{uuid.uuid4().hex[:8]}", "Senator Revolving")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "ACME LOBBY GROUP FEDERAL AFFAIRS",
                    "client_name": "MegaCorp",
                    "filing_year": 2025,
                    "issue_codes": ["TAX"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="ACME LOBBY GROUP",
        amount=500.0,
        receipt_date="2026-06-01",
    )
    s = _signal_with_fec(
        db,
        c.id,
        "ACME LOBBY GROUP",
        "Senator Revolving",
        "2026-06-01",
        0.5,
        fe,
        total_amount=500.0,
        committee_label="Friends of Revolving",
        donor_key="acme",
        has_lda_filing=True,
    )
    _fingerprint(db, "acme", c.id, s.id, "Senator Revolving", "C001095")
    _vote_record(
        db,
        c.id,
        date(2026, 6, 5),
        raw_data={"bill": {"title": "Corporate income tax reform"}},
    )
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR]
    db.close()
    assert any(case_id_str in x.matched_case_ids for x in rev)


def test_revolving_door_vote_relevant_true(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-vr-{uuid.uuid4().hex[:8]}", "Senator RelV")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "TAX ADVOCATES INC",
                    "client_name": "ClientCo",
                    "filing_year": 2025,
                    "issue_codes": ["TAX", "FIN"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(db, c.id, contributor_name="TAX ADVOCATES INC", amount=400.0, receipt_date="2026-08-01")
    s = _signal_with_fec(
        db,
        c.id,
        "TAX ADVOCATES INC",
        "Senator RelV",
        "2026-08-01",
        0.5,
        fe,
        total_amount=400.0,
        committee_label="Friends Rel",
        donor_key="taxadv",
        has_lda_filing=True,
    )
    _fingerprint(db, "taxadv", c.id, s.id, "Senator RelV", "C001095")
    _vote_record(
        db,
        c.id,
        date(2026, 8, 5),
        raw_data={"bill": {"title": "Securities lending reform and bank oversight"}},
    )
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert rev and rev[0].revolving_door_vote_relevant is True


def test_match_donor_to_lda_short_employer_does_not_fire() -> None:
    """Employer substring match requires >= 8 chars (e.g. 'BURLING' vs Covington)."""
    raw = {
        "filing_uuid": str(uuid.uuid4()),
        "registrant_name": "COVINGTON & BURLING LLP",
        "client_name": "",
        "filing_year": 2025,
        "issue_codes": [],
        "lobbyist_names": [],
    }
    ent = SimpleNamespace(
        entry_type="lobbying_filing",
        raw_data_json=json.dumps(raw, separators=(",", ":")),
    )
    assert match_donor_to_lda("SOME DRAFT COMMITTEE", "BURLING", [ent]) == []


def test_match_donor_to_lda_retired_employer_ignored() -> None:
    raw = {
        "filing_uuid": str(uuid.uuid4()),
        "registrant_name": "COVINGTON & BURLING LLP",
        "client_name": "",
        "filing_year": 2025,
        "issue_codes": [],
        "lobbyist_names": [],
    }
    ent = SimpleNamespace(
        entry_type="lobbying_filing",
        raw_data_json=json.dumps(raw, separators=(",", ":")),
    )
    assert match_donor_to_lda("UNRELATED PAC", "Retired", [ent]) == []


def test_revolving_door_actblue_skipped(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-ab-{uuid.uuid4().hex[:8]}", "Senator ActblueBlock")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "COVINGTON & BURLING LLP",
                    "client_name": "Some Client",
                    "filing_year": 2025,
                    "issue_codes": ["TAX"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="ACTBLUE",
        amount=250.0,
        receipt_date="2026-10-01",
        contributor_employer="ATTORNEY",
    )
    s = _signal_with_fec(
        db,
        c.id,
        "ACTBLUE",
        "Senator ActblueBlock",
        "2026-10-01",
        0.5,
        fe,
        total_amount=250.0,
        committee_label="Friends Actblue",
        donor_key="ab",
        has_lda_filing=True,
    )
    _fingerprint(db, "ab", c.id, s.id, "Senator ActblueBlock", "C001095")
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert not rev


def test_revolving_door_dedup_single_alert_per_relationship(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-dedup1-{uuid.uuid4().hex[:8]}", "Senator Dedup1")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "DEDUP LOBBY INC SERVICES LLC",
                    "client_name": "Client",
                    "filing_year": 2025,
                    "issue_codes": ["TAX"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    donor_disp = "DEDUP LOBBY INC"
    for i in range(5):
        fd = f"2026-04-{i + 1:02d}"
        fe = _fec_receipt_entry(
            db,
            c.id,
            contributor_name=donor_disp,
            amount=300.0,
            receipt_date=fd,
        )
        s = _signal_with_fec(
            db,
            c.id,
            donor_disp,
            "Senator Dedup1",
            fd,
            0.5,
            fe,
            total_amount=300.0,
            committee_label="Friends Dedup",
            donor_key="dedupdk",
            has_lda_filing=True,
        )
        _fingerprint(db, "dedupdk", c.id, s.id, "Senator Dedup1", "C001095")
    _vote_record(db, c.id, date(2026, 4, 10))
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert len(rev) == 1
    assert len(rev[0].evidence_refs) == 5
    assert rev[0].lda_match_count == 1


def test_revolving_door_dedup_two_registrants_two_alerts(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-dedup2-{uuid.uuid4().hex[:8]}", "Senator Dedup2")
    db.flush()
    for reg_name in ("DEDUP TWO REG ALPHA LLC", "DEDUP TWO REG BETA LLC"):
        db.add(
            EvidenceEntry(
                case_file_id=c.id,
                entry_type="lobbying_filing",
                title="LDA filing",
                body="test",
                source_url="https://lda.senate.gov/",
                source_name="Senate LDA",
                adapter_name="Senate LDA",
                entered_by="pat_eng_tester",
                confidence="confirmed",
                raw_data_json=json.dumps(
                    {
                        "filing_uuid": str(uuid.uuid4()),
                        "registrant_name": reg_name,
                        "client_name": "Client",
                        "filing_year": 2025,
                        "issue_codes": ["TAX"],
                        "lobbyist_names": [],
                    },
                    separators=(",", ":"),
                ),
            )
        )
    donor_disp = "DEDUP TWO REG"
    for i, fd in enumerate(["2026-05-01", "2026-05-02"]):
        fe = _fec_receipt_entry(
            db,
            c.id,
            contributor_name=donor_disp,
            amount=350.0,
            receipt_date=fd,
        )
        s = _signal_with_fec(
            db,
            c.id,
            donor_disp,
            "Senator Dedup2",
            fd,
            0.5,
            fe,
            total_amount=350.0,
            committee_label="Friends Dedup2",
            donor_key="dedup2dk",
            has_lda_filing=True,
        )
        _fingerprint(db, "dedup2dk", c.id, s.id, "Senator Dedup2", "C001095")
    _vote_record(db, c.id, date(2026, 5, 5))
    db.commit()
    case_id_str = str(c.id)
    rev = sorted(
        [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids],
        key=lambda x: (x.matched_lda_registrant or ""),
    )
    db.close()
    assert len(rev) == 2
    assert rev[0].lda_match_count == 2
    assert rev[1].lda_match_count == 2
    assert len(rev[0].evidence_refs) == 2
    regs = {a.matched_lda_registrant for a in rev}
    assert "DEDUP TWO REG ALPHA LLC" in regs
    assert "DEDUP TWO REG BETA LLC" in regs


def test_revolving_door_nomination_vote_relevant(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-nom-y-{uuid.uuid4().hex[:8]}", "Senator NomYes")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "NOM DEFENSE LOBBY LLC",
                    "client_name": "Client",
                    "filing_year": 2025,
                    "issue_codes": ["DEF"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="NOM DEFENSE LOBBY",
        amount=400.0,
        receipt_date="2026-06-08",
    )
    s = _signal_with_fec(
        db,
        c.id,
        "NOM DEFENSE LOBBY",
        "Senator NomYes",
        "2026-06-08",
        0.5,
        fe,
        total_amount=400.0,
        committee_label="Friends NomY",
        donor_key="nomdef",
        has_lda_filing=True,
    )
    _fingerprint(db, "nomdef", c.id, s.id, "Senator NomYes", "C001095")
    _vote_record(
        db,
        c.id,
        date(2026, 6, 12),
        raw_data={
            "question": "On the Nomination",
            "bill": {
                "title": (
                    "Jane Q. Public, of Virginia, to be Assistant Secretary of Defense for "
                    "Health Affairs"
                ),
            },
        },
    )
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert rev and rev[0].revolving_door_vote_relevant is True


def test_revolving_door_nomination_vote_not_relevant(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-nom-n-{uuid.uuid4().hex[:8]}", "Senator NomNo")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "NOM DEFENSE LOBBY TWO LLC",
                    "client_name": "Client",
                    "filing_year": 2025,
                    "issue_codes": ["DEF"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="NOM DEFENSE LOBBY TWO",
        amount=400.0,
        receipt_date="2026-07-08",
    )
    s = _signal_with_fec(
        db,
        c.id,
        "NOM DEFENSE LOBBY TWO",
        "Senator NomNo",
        "2026-07-08",
        0.5,
        fe,
        total_amount=400.0,
        committee_label="Friends NomN",
        donor_key="nomdef2",
        has_lda_filing=True,
    )
    _fingerprint(db, "nomdef2", c.id, s.id, "Senator NomNo", "C001095")
    _vote_record(
        db,
        c.id,
        date(2026, 7, 12),
        raw_data={
            "question": "On the Nomination",
            "bill": {
                "title": "Pat Smith, of Iowa, to be Secretary of Agriculture",
            },
        },
    )
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert rev and rev[0].revolving_door_vote_relevant is False


def test_geo_mismatch_calendar_merge(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-dedup-{uuid.uuid4().hex[:8]}", "Senator Sullivan")
    db.flush()
    comm = "Alaska PAC Merge"
    states_and_days = [
        ("TX", "2026-06-01"),
        ("FL", "2026-06-02"),
        ("NY", "2026-06-03"),
        ("CA", "2026-06-04"),
        ("WA", "2026-06-05"),
        ("OH", "2026-06-08"),
    ]
    dedup_people = [
        "Geo Dedup Adams",
        "Geo Dedup Bell",
        "Geo Dedup Cabot",
        "Geo Dedup Diaz",
        "Geo Dedup Ellis",
        "Geo Dedup Ford",
    ]
    for i, (st, fd) in enumerate(states_and_days):
        dk = f"gm{i}"
        disp = dedup_people[i]
        amt = 500.0
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=amt, receipt_date=fd, contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sullivan",
            fd,
            0.5,
            fe,
            total_amount=amt,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sullivan", "S001198")
    fe2 = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="Geo Dedup Cabot",
        amount=500.0,
        receipt_date="2026-06-06",
        contributor_state="NY",
    )
    s2 = _signal_with_fec(
        db,
        c.id,
        "Geo Dedup Cabot",
        "Senator Sullivan",
        "2026-06-06",
        0.5,
        fe2,
        total_amount=500.0,
        committee_label=comm,
        donor_key="gm2",
    )
    _fingerprint(db, "gm2", c.id, s2.id, "Senator Sullivan", "S001198")
    for dk, disp, fd, st in [
        ("gm0", "Geo Dedup Adams", "2026-06-09", "TX"),
        ("gm1", "Geo Dedup Bell", "2026-06-10", "FL"),
    ]:
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=disp, amount=500.0, receipt_date=fd, contributor_state=st
        )
        s = _signal_with_fec(
            db,
            c.id,
            disp,
            "Senator Sullivan",
            fd,
            0.5,
            fe,
            total_amount=500.0,
            committee_label=comm,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator Sullivan", "S001198")
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    merged = geo[0]
    assert len(geo) == 1
    assert merged.donation_window_start == date(2026, 6, 1)
    assert merged.donation_window_end == date(2026, 6, 10)
    assert (merged.donation_window_end - merged.donation_window_start).days == 9


def test_geo_mismatch_per_committee_cap(test_engine) -> None:
    """At most 3 GEO_MISMATCH alerts per committee; keep highest suspicion_score."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"geo-cap-{uuid.uuid4().hex[:8]}", "Senator Cap")
    db.flush()
    comm = "Alaska Cap Committee"
    month_states = [
        ["TX", "FL", "NY", "CA", "WA"],
        ["OH", "TX", "FL", "NY", "CA"],
        ["WA", "OH", "TX", "FL", "NY"],
        ["CA", "WA", "OH", "TX", "FL"],
    ]
    dk_ix_session = [0]
    for mnum, states in enumerate(month_states, start=1):
        for i, st in enumerate(states):
            dk_ix_session[0] += 1
            dk = f"capdk{dk_ix_session[0]}"
            disp = f"Cap Person {dk_ix_session[0]}"
            fd = f"2026-{mnum:02d}-{i + 1:02d}"
            fe = _fec_receipt_entry(
                db, c.id, contributor_name=disp, amount=600.0, receipt_date=fd, contributor_state=st
            )
            s = _signal_with_fec(
                db,
                c.id,
                disp,
                "Senator Cap",
                fd,
                0.5,
                fe,
                total_amount=600.0,
                committee_label=comm,
                donor_key=dk,
            )
            _fingerprint(db, dk, c.id, s.id, "Senator Cap", "S001198")
    _vote_record(db, c.id, date(2026, 3, 10))
    db.commit()
    case_id_str = str(c.id)
    geo = [a for a in run_pattern_engine(db) if a.rule_id == RULE_GEO_MISMATCH and case_id_str in a.matched_case_ids]
    db.close()
    assert len(geo) == 3
    jan_alert = [a for a in geo if a.donation_window_start == date(2026, 1, 1)]
    assert not jan_alert


def test_revolving_door_filing_before_2024_skipped(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-old-{uuid.uuid4().hex[:8]}", "Senator OldLda")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            adapter_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "OLD YEAR LOBBYISTS NATIONAL HEADQUARTERS",
                    "client_name": "ClientCo",
                    "filing_year": 2023,
                    "issue_codes": ["TAX"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(
        db,
        c.id,
        contributor_name="OLD YEAR LOBBYISTS",
        amount=500.0,
        receipt_date="2026-06-01",
    )
    s = _signal_with_fec(
        db,
        c.id,
        "OLD YEAR LOBBYISTS",
        "Senator OldLda",
        "2026-06-01",
        0.5,
        fe,
        total_amount=500.0,
        committee_label="Friends Old",
        donor_key="oldy",
        has_lda_filing=True,
    )
    _fingerprint(db, "oldy", c.id, s.id, "Senator OldLda", "C001095")
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert not rev


def test_revolving_door_no_match(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"rev-nm-{uuid.uuid4().hex[:8]}", "Senator NoLda")
    db.flush()
    db.add(
        EvidenceEntry(
            case_file_id=c.id,
            entry_type="lobbying_filing",
            title="LDA filing",
            body="test",
            source_url="https://lda.senate.gov/",
            source_name="Senate LDA",
            entered_by="pat_eng_tester",
            confidence="confirmed",
            raw_data_json=json.dumps(
                {
                    "filing_uuid": str(uuid.uuid4()),
                    "registrant_name": "UNRELATED ENTITY LLC",
                    "client_name": "OtherCo",
                    "filing_year": 2025,
                    "issue_codes": ["TAX"],
                    "lobbyist_names": [],
                },
                separators=(",", ":"),
            ),
        )
    )
    fe = _fec_receipt_entry(db, c.id, contributor_name="RANDOM DONOR PAC", amount=400.0, receipt_date="2026-09-01")
    s = _signal_with_fec(
        db,
        c.id,
        "RANDOM DONOR PAC",
        "Senator NoLda",
        "2026-09-01",
        0.5,
        fe,
        total_amount=400.0,
        committee_label="Friends Nm",
        donor_key="rnd",
        has_lda_filing=True,
    )
    _fingerprint(db, "rnd", c.id, s.id, "Senator NoLda", "C001095")
    db.commit()
    case_id_str = str(c.id)
    rev = [a for a in run_pattern_engine(db) if a.rule_id == RULE_REVOLVING_DOOR and case_id_str in a.matched_case_ids]
    db.close()
    assert not rev


def test_classify_donor_type_individual() -> None:
    assert classify_donor_type("IND", None) == "individual"


def test_classify_donor_type_super_pac() -> None:
    assert classify_donor_type("COM", "U") == "super_pac"


def test_classify_donor_type_pac() -> None:
    assert classify_donor_type("COM", "N") == "pac"


def test_soft_bundle_v2_individual_bonus(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb2-ib-{uuid.uuid4().hex[:8]}", "Senator V2IndivBonus")
    db.flush()
    committee = "V2 Indiv Committee"
    donors = [
        ("ib1", "Indiv Bonus A", "individual"),
        ("ib2", "Indiv Bonus B", "individual"),
        ("ib3", "Indiv Bonus C", "individual"),
        ("ib4", "Indiv Bonus D", "individual"),
        ("ib5", "PAC BONUS E", "pac"),
    ]
    for i, (dk, name, dt) in enumerate(donors):
        fd = f"2026-04-{1 + i:02d}"
        fe = _fec_receipt_entry(
            db,
            c.id,
            contributor_name=name,
            amount=400.0,
            receipt_date=fd,
            donor_type=dt,
        )
        s = _signal_with_fec(
            db,
            c.id,
            name,
            "Senator V2IndivBonus",
            fd,
            0.5,
            fe,
            total_amount=400.0,
            committee_label=committee,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator V2IndivBonus", "S001198")
    db.commit()
    case_id_str = str(c.id)
    v2 = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_SOFT_BUNDLE_V2 and case_id_str in a.matched_case_ids
    ]
    db.close()
    assert v2
    diag = json.loads(v2[0].diagnostics_json or "{}")
    assert diag["individual_fraction"] >= 0.7
    comps = {x.get("component") for x in diag.get("adjustments", [])}
    assert "individual_bonus" in comps


def test_soft_bundle_v2_org_penalty(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb2-op-{uuid.uuid4().hex[:8]}", "Senator V2OrgPen")
    db.flush()
    committee = "V2 Org Committee"
    donors = [
        ("op1", "Solo Indiv Person", "individual"),
        ("op2", "PAC Zebra", "pac"),
        ("op3", "PAC Yak", "pac"),
        ("op4", "PAC Xray", "pac"),
        ("op5", "PAC Walt", "pac"),
    ]
    for i, (dk, name, dt) in enumerate(donors):
        fd = f"2026-05-{1 + i:02d}"
        fe = _fec_receipt_entry(
            db,
            c.id,
            contributor_name=name,
            amount=400.0,
            receipt_date=fd,
            donor_type=dt,
        )
        s = _signal_with_fec(
            db,
            c.id,
            name,
            "Senator V2OrgPen",
            fd,
            0.5,
            fe,
            total_amount=400.0,
            committee_label=committee,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator V2OrgPen", "S001198")
    db.commit()
    case_id_str = str(c.id)
    v2 = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_SOFT_BUNDLE_V2 and case_id_str in a.matched_case_ids
    ]
    db.close()
    assert v2
    diag = json.loads(v2[0].diagnostics_json or "{}")
    assert diag["individual_fraction"] <= 0.3
    comps = {x.get("component") for x in diag.get("adjustments", [])}
    assert "org_dominated_penalty" in comps


def test_soft_bundle_v2_sector_bonus(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"sb2-sb-{uuid.uuid4().hex[:8]}", "Senator V2Sector")
    db.flush()
    committee = "V2 Sector Committee"
    donors = [
        ("sb1", "Sector A", "individual", "investment manager"),
        ("sb2", "Sector B", "individual", "mortgage broker"),
        ("sb3", "Sector C", "individual", "hedge fund analyst"),
        ("sb4", "Sector D", "individual", "public school teacher"),
    ]
    for i, (dk, name, dt, occ) in enumerate(donors):
        fd = f"2026-06-{1 + i:02d}"
        fe = _fec_receipt_entry(
            db,
            c.id,
            contributor_name=name,
            amount=400.0,
            receipt_date=fd,
            donor_type=dt,
            contributor_occupation=occ,
        )
        s = _signal_with_fec(
            db,
            c.id,
            name,
            "Senator V2Sector",
            fd,
            0.5,
            fe,
            total_amount=400.0,
            committee_label=committee,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator V2Sector", "S001198")
    db.commit()
    case_id_str = str(c.id)
    v2 = [
        a
        for a in run_pattern_engine(db)
        if a.rule_id == RULE_SOFT_BUNDLE_V2 and case_id_str in a.matched_case_ids
    ]
    db.close()
    assert v2
    diag = json.loads(v2[0].diagnostics_json or "{}")
    assert diag["sector_similarity"] >= 0.6
    comps = {x.get("component") for x in diag.get("adjustments", [])}
    assert "sector_bonus" in comps


def test_diagnostics_endpoint_returns_v2(client, test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _case(db, f"diag-v2-{uuid.uuid4().hex[:8]}", "Senator DiagV2")
    db.flush()
    committee = "Diag V2 Committee"
    for i, (dk, name) in enumerate(
        [
            ("d1", "Diag Person A"),
            ("d2", "Diag Person B"),
            ("d3", "Diag Person C"),
        ]
    ):
        fd = f"2026-07-{1 + i:02d}"
        fe = _fec_receipt_entry(
            db, c.id, contributor_name=name, amount=400.0, receipt_date=fd
        )
        s = _signal_with_fec(
            db,
            c.id,
            name,
            "Senator DiagV2",
            fd,
            0.5,
            fe,
            total_amount=400.0,
            committee_label=committee,
            donor_key=dk,
        )
        _fingerprint(db, dk, c.id, s.id, "Senator DiagV2", "S001198")
    db.commit()
    case_uuid = str(c.id)
    db.close()
    r = client.get(f"/api/v1/patterns/diagnostics?case_id={case_uuid}")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert any(a.get("rule_id") == RULE_SOFT_BUNDLE_V2 for a in data["alerts"])
    first = next(a for a in data["alerts"] if a.get("rule_id") == RULE_SOFT_BUNDLE_V2)
    assert first.get("diagnostics") is not None
    assert "final_weight" in (first["diagnostics"] or {})
    r2 = client.get(f"/api/v1/patterns?case_id={case_uuid}")
    assert r2.status_code == 200
    assert any(a.get("rule_id") == RULE_SOFT_BUNDLE_V2 for a in r2.json()["alerts"])


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
