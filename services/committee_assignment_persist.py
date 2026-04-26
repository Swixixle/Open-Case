"""Persist ``committee_assignment`` ``AdapterResult`` rows into ``committee_assignments``."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from models import CommitteeAssignment, EvidenceEntry


def stable_committee_assignment_id(
    case_id: uuid.UUID,
    bioguide: str,
    congress: int,
    committee_code: str,
    sub: str | None,
) -> str:
    s = (sub or "").strip()
    key = f"{case_id}|{bioguide}|{congress}|{committee_code}|{s}"
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


def upsert_committee_assignment_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    bioguide_id: str | None,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "committee_assignment":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}
    bg = (bioguide_id or str(raw.get("bioguide_id") or "")).strip()[:32]
    if not bg:
        return
    try:
        congress = int(raw.get("congress"))
    except (TypeError, ValueError):
        return
    code = str(raw.get("committee_code") or "").strip()[:16] or "?"
    _sn = raw.get("subcommittee_name")
    sub: str | None = (
        str(_sn).strip()[:256] if _sn is not None and str(_sn).strip() else None
    )
    cid = stable_committee_assignment_id(case_id, bg, congress, code, sub)
    if db.get(CommitteeAssignment, cid):
        return
    cname = str(raw.get("committee_name") or result.title or "")[:256] or "Committee"
    ch = str(raw.get("chamber") or "").strip()[:16] or None
    ct = str(raw.get("committee_type") or "").strip()[:32] or None
    rk = raw.get("rank_in_party")
    rki: int | None = None
    if rk is not None:
        try:
            rki = int(rk)
        except (TypeError, ValueError):
            rki = None
    sd = _parse_date(str(raw.get("start_date") or ""))
    ed = _parse_date(str(raw.get("end_date") or ""))
    src = (raw.get("source_url") or result.source_url or "").strip()
    if not src:
        return
    db.add(
        CommitteeAssignment(
            id=cid,
            case_file_id=case_id,
            bioguide_id=bg,
            congress=congress,
            chamber=ch,
            committee_code=code,
            committee_name=cname,
            committee_type=ct,
            subcommittee_name=sub,
            rank_in_party=rki,
            start_date=sd,
            end_date=ed,
            source_url=src,
            entered_at=datetime.now(timezone.utc),
        )
    )
