from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import httpx

from adapters.regulations import LEGAL_NOISE, _normalize_tokens

logger = logging.getLogger(__name__)

GOVINFO_BASE = "https://api.govinfo.gov/"


def current_congress_number(d: date | None = None) -> int:
    """Approximate sitting Congress number for the given date."""
    y = (d or date.today()).year
    return max(1, (y - 1788) // 2)


def _flatten_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj + "\n"
    if isinstance(obj, dict):
        return "".join(_flatten_text(v) for v in obj.values())
    if isinstance(obj, list):
        return "".join(_flatten_text(v) for v in obj)
    return ""


def _token_match_in_blob(
    blob: str, donor_name: str, connected_org_name: str
) -> tuple[str | None, str | None]:
    lb = (blob or "").lower()
    for label in (donor_name, connected_org_name):
        n = (label or "").strip()
        if len(n) < 3:
            continue
        if n.lower() in lb:
            return "confirmed", n

    text_tokens: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", lb):
        if w not in LEGAL_NOISE and len(w) > 2:
            text_tokens.add(w)
    if not text_tokens:
        return None, None

    for label in (donor_name, connected_org_name):
        dtoks = _normalize_tokens(label)
        if not dtoks:
            continue
        inter = dtoks & text_tokens
        union = dtoks | text_tokens
        if union and len(inter) / len(union) >= 0.6:
            return "probable", (label or "").strip()
    return None, None


def _package_ids_from_collection(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in data.get("results") or data.get("packages") or []:
        if isinstance(row, dict):
            pid = row.get("packageId") or row.get("package_id")
            if pid:
                out.append(str(pid))
    return out


async def search_hearing_witnesses(
    donor_name: str,
    connected_org_name: str,
    committee_codes: list[str],
    congress: int,
    api_key: str | None,
) -> dict[str, Any]:
    """
    Search recent CHRG (hearing) packages for donor / org mentions.
    Stops at first match. Returns hits (0 or 1), searched, and matched flags.
    """
    result: dict[str, Any] = {"hits": [], "searched": False, "matched": False}
    if not api_key:
        return result

    codes = [str(c).strip() for c in (committee_codes or []) if str(c).strip()]
    if not codes:
        return result

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OpenCase/1.0) "
            "congressional-research"
        )
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=45.0) as client:
        for code in codes:
            try:
                r = await client.get(
                    f"{GOVINFO_BASE}collections/CHRG",
                    params={
                        "api_key": api_key,
                        "pageSize": 20,
                        "congress": str(congress),
                        "committeeCode": code,
                    },
                )
                r.raise_for_status()
                coll = r.json()
            except Exception as e:
                logger.warning("[govinfo] collection fetch failed %s: %s", code, e)
                continue

            if not isinstance(coll, dict):
                continue
            result["searched"] = True
            pids = _package_ids_from_collection(coll)
            for package_id in pids[:20]:
                try:
                    sr = await client.get(
                        f"{GOVINFO_BASE}packages/{package_id}/summary",
                        params={"api_key": api_key},
                    )
                    sr.raise_for_status()
                    summary = sr.json()
                except Exception as e:
                    logger.warning("[govinfo] summary fetch failed %s: %s", package_id, e)
                    continue

                if not isinstance(summary, dict):
                    continue
                title = str(summary.get("title") or "")
                blob = title + "\n" + _flatten_text(summary)
                conf, matched = _token_match_in_blob(blob, donor_name, connected_org_name)
                if conf:
                    date_issued = str(
                        summary.get("dateIssued")
                        or summary.get("issued")
                        or summary.get("lastModified")
                        or ""
                    )
                    hit = {
                        "package_id": package_id,
                        "hearing_title": title,
                        "committee_code": code,
                        "date_issued": date_issued,
                        "matched_name": matched or "",
                        "match_confidence": conf,
                        "source_url": f"https://www.govinfo.gov/app/details/{package_id}",
                    }
                    result["hits"] = [hit]
                    result["matched"] = True
                    return result

    if result["searched"]:
        result["matched"] = False
    return result
