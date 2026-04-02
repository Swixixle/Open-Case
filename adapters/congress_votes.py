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

# Congress.gov v3 documents house roll calls only (no public senate-vote or member votes URL).
MAX_VOTE_RESULTS = 50
MAX_ROLL_CALL_DETAIL_FETCHES = 160


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


def _congress_api_error_message(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)[:300]
    err = data.get("error")
    if isinstance(err, str):
        return err[:500]
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:500]
    return str(data.get("message", data))[:300]


def _member_has_senate_service(member: dict[str, Any]) -> bool:
    for t in member.get("terms") or []:
        if not isinstance(t, dict):
            continue
        if t.get("chamber") == "Senate":
            return True
    return False


def _house_congress_session_pairs(member: dict[str, Any]) -> list[tuple[int, int]]:
    """(congress, session) for House terms, recent congresses and session 2 first."""
    house_congresses: list[int] = []
    for t in member.get("terms") or []:
        if not isinstance(t, dict):
            continue
        if t.get("chamber") != "House of Representatives":
            continue
        c = t.get("congress")
        if isinstance(c, int):
            house_congresses.append(c)
    ordered = sorted(set(house_congresses), reverse=True)
    pairs: list[tuple[int, int]] = []
    for c in ordered:
        pairs.append((c, 2))
        pairs.append((c, 1))
    return pairs


def _bioguide_matches_row(row: dict[str, Any], bioguide_id: str) -> bool:
    rid = row.get("bioguideID") or row.get("bioguideId") or ""
    return str(rid).upper() == str(bioguide_id).upper()


