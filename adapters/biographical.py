"""
Biographical data from Congress.gov v3 member profile.

https://api.congress.gov/v3/member/{bioguideId}
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.congress_gov_headers import CONGRESS_GOV_BROWSER_HEADERS
from core.credentials import CredentialRegistry, CredentialUnavailable

logger = logging.getLogger(__name__)

BASE = "https://api.congress.gov/v3/member"
HEADERS = CONGRESS_GOV_BROWSER_HEADERS


def _terms_as_list(member: dict[str, Any]) -> list[dict[str, Any]]:
    t = member.get("terms")
    if isinstance(t, list):
        return [x for x in t if isinstance(x, dict)]
    if isinstance(t, dict):
        items = t.get("item")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _address_items(member: dict[str, Any]) -> list[dict[str, Any]]:
    ai = member.get("addressInformation")
    if isinstance(ai, list):
        return [x for x in ai if isinstance(x, dict)]
    if isinstance(ai, dict):
        it = ai.get("item")
        if isinstance(it, list):
            return [x for x in it if isinstance(x, dict)]
    return []


def parse_member_data(member: dict[str, Any], bioguide_id: str) -> dict[str, Any]:
    """Map Congress.gov v3 ``member`` object to a persistable biographical profile dict."""
    full_name = str(member.get("directOrderName") or "").strip()
    if not full_name:
        first = str(member.get("firstName") or "").strip()
        last = str(member.get("lastName") or "").strip()
        full_name = f"{first} {last}".strip()

    by_raw = member.get("birthYear")
    birth_date: str | None = None
    if by_raw is not None:
        try:
            y = int(str(by_raw).strip()[:4])
            birth_date = f"{y}-01-01"
        except (TypeError, ValueError):
            pass

    party: str | None = None
    ph = member.get("partyHistory")
    if isinstance(ph, list) and ph:
        sorted_parties = sorted(
            (x for x in ph if isinstance(x, dict)),
            key=lambda x: int(x.get("startYear", 0) or 0),
            reverse=True,
        )
        if sorted_parties:
            party = str(sorted_parties[0].get("partyName") or "").strip() or None

    terms = _terms_as_list(member)
    sorted_terms = sorted(
        terms,
        key=lambda x: int(x.get("startYear") or 0),
        reverse=True,
    )
    current_office: str | None = None
    office_start_date: str | None = None
    previous_offices: list[dict[str, Any]] = []
    if sorted_terms:
        cur = sorted_terms[0]
        ch = str(cur.get("chamber") or "").strip()
        st = str(cur.get("stateCode") or cur.get("state") or "").strip()
        if ch and st:
            current_office = f"{ch} — {st}"
        elif ch:
            current_office = ch
        sy = cur.get("startYear")
        if sy is not None:
            try:
                y = int(sy)
                office_start_date = f"{y}-01-03"
            except (TypeError, ValueError):
                pass
        for term in sorted_terms[1:]:
            ch2 = str(term.get("chamber") or "").strip()
            st2 = str(term.get("stateCode") or term.get("state") or "").strip()
            s_y = term.get("startYear")
            e_y = term.get("endYear")
            label = f"{ch2} — {st2}" if (ch2 and st2) else (ch2 or st2)
            if label:
                previous_offices.append(
                    {
                        "office": label,
                        "start": s_y,
                        "end": e_y,
                        "jurisdiction": st2 or "Federal",
                    }
                )

    office_addresses: list[dict[str, Any]] = []
    for office in _address_items(member):
        office_addresses.append(
            {
                "type": str(office.get("officeCode") or ""),
                "street": str(office.get("street") or ""),
                "city": str(office.get("city") or ""),
                "state": str(office.get("stateCode") or ""),
                "zip": str(office.get("zipCode") or ""),
                "phone": str(office.get("phoneNumber") or ""),
            }
        )

    official_website = member.get("officialWebsiteUrl")
    if official_website is not None:
        official_website = str(official_website).strip() or None

    return {
        "bioguide_id": bioguide_id,
        "full_name": full_name or None,
        "birth_date": birth_date,
        "birth_city": None,
        "birth_state": str(member.get("state") or member.get("stateOfResidence") or "")[:2]
        or None,
        "party": party,
        "current_office": current_office,
        "office_start_date": office_start_date,
        "previous_offices": previous_offices or None,
        "education": None,
        "military_service": None,
        "employment_history": None,
        "office_addresses": office_addresses or None,
        "official_website": official_website,
        "social_media": None,
    }


def _result_hash(raw: dict[str, Any], bioguide: str) -> str:
    h = hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    return f"{bioguide}|{h}"


class BiographicalAdapter(BaseAdapter):
    source_name = "Biographical Profile"

    async def search(self, query: str, query_type: str = "bioguide") -> AdapterResponse:
        """``query`` = bioguide_id (e.g. Y000064)."""
        _ = query_type
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
        url = f"{BASE}/{bg}"
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.get(
                    url,
                    params={"api_key": api_key, "format": "json"},
                    headers=HEADERS,
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
        if resp.status_code == 404:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=True,
                empty_success=True,
                error=f"No member found for bioguide {bg}",
                error_kind="processing",
            )
        if resp.status_code != 200:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"Congress.gov member HTTP {resp.status_code}",
                error_kind="network",
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"Invalid JSON: {e}",
                error_kind="processing",
            )
        if not isinstance(data, dict):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Unexpected member response",
                error_kind="processing",
            )
        m = data.get("member")
        if not isinstance(m, dict):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=True,
                empty_success=True,
                error="No member object in response",
            )
        prof = parse_member_data(m, bg)
        rhash = _result_hash(prof, bg)
        www = "https://www.congress.gov/member/{}"
        list_url = www.format(bg)
        body_bits = [prof.get("full_name") or "", prof.get("current_office") or ""]
        body = " — ".join(b for b in body_bits if b) or "Member profile"
        res = [
            AdapterResult(
                source_name=self.source_name,
                source_url=list_url,
                entry_type="biographical_profile",
                title=f"Biography: {prof.get('full_name') or bg}",
                body=body,
                date_of_event=None,
                confidence="confirmed",
                raw_data=prof,
            )
        ]
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=res,
            found=True,
            result_hash=rhash,
            credential_mode="ok",
        )
