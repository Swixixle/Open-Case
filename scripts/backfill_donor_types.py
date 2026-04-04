#!/usr/bin/env python3
"""Backfill evidence_entries.donor_type from FEC raw_data_json (entity_type + committee)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from adapters.fec import classify_donor_type
from models import EvidenceEntry


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: backfill_donor_types.py <DATABASE_URL>", file=sys.stderr)
        return 1
    url = sys.argv[1]
    eng = create_engine(url)
    updated = 0
    with Session(eng) as session:
        rows = session.scalars(
            select(EvidenceEntry).where(EvidenceEntry.entry_type == "financial_connection")
        ).all()
        for e in rows:
            if getattr(e, "donor_type", None):
                continue
            try:
                raw = json.loads(e.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            et = str(raw.get("entity_type") or "")
            committee = raw.get("committee") if isinstance(raw.get("committee"), dict) else {}
            ct_raw = committee.get("committee_type") if isinstance(committee, dict) else None
            e.donor_type = classify_donor_type(et, str(ct_raw) if ct_raw is not None else None)
            updated += 1
        session.commit()
    print(f"Backfilled donor_type on {updated} financial_connection rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
