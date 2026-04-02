from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from data.industry_jurisdiction_map import get_agencies_for_committees

logger = logging.getLogger(__name__)

REGULATIONS_BASE = "https://api.regulations.gov/v4/"

LEGAL_NOISE = [
    "inc",
    "llc",
    "pac",
    "corp",
    "corporation",
    "association",
    "foundation",
    "fund",
    "company",
    "co",
    "ltd",
    "group",
    "holdings",
    "enterprises",
    "international",
]


def _normalize_tokens(name: str) -> set[str]:
    # Split embedded caps (e.g. MassMutual → Mass Mutual) before lowercasing.
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    tokens = name.split()
    return {t for t in tokens if t not in LEGAL_NOISE and len(t) > 2}


def _match_confidence(donor_name: str, submitter_name: str) -> str | None:
    if not submitter_name:
        return None
    if donor_name.lower().strip() == submitter_name.lower().strip():
        return "confirmed"
    donor_tokens = _normalize_tokens(donor_name)
    submitter_tokens = _normalize_tokens(submitter_name)
    if not donor_tokens or not submitter_tokens:
        return None
    intersection = donor_tokens & submitter_tokens
    union = donor_tokens | submitter_tokens
    score = len(intersection) / len(union)
    if score >= 0.6:
        return "probable"
    return None


def _best_match_for_comment(
    donor_name: str,
    connected_org_name: str,
    submitter_name: str,
    organization: str,
) -> str | None:
    candidates: list[str | None] = [
        _match_confidence(donor_name, submitter_name),
        _match_confidence(donor_name, organization or ""),
        _match_confidence(connected_org_name, submitter_name),
        _match_confidence(connected_org_name, organization or ""),
    ]
    if "confirmed" in candidates:
        return "confirmed"
    if "probable" in candidates:
        return "probable"
    return None


def _parse_comment_item(
    item: dict[str, Any],
    donor_name: str,
    connected_org_name: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    attr = item.get("attributes")
    if not isinstance(attr, dict):
        return None
    submitter = str(attr.get("submitterName") or "").strip()
    organization = str(attr.get("organization") or "").strip()
    conf = _best_match_for_comment(donor_name, connected_org_name, submitter, organization)
    if conf is None:
        return None
    posted = attr.get("postedDate") or attr.get("lastModifiedDate") or ""
    docket_id = str(attr.get("docketId") or "")
    agg_agency = str(attr.get("agencyId") or attr.get("commentOnId") or "")
    title = str(attr.get("docketTitle") or attr.get("title") or "")
    return {
        "comment_id": str(item.get("id") or ""),
        "docket_id": docket_id,
        "agency_id": agg_agency,
        "submitted_date": posted,
        "submitter_name": submitter,
        "organization": organization,
        "docket_title": title,
        "match_confidence": conf,
    }


async def _fetch_comments_page(
    client: httpx.AsyncClient,
    api_key: str,
    extra_params: dict[str, str],
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        **extra_params,
        "page[size]": "25",
        "api_key": api_key,
    }
    url = f"{REGULATIONS_BASE}comments"
    try:
        r = await client.get(url, params=params, timeout=40.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("[regulations] request failed %s: %s", extra_params, e)
        return []
    items = data.get("data")
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


async def fetch_docket_comments(
    donor_name: str,
    connected_org_name: str,
    senator_committees: list[str],
    api_key: str | None,
) -> list[dict]:
    """
    Match Regulations.gov comments to donor / org using committee-derived agencies.
    Returns normalized dicts only for submitter/org matches.
    """
    if not api_key:
        return []

    agencies = get_agencies_for_committees(senator_committees)
    names: list[str] = []
    for n in (donor_name, connected_org_name):
        q = re.sub(r"\s+", " ", (n or "").strip())
        if len(q) >= 2 and q not in names:
            names.append(q)
    if not names:
        return []

    seen_comment: set[str] = set()
    out: list[dict] = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OpenCase/1.0) "
            "congressional-research"
        )
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        agency_loop = agencies if agencies else [None]
        for agency in agency_loop:
            for nm in names:
                if agency:
                    param_sets: list[dict[str, str]] = [
                        {"filter[agencyId]": agency, "filter[searchTerm]": nm},
                        {"filter[commentOnId]": agency, "filter[submitter]": nm},
                        {"filter[organization]": nm},
                    ]
                else:
                    param_sets = [
                        {"filter[searchTerm]": nm},
                        {"filter[organization]": nm},
                    ]
                for ps in param_sets:
                    raw_items = await _fetch_comments_page(client, api_key, ps)
                    for item in raw_items:
                        parsed = _parse_comment_item(item, donor_name, connected_org_name)
                        if not parsed:
                            continue
                        cid = parsed["comment_id"]
                        if not cid or cid in seen_comment:
                            continue
                        seen_comment.add(cid)
                        out.append(parsed)

    return out
