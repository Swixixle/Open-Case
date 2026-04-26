"""Persist U.S. House/Senate PTR ``AdapterResult`` rows into ``stock_trades``."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.base import AdapterResult
from adapters.stock_trades import stable_stock_trade_id
from models import EvidenceEntry, StockTrade


def _label_transaction_type(result: AdapterResult, raw: dict[str, Any]) -> str:
    t = str(
        raw.get("trade_type")
        or raw.get("transactionType")
        or raw.get("type")
        or ""
    ).lower()
    if "exchange" in t:
        return "Exchange"
    if "sale" in t or t == "s" or "sell" in t:
        return "Sale"
    if "purchase" in t or t == "p" or "buy" in t:
        return "Purchase"
    return (t[:32] or "Transaction").title() or "Transaction"


def upsert_stock_trade_from_adapter_result(
    db: Session,
    case_id: uuid.UUID,
    bioguide_id: str | None,
    entry: EvidenceEntry,
    result: AdapterResult,
) -> None:
    if (getattr(result, "entry_type", None) or "") != "stock_trade":
        return
    raw: dict[str, Any] = result.raw_data if isinstance(result.raw_data, dict) else {}

    td: date | None = entry.date_of_event
    if td is None:
        ts = str(
            raw.get("trade_date")
            or raw.get("transactionDate")
            or result.date_of_event
            or ""
        )[:10]
        if len(ts) == 10:
            try:
                td = date.fromisoformat(ts)
            except ValueError:
                td = None
    if td is None:
        td = date.today()

    disc: date | None = None
    disc_s = str(raw.get("disclosure_date") or raw.get("filed") or "")[:10]
    if len(disc_s) == 10:
        try:
            disc = date.fromisoformat(disc_s)
        except ValueError:
            disc = None

    asset = (
        str(
            raw.get("company_name")
            or raw.get("assetName")
            or raw.get("asset_name")
            or (result.title or "")
        )[:512]
        or "Asset"
    )
    tkr = str(raw.get("ticker") or raw.get("symbol") or "").strip()
    tkr = tkr[:16] if tkr else None
    tx = _label_transaction_type(result, raw)[:32]
    amt = str(raw.get("amount_range") or raw.get("amount") or "")[:64] or "Unknown"
    own = str(raw.get("owner") or "").strip()
    own = own[:64] if own else None
    src = (result.source_url or "").strip() or "https://www.senate.gov/"

    bg = (bioguide_id or "").strip()[:32] or None

    st_id = stable_stock_trade_id(
        str(case_id),
        bg or "",
        td.isoformat(),
        asset,
        tx,
        amt,
        result.source_url or "",
    )
    if db.get(StockTrade, st_id):
        return
    st = StockTrade(
        id=st_id,
        case_file_id=case_id,
        bioguide_id=bg,
        transaction_date=td,
        disclosure_date=disc,
        asset_name=asset,
        asset_ticker=tkr,
        asset_type=None,
        transaction_type=tx,
        amount_range=amt,
        owner=own,
        source_url=src,
        entered_at=datetime.now(timezone.utc),
    )
    db.add(st)
