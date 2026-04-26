from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.subject_name_match import subject_name_match_score
from database import get_db
from models import CaseFile, SubjectProfile

router = APIRouter(prefix="/api/v1/subjects", tags=["subjects"])

# Drop weak matches so the dropdown is not filled with unrelated officials.
MIN_SUBJECT_SEARCH_MATCH = 0.40

INDIANA_OFFICIALS: list[dict[str, Any]] = [
    # Federal (existing)
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
    # State officials
    {
        "name": "Eric Holcomb",
        "state": "IN",
        "office": "governor",
        "subject_type": "state_governor",
        "government_level": "state",
        "branch": "executive",
        "party": "R",
        "notes": "Indiana Governor",
    },
    {
        "name": "Suzanne Crouch",
        "state": "IN",
        "office": "lt_governor",
        "subject_type": "state_lt_governor",
        "government_level": "state",
        "branch": "executive",
        "party": "R",
        "notes": "Indiana Lt. Governor",
    },
    {
        "name": "Diego Morales",
        "state": "IN",
        "office": "sos",
        "subject_type": "state_sos",
        "government_level": "state",
        "branch": "executive",
        "party": "R",
        "notes": "Indiana Secretary of State",
    },
    {
        "name": "Todd Rokita",
        "state": "IN",
        "office": "ag",
        "subject_type": "state_ag",
        "government_level": "state",
        "branch": "executive",
        "party": "R",
        "notes": "Indiana Attorney General",
    },
    # Indianapolis / Marion County local officials
    {
        "name": "Joe Hogsett",
        "state": "IN",
        "jurisdiction": "Indianapolis",
        "office": "mayor",
        "subject_type": "mayor",
        "government_level": "local",
        "branch": "executive",
        "party": "D",
        "notes": "Mayor of Indianapolis",
    },
    {
        "name": "Ryan Mears",
        "state": "IN",
        "jurisdiction": "Marion County",
        "office": "prosecutor",
        "subject_type": "county_prosecutor",
        "government_level": "local",
        "branch": "executive",
        "party": "D",
        "notes": "Marion County Prosecutor",
    },
    {
        "name": "Kerry Forestal",
        "state": "IN",
        "jurisdiction": "Marion County",
        "office": "sheriff",
        "subject_type": "county_sheriff",
        "government_level": "local",
        "branch": "executive",
        "party": "D",
        "notes": "Marion County Sheriff",
    },
]

_SUBJECT_SEED: dict[str, dict[str, Any]] = {
    "Y000064": {"bioguide_id": "Y000064", "name": "Todd Young", "state": "IN", "office": "senate"},
}


def _congress_gov_member_list_url(state_u: str | None) -> str:
    """
    State must be in the path: ``GET /v3/member/{stateCode}`` returns that state's members.
    The query form ``GET /v3/member?stateCode=TX`` does *not* filter and returns unrelated rows.
    """
    if state_u:
        return f"https://api.congress.gov/v3/member/{state_u}"
    return "https://api.congress.gov/v3/member"


def _congress_member_match_score(name: str, member: dict[str, Any]) -> tuple[float, str]:
    """Best score over list-level name fields (nickname + legal names). Returns (score, label)."""
    strings: list[str] = []
    for k in ("name", "directOrderName"):
        v = member.get(k)
        if v:
            strings.append(str(v).strip())
    fn = str(member.get("firstName") or "").strip()
    ln = str(member.get("lastName") or "").strip()
    if fn and ln:
        strings.append(f"{fn} {ln}")
    nick = str(member.get("nickName") or member.get("nickname") or "").strip()
    if nick and ln:
        strings.append(f"{nick} {ln}")
    best = 0.0
    label = str(member.get("name") or member.get("directOrderName") or "").strip()
    for s in strings:
        if not s:
            continue
        score = subject_name_match_score(name, s)
        if score > best:
            best = score
            label = s
    return best, label


