"""CourtListener REST API v4 — judicial profiles, opinions, dockets, disclosures."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any
from urllib.parse import urlencode

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter, apply_collision_rule
from core.credentials import CredentialRegistry

BASE_URL = "https://www.courtlistener.com/api/rest/v4"

RECUSAL_MARKERS = ("recusal", "recused", "disqualif", "disqualification")
SANCTION_MARKERS = ("sanction", "sanctions", "monetary penalty")

# Jurisdiction substring → CourtListener court id (short_name)
_JURISDICTION_COURT_IDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "southern district of indiana",
            "s.d. indiana",
            "sd indiana",
            "s.d. ind.",
            "s.d. indiana",
            "district court, southern district of indiana",
        ),
        "insd",
    ),
    (
        (
            "northern district of indiana",
            "n.d. indiana",
            "nd indiana",
        ),
        "innd",
    ),
)


def courtlistener_court_ids_from_jurisdiction(jurisdiction: str | None) -> list[str]:
    """Best-effort map from case jurisdiction text to CourtListener court id(s)."""
    j = (jurisdiction or "").strip().lower()
    if not j:
        return []
    for needles, cid in _JURISDICTION_COURT_IDS:
        if any(n in j for n in needles):
            return [cid]
    return []


def _strip_suffixes(parts: list[str]) -> list[str]:
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v", "2nd", "2"}
    out = list(parts)
    while out and out[-1].lower().rstrip(".") in suffixes:
        out.pop()
    return out


def split_judge_name(display_name: str) -> tuple[str, str]:
    """Split 'James R. Sweeney II' → ('James', 'Sweeney')."""
    cleaned = re.sub(r"[,]+", " ", (display_name or "").strip())
    parts = cleaned.split()
    parts = _strip_suffixes(parts)
    if len(parts) < 2:
        return (parts[0] if parts else "", parts[-1] if parts else "")
    return (parts[0], parts[-1])


def _person_profile_url(person: dict[str, Any]) -> str:
    pid = person.get("id")
    slug = (person.get("slug") or "").strip()
    if pid and slug:
        return f"https://www.courtlistener.com/person/{pid}/{slug}/"
    if pid:
        return f"https://www.courtlistener.com/person/{pid}/"
    return "https://www.courtlistener.com/"


def _court_id_from_position(pos: dict[str, Any]) -> str | None:
    c = pos.get("court")
    if isinstance(c, dict):
        return str(c.get("id") or c.get("short_name") or "").strip() or None
    return None


def _pick_people_matches(
    results: list[dict[str, Any]],
    court_ids: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Prefer people with a judicial position on one of court_ids; else all results."""
    if not results:
        return [], 0
    if not court_ids:
        judgy: list[dict[str, Any]] = []
        for p in results:
            for pos in p.get("_positions") or []:
                ptype = str(pos.get("position_type") or "").lower()
                job = str(pos.get("job_title") or "").lower()
                if "jud" in ptype or "judge" in job:
                    judgy.append(p)
                    break
        use = judgy or results
        return use, len(use)
    matched: list[dict[str, Any]] = []
    for p in results:
        ok = False
        for pos in p.get("_positions") or []:
            cid = _court_id_from_position(pos)
            ptype = str(pos.get("position_type") or "").lower()
            job = str(pos.get("job_title") or "").lower()
            if cid and cid in court_ids and ("jud" in ptype or "judge" in job or "district" in job):
                ok = True
                break
        if ok:
            matched.append(p)
    if matched:
        return matched, len(results)
    return [], len(results)


