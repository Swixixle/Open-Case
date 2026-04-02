from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

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
# profile match is unavailable. Extend as needed.
LIS_MEMBER_ID_BY_BIOGUIDE: dict[str, str] = {
    "Y000064": "S391",  # Todd Young — verified in 119th roll call XML
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


def _senate_session_for_date(when: date | None = None) -> int:
    """First session in odd years, second in even years (simplified)."""
    when = when or date.today()
    return 1 if when.year % 2 == 1 else 2


def _vote_dir(congress: int, session: int) -> str:
    return f"vote{congress}{session}"


def _vote_xml_url(congress: int, session: int, roll: int) -> str:
    d = _vote_dir(congress, session)
    return f"{SENATE_XML_BASE}/{d}/vote_{congress}_{session}_{roll:05d}.xml"


def _is_roll_call_xml(body: str) -> bool:
    t = body.lstrip()[:120]
    return t.startswith("<?xml") or t.startswith("<roll_call_vote")


async def _binary_search_max_roll(
    client: httpx.AsyncClient, congress: int, session: int
) -> int:
    """Largest roll number with a valid roll_call_vote XML body."""

    async def exists(n: int) -> bool:
        url = _vote_xml_url(congress, session, n)
        try:
            r = await client.get(url)
        except httpx.HTTPError:
            return False
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
    key = os.getenv("CONGRESS_API_KEY")
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

    return {
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
        "source_url": source_url,
        "memberVote": {
            "bioguideId": bioguide_id,
            "vote": position,
        },
    }


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
        try:
            if query_type == "bioguide_id":
                return await self._fetch_senate_votes_by_bioguide(query)
            return await self._resolve_and_fetch(query)
        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
            )

    async def _fetch_senate_votes_by_bioguide(
        self, bioguide_id: str
    ) -> AdapterResponse:
        bioguide_id = bioguide_id.strip()
        when = date.today()
        congress = _congress_number_for_date(when)
        session = _senate_session_for_date(when)

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
                    try:
                        resp = await client.get(url)
                    except httpx.HTTPError:
                        misses += 1
                        continue
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

                    collected.append(
                        _xml_to_vote_dict(
                            root, bioguide_id, roll, pos, url, mem_el, profile
                        )
                    )
                    misses = 0

                if len(collected) >= MAX_VOTE_RESULTS:
                    break

        raw_hash = _hash_raw(
            {"bioguide": bioguide_id, "votes": collected, "congress": congress, "session": session}
        )

        if not collected:
            pw_parts = [
                "No Senate LIS votes matched this bioguide. "
                "Senate XML identifies members by `lis_member_id` and name/state — "
                "set CONGRESS_API_KEY for reliable matching, or add an entry to "
                "LIS_MEMBER_ID_BY_BIOGUIDE.",
                "House roll calls are not read by this adapter.",
            ]
            empty = self._make_empty_response(bioguide_id, parse_warning=" ".join(pw_parts))
            empty.result_hash = raw_hash
            empty.found = False
            return empty

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
        congress_key = os.getenv("CONGRESS_API_KEY")
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
            )

        m0 = members[0]
        if not isinstance(m0, dict):
            return self._make_empty_response(name)
        bioguide_id = m0.get("bioguideId") or m0.get("bioguide_id")
        if bioguide_id:
            return await self._fetch_senate_votes_by_bioguide(str(bioguide_id))

        empty = self._make_empty_response(name)
        empty.result_hash = raw_hash
        return empty
