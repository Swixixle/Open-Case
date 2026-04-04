from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from core.credentials import CredentialRegistry

logger = logging.getLogger(__name__)

MAX_VOTE_RESULTS = 100
# Cap how many roll calls to walk backward if the member never appears (e.g. House-only).
MAX_ROLLS_SCAN = 400
# Upper bound for binary search for latest roll number per congress/session.
MAX_ROLL_CAP = 3500

SENATE_XML_BASE = "https://www.senate.gov/legislative/LIS/roll_call_votes"

DEFAULT_UA = (
    "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) "
    "Congressional-research bot"
)

# Senate LIS XML lists `lis_member_id`, not Bioguide. Optional overrides when Congress.gov
# profile match is unavailable. Verified from Senate vote XML (e.g. vote_119_1_00050.xml, 119th).
LIS_MEMBER_ID_BY_BIOGUIDE: dict[str, str] = {
    "B001306": "S429",  # Jim Banks, Indiana R
    "C000127": "S275",  # Maria Cantwell, Washington D
    "C000880": "S266",  # Mike Crapo, Idaho R
    "C001095": "S374",  # Tom Cotton, Arkansas R
    "E000295": "S376",  # Joni Ernst, Iowa R
    "G000386": "S153",  # Chuck Grassley, Iowa R
    "S001198": "S383",  # Dan Sullivan, Alaska R (not S000033 — that is Bernie Sanders)
    "S001181": "S324",  # Jeanne Shaheen, New Hampshire D
    "W000779": "S247",  # Ron Wyden, Oregon D
    "Y000064": "S391",  # Todd Young, Indiana R
}

# Congress.gov returns full state names; Senate XML uses postal abbreviations.
_US_STATE_ABBR: dict[str, str] = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}


def _congress_number_for_date(when: date | None = None) -> int:
    when = when or date.today()
    return (when.year - 1789) // 2 + 1


_AMENDMENT_TEXT_MARKERS = (
    "amendment",
    "amdt",
    "s.amdt",
    "h.amdt",
    "sa ",
    "ha ",
)


def _is_amendment_congress_vote_payload(vote_blob: dict[str, Any]) -> bool:
    q = str(vote_blob.get("question") or vote_blob.get("voteQuestion") or "").lower()
    desc = str(vote_blob.get("description") or vote_blob.get("voteDescription") or "").lower()
    if any(m in q or m in desc for m in _AMENDMENT_TEXT_MARKERS):
        return True
    am = vote_blob.get("amendment")
    if isinstance(am, dict) and (am.get("number") or am.get("amendmentNumber")):
        return True
    am2 = vote_blob.get("amendments") or vote_blob.get("amendmentNumber")
    if am2:
        return True
    lt = str(vote_blob.get("legislationType") or vote_blob.get("type") or "").upper()
    if "AMDT" in lt or "AMENDMENT" in lt:
        return True
    return False


