from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from engines.signal_receipt_backfill import backfill_receipt_date_on_signal
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator, Signal


def test_backfill_merges_receipt_date_from_fec_evidence(test_engine) -> None:
    Session = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db = Session()
    case_id = uuid.uuid4()
    fin_id = uuid.uuid4()
    db.add(
        CaseFile(
            id=case_id,
            slug=f"bf-{uuid.uuid4().hex[:8]}",
            title="T",
            subject_name="S",
            subject_type="public_official",
            jurisdiction="US",
            status="open",
            created_by="u",
            summary="",
        )
    )
    db.add(CaseContributor(case_file_id=case_id, investigator_handle="u", role="field"))
    db.add(Investigator(handle="u", hashed_api_key="x", public_key=""))
    raw = json.dumps({"contribution_receipt_date": "2019-06-15"}, separators=(",", ":"))
    db.add(
        EvidenceEntry(
            id=fin_id,
            case_file_id=case_id,
            entry_type="financial_connection",
            title="t",
            body="b",
            source_name="FEC",
            entered_by="u",
            confidence="confirmed",
            raw_data_json=raw,
        )
    )
    sig_id = uuid.uuid4()
    ident = ("b" * 64)[:64]
    bd_old = {"kind": "donor_cluster", "donor": "D", "committee_label": "C"}
    db.add(
        Signal(
            id=sig_id,
            case_file_id=case_id,
            signal_identity_hash=ident,
            signal_type="temporal_proximity",
            weight=0.5,
            description="d",
            evidence_ids=json.dumps([str(fin_id)], separators=(",", ":")),
            exposure_state="internal",
            weight_breakdown=json.dumps(bd_old, separators=(",", ":")),
        )
    )
    db.commit()

    sig = db.scalars(select(Signal).where(Signal.id == sig_id)).first()
    assert sig is not None
    assert backfill_receipt_date_on_signal(db, sig, force=False) is True
    db.commit()
    db.refresh(sig)
    parsed = json.loads(sig.weight_breakdown or "{}")
    assert parsed.get("receipt_date") == "2019-06-15"
    assert parsed.get("exemplar_financial_date") == "2019-06-15"
    db.close()
