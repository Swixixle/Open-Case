"""Signal upsert / deduplication behavior."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from models import CaseContributor, CaseFile, Investigator, Signal
from signals.dedup import upsert_signal


def test_upsert_updates_weight_breakdown_on_repeat_without_weight_increase(
    test_engine,
) -> None:
    """Regression: repeat investigate must merge latest breakdown (e.g. receipt_date)."""
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    case_id = uuid.uuid4()
    ident_hash = "a" * 64
    db.add(
        CaseFile(
            id=case_id,
            slug=f"dedup-{uuid.uuid4().hex[:8]}",
            title="T",
            subject_name="S",
            subject_type="public_official",
            jurisdiction="US",
            status="open",
            created_by="t",
            summary="",
        )
    )
    db.add(CaseContributor(case_file_id=case_id, investigator_handle="t", role="field"))
    db.add(Investigator(handle="t", hashed_api_key="x", public_key=""))
    old_bd = json.dumps({"kind": "donor_cluster", "donor": "X"}, separators=(",", ":"))
    db.add(
        Signal(
            case_file_id=case_id,
            signal_identity_hash=ident_hash,
            signal_type="temporal_proximity",
            weight=0.55,
            description="d",
            evidence_ids="[]",
            exposure_state="internal",
            weight_breakdown=old_bd,
        )
    )
    db.commit()

    new_bd = {
        "kind": "donor_cluster",
        "donor": "X",
        "receipt_date": "2020-01-15",
        "exemplar_financial_date": "2020-01-15",
    }
    upsert_signal(
        db,
        {
            "case_file_id": case_id,
            "signal_identity_hash": ident_hash,
            "signal_type": "temporal_proximity",
            "weight": 0.55,
            "description": "d",
            "evidence_ids": [],
            "exposure_state": "internal",
            "weight_breakdown": json.dumps(new_bd, separators=(",", ":")),
        },
        performed_by="t",
    )
    db.commit()

    s = db.scalars(
        select(Signal).where(Signal.signal_identity_hash == ident_hash)
    ).first()
    db.close()
    assert s is not None
    parsed = json.loads(s.weight_breakdown or "{}")
    assert parsed.get("receipt_date") == "2020-01-15"
    assert parsed.get("exemplar_financial_date") == "2020-01-15"
