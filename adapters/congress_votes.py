from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    return re.sub(r"(api_key=)([^&]+)", r"\1***", url, flags=re.IGNORECASE)


def _coerce_vote_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for inner_key in ("vote", "votes", "item", "items", "houseRollCallVote"):
            inner = raw.get(inner_key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                return [inner]
    return []


def _extract_vote_rows(data: dict[str, Any]) -> tuple[list[Any], str]:
    """
    Congress.gov v3 envelopes vary by endpoint. Try documented / observed keys,
    then fall back to any vote-like top-level list.
    """
    tried: list[str] = []

    named_paths: list[tuple[str, Any]] = [
        ("votes", data.get("votes")),
        ("memberVotes", data.get("memberVotes")),
        ("results", data.get("results")),
        ("houseRollCallVotes", data.get("houseRollCallVotes")),
        ("houseRollCallVote", data.get("houseRollCallVote")),
        ("member-votes", data.get("member-votes")),
        ("senateRollCallVotes", data.get("senateRollCallVotes")),
        ("rollCallVotes", data.get("rollCallVotes")),
    ]
    for path, raw in named_paths:
        if raw is None:
            continue
        votes = _coerce_vote_list(raw)
        tried.append(f"{path}→{len(votes)}")
        if votes:
            return votes, path

    skip_keys = frozenset({"request", "pagination", "error", "errors"})
    for k, v in data.items():
        if k in skip_keys:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            sample = v[0]
            if any(
                key in sample
                for key in (
                    "voteDate",
                    "startDate",
                    "updateDate",
                    "date",
                    "bill",
                    "legislation",
                    "rollCallNumber",
                    "voteCast",
                    "memberVote",
                    "result",
                )
            ):
                return v, f"heuristic:{k}"

    for k, v in data.items():
        if k in skip_keys:
            continue
        if isinstance(v, dict):
            for ik, iv in v.items():
                votes = _coerce_vote_list(iv)
                tried.append(f"{k}.{ik}→{len(votes)}")
                if votes:
                    return votes, f"{k}.{ik}"

    logger.warning(
        "[CongressVotesAdapter DEBUG] vote row extraction failed; tried: %s",
        ", ".join(tried) if tried else "(no candidate keys)",
    )
    return [], "none"


def _debug_log_congress_json_shape(
    *,
    bioguide_id: str,
    status_code: int,
    endpoint_label: str,
    data: Any,
) -> None:
    """Temporary diagnostics: response shape for Congress.gov member votes parsing."""
    if not isinstance(data, dict):
        logger.warning(
            "[CongressVotesAdapter DEBUG] bioguide=%s endpoint=%s status=%s body_type=%s body_preview=%r",
            bioguide_id,
            endpoint_label,
            status_code,
            type(data).__name__,
            (str(data)[:300] + "…") if len(str(data)) > 300 else str(data),
        )
        return

    keys = sorted(data.keys())
    logger.warning(
        "[CongressVotesAdapter DEBUG] bioguide=%s endpoint=%s status=%s top_level_keys=%s",
        bioguide_id,
        endpoint_label,
        status_code,
        keys,
    )

    found_list: tuple[str, list[Any]] | None = None
    for name, val in data.items():
        if isinstance(val, list) and len(val) > 0:
            found_list = (name, val)
            break

    if found_list is None:
        for name, val in data.items():
            if isinstance(val, dict):
                for inner_k, inner_v in val.items():
                    if isinstance(inner_v, list) and len(inner_v) > 0:
                        found_list = (f"{name}.{inner_k}", inner_v)
                        break
            if found_list is not None:
                break

    if found_list is None:
        logger.warning(
            "[CongressVotesAdapter DEBUG] bioguide=%s no non-empty list at top level or one dict level down",
            bioguide_id,
        )
        return

    list_path, lst = found_list
    first = lst[0]
    if isinstance(first, dict):
        logger.warning(
            "[CongressVotesAdapter DEBUG] bioguide=%s first_list_path=%r list_len=%d first_item_keys=%s",
            bioguide_id,
            list_path,
            len(lst),
            sorted(first.keys()),
        )
    else:
        logger.warning(
            "[CongressVotesAdapter DEBUG] bioguide=%s first_list_path=%r list_len=%d first_item=%r",
            bioguide_id,
            list_path,
            len(lst),
            (str(first)[:400] + "…") if len(str(first)) > 400 else first,
        )


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

        votes_url = f"{self.BASE_URL}/member/{bioguide_id}/votes"
        member_url = f"{self.BASE_URL}/member/{bioguide_id}"
        endpoint_used = "member/bioguide/votes"
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(votes_url, params=params)
            logger.warning(
                "[CongressVotesAdapter DEBUG] bioguide=%s GET votes status=%s url=%s",
                bioguide_id,
                resp.status_code,
                _mask_url(str(resp.request.url)),
            )
            if resp.status_code == 404:
                endpoint_used = "member/bioguide (fallback after votes 404)"
                resp = await client.get(member_url, params=params)
                logger.warning(
                    "[CongressVotesAdapter DEBUG] bioguide=%s GET member fallback status=%s url=%s",
                    bioguide_id,
                    resp.status_code,
                    _mask_url(str(resp.request.url)),
                )
            try:
                data = resp.json()
            except Exception as e:
                logger.warning(
                    "[CongressVotesAdapter DEBUG] bioguide=%s endpoint=%s status=%s json_parse_error=%s text_preview=%r",
                    bioguide_id,
                    endpoint_used,
                    resp.status_code,
                    e,
                    (resp.text[:500] + "…") if len(resp.text) > 500 else resp.text,
                )
                raise

            _debug_log_congress_json_shape(
                bioguide_id=bioguide_id,
                status_code=resp.status_code,
                endpoint_label=endpoint_used,
                data=data,
            )

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

        if not isinstance(data, dict):
            logger.warning(
                "[CongressVotesAdapter DEBUG] bioguide=%s response not a dict; preview=%r",
                bioguide_id,
                (str(data)[:1500] + "…") if len(str(data)) > 1500 else data,
            )
            empty = self._make_empty_response(
                bioguide_id,
                parse_warning="Congress.gov JSON was not an object; cannot parse votes.",
            )
            empty.result_hash = raw_hash
            return empty

        votes, vote_path = _extract_vote_rows(data)
        logger.warning(
            "[CongressVotesAdapter DEBUG] bioguide=%s vote_path=%r vote_row_count=%s",
            bioguide_id,
            vote_path,
            len(votes),
        )

        if not votes:
            preview = json.dumps(data, default=str, indent=2)
            if len(preview) > 4000:
                preview = preview[:4000] + "\n…(truncated)"
            logger.warning(
                "[CongressVotesAdapter DEBUG] bioguide=%s no vote rows — raw JSON shape:\n%s",
                bioguide_id,
                preview,
            )
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
        lt = vote.get("legislationType") or vote.get("billType")
        ln = vote.get("legislationNumber") or vote.get("billNumber")
        if bill_number == "Unknown bill" and (lt or ln):
            bill_number = f"{lt or ''} {ln or ''}".strip() or "Unknown bill"
        if bill_title == "Unknown":
            bill_title = (
                vote.get("voteQuestion")
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
            position = member_block.get("vote") or member_block.get("voteCast") or "Unknown"
        if position == "Unknown":
            vc = vote.get("voteCast") or vote.get("vote") or vote.get("position")
            if vc:
                position = str(vc)
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
