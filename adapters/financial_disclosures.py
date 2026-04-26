"""
Structured financial disclosure line items (assets, income, liabilities, etc.).

Uses the same public sources as :mod:`adapters.stock_trades` where machine-readable
text exists. House ``house_xml:`` fetches are parsed with
``parse_house_financial_disclosure_xml`` and mapped into row categories. Senate annual
disclosures remain PDF-primary upstream; this module documents that in ``search``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

from adapters.base import AdapterResponse, AdapterResult, BaseAdapter
from adapters.stock_trades import parse_house_financial_disclosure_xml
from utils.http_retry import async_http_request_with_retry

SENATE_EFD = "https://efdsearch.senate.gov/search/home/"
HOUSE_CLERK = "https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OpenCase/1.0) congressional-research"
    ),
    "Accept": "text/xml, application/xml, */*",
}

CATEGORY_PTR = "PTR"


def parse_house_disclosure_line_items(
    xml_text: str, *, default_url: str, default_year: int
) -> list[dict[str, Any]]:
    """
    Reuse the House PTR XML path from :mod:`adapters.stock_trades`, then map to
    ``financial_disclosures``-style row dicts. Extend with annual-schema parsers later.
    """
    ptr_rows = parse_house_financial_disclosure_xml(
        xml_text, default_source_url=default_url
    )
    out: list[dict[str, Any]] = []
    for r in ptr_rows:
        an = (r.get("asset_name") or "Asset")[:2000]
        tkr = (r.get("ticker") or "").strip()
        desc = f"{an}" + (f" ({tkr})" if tkr else "")
        out.append(
            {
                "category": CATEGORY_PTR,
                "description": desc,
                "value_range": (r.get("amount_range") or "")[:64] or None,
                "income_amount": None,
                "filing_year": default_year,
                "disclosure_type": "Periodic Transaction Report",
                "source_name": "U.S. House Clerk (PTR XML)",
                "source_url": default_url,
            }
        )
    return out


def _results_from_line_dicts(
    items: list[dict[str, Any]], base_query: str
) -> list[AdapterResult]:
    res: list[AdapterResult] = []
    for row in items:
        title = f"{row.get('category', 'Item')}: {str(row.get('description', ''))[:120]}"
        body = " ".join(
            str(x)
            for x in (
                row.get("disclosure_type"),
                row.get("value_range"),
                row.get("income_amount"),
            )
            if x
        )
        raw = {**row, "query": base_query}
        res.append(
            AdapterResult(
                source_name=raw["source_name"] or "U.S. financial disclosures",
                source_url=row.get("source_url", HOUSE_CLERK),
                entry_type="financial_disclosure",
                title=title,
                body=body or title,
                date_of_event=None,
                confidence="confirmed",
                raw_data=raw,
            )
        )
    return res


class FinancialDisclosuresAdapter(BaseAdapter):
    """
    * ``query_type="house"`` and ``query`` = ``house_xml:https://…`` — fetch and parse.
    * ``query_type="senate"`` — guidance; structured annual is not in this adapter.
    * ``query_type="auto"`` — if ``query`` is ``house_xml:…``, delegates to **house**;
      otherwise the same as **senate** guidance.
    """

    source_name = "U.S. financial disclosures"

    async def search(self, query: str, query_type: str = "auto") -> AdapterResponse:
        q = (query or "").strip()
        qt = (query_type or "auto").lower().strip()
        if qt == "auto" and q.lower().startswith("house_xml:"):
            return await self.search(q, "house")
        y0 = datetime.now(timezone.utc).year
        if qt in ("auto", "senate"):
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=[],
                found=True,
                empty_success=True,
                parse_warning=(
                    f"Senate **annual** disclosure is not available as a stable JSON feed here; "
                    f"use {SENATE_EFD} for PTR/stock context (see StockTradesAdapter). "
                    f"For House electronic XML, use house_xml:https://… with query_type=house (or auto)."
                ),
            )

        if qt == "house" and q.lower().startswith("house_xml:"):
            url = q.split(":", 1)[-1].strip()
            if not re.match(r"^https?://", url, re.I):
                return AdapterResponse(
                    source_name=self.source_name,
                    query=q,
                    results=[],
                    found=False,
                    error="house_xml: requires https URL",
                    error_kind="processing",
                )
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await async_http_request_with_retry(
                        client, "GET", url, headers=HEADERS
                    )
                body = resp.text
            except Exception as e:
                return AdapterResponse(
                    source_name=self.source_name,
                    query=q,
                    results=[],
                    found=False,
                    error=str(e),
                    error_kind="network",
                )
            items = parse_house_disclosure_line_items(
                body, default_url=url, default_year=y0
            )
            results = _results_from_line_dicts(items, q)
            return AdapterResponse(
                source_name=self.source_name,
                query=q,
                results=results,
                found=True,
                empty_success=not bool(results),
                parse_warning=None
                if results
                else f"No line items parsed from {url!r} (empty or non-PTR schema).",
            )

        return AdapterResponse(
            source_name=self.source_name,
            query=q,
            results=[],
            found=False,
            error=(
                "Use query_type 'senate' (guidance) or 'house' with house_xml:https://… — "
                f"see {HOUSE_CLERK}"
            ),
            error_kind="processing",
        )
