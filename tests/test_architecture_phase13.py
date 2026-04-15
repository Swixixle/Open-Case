from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import generate_raw_key, hash_key
from core.subject_taxonomy import (
    APPOINTED_AND_DECISION_MAKER_SUBJECT_TYPES,
    SUBJECT_TYPES,
    subject_type_is_judicial,
)
from engines.pattern_engine import PatternAlert, pattern_alerts_for_case
from models import Base, CaseFile, EvidenceEntry, Investigator, SubjectProfile
from payloads import epistemic_distribution_from_entries
from services.epistemic_classifier import (
    ALLEGED,
    CONTEXTUAL,
    REPORTED,
    VERIFIED,
    aggregate_epistemic_levels,
    classify_epistemic_level,
)
from services.research_profile import ResearchProfile, load_subject_type_sources


@pytest.fixture
def ph13_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


def test_epistemic_classifier_known_urls() -> None:
    assert classify_epistemic_level(source_url="https://www.fec.gov/data/") == VERIFIED
    assert (
        classify_epistemic_level(
            source_url="https://gis.indy.gov/server/rest/services/OpenData/OpenData_NonSpatial/MapServer/9"
        )
        == VERIFIED
    )
    assert classify_epistemic_level(source_url="https://www.nytimes.com/2024/x") == REPORTED
    assert (
        classify_epistemic_level(source_url="https://en.wikipedia.org/wiki/X") == CONTEXTUAL
    )
    assert (
        classify_epistemic_level(body="plaintiff_claim allegation in civil complaint")
        == ALLEGED
    )
    assert classify_epistemic_level(source_url="https://unknown.example/foo") == REPORTED


def test_aggregate_epistemic_levels_weakest() -> None:
    assert aggregate_epistemic_levels([VERIFIED, CONTEXTUAL]) == CONTEXTUAL


def test_subject_taxonomy_registry_has_all_types() -> None:
    reg = load_subject_type_sources()
    person_types = SUBJECT_TYPES - {"corporation", "organization"}
    for st in person_types:
        assert st in reg, f"missing registry entry for {st}"


def test_research_profile_adapter_order_examples() -> None:
    def prof(st: str) -> SubjectProfile:
        return SubjectProfile(
            case_file_id=uuid.uuid4(),
            subject_name="X",
            subject_type=st,
            government_level="federal",
            branch="legislative",
            historical_depth="career",
        )

    sen = ResearchProfile(prof("senator")).get_adapters()
    assert sen[0] == "fec"
    assert "congress" in sen

    fj = ResearchProfile(prof("federal_judge_district")).get_adapters()
    assert "fjc_biographical" in fj
    assert "courtlistener" in fj

    my = ResearchProfile(prof("mayor")).get_adapters()
    assert "local_campaign_finance" in my

    sh = ResearchProfile(prof("county_sheriff")).get_adapters()
    assert "use_of_force_records" in sh or "local_campaign_finance" in sh


def test_appointed_decision_maker_subject_types_resolve_adapters() -> None:
    reg = load_subject_type_sources()

    def prof(st: str) -> SubjectProfile:
        return SubjectProfile(
            case_file_id=uuid.uuid4(),
            subject_name="X",
            subject_type=st,
            government_level="local",
            branch="administrative",
            historical_depth="career",
        )

    for st in sorted(APPOINTED_AND_DECISION_MAKER_SUBJECT_TYPES):
        assert st in SUBJECT_TYPES
        assert st in reg, f"missing registry entry for {st}"
        adapters = ResearchProfile(prof(st)).get_adapters()
        assert adapters, f"no adapters for {st}"
        assert all(
            reg[st].get(k) is not None for k in ("primary", "secondary", "judicial", "local", "historical")
        )


def test_research_profile_warns_and_falls_back(caplog) -> None:
    import logging

    caplog.set_level(logging.WARNING)
    p = SubjectProfile(
        case_file_id=uuid.uuid4(),
        subject_name="Y",
        subject_type="not_a_real_type",
        government_level="federal",
        branch="legislative",
        historical_depth="career",
    )
    ResearchProfile(p).get_adapters()
    assert "not in source registry" in caplog.text


