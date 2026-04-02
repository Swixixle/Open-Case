"""Phase 9B — pattern engine and patterns API (cross-official detection)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.pattern_engine import (
    COMMITTEE_SWEEP_MAX_WINDOW_DAYS,
    FINGERPRINT_BLOOM_MIN_RELEVANCE,
    RULE_COMMITTEE_SWEEP,
    RULE_FINGERPRINT_BLOOM,
    run_pattern_engine,
)
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
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
) -> Signal:
    ident = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
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
        event_date_a=fin_date,
        event_date_b="2025-06-01",
        days_between=-5,
        relevance_score=relevance,
        weight_breakdown=_breakdown_json(donor, official, relevance_score=relevance),
    )
    db.add(s)
    db.flush()
    return s


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
