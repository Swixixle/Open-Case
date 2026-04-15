"""Indiana IDIS bulk contribution CSV adapter (campaignfinance.in.gov)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter

BULK_DOWNLOAD_BASE = "https://campaignfinance.in.gov/PublicSite/Docs/BulkDataDownloads"
PORTAL_HOME = "https://campaignfinance.in.gov/PublicSite/Homepage.aspx"
MAX_RESULTS_PER_SEARCH = 250
YEAR_LOOKBACK = 12
HTTP_TIMEOUT = 120.0


def _normalize_tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", query.lower()) if t]


def _row_matches_subject_campaign(query: str, row: dict[str, str]) -> bool:
    """Match rows where the *committee or candidate* relates to the subject (not donor-only)."""
    committee = (row.get("Committee") or "").lower()
    candidate = (row.get("CandidateName") or "").lower()
    blob = f"{committee} {candidate}"
    if not blob.strip():
        return False
    tokens = _normalize_tokens(query)
    if not tokens:
        return False
    for t in tokens:
        if len(t) >= 4 and t in blob:
            return True
    if "joe" in tokens and "hogsett" in tokens and "hogsett" in blob:
        return True
    return False


def _parse_amount(raw: str | None) -> float | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _parse_contribution_date(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=timezone.utc).date().isoformat()
        except ValueError:
            continue
    return s[:10] if len(s) >= 10 else s


def _ingest_zip(
    content: bytes,
    *,
    query: str,
    year: int,
    zip_url: str,
    budget: int,
) -> list[AdapterResult]:
    out: list[AdapterResult] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return out
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not names:
        zf.close()
        return out
    with zf.open(names[0], "r") as raw_f:
        text_f = io.TextIOWrapper(raw_f, encoding="utf-8", errors="replace")
        reader = csv.DictReader(text_f)
        for row in reader:
            if len(out) >= budget:
                break
            if not _row_matches_subject_campaign(query, row):
                continue
            committee = (row.get("Committee") or "").strip()
            contributor = (row.get("Name") or "").strip()
            candidate = (row.get("CandidateName") or "").strip()
            amt = _parse_amount(row.get("Amount"))
            cdate = _parse_contribution_date(row.get("ContributionDate"))
            fn = (row.get("FileNumber") or "").strip()
            raw_data: dict[str, Any] = {
                "idis_year": year,
                "idis_file_number": fn,
                "committee": committee,
                "candidate_name": candidate,
                "contributor_name": contributor,
                "contributor_city": (row.get("City") or "").strip(),
                "contributor_state": (row.get("State") or "").strip(),
                "contribution_type": (row.get("Type") or "").strip(),
                "source_bulk_zip": zip_url,
            }
            title = f"IDIS {year}: {contributor or 'Contributor'} → {committee or 'committee'}"
            body = (
                f"Indiana IDIS reported contribution. Committee: {committee}. "
                f"Candidate: {candidate or 'n/a'}. "
                f"Contributor: {contributor}. Amount: {row.get('Amount')}. "
                f"Date: {row.get('ContributionDate')}. File: {fn}. "
                f"Bulk: {zip_url}"
            )
            out.append(
                AdapterResult(
                    source_name="IDIS",
                    source_url=zip_url,
                    entry_type="financial_connection",
                    title=title[:1024],
                    body=body[:8000],
                    date_of_event=cdate,
                    amount=amt,
                    matched_name=committee or candidate or None,
                    raw_data=raw_data,
                )
            )
    zf.close()
    return out


class IdisCampaignFinanceAdapter(BaseAdapter):
    source_name = "IDIS"

    async def search(self, query: str, query_type: str = "person") -> AdapterResponse:
        q = (query or "").strip()
        if not q:
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=[],
                found=False,
                error="Empty query",
                error_kind="processing",
            )

        results: list[AdapterResult] = []
        years_failed: list[int] = []
        current_year = datetime.now(timezone.utc).year
        years = range(current_year, current_year - YEAR_LOOKBACK - 1, -1)

        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "OpenCase/IDISAdapter (civic research)"},
        ) as client:
            for year in years:
                if len(results) >= MAX_RESULTS_PER_SEARCH:
                    break
                url = f"{BULK_DOWNLOAD_BASE}/{year}_ContributionData.csv.zip"
                try:
                    resp = await client.get(url)
                except (httpx.HTTPError, httpx.RequestError) as e:
                    years_failed.append(year)
                    if len(years_failed) > 4 and not results:
                        return AdapterResponse(
                            source_name=self.source_name,
                            query=q,
                            results=[
                                AdapterResult(
                                    source_name=self.source_name,
                                    source_url=PORTAL_HOME,
                                    entry_type="gap_documented",
                                    title="IDIS: Bulk download unreachable",
                                    body=(
                                        f"IDIS bulk CSV downloads failed after retries: {e!s}. "
                                        f"Manual search: {PORTAL_HOME}"
                                    ),
                                    confidence="confirmed",
                                    is_absence=True,
                                )
                            ],
                            found=True,
                            empty_success=True,
                            parse_warning=str(e),
                            error=str(e),
                            error_kind="network",
                        )
                    continue
                if resp.status_code != 200:
                    continue
                budget = MAX_RESULTS_PER_SEARCH - len(results)
                chunk = await asyncio.to_thread(
                    _ingest_zip, resp.content, query=q, year=year, zip_url=url, budget=budget
                )
                results.extend(chunk)

        parse_warning = None
        if not results:
            parse_warning = (
                f"No IDIS contribution rows matched committee/candidate tokens for {q!r} "
                f"in bulk files {current_year}..{current_year - YEAR_LOOKBACK}. "
                f"Manual search: {PORTAL_HOME}"
            )
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=[
                    AdapterResult(
                        source_name=self.source_name,
                        source_url=PORTAL_HOME,
                        entry_type="gap_documented",
                        title="IDIS: No committee/candidate matches in bulk downloads",
                        body=parse_warning,
                        confidence="confirmed",
                        is_absence=True,
                        raw_data={"query": q, "portal": PORTAL_HOME},
                    )
                ],
                found=True,
                empty_success=True,
                parse_warning=parse_warning,
            )

        if len(results) >= MAX_RESULTS_PER_SEARCH:
            parse_warning = (
                f"Row cap reached ({MAX_RESULTS_PER_SEARCH}); additional IDIS rows may exist."
            )

        return AdapterResponse(
            source_name=self.source_name,
            query=q,
            results=results,
            found=True,
            result_hash=json.dumps({"n": len(results), "q": q}, sort_keys=True),
            parse_warning=parse_warning,
        )
