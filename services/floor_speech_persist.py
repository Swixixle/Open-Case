"""Persist ``floor_speech`` ``AdapterResult`` rows into ``floor_speeches``."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from models import EvidenceEntry, FloorSpeech


def stable_floor_speech_id(
    case_id: uuid.UUID,
    bioguide: str | None,
    congress: int,
    volume: int,
    number: int,
    speech_date: date,
) -> str:
    key = (
        f"{case_id}|{bioguide or ''}|{congress}|{volume}|{number}|{speech_date.isoformat()}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:64]


def upsert_floor_speech_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    bioguide_id: str | None,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "floor_speech":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}
    try:
        cong = int(raw.get("congress"))
    except (TypeError, ValueError):
        return
    vol = int(raw.get("volume") or 0)
    num = int(raw.get("number") or 0)
    pub = str(raw.get("speech_date") or "")[:10]
    if len(pub) != 10:
        if entry.date_of_event is not None:
            d = entry.date_of_event
            pub = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        else:
            return
    try:
        sd = date.fromisoformat(pub)
    except ValueError:
        return
    bg = (bioguide_id or str(raw.get("bioguide_id") or "")).strip()[:32] or None
    sid = stable_floor_speech_id(case_id, bg, cong, vol, num, sd)
    if db.get(FloorSpeech, sid):
        return
    ch = str(raw.get("chamber") or "Congress")[:16] or "Congress"
    ex = (raw.get("excerpt") or result.body or "")[:8000] or None
    tit = (raw.get("title") or result.title or "")[:2000] or None
    pr = raw.get("page_range")
    pr_s = str(pr)[:64] if pr is not None and str(pr).strip() else None
    furl = (raw.get("full_text_url") or result.source_url or "").strip() or "https://www.congress.gov/congressional-record"
    ttags = (raw.get("topic_tags") or None) and str(raw.get("topic_tags")) or None
    src = (raw.get("source_url") or result.source_url or "").strip() or furl
    db.add(
        FloorSpeech(
            id=sid,
            case_file_id=case_id,
            bioguide_id=bg,
            congress=cong,
            chamber=ch,
            speech_date=sd,
            volume=vol,
            number=num,
            page_range=pr_s,
            title=tit,
            excerpt=ex,
            full_text_url=furl,
            topic_tags=ttags,
            source_url=src,
            entered_at=datetime.now(timezone.utc),
        )
    )
