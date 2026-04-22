from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Unified LDA public API (House + Senate); lda.senate.gov was consolidated under lda.gov.
LDA_API_FILINGS_URL = "https://lda.gov/api/v1/filings/"

# Backwards alias for importers
LDA_FILINGS_URL = LDA_API_FILINGS_URL


def lda_public_filing_url(filing_uuid: str) -> str:
    """
    Human-readable filing view on lda.gov. Old links like
    `https://lda.senate.gov/filings/{uuid}/` return 404; the active pattern is
    `https://lda.gov/filings/public/filing/{uuid}/print/`.
    """
    u = str(filing_uuid).strip()
    if not u:
        return "https://lda.gov/"
    return f"https://lda.gov/filings/public/filing/{u}/print/"


def _normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip())


async def fetch_lda_filings(donor_name: str, connected_org_name: str) -> list[dict[str, Any]]:
    """
    Query Senate LDA public API by registrant_name and client_name.
    Returns normalized filing dicts from the last two calendar years (inclusive).
    """
    year_min = date.today().year - 2
    queries: list[tuple[str, str]] = []
    for q in (_normalize_query(donor_name), _normalize_query(connected_org_name)):
        if len(q) < 2:
            continue
        queries.append(("registrant_name", q))
        queries.append(("client_name", q))

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) "
            "congressional-research"
        )
    }

    async with httpx.AsyncClient(timeout=40.0, headers=headers, follow_redirects=True) as client:
        for param, value in queries:
            req_url = LDA_API_FILINGS_URL
            req_params: dict[str, str] | None = {param: value, "format": "json"}
            pages = 0
            while req_url and pages < 12:
                pages += 1
                try:
                    if req_params:
                        resp = await client.get(req_url, params=req_params)
                    else:
                        resp = await client.get(req_url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.warning("[lda] fetch failed %s=%r: %s", param, value, e)
                    break

                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    break
                for raw in results:
                    if not isinstance(raw, dict):
                        continue
                    norm = _normalize_filing_dict(raw)
                    fy = norm.get("filing_year")
                    try:
                        y = int(fy) if fy is not None else 0
                    except (TypeError, ValueError):
                        y = 0
                    if y and y < year_min:
                        continue
                    uid = str(norm.get("filing_uuid") or "")
                    if not uid or uid in seen:
                        continue
                    seen.add(uid)
                    merged.append(norm)

                next_u = data.get("next") if isinstance(data, dict) else None
                if not next_u:
                    break
                req_url = str(next_u)
                req_params = None

    return merged


def _normalize_filing_dict(raw: dict[str, Any]) -> dict[str, Any]:
    registrant = raw.get("registrant") if isinstance(raw.get("registrant"), dict) else {}
    client = raw.get("client") if isinstance(raw.get("client"), dict) else {}
    lobbyist_names: list[str] = []
    lobbying_activities_out: list[dict[str, Any]] = []

    for act in raw.get("lobbying_activities") or []:
        if not isinstance(act, dict):
            continue
        lobbying_activities_out.append(
            {
                "general_issue_code": act.get("general_issue_code"),
                "general_issue": act.get("general_issue_code_display")
                or act.get("general_issue_code"),
                "specific_issues": act.get("description"),
            }
        )
        for row in act.get("lobbyists") or []:
            if not isinstance(row, dict):
                continue
            lm = row.get("lobbyist")
            if not isinstance(lm, dict):
                continue
            parts = [
                lm.get("first_name"),
                lm.get("middle_name"),
                lm.get("last_name"),
                lm.get("suffix"),
            ]
            name = " ".join(str(p).strip() for p in parts if p).strip()
            if name and name.upper() not in {n.upper() for n in lobbyist_names}:
                lobbyist_names.append(name)

    return {
        "filing_uuid": raw.get("filing_uuid"),
        "registrant_name": registrant.get("name") or raw.get("registrant_name"),
        "client_name": client.get("name") or raw.get("client_name"),
        "filing_year": raw.get("filing_year"),
        "filing_period": raw.get("filing_period") or raw.get("filing_period_display"),
        "income": raw.get("income"),
        "expenses": raw.get("expenses"),
        "lobbyist_names": lobbyist_names,
        "lobbying_activities": lobbying_activities_out,
    }