def test_pattern_alerts_respect_human_review_gate(ph13_engine) -> None:
    cid = uuid.uuid4()
    fired = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    alert = PatternAlert(
        rule_id="T",
        pattern_version="1",
        donor_entity="D",
        matched_officials=[],
        matched_case_ids=[str(cid)],
        committee="",
        window_days=None,
        evidence_refs=[],
        fired_at=fired,
        epistemic_level=ALLEGED,
        requires_human_review=True,
    )
    rows = pattern_alerts_for_case(cid, [alert], include_unreviewed=False)
    assert rows == []
    rows_admin = pattern_alerts_for_case(cid, [alert], include_unreviewed=True)
    assert len(rows_admin) == 1


def test_epistemic_distribution_payload() -> None:
    e1 = EvidenceEntry(
        case_file_id=uuid.uuid4(),
        entry_type="financial_connection",
        title="t",
        body="b",
        source_name="FEC",
        source_url="https://fec.gov/x",
        entered_by="u",
        confidence="high",
        epistemic_level=VERIFIED,
    )
    e2 = EvidenceEntry(
        case_file_id=uuid.uuid4(),
        entry_type="financial_connection",
        title="t2",
        body="b2",
        source_name="x",
        source_url="https://twitter.com/x",
        entered_by="u",
        confidence="low",
        epistemic_level=CONTEXTUAL,
    )
    d = epistemic_distribution_from_entries([e1, e2])
    assert d[VERIFIED] == 1
    assert d[CONTEXTUAL] == 1


def test_subject_type_is_judicial() -> None:
    assert subject_type_is_judicial("federal_judge_district")
    assert not subject_type_is_judicial("mayor")


def test_classify_epistemic_script_dry_run(tmp_path) -> None:
    """Script must run against a DB with current models (not the dev Postgres URL)."""
    dbfile = tmp_path / "classify_script.db"
    url = f"sqlite:///{dbfile}"
    eng = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    eng.dispose()
    env = {**os.environ, "DATABASE_URL": url}
    r = subprocess.run(
        [sys.executable, "scripts/classify_epistemic_levels.py", "--dry-run"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "Evidence epistemic counts" in r.stdout


def test_report_section_filter_and_unreviewed_gate(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    raw_key = generate_raw_key()
    handle = "tsec"
    db = database.SessionLocal()
    try:
        inv = Investigator(handle=handle, hashed_api_key=hash_key(raw_key), public_key="")
        db.add(inv)
        case = CaseFile(
            slug=f"sec-{uuid.uuid4().hex[:10]}",
            title="Judge X",
            subject_name="Judge X",
            subject_type="federal_judge_district",
            jurisdiction="S.D. Indiana",
            status="open",
            created_by=handle,
            summary="",
            government_level="federal",
            branch="judicial",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name=case.subject_name,
                subject_type=case.subject_type,
                government_level="federal",
                branch="judicial",
                historical_depth="career",
            )
        )
        pub = EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title="FEC",
            body="donation",
            source_name="FEC",
            source_url="https://www.fec.gov/x",
            entered_by=handle,
            confidence="high",
            epistemic_level=VERIFIED,
            requires_human_review=False,
        )
        hid = EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title="Allegation",
            body="formal complaint filed against judge",
            source_name="News",
            source_url="https://example.test/a",
            entered_by=handle,
            confidence="low",
            epistemic_level=ALLEGED,
            requires_human_review=True,
        )
        db.add(pub)
        db.add(hid)
        db.commit()
        cid = str(case.id)
        pub_id = str(pub.id)
        hid_id = str(hid.id)
    finally:
        db.close()

    with patch.object(main, "init_db", lambda: None):
        with patch.dict("os.environ", {"ADMIN_SECRET": "sec123"}):
            hdr = {"Authorization": f"Bearer {raw_key}"}
            r0 = client.get(f"/api/v1/cases/{cid}/report")
            assert r0.status_code == 200
            fin = r0.json()["financial_connections"]
            ids = {x["id"] for x in fin}
            assert pub_id in ids
            assert hid_id not in ids

            r_ad = client.get(
                f"/api/v1/cases/{cid}/report?include_unreviewed=true",
                headers={**hdr, "X-Admin-Secret": "sec123"},
            )
            assert r_ad.status_code == 200
            ids_ad = {x["id"] for x in r_ad.json()["financial_connections"]}
            assert hid_id in ids_ad

            r_bad = client.get(
                f"/api/v1/cases/{cid}/report?include_unreviewed=true",
                headers=hdr,
            )
            assert r_bad.status_code == 403

            rs = client.get(f"/api/v1/cases/{cid}/report?section=money")
            assert rs.status_code == 200
            body = rs.json()
            assert body.get("section_filter") == "money"
            assert body["sections"]["money"]
            assert body["sections"]["identity"] == []


