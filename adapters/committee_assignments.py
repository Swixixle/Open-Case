"""
Congress.gov v3 — member profile committee assignments.
Uses GET /v3/member/{bioguideId} (``committeeAssignments`` on ``member``), with
GET /v3/member/{bioguideId}/committee-assignments as a fallback.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.govinfo_hearings import current_congress_number
from core.credentials import CredentialRegistry, CredentialUnavailable

logger = logging.getLogger(__name__)

BASE = "https://api.congress.gov/v3"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OpenCase/1.0) committee-assignments",
    "Accept": "application/json",
}


def public_congress_gov_url_from_api(api_url: str) -> str:
    """Map ``api.congress.gov/v3/...`` to ``www.congress.gov/...`` when possible."""
    u = (api_url or "").strip()
    if u.startswith("https://api.congress.gov/v3/"):
        return "https://www.congress.gov/" + u.split("/v3/", 1)[1]
    if u.startswith("http"):
        return u
    return u


def _list_assignments(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in (
        "committeeAssignments",
        "committees",
        "assignments",
    ):
        block = data.get(key)
        if block is None:
            continue
        if isinstance(block, list):
            return [x for x in block if isinstance(x, dict)]
        if isinstance(block, dict):
            inner = block.get("item") or block.get("assignments")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
            if isinstance(inner, dict):
                return [inner]
    return []


def _norm_chamber(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if "house" in low:
        return "House"
    if "senate" in low:
        return "Senate"
    if low in ("h", "house"):
        return "House"
    if low in ("s", "senate"):
        return "Senate"
    return s[:16]


def _parse_iso(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        return t
    return None


def _code_from(a: dict[str, Any]) -> str:
    for k in (
        "systemCode",
        "code",
        "committeeCode",
        "thomasId",
    ):
        v = a.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()[:16]
    return "unknown"


def _name_from(a: dict[str, Any]) -> str:
    return (a.get("name") or a.get("title") or a.get("committeeName") or "Committee")[
        :256
    ]


def _com_type_from(a: dict[str, Any]) -> str | None:
    t = a.get("committeeType") or a.get("type") or a.get("committeeTypeName")
    if t is None:
        return None
    s = str(t).strip()[:32]
    return s or None


def _rank_from(a: dict[str, Any]) -> int | None:
    for k in ("rank", "partyMemberRank", "memberRank", "seniority"):
        v = a.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def is_current_filter(a: dict[str, Any]) -> bool:
    """``current`` mode: drop assignments whose service clearly ended in the past."""
    end = _parse_iso(str(a.get("endDate") or a.get("end") or ""))
    if not end:
        return True
    try:
        return date.fromisoformat(end) >= date.today()
    except ValueError:
        return True


def _result_hash(rows: list[dict[str, Any]], bioguide: str) -> str:
    h = hashlib.sha256(
        json.dumps(
            [sorted((r or {}).items()) for r in rows], sort_keys=True, default=str
        ).encode()
    ).hexdigest()[:32]
    return f"{bioguide}|{h}"


class CommitteeAssignmentsAdapter(BaseAdapter):
    source_name = "Congress.gov (Committees)"
    BASE_URL = "https://api.congress.gov/v3"

    async def search(self, query: str, query_type: str = "current") -> AdapterResponse:
        """
        ``query`` = bioguide_id (7-letter pattern).
        ``query_type`` = ``current`` (filter likely stale rows) or ``all``.
        """
        bg = (query or "").strip().upper()
        if not re.match(r"^[A-Z][0-9]{6}$", bg):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="query must be a 7-char bioguide_id (e.g. L000174)",
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
        qmode = (query_type or "current").lower().strip()
        if qmode not in ("current", "all"):
            qmode = "current"
        cur_c = current_congress_number()
        all_rows: list[AdapterResult] = []
        raw_items: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                member_url = f"{BASE}/member/{bg}"
                r = await client.get(
                    member_url,
                    params={"api_key": api_key, "format": "json"},
                    headers=HEADERS,
                    timeout=45.0,
                )
                if r.status_code != 200:
                    logger.warning(
                        "committee member GET HTTP %s for %s", r.status_code, bg
                    )
                data = r.json() if r.status_code == 200 else {}
                m = data.get("member") if isinstance(data, dict) else None
                member_obj = m if isinstance(m, dict) else (data if isinstance(data, dict) else {})
                raw_items = _list_assignments(member_obj)
                if not raw_items and isinstance(data, dict):
                    raw_items = _list_assignments(data)

                if not raw_items:
                    sub_url = f"{BASE}/member/{bg}/committee-assignments"
                    r2 = await client.get(
                        sub_url,
                        params={"api_key": api_key, "format": "json", "limit": 100},
                        headers=HEADERS,
                        timeout=45.0,
                    )
                    if r2.status_code == 200:
                        try:
                            d2 = r2.json()
                        except json.JSONDecodeError:
                            d2 = {}
                        raw_items = _list_assignments(d2 if isinstance(d2, dict) else {})
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
                error_kind="network",
            )

        for a in raw_items:
            if qmode == "current" and not is_current_filter(a):
                continue
            for res in _assignment_to_results(self, a, bg, cur_c):
                all_rows.append(res)

        rhash = _result_hash(
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
            else f"No committee rows returned for {bg}.",
        )


def _infer_chamber(a: dict[str, Any]) -> str:
    c = _norm_chamber(str(a.get("chamber") or a.get("chamberName") or ""))
    if c:
        return c
    scu = str(a.get("systemCode") or a.get("code") or "").upper()
    if len(scu) >= 2 and scu[0] == "S" and scu[1] == "S":
        return "Senate"
    if len(scu) >= 2 and scu[0] == "H" and scu[1] == "S":
        return "House"
    if scu.startswith("S") and not scu.startswith("H"):
        return "Senate"
    return "House"


def _assignment_to_results(
    adapter: CommitteeAssignmentsAdapter, a: dict[str, Any], bioguide: str, default_cong: int
) -> list[AdapterResult]:
    out: list[AdapterResult] = []
    cong: int
    try:
        cong = int(a.get("congress") or default_cong)
    except (TypeError, ValueError):
        cong = default_cong
    ch = _infer_chamber(a)
    code = _code_from(a)
    cname = _name_from(a)
    ctype = _com_type_from(a)
    rk = _rank_from(a)
    sdt = _parse_iso(str(a.get("startDate") or a.get("start") or ""))
    edt = _parse_iso(str(a.get("endDate") or a.get("end") or ""))

    # Nested subcommittees: emit parent + children
    subs = a.get("subcommittees")
    if isinstance(subs, list) and subs:
        parent_url = a.get("url")
        src0 = public_congress_gov_url_from_api(str(parent_url or "")) or f"https://www.congress.gov/member/{bioguide}/committees"
        ptitle = f"Committee: {cname}"
        pbody = _body_bits(ctype, rk, sdt, edt, cong, ch, None)
        raw_p = _raw_dict(
            bioguide, cong, ch, code, cname, ctype, None, rk, sdt, edt, src0, a, False
        )
        out.append(
            AdapterResult(
                source_name=adapter.source_name,
                source_url=src0,
                entry_type="committee_assignment",
                title=ptitle,
                body=pbody,
                date_of_event=sdt,
                confidence="confirmed",
                raw_data=raw_p,
            )
        )
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            s_code = _code_from(sub) or code
            s_n = _name_from(sub)[:256]
            su = sub.get("url") or parent_url
            src_s = public_congress_gov_url_from_api(str(su or "")) or src0
            stitle = f"Subcommittee: {cname} - {s_n}"
            sbody = _body_bits(ctype, _rank_from(sub) or rk, sdt, edt, cong, ch, s_n)
            raw_s = _raw_dict(
                bioguide,
                cong,
                ch,
                s_code,
                cname,
                ctype,
                s_n,
                _rank_from(sub) or rk,
                sdt,
                edt,
                src_s,
                sub,
                True,
            )
            raw_s["parent_committee_code"] = code
            out.append(
                AdapterResult(
                    source_name=adapter.source_name,
                    source_url=src_s,
                    entry_type="committee_assignment",
                    title=stitle,
                    body=sbody,
                    date_of_event=sdt,
                    confidence="confirmed",
                    raw_data=raw_s,
                )
            )
        return out

    parent = str(
        a.get("parentName")
        or a.get("parentCommitteeName")
        or a.get("parent", "")
    ).strip()
    if parent:
        sub_name = cname
        cname = parent[:256]
        title = f"Subcommittee: {cname} - {sub_name}"
    else:
        sub_name = None
        title = f"Committee: {cname}"
    u = a.get("url")
    src = public_congress_gov_url_from_api(str(u or "")) or f"https://www.congress.gov/member/{bioguide}/committees"
    body = _body_bits(ctype, rk, sdt, edt, cong, ch, sub_name)
    raw = _raw_dict(
        bioguide, cong, ch, code, cname, ctype, sub_name, rk, sdt, edt, src, a, bool(sub_name)
    )
    out.append(
        AdapterResult(
            source_name=adapter.source_name,
            source_url=src,
            entry_type="committee_assignment",
            title=title,
            body=body,
            date_of_event=sdt,
            confidence="confirmed",
            raw_data=raw,
        )
    )
    return out


def _body_bits(
    ctype: str | None,
    rk: int | None,
    sdt: str | None,
    edt: str | None,
    cong: int,
    chamber: str,
    sub: str | None,
) -> str:
    parts: list[str] = []
    if ctype:
        parts.append(f"Type: {ctype}.")
    if rk is not None:
        parts.append(f"Rank (party list): {rk}.")
    if chamber:
        parts.append(f"Chamber: {chamber}.")
    parts.append(f"Congress: {cong}.")
    if sdt or edt:
        parts.append(
            f"Service: {sdt or '?'} to {edt or 'present'}."
        )
    if sub:
        parts.append(f"Subcommittee: {sub}.")
    return " ".join(parts)[:8000]


def _raw_dict(
    bioguide: str,
    cong: int,
    chamber: str,
    code: str,
    cname: str,
    ctype: str | None,
    sub_name: str | None,
    rk: int | None,
    sdt: str | None,
    edt: str | None,
    src: str,
    full: dict[str, Any],
    is_sub: bool,
) -> dict[str, Any]:
    return {
        "bioguide_id": bioguide,
        "congress": cong,
        "chamber": chamber,
        "committee_code": code,
        "committee_name": cname,
        "committee_type": ctype,
        "subcommittee_name": sub_name,
        "rank_in_party": rk,
        "start_date": sdt,
        "end_date": edt,
        "source_url": src,
        "is_subcommittee": is_sub,
        "committee": full,
    }
