"""
Congress.gov v3 Congressional Record listing (per-issue PDFs for a Congress).
``/v3/congressional-record`` returns daily *issues* with links to PDFs; speech-level
text is in those documents. We store one row per issue as ``floor_speech`` context.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.congress_gov_headers import CONGRESS_GOV_BROWSER_HEADERS
from adapters.govinfo_hearings import current_congress_number
from core.credentials import CredentialRegistry, CredentialUnavailable

BASE = "https://api.congress.gov/v3"
MAX_ISSUES = 100
HEADERS = CONGRESS_GOV_BROWSER_HEADERS

CREC_WARNING = (
    "Congress.gov returns Congressional Record *issues* (daily volumes) for this "
    "Congress; member-specific remarks are located within the linked PDF sections."
)


def _issues_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    root = data.get("Results") or data.get("results")
    if not isinstance(root, dict):
        return []
    issues = root.get("Issues") or root.get("issues")
    if not isinstance(issues, list):
        return []
    return [x for x in issues if isinstance(x, dict)]


def _int_field(x: Any) -> int:
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return 0


def _parse_date(s: str | None) -> str | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    if len(t) == 10 and t[4] == "-":
        return t
    return None


def _chamber_label(links: Any) -> str:
    if not isinstance(links, dict):
        return "Congress"
    has_s = bool(links.get("Senate") or any("Senate" in k for k in links))
    has_h = bool(links.get("House") or "House" in links)
    if has_s and not has_h:
        return "Senate"
    if has_h and not has_s:
        return "House"
    return "Congress"


def _full_record_url(links: Any) -> str | None:
    if not isinstance(links, dict):
        return None
    fr = links.get("FullRecord") or links.get("fullRecord")
    if isinstance(fr, dict):
        pdfs = fr.get("PDF") or fr.get("pdf")
        if isinstance(pdfs, list) and pdfs and isinstance(pdfs[0], dict):
            u = pdfs[0].get("Url") or pdfs[0].get("url")
            if isinstance(u, str) and u.startswith("http"):
                return u
    return None


def _crec_directory_url(full_pdf: str, congress: int) -> str:
    """Folder-style CREC page under ``/crec/…`` when the API provides a PDF URL."""
    if full_pdf.startswith("http") and "/crec/" in full_pdf:
        return full_pdf.rsplit("/", 1)[0] + "/"
    return f"https://www.congress.gov/congressional-record/{int(congress)}th-congress"


def _crec_issue_folder_from_metadata(
    congress: int, publish_date: str | None, volume: int, issue: int
) -> str | None:
    """
    When the API omits ``FullRecord`` PDF links, build the directory path Congress.gov
    uses: ``/{congress}/crec/{YYYY}/{MM}/{DD}/{volume}/{issue}/``.
    """
    pub = publish_date or ""
    if len(pub) != 10 or pub[4] != "-" or pub[7] != "-":
        return None
    if volume <= 0 or issue <= 0:
        return None
    y, mo, d = pub[0:4], pub[5:7], pub[8:10]
    return f"https://www.congress.gov/{int(congress)}/crec/{y}/{mo}/{d}/{volume}/{issue}/"


def _row_hash_id(rows: list[dict[str, Any]], bg: str) -> str:
    h = hashlib.sha256(
        json.dumps(rows, default=str, sort_keys=True).encode()
    ).hexdigest()[:32]
    return f"{bg}|{h}"


class FloorSpeechesAdapter(BaseAdapter):
    source_name = "Congressional Record"
    BASE_URL = "https://api.congress.gov/v3"

    async def search(self, query: str, query_type: str = "bioguide") -> AdapterResponse:
        """
        ``query`` = bioguide_id (7 letters). Searches the Congressional Record
        for the current Congress, passing ``bioguideId`` as requested by the API.
        """
        _ = query_type
        bg = (query or "").strip().upper()
        if not re.match(r"^[A-Z][0-9]{6}$", bg):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="query must be a 7-char bioguide_id",
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
        cong = current_congress_number()
        url = f"{BASE}/congressional-record"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(
                    url,
                    params={
                        "api_key": api_key,
                        "format": "json",
                        "congress": cong,
                        "bioguideId": bg,
                        "limit": MAX_ISSUES,
                    },
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
        if r.status_code == 429:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Congress.gov rate limit",
                error_kind="rate_limited",
            )
        if r.status_code not in (200, 201):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"HTTP {r.status_code} from congressional-record",
                error_kind="network",
            )
        try:
            data = r.json()
        except json.JSONDecodeError:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Invalid JSON from congressional-record",
                error_kind="processing",
            )
        if isinstance(data, dict) and data.get("error"):
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(data.get("error")),
                error_kind="processing",
            )
        issues = _issues_from_response(data)[:MAX_ISSUES]
        out: list[AdapterResult] = []
        for iss in issues:
            vol = _int_field(iss.get("Volume") or iss.get("volume"))
            num = _int_field(iss.get("Issue") or iss.get("issue") or iss.get("number"))
            c_iss = _int_field(iss.get("Congress") or cong)
            pub = _parse_date(str(iss.get("PublishDate") or iss.get("publishDate") or ""))
            if not pub:
                continue
            links = iss.get("Links") or iss.get("links")
            chamber = _chamber_label(links)
            full_pdf = _full_record_url(links)
            if not full_pdf:
                full_pdf = _crec_issue_folder_from_metadata(c_iss, pub, vol, num) or (
                    f"https://www.congress.gov/crec/{c_iss}/"
                )
            source_page = _crec_directory_url(full_pdf, c_iss)
            tags = [f"congress={c_iss}", f"bioguide={bg}", "crec_issue"]
            excerpt = (
                f"Congressional Record issue published {pub}. "
                f"Volume {vol}, number {num}, chamber context: {chamber}. "
                f"Open the full-record PDF to locate this member’s remarks. "
            )[:500]
            title = f"Floor Speech: Congressional Record — {pub} (Vol. {vol}, No. {num})"
            raw: dict[str, Any] = {
                "bioguide_id": bg,
                "congress": c_iss,
                "chamber": chamber,
                "speech_date": pub,
                "volume": vol,
                "number": num,
                "page_range": None,
                "title": title,
                "excerpt": excerpt,
                "full_text_url": full_pdf,
                "topic_tags": json.dumps(tags, ensure_ascii=False),
                "source_url": source_page,
                "data_note": CREC_WARNING,
                "crec_issue": iss,
            }
            out.append(
                AdapterResult(
                    source_name=self.source_name,
                    source_url=source_page,
                    entry_type="floor_speech",
                    title=title,
                    body=excerpt,
                    date_of_event=pub,
                    confidence="confirmed",
                    raw_data=raw,
                )
            )
        rhash = _row_hash_id([o.raw_data for o in out if o.raw_data], bg)
        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=out,
            found=True,
            result_hash=rhash,
            empty_success=not bool(out),
            parse_warning=f"No CREC issues returned for Congress {cong}." if not out else None,
        )