def _database_subject_matches(
    db: Session,
    name: str,
    subject_type: str | None,
    government_level: str | None,
    branch: str | None,
) -> list[dict[str, Any]]:
    """Match subjects with substring + short-prefix recall; score and sort by match quality."""
    q = name.strip()

    conds: list[Any] = [CaseFile.subject_name.ilike(f"%{q}%")]
    if len(q) >= 3:
        conds.append(CaseFile.subject_name.ilike(f"%{q[:3]}%"))

    stmt = select(SubjectProfile, CaseFile).join(
        CaseFile, CaseFile.id == SubjectProfile.case_file_id
    )
    stmt = stmt.where(or_(*conds))
    if subject_type:
        stmt = stmt.where(SubjectProfile.subject_type == subject_type.strip())
    if government_level:
        stmt = stmt.where(SubjectProfile.government_level == government_level.strip())
    if branch:
        stmt = stmt.where(SubjectProfile.branch == branch.strip())
    stmt = stmt.limit(150)

    out: list[dict[str, Any]] = []
    for prof, case in db.execute(stmt).all():
        display = case.subject_name or ""
        ms = subject_name_match_score(q, display)
        if ms < MIN_SUBJECT_SEARCH_MATCH:
            continue
        out.append(
            {
                "case_id": str(case.id),
                "subject_name": display,
                "subject_type": prof.subject_type,
                "slug": case.slug,
                "bioguide_id": prof.bioguide_id,
                "government_level": prof.government_level,
                "branch": prof.branch,
                "historical_depth": prof.historical_depth,
                "match_score": round(ms, 4),
            }
        )
    out.sort(key=lambda r: -r["match_score"])
    return out[:25]


def _candidate_subject_type(office: str) -> str:
    o = (office or "").lower()
    if o == "house":
        return "house_member"
    if o == "senate":
        return "senator"
    # State offices
    if o in ["governor", "lt_governor"]:
        return f"state_{o}"
    if o in ["sos", "ag"]:
        return f"state_{o}"
    # Local offices
    if o == "mayor":
        return "mayor"
    if o in ["prosecutor", "sheriff"]:
        return f"county_{o}"
    # Default fallback
    return "public_official"


