"""
Senate PTR / STOCK Act trades with FEC, LDA, and hearing cross-reference.
Structured pulls only (no LLM). Builds on eFTDS patterns from stock_trade_proximity.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from adapters.lda import fetch_lda_filings
from adapters.staff_network import _entities_overlap, _fec_donor_strings_for_case
from adapters.stock_trade_proximity import (
    HEADERS_JSON,
    SENATE_DISCLOSURE_URL as SENATE_PTR_URL,
    _committee_overlap,
    _congress_for_year,
    _fetch_eftds_text,
    _fetch_pp_hearings,
    _parse_iso_date,
)

logger = logging.getLogger(__name__)

HOUSE_DISCLOSURE_URL = (
    "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewMemberSearchResult"
)

CACHE_ADAPTER = "stock_act_trades"
CACHE_TTL_HOURS = 24
STOCK_ACT_DISCLAIMER = (
    "Periodic transaction reports are public Senate disclosures. Matches to donors, "
    "lobbying clients, or hearings document co-appearance in public records only — "
    "not coordination or wrongdoing."
)


def _parse_trades_enriched(text: str, *, default_disclosure_url: str = "") -> list[dict[str, Any]]:
    """Parse eFTDS search-index JSON/NDJSON with filed_date and asset_type when present."""
    raw = (text or "").strip()
    if not raw:
        return []
    items: list[Any] = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            if isinstance(data.get("results"), list):
                items = data["results"]
            elif isinstance(data.get("hits"), list):
                items = data["hits"]
            elif isinstance(data.get("transactions"), list):
                items = data["transactions"]
            else:
                items = [data]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    items.append(row)
            except json.JSONDecodeError:
                continue

    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        trade_date = str(
            row.get("trade_date")
            or row.get("transactionDate")
            or row.get("date")
            or ""
        )[:10]
        filed_date = str(
            row.get("filed_date")
            or row.get("filedDate")
            or row.get("filingDate")
            or row.get("dateSigned")
            or ""
        )[:10]
        ticker = str(row.get("ticker") or row.get("symbol") or row.get("assetTicker") or "")
        company = str(
            row.get("company_name")
            or row.get("assetName")
            or row.get("name")
            or row.get("issuer")
            or ""
        )
        trade_type_raw = str(
            row.get("trade_type") or row.get("type") or row.get("transactionType") or ""
        ).lower()
        if "exchange" in trade_type_raw:
            tt = "exchange"
        elif "sale" in trade_type_raw or trade_type_raw == "s":
            tt = "sale"
        elif "purchase" in trade_type_raw or trade_type_raw == "p" or "buy" in trade_type_raw:
            tt = "purchase"
        else:
            tt = "sale" if "sell" in trade_type_raw else "purchase"
        amt = str(
            row.get("amount_range")
            or row.get("amount")
            or row.get("value")
            or row.get("transactionValue")
            or ""
        )
        asset_type = str(
            row.get("asset_type")
            or row.get("assetType")
            or row.get("assetClass")
            or "stock"
        ).lower()
        if "bond" in asset_type:
            at = "bond"
        elif "option" in asset_type:
            at = "option"
        else:
            at = "stock"
        disc_url = str(row.get("disclosure_url") or row.get("url") or default_disclosure_url)
        if trade_date and (company or ticker):
            out.append(
                {
                    "trade_date": trade_date,
                    "filed_date": filed_date,
                    "ticker": ticker,
                    "company_name": company,
                    "transaction_type": tt,
                    "amount_range": amt,
                    "asset_type": at,
                    "disclosure_url": disc_url,
                }
            )
    return out


def _nearest_hearing_any_distance(
    trade_day: Any,
    hearings: list[dict[str, Any]],
    senator_committees: list[str],
) -> tuple[str, str, str, int]:
    """Closest hearing by calendar distance (committee overlap only). Returns topic, url, committee, days."""
    if trade_day is None:
        return "", "", "", -1
    best_d: int | None = None
    best_topic = ""
    best_comm = ""
    best_url = ""
    best_delta = -1
    for h in hearings:
        if not isinstance(h, dict):
            continue
        comm = str(h.get("committee") or h.get("committee_name") or "")
        if senator_committees and not _committee_overlap(comm, senator_committees):
            continue
        topic = str(h.get("topic") or h.get("title") or h.get("description") or "")
        hd = _parse_iso_date(str(h.get("date") or h.get("hearing_date") or h.get("occurs_at") or ""))
        if hd is None:
            continue
        delta = (hd - trade_day).days
        dist = abs(delta)
        if best_d is None or dist < best_d:
            best_d = dist
            best_delta = delta
            best_topic = topic
            best_comm = comm
            best_url = str(h.get("url") or h.get("hearing_url") or "")
    return best_topic, best_url, best_comm, best_delta


def _stock_act_window_ok(trade_date: str, filed_date: str) -> bool:
    td = _parse_iso_date(trade_date)
    fd = _parse_iso_date(filed_date)
    if td is None or fd is None:
        return False
    days = (fd - td).days
    return 0 <= days <= 45


async def _lda_match_for_company(company: str) -> bool:
    c = (company or "").strip()
    if len(c) < 3:
        return False
    try:
        filings = await fetch_lda_filings(c, c)
    except Exception as e:
        logger.warning("[stock_act_trades] LDA lookup failed for %r: %s", c, e)
        return False
    return bool(filings)


async def fetch_stock_act_trades_for_year(
    db: Session,
    bioguide_id: str,
    senator_name: str,
    senator_committees: list[str],
    case_file_id: UUID,
    year: int,
) -> list[dict[str, Any]]:
    """One calendar year of PTR rows with cross-references; cache 24h."""
    bg = (bioguide_id or "").strip()
    qkey = f"{bg}:{year}"
    cached = get_cached_raw_json(db, CACHE_ADAPTER, qkey)
    if cached is not None and isinstance(cached.get("trades"), list):
        return list(cached["trades"])

    name_q = (senator_name or "").strip()
    if not name_q:
        return []

    fec_entities = _fec_donor_strings_for_case(db, case_file_id)
    import os

    pp_key = (os.environ.get("PROPUBLICA_API_KEY") or "").strip()
    trades_raw: list[dict[str, Any]] = []
    hearings: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        body = await _fetch_eftds_text(client, name_q, year)
        disc_url = SENATE_PTR_URL.format(name=name_q, year=year)
        trades_raw = _parse_trades_enriched(body, default_disclosure_url=disc_url)
        if pp_key:
            congress = _congress_for_year(year)
            hearings = await _fetch_pp_hearings(client, congress, pp_key)

    unique_cos = list(
        dict.fromkeys(str(t.get("company_name") or "").strip() for t in trades_raw if t.get("company_name"))
    )[:30]
    lda_cache: dict[str, bool] = {}
    for co in unique_cos:
        if co not in lda_cache:
            lda_cache[co] = await _lda_match_for_company(co)

    out: list[dict[str, Any]] = []
    for tr in trades_raw[:200]:
        company = str(tr.get("company_name") or "")
        ticker = str(tr.get("ticker") or "")
        trade_day = _parse_iso_date(str(tr.get("trade_date") or ""))
        nh_topic, nh_url, nh_comm, days_h = _nearest_hearing_any_distance(
            trade_day, hearings, senator_committees
        )
        committee_jurisdiction_match = bool(
            nh_comm and senator_committees and _committee_overlap(nh_comm, senator_committees)
        )
        fec_donor_match = False
        for fe in fec_entities:
            if _entities_overlap(company, fe) or (ticker and _entities_overlap(ticker, fe)):
                fec_donor_match = True
                break
        lda_client_match = lda_cache.get(company.strip(), False) if company.strip() else False
        filed = str(tr.get("filed_date") or "")[:10]
        trade_d = str(tr.get("trade_date") or "")[:10]
        stock_act_window = _stock_act_window_ok(trade_d, filed)
        needs_human_review = bool(
            fec_donor_match
            or lda_client_match
            or (committee_jurisdiction_match and days_h != -1 and abs(days_h) <= 45)
        )
        source_url = str(tr.get("disclosure_url") or disc_url)
        out.append(
            {
                "trade_date": trade_d,
                "filed_date": filed,
                "ticker": ticker,
                "company_name": company,
                "transaction_type": str(tr.get("transaction_type") or "purchase"),
                "amount_range": str(tr.get("amount_range") or ""),
                "asset_type": str(tr.get("asset_type") or "stock"),
                "committee_jurisdiction_match": committee_jurisdiction_match,
                "fec_donor_match": fec_donor_match,
                "lda_client_match": lda_client_match,
                "days_to_nearest_hearing": int(days_h),
                "nearest_hearing_topic": nh_topic,
                "stock_act_window": stock_act_window,
                "needs_human_review": needs_human_review,
                "source_url": source_url,
                "disclaimer": STOCK_ACT_DISCLAIMER,
            }
        )

    try:
        store_cached_raw_json(
            db,
            CACHE_ADAPTER,
            qkey,
            {"trades": out, "year": year, "raw_trade_count": len(trades_raw)},
            CACHE_TTL_HOURS,
        )
    except Exception as e:
        logger.warning("stock_act_trades cache store failed: %s", e)
    return out


async def fetch_stock_act_trades_all_years(
    db: Session,
    bioguide_id: str,
    senator_name: str,
    senator_committees: list[str],
    case_file_id: UUID,
) -> list[dict[str, Any]]:
    y0 = datetime.now(timezone.utc).year
    merged: list[dict[str, Any]] = []
    for y in (y0, y0 - 1):
        part = await fetch_stock_act_trades_for_year(
            db, bioguide_id, senator_name, senator_committees, case_file_id, y
        )
        merged.extend(part)
    return merged
