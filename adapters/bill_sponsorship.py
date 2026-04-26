"""
Congress.gov v3 member sponsored and cosponsored legislation.
https://api.congress.gov/v3/member/{bioguideId}/sponsored-legislation
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from core.credentials import CredentialRegistry, CredentialUnavailable
logger = logging.getLogger(__name__)

BASE = "https://api.congress.gov/v3/member"
PER_PAGE = 25
MAX_BILLS_PER_ROLE = 120  # soft cap per endpoint to keep investigate bounded

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OpenCase/1.0) bill-sponsorship",
    "Accept": "application/json",
}


def _norm_bill_type(raw: str) -> str:
    t = (raw or "HR").strip().upper().replace(".", "")
    if not t:
        return "hr"
    return t.lower()[:16]


def _display_bill_number(bill_type: str, number: str) -> str:
    t = (bill_type or "HR").strip().upper()
    n = (number or "").strip()
    if t == "HR" or t == "H R":
        return f"H.R.{n}" if n else "H.R."
    if t == "S":
        return f"S.{n}" if n else "S."
    if t and n:
        return f"{t} {n}"
    return t or n or "Bill"


def public_congress_gov_bill_url(congress: int, bill_type: str, number: str) -> str:
    """Human-facing bill page on www.congress.gov (best effort: House vs Senate by bill type)."""
    t = (bill_type or "hr").strip().upper()
    n = (number or "").strip()
    c = int(congress)
    if t == "H.R." or t == "H R":
        t = "HR"
    if t.startswith("H") or t in ("HR", "HRES", "HJRES", "HCONRES"):
        path = "house-bill"
    else:
        path = "senate-bill"
    return f"https://www.congress.gov/bill/{c}th-congress/{path}/{n}"


def _parse_iso_date(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        return t
    return None


def _list_from_payload(
    data: dict[str, Any], *keys: str
) -> list[dict[str, Any]]:
    for k in keys:
        block = data.get(k)
        if block is None:
            continue
        if isinstance(block, list):
            return [x for x in block if isinstance(x, dict)]
        if isinstance(block, dict):
            inner = block.get("item") or block.get("legislation")
            if inner is None:
                return [block]
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
            if isinstance(inner, dict):
                return [inner]
    return []


async def _iter_bill_pages(
    client: httpx.AsyncClient, base_url: str, api_key: str, label: str
) -> list[dict[str, Any]]:
    """First page uses query params; ``pagination.next`` is followed as a full URL."""
    out: list[dict[str, Any]] = []
    next_url: str | None = base_url
    first = True
    while next_url and len(out) < MAX_BILLS_PER_ROLE:
        if first:
            resp = await client.get(
                next_url,
                params={
                    "api_key": api_key,
                    "format": "json",
                    "limit": PER_PAGE,
                },
                headers=HEADERS,
                timeout=45.0,
            )
            first = False
        else:
            resp = await client.get(next_url, headers=HEADERS, timeout=45.0)
        if resp.status_code in (401, 403, 404):
            logger.warning("Congress.gov %s: HTTP %s", label, resp.status_code)
            break
        if resp.status_code != 200:
            logger.warning("Congress.gov %s: HTTP %s", label, resp.status_code)
            break
        try:
            data = resp.json()
        except json.JSONDecodeError:
            break
        if not isinstance(data, dict):
            break
        if "error" in data:
            logger.warning("Congress.gov %s error: %s", label, data.get("error"))
            break
        items = _list_from_payload(
            data, "sponsoredLegislation", "cosponsoredLegislation", "bills", "legislation"
        )
        out.extend(items)
        pag = data.get("pagination")
        nxt = pag.get("next") if isinstance(pag, dict) else None
        next_url = str(nxt) if nxt else None
        if not items:
            break
    return out


def _result_hash_from_rows(rows: list[dict[str, Any]], bioguide: str) -> str:
    h = hashlib.sha256(
        json.dumps(
            [sorted(r.items()) for r in rows], sort_keys=True, default=str
        ).encode()
    ).hexdigest()[:32]
    return f"{bioguide}|{h}"


class BillSponsorshipAdapter(BaseAdapter):
    source_name = "Congress.gov (Bill Activity)"

    async def search(self, query: str, query_type: str = "both") -> AdapterResponse:
        """
        ``query`` = bioguide_id (e.g. Y000064).
        ``query_type`` = ``sponsored`` | ``cosponsored`` | ``both``.
        """
        bg = (query or "").strip().upper()
        if not re.match(r"^[A-Z][0-9]{6}$", bg):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="query must be a 7-char bioguide_id (e.g. Y000064)",
                error_kind="processing",
            )
        try:
            api_key = CredentialRegistry.get_credential("congress")
        except CredentialUnavailable:
            api_key = None
        if not (api_key or "").strip():
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Congress.gov API key not configured (CredentialRegistry 'congress')",
                error_kind="credential",
            )
        api_key = str(api_key).strip()
        mode = (query_type or "both").lower().strip()
        if mode not in ("sponsored", "cosponsored", "both"):
            mode = "both"
        all_rows: list[AdapterResult] = []
        sp_url = f"{BASE}/{bg}/sponsored-legislation"
        cs_url = f"{BASE}/{bg}/cosponsored-legislation"
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                if mode in ("sponsored", "both"):
                    spon = await _iter_bill_pages(
                        client, sp_url, api_key, "sponsored"
                    )
                    all_rows.extend(self._bills_to_results(spon, bg, "sponsor"))
                if mode in ("cosponsored", "both"):
                    cosp = await _iter_bill_pages(
                        client, cs_url, api_key, "cosponsored"
                    )
                    all_rows.extend(
                        self._bills_to_results(cosp, bg, "cosponsor")
                    )
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
                error_kind="network",
            )
        rhash = _result_hash_from_rows(
            [r.raw_data for r in all_rows if isinstance(r.raw_data, dict)], bg
        )
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=all_rows,
            found=True,
            result_hash=rhash,
            empty_success=not bool(all_rows),
            parse_warning=None
            if all_rows
            else f"No bills returned for {bg} (or key lacks access). Modes: {mode}.",
        )

    def _bills_to_results(
        self, bills: list[dict[str, Any]], bioguide: str, role: str
    ) -> list[AdapterResult]:
        out: list[AdapterResult] = []
        for b in bills:
            if not isinstance(b, dict):
                continue
            congress = b.get("congress")
            if congress is None:
                continue
            try:
                cnum = int(congress)
            except (TypeError, ValueError):
                continue
            btype = _norm_bill_type(str(b.get("type") or b.get("billType") or "HR"))
            number = str(b.get("number") or "").strip()
            title = (b.get("title") or b.get("displayTitle") or "Untitled").strip()[:2000]
            display_num = _display_bill_number(
                str(b.get("type") or b.get("billType") or "HR").strip()[:16] or "HR",
                number,
            )
            intro = _parse_iso_date(
                str(b.get("introducedDate") or b.get("updateDate") or "")[:20]
            )
            cosd = _parse_iso_date(
                str(b.get("cosponsoredDate") or b.get("actionDate") or "")
            ) if role == "cosponsor" else None
            pol = b.get("policyArea") or b.get("subjectPolicyArea") or {}
            if isinstance(pol, dict):
                subj = str(pol.get("name") or pol.get("policyArea", "") or "")[:128] or None
            else:
                subj = str(pol)[:128] if pol else None
            latest = b.get("latestAction") or {}
            if isinstance(latest, dict):
                st = str(latest.get("text") or latest.get("actionTime") or "")[:64] or None
            else:
                st = None
            src = public_congress_gov_bill_url(cnum, btype, number)
            bu = b.get("url")
            if (
                isinstance(bu, str)
                and bu.startswith("http")
                and "www.congress.gov" in bu
            ):
                src = bu
            prefix = "Sponsored" if role == "sponsor" else "Cosponsored"
            short_title = title[:200] + ("…" if len(title) > 200 else "")
            ev_title = f"{prefix}: {display_num} - {short_title}"
            body_bits = [title, ""]
            if subj:
                body_bits.append(f"Policy: {subj}.")
            if st:
                body_bits.append(f"Status: {st}.")
            body = " ".join(x for x in body_bits if x).strip()[:8000]
            raw: dict[str, Any] = {
                "bioguide_id": bioguide,
                "bill_number": display_num,
                "congress": cnum,
                "bill_type": btype,
                "role": "sponsor" if role == "sponsor" else "cosponsor",
                "title": title,
                "introduced_date": intro,
                "cosponsored_date": cosd,
                "current_status": st,
                "subject_policy_area": subj,
                "source_url": src,
            }
            raw["bill"] = b
            out.append(
                AdapterResult(
                    source_name=self.source_name,
                    source_url=src,
                    entry_type="bill_sponsorship",
                    title=ev_title,
                    body=body,
                    date_of_event=intro,
                    confidence="confirmed",
                    raw_data=raw,
                )
            )
        return out