def test_list_cases_filters(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    db = database.SessionLocal()
    try:
        case = CaseFile(
            slug=f"pf-{uuid.uuid4().hex[:10]}",
            title="Pilot",
            subject_name="P",
            subject_type="mayor",
            jurisdiction="Indy",
            status="open",
            created_by="x",
            summary="",
            pilot_cohort="indianapolis",
            government_level="local",
            branch="executive",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name="P",
                subject_type="mayor",
                government_level="local",
                branch="executive",
                historical_depth="career",
            )
        )
        db.commit()
        pilot = case.pilot_cohort
    finally:
        db.close()

    with patch.object(main, "init_db", lambda: None):
        r = client.get("/api/v1/cases?pilot=indianapolis&subject_type=mayor")
        assert r.status_code == 200
        assert r.json()["count"] >= 1
        r2 = client.get("/api/v1/cases?government_level=local&branch=executive")
        assert r2.status_code == 200
        assert any(
            row.get("pilot_cohort") == pilot for row in r2.json()["cases"]
        )


def test_subjects_search_database_branch(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    db = database.SessionLocal()
    try:
        case = CaseFile(
            slug=f"sd-{uuid.uuid4().hex[:10]}",
            title="UniqueSearchNameXYZ",
            subject_name="UniqueSearchNameXYZ",
            subject_type="mayor",
            jurisdiction="Indy",
            status="open",
            created_by="x",
            summary="",
        )
        db.add(case)
        db.flush()
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name="UniqueSearchNameXYZ",
                subject_type="mayor",
                government_level="local",
                branch="executive",
                historical_depth="career",
            )
        )
        db.commit()
    finally:
        db.close()

    with patch.object(main, "init_db", lambda: None):
        r = client.get(
            "/api/v1/subjects/search",
            params={
                "name": "UniqueSearchNameXYZ",
                "subject_type": "mayor",
                "government_level": "local",
                "branch": "executive",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "database"
        assert len(data["database_matches"]) >= 1
        assert "match_score" in data["database_matches"][0]
        assert data["database_matches"][0]["match_score"] >= 0.99
        assert "results" in data
        assert len(data["results"]) >= 1
        assert data["results"][0]["match_score"] >= 0.99


def test_methodology_endpoint(client, test_engine) -> None:
    import database
    import main

    database.engine = test_engine
    database.SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    with patch.object(main, "init_db", lambda: None):
        r = client.get("/api/v1/methodology")
        assert r.status_code == 200
        j = r.json()
        assert "legal_liability_note" in j
        assert "judicial officers" in j["legal_liability_note"]


def test_evidence_defaults_reported(ph13_engine) -> None:
    Session = sessionmaker(bind=ph13_engine)
    db = Session()
    try:
        case = CaseFile(
            slug="def-ep",
            title="C",
            subject_name="S",
            subject_type="public_official",
            jurisdiction="US",
            status="open",
            created_by="u",
            summary="",
        )
        db.add(case)
        db.flush()
        e = EvidenceEntry(
            case_file_id=case.id,
            entry_type="financial_connection",
            title="t",
            body="b",
            source_name="FEC",
            source_url="",
            entered_by="u",
            confidence="high",
        )
        db.add(e)
        db.commit()
        row = db.scalar(select(EvidenceEntry).where(EvidenceEntry.id == e.id))
        assert (row.epistemic_level or "").strip() == REPORTED
    finally:
        db.close()


def test_core_required_adapters_empty_for_judicial() -> None:
    from types import SimpleNamespace

    from routes.investigate import _temporal_core_required_adapters

    case = SimpleNamespace(subject_type="federal_judge_district")
    assert _temporal_core_required_adapters(case) == []