def _merge_ranked(
    database_matches: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Dedupe by case_id (preferred) or bioguide_id; keep best match_score."""

    def dedupe_key(row: dict[str, Any]) -> tuple[str, str]:
        cid = (row.get("case_id") or "").strip()
        if cid:
            return ("case", cid)
        bid = (row.get("bioguide_id") or "").strip().upper()
        if bid:
            return ("bio", bid)
        return ("name", (row.get("subject_name") or row.get("name") or "").lower())

    best: dict[tuple[str, str], dict[str, Any]] = {}

    for row in database_matches:
        k = dedupe_key(row)
        prev = best.get(k)
        mapped = {
            "case_id": row.get("case_id") or None,
            "bioguide_id": row.get("bioguide_id") or "",
            "name": row["subject_name"],
            "subject_type": row.get("subject_type") or "public_official",
            "match_score": float(row.get("match_score") or 0.0),
            "source": "database",
            "slug": row.get("slug"),
            "government_level": row.get("government_level"),
            "branch": row.get("branch"),
        }
        if prev is None or mapped["match_score"] > float(prev.get("match_score") or 0.0):
            best[k] = mapped

    for row in candidates:
        k = dedupe_key(row)
        prev = best.get(k)
        entry = {
            "case_id": row.get("case_id") or None,
            "bioguide_id": row.get("bioguide_id") or "",
            "name": row.get("name") or row.get("subject_name") or "",
            "subject_type": row.get("subject_type")
            or _candidate_subject_type(str(row.get("office") or "")),
            "match_score": float(row.get("match_score") or 0.0),
            "source": row.get("source", "candidate"),
            "state": row.get("state"),
            "party": row.get("party"),
        }
        if prev is None or entry["match_score"] > float(prev.get("match_score") or 0.0):
            best[k] = entry

    merged = list(best.values())
    merged.sort(key=lambda r: -float(r.get("match_score") or 0.0))
    return merged[:limit]


@router.get("/search")
async def search_subjects(
    name: str | None = Query(None, min_length=1, description="Search text (``q`` is an alias)"),
    q: str | None = Query(
        None,
        min_length=1,
        description="Alias for ``name`` (common in clients and ad hoc curls)",
    ),
    state: str | None = None,
    subject_type: str | None = None,
    government_level: str | None = None,
    branch: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    term = (name or q or "").strip()
    if not term:
        raise HTTPException(
            status_code=422,
            detail="Provide query parameter `name` or `q` (non-empty).",
        )
    # Resolved text (shadows Query params; used for match + Congress `query=`)
    name = term
    state_u = state.upper().strip() if state else None

    database_matches = _database_subject_matches(
        db, name, subject_type, government_level, branch
    )

    if subject_type or government_level or branch:
        ranked = _merge_ranked(database_matches, [], limit=25)
        return {
            "query": name,
            "state_filter": state,
            "subject_type": subject_type,
            "government_level": government_level,
            "branch": branch,
            "source": "database",
            "database_matches": database_matches,
            "candidates": [],
            "results": ranked,
            "instruction": (
                "Review database_matches before creating a duplicate case. "
                "Use POST /api/v1/cases to open a new investigation when appropriate."
            ),
        }

    candidates_scored: list[dict[str, Any]] = []
    for o in INDIANA_OFFICIALS:
        if state_u is not None and o["state"].upper() != state_u:
            continue
        ms = subject_name_match_score(name, o["name"])
        if ms < MIN_SUBJECT_SEARCH_MATCH:
            continue
        candidates_scored.append(
            {
                **o,
                "subject_type": _candidate_subject_type(o.get("office", "")),
                "match_score": round(ms, 4),
                "source": "hardcoded_indiana",
            }
        )
    candidates_scored.sort(key=lambda r: -r["match_score"])

    if candidates_scored:
        ranked = _merge_ranked(database_matches, candidates_scored[:12], limit=25)
        return {
            "query": name,
            "state_filter": state,
            "source": "hardcoded_indiana",
            "database_matches": database_matches,
            "candidates": candidates_scored[:12],
            "results": ranked,
            "instruction": (
                "Pass bioguide_id and optional fec_committee on POST "
                "/api/v1/cases/{id}/investigate."
            ),
        }

    from core.credentials import CredentialRegistry

    api_key = CredentialRegistry.get_credential("congress")
    if api_key:
        try:
            url = _congress_gov_member_list_url(state_u)
            # Per-state list can be long; unscoped global /member list returns arbitrary rows.
            list_limit = 50 if state_u else 8
            params: dict[str, str | int] = {
                "api_key": api_key,
                "format": "json",
                "query": name,
                "limit": list_limit,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params=params)
                data = response.json()

            members = data.get("members") or []
            if isinstance(members, dict) and "member" in members:
                inner = members["member"]
                members = inner if isinstance(inner, list) else [inner]

            api_candidates: list[dict[str, Any]] = []
            for member in members:
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
                ms, mname = _congress_member_match_score(name, member)
                if ms < MIN_SUBJECT_SEARCH_MATCH:
                    continue
                api_candidates.append(
                    {
                        "name": mname,
                        "bioguide_id": member.get("bioguideId")
                        or member.get("bioguide_id"),
                        "state": member.get("state") or member.get("stateCode"),
                        "office": chamber,
                        "party": member.get("partyName") or member.get("party") or "",
                        "notes": "From Congress.gov API — verify before use",
                        "subject_type": _candidate_subject_type(chamber),
                        "match_score": round(ms, 4),
                        "source": "congress_gov_api",
                    }
                )
            api_candidates.sort(key=lambda r: -r["match_score"])

            ranked = _merge_ranked(database_matches, api_candidates, limit=25)
            return {
                "query": name,
                "state_filter": state,
                "source": "congress_gov_api",
                "database_matches": database_matches,
                "candidates": api_candidates,
                "results": ranked,
                "instruction": (
                    "Confirm the correct candidate before using the bioguide_id "
                    "on investigate."
                ),
            }
        except Exception:
            pass

    ranked = _merge_ranked(database_matches, [], limit=25)
    return {
        "query": name,
        "state_filter": state,
        "source": "none",
        "database_matches": database_matches,
        "candidates": [],
        "results": ranked,
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
