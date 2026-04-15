#!/usr/bin/env python3
"""
Seed expansion subject profiles (cases + SubjectProfile rows) without duplicating bioguides.

Idempotent: skips when SubjectProfile.bioguide_id already exists, or CaseFile.slug matches.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import SessionLocal
from models import CaseContributor, CaseFile, SubjectProfile
from payloads import apply_case_file_signature
from routes.investigate import InvestigateRequest, execute_investigation_for_case

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HANDLE = "corpus_expansion"

# New profiles only (Warren, Cruz, and existing senators are skipped via bioguide / slug checks).
EXPANSION: list[dict[str, Any]] = [
    {
        "slug": "expansion-jim-jordan-j000289",
        "title": "Public official: Jim Jordan (R-OH)",
        "subject_name": "Jim Jordan",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": "J000289",
        "state": "OH",
        "district": "4",
        "office": "U.S. House",
        "summary": (
            "House Republican (OH-04). FEC + Congress.gov vote path. "
            "Principal committee resolved at investigate time if not set."
        ),
        "investigate": True,
    },
    {
        "slug": "expansion-aoc-o000172",
        "title": "Public official: Alexandria Ocasio-Cortez (D-NY)",
        "subject_name": "Alexandria Ocasio-Cortez",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": "O000172",
        "state": "NY",
        "district": "14",
        "office": "U.S. House",
        "summary": (
            "House Democrat (NY-14). FEC + Congress.gov vote path. "
            "Principal committee resolved at investigate time if not set."
        ),
        "investigate": True,
    },
    {
        "slug": "expansion-donald-trump-p80001571",
        "title": "Public official: Donald Trump",
        "subject_name": "Donald Trump",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": None,
        "state": None,
        "district": None,
        "office": "Executive",
        "summary": (
            "Executive branch; party R. OpenFEC candidate_id P80001571. "
            "HOLD: do not run investigate until executive/presidential FEC path is confirmed."
        ),
        "investigate": False,
    },
    {
        "slug": "expansion-joe-biden-p80000722",
        "title": "Public official: Joe Biden",
        "subject_name": "Joe Biden",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": None,
        "state": None,
        "district": None,
        "office": "Executive",
        "summary": (
            "Executive branch; party D. OpenFEC candidate_id P80000722. "
            "HOLD: do not run investigate until executive/presidential FEC path is confirmed."
        ),
        "investigate": False,
    },
    {
        "slug": "expansion-clarence-thomas-scotus",
        "title": "Public official: Clarence Thomas",
        "subject_name": "Clarence Thomas",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": None,
        "state": None,
        "district": None,
        "office": "Supreme Court",
        "summary": (
            "SCOTUS. No FEC candidate/committee linkage; no bioguide. "
            "Judicial financial disclosure path only. "
            "HOLD: wait for judicial accountability data model before investigate."
        ),
        "investigate": False,
    },
    {
        "slug": "expansion-ketanji-brown-jackson-scotus",
        "title": "Public official: Ketanji Brown Jackson",
        "subject_name": "Ketanji Brown Jackson",
        "subject_type": "public_official",
        "jurisdiction": "United States",
        "bioguide_id": None,
        "state": None,
        "district": None,
        "office": "Supreme Court",
        "summary": (
            "SCOTUS. No FEC candidate/committee linkage; no bioguide. "
            "Judicial financial disclosure path only. "
            "HOLD: wait for judicial accountability data model before investigate."
        ),
        "investigate": False,
    },
]


def _already_have_profile(db: Session, row: dict[str, Any]) -> bool:
    bg = row.get("bioguide_id")
    if bg and str(bg).strip():
        if db.scalar(select(SubjectProfile.id).where(SubjectProfile.bioguide_id == str(bg).strip())):
            logger.info("Skip (existing bioguide): %s", bg)
            return True
    slug = row["slug"]
    if db.scalar(select(CaseFile.id).where(CaseFile.slug == slug)):
        logger.info("Skip (existing slug): %s", slug)
        return True
    if not (bg and str(bg).strip()):
        sid = db.scalar(
            select(SubjectProfile.id).where(
                SubjectProfile.subject_name == row["subject_name"],
                SubjectProfile.bioguide_id.is_(None),
            )
        )
        if sid:
            logger.info("Skip (existing profile, no bioguide): %s", row["subject_name"])
            return True
    return False


def seed_profiles(db: Session) -> list[uuid.UUID]:
    from routes.investigate import _ensure_investigator

    _ensure_investigator(db, HANDLE)
    created_case_ids: list[uuid.UUID] = []
    for row in EXPANSION:
        if _already_have_profile(db, row):
            continue
        case = CaseFile(
            slug=row["slug"],
            title=row["title"],
            subject_name=row["subject_name"],
            subject_type=row["subject_type"],
            jurisdiction=row["jurisdiction"],
            status="open",
            created_by=HANDLE,
            summary=row["summary"],
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
                subject_name=row["subject_name"],
                subject_type=row["subject_type"],
                bioguide_id=(str(row["bioguide_id"]).strip() if row.get("bioguide_id") else None),
                state=row.get("state"),
                district=row.get("district"),
                office=row.get("office"),
                updated_by=HANDLE,
            )
        )
        apply_case_file_signature(case, [], db=db)
        created_case_ids.append(case.id)
        logger.info("Created case %s slug=%s", case.id, row["slug"])
    db.commit()
    return created_case_ids


async def run_investigate_for_bioguides(db: Session, bioguides: list[str]) -> None:
    for bg in bioguides:
        prof = db.scalar(select(SubjectProfile).where(SubjectProfile.bioguide_id == bg))
        if not prof:
            logger.error("No SubjectProfile for %s — seed first", bg)
            continue
        req = InvestigateRequest(
            subject_name=prof.subject_name,
            investigator_handle=HANDLE,
            address=None,
            bioguide_id=bg,
            fec_committee_id=None,
        )
        logger.info("Investigate start case_id=%s bioguide=%s", prof.case_file_id, bg)
        await execute_investigation_for_case(
            db,
            prof.case_file_id,
            req,
            background_tasks=None,
            include_unresolved=False,
            debug=False,
        )
        logger.info("Investigate done case_id=%s bioguide=%s", prof.case_file_id, bg)


async def _async_main(investigate: bool) -> None:
    db = SessionLocal()
    try:
        seed_profiles(db)
        if investigate:
            await run_investigate_for_bioguides(db, ["J000289", "O000172"])
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--investigate-house",
        action="store_true",
        help="Run full investigate for Jim Jordan and AOC (network + FEC).",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(args.investigate_house))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
