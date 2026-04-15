#!/usr/bin/env python3
"""Seed pilot Indianapolis / Chicago judge cohorts as cases + SubjectProfile (no investigate)."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.subject_taxonomy import (
    default_branch_for_subject_type,
    default_government_level_for_subject_type,
)
from database import SessionLocal
from models import CaseContributor, CaseFile, Investigator, SubjectProfile
from payloads import apply_case_file_signature

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Representative placeholders — replace with live rosters from court websites / FJC exports.
SEED: list[dict[str, str]] = [
    # Federal Indianapolis
    {
        "pilot": "indianapolis",
        "name": "Pilot S.D. Indiana District Judge (placeholder)",
        "subject_type": "federal_judge_district",
        "jurisdiction": "S.D. Indiana",
    },
    {
        "pilot": "indianapolis",
        "name": "Pilot S.D. Indiana Magistrate Judge (placeholder)",
        "subject_type": "federal_judge_magistrate",
        "jurisdiction": "S.D. Indiana",
    },
    {
        "pilot": "indianapolis",
        "name": "Pilot S.D. Indiana Bankruptcy Judge (placeholder)",
        "subject_type": "federal_judge_bankruptcy",
        "jurisdiction": "S.D. Indiana",
    },
    # Federal Chicago
    {
        "pilot": "chicago",
        "name": "Pilot N.D. Illinois District Judge (placeholder)",
        "subject_type": "federal_judge_district",
        "jurisdiction": "N.D. Illinois",
    },
    {
        "pilot": "chicago",
        "name": "Pilot N.D. Illinois Magistrate Judge (placeholder)",
        "subject_type": "federal_judge_magistrate",
        "jurisdiction": "N.D. Illinois",
    },
    {
        "pilot": "chicago",
        "name": "Pilot N.D. Illinois Bankruptcy Judge (placeholder)",
        "subject_type": "federal_judge_bankruptcy",
        "jurisdiction": "N.D. Illinois",
    },
    # State Indianapolis
    {
        "pilot": "indianapolis",
        "name": "Pilot Marion Superior Court Judge (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Marion County, IN",
    },
    {
        "pilot": "indianapolis",
        "name": "Pilot Indiana Court of Appeals Judge (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Indiana Court of Appeals",
    },
    {
        "pilot": "indianapolis",
        "name": "Pilot Indiana Supreme Court Justice (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Indiana Supreme Court",
    },
    # State Chicago
    {
        "pilot": "chicago",
        "name": "Pilot Cook County Circuit Judge (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Cook County, IL",
    },
    {
        "pilot": "chicago",
        "name": "Pilot Illinois Appellate First District Judge (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Illinois Appellate Court — First District",
    },
    {
        "pilot": "chicago",
        "name": "Pilot Illinois Supreme Court Justice (placeholder)",
        "subject_type": "state_judge",
        "jurisdiction": "Illinois Supreme Court",
    },
]

HANDLE = "pilot_seed"


def _slug(name: str, pilot: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    base = (base.strip("-") or "subject")[:120]
    return f"pilot-{pilot}-{base}"


def _ensure_investigator(db: Session) -> None:
    row = db.scalar(select(Investigator).where(Investigator.handle == HANDLE))
    if row:
        return
    db.add(Investigator(handle=HANDLE, public_key=""))
    db.flush()


def main() -> None:
    db = SessionLocal()
    try:
        _ensure_investigator(db)
        created = 0
        for row in SEED:
            st = row["subject_type"]
            gl = default_government_level_for_subject_type(st)
            br = default_branch_for_subject_type(st)
            slug = _slug(row["name"], row["pilot"])
            if db.scalar(select(CaseFile.id).where(CaseFile.slug == slug)):
                continue
            case = CaseFile(
                slug=slug,
                title=row["name"],
                subject_name=row["name"],
                subject_type=st,
                jurisdiction=row["jurisdiction"],
                status="open",
                created_by=HANDLE,
                summary=f"Pilot cohort {row['pilot']} — seeded without investigation.",
                government_level=gl,
                branch=br,
                pilot_cohort=row["pilot"],
            )
            db.add(case)
            db.flush()
            db.add(
                CaseContributor(
                    case_file_id=case.id,
                    investigator_handle=HANDLE,
                    role="originator",
                )
            )
            db.add(
                SubjectProfile(
                    case_file_id=case.id,
                    subject_name=row["name"],
                    subject_type=st,
                    government_level=gl,
                    branch=br,
                    historical_depth="full",
                    updated_by=HANDLE,
                )
            )
            apply_case_file_signature(case, [], db=None)
            created += 1
        db.commit()
        logger.info("Pilot cohort seed complete: %s new case(s)", created)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
