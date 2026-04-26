"""
Lazy FEC + signal pipeline for case report views when evidence is missing or stale.

Triggers the same investigation path as POST /cases/{id}/investigate without API auth,
using the case owner's handle (auto-created investigator row if needed). That run
includes Senate eFTDS stock/PTR (``StockTradesAdapter``) for ``senator`` /
``public_official`` cases and persists ``stock_trades`` rows alongside evidence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from core.subject_taxonomy import subject_type_uses_fec_congress_pipeline
from models import CaseFile, EvidenceEntry, SubjectProfile

logger = logging.getLogger(__name__)

FEC_STALE_DAYS = 30


def count_fec_financial_rows(db: Session, case_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceEntry)
            .where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "financial_connection",
                EvidenceEntry.source_name == "FEC",
            )
        )
        or 0
    )


def latest_fec_evidence_entered_at(db: Session, case_id: UUID) -> datetime | None:
    return db.scalar(
        select(func.max(EvidenceEntry.entered_at)).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    )


def case_needs_fec_refresh(db: Session, case_id: UUID, case: CaseFile) -> bool:
    if not subject_type_uses_fec_congress_pipeline(case.subject_type):
        return False
    n = count_fec_financial_rows(db, case_id)
    if n == 0:
        return True
    ts = latest_fec_evidence_entered_at(db, case_id)
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return age > timedelta(days=FEC_STALE_DAYS)


def build_investigate_request_for_case(db: Session, case_id: UUID) -> Any | None:
    """Return InvestigateRequest or None if case cannot be auto-ingested."""
    from routes.investigate import InvestigateRequest

    case = db.get(CaseFile, case_id)
    if not case or not subject_type_uses_fec_congress_pipeline(case.subject_type):
        return None
    prof = db.scalar(select(SubjectProfile).where(SubjectProfile.case_file_id == case_id))
    handle = (case.created_by or "").strip() or "auto_ingest"
    name = (
        (prof.subject_name if prof and prof.subject_name else None)
        or (case.subject_name or "")
    ).strip()
    if not name:
        logger.warning("auto-ingest skipped: no subject name case_id=%s", case_id)
        return None
    bioguide = (prof.bioguide_id if prof else None) or None
    if prof and not (prof.state or "").strip():
        jur = (case.jurisdiction or "").strip().upper()
        if len(jur) == 2 and jur.isalpha():
            prof.state = jur
            db.add(prof)
            db.flush()
    return InvestigateRequest(
        subject_name=name,
        investigator_handle=handle,
        address=None,
        bioguide_id=bioguide,
        proximity_days=90,
        fec_committee_id=None,
    )


async def maybe_auto_ingest_case(
    db: Session,
    case_id: UUID,
    *,
    background_tasks: Any | None = None,
) -> None:
    """
    Run full investigation pipeline when FEC rows are absent or older than FEC_STALE_DAYS.
    Swallows adapter failures — report still returns with whatever evidence exists.
    """
    from routes.investigate import execute_investigation_for_case

    case = db.get(CaseFile, case_id)
    if not case:
        return
    if not case_needs_fec_refresh(db, case_id, case):
        return
    request = build_investigate_request_for_case(db, case_id)
    if request is None:
        return
    try:
        result = await execute_investigation_for_case(
            db,
            case_id,
            request,
            background_tasks,
            include_unresolved=False,
            debug=False,
        )
        if isinstance(result, JSONResponse):
            logger.warning(
                "auto-ingest returned non-OK for case_id=%s (adapters or validation)",
                case_id,
            )
    except Exception as e:
        logger.warning("auto-ingest failed case_id=%s: %s", case_id, e)
