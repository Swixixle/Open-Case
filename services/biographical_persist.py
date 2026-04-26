"""Persist biographical profile rows (Congress.gov) — no evidence entry."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from models import BiographicalProfile


def stable_biographical_profile_id(case_id: uuid.UUID, bioguide_id: str) -> str:
    key = f"{case_id}|{bioguide_id.strip().upper()}"
    return hashlib.md5(key.encode()).hexdigest()


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    return None


def upsert_biographical_profile(
    db: Session,
    case_id: uuid.UUID,
    profile_data: dict[str, Any],
) -> str | None:
    """Insert or update ``BiographicalProfile`` for a case. Returns profile id or None."""
    bg = str(profile_data.get("bioguide_id") or "").strip().upper()
    if not bg:
        return None
    eid = stable_biographical_profile_id(case_id, bg)
    row = db.get(BiographicalProfile, eid)
    common: dict[str, Any] = {
        "case_file_id": case_id,
        "bioguide_id": bg[:10],
        "full_name": (str(profile_data.get("full_name") or "")[:200] or None),
        "birth_date": _parse_iso_date(profile_data.get("birth_date")),
        "birth_city": (str(profile_data.get("birth_city") or "")[:100] or None),
        "birth_state": (str(profile_data.get("birth_state") or "").strip()[:2] or None),
        "party": (str(profile_data.get("party") or "")[:50] or None),
        "current_office": (str(profile_data.get("current_office") or "")[:200] or None),
        "office_start_date": _parse_iso_date(
            str(profile_data.get("office_start_date") or "")
        ),
        "previous_offices": profile_data.get("previous_offices"),
        "education": profile_data.get("education"),
        "military_service": profile_data.get("military_service"),
        "employment_history": profile_data.get("employment_history"),
        "office_addresses": profile_data.get("office_addresses"),
        "official_website": (str(profile_data.get("official_website") or "")[:500] or None),
        "social_media": profile_data.get("social_media"),
    }
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(
            BiographicalProfile(
                id=eid,
                **common,
                entered_at=now,
            )
        )
    else:
        for k, v in common.items():
            setattr(row, k, v)
        row.entered_at = now
    return eid
