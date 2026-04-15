#!/usr/bin/env python3
"""Retroactively classify epistemic_level on evidence and signals (idempotent)."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import SessionLocal
from models import CaseFile, EvidenceEntry, Signal
from payloads import sign_evidence_entry
from services.evidence_epistemic import apply_epistemic_metadata_to_entry
from services.finding_policy import finalize_finding_after_sign
from services.signal_epistemic import refresh_signal_epistemic_from_evidence


def _level_counts_evidence(db: Session) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in db.scalars(select(EvidenceEntry.epistemic_level)).all():
        c[str(row or "REPORTED")] += 1
    return c


def _level_counts_signals(db: Session) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in db.scalars(select(Signal.epistemic_level)).all():
        c[str(row or "REPORTED")] += 1
    return c


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log counts only; do not commit changes",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ev_before = _level_counts_evidence(db)
        sig_before = _level_counts_signals(db)
        print("Evidence epistemic counts (before):", dict(ev_before))
        print("Signal epistemic counts (before):", dict(sig_before))

        entries = db.scalars(select(EvidenceEntry)).all()
        touched_ev = 0
        for e in entries:
            old = (e.epistemic_level or "REPORTED").strip()
            case = db.get(CaseFile, e.case_file_id)
            apply_epistemic_metadata_to_entry(
                e,
                case_subject_type=case.subject_type if case else None,
                case=case,
                db=db,
            )
            sign_evidence_entry(e)
            finalize_finding_after_sign(e, case)
            new_level = (e.epistemic_level or "REPORTED").strip()
            if old != new_level:
                touched_ev += 1

        if not args.dry_run:
            for s in db.scalars(select(Signal)).all():
                case = db.get(CaseFile, s.case_file_id)
                refresh_signal_epistemic_from_evidence(
                    s, db, case_subject_type=case.subject_type if case else None
                )

        if not args.dry_run:
            db.commit()
        else:
            db.rollback()

        db.expire_all()
        ev_after = _level_counts_evidence(db)
        sig_after = _level_counts_signals(db)
        print(f"Evidence rows where classifier disagrees with stored level: {touched_ev}")
        print("Evidence epistemic counts (after):", dict(ev_after))
        print("Signal epistemic counts (after):", dict(sig_after))
    finally:
        db.close()


if __name__ == "__main__":
    main()
