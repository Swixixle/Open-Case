from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from database import get_db
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator
from payloads import (
    apply_case_file_signature,
    evidence_semantic_dict,
    full_case_signing_payload,
    sign_evidence_entry,
)
from scoring import add_credibility
from signing import verify_signed_hash_string

ENTRY_TYPES = frozenset(
    {
        "financial_connection",
        "vote_record",
        "property_record",
        "court_record",
        "disclosure",
        "timeline_event",
        "gap_documented",
        "photo_tap",
        "foia_response",
        "investigator_note",
        "handoff_note",
    }
)
CONFIDENCE = frozenset({"confirmed", "probable", "unverified"})


class EvidenceCreate(BaseModel):
    entry_type: str
    title: str
    body: str
    source_url: str = ""
    source_name: str = ""
    date_of_event: str | None = None  # ISO date YYYY-MM-DD
    entered_by: str
    confidence: str
    is_absence: bool = False
    flagged_for_review: bool = False


def _parse_event_date(s: str | None):
    if not s:
        return None
    from datetime import date as date_cls

    return date_cls.fromisoformat(s[:10])


def case_to_response(case: CaseFile) -> dict[str, Any]:
    entries = sorted(case.evidence_entries, key=lambda e: str(e.id))
    return {
        "id": str(case.id),
        "slug": case.slug,
        "title": case.title,
        "subject_name": case.subject_name,
        "subject_type": case.subject_type,
        "jurisdiction": case.jurisdiction,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "created_by": case.created_by,
        "summary": case.summary,
        "pickup_note": case.pickup_note or "",
        "signed_hash": case.signed_hash,
        "last_signed_at": case.last_signed_at.isoformat() if case.last_signed_at else None,
        "view_count": case.view_count,
        "is_public": case.is_public,
        "evidence_entries": [evidence_to_response(e) for e in entries],
    }


def evidence_to_response(e: EvidenceEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "case_file_id": str(e.case_file_id),
        "entry_type": e.entry_type,
        "title": e.title,
        "body": e.body,
        "source_url": e.source_url,
        "source_name": e.source_name,
        "date_of_event": e.date_of_event.isoformat() if e.date_of_event else None,
        "entered_at": e.entered_at.isoformat() if e.entered_at else None,
        "entered_by": e.entered_by,
        "signed_hash": e.signed_hash,
        "confidence": e.confidence,
        "is_absence": e.is_absence,
        "flagged_for_review": e.flagged_for_review,
        "amount": e.amount,
        "matched_name": e.matched_name,
    }


def case_detail_response(db: Session, case: CaseFile) -> dict[str, Any]:
    """Re-load evidence for fresh list after commits."""
    c = db.scalar(
        select(CaseFile)
        .options(selectinload(CaseFile.evidence_entries))
        .where(CaseFile.id == case.id)
    )
    if not c:
        raise HTTPException(404, detail="case not found")
    out = case_to_response(c)
    out["signature_check"] = verify_signed_hash_string(
        c.signed_hash, full_case_signing_payload(c, list(c.evidence_entries))
    )
    return out


def attach_evidence_routes(router: APIRouter) -> None:
    @router.post("/{case_id}/evidence")
    def add_evidence(
        case_id: uuid.UUID,
        body: EvidenceCreate,
        db: Session = Depends(get_db),
        auth_inv: Investigator = Depends(require_api_key),
    ):
        require_matching_handle(auth_inv, body.entered_by)
        if body.entry_type not in ENTRY_TYPES:
            raise HTTPException(400, detail=f"entry_type must be one of {sorted(ENTRY_TYPES)}")
        if body.confidence not in CONFIDENCE:
            raise HTTPException(400, detail=f"confidence must be one of {sorted(CONFIDENCE)}")

        case = db.scalar(
            select(CaseFile)
            .options(selectinload(CaseFile.evidence_entries))
            .where(CaseFile.id == case_id)
        )
        if not case:
            raise HTTPException(404, detail="case not found")

        try:
            d = _parse_event_date(body.date_of_event)
        except ValueError:
            raise HTTPException(400, detail="date_of_event must be ISO YYYY-MM-DD") from None

        inv = db.scalar(select(Investigator).where(Investigator.handle == body.entered_by))
        if not inv:
            inv = Investigator(handle=body.entered_by, public_key="")
            db.add(inv)
            db.flush()
        inv.entries_contributed = (inv.entries_contributed or 0) + 1

        cc = db.scalar(
            select(CaseContributor).where(
                CaseContributor.case_file_id == case.id,
                CaseContributor.investigator_handle == body.entered_by,
            )
        )
        if cc:
            cc.entry_count = (cc.entry_count or 0) + 1
            cc.last_active_at = datetime.now(timezone.utc)
        else:
            db.add(
                CaseContributor(
                    case_file_id=case.id,
                    investigator_handle=body.entered_by,
                    role="field",
                    entry_count=1,
                )
            )

        entry = EvidenceEntry(
            case_file_id=case.id,
            entry_type=body.entry_type,
            title=body.title,
            body=body.body,
            source_url=body.source_url,
            source_name=body.source_name,
            date_of_event=d,
            entered_by=body.entered_by,
            confidence=body.confidence,
            is_absence=body.is_absence,
            flagged_for_review=body.flagged_for_review,
        )
        db.add(entry)
        db.flush()
        sign_evidence_entry(entry)

        all_entries = db.scalars(
            select(EvidenceEntry).where(EvidenceEntry.case_file_id == case.id)
        ).all()
        apply_case_file_signature(case, list(all_entries))

        add_credibility(db, body.entered_by, 1, "added evidence")
        db.commit()
        return case_detail_response(db, case)
