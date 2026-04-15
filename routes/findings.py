"""Formal dispute / correction workflow for evidence findings (Phase 14)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import require_api_key, require_matching_handle
from core.admin_gate import admin_authorized
from database import get_db
from models import CaseFile, DisputeRecord, EvidenceEntry, Investigator
from services.epistemic_classifier import DISPUTED
from services.finding_audit import log_finding_audit
from services.finding_policy import compute_display_label, finalize_finding_after_sign
from payloads import sign_evidence_entry

router = APIRouter(prefix="/api/v1/findings", tags=["findings"])

DISPUTE_TYPES = frozenset(
    {
        "rebuttal",
        "correction",
        "dismissal",
        "later_adjudication",
        "takedown_request",
    }
)
RESOLUTION_STATUSES = frozenset({"pending", "accepted", "rejected", "incorporated"})
SUBMITTERS = frozenset({"judge", "reporter", "editor", "public", "investigator"})


class DisputeCreate(BaseModel):
    submitted_by: str = Field(..., description="judge | reporter | editor | public | investigator")
    dispute_type: str
    dispute_text: str
    supporting_source_url: str = ""
    supporting_document_hash: str | None = None
    investigator_handle: str


class DisputeResolve(BaseModel):
    resolution_status: str
    resolution_notes: str = ""
    investigator_handle: str


def _get_finding(db: Session, finding_id: uuid.UUID) -> EvidenceEntry | None:
    return db.get(EvidenceEntry, finding_id)


@router.post("/{finding_id}/dispute")
def submit_dispute(
    finding_id: uuid.UUID,
    body: DisputeCreate,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    require_matching_handle(auth_inv, body.investigator_handle)
    if body.submitted_by not in SUBMITTERS:
        raise HTTPException(400, detail=f"submitted_by must be one of {sorted(SUBMITTERS)}")
    if body.dispute_type not in DISPUTE_TYPES:
        raise HTTPException(400, detail=f"dispute_type must be one of {sorted(DISPUTE_TYPES)}")
    ent = _get_finding(db, finding_id)
    if not ent:
        raise HTTPException(404, detail="finding not found")
    row = DisputeRecord(
        finding_id=ent.id,
        subject_id=ent.subject_id,
        submitted_by=body.submitted_by,
        dispute_type=body.dispute_type,
        dispute_text=body.dispute_text,
        supporting_source_url=body.supporting_source_url or "",
        supporting_document_hash=body.supporting_document_hash,
    )
    db.add(row)
    db.flush()
    ent.contradiction_count = int(getattr(ent, "contradiction_count", 0) or 0) + 1
    ent.epistemic_level = DISPUTED
    ent.display_label = compute_display_label(DISPUTED)
    sign_evidence_entry(ent)
    finalize_finding_after_sign(ent, db.get(CaseFile, ent.case_file_id))
    log_finding_audit(
        db,
        finding_id=ent.id,
        event_type="dispute_opened",
        detail={"dispute_id": str(row.id), "type": body.dispute_type},
    )
    db.commit()
    return {"dispute_id": str(row.id), "finding_id": str(ent.id), "status": "pending"}


@router.get("/{finding_id}/disputes")
def list_disputes(
    finding_id: uuid.UUID,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    ent = _get_finding(db, finding_id)
    if not ent:
        raise HTTPException(404, detail="finding not found")
    rows = db.scalars(
        select(DisputeRecord)
        .where(DisputeRecord.finding_id == finding_id)
        .order_by(DisputeRecord.submission_date.desc())
    ).all()
    return {
        "finding_id": str(finding_id),
        "disputes": [
            {
                "dispute_id": str(r.id),
                "submitted_by": r.submitted_by,
                "submission_date": r.submission_date.isoformat() if r.submission_date else None,
                "dispute_type": r.dispute_type,
                "dispute_text": r.dispute_text,
                "supporting_source_url": r.supporting_source_url,
                "resolution_status": r.resolution_status,
                "resolution_notes": r.resolution_notes,
            }
            for r in rows
        ],
    }


@router.patch("/disputes/{dispute_id}")
def resolve_dispute(
    dispute_id: uuid.UUID,
    body: DisputeResolve,
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    if not admin_authorized(x_admin_secret):
        raise HTTPException(403, detail="Admin resolution requires X-Admin-Secret")
    require_matching_handle(auth_inv, body.investigator_handle)
    if body.resolution_status not in RESOLUTION_STATUSES:
        raise HTTPException(
            400, detail=f"resolution_status must be one of {sorted(RESOLUTION_STATUSES)}"
        )
    row = db.get(DisputeRecord, dispute_id)
    if not row:
        raise HTTPException(404, detail="dispute not found")
    ent = _get_finding(db, row.finding_id)
    if not ent:
        raise HTTPException(404, detail="finding not found")
    row.resolution_status = body.resolution_status
    row.resolution_notes = body.resolution_notes or ""
    row.resolution_date = datetime.now(timezone.utc)
    row.resolved_by = body.investigator_handle
    if body.resolution_status == "accepted":
        ent.claim_status = "superseded"
        log_finding_audit(
            db,
            finding_id=ent.id,
            event_type="dispute_accepted",
            detail={"dispute_id": str(row.id), "notes": body.resolution_notes[:2000]},
        )
    else:
        log_finding_audit(
            db,
            finding_id=ent.id,
            event_type="dispute_resolved",
            detail={"dispute_id": str(row.id), "status": body.resolution_status},
        )
    sign_evidence_entry(ent)
    finalize_finding_after_sign(ent, db.get(CaseFile, ent.case_file_id))
    db.commit()
    return {"dispute_id": str(dispute_id), "resolution_status": row.resolution_status}