def _normalize_amendment_vote_record(
    item: dict[str, Any], bioguide_id: str, default_congress: int
) -> dict[str, Any] | None:
    vb = item.get("vote")
    if isinstance(vb, dict):
        vote_core = {**item, **vb}
    else:
        vote_core = dict(item)
    if not _is_amendment_congress_vote_payload(vote_core):
        return None

    vd = (
        vote_core.get("date")
        or vote_core.get("voteDate")
        or vote_core.get("updateDate")
        or vote_core.get("startDate")
        or ""
    )
    vote_date = str(vd).strip()[:10] if vd else ""

    bill = vote_core.get("bill") or vote_core.get("legislation") or {}
    if not isinstance(bill, dict):
        bill = {}
    parent_bill = bill.get("number") or bill.get("billNumber")
    lt = bill.get("type") or bill.get("billType") or ""
    ln = bill.get("number") or bill.get("legislationNumber")
    bill_number = parent_bill
    if not bill_number and (lt or ln):
        bill_number = f"{lt} {ln}".strip()

    am = vote_core.get("amendment")
    if isinstance(am, dict):
        amendment_number = am.get("number") or am.get("amendmentNumber")
    else:
        amendment_number = vote_core.get("amendmentNumber") or vote_core.get("rollCall")

    description = (
        vote_core.get("question")
        or vote_core.get("voteQuestion")
        or vote_core.get("description")
        or vote_core.get("voteDescription")
        or ""
    )
    pos = (
        vote_core.get("position")
        or vote_core.get("voteCast")
        or vote_core.get("vote")
        or vote_core.get("memberVote")
        or ""
    )
    if isinstance(pos, dict):
        pos = pos.get("vote") or pos.get("voteCast") or ""

    ch = vote_core.get("chamber") or item.get("chamber") or ""
    src_url = (
        vote_core.get("url")
        or vote_core.get("voteUrl")
        or item.get("url")
        or f"https://www.congress.gov/member/{bioguide_id}"
    )

    out = {
        "vote_date": vote_date,
        "congress": int(vote_core.get("congress") or item.get("congress") or default_congress),
        "chamber": str(ch),
        "amendment_number": str(amendment_number) if amendment_number is not None else "",
        "bill_number": str(bill_number) if bill_number else "",
        "amendment_description": str(description)[:4000],
        "description": str(description)[:4000],
        "vote_position": str(pos).strip(),
        "position": str(pos).strip(),
        "vote_result": str(vote_core.get("result") or vote_core.get("voteResult") or ""),
        "source_url": str(src_url),
        "member_bioguide_id": bioguide_id,
        "entry_type": "amendment_vote",
    }
    if not vote_date:
        return None
    return out


