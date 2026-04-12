"""
Senior staff + LDA lobbying cross-reference (ProPublica member + Senate LDA API).
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from models import EvidenceEntry
from utils.http_retry import async_http_request_with_retry

logger = logging.getLogger(__name__)

PROPUBLICA_MEMBER_URL = "https://api.propublica.org/congress/v1/members/{bioguide_id}.json"
LDA_LOBBYIST_SEARCH = "https://lda.senate.gov/api/v1/lobbyists/"
LDA_FILINGS_URL = "https://lda.senate.gov/api/v1/filings/"

CACHE_ADAPTER = "senator_staff_network"
CACHE_TTL_HOURS = 7 * 24

HEADERS_LDA = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
    )
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_entity(s: str) -> str:
    t = unicodedata.normalize("NFKD", (s or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-zA-Z0-9]+", "", t).lower()
    return t


def _entities_overlap(a: str, b: str) -> bool:
    na, nb = _norm_entity(a), _norm_entity(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 6 and len(nb) >= 6 and (na in nb or nb in na):
        return True
    if len(na) >= 5 and len(nb) >= 5:
        lo, hi = (na, nb) if len(na) <= len(nb) else (nb, na)
        if hi.startswith(lo):
            return True
    return False


def _fec_donor_strings_for_case(db: Session, case_file_id: UUID) -> set[str]:
    out: set[str] = set()
    rows = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_file_id,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    ).all()
    for e in rows:
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        for key in ("contributor_name", "contributor_employer", "contributor_organization"):
            v = str(raw.get(key) or "").strip()
            if v:
                out.add(v)
    return out


def _donor_overlap_for_clients(
    client_names: list[str],
    fec_entities: set[str],
) -> tuple[bool, list[str]]:
    hits: list[str] = []
    for c in client_names:
        for fe in fec_entities:
            if _entities_overlap(c, fe):
                hits.append(c)
                break
    return bool(hits), sorted(set(hits))


def _parse_staff_entry(raw: dict[str, Any], role_label: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = (
        str(raw.get("name") or raw.get("staff_name") or "").strip()
        or " ".join(
            str(p or "").strip()
            for p in (
                raw.get("first_name"),
                raw.get("middle_name"),
                raw.get("last_name"),
            )
            if p
        ).strip()
    )
    if not name:
        return None
    role = str(raw.get("title") or raw.get("role") or raw.get("position") or role_label)
    return {
        "name": name,
        "role_at_office": role,
        "start_date": str(raw.get("start_date") or raw.get("begin_date") or "") or "",
        "end_date": str(raw.get("end_date") or "") or None,
    }


def extract_senior_staff_from_propublica_member(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort: ProPublica payloads vary; tests inject structured staff lists."""
    staff_rows: list[dict[str, Any]] = []
    for key in ("staff", "senior_staff", "office_staff", "staff_list"):
        block = member.get(key)
        if isinstance(block, list):
            for item in block:
                parsed = _parse_staff_entry(item, "Staff")
                if parsed:
                    staff_rows.append(parsed)
        elif isinstance(block, dict):
            parsed = _parse_staff_entry(block, "Staff")
            if parsed:
                staff_rows.append(parsed)

    for role in member.get("roles") or []:
        if not isinstance(role, dict):
            continue
        rtitle = str(role.get("title") or role.get("chamber") or "Senate role")
        for key in ("staff", "senior_staff", "office_staff", "staff_list"):
            block = role.get(key)
            if isinstance(block, list):
                for item in block:
                    parsed = _parse_staff_entry(item, rtitle)
                    if parsed:
                        staff_rows.append(parsed)
            elif isinstance(block, dict):
                parsed = _parse_staff_entry(block, rtitle)
                if parsed:
                    staff_rows.append(parsed)
    return staff_rows


def _subject_meta_from_member(member: dict[str, Any]) -> dict[str, Any]:
    first = str(member.get("first_name") or "").strip()
    last = str(member.get("last_name") or "").strip()
    name = f"{first} {last}".strip() or str(member.get("name") or "").strip()
    party = str(member.get("current_party") or member.get("party") or "").strip()
    state = str(member.get("state") or "").strip()
    roles = [r for r in (member.get("roles") or []) if isinstance(r, dict)]
    committees: list[str] = []
    years_in_office = 0
    if roles:
        latest = roles[-1]
        for c in latest.get("committees") or []:
            if isinstance(c, dict) and c.get("name"):
                committees.append(str(c["name"]))
        begin = str(latest.get("start_date") or latest.get("begin_date") or "")[:10]
        if begin and len(begin) >= 4:
            try:
                y0 = int(begin[:4])
                years_in_office = max(0, datetime.now(timezone.utc).year - y0)
            except ValueError:
                years_in_office = 0
    return {
        "name": name,
        "party": party,
        "state": state,
        "committees": committees,
        "years_in_office": years_in_office,
    }


