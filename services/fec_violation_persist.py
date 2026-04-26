"""Persist ``fec_violation`` ``AdapterResult`` rows into ``fec_violations``."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from models import EvidenceEntry, FECViolation


def stable_fec_violation_id(
    case_id: uuid.UUID,
    case_type: str,
    mur_number: str,
) -> str:
    key = f"{case_id}|{case_type}|{mur_number}"
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    return None


def upsert_fec_violation_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "fec_violation":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}
    mno = str(raw.get("mur_number") or "").strip()[:32] or "?"
    cty = str(raw.get("case_type") or "MUR").strip()[:32] or "MUR"
    vid = stable_fec_violation_id(case_id, cty, mno)
    if db.get(FECViolation, vid):
        return
    rj = (raw.get("respondent_names") or "[]").strip()
    try:
        json.loads(rj)
    except json.JSONDecodeError:
        rj = json.dumps([rj], ensure_ascii=False)
    subj = str(raw.get("subject_matter") or result.title or "")[:8000] or "—"
    disp = raw.get("disposition")
    disp_t: str | None = None
    if disp is not None and str(disp).strip():
        disp_t = str(disp)[:8000]
    fa = raw.get("fine_amount")
    fai: int | None = None
    if fa is not None:
        try:
            fai = int(fa)
        except (TypeError, ValueError):
            fai = None
    st = str(raw.get("status") or "Unknown")[:64]
    src = (raw.get("source_url") or result.source_url or "").strip() or "https://www.fec.gov/"
    fd = _parse_date(str(raw.get("filed_date") or ""))
    if fd is None and entry.date_of_event is not None:
        fd = entry.date_of_event
    cd = _parse_date(str(raw.get("closed_date") or ""))
    db.add(
        FECViolation(
            id=vid,
            case_file_id=case_id,
            mur_number=mno,
            case_type=cty,
            filed_date=fd,
            closed_date=cd,
            respondent_names=rj,
            subject_matter=subj,
            disposition=disp_t,
            fine_amount=fai,
            status=st,
            source_url=src,
            entered_at=datetime.now(timezone.utc),
        )
    )
