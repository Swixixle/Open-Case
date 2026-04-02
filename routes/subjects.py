from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/subjects", tags=["subjects"])

INDIANA_OFFICIALS: list[dict[str, Any]] = [
    {
        "name": "Todd Young",
        "bioguide_id": "Y000064",
        "state": "IN",
        "office": "senate",
        "party": "R",
        "fec_committee": "C00459255",
        "notes": "Indiana senior senator since 2017",
    },
    {
        "name": "Victoria Spartz",
        "bioguide_id": "S001213",
        "state": "IN",
        "office": "house",
        "district": "05",
        "party": "R",
        "notes": "Indiana 5th district — Indianapolis area",
    },
    {
        "name": "André Carson",
        "bioguide_id": "C001072",
        "state": "IN",
        "office": "house",
        "district": "07",
        "party": "D",
        "notes": "Indiana 7th district — Indianapolis",
    },
    {
        "name": "Greg Pence",
        "bioguide_id": "P000615",
        "state": "IN",
        "office": "house",
        "district": "06",
        "party": "R",
        "notes": "Indiana 6th district",
    },
    {
        "name": "Jim Baird",
        "bioguide_id": "B001307",
        "state": "IN",
        "office": "house",
        "district": "04",
        "party": "R",
        "notes": "Indiana 4th district",
    },
]

_SUBJECT_SEED: dict[str, dict[str, Any]] = {
    "Y000064": {"bioguide_id": "Y000064", "name": "Todd Young", "state": "IN", "office": "senate"},
}


@router.get("/search")
async def search_subjects(
    name: str = Query(..., min_length=1),
    state: str | None = None,
) -> dict[str, Any]:
    name_lower = name.lower().strip()
    state_u = state.upper().strip() if state else None

    matches = [
        o
        for o in INDIANA_OFFICIALS
        if name_lower in o["name"].lower()
        and (state_u is None or o["state"].upper() == state_u)
    ]
    if matches:
        return {
            "query": name,
            "state_filter": state,
            "source": "hardcoded_indiana",
            "candidates": matches,
            "instruction": (
                "Pass bioguide_id and optional fec_committee on POST "
                "/api/v1/cases/{id}/investigate."
            ),
        }

    api_key = os.getenv("CONGRESS_API_KEY")
    if api_key:
        try:
            params: dict[str, str | int] = {
                "api_key": api_key,
                "format": "json",
                "query": name,
                "limit": 5,
            }
            if state_u:
                params["stateCode"] = state_u
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.congress.gov/v3/member",
                    params=params,
                )
                data = response.json()

            members = data.get("members") or []
            if isinstance(members, dict) and "member" in members:
                inner = members["member"]
                members = inner if isinstance(inner, list) else [inner]

            api_candidates: list[dict[str, Any]] = []
            for member in members[:5]:
                if not isinstance(member, dict):
                    continue
                chamber = ""
                terms = member.get("terms") or member.get("termsOfService")
                if isinstance(terms, dict) and "item" in terms:
                    items = terms["item"]
                    tlist = items if isinstance(items, list) else [items]
                    if tlist:
                        last = tlist[-1] if isinstance(tlist[-1], dict) else {}
                        chamber = str(last.get("chamber") or "").lower()
                api_candidates.append(
                    {
                        "name": member.get("name")
                        or member.get("directOrderName")
                        or "",
                        "bioguide_id": member.get("bioguideId")
                        or member.get("bioguide_id"),
                        "state": member.get("state") or member.get("stateCode"),
                        "office": chamber,
                        "party": member.get("partyName") or member.get("party") or "",
                        "notes": "From Congress.gov API — verify before use",
                    }
                )

            return {
                "query": name,
                "state_filter": state,
                "source": "congress_gov_api",
                "candidates": api_candidates,
                "instruction": (
                    "Confirm the correct candidate before using the bioguide_id "
                    "on investigate."
                ),
            }
        except Exception:
            pass

    return {
        "query": name,
        "state_filter": state,
        "source": "none",
        "candidates": [],
        "instruction": (
            "No matches found. Try a different spelling or look up the bioguide_id "
            "at https://bioguide.congress.gov"
        ),
    }


@router.get("/bioguide/{bioguide_id}")
def get_by_bioguide(bioguide_id: str) -> dict[str, Any]:
    key = bioguide_id.strip().upper()
    for o in INDIANA_OFFICIALS:
        if o.get("bioguide_id", "").upper() == key:
            return {"known": True, **o}
    row = _SUBJECT_SEED.get(key)
    if not row:
        return {
            "bioguide_id": key,
            "known": False,
            "note": "No static profile; set bioguide on SubjectProfile at investigate time.",
        }
    return {"known": True, **row}
