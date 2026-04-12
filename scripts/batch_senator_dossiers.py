#!/usr/bin/env python3
"""Batch-build senator dossiers with pacing and optional dry-run."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import SessionLocal
from models import CaseContributor, CaseFile, SenatorDossier, SubjectProfile
from services.senator_dossier import run_senator_dossier_job

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SENATORS = [
    {"name": "Mitch McConnell", "bioguide_id": "M000355"},
    {"name": "Chuck Grassley", "bioguide_id": "G000386"},
    {"name": "Lindsey Graham", "bioguide_id": "G000359"},
    {"name": "Bob Menendez", "bioguide_id": "M000639"},
    {"name": "Ted Cruz", "bioguide_id": "C001098"},
    {"name": "Tom Cotton", "bioguide_id": "C001095"},
    {"name": "Joni Ernst", "bioguide_id": "E000295"},
    {"name": "Mike Crapo", "bioguide_id": "C000880"},
    {"name": "Bernie Sanders", "bioguide_id": "S000033"},
    {"name": "Elizabeth Warren", "bioguide_id": "W000817"},
    {"name": "Ron Wyden", "bioguide_id": "W000779"},
    {"name": "Tammy Duckworth", "bioguide_id": "D000622"},
    {"name": "Jon Tester", "bioguide_id": "T000464"},
    {"name": "Sheldon Whitehouse", "bioguide_id": "W000802"},
    {"name": "John Cornyn", "bioguide_id": "C001056"},
    {"name": "Marco Rubio", "bioguide_id": "R000595"},
    {"name": "Amy Klobuchar", "bioguide_id": "K000367"},
    {"name": "Lisa Murkowski", "bioguide_id": "M001153"},
    {"name": "Dan Sullivan", "bioguide_id": "S001198"},
    {"name": "Maria Cantwell", "bioguide_id": "C000127"},
]

# Rough list-price hints for logging only (not billing advice).
PERPLEXITY_DEEP_RESEARCH_USD_PER_CATEGORY = 0.05
PROPUBLICA_MEMBER_USD = 0.0
LDA_USD_PER_SEARCH = 0.0

_BATCH_CASE_HANDLE = "batch_senator_dossiers"


def ensure_subject_profiles(db: Session, senators: list[dict]) -> None:
    """
    Ensure each senator has a SubjectProfile (and backing CaseFile). SubjectProfile
    requires case_file_id, subject_name, and subject_type — not standalone rows.
    """
    for s in senators:
        bg = s["bioguide_id"].strip()
        name = (s["name"] or "").strip()
        existing = db.scalar(select(SubjectProfile).where(SubjectProfile.bioguide_id == bg))
        if existing:
            continue
        case = CaseFile(
            slug=f"sen-batch-{bg}",
            title=f"Senator dossier: {name}",
            subject_name=name,
            subject_type="public_official",
            jurisdiction="US",
            status="open",
            created_by=_BATCH_CASE_HANDLE,
            summary="Auto-seeded for batch senator dossier builds.",
        )
        db.add(case)
        db.flush()
        db.add(
            CaseContributor(
                case_file_id=case.id,
                investigator_handle=_BATCH_CASE_HANDLE,
                role="field",
            )
        )
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name=name,
                subject_type="public_official",
                bioguide_id=bg,
            )
        )
    db.commit()


def _unique_share_token(db, alphabet: str = "abcdefghijklmnopqrstuvwxyz0123456789") -> str:
    import secrets

    for _ in range(80):
        token = "".join(secrets.choice(alphabet) for _ in range(8))
        if db.scalar(select(SenatorDossier.id).where(SenatorDossier.share_token == token)) is None:
            return token
    raise RuntimeError("share_token collision")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch senator dossier builds")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without calling external APIs or writing dossiers",
    )
    args = parser.parse_args()

    total_perplexity = 0
    total_pp = 0
    total_lda = 0
    ok = 0
    failed = 0

    if not args.dry_run:
        seed_db = SessionLocal()
        try:
            ensure_subject_profiles(seed_db, SENATORS)
            logger.info("Subject profiles ensured for %s senators", len(SENATORS))
        except Exception:
            seed_db.rollback()
            logger.exception("Seeding SubjectProfile rows failed")
            raise
        finally:
            seed_db.close()

    for i, sub in enumerate(SENATORS):
        bg = sub["bioguide_id"].strip()
        name = sub["name"]
        if args.dry_run:
            logger.info("[dry-run] Would create dossier for %s (%s)", name, bg)
            total_perplexity += 6
            total_pp += 1
            total_lda += 3
            continue

        dossier_id = None
        db = SessionLocal()
        try:
            prof = db.scalar(select(SubjectProfile).where(SubjectProfile.bioguide_id == bg))
            if not prof:
                logger.error("Skip %s — no SubjectProfile for bioguide_id=%s", name, bg)
                failed += 1
                continue
            prev = db.scalar(
                select(SenatorDossier)
                .where(SenatorDossier.bioguide_id == bg, SenatorDossier.status == "completed")
                .order_by(SenatorDossier.completed_at.desc())
            )
            version = 1
            prev_id = None
            if prev:
                version = int(prev.version or 1) + 1
                prev_id = prev.id
            share = _unique_share_token(db)
            row = SenatorDossier(
                bioguide_id=bg,
                senator_name=name[:256],
                dossier_json="{}",
                signature="",
                share_token=share,
                version=version,
                previous_version_id=prev_id,
                status="building",
            )
            db.add(row)
            db.commit()
            dossier_id = row.id
        except Exception as e:
            failed += 1
            logger.exception("Error creating dossier row for %s: %s", name, e)
            dossier_id = None
        finally:
            db.close()

        if dossier_id is None:
            continue

        try:
            logger.info("Building dossier_id=%s for %s (%s)", dossier_id, name, bg)
            run_senator_dossier_job(dossier_id)
        except Exception as e:
            failed += 1
            logger.exception("Error for %s: %s", name, e)
            continue

        db = SessionLocal()
        try:
            row = db.get(SenatorDossier, dossier_id)
            if row and row.status == "completed":
                ok += 1
                logger.info("OK %s dossier_id=%s", name, row.id)
            else:
                failed += 1
                st = row.status if row else "missing"
                logger.error("Failed %s dossier_id=%s status=%s", name, dossier_id, st)
        finally:
            db.close()

        total_perplexity += 6
        total_pp += 1
        total_lda += 3

        if i + 1 < len(SENATORS) and not args.dry_run:
            logger.info("Sleeping 60s before next senator...")
            time.sleep(60)

    est = (
        total_perplexity * PERPLEXITY_DEEP_RESEARCH_USD_PER_CATEGORY
        + total_pp * PROPUBLICA_MEMBER_USD
        + total_lda * LDA_USD_PER_SEARCH
    )
    logger.info(
        "Summary: ok=%s failed=%s ~perplexity_calls=%s (rough USD ~%.2f, see vendor pricing)",
        ok,
        failed,
        total_perplexity,
        est,
    )


if __name__ == "__main__":
    main()
