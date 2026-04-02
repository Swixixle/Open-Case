from __future__ import annotations

"""Case CRUD and evidence/snapshot sub-routes. Investigation pipeline: `routes/investigate.py`."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from database import get_db
from models import CaseContributor, CaseFile, Investigator
from payloads import apply_case_file_signature
from routes import evidence as evidence_routes
from routes import snapshots as snapshots_routes
from scoring import add_credibility

router = APIRouter(prefix="/cases", tags=["cases"])

SUBJECT_TYPES = frozenset({"public_official", "corporation", "organization"})
STATUSES = frozenset({"open", "active", "needs_pickup", "stalled", "closed", "referred"})


class StatusUpdateRequest(BaseModel):
    status: str
    pickup_note: str | None = None
    investigator_handle: str


class PickupRequest(BaseModel):
    investigator_handle: str


class CaseCreate(BaseModel):
    slug: str = Field(..., max_length=512)
    title: str
    subject_name: str
    subject_type: str
    jurisdiction: str
    status: str = "open"
    created_by: str
    summary: str
    pickup_note: str = ""
    is_public: bool = True


def _ensure_investigator(db: Session, handle: str) -> Investigator:
    row = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if row:
        return row
    inv = Investigator(handle=handle, public_key="")
    db.add(inv)
    db.flush()
    return inv


@router.post("")
def create_case(
    body: CaseCreate,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
):
    require_matching_handle(auth_inv, body.created_by)
    if body.subject_type not in SUBJECT_TYPES:
        raise HTTPException(400, detail=f"subject_type must be one of {sorted(SUBJECT_TYPES)}")
    if body.status not in STATUSES:
        raise HTTPException(400, detail=f"status must be one of {sorted(STATUSES)}")

    exists = db.scalar(select(CaseFile).where(CaseFile.slug == body.slug))
    if exists:
        raise HTTPException(409, detail="slug already in use")

    inv = _ensure_investigator(db, body.created_by)
    case = CaseFile(
        slug=body.slug,
        title=body.title,
        subject_name=body.subject_name,
        subject_type=body.subject_type,
        jurisdiction=body.jurisdiction,
        status=body.status,
        created_by=body.created_by,
        summary=body.summary,
        pickup_note=body.pickup_note,
        is_public=body.is_public,
    )
    db.add(case)
    db.flush()

    inv.cases_opened = (inv.cases_opened or 0) + 1
    db.add(
        CaseContributor(
            case_file_id=case.id,
            investigator_handle=body.created_by,
            role="originator",
        )
    )

    apply_case_file_signature(case, [], db=db)
    add_credibility(db, body.created_by, 2, "opened case")
    db.commit()
    db.refresh(case)
    return evidence_routes.case_detail_response(db, case)


@router.get("/browse/available")
def get_available_cases(
    status: str = "needs_pickup",
    jurisdiction: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    q = select(CaseFile).where(CaseFile.status == status)
    if jurisdiction:
        q = q.where(CaseFile.jurisdiction.ilike(f"%{jurisdiction}%"))
    q = q.order_by(CaseFile.created_at.desc()).limit(50)
    cases = db.scalars(q).all()
    return {
        "count": len(cases),
        "cases": [
            {
                "id": str(c.id),
                "title": c.title,
                "subject_name": c.subject_name,
                "jurisdiction": c.jurisdiction,
                "status": c.status,
                "pickup_note": c.pickup_note,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "view_count": c.view_count,
            }
            for c in cases
        ],
    }


@router.patch("/{case_id}/status")
def update_case_status(
    case_id: uuid.UUID,
    request: StatusUpdateRequest,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, str]:
    require_matching_handle(auth_inv, request.investigator_handle)
    if request.status not in STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {sorted(STATUSES)}",
        )
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    case.status = request.status
    if request.pickup_note is not None:
        case.pickup_note = request.pickup_note
    db.commit()
    db.refresh(case)
    return {
        "case_id": str(case_id),
        "status": case.status,
        "pickup_note": case.pickup_note or "",
    }


@router.post("/{case_id}/pickup")
def pickup_case(
    case_id: uuid.UUID,
    request: PickupRequest,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, str]:
    require_matching_handle(auth_inv, request.investigator_handle)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    existing = db.scalar(
        select(CaseContributor).where(
            CaseContributor.case_file_id == case_id,
            CaseContributor.investigator_handle == request.investigator_handle,
        )
    )
    if not existing:
        db.add(
            CaseContributor(
                case_file_id=case_id,
                investigator_handle=request.investigator_handle,
                role="pickup",
            )
        )

    case.status = "active"
    add_credibility(db, request.investigator_handle, 1, "picked up case")
    db.commit()
    db.refresh(case)
    return {
        "case_id": str(case_id),
        "picked_up_by": request.investigator_handle,
        "prior_pickup_note": case.pickup_note or "",
        "status": case.status,
    }


@router.get("/{case_id}")
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)):
    case = db.scalar(
        select(CaseFile)
        .options(selectinload(CaseFile.evidence_entries))
        .where(CaseFile.id == case_id)
    )
    if not case:
        raise HTTPException(404, detail="case not found")
    case.view_count = (case.view_count or 0) + 1
    db.commit()
    db.refresh(case)
    return evidence_routes.case_detail_response(db, case)


evidence_routes.attach_evidence_routes(router)
snapshots_routes.attach_snapshot_routes(router)