async def _lda_search_lobbyist(client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]:
    if len(name.strip()) < 2:
        return []
    params = {"name": name.strip(), "format": "json"}
    resp = await async_http_request_with_retry(
        client, "GET", LDA_LOBBYIST_SEARCH, params=params, headers=HEADERS_LDA
    )
    data = resp.json()
    results = data.get("results") if isinstance(data, dict) else None
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _filing_mentions_lobbyist(filing: dict[str, Any], lobbyist_id: int) -> bool:
    for act in filing.get("lobbying_activities") or []:
        if not isinstance(act, dict):
            continue
        for row in act.get("lobbyists") or []:
            if not isinstance(row, dict):
                continue
            lm = row.get("lobbyist")
            if isinstance(lm, dict) and lm.get("id") == lobbyist_id:
                return True
    return False


def _harvest_clients_and_issues(filings: list[dict[str, Any]], lobbyist_id: int) -> tuple[list[str], list[str]]:
    clients: set[str] = set()
    issues: set[str] = set()
    for filing in filings:
        if not _filing_mentions_lobbyist(filing, lobbyist_id):
            continue
        cl = filing.get("client") if isinstance(filing.get("client"), dict) else {}
        cn = str(cl.get("name") or "").strip()
        if cn:
            clients.add(cn)
        for act in filing.get("lobbying_activities") or []:
            if not isinstance(act, dict):
                continue
            code = str(act.get("general_issue_code") or "").strip().upper()
            if code:
                issues.add(code)
    return sorted(clients), sorted(issues)


async def _lda_filings_for_lobbyist(
    client: httpx.AsyncClient,
    lobbyist_id: int,
    *,
    max_filings: int = 24,
    max_pages_per_year: int = 4,
) -> list[dict[str, Any]]:
    """Scan recent-year LDA filing pages and keep rows naming this lobbyist id."""
    collected: list[dict[str, Any]] = []
    year_now = datetime.now(timezone.utc).year
    for year in range(year_now, year_now - 6, -1):
        url: str | None = LDA_FILINGS_URL
        params: dict[str, str] | None = {
            "filing_year": str(year),
            "page_size": "50",
            "format": "json",
        }
        pages = 0
        while url and pages < max_pages_per_year and len(collected) < max_filings:
            pages += 1
            try:
                if params:
                    resp = await async_http_request_with_retry(
                        client, "GET", url, params=params, headers=HEADERS_LDA
                    )
                else:
                    resp = await async_http_request_with_retry(
                        client, "GET", url, headers=HEADERS_LDA
                    )
            except Exception as e:
                logger.warning("LDA filings page failed year=%s: %s", year, e)
                break
            data = resp.json()
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, list):
                break
            for raw in results:
                if isinstance(raw, dict) and _filing_mentions_lobbyist(raw, lobbyist_id):
                    collected.append(raw)
                    if len(collected) >= max_filings:
                        break
            nxt = data.get("next") if isinstance(data, dict) else None
            url = str(nxt) if nxt else None
            params = None
        if len(collected) >= max_filings:
            break
    return collected


