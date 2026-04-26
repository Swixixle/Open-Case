#!/usr/bin/env python3
"""
One-off: remove duplicate Todd Young (Y000064) case files, keeping the canonical one.

Keeps: ef5493ed-0a5c-4b1d-8401-a815-19ae1084
Removes: a7b7d4b3-ef41-4c55-b571-0c735035206d, d83c6587-be82-4beb-abdc-81078ad71ece

Uses explicit deletes (same order as routes/investigate) so local SQLite works even if
`case_files` were removed earlier without `PRAGMA foreign_keys=ON`. After this repo change,
`database.py` turns foreign keys on for every connection.

Run from repo root (after a backup if needed):

  DATABASE_URL=sqlite:///./open_case.db python3 scripts/dedupe_todd_young_duplicate_case_files.py

"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    CaseContributor,
    CaseFile,
    CaseNarrative,
    CaseSnapshot,
    DonorFingerprint,
    EnrichmentReceipt,
    EvidenceEntry,
    InvestigationRun,
    Signal,
    SourceCheckLog,
    SubjectProfile,
)

KEEP = uuid.UUID("ef5493ed-0a5c-4b1d-8401-a815-19ae1084")
REMOVE: tuple[uuid.UUID, ...] = (
    uuid.UUID("a7b7d4b3-ef41-4c55-b571-0c735035206d"),
    uuid.UUID("d83c6587-be82-4beb-abdc-81078ad71ece"),
)


def _purge_case_tree(session: Session, case_id: uuid.UUID) -> None:
    """Delete all rows that reference this case (handles orphan rows if `case_file` is gone)."""
    session.execute(
        delete(DonorFingerprint).where(DonorFingerprint.case_file_id == case_id)
    )
    session.execute(delete(Signal).where(Signal.case_file_id == case_id))
    session.execute(
        delete(EvidenceEntry).where(EvidenceEntry.case_file_id == case_id)
    )
    session.execute(
        delete(CaseNarrative).where(CaseNarrative.case_file_id == case_id)
    )
    session.execute(
        delete(SourceCheckLog).where(SourceCheckLog.case_file_id == case_id)
    )
    session.execute(
        delete(CaseSnapshot).where(CaseSnapshot.case_file_id == case_id)
    )
    session.execute(
        delete(CaseContributor).where(CaseContributor.case_file_id == case_id)
    )
    session.execute(
        delete(InvestigationRun).where(InvestigationRun.case_file_id == case_id)
    )
    session.execute(
        delete(EnrichmentReceipt).where(EnrichmentReceipt.case_file_id == case_id)
    )
    session.execute(
        delete(SubjectProfile).where(SubjectProfile.case_file_id == case_id)
    )
    session.execute(delete(CaseFile).where(CaseFile.id == case_id))


def main() -> int:
    with SessionLocal() as session:
        for cid in (*REMOVE, KEEP):
            cf = session.scalar(select(CaseFile).where(CaseFile.id == cid))
            if not cf:
                print(f"  (note) case {cid} not in case_files (may be already removed)")

        for cid in REMOVE:
            _purge_case_tree(session, cid)
            print(f"Purged all data for case {cid}.")

        y = list(
            session.scalars(
                select(SubjectProfile).where(SubjectProfile.bioguide_id == "Y000064")
            ).all()
        )
        print(f"Remaining Y000064 subject_profiles: {len(y)} (expect 1)")
        for sp in y:
            print(f"  case_file_id={sp.case_file_id}")
        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
