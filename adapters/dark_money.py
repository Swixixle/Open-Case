"""
501(c)(4)/(6) / 527 nonprofit signals from ProPublica Nonprofit Explorer (structured only).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from models import EvidenceEntry

logger = logging.getLogger(__name__)

PROPUBLICA_NONPROFIT_URL = (
    "https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json"
)
PROPUBLICA_NONPROFIT_SEARCH = (
    "https://projects.propublica.org/nonprofits/api/v2/search.json?q={q}&state={state}"
)
IRS_990_INDEX = "https://s3.amazonaws.com/irs-form-990/index_{year}.json"

CACHE_ADAPTER = "dark_money"
CACHE_TTL_DAYS = 7
CACHE_TTL_HOURS = CACHE_TTL_DAYS * 24

DARK_MONEY_DISCLAIMER = (
    "Nonprofit Form 990 data documents public tax filings only. Revenue and grants "
    "do not establish coordination between organizations and campaigns."
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def top_fec_organization_donors(db: Session, case_file_id: UUID, *, limit: int = 12) -> list[str]:
    """Distinct contributor / org names from FEC financial_connection evidence."""
    rows = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_file_id,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    ).all()
    totals: dict[str, float] = {}
    for e in rows:
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("contributor_name") or raw.get("contributor_organization") or "").strip()
        if not name or len(name) < 3:
            continue
        try:
            amt = float(raw.get("contribution_receipt_amount") or e.amount or 0.0)
        except (TypeError, ValueError):
            amt = float(e.amount or 0.0)
        totals[name] = totals.get(name, 0.0) + amt
    ranked = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    return ranked[:limit]


def _org_type_from_payload(payload: dict[str, Any]) -> str | None:
    org = payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
    sub = str(org.get("subsection") or org.get("subsection_code") or org.get("sub_code") or "")
    sl = sub.lower()
    if "501(c)(4)" in sl or "501c4" in sl:
        return "501c4"
    if "501(c)(6)" in sl or "501c6" in sl:
        return "501c6"
    if "527" in sl:
        return "527"
    ntee = str(org.get("ntee_code") or org.get("ntee") or "").upper()
    if ntee.startswith("W"):
        return "527"
    name_l = str(org.get("name") or "").lower()
    if "pac" in name_l and "pack" not in name_l:
        return "SuperPAC"
    return None


def _allowed_nonprofit_type(org_type: str | None) -> bool:
    return org_type in ("501c4", "501c6", "527", "SuperPAC")


def _latest_filing_metrics(payload: dict[str, Any]) -> tuple[float, float, float, int | None]:
    filings = payload.get("filings_with_data") or payload.get("filings") or []
    if not isinstance(filings, list) or not filings:
        return 0.0, 0.0, 0.0, None
    latest = filings[0] if isinstance(filings[0], dict) else {}
    if not isinstance(latest, dict):
        return 0.0, 0.0, 0.0, None

    def _f(key: str) -> float:
        v = latest.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    revenue = _f("totrevenue") or _f("total_revenue") or _f("revenue_total")
    political = _f("political_amt") or _f("political_activities") or _f("lobbying_amt")
    grants = _f("grants_given") or _f("grants_and_similar_amount") or _f("grants_total")
    tax_year = latest.get("tax_prd_yr") or latest.get("year")
    try:
        ty = int(tax_year) if tax_year is not None else None
    except (TypeError, ValueError):
        ty = None
    return revenue, political, grants, ty


def pass_through_entities_from_filing(
    grants: float,
    other_donor_names: list[str],
    filing_blob: str,
) -> list[str]:
    """Heuristic: grants > 0 and another tracked donor name appears in filing text."""
    if grants <= 0 or not other_donor_names:
        return []
    blob_l = (filing_blob or "").lower()
    out: list[str] = []
    for name in other_donor_names:
        n = _norm(name)
        if len(n) < 4:
            continue
        if n in blob_l or name.lower() in blob_l:
            out.append(name)
    return sorted(set(out))


def officers_from_org(payload: dict[str, Any]) -> list[str]:
    org = payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
    raw = org.get("officers") or org.get("board_members") or []
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for o in raw:
        if isinstance(o, dict):
            n = str(o.get("name") or o.get("officer_name") or "").strip()
            if n:
                names.append(n)
        elif isinstance(o, str) and o.strip():
            names.append(o.strip())
    return names[:25]


async def fetch_dark_money(
    db: Session,
    bioguide_id: str,
    case_file_id: UUID,
    state: str,
) -> list[dict[str, Any]]:
    bg = (bioguide_id or "").strip()
    cache_key = bg
    cached = get_cached_raw_json(db, CACHE_ADAPTER, cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("entities"), list):
        return list(cached["entities"])

    donors = top_fec_organization_donors(db, case_file_id)
    if not donors:
        store_cached_raw_json(db, CACHE_ADAPTER, cache_key, {"entities": []}, CACHE_TTL_HOURS)
        return []

    st = (state or "").strip().upper()[:2] if len((state or "").strip()) == 2 else ""
    out: list[dict[str, Any]] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
        ),
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(timeout=45.0, headers=headers, follow_redirects=True) as client:
        for donor_name in donors[:10]:
            search_url = (
                f"https://projects.propublica.org/nonprofits/api/v2/search.json?q={quote(donor_name)}"
            )
            if st:
                search_url += f"&state={st}"
            try:
                sr = await client.get(search_url)
                if sr.status_code == 404:
                    logger.warning("[dark_money] ProPublica search 404 for %r", donor_name)
                    continue
                sr.raise_for_status()
                sdata = sr.json()
            except Exception as e:
                logger.warning("[dark_money] search failed for %r: %s", donor_name, e)
                continue

            orgs = sdata.get("organizations") if isinstance(sdata, dict) else None
            if not isinstance(orgs, list) or not orgs:
                continue
            first = orgs[0] if isinstance(orgs[0], dict) else {}
            ein = str(first.get("ein") or "").replace("-", "").strip()
            if not ein or len(ein) < 9:
                continue
            detail_url = PROPUBLICA_NONPROFIT_URL.format(ein=ein)
            try:
                dr = await client.get(detail_url)
                if dr.status_code == 404:
                    logger.warning("[dark_money] ProPublica org 404 ein=%s", ein)
                    continue
                dr.raise_for_status()
                payload = dr.json()
            except Exception as e:
                logger.warning("[dark_money] org fetch failed ein=%s: %s", ein, e)
                continue

            if not isinstance(payload, dict):
                continue
            org_type = _org_type_from_payload(payload)
            if not _allowed_nonprofit_type(org_type):
                continue
            revenue, political, grants, tax_year = _latest_filing_metrics(payload)
            filing_blob = json.dumps(payload, default=str)[:8000]
            pass_through = pass_through_entities_from_filing(
                grants, [d for d in donors if _norm(d) != _norm(donor_name)], filing_blob
            )
            org_block = payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
            display_name = str(org_block.get("name") or first.get("name") or donor_name)

            out.append(
                {
                    "org_name": display_name,
                    "ein": ein,
                    "org_type": org_type,
                    "total_revenue": revenue,
                    "political_expenditures": political,
                    "grants_to_others": grants,
                    "connected_to_fec_donor": True,
                    "fec_donor_name": donor_name,
                    "pass_through_entities": pass_through,
                    "officers": officers_from_org(payload),
                    "source_url": f"https://projects.propublica.org/nonprofits/organizations/{ein}",
                    "tax_year": tax_year or 0,
                    "disclaimer": DARK_MONEY_DISCLAIMER,
                }
            )

    try:
        store_cached_raw_json(
            db,
            CACHE_ADAPTER,
            cache_key,
            {"entities": out},
            CACHE_TTL_HOURS,
        )
    except Exception as e:
        logger.warning("dark_money cache store failed: %s", e)
    return out
