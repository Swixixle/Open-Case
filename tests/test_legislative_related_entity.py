"""LEGISLATIVE_RELATED_ENTITY_DONOR_V1 and federal curated-alias infrastructure."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.pattern_engine import (
    CURRENT_CONGRESS_FOR_VOTE_CONTEXT,
    LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS,
    RULE_FAMILIES,
    RULE_LEGISLATIVE_RELATED_ENTITY_DONOR,
    _legislative_related_entity_amount_score,
    run_pattern_engine,
    vote_qualifies,
)
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, SubjectProfile
from utils.local_entity_matching import _load_local_aliases


def _seed_investigator(db) -> None:
    raw_key = generate_raw_key()
    db.add(
        Investigator(
            handle="leg_rel_tester",
            hashed_api_key=hash_key(raw_key),
            public_key="",
        )
    )
    db.commit()


def _fed_leg_case(db, slug: str) -> CaseFile:
    c = CaseFile(
        slug=slug,
        title=f"Case {slug}",
        subject_name="Senator Testrel",
        subject_type="senator",
        jurisdiction="US",
        status="open",
        created_by="leg_rel_tester",
        summary="",
        government_level="federal",
        branch="legislative",
    )
    db.add(c)
    db.flush()
    db.add(
        CaseContributor(
            case_file_id=c.id,
            investigator_handle="leg_rel_tester",
            role="field",
        )
    )
    return c


def _subject(db, case_id: uuid.UUID, bioguide: str) -> None:
    db.add(
        SubjectProfile(
            case_file_id=case_id,
            subject_name="Senator Testrel",
            subject_type="senator",
            bioguide_id=bioguide,
            government_level="federal",
            branch="legislative",
        )
    )


def _vote_roll(
    db,
    case_id: uuid.UUID,
    day: date,
    bioguide: str,
    *,
    question: str = "On Passage of S. 1",
) -> None:
    raw = {
        "congress": CURRENT_CONGRESS_FOR_VOTE_CONTEXT,
        "question": question,
        "bioguide_id": bioguide,
        "member_vote": "Yea",
        "vote_result": "Passed",
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="vote_record",
            title="Roll call",
            body="test",
            source_url="https://www.senate.gov/",
            entered_by="leg_rel_tester",
            confidence="confirmed",
            date_of_event=day,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _fec(db, case_id: uuid.UUID, contributor: str, amt: float, day: date) -> None:
    raw = {
        "contributor_name": contributor,
        "contribution_receipt_amount": amt,
        "contribution_receipt_date": day.isoformat(),
        "entity_type": "ORG",
    }
    db.add(
        EvidenceEntry(
            case_file_id=case_id,
            entry_type="financial_connection",
            title=f"FEC: {contributor}",
            body="test",
            source_url="https://www.fec.gov/",
            source_name="FEC",
            adapter_name="FEC",
            date_of_event=day,
            entered_by="leg_rel_tester",
            confidence="confirmed",
            amount=amt,
            raw_data_json=json.dumps(raw, separators=(",", ":")),
        )
    )


def _write_federal_aliases(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "federal_entity_aliases_test.json"
    p.write_text(
        json.dumps({"description": "test", "aliases": rows}, indent=2),
        encoding="utf-8",
    )
    return p


def test_legislative_related_entity_fires_with_qualifying_vote(
    test_engine, tmp_path, monkeypatch
) -> None:
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": "Acme Parent Corporation",
            "alias": "Acme Parent Corporation Employees PAC",
            "relationship_type": "pac_of_donor",
            "confidence": "high",
            "source_note": "test fixture",
            "public_explanation": "test",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )

    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999119"
    _subject(db, c.id, bg)
    vday = date(2026, 3, 10)
    dday = date(2026, 3, 12)
    _vote_roll(db, c.id, vday, bg)
    _fec(db, c.id, "Acme Parent Corporation Employees PAC", 2500.0, dday)
    db.commit()

    alerts = [a for a in run_pattern_engine(db) if a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR]
    db.close()
    assert len(alerts) == 1
    pe = alerts[0].payload_extra or {}
    assert pe.get("match_type") == "related_entity"
    assert pe.get("relationship_type") == "pac_of_donor"
    assert pe.get("indirect_match_label") == "PAC affiliated with donor"
    assert int(pe.get("days_contribution_to_vote") or -1) <= LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS


def test_legislative_related_entity_no_fire_curated_alias_match_only(
    test_engine, tmp_path, monkeypatch
) -> None:
    """Alias rows (non-related_entity) must not trigger LEGISLATIVE_RELATED_ENTITY_DONOR."""
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": "Zeta Logistics Inc",
            "alias": "Zeta Logistics Incorporated",
            "relationship_type": "alias",
            "confidence": "high",
            "source_note": "suffix",
            "public_explanation": "x",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-alias-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999666"
    _subject(db, c.id, bg)
    day = date(2026, 7, 1)
    _vote_roll(db, c.id, day, bg)
    _fec(db, c.id, "Zeta Logistics Incorporated", 900.0, day)
    db.commit()
    alerts = [a for a in run_pattern_engine(db) if a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR]
    db.close()
    assert alerts == []


def test_legislative_related_entity_no_fire_direct_same_entity(
    test_engine, tmp_path, monkeypatch
) -> None:
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": "Beta Holdings Inc",
            "alias": "Beta Holdings Inc PAC",
            "relationship_type": "pac_of_donor",
            "confidence": "high",
            "source_note": "test",
            "public_explanation": "test",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-d-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999222"
    _subject(db, c.id, bg)
    day = date(2026, 4, 1)
    _vote_roll(db, c.id, day, bg)
    _fec(db, c.id, "Beta Holdings Inc", 5000.0, day)
    db.commit()
    alerts = [a for a in run_pattern_engine(db) if a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR]
    db.close()
    assert alerts == []


def test_legislative_related_entity_no_fire_vote_outside_window(
    test_engine, tmp_path, monkeypatch
) -> None:
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": "Gamma Defense LLC",
            "alias": "Gamma Defense LLC Employees PAC",
            "relationship_type": "pac_of_donor",
            "confidence": "high",
            "source_note": "test",
            "public_explanation": "test",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-far-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999333"
    _subject(db, c.id, bg)
    _vote_roll(db, c.id, date(2025, 1, 1), bg)
    _fec(db, c.id, "Gamma Defense LLC Employees PAC", 1000.0, date(2026, 8, 1))
    db.commit()
    alerts = [a for a in run_pattern_engine(db) if a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR]
    db.close()
    assert alerts == []


def test_federal_indirect_match_label_affiliate(test_engine, tmp_path, monkeypatch) -> None:
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": "Delta Prime Industries",
            "alias": "Delta Prime Services LLC",
            "relationship_type": "affiliate",
            "confidence": "medium",
            "source_note": "test",
            "public_explanation": "test",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-aff-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999444"
    _subject(db, c.id, bg)
    vday = date(2026, 5, 1)
    _vote_roll(db, c.id, vday, bg)
    _fec(db, c.id, "Delta Prime Services LLC", 800.0, vday)
    db.commit()
    alerts = [a for a in run_pattern_engine(db) if a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR]
    db.close()
    assert len(alerts) == 1
    assert (alerts[0].payload_extra or {}).get("indirect_match_label") == "Corporate affiliate of donor"


def test_legislative_related_entity_payload_extra_required_fields(
    test_engine, tmp_path, monkeypatch
) -> None:
    u = uuid.uuid4().hex[:10]
    canon = f"Epsilon Rail Corp Fixture {u}"
    pac = f"Epsilon Rail Corp Fixture {u} Employees PAC"
    rows = [
        {
            "jurisdiction": "federal",
            "canonical_key": canon,
            "alias": pac,
            "relationship_type": "pac_of_donor",
            "confidence": "high",
            "source_note": "note",
            "public_explanation": "x",
            "reviewed_by": "test",
            "active": True,
        }
    ]
    monkeypatch.setenv(
        "OPEN_CASE_FEDERAL_ENTITY_ALIASES", str(_write_federal_aliases(tmp_path, rows))
    )
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    _seed_investigator(db)
    c = _fed_leg_case(db, f"legrel-pe-{uuid.uuid4().hex[:8]}")
    db.flush()
    bg = "S99999555"
    _subject(db, c.id, bg)
    vday = date(2026, 6, 1)
    _vote_roll(db, c.id, vday, bg)
    _fec(db, c.id, pac, 1200.0, vday)
    db.commit()
    a = next(
        x for x in run_pattern_engine(db) if x.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR
    )
    db.close()
    pe = a.payload_extra or {}
    required = {
        "match_type",
        "relationship_type",
        "relationship_source_note",
        "donor_canonical",
        "related_entity_canonical",
        "donor_name_raw",
        "related_entity_name_raw",
        "contribution_amount",
        "contribution_date",
        "adjacent_vote_id",
        "adjacent_vote_date",
        "days_contribution_to_vote",
        "indirect_match_label",
        "epistemic_basis",
        "evidence_refs",
        "score_components",
    }
    assert required <= set(pe.keys())


def test_legislative_score_lower_band_than_soft_bundle_cap() -> None:
    """LEGISLATIVE_RELATED_ENTITY band caps at 0.75; SOFT_BUNDLE_V1 can reach 1.0 in-product."""
    leg_hi = _legislative_related_entity_amount_score(500_000.0)
    assert leg_hi <= 0.75
    assert leg_hi < 1.0


def test_vote_qualifies_excludes_voice_vote_and_missing_bioguide() -> None:
    bg = "S77777001"
    assert vote_qualifies(
        {
            "congress": 119,
            "question": "On voice vote, the amendment was agreed to",
            "bioguide_id": bg,
        },
        bg,
    ) is False
    assert vote_qualifies({"congress": 119, "question": "On Passage"}, bg) is False


def test_federal_entity_aliases_loads_for_jurisdiction_federal() -> None:
    rows = _load_local_aliases("federal")
    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert all(str(r.get("jurisdiction") or "").lower() == "federal" for r in rows)


def test_rule_family_includes_legislative_related_entity() -> None:
    assert RULE_LEGISLATIVE_RELATED_ENTITY_DONOR in RULE_FAMILIES["related_entity_influence"]["members"]
