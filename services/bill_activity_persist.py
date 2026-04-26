"""Persist ``bill_sponsorship`` ``AdapterResult`` rows into ``bill_activity``."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from models import BillActivity, EvidenceEntry


def stable_bill_activity_id(
    case_id: uuid.UUID,
    bioguide: str | None,
    congress: int,
    bill_number: str,
    role: str,
) -> str:
    key = f"{case_id}|{bioguide or ''}|{congress}|{bill_number}|{role}"
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


def upsert_bill_activity_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    bioguide_id: str | None,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "bill_sponsorship":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}
    bill_num = str(raw.get("bill_number") or "").strip()[:32] or "?"
    try:
        congress = int(raw.get("congress"))
    except (TypeError, ValueError):
        return
    role = str(raw.get("role") or "")[:32] or "sponsor"
    bid = stable_bill_activity_id(case_id, bioguide_id, congress, bill_num, role)
    if db.get(BillActivity, bid):
        return
    title = str(raw.get("title") or result.title or "")[:2000] or "Untitled"
    btype = str(raw.get("bill_type") or "").strip()[:16] or None
    intro = _parse_date(str(raw.get("introduced_date") or "") or None)
    if intro is None and entry.date_of_event is not None:
        intro = entry.date_of_event
    cosd = _parse_date(str(raw.get("cosponsored_date") or "") or None)
    st = str(raw.get("current_status") or "")[:64] or None
    subj = str(raw.get("subject_policy_area") or "")[:128] or None
    src = (raw.get("source_url") or result.source_url or "").strip() or ""
    if not src:
        return
    bg = (bioguide_id or str(raw.get("bioguide_id") or "")).strip()[:32] or None
    db.add(
        BillActivity(
            id=bid,
            case_file_id=case_id,
            bioguide_id=bg,
            bill_number=bill_num,
            congress=congress,
            bill_type=btype,
            role=role,
            title=title,
            introduced_date=intro,
            cosponsored_date=cosd,
            current_status=st,
            subject_policy_area=subj,
            source_url=src,
            entered_at=datetime.now(timezone.utc),
        )
    )
