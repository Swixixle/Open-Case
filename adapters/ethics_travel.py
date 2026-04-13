"""
Gift and sponsored travel disclosures (best-effort HTML parse; structured only).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from adapters.lda import fetch_lda_filings
from adapters.staff_network import _entities_overlap, _fec_donor_strings_for_case

logger = logging.getLogger(__name__)

SENATE_TRAVEL_URL = "https://www.senate.gov/legislative/lec/sponsored_travel.htm"
HOUSE_TRAVEL_URL = "https://disclosures-clerk.house.gov/GiftTravel/GiftTravelIndex"

CACHE_ADAPTER = "senator_travel"
CACHE_TTL_HOURS = 7 * 24

TRAVEL_DISCLAIMER = (
    "Gift and travel disclosures are parsed from public ethics pages when available. "
    "Co-appearance with FEC or LDA records does not establish improper influence."
)


def _sponsor_type_guess(sponsor: str) -> str:
    s = (sponsor or "").lower()
    if any(x in s for x in ("embassy", "ministry", "government of")):
        return "foreign_government"
    if any(x in s for x in ("foundation", "institute", "association", "fund")):
        return "nonprofit"
    if any(x in s for x in ("inc", "llc", "corp", "ltd", "company")):
        return "corporation"
    return "ngo"


def _parse_float_money(s: str) -> float:
    t = re.sub(r"[^\d.]", "", s or "")
    try:
        return float(t) if t else 0.0
    except ValueError:
        return 0.0


def parse_senate_travel_html(html: str, senator_last: str) -> list[dict[str, Any]]:
    """Very loose parse: lines mentioning senator last name with dollar amounts."""
    if not html or not senator_last:
        return []
    last = senator_last.strip()
    if len(last) < 2:
        return []
    rows: list[dict[str, Any]] = []
    for m in re.finditer(
        r"([\$][\d,]+(?:\.\d{2})?)[^\n]{0,120}(" + re.escape(last) + r")[^\n]{0,200}",
        html,
        re.I | re.DOTALL,
    ):
        chunk = m.group(0)
        val = _parse_float_money(m.group(1))
        sponsor_guess = ""
        sm = re.search(
            r"(?:sponsor|paid\s+by|hosted\s+by)[:\s]+([^<\n;]{4,80})",
            chunk,
            re.I,
        )
        if sm:
            sponsor_guess = sm.group(1).strip()
        rows.append(
            {
                "disclosure_type": "travel",
                "sponsor_name": sponsor_guess or "unknown",
                "sponsor_type": _sponsor_type_guess(sponsor_guess),
                "destination": "",
                "date": "",
                "value": val,
                "purpose": chunk[:300].strip(),
            }
        )
        if len(rows) >= 40:
            break
    return rows


async def fetch_ethics_travel(
    db: Session,
    bioguide_id: str,
    senator_name: str,
    case_file_id: UUID,
) -> list[dict[str, Any]]:
    bg = (bioguide_id or "").strip()
    cached = get_cached_raw_json(db, CACHE_ADAPTER, bg)
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        return list(cached["items"])

    fec_entities = _fec_donor_strings_for_case(db, case_file_id)
    parts = (senator_name or "").strip().split()
    last = parts[-1] if parts else ""

    raw_items: list[dict[str, Any]] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
        ),
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=45.0, headers=headers, follow_redirects=True) as client:
            r = await client.get(SENATE_TRAVEL_URL)
            r.raise_for_status()
            raw_items.extend(parse_senate_travel_html(r.text, last))
    except Exception as e:
        logger.warning("[ethics_travel] senate page failed: %s", e)

    out: list[dict[str, Any]] = []
    for it in raw_items:
        sponsor = str(it.get("sponsor_name") or "")
        fec_donor_match = any(_entities_overlap(sponsor, fe) for fe in fec_entities)
        lda_match = False
        if len(sponsor) >= 3:
            try:
                lda_match = bool(await fetch_lda_filings(sponsor, sponsor))
            except Exception as e:
                logger.warning("[ethics_travel] LDA check failed: %s", e)
        out.append(
            {
                "disclosure_type": str(it.get("disclosure_type") or "travel"),
                "sponsor_name": sponsor,
                "sponsor_type": str(it.get("sponsor_type") or "corporation"),
                "destination": str(it.get("destination") or ""),
                "date": str(it.get("date") or ""),
                "value": float(it.get("value") or 0.0),
                "purpose": str(it.get("purpose") or "")[:2000],
                "fec_donor_match": fec_donor_match,
                "lda_match": lda_match,
                "source_url": SENATE_TRAVEL_URL,
                "disclaimer": TRAVEL_DISCLAIMER,
            }
        )

    try:
        store_cached_raw_json(db, CACHE_ADAPTER, bg, {"items": out}, CACHE_TTL_HOURS)
    except Exception as e:
        logger.warning("ethics_travel cache store failed: %s", e)
    return out
