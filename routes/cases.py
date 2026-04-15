from __future__ import annotations

"""Case CRUD and evidence/snapshot sub-routes. Investigation pipeline: `routes/investigate.py`."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from core.admin_gate import admin_authorized
from core.subject_taxonomy import (
    BRANCHES,
    GOVERNMENT_LEVELS,
    SUBJECT_TYPES,
    default_branch_for_subject_type,
    default_government_level_for_subject_type,
    default_historical_depth_for_subject_type,
)
from database import get_db
from models import CaseContributor, CaseFile, Investigator, SubjectProfile
from payloads import apply_case_file_signature
from routes import evidence as evidence_routes
from routes import snapshots as snapshots_routes
from scoring import add_credibility

router = APIRouter(prefix="/cases", tags=["cases"])

STATUSES = frozenset({"open", "active", "needs_pickup", "stalled", "closed", "referred"})
PILOT_COHORTS = frozenset({"indianapolis", "chicago"})


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
    government_level: str | None = None
    branch: str | None = None
    pilot_cohort: str | None = None


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
    gl_raw = (body.government_level or "").strip()
    br_raw = (body.branch or "").strip()
    gl = (
        gl_raw
        if gl_raw
        else default_government_level_for_subject_type(body.subject_type)
    )
    br = br_raw if br_raw else default_branch_for_subject_type(body.subject_type)
    if gl not in GOVERNMENT_LEVELS:
        raise HTTPException(400, detail=f"government_level must be one of {sorted(GOVERNMENT_LEVELS)}")
    if br not in BRANCHES:
        raise HTTPException(400, detail=f"branch must be one of {sorted(BRANCHES)}")
    pilot = (body.pilot_cohort or "").strip() or None
    if pilot and pilot not in PILOT_COHORTS:
        raise HTTPException(400, detail=f"pilot_cohort must be one of {sorted(PILOT_COHORTS)}")

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
        government_level=gl,
        branch=br,
        pilot_cohort=pilot,
    )
    db.add(case)
    db.flush()

    if (body.subject_type or "").strip() not in ("corporation", "organization"):
        db.add(
            SubjectProfile(
                case_file_id=case.id,
                subject_name=body.subject_name,
                subject_type=body.subject_type,
                government_level=gl,
                branch=br,
                historical_depth=default_historical_depth_for_subject_type(body.subject_type),
                updated_by=body.created_by,
            )
        )

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
def get_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    include_unreviewed: bool = Query(False),
    epistemic_level: str | None = Query(None),
    review_status: str | None = Query(None),
    source_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    court: str | None = Query(None),
    case_number: str | None = Query(None),
    has_direct_source: bool | None = Query(None),
    is_publicly_renderable: bool | None = Query(None),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
):
    if include_unreviewed and not admin_authorized(x_admin_secret):
        raise HTTPException(
            status_code=403,
            detail="include_unreviewed requires a valid X-Admin-Secret",
        )
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
    return evidence_routes.case_detail_response(
        db,
        case,
        include_unreviewed=include_unreviewed,
        admin_authorized=admin_authorized(x_admin_secret),
        epistemic_level=epistemic_level,
        review_status=review_status,
        source_type=source_type,
        date_from=date_from,
        date_to=date_to,
        court=court,
        case_number=case_number,
        has_direct_source=has_direct_source,
        is_publicly_renderable=is_publicly_renderable,
    )


evidence_routes.attach_evidence_routes(router)
snapshots_routes.attach_snapshot_routes(router)
