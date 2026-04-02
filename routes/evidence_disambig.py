from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.cache import flush_adapter_cache
from auth import require_api_key, require_matching_handle
from database import get_db
from models import EvidenceEntry, Investigator
from payloads import sign_evidence_entry
from scoring import add_credibility

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


class DisambiguateRequest(BaseModel):
    investigator_handle: str
    confirmed_entity_name: str
    confirmation_note: str = Field(..., min_length=1)


@router.patch("/{evidence_id}/disambiguate")
def disambiguate_evidence(
    evidence_id: uuid.UUID,
    request: DisambiguateRequest,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, str | bool]:
    require_matching_handle(auth_inv, request.investigator_handle)
    entry = db.scalar(select(EvidenceEntry).where(EvidenceEntry.id == evidence_id))
    if not entry:
        raise HTTPException(status_code=404, detail="Evidence entry not found")

    if not entry.flagged_for_review:
        return {
            "evidence_id": str(evidence_id),
            "message": "Entry was not flagged for review — no action needed",
        }

    now = datetime.now(timezone.utc)
    entry.confidence = "confirmed"
    entry.flagged_for_review = False
    entry.disambiguation_note = request.confirmation_note
    entry.disambiguation_by = request.investigator_handle
    entry.disambiguation_at = now
    entry.matched_name = request.confirmed_entity_name

    sign_evidence_entry(entry)

    flush_adapter_cache(
        db,
        [
            "FEC",
            "Congress.gov",
            "USASpending",
            "Indiana Campaign Finance",
            "IndyGIS / MapIndy",
            "Marion County Assessor",
        ],
    )

    inv = db.scalar(
        select(Investigator).where(Investigator.handle == request.investigator_handle)
    )
    if not inv:
        inv = Investigator(handle=request.investigator_handle, public_key="")
        db.add(inv)
        db.flush()
    add_credibility(db, request.investigator_handle, 1, "resolved collision")
    db.commit()

    return {
        "evidence_id": str(evidence_id),
        "confidence": "confirmed",
        "disambiguated_by": request.investigator_handle,
        "message": "Evidence entry confirmed and signed",
    }