class CourtListenerAdapter(BaseAdapter):
    source_name = "CourtListener"
    """Set before search for judge resolution (e.g. ['insd'])."""
    court_ids: list[str]

    def __init__(self) -> None:
        self.court_ids = []

    def _subject_query(self, query: str) -> str:
        if "|" in query:
            return query.split("|", 1)[0].strip()
        return (query or "").strip()

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        try:
            tok = CredentialRegistry.get_credential("courtlistener")
        except ValueError:
            tok = None
        if tok:
            h["Authorization"] = f"Token {tok.strip()}"
        return h

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        r = await client.get(url, headers=self._headers(), timeout=45.0)
        r.raise_for_status()
        return r.json()

    async def _fetch_paginated(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        *,
        max_pages: int = 25,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        q = dict(params)
        q.setdefault("page_size", 50)
        url: str | None = f"{BASE_URL}/{path.lstrip('/')}?{urlencode(q)}"
        pages = 0
        while url and pages < max_pages:
            r = await client.get(url, headers=self._headers(), timeout=45.0)
            if r.status_code == 401 or r.status_code == 403:
                raise RuntimeError(f"CourtListener HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            data = r.json()
            batch = data.get("results") or []
            out.extend(batch)
            nxt = data.get("next")
            url = str(nxt).strip() if nxt else None
            pages += 1
        return out

    async def _load_positions(
        self, client: httpx.AsyncClient, person_id: int
    ) -> list[dict[str, Any]]:
        data = await self._get_json(
            client,
            f"{BASE_URL}/positions/?{urlencode({'person': person_id, 'page_size': '50'})}",
        )
        return list(data.get("results") or [])

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        subject = self._subject_query(query)
        if not subject:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Empty subject name",
                error_kind="processing",
            )

        first, last = split_judge_name(subject)
        if not first or not last:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"Could not parse first/last name from {subject!r}",
                error_kind="processing",
            )

        court_ids = list(self.court_ids or [])
        try:
            async with httpx.AsyncClient() as client:
                people_url = (
                    f"{BASE_URL}/people/?{urlencode({'name_first': first, 'name_last': last, 'page_size': '25'})}"
                )
                pdata = await self._get_json(client, people_url)
                raw_people = list(pdata.get("results") or [])

                for p in raw_people:
                    pid = p.get("id")
                    if pid is None:
                        p["_positions"] = []
                        continue
                    try:
                        p["_positions"] = await self._load_positions(client, int(pid))
                    except (TypeError, ValueError):
                        p["_positions"] = []

                matches, name_collision_n = _pick_people_matches(raw_people, court_ids)
                if not matches:
                    empty = self._make_empty_response(
                        query,
                        error=f"No CourtListener person matched {first!r} {last!r} for courts {court_ids or 'any'}.",
                    )
                    empty.found = True
                    empty.empty_success = True
                    return empty

                person = matches[0]
                collision_count = max(1, len(matches) if len(matches) > 1 else name_collision_n)
                person_id = int(person["id"])
                profile_url = _person_profile_url(person)
                positions = person.get("_positions") or []

                pos_lines: list[str] = []
                for pos in positions:
                    cid = _court_id_from_position(pos) or "—"
                    title = (pos.get("job_title") or pos.get("position_type") or "").strip()
                    start = (pos.get("date_start") or "")[:10]
                    pos_lines.append(f"- {title} ({cid}) starting {start or 'unknown'}")

                body_profile = (
                    f"CourtListener person id {person_id}. Positions:\n"
                    + ("\n".join(pos_lines) if pos_lines else "(no positions returned)")
                )

                results: list[AdapterResult] = [
                    AdapterResult(
                        source_name=self.source_name,
                        source_url=profile_url,
                        entry_type="judicial_index",
                        title=f"CourtListener profile: {person.get('name_first', '')} {person.get('name_last', '')}".strip(),
                        body=body_profile,
                        date_of_event=None,
                        matched_name=subject,
                        collision_count=collision_count,
                        raw_data={
                            "courtlistener_person_id": person_id,
                            "fjc_id": person.get("fjc_id"),
                            "slug": person.get("slug"),
                            "positions_minimal": [
                                {
                                    "court_id": _court_id_from_position(pos),
                                    "job_title": pos.get("job_title"),
                                    "position_type": pos.get("position_type"),
                                    "date_start": pos.get("date_start"),
                                }
                                for pos in positions
                            ],
                        },
                    )
                ]
                apply_collision_rule(results[0])

                # Financial disclosures (typically public read on v4).
                disc_results: list[dict[str, Any]] = []
                try:
                    durl = f"{BASE_URL}/financial-disclosures/?{urlencode({'person': person_id, 'page_size': '50'})}"
                    ddata = await self._get_json(client, durl)
                    disc_results = list(ddata.get("results") or [])
                except (httpx.HTTPError, httpx.RequestError) as e:
                    disc_results = []
                    # Non-fatal; profile still valuable.
                    _ = e

                if disc_results:
                    links: list[str] = []
                    years: list[str] = []
                    for d in disc_results[:30]:
                        y = str(d.get("year") or d.get("date_raw") or "").strip()
                        if y:
                            years.append(y)
                        for key in ("download_filepath", "filepath", "resource_uri"):
                            v = d.get(key)
                            if isinstance(v, str) and v.startswith("http"):
                                links.append(v)
                                break
                        else:
                            uri = d.get("resource_uri")
                            if isinstance(uri, str):
                                links.append(uri)
                    body_d = (
                        f"{len(disc_results)} financial disclosure record(s) indexed in CourtListener "
                        f"for this judge. Years (sample): {', '.join(sorted(set(years))[:12]) or 'see raw index'}."
                    )
                    link_note = ""
                    if links:
                        link_note = "\nResource links (sample):\n" + "\n".join(
                            f"- {u}" for u in links[:8]
                        )
                    results.append(
                        AdapterResult(
                            source_name=self.source_name,
                            source_url=profile_url,
                            entry_type="financial_disclosure_index",
                            title="CourtListener — financial disclosure index entries",
                            body=body_d + link_note,
                            raw_data={"disclosures": disc_results[:15]},
                        )
                    )

                tok = CredentialRegistry.get_credential("courtlistener")
                tok_present = bool((tok or "").strip())

                parse_bits: list[str] = []
                if not tok_present:
                    parse_bits.append(
                        "COURTLISTENER_API_KEY not set — authored opinions and assigned-docket "
                        "searches were skipped (those endpoints require a free API token)."
                    )
                else:
                    # Opinions
                    try:
                        opinions = await self._fetch_paginated(
                            client,
                            "opinions/",
                            {"author": person_id},
                        )
                    except Exception as e:
                        opinions = []
                        parse_bits.append(f"Opinions fetch failed: {e!s}")

                    if opinions:
                        by_year: Counter[str] = Counter()
                        for o in opinions:
                            d = str(o.get("date_filed") or o.get("date_created") or "")[:4]
                            if d.isdigit():
                                by_year[d] += 1
                        top_years = ", ".join(
                            f"{y} ({n})" for y, n in by_year.most_common(12)
                        )

                        def _cite_count(o: dict[str, Any]) -> int:
                            cl = o.get("cluster")
                            if isinstance(cl, dict):
                                for k in ("citation_count", "case_citation_count", "scdb_id"):
                                    v = cl.get(k)
                                    if isinstance(v, int):
                                        return v
                            for k in ("citation_count", "depth"):
                                v = o.get(k)
                                if isinstance(v, int):
                                    return v
                            return 0

                        notable = sorted(opinions, key=_cite_count, reverse=True)[:5]
                        notable_lines: list[str] = []
                        for o in notable:
                            cn = _cite_count(o)
                            case = (o.get("case_name") or "").strip() or "(case name redacted)"
                            od = str(o.get("date_filed") or "")[:10]
                            url_o: str | None = None
                            if isinstance(o.get("absolute_url"), str):
                                url_o = o.get("absolute_url")
                            elif isinstance(o.get("download_url"), str):
                                url_o = o.get("download_url")
                            cl = o.get("cluster")
                            if not url_o and isinstance(cl, dict):
                                u2 = cl.get("absolute_url")
                                url_o = u2 if isinstance(u2, str) else None
                            if not isinstance(url_o, str):
                                url_o = profile_url
                            notable_lines.append(
                                f"- {od} — {case} — citation_index={cn} — {url_o}"
                            )

                        results.append(
                            AdapterResult(
                                source_name=self.source_name,
                                source_url=profile_url,
                                entry_type="court_opinion_summary",
                                title=f"CourtListener — {len(opinions)} authored opinion(s) indexed",
                                body=(
                                    f"Opinion counts by year (filed date): {top_years or 'n/a'}.\n"
                                    "Notable (highest citation index among returned opinions):\n"
                                    + "\n".join(notable_lines)
                                ),
                                raw_data={
                                    "opinion_count": len(opinions),
                                    "by_year": dict(by_year),
                                },
                            )
                        )

                    # Dockets (assigned judge)
                    target_courts = court_ids or [
                        cid
                        for cid in (_court_id_from_position(p) for p in positions)
                        if cid
                    ]
                    target_courts = list(dict.fromkeys([c for c in target_courts if c]))
                    if not target_courts:
                        target_courts = [""]

                    recusal_hits: list[str] = []
                    sanction_hits: list[str] = []
                    docket_total = 0
                    for cid in target_courts:
                        params: dict[str, Any] = {"assigned_to": person_id, "page_size": 50}
                        if cid:
                            params["court"] = cid
                        try:
                            dockets = await self._fetch_paginated(
                                client, "dockets/", params, max_pages=15
                            )
                        except Exception:
                            continue
                        docket_total += len(dockets)
                        for d in dockets:
                            cname = str(d.get("case_name") or "").lower()
                            dnum = str(d.get("docket_number") or "")
                            if any(m in cname for m in RECUSAL_MARKERS):
                                recusal_hits.append(f"{dnum}: {d.get('case_name')}")
                            if any(m in cname for m in SANCTION_MARKERS):
                                sanction_hits.append(f"{dnum}: {d.get('case_name')}")

                    if docket_total:
                        parse_bits.append(f"Docket rows scanned (assigned): {docket_total}")
                    if recusal_hits:
                        results.append(
                            AdapterResult(
                                source_name=self.source_name,
                                source_url=profile_url,
                                entry_type="court_docket_reference",
                                title="CourtListener — docket case names mentioning recusal",
                                body="Possible recusal-related captions (human review):\n"
                                + "\n".join(f"- {h}" for h in recusal_hits[:40]),
                                raw_data={"recusal_hits": recusal_hits},
                            )
                        )
                    if sanction_hits:
                        results.append(
                            AdapterResult(
                                source_name=self.source_name,
                                source_url=profile_url,
                                entry_type="court_docket_reference",
                                title="CourtListener — docket case names mentioning sanctions",
                                body="Possible sanctions-related captions (human review):\n"
                                + "\n".join(f"- {h}" for h in sanction_hits[:40]),
                                raw_data={"sanction_hits": sanction_hits},
                            )
                        )

                raw_bundle = {
                    "person_id": person_id,
                    "opinions_fetched": bool(tok_present),
                }
                raw_hash = hashlib.sha256(
                    json.dumps(raw_bundle, sort_keys=True, default=str).encode()
                ).hexdigest()

                warn = " ".join(parse_bits) if parse_bits else None
                return AdapterResponse(
                    source_name=self.source_name,
                    query=query,
                    results=results,
                    found=True,
                    result_hash=raw_hash,
                    parse_warning=warn,
                    credential_mode="ok" if tok_present else "credential_unavailable",
                )

        except httpx.HTTPStatusError as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"CourtListener HTTP {e.response.status_code}: {e.response.text[:300]}",
                error_kind="network",
            )
        except (httpx.HTTPError, httpx.RequestError) as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"CourtListener network error: {e!s}",
                error_kind="network",
            )
        except Exception as e:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error=f"CourtListener processing error: {e!s}",
                error_kind="processing",
            )
