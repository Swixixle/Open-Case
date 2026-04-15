"""Local-government pattern rules (IDIS + INDY_PROCUREMENT / INDY_GATEWAY_CONTRACT_DOC)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from adapters.indianapolis_procurement import normalize_vendor_name
from engines.entity_resolution import canonicalize, resolve
from engines.pattern_engine import (
    LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS,
    PATTERN_RULE_IDS,
    RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
    RULE_LOCAL_CONTRACT_DONATION_TIMING,
    RULE_LOCAL_RELATED_ENTITY_DONOR,
    RULE_LOCAL_VENDOR_CONCENTRATION,
    _local_loop_score,
    _local_related_entity_score,
    pattern_alert_to_report_dict,
    run_pattern_engine,
)
from utils.local_entity_matching import (
    MATCH_ALIAS,
    MATCH_DIRECT,
    MATCH_NONE,
    MATCH_RELATED_ENTITY,
    _local_match_type,
    local_jurisdiction_alias_key,
    local_match_eligible_for_loop_and_timing,
)
from models import Base, CaseFile, EvidenceEntry


@pytest.fixture
def db_session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def test_pattern_rule_ids_includes_local_rules() -> None:
    assert RULE_LOCAL_CONTRACTOR_DONOR_LOOP in PATTERN_RULE_IDS
    assert RULE_LOCAL_RELATED_ENTITY_DONOR in PATTERN_RULE_IDS
    assert RULE_LOCAL_CONTRACT_DONATION_TIMING in PATTERN_RULE_IDS
    assert RULE_LOCAL_VENDOR_CONCENTRATION in PATTERN_RULE_IDS


def test_normalize_vendor_name_strips_suffix() -> None:
    assert "ACME PLUMBING" == normalize_vendor_name("Acme Plumbing, LLC")


def test_local_loop_and_timing_and_concentration(db_session) -> None:
    case = CaseFile(
        slug="local-pat-test",
        title="Mayor Test",
        subject_name="Mayor Test",
        subject_type="official",
        jurisdiction="Test City, TS",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()

    shared_raw = "Acme Plumbing LLC"
    shared_can = normalize_vendor_name(shared_raw)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="Contract: Acme",
        body="x",
        source_url="https://example.gov/c1",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100_000.0,
        matched_name=shared_raw,
        date_of_event=date(2020, 6, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": shared_raw,
                "vendor_canonical": shared_can,
                "department": "DPW",
                "contract_id": "C-1",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="IDIS row",
        body="y",
        source_url="https://campaignfinance.in.gov/x",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=500.0,
        date_of_event=date(2020, 6, 15),
        raw_data_json=json.dumps({"contributor_name": shared_raw}, sort_keys=True),
    )
    db_session.add_all([vend, don])

    v2_raw = "Beta Construction Inc"
    v2_can = normalize_vendor_name(v2_raw)
    v2 = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="Contract: Beta",
        body="b",
        source_url="https://example.gov/c2",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Beta Construction-2018",
        date_of_event=date(2019, 1, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": v2_raw,
                "vendor_canonical": v2_can,
                "contract_id": "C-2",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    d2 = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="id2",
        body="b",
        source_url="https://campaignfinance.in.gov/y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=2_000.0,
        date_of_event=date(2019, 2, 1),
        raw_data_json=json.dumps({"contributor_name": "Beta Construction Inc"}, sort_keys=True),
    )
    d3 = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="id3",
        body="c",
        source_url="https://campaignfinance.in.gov/z",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=3_000.0,
        date_of_event=date(2019, 3, 1),
        raw_data_json=json.dumps({"contributor_name": "Gamma LLC"}, sort_keys=True),
    )
    db_session.add_all([v2, d2, d3])
    db_session.commit()

    alerts = run_pattern_engine(db_session)

    loops = [a for a in alerts if a.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP]
    assert loops
    assert any(
        str(vend.id) in a.evidence_refs and str(don.id) in a.evidence_refs for a in loops
    )
    assert loops[0].suspicion_score is not None and loops[0].suspicion_score > 0

    timings = [a for a in alerts if a.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING]
    assert timings
    timing_acme = next(
        a
        for a in timings
        if str(vend.id) in a.evidence_refs and str(don.id) in a.evidence_refs
    )
    assert timing_acme.payload_extra and timing_acme.payload_extra.get(
        "timing_direction"
    ) in ("pre_award", "post_award")
    loop_acme = next(
        a
        for a in loops
        if str(vend.id) in a.evidence_refs and str(don.id) in a.evidence_refs
    )
    assert timing_acme.suspicion_score is not None and timing_acme.suspicion_score >= (
        loop_acme.suspicion_score or 0
    )

    concs = [a for a in alerts if a.rule_id == RULE_LOCAL_VENDOR_CONCENTRATION]
    assert concs
    conc = concs[0]
    assert conc.cluster_size is not None and conc.cluster_size >= 2
    assert conc.payload_extra and conc.payload_extra.get("overlap_count", 0) >= 2


_REF_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"
_TEST_ALIASES = _REF_DIR / "local_entity_aliases.test.json"


def test_local_match_type_direct_and_alias_and_affiliate(db_session, monkeypatch) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    j = "testville"
    d, _, _ = _local_match_type("Acme Plumbing Services", "Acme Plumbing Services", j, db_session)
    assert d == MATCH_DIRECT
    a, rt, _ = _local_match_type(
        "ACME PLUMBING SERVICES", "Acme Plumbing LLC", j, db_session
    )
    assert a == MATCH_ALIAS and rt == "alias"
    rel, rt2, _ = _local_match_type(
        "OMEGA HOLDINGS", "Omega Construction Inc", j, db_session
    )
    assert rel == MATCH_RELATED_ENTITY and rt2 == "affiliate"
    pac, rt3, _ = _local_match_type(
        "GAMMA CONSTRUCTION", "Gamma Construction Indiana PAC", j, db_session
    )
    assert pac == MATCH_RELATED_ENTITY and rt3 == "pac_of_vendor"
    ok, mt, _, _ = local_match_eligible_for_loop_and_timing(
        "OMEGA HOLDINGS", "Omega Construction Inc", j, db_session
    )
    assert not ok and mt == MATCH_RELATED_ENTITY


def test_curated_alias_enables_loop(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = CaseFile(
        slug="alias-loop",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Acme Plumbing Services LLC",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Acme Plumbing Services LLC",
                "vendor_canonical": "ACME PLUMBING SERVICES",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 2, 1),
        raw_data_json=json.dumps({"contributor_name": "Acme Plumbing LLC"}, sort_keys=True),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    loops = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP]
    assert loops
    pe = loops[0].payload_extra or {}
    assert pe.get("match_type") == MATCH_ALIAS


def test_affiliate_does_not_enable_loop(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = CaseFile(
        slug="aff-no-loop",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Omega Holdings",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Omega Holdings",
                "vendor_canonical": "OMEGA HOLDINGS",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 2, 1),
        raw_data_json=json.dumps({"contributor_name": "Omega Construction Inc"}, sort_keys=True),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    loops = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP]
    assert not loops


def test_timing_ignores_final_acceptance(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = CaseFile(
        slug="time-fa",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Acme Plumbing Services LLC",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Acme Plumbing Services LLC",
                "vendor_canonical": "ACME PLUMBING SERVICES",
                "contract_event_type": "final_acceptance",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 1, 15),
        raw_data_json=json.dumps({"contributor_name": "Acme Plumbing LLC"}, sort_keys=True),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    timings = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING]
    assert not timings


def test_spotcheck1_alias_driven_payload_extra_fully_auditable_loop_and_timing(
    monkeypatch, db_session
) -> None:
    """Spot-check 1: curated alias is the match reason; payload_extra carries full audit fields."""
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    v_label = "ACME PLUMBING SERVICES"
    d_label = "Acme Plumbing LLC"
    assert canonicalize(v_label) != canonicalize(d_label)
    assert resolve(v_label, db_session).canonical_id != resolve(d_label, db_session).canonical_id

    case = CaseFile(
        slug="spotcheck-alias-audit",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Acme Plumbing Services LLC",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Acme Plumbing Services LLC",
                "vendor_canonical": "ACME PLUMBING SERVICES",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 1, 20),
        raw_data_json=json.dumps(
            {
                "contributor_name": "Acme Plumbing LLC",
                "contributor_name_raw": "Acme Plumbing LLC",
                "contributor_canonical": "ACME PLUMBING",
            },
            sort_keys=True,
        ),
    )
    db_session.add_all([vend, don])
    db_session.commit()

    alerts = run_pattern_engine(db_session)
    loops = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP]
    timings = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING]
    assert len(loops) == 1
    assert len(timings) == 1

    pe_loop = loops[0].payload_extra or {}
    assert pe_loop.get("match_type") == MATCH_ALIAS
    assert pe_loop.get("relationship_type") == "alias"
    assert pe_loop.get("relationship_source_note")
    assert pe_loop.get("vendor_canonical") == "ACME PLUMBING SERVICES"
    assert pe_loop.get("donor_canonical") == "ACME PLUMBING"
    assert pe_loop.get("vendor_name_raw") == "Acme Plumbing Services LLC"
    assert pe_loop.get("donor_name_raw") == "Acme Plumbing LLC"

    pe_time = timings[0].payload_extra or {}
    assert pe_time.get("match_type") == MATCH_ALIAS
    assert pe_time.get("relationship_type") == "alias"
    assert pe_time.get("relationship_source_note")
    assert pe_time.get("vendor_canonical") == "ACME PLUMBING SERVICES"
    assert pe_time.get("donor_canonical") == "ACME PLUMBING"
    assert pe_time.get("vendor_name_raw") == "Acme Plumbing Services LLC"
    assert pe_time.get("donor_name_raw") == "Acme Plumbing LLC"
    assert pe_time.get("award_date") == "2024-01-10"
    assert pe_time.get("donation_date") == "2024-01-20"
    assert pe_time.get("days_donation_minus_award") == 10
    assert pe_time.get("timing_direction") == "post_award"


def test_local_loop_allows_final_acceptance_but_timing_does_not(monkeypatch, db_session) -> None:
    """Spot-check 2: loop may use final_acceptance; timing must be award-only."""
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = CaseFile(
        slug="spotcheck-fa-loop",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Acme Plumbing Services LLC",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Acme Plumbing Services LLC",
                "vendor_canonical": "ACME PLUMBING SERVICES",
                "contract_event_type": "final_acceptance",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 1, 15),
        raw_data_json=json.dumps(
            {
                "contributor_name": "Acme Plumbing LLC",
                "contributor_name_raw": "Acme Plumbing LLC",
                "contributor_canonical": "ACME PLUMBING",
            },
            sort_keys=True,
        ),
    )
    db_session.add_all([vend, don])
    db_session.commit()

    alerts = run_pattern_engine(db_session)
    vid, did = str(vend.id), str(don.id)
    loops = [
        x
        for x in alerts
        if x.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP
        and vid in x.evidence_refs
        and did in x.evidence_refs
    ]
    timings = [
        x
        for x in alerts
        if x.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING
        and vid in x.evidence_refs
        and did in x.evidence_refs
    ]
    assert len(loops) == 1
    assert len(timings) == 0

    pe = loops[0].payload_extra or {}
    assert pe.get("contract_event_type") == "final_acceptance"
    assert pe.get("event_type_used") == "final_acceptance"
    assert pe.get("event_type_warning")
    assert "not a primary award" in (pe.get("event_type_warning") or "").lower()


def _case_testville(db_session) -> CaseFile:
    case = CaseFile(
        slug=f"tv-{uuid4().hex[:8]}",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    return case


def test_related_entity_donor_fires_pac_and_not_loop(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = _case_testville(db_session)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=1_000_000.0,
        matched_name="Gamma Construction",
        date_of_event=date(2024, 3, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Gamma Construction",
                "vendor_canonical": "GAMMA CONSTRUCTION",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=500.0,
        date_of_event=date(2024, 3, 15),
        raw_data_json=json.dumps(
            {
                "contributor_name": "Gamma Construction Indiana PAC",
                "contributor_name_raw": "Gamma Construction Indiana PAC",
                "contributor_canonical": "GAMMA CONSTRUCTION INDIANA",
            },
            sort_keys=True,
        ),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    vid, did = str(vend.id), str(don.id)
    alerts = run_pattern_engine(db_session)
    related = [
        a
        for a in alerts
        if a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR
        and vid in a.evidence_refs
        and did in a.evidence_refs
    ]
    loops = [
        a
        for a in alerts
        if a.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP
        and vid in a.evidence_refs
        and did in a.evidence_refs
    ]
    assert len(related) == 1
    assert not loops
    pe = related[0].payload_extra or {}
    assert pe.get("match_type") == MATCH_RELATED_ENTITY
    assert pe.get("relationship_type") == "pac_of_vendor"
    assert pe.get("indirect_match_label") == "PAC affiliated with vendor"


def test_related_entity_donor_affiliate_indirect_label(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = _case_testville(db_session)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=800_000.0,
        matched_name="Omega Holdings",
        date_of_event=date(2024, 4, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Omega Holdings",
                "vendor_canonical": "OMEGA HOLDINGS",
                "contract_event_type": "supply_purchase",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=250.0,
        date_of_event=date(2024, 4, 10),
        raw_data_json=json.dumps(
            {
                "contributor_name": "Omega Construction Inc",
                "contributor_name_raw": "Omega Construction Inc",
                "contributor_canonical": "OMEGA CONSTRUCTION",
            },
            sort_keys=True,
        ),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    rel = next(a for a in alerts if a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR)
    pe = rel.payload_extra or {}
    assert pe.get("relationship_type") == "affiliate"
    assert pe.get("indirect_match_label") == "Corporate affiliate of vendor"
    assert pe.get("contract_event_type") == "supply_purchase"


def test_related_entity_donor_suppressed_on_final_acceptance(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = _case_testville(db_session)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=1_000_000.0,
        matched_name="Gamma Construction",
        date_of_event=date(2024, 3, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Gamma Construction",
                "vendor_canonical": "GAMMA CONSTRUCTION",
                "contract_event_type": "final_acceptance",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=500.0,
        date_of_event=date(2024, 3, 15),
        raw_data_json=json.dumps({"contributor_name": "Gamma Construction Indiana PAC"}),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    assert not [a for a in alerts if a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR]


def test_related_entity_score_lower_than_direct_loop_same_amounts() -> None:
    c_amt, d_amt = 500_000.0, 5_000.0
    assert _local_related_entity_score(c_amt, d_amt) < _local_loop_score(c_amt, d_amt)


def test_related_entity_donor_payload_extra_complete(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = _case_testville(db_session)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100_000.0,
        matched_name="Gamma Construction",
        date_of_event=date(2024, 5, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Gamma Construction LLC",
                "vendor_canonical": "GAMMA CONSTRUCTION",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 5, 2),
        raw_data_json=json.dumps(
            {
                "contributor_name": "Gamma Construction Indiana PAC",
                "contributor_name_raw": "Gamma Construction Indiana PAC",
                "contributor_canonical": "GAMMA CONSTRUCTION INDIANA",
            },
            sort_keys=True,
        ),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    a = next(x for x in alerts if x.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR)
    pe = a.payload_extra or {}
    for key in (
        "match_type",
        "relationship_type",
        "relationship_source_note",
        "vendor_canonical",
        "donor_canonical",
        "vendor_name_raw",
        "donor_name_raw",
        "contract_event_type",
        "contract_amount",
        "donation_amount",
        "indirect_match_label",
    ):
        assert key in pe
    assert pe["match_type"] == MATCH_RELATED_ENTITY
    assert pe["indirect_match_label"] == "PAC affiliated with vendor"
    row = pattern_alert_to_report_dict(a)
    assert row["badge"] == "Related entity donor"
    assert "not a direct same-entity" in (row.get("rule_line") or "").lower()


def test_related_entity_skipped_when_contract_event_type_missing(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = _case_testville(db_session)
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Gamma Construction",
        date_of_event=date(2024, 6, 1),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Gamma Construction",
                "vendor_canonical": "GAMMA CONSTRUCTION",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 6, 2),
        raw_data_json=json.dumps({"contributor_name": "Gamma Construction Indiana PAC"}),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    assert not [a for a in alerts if a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR]
    assert LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS["skipped_missing_contract_event_type"] >= 1


def test_timing_fires_on_award_with_alias(monkeypatch, db_session) -> None:
    monkeypatch.setenv("OPEN_CASE_LOCAL_ENTITY_ALIASES", str(_TEST_ALIASES))
    case = CaseFile(
        slug="time-aw",
        title="t",
        subject_name="Mayor T",
        subject_type="official",
        jurisdiction="Testville, TV",
        status="open",
        created_by="t",
        summary="",
        government_level="local",
        branch="executive",
    )
    db_session.add(case)
    db_session.flush()
    vend = EvidenceEntry(
        case_file_id=case.id,
        entry_type="government_record",
        title="c",
        body="b",
        source_url="https://x",
        source_name="INDY_PROCUREMENT",
        adapter_name="INDY_PROCUREMENT",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=50_000.0,
        matched_name="Acme Plumbing Services LLC",
        date_of_event=date(2024, 1, 10),
        raw_data_json=json.dumps(
            {
                "vendor_name_raw": "Acme Plumbing Services LLC",
                "vendor_canonical": "ACME PLUMBING SERVICES",
                "contract_event_type": "award",
            },
            sort_keys=True,
        ),
    )
    don = EvidenceEntry(
        case_file_id=case.id,
        entry_type="financial_connection",
        title="d",
        body="b",
        source_url="https://y",
        source_name="IDIS",
        adapter_name="IDIS",
        entered_by="t",
        confidence="confirmed",
        is_absence=False,
        amount=100.0,
        date_of_event=date(2024, 1, 20),
        raw_data_json=json.dumps({"contributor_name": "Acme Plumbing LLC"}, sort_keys=True),
    )
    db_session.add_all([vend, don])
    db_session.commit()
    alerts = run_pattern_engine(db_session)
    timings = [x for x in alerts if x.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING]
    assert timings
    pe = timings[0].payload_extra or {}
    assert pe.get("contract_event_type") == "award"
    assert pe.get("match_type") == MATCH_ALIAS


def test_local_jurisdiction_alias_key_maps_testville() -> None:
    assert local_jurisdiction_alias_key("Testville, TV") == "testville"
