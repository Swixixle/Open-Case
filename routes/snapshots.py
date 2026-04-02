from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from database import get_db
from models import CaseFile, CaseSnapshot, Investigator
from payloads import apply_case_file_signature, full_case_signing_payload
from signing import pack_signed_hash, sign_payload, verify_signed_hash_string
from routes.evidence import case_detail_response
from scoring import add_credibility


class SnapshotCreate(BaseModel):
    taken_by: str
    label: str = ""


def attach_snapshot_routes(router: APIRouter) -> None:
    @router.post("/{case_id}/snapshot")
    def create_snapshot(
        case_id: uuid.UUID,
        body: SnapshotCreate,
        db: Session = Depends(get_db),
    ):
        case = db.scalar(
            select(CaseFile)
            .options(selectinload(CaseFile.evidence_entries))
            .where(CaseFile.id == case_id)
        )
        if not case:
            raise HTTPException(404, detail="case not found")

        inv = db.scalar(select(Investigator).where(Investigator.handle == body.taken_by))
        if not inv:
            inv = Investigator(handle=body.taken_by, public_key="")
            db.add(inv)
            db.flush()

        max_num = db.scalar(
            select(func.max(CaseSnapshot.snapshot_number)).where(CaseSnapshot.case_file_id == case.id)
        )
        next_num = (max_num or 0) + 1

        entries = list(case.evidence_entries)
        payload = full_case_signing_payload(case, entries)
        payload["snapshot"] = {
            "snapshot_number": next_num,
            "taken_at": datetime.now(timezone.utc).isoformat(),
            "taken_by": body.taken_by,
            "entry_count": len(entries),
            "label": body.label or "",
        }

        signed = sign_payload(payload)
        packed = pack_signed_hash(signed["content_hash"], signed["signature"], payload)

        snap = CaseSnapshot(
            case_file_id=case.id,
            snapshot_number=next_num,
            taken_by=body.taken_by,
            entry_count=len(entries),
            signed_hash=packed,
            share_url="",
            label=body.label or "",
        )
        db.add(snap)
        db.flush()
        snap.share_url = f"/cases/{case.id}/snapshots/{snap.id}"

        apply_case_file_signature(case, entries)

        add_credibility(db, body.taken_by, 1, "generated snapshot")
        db.commit()
        db.refresh(snap)

        verify_embedded = verify_signed_hash_string(packed, None)

        return {
            "snapshot": {
                "id": str(snap.id),
                "case_file_id": str(snap.case_file_id),
                "snapshot_number": snap.snapshot_number,
                "taken_at": snap.taken_at.isoformat() if snap.taken_at else None,
                "taken_by": snap.taken_by,
                "entry_count": snap.entry_count,
                "signed_hash": snap.signed_hash,
                "share_url": snap.share_url,
                "label": snap.label or "",
            },
            "signature_check": verify_embedded,
            "case": case_detail_response(db, case),
        }