async def fetch_amendment_votes_for_member(
    bioguide_id: str,
    *,
    congress: int = 119,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Amendment-focused votes for a member via Congress.gov v3.
    Returns normalized dicts suitable for EvidenceEntry.raw_data_json (pattern engine).
    """
    bg = (bioguide_id or "").strip()
    if not bg:
        return []
    key = (api_key or "").strip() or None
    if not key:
        try:
            key = CredentialRegistry.get_credential("congress")
        except Exception:
            key = None
    if not key:
        logger.info("fetch_amendment_votes_for_member: no congress API key, skipping")
        return []

    params: dict[str, str | int] = {
        "api_key": key,
        "format": "json",
        "limit": 250,
        "congress": congress,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": DEFAULT_UA}) as client:
            r = await client.get(
                f"https://api.congress.gov/v3/member/{bg}/votes",
                params=params,
            )
        if r.status_code >= 400:
            logger.warning(
                "fetch_amendment_votes_for_member HTTP %s for %s", r.status_code, bg
            )
            return []
        data = r.json()
    except Exception as e:
        logger.warning("fetch_amendment_votes_for_member request failed: %s", e)
        return []

    raw_list = data.get("votes") or data.get("memberVotes") or []
    if isinstance(raw_list, dict):
        inner = raw_list.get("vote") or raw_list.get("votes")
        if isinstance(inner, list):
            raw_list = inner
        elif isinstance(inner, dict):
            raw_list = [inner]
        else:
            raw_list = []
    if isinstance(raw_list, dict):
        raw_list = [raw_list]

    out: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        norm = _normalize_amendment_vote_record(item, bg, congress)
        if norm:
            out.append(norm)
    logger.info(
        "fetch_amendment_votes_for_member: %s amendment-shaped votes for %s",
        len(out),
        bg,
    )
    return out


def _senate_session_for_date(when: date | None = None) -> int:
    """First session in odd years, second in even years (simplified)."""
    when = when or date.today()
    return 1 if when.year % 2 == 1 else 2


def _vote_dir(congress: int, session: int) -> str:
    return f"vote{congress}{session}"


def _vote_xml_url(congress: int, session: int, roll: int) -> str:
    d = _vote_dir(congress, session)
    return f"{SENATE_XML_BASE}/{d}/vote_{congress}_{session}_{roll:05d}.xml"


async def _http_get_senate_roll(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """
    One retry on transport failure only (timeouts, connection errors).
    HTTP 4xx/5xx are returned to the caller; do not retry those here.
    """
    for attempt in range(2):
        try:
            return await client.get(url, timeout=15.0)
        except httpx.RequestError:
            if attempt == 0:
                await asyncio.sleep(2.0)
            else:
                raise
    raise RuntimeError("_http_get_senate_roll: unreachable")


def _is_roll_call_xml(body: str) -> bool:
    t = body.lstrip()[:120]
    return t.startswith("<?xml") or t.startswith("<roll_call_vote")


async def _binary_search_max_roll(
    client: httpx.AsyncClient, congress: int, session: int
) -> int:
    """Largest roll number with a valid roll_call_vote XML body."""

    async def exists(n: int) -> bool:
        url = _vote_xml_url(congress, session, n)
        r = await _http_get_senate_roll(client, url)
        if r.status_code != 200:
            return False
        return _is_roll_call_xml(r.text)

    if not await exists(1):
        return 0

    lo, hi = 1, MAX_ROLL_CAP
    best = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if await exists(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _parse_vote_date(raw: str) -> str:
    """Return 'YYYY-MM-DD' from e.g. 'January 9, 2025,  02:54 PM'."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    # First segment before second comma is "Month D, YYYY" in practice.
    parts = raw.split(",")
    if len(parts) >= 2:
        head = (parts[0] + "," + parts[1]).strip()
    else:
        head = parts[0]
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else ""


async def _fetch_member_identity(
    client: httpx.AsyncClient, bioguide_id: str
) -> dict[str, str] | None:
    key = CredentialRegistry.get_credential("congress")
    if not key:
        return None
    url = f"https://api.congress.gov/v3/member/{bioguide_id}"
    try:
        r = await client.get(url, params={"api_key": key, "format": "json"}, timeout=25.0)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    m = data.get("member")
    if not isinstance(m, dict):
        return None
    fn = (m.get("firstName") or "").strip()
    ln = (m.get("lastname") or m.get("lastName") or "").strip()
    st = (m.get("state") or "").strip()
    if not fn or not ln:
        return None
    return {"first": fn, "last": ln, "state": st}


def _identity_matches_member(
    profile: dict[str, str], first: str, last: str, state_abbr: str
) -> bool:
    abbr = _US_STATE_ABBR.get(profile["state"], profile["state"])[:2].upper()
    return (
        first.lower() == profile["first"].lower()
        and last.lower() == profile["last"].lower()
        and state_abbr.upper() == abbr
    )


def _find_member_vote(
    root: ET.Element,
    bioguide_id: str,
    profile: dict[str, str] | None,
) -> tuple[str, ET.Element | None]:
    """
    Returns (vote_cast or '', member element or None).
    Senate XML may include <bioguide_id>; otherwise LIS map or name/state profile.
    """
    bg_upper = bioguide_id.strip().upper()
    lis_expected = LIS_MEMBER_ID_BY_BIOGUIDE.get(bg_upper)

    for member in root.findall(".//members/member"):
        xml_bio = (member.findtext("bioguide_id") or "").strip()
        if xml_bio and xml_bio.upper() == bg_upper:
            return (member.findtext("vote_cast") or "").strip(), member

        lis = (member.findtext("lis_member_id") or "").strip()
        if lis_expected and lis == lis_expected:
            return (member.findtext("vote_cast") or "").strip(), member

        if profile:
            fn = (member.findtext("first_name") or "").strip()
            ln = (member.findtext("last_name") or "").strip()
            st = (member.findtext("state") or "").strip()
            if _identity_matches_member(profile, fn, ln, st):
                return (member.findtext("vote_cast") or "").strip(), member

    return "", None


def _member_display_name(
    member_el: ET.Element | None, profile: dict[str, str] | None
) -> str:
    """Human-readable legislator name for signal engine / evidence matched_name."""
    if member_el is not None:
        fn = (member_el.findtext("first_name") or "").strip()
        ln = (member_el.findtext("last_name") or "").strip()
        if fn or ln:
            return f"{fn} {ln}".strip()
        full = (member_el.findtext("member_full") or "").strip()
        if full:
            return full.split("(")[0].strip() or full
    if profile:
        return f"{profile.get('first', '')} {profile.get('last', '')}".strip()
    return ""


def _xml_to_vote_dict(
    root: ET.Element,
    bioguide_id: str,
    roll: int,
    position: str,
    source_url: str,
    member_el: ET.Element | None,
    profile: dict[str, str] | None,
) -> dict[str, Any]:
    congress = root.findtext("congress") or ""
    session = root.findtext("session") or ""
    raw_date = root.findtext("vote_date") or ""
    iso_date = _parse_vote_date(raw_date)
    doc = root.find("document")
    doc_type = doc.findtext("document_type") if doc is not None else ""
    doc_num = doc.findtext("document_number") if doc is not None else ""
    doc_name = doc.findtext("document_name") if doc is not None else ""
    doc_title = doc.findtext("document_title") if doc is not None else ""
    bill_label = (doc_name or f"{doc_type} {doc_num}".strip()).strip() or "Senate vote"
    bill_title = (doc_title or root.findtext("vote_document_text") or "").strip() or "—"

    display_name = _member_display_name(member_el, profile)

    vote_result_plain = (root.findtext("vote_result") or "").strip()
    vote_result_text = (root.findtext("vote_result_text") or "").strip()
    result_elem = (root.findtext("result") or "").strip()
    outcome = vote_result_text or vote_result_plain or result_elem

    vote_dict: dict[str, Any] = {
        "bioguide_id": bioguide_id,
        "member_name": display_name,
        "date": iso_date or raw_date,
        "voteDate": iso_date or raw_date,
        "position": position,
        "voteCast": position,
        "congress": congress,
        "session": session,
        "rollCallNumber": roll,
        "voteQuestion": (root.findtext("vote_question_text") or "").strip(),
        "question": (root.findtext("question") or "").strip(),
        "bill": {"number": bill_label, "title": bill_title},
        "document_type": (doc_type or "").strip(),
        "document_number": (doc_num or "").strip(),
        "source_url": source_url,
        "memberVote": {
            "bioguideId": bioguide_id,
            "vote": position,
        },
        "subject_is_sponsor": False,
        "subject_is_cosponsor": False,
    }
    if outcome:
        vote_dict["result"] = outcome
    if vote_result_plain:
        vote_dict["vote_result"] = vote_result_plain
    if vote_result_text:
        vote_dict["vote_result_text"] = vote_result_text
    return vote_dict


def _lis_document_to_api_bill(
    document_type: str, document_number: str, bill_label: str, question: str
) -> tuple[str, str] | None:
    """Return (api_bill_type, number_str) for Congress.gov /v3/bill/... or None."""
    blob = f"{bill_label} {question}".upper()
    if re.search(r"\bS\.?\s*AMDT|\bH\.?\s*AMDT|\bAMDT\.\s*NO", blob):
        return None
    if re.search(r"\bPN\s*#?\s*\d+", blob) or "NOMINATION" in blob:
        return None

    dt_raw = (document_type or "").strip()
    dt = dt_raw.upper().replace(" ", "")
    num = (document_number or "").strip()
    if not num.isdigit():
        m_num = re.search(
            r"(?i)\bS\.\s*J\.\s*R\.\s*(\d+)\b|\bH\.\s*J\.\s*R\.\s*(\d+)\b"
            r"|\bS\.\s*(\d+)\b|\bH\.?\s*R\.\s*(\d+)\b",
            bill_label or "",
        )
        if not m_num:
            return None
        groups = [g for g in m_num.groups() if g]
        if not groups:
            return None
        num = groups[0]
        raw = (m_num.group(0) or "").upper().replace(" ", "")
        blu = (bill_label or "").upper().replace(" ", "")
        if "H.J.R" in raw or "H.J.RES" in blu:
            return "hjres", num
        if "S.J.R" in raw or "S.J.RES" in blu:
            return "sjres", num
        if "H.R" in raw or raw.startswith("H."):
            return "hr", num
        return "s", num

    if "H.J.RES" in dt or re.match(r"H\.?\s*J\.?\s*R\.?", dt_raw, re.I):
        return "hjres", num
    if "S.J.RES" in dt or re.match(r"S\.?\s*J\.?\s*R\.?", dt_raw, re.I):
        return "sjres", num
    if dt.startswith("H.R") or dt.startswith("H.R."):
        return "hr", num
    if dt.startswith("S.") or dt == "S":
        return "s", num
    return None


def _bioguides_from_bill_endpoint(payload: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(payload, dict):
        return out
    raw = payload.get("sponsors")
    if raw is None:
        raw = payload.get("cosponsors")
    if raw is None:
        return out

    items: list[Any] | None
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        inner = raw.get("sponsor") or raw.get("cosponsor")
        if isinstance(inner, list):
            items = inner
        elif isinstance(inner, dict):
            items = [inner]
        else:
            items = None
    else:
        items = None
    if not items:
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        bid = it.get("bioguideId") or it.get("bioguide_id")
        if bid:
            out.add(str(bid).strip().upper())
    return out


async def _apply_cosponsorship_flags(
    client: httpx.AsyncClient, vote: dict[str, Any], bioguide_id: str
) -> None:
    vote.setdefault("subject_is_sponsor", False)
    vote.setdefault("subject_is_cosponsor", False)
    key = CredentialRegistry.get_credential("congress")
    if not key:
        return

    bg = bioguide_id.strip().upper()
    qtext = f"{vote.get('voteQuestion') or ''} {vote.get('question') or ''}"
    bill = vote.get("bill") or {}
    bill_label = bill.get("number") or ""

    try:
        cong_i = int(str(vote.get("congress") or "0"))
    except ValueError:
        return
    if cong_i <= 0:
        return

    parsed = _lis_document_to_api_bill(
        str(vote.get("document_type") or ""),
        str(vote.get("document_number") or ""),
        str(bill_label),
        qtext,
    )
    if not parsed:
        return
    btype, bnum = parsed
    if not bnum.isdigit():
        return

    base = f"https://api.congress.gov/v3/bill/{cong_i}/{btype}/{bnum}"
    params = {"api_key": key, "format": "json"}
    try:
        sp_resp = await client.get(f"{base}/sponsors", params=params, timeout=20.0)
        cs_resp = await client.get(f"{base}/cosponsors", params=params, timeout=20.0)
        sp_json = sp_resp.json() if sp_resp.status_code == 200 else {}
        cs_json = cs_resp.json() if cs_resp.status_code == 200 else {}
    except Exception:
        return

    sponsors = _bioguides_from_bill_endpoint(sp_json)
    cosponsors = _bioguides_from_bill_endpoint(cs_json)
    vote["subject_is_sponsor"] = bg in sponsors
    vote["subject_is_cosponsor"] = bool(bg in cosponsors and bg not in sponsors)


def _hash_raw(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


class CongressVotesAdapter(BaseAdapter):
    """U.S. Senate roll-call votes from Senate.gov public LIS XML (no API key)."""

    source_name = "U.S. Senate (LIS XML)"

    async def search(
        self,
        query: str,
        query_type: str = "bioguide_id",
    ) -> AdapterResponse:
        if query_type == "bioguide_id":
            return await self._fetch_senate_votes_by_bioguide(query)
        return await self._resolve_and_fetch(query)

    async def _fetch_senate_votes_by_bioguide(
        self, bioguide_id: str
    ) -> AdapterResponse:
        bioguide_id = bioguide_id.strip()
        when = date.today()
        congress = _congress_number_for_date(when)
        session = _senate_session_for_date(when)
        cred_mode = (
            "ok"
            if CredentialRegistry.get_credential("congress")
            else "credential_unavailable"
        )

        headers = {"User-Agent": DEFAULT_UA}

        async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
            profile = await _fetch_member_identity(client, bioguide_id)
            if not profile and not LIS_MEMBER_ID_BY_BIOGUIDE.get(bioguide_id.upper()):
                logger.warning(
                    "[CongressVotesAdapter] bioguide=%s no LIS override and no CONGRESS_API_KEY "
                    "identity — matching may fail",
                    bioguide_id,
                )

            max_roll = await _binary_search_max_roll(client, congress, session)
            pairs = [(congress, session, max_roll)]
            # Include prior session of same Congress if current has few rolls.
            if max_roll < MAX_VOTE_RESULTS and session == 2:
                prev_max = await _binary_search_max_roll(client, congress, 1)
                if prev_max > 0:
                    pairs.append((congress, 1, prev_max))

            collected: list[dict[str, Any]] = []
            misses = 0

            for c, s, hi in pairs:
                if not hi:
                    continue
                for roll in range(hi, 0, -1):
                    if len(collected) >= MAX_VOTE_RESULTS:
                        break
                    if misses >= MAX_ROLLS_SCAN:
                        break
                    url = _vote_xml_url(c, s, roll)
                    resp = await _http_get_senate_roll(client, url)
                    if resp.status_code != 200 or not _is_roll_call_xml(resp.text):
                        misses += 1
                        continue
                    try:
                        root = ET.fromstring(resp.text)
                    except ET.ParseError:
                        misses += 1
                        continue

                    pos, mem_el = _find_member_vote(root, bioguide_id, profile)
                    if not pos:
                        misses += 1
                        continue

                    vd = _xml_to_vote_dict(
                        root, bioguide_id, roll, pos, url, mem_el, profile
                    )
                    collected.append(vd)
                    if len(collected) == 1:
                        logger.debug(
                            "Senate LIS sample vote raw_data keys: %s",
                            sorted(vd.keys()),
                        )
                    misses = 0

                if len(collected) >= MAX_VOTE_RESULTS:
                    break

            for vote in collected:
                try:
                    await _apply_cosponsorship_flags(client, vote, bioguide_id)
                except Exception:
                    vote["subject_is_sponsor"] = False
                    vote["subject_is_cosponsor"] = False

        raw_hash = _hash_raw(
            {"bioguide": bioguide_id, "votes": collected, "congress": congress, "session": session}
        )

        if not collected:
            pw_parts = [
                "0 Senate LIS votes matched this bioguide (fetch and parse succeeded). "
                "Senate XML identifies members by `lis_member_id` and name/state — "
                "set CONGRESS_API_KEY for reliable matching, or add an entry to "
                "LIS_MEMBER_ID_BY_BIOGUIDE.",
                "House roll calls are not read by this adapter.",
            ]
            return AdapterResponse(
                source_name=self.source_name,
                query=bioguide_id,
                results=[],
                found=True,
                error=None,
                result_hash=raw_hash,
                parse_warning=" ".join(pw_parts),
                credential_mode=cred_mode,
                empty_success=True,
            )

        results: list[AdapterResult] = []
        for vote in collected[:MAX_VOTE_RESULTS]:
            vr, _ = self._vote_to_result(vote, bioguide_id)
            if vr:
                results.append(vr)

        pw = (
            "Vote rows are U.S. Senate roll calls only (Senate.gov XML). "
            "House votes are not included."
        )

        return AdapterResponse(
            source_name=self.source_name,
            query=bioguide_id,
            results=results,
            found=True,
            result_hash=raw_hash,
            parse_warning=pw,
            credential_mode=cred_mode,
        )

    def _vote_to_result(
        self, vote: dict[str, Any], bioguide_id: str
    ) -> tuple[AdapterResult | None, bool]:
        """Returns (result, member_identifier_mismatch)."""
        bill = vote.get("bill") or vote.get("legislation") or {}
        if not isinstance(bill, dict):
            bill = {}
        bill_number = bill.get("number") or bill.get("billNumber") or "Unknown bill"
        bill_title = bill.get("title") or bill.get("billTitle") or "Unknown"
        lt = vote.get("legislationType") or vote.get("billType")
        ln = vote.get("legislationNumber") or vote.get("billNumber")
        if bill_number == "Unknown bill" and (lt or ln):
            bill_number = f"{lt or ''} {ln or ''}".strip() or "Unknown bill"
        if bill_title == "Unknown":
            bill_title = (
                vote.get("description")
                or vote.get("voteQuestion")
                or vote.get("question")
                or vote.get("voteDescription")
                or "Unknown"
            )

        vote_date = (
            vote.get("date")
            or vote.get("voteDate")
            or vote.get("updateDate")
            or vote.get("startDate")
            or ""
        )
        member_block = vote.get("memberVote") or vote.get("voteCast") or vote
        position = "Unknown"
        if isinstance(member_block, dict):
            position = (
                member_block.get("vote") or member_block.get("voteCast") or "Unknown"
            )
        if position == "Unknown":
            vc = vote.get("voteCast") or vote.get("vote") or vote.get("position")
            if vc:
                position = str(vc)
        congress = vote.get("congress", "")
        session = vote.get("session", vote.get("sessionNumber", ""))

        mismatch = False
        seen: set[str] = set()
        mid = vote.get("member_id")
        if mid:
            seen.add(str(mid))
        if isinstance(member_block, dict):
            bid = member_block.get("bioguideId") or member_block.get("bioguide_id")
            if bid:
                seen.add(str(bid))
        for m in vote.get("members") or []:
            if isinstance(m, dict):
                bid = m.get("bioguideId") or m.get("bioguide_id")
                if bid:
                    seen.add(str(bid))
        if seen and str(bioguide_id) not in seen:
            mismatch = True

        if not vote_date:
            return None, mismatch

        source_url = vote.get("source_url") or vote.get("vote_uri")
        if not source_url:
            source_url = (
                f"https://www.congress.gov/member/"
                f"{bioguide_id}?q=%7B%22role%22%3A%22legislator%22%7D"
            )

        mn = (vote.get("member_name") or "").strip()
        matched_name = mn or vote.get("bioguide_id") or bioguide_id or None

        return (
            AdapterResult(
                source_name=self.source_name,
                source_url=source_url,
                entry_type="vote_record",
                title=f"Vote: {position} on {bill_number} ({congress}th Congress)",
                body=(
                    f"Voted {position} on bill {bill_number}: "
                    f"{str(bill_title)[:200]}. "
                    f"Date: {vote_date}. Congress: {congress}, Session: {session}."
                ),
                date_of_event=str(vote_date)[:10] if vote_date else None,
                confidence="confirmed",
                matched_name=str(matched_name) if matched_name else None,
                raw_data=dict(vote),
            ),
            mismatch,
        )

    async def _resolve_and_fetch(self, name: str) -> AdapterResponse:
        congress_key = CredentialRegistry.get_credential("congress")
        if not congress_key:
            raw_hash = _hash_raw({"mode": "name_resolve", "missing": "CONGRESS_API_KEY"})
            return AdapterResponse(
                source_name=self.source_name,
                query=name,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url="https://www.congress.gov/members",
                        entry_type="gap_documented",
                        title="Senate votes: name lookup needs Congress.gov key",
                        body=(
                            f"No CONGRESS_API_KEY — cannot resolve '{name}' to a bioguide. "
                            "Pass bioguide_id on the subject, or set CONGRESS_API_KEY."
                        ),
                        confidence="unverified",
                    )
                ],
                found=True,
                result_hash=raw_hash,
                credential_mode="credential_unavailable",
            )

        params = {
            "api_key": congress_key,
            "format": "json",
            "query": name,
            "limit": 5,
        }

        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": DEFAULT_UA}) as client:
            response = await client.get(
                "https://api.congress.gov/v3/member", params=params
            )
            data = response.json()

        raw_hash = _hash_raw(data)

        members = data.get("members") or []
        if isinstance(members, dict) and "member" in members:
            inner = members["member"]
            members = inner if isinstance(inner, list) else [inner]

        if not members:
            empty = self._make_empty_response(name)
            empty.result_hash = raw_hash
            empty.credential_mode = "ok"
            return empty

        if len(members) > 1:
            labels: list[str] = []
            for m in members[:5]:
                if not isinstance(m, dict):
                    continue
                nm = m.get("name") or m.get("directOrderName") or "?"
                bid = m.get("bioguideId") or m.get("bioguide_id") or "?"
                labels.append(f"{nm} ({bid})")
            return AdapterResponse(
                source_name=self.source_name,
                query=name,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url="https://www.congress.gov/members",
                        entry_type="gap_documented",
                        title=f"Multiple members match '{name}'",
                        body=(
                            f"Found {len(members)} congressional members matching '{name}'. "
                            "Disambiguate with bioguide_id on SubjectProfile. Candidates: "
                            + ", ".join(labels)
                        ),
                        confidence="unverified",
                        collision_count=len(members),
                        collision_set=labels,
                    )
                ],
                found=True,
                result_hash=raw_hash,
                credential_mode="ok",
            )

        m0 = members[0]
        if not isinstance(m0, dict):
            e = self._make_empty_response(name)
            e.credential_mode = "ok"
            return e
        bioguide_id = m0.get("bioguideId") or m0.get("bioguide_id")
        if bioguide_id:
            return await self._fetch_senate_votes_by_bioguide(str(bioguide_id))

        empty = self._make_empty_response(name)
        empty.result_hash = raw_hash
        empty.credential_mode = "ok"
        return empty