async def fetch_staff_network(
    db: Session,
    bioguide_id: str,
    case_file_id: UUID,
) -> dict[str, Any]:
    """
    Returns { "staff": [...], "subject_meta": {...}, "retrieved_at", "source_urls" }.
    Cached7 days per bioguide (case-specific donor overlap recomputed when missing from cache).
    """
    bg = (bioguide_id or "").strip()
    cache_key = bg
    cached = get_cached_raw_json(db, CACHE_ADAPTER, cache_key)
    if cached is not None and isinstance(cached, dict):
        # Recompute donor overlap for this case (not cached with case id to keep key stable).
        fec = _fec_donor_strings_for_case(db, case_file_id)
        staff = []
        for row in cached.get("staff") or []:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            clients = [str(c) for c in (r.get("lobbying_clients") or []) if c]
            overlap, entities = _donor_overlap_for_clients(clients, fec)
            r["donor_overlap"] = overlap
            r["donor_overlap_entities"] = entities
            staff.append(r)
        return {
            "staff": staff,
            "subject_meta": cached.get("subject_meta") or {},
            "retrieved_at": cached.get("retrieved_at") or _now_iso(),
            "source_urls": list(cached.get("source_urls") or []),
            "from_cache": True,
        }

    api_key = (os.environ.get("PROPUBLICA_API_KEY") or "").strip()
    source_urls: list[str] = []
    member: dict[str, Any] = {}
    if api_key:
        url = PROPUBLICA_MEMBER_URL.format(bioguide_id=bg)
        source_urls.append(url)
        try:
            async with httpx.AsyncClient(timeout=60.0, headers={"X-API-Key": api_key}) as hc:
                resp = await async_http_request_with_retry(hc, "GET", url)
                data = resp.json()
        except Exception as e:
            logger.warning("ProPublica member fetch failed: %s", e)
            data = {}
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, list) and results and isinstance(results[0], dict):
            member = results[0]
    else:
        logger.warning("PROPUBLICA_API_KEY missing; staff_network uses empty member profile")

    subject_meta = _subject_meta_from_member(member)
    staff_seed = extract_senior_staff_from_propublica_member(member)
    if not staff_seed and subject_meta.get("name"):
        # Allow minimal pipeline progress when ProPublica omits staff lists.
        staff_seed = []

    fec_entities = _fec_donor_strings_for_case(db, case_file_id)
    staff_out: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60.0, headers=HEADERS_LDA) as lda_client:
        for s in staff_seed:
            name = str(s.get("name") or "").strip()
            role = str(s.get("role_at_office") or "").strip()
            start_date = str(s.get("start_date") or "").strip()
            end_date = s.get("end_date")
            lda_url = f"{LDA_LOBBYIST_SEARCH}?name={quote(name)}"
            if lda_url not in source_urls:
                source_urls.append(lda_url)

            registered = False
            clients: list[str] = []
            issue_codes: list[str] = []
            try:
                matches = await _lda_search_lobbyist(lda_client, name)
            except Exception as e:
                logger.warning("LDA lobbyist search failed for %r: %s", name, e)
                matches = []

            best_id: int | None = None
            if matches:
                # Prefer exact last-name match on final token
                target_last = name.lower().split()[-1] if name else ""
                for m in matches:
                    ln = str(m.get("last_name") or "").lower().strip()
                    if target_last and ln == target_last:
                        best_id = int(m["id"]) if m.get("id") is not None else None
                        break
                if best_id is None and matches[0].get("id") is not None:
                    best_id = int(matches[0]["id"])

            if best_id is not None:
                registered = True
                try:
                    filings = await _lda_filings_for_lobbyist(lda_client, best_id)
                except Exception as e:
                    logger.warning("LDA filings harvest failed for lobbyist %s: %s", best_id, e)
                    filings = []
                clients, issue_codes = _harvest_clients_and_issues(filings, best_id)

            overlap, overlap_entities = _donor_overlap_for_clients(clients, fec_entities)

            staff_out.append(
                {
                    "name": name,
                    "role_at_office": role,
                    "start_date": start_date,
                    "end_date": end_date,
                    "registered_lobbyist": registered,
                    "lobbying_clients": clients,
                    "issue_codes": issue_codes,
                    "donor_overlap": overlap,
                    "donor_overlap_entities": overlap_entities,
                    "source_urls": [lda_url],
                }
            )

    payload_for_cache = {
        "staff": [
            {
                **{k: v for k, v in row.items() if k not in ("donor_overlap", "donor_overlap_entities")},
                "lobbying_clients": row["lobbying_clients"],
                "issue_codes": row["issue_codes"],
                "registered_lobbyist": row["registered_lobbyist"],
            }
            for row in staff_out
        ],
        "subject_meta": subject_meta,
        "retrieved_at": _now_iso(),
        "source_urls": source_urls,
        "from_cache": False,
    }
    try:
        store_cached_raw_json(db, CACHE_ADAPTER, cache_key, payload_for_cache, CACHE_TTL_HOURS)
    except Exception as e:
        logger.warning("staff network cache store failed: %s", e)

    return {
        "staff": staff_out,
        "subject_meta": subject_meta,
        "retrieved_at": _now_iso(),
        "source_urls": source_urls,
        "from_cache": False,
    }
