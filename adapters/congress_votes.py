from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter


class CongressVotesAdapter(BaseAdapter):
    source_name = "Congress.gov"
    BASE_URL = "https://api.congress.gov/v3"

    async def search(
        self,
        query: str,
        query_type: str = "bioguide_id",
    ) -> AdapterResponse:
        api_key = os.getenv("CONGRESS_API_KEY")
        if not api_key:
            return self._make_empty_response(
                query,
                error=(
                    "CONGRESS_API_KEY not set in .env — "
                    "get a free key at https://api.data.gov/signup/"
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
        params: dict[str, str | int] = {
            "api_key": api_key,
            "format": "json",
            "limit": 50,
            "offset": 0,
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/member/{bioguide_id}/votes",
                params=params,
            )
            if resp.status_code == 404:
                resp = await client.get(
                    f"{self.BASE_URL}/member/{bioguide_id}",
                    params=params,
                )
            data = resp.json()

        raw_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

        if resp.status_code >= 400:
            return AdapterResponse(
                source_name=self.source_name,
                query=bioguide_id,
                results=[],
                found=False,
                error=data.get("error", {}).get("message", resp.text[:200]),
                result_hash=raw_hash,
            )

        votes = (
            data.get("votes")
            or data.get("memberVotes")
            or data.get("results")
            or []
        )

        if isinstance(data.get("votes"), dict) and "vote" in data["votes"]:
            inner = data["votes"]["vote"]
            votes = inner if isinstance(inner, list) else [inner]

        if not votes:
            empty = self._make_empty_response(
                bioguide_id,
                parse_warning=(
                    "Congress.gov returned 200 OK but no vote rows were found in the response body."
                ),
            )
            empty.result_hash = raw_hash
            return empty

        results: list[AdapterResult] = []
        member_mismatch = False
        for vote in votes[:50]:
            if not isinstance(vote, dict):
                continue
            vr, bad = self._vote_to_result(vote, bioguide_id)
            if bad:
                member_mismatch = True
            if vr:
                results.append(vr)

        if not results:
            empty = self._make_empty_response(
                bioguide_id,
                parse_warning=(
                    "Congress.gov returned vote rows but none could be normalized into "
                    "structured vote_record entries."
                ),
            )
            empty.result_hash = raw_hash
            return empty

        pw = None
        if member_mismatch:
            pw = (
                "At least one vote row listed other members/bioguides than the requested "
                f"{bioguide_id}; spot-check raw votes before relying on timing."
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
            position = member_block.get("vote") or member_block.get("voteCast") or "Unknown"
        congress = vote.get("congress", "")
        session = vote.get("session", vote.get("sessionNumber", ""))

        mismatch = False
        seen: set[str] = set()
        if isinstance(member_block, dict):
            bid = member_block.get("bioguideId") or member_block.get("bioguide_id")
            if bid:
                seen.add(str(bid))
        for m in vote.get("members") or []:
            if isinstance(m, dict):
                bid = m.get("bioguideId") or m.get("bioguide_id")
                if bid:
                    seen.add(str(bid))
        if seen and bioguide_id not in seen:
            mismatch = True

        if not vote_date:
            return None, mismatch

        return (
            AdapterResult(
                source_name=self.source_name,
                source_url=(
                    f"https://www.congress.gov/member/"
                    f"{bioguide_id}?q=%7B%22role%22%3A%22legislator%22%7D"
                ),
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
        params = {
            "api_key": api_key,
            "format": "json",
            "query": name,
            "limit": 5,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.BASE_URL}/member", params=params)
            data = response.json()

        raw_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=str).encode()
        ).hexdigest()

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
                        title=f"Congress.gov: Multiple members match '{name}'",
                        body=(
                            f"Found {len(members)} congressional members matching '{name}'. "
                            f"Disambiguation required. Add bioguide_id to SubjectProfile "
                            f"to enable vote record lookup. Candidates: "
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