def _synthetic_vote_from_house_roll(
    roll: dict[str, Any], member_row: dict[str, Any], bioguide_id: str
) -> dict[str, Any]:
    leg_num = roll.get("legislationNumber")
    leg_type = roll.get("legislationType")
    title_bits = [
        roll.get("amendmentAuthor"),
        roll.get("voteType"),
        roll.get("result"),
    ]
    title_guess = next((x for x in title_bits if x), "House roll call vote")
    bill: dict[str, Any] = {
        "number": (
            f"{leg_type} {leg_num}".strip() if leg_type and leg_num else "House vote"
        ),
        "title": title_guess,
    }
    if leg_num:
        bill["billNumber"] = str(leg_num)
    vote_date = roll.get("startDate") or roll.get("updateDate") or ""
    position = member_row.get("voteCast") or "Unknown"
    return {
        "congress": roll.get("congress"),
        "session": roll.get("sessionNumber"),
        "sessionNumber": roll.get("sessionNumber"),
        "date": vote_date,
        "voteDate": vote_date,
        "rollCallNumber": roll.get("rollCallNumber"),
        "bill": bill,
        "legislationNumber": leg_num,
        "legislationType": leg_type,
        "legislationUrl": roll.get("legislationUrl"),
        "sourceDataURL": roll.get("sourceDataURL"),
        "voteQuestion": title_guess,
        "memberVote": {
            "bioguideId": bioguide_id,
            "vote": position,
        },
        "voteCast": position,
    }


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
        base_params = {"api_key": api_key, "format": "json"}

        async with httpx.AsyncClient(timeout=45.0) as client:
            member_url = f"{self.BASE_URL}/member/{bioguide_id}"
            resp = await client.get(member_url, params=base_params)
            try:
                member_payload = resp.json()
            except Exception as e:
                logger.warning(
                    "[CongressVotesAdapter DEBUG] bioguide=%s member json_error=%s preview=%r",
                    bioguide_id,
                    e,
                    (resp.text[:500] + "…") if len(resp.text) > 500 else resp.text,
                )
                raise

            logger.warning(
                "[CongressVotesAdapter DEBUG] bioguide=%s GET member status=%s url=%s",
                bioguide_id,
                resp.status_code,
                _mask_url(str(resp.request.url)),
            )
            _debug_log_congress_json_shape(
                bioguide_id=bioguide_id,
                status_code=resp.status_code,
                endpoint_label="member/bioguide",
                data=member_payload,
            )

            err_early = None
            if resp.status_code >= 400:
                err_early = _congress_api_error_message(member_payload)
            elif not isinstance(member_payload, dict):
                err_early = "Congress.gov member response was not a JSON object."

            raw_for_hash = {
                "member": member_payload if isinstance(member_payload, dict) else {},
                "strategy": "house-vote/members",
                "bioguideId": bioguide_id,
            }

            if err_early:
                raw_hash = hashlib.sha256(
                    json.dumps(raw_for_hash, sort_keys=True, default=str).encode()
                ).hexdigest()
                return AdapterResponse(
                    source_name=self.source_name,
                    query=bioguide_id,
                    results=[],
                    found=False,
                    error=err_early,
                    result_hash=raw_hash,
                )

            member_obj = member_payload.get("member")
            if not isinstance(member_obj, dict):
                raw_hash = hashlib.sha256(
                    json.dumps(raw_for_hash, sort_keys=True, default=str).encode()
                ).hexdigest()
                empty = self._make_empty_response(
                    bioguide_id,
                    parse_warning="member payload missing .member object.",
                )
                empty.result_hash = raw_hash
                return empty

            pairs = _house_congress_session_pairs(member_obj)
            has_senate = _member_has_senate_service(member_obj)

            if not pairs:
                msg = (
                    "No House service on record for this member in Congress.gov; "
                    "the v3 API only exposes House roll-call member votes "
                    "(house-vote/…/members). Senate per-member votes are not available "
                    "on this endpoint."
                )
                raw_hash = hashlib.sha256(
                    json.dumps(
                        {**raw_for_hash, "house_pairs": [], "note": msg},
                        sort_keys=True,
                        default=str,
                    ).encode()
                ).hexdigest()
                empty = self._make_empty_response(bioguide_id, parse_warning=msg)
                empty.result_hash = raw_hash
                return empty

            collected: list[dict[str, Any]] = []
            lookups = 0

            for congress, session in pairs:
                if len(collected) >= MAX_VOTE_RESULTS or lookups >= MAX_ROLL_CALL_DETAIL_FETCHES:
                    break
                offset = 0
                page_limit = 100
                while (
                    len(collected) < MAX_VOTE_RESULTS
                    and lookups < MAX_ROLL_CALL_DETAIL_FETCHES
                ):
                    list_params: dict[str, str | int] = {
                        **base_params,
                        "limit": page_limit,
                        "offset": offset,
                    }
                    list_url = f"{self.BASE_URL}/house-vote/{congress}/{session}"
                    list_resp = await client.get(list_url, params=list_params)
                    try:
                        list_data = list_resp.json()
                    except Exception:
                        logger.warning(
                            "[CongressVotesAdapter DEBUG] house-vote list parse fail %s/%s offset=%s",
                            congress,
                            session,
                            offset,
                        )
                        break

                    if list_resp.status_code >= 400:
                        logger.warning(
                            "[CongressVotesAdapter DEBUG] house-vote list status=%s %s/%s msg=%s",
                            list_resp.status_code,
                            congress,
                            session,
                            _congress_api_error_message(list_data),
                        )
                        break

                    batch = list_data.get("houseRollCallVotes") or []
                    pag = list_data.get("pagination") or {}
                    total = int(pag.get("count") or 0)

                    for roll in batch:
                        if len(collected) >= MAX_VOTE_RESULTS or lookups >= MAX_ROLL_CALL_DETAIL_FETCHES:
                            break
                        if not isinstance(roll, dict):
                            continue
                        rcn = roll.get("rollCallNumber")
                        if rcn is None:
                            continue
                        lookups += 1
                        member_row = await self._fetch_house_vote_member_row(
                            client,
                            congress,
                            session,
                            int(rcn),
                            bioguide_id,
                            api_key,
                        )
                        if member_row is None:
                            continue
                        collected.append(
                            _synthetic_vote_from_house_roll(roll, member_row, bioguide_id)
                        )

                    offset += len(batch)
                    if not batch or offset >= total:
                        break

            raw_for_hash["collected_roll_calls"] = collected
            raw_hash = hashlib.sha256(
                json.dumps(raw_for_hash, sort_keys=True, default=str).encode()
            ).hexdigest()

            logger.warning(
                "[CongressVotesAdapter DEBUG] bioguide=%s house_vote_rows=%s roll_lookups=%s "
                "pairs_tried=%s",
                bioguide_id,
                len(collected),
                lookups,
                pairs,
            )

            if not collected:
                empty = self._make_empty_response(
                    bioguide_id,
                    parse_warning=(
                        "No House roll-call rows matched this bioguide after scanning "
                        f"available house-vote listings (lookups={lookups})."
                    ),
                )
                empty.result_hash = raw_hash
                return empty

            results: list[AdapterResult] = []
            for vote in collected[:MAX_VOTE_RESULTS]:
                vr, _bad = self._vote_to_result(vote, bioguide_id)
                if vr:
                    results.append(vr)

            pw_parts: list[str] = []
            if has_senate:
                pw_parts.append(
                    "Congress.gov v3 does not publish Senate roll-call member votes; "
                    "only House votes from this member's House terms are included."
                )

            return AdapterResponse(
                source_name=self.source_name,
                query=bioguide_id,
                results=results,
                found=True,
                result_hash=raw_hash,
                parse_warning=" ".join(pw_parts) if pw_parts else None,
            )

    async def _fetch_house_vote_member_row(
        self,
        client: httpx.AsyncClient,
        congress: int,
        session: int,
        roll_call_number: int,
        bioguide_id: str,
        api_key: str,
    ) -> dict[str, Any] | None:
        offset = 0
        page_limit = 250
        url = f"{self.BASE_URL}/house-vote/{congress}/{session}/{roll_call_number}/members"

        while True:
            params: dict[str, str | int] = {
                "api_key": api_key,
                "format": "json",
                "limit": page_limit,
                "offset": offset,
            }
            resp = await client.get(url, params=params)
            try:
                data = resp.json()
            except Exception:
                return None

            if resp.status_code >= 400:
                return None

            env = data.get("houseRollCallVoteMemberVotes")
            if not isinstance(env, dict):
                return None
            rows = env.get("results") or []
            for row in rows:
                if isinstance(row, dict) and _bioguide_matches_row(row, bioguide_id):
                    return row

            pag = env.get("pagination") or data.get("pagination") or {}
            total = int(pag.get("count") or 0)
            offset += len(rows)
            if not rows or offset >= total:
                return None

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
