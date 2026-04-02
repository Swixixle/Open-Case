from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import EvidenceEntry


def make_evidence_hash(
    case_file_id: str | uuid.UUID,
    source_name: str,
    source_url: str,
    date_of_event: str | None,
    amount: float | None,
    matched_name: str | None,
) -> str:
    cid = str(case_file_id)
    amt_part = ""
    if amount is not None:
        try:
            amt_part = str(round(float(amount), 2))
        except (TypeError, ValueError):
            amt_part = str(amount)
    payload = ":".join(
        [
            cid,
            source_name or "",
            source_url or "",
            str(date_of_event or ""),
            amt_part,
            (matched_name or "").lower().strip(),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def is_duplicate(db: Session, case_file_id: uuid.UUID, evidence_hash: str) -> bool:
    if not evidence_hash:
        return False
    row = db.scalar(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_file_id,
            EvidenceEntry.evidence_hash == evidence_hash,
        )
    )
    return row is not None
