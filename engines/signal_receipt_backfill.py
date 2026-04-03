"""
Backfill donor_cluster weight_breakdown.receipt_date from linked FEC evidence.

Use when rows predate receipt_date in breakdown or when only GET /signals is used
(no new run through build_signals_from_proximity).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engines.temporal_proximity import _fec_contribution_receipt_date_from_entry
from models import EvidenceEntry, Signal
from signals.dedup import _parse_evidence_id_list


def _fec_receipt_from_evidence_ids(db: Session, sig: Signal) -> str:
    for sid in _parse_evidence_id_list(sig.evidence_ids):
        try:
            eid = uuid.UUID(str(sid))
        except ValueError:
            continue
        entry = db.get(EvidenceEntry, eid)
        if not entry:
            continue
        d = _fec_contribution_receipt_date_from_entry(entry)
        if d:
            return d
    return ""


def backfill_receipt_date_on_signal(db: Session, sig: Signal, *, force: bool = False) -> bool:
    if sig.signal_type != "temporal_proximity":
        return False
    try:
        bd: dict[str, Any] = json.loads(sig.weight_breakdown or "{}")
    except json.JSONDecodeError:
        return False
    if bd.get("kind") != "donor_cluster":
        return False
    if not force and (str(bd.get("receipt_date") or "").strip()):
        return False
    rec = _fec_receipt_from_evidence_ids(db, sig)
    if not rec and sig.event_date_a:
        rec = str(sig.event_date_a).strip()[:10]
    if not rec:
        return False
    bd["receipt_date"] = rec
    if not (str(bd.get("exemplar_financial_date") or "").strip()):
        bd["exemplar_financial_date"] = rec
    sig.weight_breakdown = json.dumps(bd, separators=(",", ":"), default=str)
    return True


def backfill_all_temporal_signals(db: Session, *, force: bool = False) -> int:
    rows = list(
        db.scalars(select(Signal).where(Signal.signal_type == "temporal_proximity")).all()
    )
    n = 0
    for sig in rows:
        if backfill_receipt_date_on_signal(db, sig, force=force):
            n += 1
    return n
