"""
Congressional stock / PTR disclosures: Senate eFTDS JSON (efts.senate.gov) and House XML.

House automatic fetch is best-effort XML parsing when a disclosure file body is supplied;
the Clerk site is form-driven — use ``search(..., query_type="house")`` with a direct XML
URL in ``query`` (``house_xml:https://...``) or extend the fetcher with a stable bulk URL.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.stock_trade_proximity import (
    SENATE_DISCLOSURE_URL,
    parse_eftds_trades_from_response,
)
from utils.http_retry import async_http_request_with_retry

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
    ),
    "Accept": "application/json, text/xml, application/xml, */*",
}

SENATE_EFD_HOME = "https://efdsearch.senate.gov/search/home/"
HOUSE_FDISC_BASE = "https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure"


def stable_stock_trade_id(*parts: str) -> str:
    """64-char id for ORM de-dupe."""
    s = "|".join(str(p) for p in parts)
    return hashlib.sha256(s.encode()).hexdigest()


def _coerce_date(s: str) -> str:
    t = (s or "").strip()[:10]
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        return t
    return (s or "")[:10]


def _tt_label(raw: str) -> str:
    u = (raw or "").lower()
    if "exchange" in u:
        return "Exchange"
    if "sale" in u or u == "s" or "sell" in u:
        return "Sale"
    if "purchase" in u or u == "p" or "buy" in u:
        return "Purchase"
    return (raw or "Transaction").title() or "Transaction"


def _eftds_row_to_result(
    row: dict[str, Any], *, sub_label: str, year: int, bioguide_hint: str
) -> AdapterResult:
    disc_url = str(
        row.get("disclosure_url")
        or f"{SENATE_EFD_HOME} (search: {sub_label!r} / {year})"
    )
    company = str(row.get("company_name") or "Asset")
    tt = _tt_label(str(row.get("trade_type") or ""))
    tdate = _coerce_date(str(row.get("trade_date") or ""))
    title = f"PTR: {tt} {company}" + (f" ({row.get('ticker')})" if row.get("ticker") else "")
    body = f"{tt} on {tdate} — amount: {row.get('amount_range') or 'n/a'}. Source: U.S. Senate eFD / eFTDS."
    raw: dict[str, Any] = dict(row)
    raw["chamber"] = "senate"
    raw["bioguide_id"] = bioguide_hint
    return AdapterResult(
        source_name="U.S. Senate eFD / eFTDS",
        source_url=disc_url,
        entry_type="stock_trade",
        title=title,
        body=body,
        date_of_event=tdate or None,
        confidence="confirmed",
        raw_data=raw,
    )


def parse_house_financial_disclosure_xml(
    xml_text: str, *, default_source_url: str = HOUSE_FDISC_BASE
) -> list[dict[str, Any]]:
    """
    Best-effort parse of House Clerk electronic financial disclosure (PTR) XML.
    Tries common tag names; unknown schemas return [].
    """
    text = (xml_text or "").strip()
    if not text:
        return []
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.debug("house PTR XML parse error: %s", e)
        return []

    def local(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    for el in root.iter():
        t = local(el.tag)
        if t.lower() not in (
            "transaction",
            "ptr",
            "periodictransaction",
            "trans",
        ):
            continue
        d: dict[str, str] = {}
        for ch in el.iter():
            ctag = local(ch.tag).lower()
            if ch is el:
                continue
            val = (ch.text or "").strip()
            if not val and len(ch) == 0:
                continue
            if ctag in (
                "asset",
                "assetname",
                "name",
                "stockname",
                "issuer",
            ):
                d["asset_name"] = d.get("asset_name") or val
            elif ctag in ("ticker", "symbol", "tickername"):
                d["ticker"] = d.get("ticker") or val
            elif ctag in (
                "transactiondate",
                "transdate",
                "date",
                "transactiondate",
            ):
                d["trans_date"] = d.get("trans_date") or val
            elif ctag in (
                "disclosuredate",
                "filed",
            ):
                d["disclosure_date"] = d.get("disclosure_date") or val
            elif ctag in (
                "type",
                "transactiontype",
            ):
                d["ttype"] = d.get("ttype") or val
            elif ctag in (
                "amount",
                "amountrange",
            ):
                d["amount"] = d.get("amount") or val
            elif ctag in (
                "owner",
                "ownerind",
            ):
                d["owner"] = d.get("owner") or val
        an = d.get("asset_name", "").strip()
        if not an:
            continue
        out.append(
            {
                "asset_name": an,
                "ticker": d.get("ticker", ""),
                "trans_date": _coerce_date(d.get("trans_date", "")),
                "disclosure_date": _coerce_date(d.get("disclosure_date", "")),
                "transaction_type": d.get("ttype", "Transaction"),
                "amount_range": d.get("amount", ""),
                "owner": d.get("owner", "") or "Self",
                "chamber": "house",
                "source_url": default_source_url,
            }
        )
    return out


def _house_dict_to_result(row: dict[str, Any], bioguide_hint: str) -> AdapterResult:
    an = str(row.get("asset_name") or "Asset")
    tt = _tt_label(str(row.get("transaction_type") or ""))
    tdate = _coerce_date(str(row.get("trans_date") or ""))
    su = str(row.get("source_url") or HOUSE_FDISC_BASE)
    title = f"PTR: {tt} {an}" + (f" ({row.get('ticker')})" if row.get("ticker") else "")
    body = f"{tt} on {tdate} — {row.get('amount_range') or 'n/a'}. Owner: {row.get('owner') or 'n/a'}. {HOUSE_FDISC_BASE}"
    raw = dict(row)
    raw["bioguide_id"] = bioguide_hint
    return AdapterResult(
        source_name="U.S. House Clerk (financial disclosure)",
        source_url=su,
        entry_type="stock_trade",
        title=title,
        body=body,
        date_of_event=tdate or None,
        confidence="confirmed",
        raw_data=raw,
    )


class StockTradesAdapter(BaseAdapter):
    """
    Public-member stock / PTR fetches.

    * ``query_type="senate"`` — query is the member name (as used on eFTDS, e.g. last name);
      pulls current and prior calendar year from efts.senate.gov LATEST search-index.
    * ``query_type="house"`` — query is either a direct XML document URL, prefixed
      ``house_xml:`` (fetched and parsed), or a free-text name (returns a documented empty
      result until a bulk House endpoint is configured).
    """

    source_name = "Congressional stock disclosures"

    async def _fetch_senate_years(self, name: str, years: list[int]) -> list[AdapterResult]:
        by_bg = ""
        results: list[AdapterResult] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=45.0) as client:
            for y in years:
                url = SENATE_DISCLOSURE_URL.format(name=name, year=y)
                try:
                    resp = await async_http_request_with_retry(
                        client, "GET", url, headers=HEADERS
                    )
                    body = resp.text
                except Exception as e:
                    logger.warning("Senate eFTDS fetch failed %s: %s", url, e)
                    body = ""
                raw_trades = parse_eftds_trades_from_response(
                    body, default_disclosure_url=url
                )
                for tr in raw_trades:
                    r = _eftds_row_to_result(
                        tr, sub_label=name, year=y, bioguide_hint=by_bg
                    )
                    k = f"{r.title}|{r.date_of_event}|{r.source_url}"[:200]
                    if k in seen:
                        continue
                    seen.add(k)
                    results.append(r)
        return results

    async def _run_house(self, query: str) -> tuple[list[AdapterResult], str | None]:
        q = (query or "").strip()
        if not q:
            return [], "empty query"
        if q.lower().startswith("house_xml:"):
            u = q.split(":", 1)[1].strip()
            if u.startswith("http") and re.match(r"^https?://", u, re.I):
                try:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await async_http_request_with_retry(
                            client, "GET", u, headers=HEADERS
                        )
                    rows = parse_house_financial_disclosure_xml(
                        resp.text, default_source_url=u
                    )
                except Exception as e:
                    return [], f"house_xml fetch failed: {e!s}"
                if not rows:
                    return (
                        [],
                        "House XML parsed to zero transaction rows (check schema or file).",
                    )
                return (
                    [
                        _house_dict_to_result(x, bioguide_hint="")
                        for x in rows
                    ],
                    None,
                )
        return (
            [],
            f"House: provide house_xml:https://… URL to clerk PTR/disclosure XML, or browse {HOUSE_FDISC_BASE} .",
        )

    async def search(
        self, query: str, query_type: str = "senate"
    ) -> AdapterResponse:
        qt = (query_type or "senate").lower().strip()
        y0 = datetime.now(timezone.utc).year
        if qt == "senate":
            name = (query or "").strip()
            if not name:
                return AdapterResponse(
                    source_name=self.source_name,
                    query=query,
                    results=[],
                    found=False,
                    error="Empty name for Senate eFTDS search",
                    error_kind="processing",
                )
            results = await self._fetch_senate_years(
                name, [y0, y0 - 1]
            )
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=results,
                # found=True so investigate ingests real rows or a documented gap; avoid false "failed" in run_cached.
                found=True,
                parse_warning=None
                if results
                else f"Senate eFTDS: no trade rows in JSON for {name!r} ({y0}–{y0 - 1}). {SENATE_EFD_HOME}",
                empty_success=True,
            )

        if qt == "house":
            results, err = await self._run_house(query)
            return AdapterResponse(
                source_name=self.source_name,
                query=query,
                results=results,
                found=True,
                parse_warning=err,
                empty_success=True,
            )

        return AdapterResponse(
            source_name=self.source_name,
            query=query,
            results=[],
            found=False,
            error=f"Unknown query_type {qt!r}; use senate or house",
            error_kind="processing",
        )
