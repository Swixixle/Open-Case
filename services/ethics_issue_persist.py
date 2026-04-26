"""Persist ``ethics_issue`` ``AdapterResult`` rows into ``ethics_issues``."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from models import EthicsIssue, EvidenceEntry


def stable_ethics_issue_id(case_id: uuid.UUID, source_url: str) -> str:
    key = f"{case_id}|{source_url.strip()}"
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


def upsert_ethics_issue_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    bioguide_id: str | None,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "ethics_issue":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}
    src = (raw.get("source_url") or result.source_url or "").strip()
    if not src:
        return
    eid = stable_ethics_issue_id(case_id, src)
    if db.get(EthicsIssue, eid):
        return
    bg = (bioguide_id or str(raw.get("bioguide_id") or "")).strip()[:32] or None
    it = str(raw.get("issue_type") or "Investigation")[:64]
    ch = str(raw.get("chamber") or "House")[:16] or "House"
    sb = str(raw.get("source_body") or "OCE")[:64] or "OCE"
    fd = _parse_date(str(raw.get("filed_date") or ""))
    if fd is None and entry.date_of_event is not None:
        fd = entry.date_of_event
    subj = str(raw.get("subject_matter") or result.title or "")[:8000] or "—"
    st = str(raw.get("status") or "Unknown")[:64]
    disp = raw.get("disposition")
    disp_t = (str(disp)[:8000] if disp is not None and str(disp).strip() else None)
    rd = _parse_date(str(raw.get("resolution_date") or "") or None)
    epi = str(raw.get("epistemic_level") or "REPORTED")[:32] or "REPORTED"
    db.add(
        EthicsIssue(
            id=eid,
            case_file_id=case_id,
            bioguide_id=bg,
            issue_type=it,
            chamber=ch,
            source_body=sb,
            filed_date=fd,
            subject_matter=subj,
            status=st,
            disposition=disp_t,
            resolution_date=rd,
            epistemic_level=epi,
            source_url=src,
            entered_at=datetime.now(timezone.utc),
        )
    )
