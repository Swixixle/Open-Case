from __future__ import annotations

import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from adapters.congress_votes import DEFAULT_UA, _US_STATE_ABBR, _fetch_member_identity
from models import SenatorCommittee, utc_now

logger = logging.getLogger(__name__)

ASSIGNMENTS_URL = "https://www.senate.gov/general/committee_assignments/assignments.htm"
CACHE_DAYS = 7

_HR_SEP = '<hr width="100%" style="clear: both;">'
_COMMITTEE_LINK_RE = re.compile(
    r"/committee_membership/committee_memberships_([A-Z0-9]+)\.htm\">([^<]+)</a>",
    re.IGNORECASE,
)


def _assignment_anchor_key(profile: dict[str, str]) -> str:
    st = (profile.get("state") or "").strip()
    abbr = (_US_STATE_ABBR.get(st, st) or "XX")[:2].upper()
    last = (profile.get("last") or "").strip()
    return f"{last}{abbr}"


def _extract_senator_chunk(html: str, anchor_key: str) -> str:
    needle = f'<a name="{anchor_key}"'
    start = html.find(needle)
    if start < 0:
        return ""
    hr_at = html.find(_HR_SEP, start + len(needle))
    if hr_at < 0:
        return html[start : start + 12000]
    return html[start:hr_at]


def _parse_committees_from_chunk(chunk: str) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for m in _COMMITTEE_LINK_RE.finditer(chunk):
        code = m.group(1).strip().upper()
        name = re.sub(r"\s+", " ", m.group(2).strip())
        key = (code, name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((name, code))
    return out


async def _download_assignments_html(client: httpx.AsyncClient) -> str:
    r = await client.get(ASSIGNMENTS_URL, timeout=45.0)
    r.raise_for_status()
    return r.text


async def fetch_committee_assignments_for_bioguide(
    bioguide_id: str,
) -> list[dict[str, str]]:
    """
    Live fetch (no DB). Each dict: committee_name, committee_code.
    """
    bioguide_id = bioguide_id.strip().upper()
    headers = {"User-Agent": DEFAULT_UA}
    async with httpx.AsyncClient(timeout=45.0, headers=headers, follow_redirects=True) as client:
        profile = await _fetch_member_identity(client, bioguide_id)
        html = await _download_assignments_html(client)
        if not profile:
            logger.warning(
                "[senate_committees] No Congress.gov profile for %s — cannot locate assignment block",
                bioguide_id,
            )
            return []
        anchor = _assignment_anchor_key(profile)
        chunk = _extract_senator_chunk(html, anchor)
        if not chunk:
            logger.warning(
                "[senate_committees] No assignment block for anchor=%s bioguide=%s",
                anchor,
                bioguide_id,
            )
            return []
        rows = _parse_committees_from_chunk(chunk)
        return [
            {"committee_name": n, "committee_code": c, "bioguide_id": bioguide_id} for n, c in rows
        ]


async def get_or_refresh_senator_committees(
    db: Session, bioguide_id: str
) -> list[SenatorCommittee]:
    """
    Return persisted assignments for a senator, using DB cache when fresh (< CACHE_DAYS).
    """
    bg = bioguide_id.strip().upper()
    existing = list(
        db.scalars(select(SenatorCommittee).where(SenatorCommittee.bioguide_id == bg)).all()
    )
    now = utc_now()
    if existing:
        newest = max(r.fetched_at for r in existing)
        if now - newest < timedelta(days=CACHE_DAYS):
            return existing

    try:
        fresh = await fetch_committee_assignments_for_bioguide(bg)
    except Exception as e:
        logger.warning("[senate_committees] refresh failed for %s: %s", bg, e)
        return existing

    if not fresh:
        return existing

    db.execute(delete(SenatorCommittee).where(SenatorCommittee.bioguide_id == bg))
    inserted: list[SenatorCommittee] = []
    for row in fresh:
        r = SenatorCommittee(
            bioguide_id=bg,
            committee_name=row["committee_name"],
            committee_code=row["committee_code"],
            fetched_at=now,
        )
        db.add(r)
        inserted.append(r)
    db.flush()
    return inserted
