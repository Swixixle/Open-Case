"""
Senior staff + LDA lobbying cross-reference (Congress.gov member + bioguide + Perplexity sonar + LDA).
"""
from __future__ import annotations

import asyncio
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
from core.credentials import CredentialRegistry
from models import EvidenceEntry
from services.perplexity_router import (
    ROUTER_CACHE_BUMP,
    ResearchPhase1Kind,
    classify_staff_network_phase1,
    run_phase1_extraction,
)
from utils.http_retry import async_http_request_with_retry, http_request_with_retry

logger = logging.getLogger(__name__)

CONGRESS_MEMBER_URL = "https://api.congress.gov/v3/member/{bioguide_id}"
LDA_LOBBYIST_SEARCH = "https://lda.gov/api/v1/lobbyists/"
LDA_FILINGS_URL = "https://lda.gov/api/v1/filings/"
CACHE_ADAPTER = "senator_staff_network"
CACHE_TTL_HOURS = 7 * 24

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
    )
}

PERPLEXITY_STAFF_SYSTEM = """You extract senior U.S. Senate office staff from the user-provided context and query.
Return only a JSON array: [{"name": "Full Name", "role": "Chief of Staff"}].
Use roles such as Chief of Staff, Legislative Director, Communications Director when stated.
If no staff are documented, return [].
No prose outside JSON."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _congress_api_key() -> str:
    raw = (os.environ.get("CONGRESS_API_KEY") or "").strip()
    if raw:
        return raw
    try:
        k = CredentialRegistry.get_credential("congress")
        return (k or "").strip()
    except Exception:
        return ""


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


def extract_subject_meta_from_congress_gov_member(member: dict[str, Any]) -> dict[str, Any]:
    """Map Congress.gov v3 `member` object to dossier subject_meta fields."""
    if not isinstance(member, dict):
        return {}
    first = str(member.get("firstName") or "").strip()
    last = str(member.get("lastName") or "").strip()
    name = str(member.get("directOrderName") or f"{first} {last}".strip() or "").strip()
    state = str(member.get("state") or "").strip()
    party = ""
    ph = member.get("partyHistory") or []
    if isinstance(ph, list) and ph:
        lastp = ph[-1] if isinstance(ph[-1], dict) else {}
        party = str(lastp.get("partyAbbreviation") or lastp.get("partyName") or "").strip()
    terms = [t for t in (member.get("terms") or []) if isinstance(t, dict)]
    senate_years: list[int] = []
    for t in terms:
        ch = str(t.get("chamber") or "").lower()
        if "senate" not in ch:
            continue
        sy = t.get("startYear")
        if sy is not None:
            try:
                senate_years.append(int(sy))
            except (TypeError, ValueError):
                pass
    years_in_office = 0
    if senate_years:
        years_in_office = max(0, datetime.now(timezone.utc).year - min(senate_years))
    return {
        "name": name,
        "party": party,
        "state": state,
        "committees": [],
        "years_in_office": years_in_office,
    }


def _extract_json_array(text: str) -> list[Any]:
    t = (text or "").strip()
    if not t:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if fence:
        t = fence.group(1).strip()
    try:
        data = json.loads(t)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
        return []


def parse_staff_from_sonar_assistant_text(text: str) -> list[dict[str, Any]]:
    """Parse Perplexity sonar output into staff_seed rows (name, role_at_office, dates)."""
    arr = _extract_json_array(text)
    out: list[dict[str, Any]] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        n = str(it.get("name") or it.get("staff_name") or "").strip()
        r = str(it.get("role") or it.get("title") or it.get("position") or "Senior staff").strip()
        if n:
            out.append(
                {
                    "name": n,
                    "role_at_office": r,
                    "start_date": str(it.get("start_date") or "") or "",
                    "end_date": it.get("end_date"),
                }
            )
    if out:
        return out
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(
            r"^[-*•\d.]+\s*(.+?)\s*[—–-]\s*(.+)$",
            line,
        )
        if m:
            n, r = m.group(1).strip(), m.group(2).strip()
            n = re.sub(r"^\*+|\*+$", "", n).strip()
            if len(n) > 2:
                out.append(
                    {
                        "name": n,
                        "role_at_office": r or "Senior staff",
                        "start_date": "",
                        "end_date": None,
                    }
                )
    return out


def _assistant_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    ch0 = choices[0]
    if not isinstance(ch0, dict):
        return ""
    msg = ch0.get("message")
    if not isinstance(msg, dict):
        return ""
    return str(msg.get("content") or "")


def _perplexity_sonar_staff_sync(senator_name: str, context_snippet: str, api_key: str) -> str:
    query = (
        f'"{senator_name}" chief of staff OR legislative director OR communications director '
        "site:politico.com OR site:rollcall.com OR site:linkedin.com"
    )
    user = f"Research query: {query}\n\n"
    if (context_snippet or "").strip():
        user += f"Optional public bioguide/office page excerpt:\n{context_snippet[:12000]}\n\n"
    user += f"Senator: {senator_name}. Return only the JSON array specified in the system message."
    kind = classify_staff_network_phase1()
    gemini_ok = bool((os.environ.get("GEMINI_API_KEY") or "").strip())
    if not (api_key or "").strip() and not (
        kind == ResearchPhase1Kind.gemini_first and gemini_ok
    ):
        return ""
    try:
        data, _trail = run_phase1_extraction(
            PERPLEXITY_STAFF_SYSTEM,
            user,
            classification=kind,
            perplexity_api_key=api_key,
            timeout=120.0,
        )
    except Exception as e:
        logger.warning("Staff seed LLM failed: %s", e)
        return ""
    return _assistant_text(data if isinstance(data, dict) else {})


async def _staff_seed_from_perplexity_sonar(
    senator_name: str, context_snippet: str
) -> list[dict[str, Any]]:
    api_key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    kind = classify_staff_network_phase1()
    gemini_ok = bool((os.environ.get("GEMINI_API_KEY") or "").strip())
    if not api_key and not (kind == ResearchPhase1Kind.gemini_first and gemini_ok):
        logger.warning(
            "PERPLEXITY_API_KEY missing and Gemini staff path unavailable; staff seed skipped"
        )
        return []
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(
            None,
            lambda: _perplexity_sonar_staff_sync(senator_name, context_snippet, api_key),
        )
    except Exception as e:
        logger.warning("Perplexity sonar staff lookup failed: %s", e)
        return []
    return parse_staff_from_sonar_assistant_text(text)


def _strip_html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


async def _fetch_bioguide_contact_excerpt(client: httpx.AsyncClient, bioguide_id: str) -> str:
    bg = (bioguide_id or "").strip()
    if not bg:
        return ""
    urls = [
        f"https://bioguide.congress.gov/search/bio/{bg}",
        f"https://bioguide.congress.gov/search/bio/{bg}.html",
    ]
    for url in urls:
        try:
            r = await client.get(url, timeout=25.0, headers=HEADERS_HTTP, follow_redirects=True)
            if r.status_code == 200 and len(r.text) > 200:
                return _strip_html_to_text(r.text)[:15000]
        except Exception as e:
            logger.debug("bioguide fetch %s: %s", url, e)
            continue
    return ""


async def _fetch_congress_gov_member(
    client: httpx.AsyncClient, bioguide_id: str, api_key: str
) -> dict[str, Any] | None:
    url = CONGRESS_MEMBER_URL.format(bioguide_id=(bioguide_id or "").strip())
    params = {"api_key": api_key, "format": "json"}
    try:
        resp = await async_http_request_with_retry(
            client, "GET", url, params=params, headers=HEADERS_HTTP
        )
        data = resp.json()
    except Exception as e:
        logger.warning("Congress.gov member fetch failed for %s: %s", bioguide_id, e)
        return None
    if not isinstance(data, dict):
        return None
    m = data.get("member")
    return m if isinstance(m, dict) else None


async def _lda_search_lobbyist(client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]:
    if len(name.strip()) < 2:
        return []
    params = {"name": name.strip(), "format": "json"}
    resp = await async_http_request_with_retry(
        client, "GET", LDA_LOBBYIST_SEARCH, params=params, headers=HEADERS_HTTP
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
                        client, "GET", url, params=params, headers=HEADERS_HTTP
                    )
                else:
                    resp = await async_http_request_with_retry(
                        client, "GET", url, headers=HEADERS_HTTP
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


def _empty_staff_response() -> dict[str, Any]:
    return {
        "staff": [],
        "subject_meta": {},
        "retrieved_at": _now_iso(),
        "source_urls": [],
        "from_cache": False,
    }


async def fetch_staff_network(
    db: Session,
    bioguide_id: str,
    case_file_id: UUID,
) -> dict[str, Any]:
    """
    Congress.gov member profile + bioguide.congress.gov excerpt + Perplexity sonar staff names + LDA.
    Cached7 days per bioguide (donor overlap recomputed per case on cache hit).
    """
    bg = (bioguide_id or "").strip()
    cache_key = f"{bg}:{ROUTER_CACHE_BUMP}"
    cached = get_cached_raw_json(db, CACHE_ADAPTER, cache_key)
    if cached is not None and isinstance(cached, dict):
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

    api_key = _congress_api_key()
    if not api_key:
        logger.warning(
            "CONGRESS_API_KEY missing; staff_network returns empty staff (Congress.gov member required)."
        )
        return _empty_staff_response()

    source_urls: list[str] = []
    member: dict[str, Any] | None = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        congress_url = CONGRESS_MEMBER_URL.format(bioguide_id=bg)
        source_urls.append(congress_url)
        member = await _fetch_congress_gov_member(client, bg, api_key)

    if member is None:
        logger.warning(
            "Congress.gov member lookup failed for bioguide_id=%s; staff_network returns empty staff.",
            bg,
        )
        return _empty_staff_response()

    subject_meta = extract_subject_meta_from_congress_gov_member(member)
    senator_name = (subject_meta.get("name") or "").strip()
    if not senator_name:
        logger.warning("Congress.gov member has no display name for %s; staff_network empty.", bg)
        return {
            **_empty_staff_response(),
            "subject_meta": subject_meta,
            "source_urls": source_urls,
        }

    bioguide_excerpt = ""
    async with httpx.AsyncClient(timeout=60.0) as client:
        bioguide_excerpt = await _fetch_bioguide_contact_excerpt(client, bg)
    if bioguide_excerpt:
        source_urls.append(f"https://bioguide.congress.gov/search/bio/{bg}")

    staff_seed = await _staff_seed_from_perplexity_sonar(senator_name, bioguide_excerpt)

    fec_entities = _fec_donor_strings_for_case(db, case_file_id)
    staff_out: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60.0) as lda_client:
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
