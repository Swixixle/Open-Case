"""Phase 10A — evidence tiers, report journalist copy, temporal counts."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

from sqlalchemy.orm import sessionmaker

from auth import generate_raw_key, hash_key
from engines.signal_scorer import evidence_tier_from_checks
from models import (
    CaseContributor,
    CaseFile,
    Investigator,
    Signal,
)
from routes.investigate import _signal_to_response_dict, temporal_signal_counts


def test_evidence_tier_documented_zero_indicators() -> None:
    assert evidence_tier_from_checks({"relevance_indicator_count": 0}) == "Documented"
    assert evidence_tier_from_checks({}) == "Documented"


def test_evidence_tier_corroborated_one_indicator() -> None:
    assert evidence_tier_from_checks({"relevance_indicator_count": 1}) == "Corroborated"


def test_evidence_tier_multi_source_two_indicators() -> None:
    assert evidence_tier_from_checks({"relevance_indicator_count": 2}) == "Multi-source"
    assert evidence_tier_from_checks({"relevance_indicator_count": 5}) == "Multi-source"


def test_signal_json_includes_evidence_tier(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    case = CaseFile(
        slug=f"tier-{uuid.uuid4().hex[:8]}",
        title="t",
        subject_name="S",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by="x",
        summary="",
    )
    db.add(case)
    db.flush()
    sig = Signal(
        case_file_id=case.id,
        signal_identity_hash="b" * 64,
        signal_type="temporal_proximity",
        weight=0.5,
        description="x",
        evidence_ids="[]",
        exposure_state="internal",
        confirmation_checks=json.dumps(
            {"relevance_indicator_count": 1, "jurisdictional_match": True}
        ),
        weight_breakdown=json.dumps({"kind": "donor_cluster", "donor": "ACME", "official": "Sen X"}),
    )
    db.add(sig)
    db.commit()
    out = _signal_to_response_dict(sig)
    assert out.get("evidence_tier") == "Corroborated"
    db.close()


def test_anticipatory_count_in_investigate_response() -> None:
    a = MagicMock()
    a.exposure_state = "internal"
    a.signal_type = "temporal_proximity"
    a.temporal_class = "anticipatory"
    b = MagicMock()
    b.exposure_state = "internal"
    b.signal_type = "temporal_proximity"
    b.temporal_class = "retrospective"
    anti, retro = temporal_signal_counts([a, b])
    assert anti == 1 and retro == 1


def test_confirmed_field_absent_from_report_view(client, test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    raw_key = generate_raw_key()
    handle = "rep_view_tester"
    db.add(
        Investigator(
            handle=handle,
            hashed_api_key=hash_key(raw_key),
            public_key="",
        )
    )
    case = CaseFile(
        slug=f"rpt-{uuid.uuid4().hex[:10]}",
        title="Report view case",
        subject_name="Test Official",
        subject_type="public_official",
        jurisdiction="US",
        status="open",
        created_by=handle,
        summary="Summary line",
    )
    db.add(case)
    db.flush()
    db.add(
        CaseContributor(case_file_id=case.id, investigator_handle=handle, role="field")
    )
    db.add(
        Signal(
            case_file_id=case.id,
            signal_identity_hash="c" * 64,
            signal_type="temporal_proximity",
            weight=0.8,
            description="Proximity test",
            evidence_ids="[]",
            exposure_state="internal",
            temporal_class="retrospective",
            days_between=-13,
            confirmation_checks=json.dumps({"relevance_indicator_count": 0}),
            weight_breakdown=json.dumps(
                {
                    "kind": "donor_cluster",
                    "donor": "DONOR LLC",
                    "official": "Test Official",
                    "total_amount": 1000,
                    "min_gap_days": 13,
                    "exemplar_vote": "S.1",
                    "exemplar_direction": "after",
                    "exemplar_position": "Yea",
                }
            ),
        )
    )
    db.commit()
    cid = str(case.id)
    db.close()

    r = client.get(f"/api/v1/cases/{cid}/report/view")
    assert r.status_code == 200
    text = r.text.lower()
    assert "confirmed: 0" not in text
    assert "confirmed: false" not in text
