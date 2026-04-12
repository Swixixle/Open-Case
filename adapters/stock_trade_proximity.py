"""
Senate financial disclosure stock trades vs committee hearing proximity (eFTDS + ProPublica).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from engines.pattern_engine import classify_donor_sector, vote_matches_sector
from utils.http_retry import async_http_request_with_retry

logger = logging.getLogger(__name__)

SENATE_DISCLOSURE_URL = (
    "https://efts.senate.gov/LATEST/search-index?q={name}&dateFrom={year}-01-01&dateTo={year}-12-31"
)
PROPUBLICA_HEARINGS_URL = (
    "https://api.propublica.org/congress/v1/{congress}/senate/hearings.json"
)

CACHE_ADAPTER = "senator_stock_trades"
CACHE_TTL_HOURS = 24
PROXIMITY_DAYS = 30
REPORTING_THRESHOLD_MIN = 1000

STOCK_TRADE_DISCLAIMER = (
    "Stock trade proximity to committee hearings is documented from public Senate "
    "disclosure records. Proximity does not establish foreknowledge or wrongdoing. "
    "The STOCK Act requires disclosure within 45 days of a trade."
)

HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; OpenCase/1.0; +https://github.com/) congressional-research"
    ),
    "Accept": "application/json, text/plain, */*",
}


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    t = str(s).strip()[:10]
    try:
        return date.fromisoformat(t)
    except ValueError:
        return None


def _sectors_for_company(company_name: str, ticker: str = "") -> set[str]:
    out: set[str] = set()
    for blob in (company_name, ticker):
        sec = classify_donor_sector(str(blob or ""), "", "")
        if sec:
            out.add(sec)
        text = str(blob or "").lower()
        for sector in (
            "pharma",
            "finance",
            "energy",
            "defense",
            "real_estate",
            "tech",
            "agriculture",
            "legal",
        ):
            if vote_matches_sector(text, sector):
                out.add(sector)
    return out


def _hearing_sectors(topic: str) -> set[str]:
    out: set[str] = set()
    t = (topic or "").lower()
    for sector in (
        "pharma",
        "finance",
        "energy",
        "defense",
        "real_estate",
        "tech",
        "agriculture",
        "legal",
    ):
        if vote_matches_sector(t, sector):
            out.add(sector)
    return out


def _amount_range_exceeds_threshold(amount_range: str) -> bool:
    s = (amount_range or "").replace(",", "")
    if not s.strip():
        return False
    if re.search(r"over\s*\$?\s*1\s*,?\s*000", s, re.I):
        return True
    nums = [int(x) for x in re.findall(r"\$?\s*(\d+)\s*", s) if x.isdigit()]
    if not nums:
        return False
    return max(nums) >= REPORTING_THRESHOLD_MIN


def _committee_overlap(hearing_committee: str, senator_committees: list[str]) -> bool:
    hc = (hearing_committee or "").strip().lower()
    if not hc:
        return False
    for sc in senator_committees:
        s = (sc or "").strip().lower()
        if not s:
            continue
        if s in hc or hc in s:
            return True
    return False


def parse_eftds_trades_from_response(
    text: str,
    *,
    default_disclosure_url: str = "",
) -> list[dict[str, Any]]:
    """
    Best-effort parse of eFTDS search-index body (JSON array/object or NDJSON).
    Tests and production can pass structured JSON.
    """
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
            or row.get("filedDate")
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
        trade_type = str(
            row.get("trade_type") or row.get("type") or row.get("transactionType") or ""
        ).lower()
        if "sale" in trade_type or trade_type == "s":
            tt = "sale"
        elif "purchase" in trade_type or trade_type == "p" or "buy" in trade_type:
            tt = "purchase"
        else:
            tt = "sale" if "sell" in trade_type else "purchase"
        amt = str(
            row.get("amount_range")
            or row.get("amount")
            or row.get("value")
            or row.get("transactionValue")
            or ""
        )
        disc_url = str(row.get("disclosure_url") or row.get("url") or default_disclosure_url)
        if trade_date and (company or ticker):
            out.append(
                {
                    "trade_date": trade_date,
                    "ticker": ticker,
                    "company_name": company,
                    "trade_type": tt,
                    "amount_range": amt,
                    "disclosure_url": disc_url,
                }
            )
    return out


def _nearest_hearing(
    trade_day: date,
    hearings: list[dict[str, Any]],
    senator_committees: list[str],
) -> tuple[date | None, str, str, str, int | None]:
    best_d: int | None = None
    best_topic = ""
    best_comm = ""
    best_url = ""
    best_date: date | None = None
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
        if dist > PROXIMITY_DAYS:
            continue
        if best_d is None or dist < best_d:
            best_d = dist
            best_date = hd
            best_topic = topic
            best_comm = comm
            best_url = str(h.get("url") or h.get("hearing_url") or "")
    if best_date is None or best_d is None:
        return None, "", "", "", None
    delta_days = (best_date - trade_day).days
    return best_date, best_topic, best_comm, best_url, delta_days


def flag_trades_against_hearings(
    trades: list[dict[str, Any]],
    hearings: list[dict[str, Any]],
    senator_committees: list[str],
) -> list[dict[str, Any]]:
    """Pure function: build flagged output rows with disclaimers."""
    flagged: list[dict[str, Any]] = []
    for tr in trades:
        trade_day = _parse_iso_date(tr.get("trade_date"))
        if trade_day is None:
            continue
        if not _amount_range_exceeds_threshold(str(tr.get("amount_range") or "")):
            continue
        nh_date, nh_topic, nh_comm, nh_url, days_between = _nearest_hearing(
            trade_day, hearings, senator_committees
        )
        if nh_date is None or days_between is None:
            continue
        stock_act_window = abs(days_between) <= PROXIMITY_DAYS
        company = str(tr.get("company_name") or "")
        ticker = str(tr.get("ticker") or "")
        c_sec = _sectors_for_company(company, ticker)
        h_sec = _hearing_sectors(nh_topic)
        sector_match = bool(c_sec & h_sec)
        if not sector_match:
            continue
        needs_hr = bool(sector_match and stock_act_window)
        flagged.append(
            {
                "trade_date": str(tr.get("trade_date") or "")[:10],
                "ticker": ticker,
                "company_name": company,
                "trade_type": tr.get("trade_type") or "purchase",
                "amount_range": str(tr.get("amount_range") or ""),
                "nearest_hearing_date": nh_date.isoformat(),
                "nearest_hearing_topic": nh_topic,
                "nearest_hearing_committee": nh_comm,
                "days_between": int(days_between),
                "sector_match": sector_match,
                "stock_act_window": stock_act_window,
                "disclosure_url": str(tr.get("disclosure_url") or ""),
                "hearing_url": nh_url,
                "needs_human_review": needs_hr,
                "disclaimer": STOCK_TRADE_DISCLAIMER,
            }
        )
    return flagged


async def _fetch_eftds_text(client: httpx.AsyncClient, name: str, year: int) -> str:
    url = SENATE_DISCLOSURE_URL.format(name=name, year=year)
    try:
        resp = await async_http_request_with_retry(client, "GET", url, headers=HEADERS_JSON)
        return resp.text
    except Exception as e:
        logger.warning("eFTDS fetch failed %s: %s", url, e)
        return ""


async def _fetch_pp_hearings(
    client: httpx.AsyncClient, congress: int, api_key: str
) -> list[dict[str, Any]]:
    url = PROPUBLICA_HEARINGS_URL.format(congress=congress)
    headers = {**HEADERS_JSON, "X-API-Key": api_key}
    try:
        resp = await async_http_request_with_retry(client, "GET", url, headers=headers)
        data = resp.json()
    except Exception as e:
        logger.warning("ProPublica hearings failed %s: %s", congress, e)
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        return []
    hearings_raw: list[dict[str, Any]] = []
    for block in results:
        if not isinstance(block, dict):
            continue
        nested = block.get("hearings")
        if isinstance(nested, list):
            for h in nested:
                if isinstance(h, dict):
                    hearings_raw.append(h)
        elif block.get("title") or block.get("date"):
            hearings_raw.append(block)
    normalized: list[dict[str, Any]] = []
    for h in hearings_raw:
        comm = ""
        if isinstance(h.get("committee"), dict):
            comm = str(h["committee"].get("name") or "")
        dt = str(h.get("date") or h.get("occurs_at") or "")[:10]
        title = str(h.get("title") or h.get("description") or "")
        url_h = str(h.get("url") or "")
        if dt:
            normalized.append(
                {
                    "committee": comm,
                    "topic": title,
                    "date": dt,
                    "url": url_h,
                }
            )
    return normalized


def _congress_for_year(y: int) -> int:
    """Approximate: 1st session Jan 2025 -> 119th per contemporary mapping."""
    if y >= 2025:
        return 119
    if y >= 2023:
        return 118
    return 117


async def fetch_stock_trade_proximity_for_year(
    db: Session,
    bioguide_id: str,
    senator_name: str,
    senator_committees: list[str],
    year: int,
) -> list[dict[str, Any]]:
    """One calendar year of trades + hearings; cached24h."""
    bg = (bioguide_id or "").strip()
    qkey = f"{bg}:{year}"
    cached = get_cached_raw_json(db, CACHE_ADAPTER, qkey)
    if cached is not None and isinstance(cached, dict) and isinstance(cached.get("flagged"), list):
        return list(cached["flagged"])

    name_q = (senator_name or "").strip()
    if not name_q:
        return []

    trades: list[dict[str, Any]] = []
    hearings: list[dict[str, Any]] = []

    import os

    pp_key = (os.environ.get("PROPUBLICA_API_KEY") or "").strip()

    async with httpx.AsyncClient(timeout=60.0) as client:
        body = await _fetch_eftds_text(client, name_q, year)
        disc_url = SENATE_DISCLOSURE_URL.format(name=name_q, year=year)
        trades = parse_eftds_trades_from_response(body, default_disclosure_url=disc_url)
        if pp_key:
            congress = _congress_for_year(year)
            hearings = await _fetch_pp_hearings(client, congress, pp_key)

    flagged = flag_trades_against_hearings(trades, hearings, senator_committees)
    try:
        store_cached_raw_json(
            db,
            CACHE_ADAPTER,
            qkey,
            {"flagged": flagged, "year": year, "raw_trade_count": len(trades)},
            CACHE_TTL_HOURS,
        )
    except Exception as e:
        logger.warning("stock trade cache store failed: %s", e)
    return flagged


async def fetch_stock_trade_proximity_all_years(
    db: Session,
    bioguide_id: str,
    senator_name: str,
    senator_committees: list[str],
) -> list[dict[str, Any]]:
    y0 = datetime.now(timezone.utc).year
    merged: list[dict[str, Any]] = []
    for y in (y0, y0 - 1):
        part = await fetch_stock_trade_proximity_for_year(
            db, bioguide_id, senator_name, senator_committees, y
        )
        merged.extend(part)
    return merged
