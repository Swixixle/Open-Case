from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta

from core.datetime_utils import coerce_utc

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from adapters.congress_gov_headers import CONGRESS_GOV_BROWSER_HEADERS
from adapters.congress_votes import _US_STATE_ABBR, _fetch_member_identity
from models import CaseFile, EvidenceEntry, SenatorCommittee, SubjectProfile, utc_now

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


def _sanitize_last_for_anchor(last: str) -> str:
    return re.sub(r"[^A-Za-z]", "", (last or "").strip())


def _subject_anchor_parts_from_db(db: Session, bioguide_id: str) -> tuple[str, str] | None:
    """Last name + state (2-letter) from subject profile / case when Congress.gov is unavailable."""
    bg = bioguide_id.strip().upper()
    prof = db.scalar(select(SubjectProfile).where(SubjectProfile.bioguide_id == bg))
    if not prof:
        return None
    case = db.get(CaseFile, prof.case_file_id)
    name_src = (prof.subject_name or (case.subject_name if case else "") or "").strip()
    parts = name_src.split()
    last = parts[-1] if parts else ""
    st = (prof.state or "").strip().upper()
    if len(st) != 2 and case:
        jur = (case.jurisdiction or "").strip().upper()
        if len(jur) == 2 and jur.isalpha():
            st = jur
    if len(st) != 2 or not last:
        return None
    return last, st[:2]


def committees_from_fec_evidence_for_bioguide(
    db: Session, bioguide_id: str
) -> list[dict[str, str]]:
    """
    Recover principal committee names from ingested FEC Schedule A rows when
    Senate.gov / Congress.gov assignment scraping is unavailable.
    """
    bg = bioguide_id.strip().upper()
    stmt = (
        select(EvidenceEntry.raw_data_json)
        .join(SubjectProfile, SubjectProfile.case_file_id == EvidenceEntry.case_file_id)
        .where(
            SubjectProfile.bioguide_id == bg,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    )
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for (raw_json,) in db.execute(stmt).all():
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            continue
        com = raw.get("committee")
        if not isinstance(com, dict):
            continue
        cid = str(
            com.get("committee_id") or com.get("id") or raw.get("committee_id") or ""
        ).strip()
        name = str(com.get("name") or "").strip()
        if not name and not cid:
            continue
        if cid:
            code = f"FEC-{cid}"
        else:
            h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10].upper()
            code = f"FECN-{h}"
        key = (code, name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "committee_name": name or "Committee (FEC)",
                "committee_code": code,
                "bioguide_id": bg,
            }
        )
    return out


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


def _assignments_from_html(html: str, anchor: str, bioguide_id: str) -> list[dict[str, str]]:
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


async def fetch_committee_assignments_for_bioguide(
    bioguide_id: str,
) -> list[dict[str, str]]:
    """
    Live fetch (no DB). Each dict: committee_name, committee_code.
    """
    bioguide_id = bioguide_id.strip().upper()
    async with httpx.AsyncClient(
        timeout=45.0, headers=CONGRESS_GOV_BROWSER_HEADERS, follow_redirects=True
    ) as client:
        profile = await _fetch_member_identity(client, bioguide_id)
        html = await _download_assignments_html(client)
        if not profile:
            logger.warning(
                "[senate_committees] No Congress.gov profile for %s — cannot locate assignment block",
                bioguide_id,
            )
            return []
        anchor = _assignment_anchor_key(profile)
        return _assignments_from_html(html, anchor, bioguide_id)


async def fetch_committee_assignments_for_bioguide_fallback(
    db: Session, bioguide_id: str
) -> list[dict[str, str]]:
    """
    Same Senate.gov HTML as the primary path, but anchor = lastName + state from DB
    when CONGRESS_API_KEY / Congress.gov member lookup is unavailable.
    """
    bg = bioguide_id.strip().upper()
    parts = _subject_anchor_parts_from_db(db, bg)
    if not parts:
        return []
    last, abbr = parts
    anchor = f"{_sanitize_last_for_anchor(last)}{abbr}"
    async with httpx.AsyncClient(
        timeout=45.0, headers=CONGRESS_GOV_BROWSER_HEADERS, follow_redirects=True
    ) as client:
        html = await _download_assignments_html(client)
    return _assignments_from_html(html, anchor, bg)


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
        now_utc = coerce_utc(now)
        newest_utc = coerce_utc(newest)
        if now_utc is None or newest_utc is None:
            logger.warning(
                "[senate_committees] Could not coerce cache timestamps; refreshing."
            )
        elif now_utc - newest_utc < timedelta(days=CACHE_DAYS):
            return existing

    try:
        fresh = await fetch_committee_assignments_for_bioguide(bg)
    except Exception as e:
        logger.warning("[senate_committees] refresh failed for %s: %s", bg, e)
        fresh = []

    if not fresh:
        try:
            fresh = await fetch_committee_assignments_for_bioguide_fallback(db, bg)
        except Exception as e:
            logger.warning("[senate_committees] fallback refresh failed for %s: %s", bg, e)
            fresh = []

    if not fresh:
        fresh = committees_from_fec_evidence_for_bioguide(db, bg)

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
