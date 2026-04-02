from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

logger = logging.getLogger(__name__)

MAX_VOTE_RESULTS = 50

# ProPublica returns up to 20 vote rows per request for member votes.
PROPUBLICA_VOTES_PAGE_SIZE = 20


class CongressVotesAdapter(BaseAdapter):
    """Member roll-call votes via ProPublica Congress API (House + Senate)."""

    source_name = "ProPublica Congress API"
    BASE_URL = "https://api.propublica.org/congress/v1"

    async def search(
        self,
        query: str,
        query_type: str = "bioguide_id",
    ) -> AdapterResponse:
        api_key = os.getenv("PROPUBLICA_API_KEY")
        if not api_key:
            return self._make_empty_response(
                query,
                error=(
                    "PROPUBLICA_API_KEY not set — "
                    "get a free key at "
                    "https://www.propublica.org/datastore/api/propublica-congress-api"
                ),
            )

        try:
            if query_type == "bioguide_id":
                return await self._fetch_by_bioguide(query, api_key)
            return await self._resolve_and_fetch(query, api_key)
        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=str(e),
            )

    async def _fetch_by_bioguide(
        self, bioguide_id: str, api_key: str
    ) -> AdapterResponse:
        headers = {"X-API-Key": api_key}
        collected: list[dict[str, Any]] = []
        offset = 0
        last_payload: dict[str, Any] = {}
        total_votes_hint = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            while len(collected) < MAX_VOTE_RESULTS:
                url = f"{self.BASE_URL}/members/{bioguide_id}/votes.json"
                resp = await client.get(
                    url, headers=headers, params={"offset": offset}
                )
                try:
                    data = resp.json()
                except Exception as e:
                    logger.warning(
                        "[CongressVotesAdapter] bioguide=%s propublica json_error=%s preview=%r",
                        bioguide_id,
                        e,
                        (resp.text[:500] + "…") if len(resp.text) > 500 else resp.text,
                    )
                    raise

                last_payload = data if isinstance(data, dict) else {}
                logger.warning(
                    "[CongressVotesAdapter] bioguide=%s propublica status=%s http=%s",
                    bioguide_id,
                    last_payload.get("status"),
                    resp.status_code,
                )

                if resp.status_code >= 400:
                    msg = _propublica_error_message(data)
                    raw_hash = _hash_raw({"error": msg, "body": data})
                    return AdapterResponse(
                        source_name=self.source_name,
                        query=bioguide_id,
                        results=[],
                        found=False,
                        error=msg,
                        result_hash=raw_hash,
                    )

                if not isinstance(data, dict) or data.get("status") != "OK":
                    msg = _propublica_error_message(data)
                    raw_hash = _hash_raw(data if isinstance(data, dict) else {"raw": data})
                    return AdapterResponse(
                        source_name=self.source_name,
                        query=bioguide_id,
                        results=[],
                        found=False,
                        error=msg or "ProPublica response was not OK.",
                        result_hash=raw_hash,
                    )

                results_block = data.get("results")
                if (
                    not isinstance(results_block, list)
                    or not results_block
                    or not isinstance(results_block[0], dict)
                ):
                    break

                block = results_block[0]
                votes = block.get("votes") or []
                if not isinstance(votes, list):
                    break

                try:
                    total_votes_hint = int(block.get("total_votes") or 0)
                except (TypeError, ValueError):
                    total_votes_hint = 0

                page_added = 0
                for v in votes:
                    if len(collected) >= MAX_VOTE_RESULTS:
                        break
                    if isinstance(v, dict):
                        collected.append(_enrich_propublica_vote(v, bioguide_id))
                        page_added += 1

                if page_added == 0:
                    break

                offset += page_added
                if total_votes_hint and offset >= total_votes_hint:
                    break
                if len(votes) < PROPUBLICA_VOTES_PAGE_SIZE:
                    break

        raw_hash = _hash_raw(
            {"bioguideId": bioguide_id, "votes": collected, "api": last_payload}
        )

        if not collected:
            empty = self._make_empty_response(
                bioguide_id,
                parse_warning=(
                    "ProPublica returned no vote rows for this member (or pagination "
                    "produced an empty first page)."
                ),
            )
            empty.result_hash = raw_hash
            return empty

        out: list[AdapterResult] = []
        for vote in collected[:MAX_VOTE_RESULTS]:
            vr, _ = self._vote_to_result(vote, bioguide_id)
            if vr:
                out.append(vr)

        if not out:
            empty = self._make_empty_response(
                bioguide_id,
                parse_warning="ProPublica votes could not be normalized to vote_record entries.",
            )
            empty.result_hash = raw_hash
            return empty

        return AdapterResponse(
            source_name=self.source_name,
            query=bioguide_id,
            results=out,
            found=True,
            result_hash=raw_hash,
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

        bill_uri = bill.get("bill_uri") if isinstance(bill, dict) else None
        source_url = vote.get("vote_uri") or bill_uri
        if not source_url:
            source_url = (
                f"https://www.congress.gov/member/"
                f"{bioguide_id}?q=%7B%22role%22%3A%22legislator%22%7D"
            )

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
                raw_data=dict(vote),
            ),
            mismatch,
        )

    async def _resolve_and_fetch(self, name: str, api_key: str) -> AdapterResponse:
        """
        Resolve a name to bioguide via Congress.gov (optional), then load ProPublica votes.
        """
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
                        title="Member votes: name lookup needs Congress.gov key",
                        body=(
                            f"No CONGRESS_API_KEY — cannot resolve '{name}' to a bioguide. "
                            "Set PROPUBLICA_API_KEY for votes and CONGRESS_API_KEY for "
                            "Congress.gov member search, or pass bioguide_id on the subject."
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

        async with httpx.AsyncClient(timeout=20.0) as client:
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
            return await self._fetch_by_bioguide(str(bioguide_id), api_key)

        empty = self._make_empty_response(name)
        empty.result_hash = raw_hash
        return empty


def _hash_raw(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def _propublica_error_message(data: Any) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, str):
            return err[:500]
        if isinstance(err, dict):
            return str(err.get("message") or err)[:500]
        if data.get("status") and data.get("status") != "OK":
            return str(data.get("status"))[:200]
    return ""


def _enrich_propublica_vote(vote: dict[str, Any], bioguide_id: str) -> dict[str, Any]:
    """Copy for raw_data safety and set fields _vote_to_result expects."""
    out = dict(vote)
    pos = vote.get("position")
    out["memberVote"] = {
        "bioguideId": bioguide_id,
        "vote": pos if pos is not None else vote.get("voteCast"),
    }
    return out
