"""StockTradesAdapter and House XML parse helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from adapters.stock_trades import (
    StockTradesAdapter,
    parse_house_financial_disclosure_xml,
    stable_stock_trade_id,
)


def test_stable_stock_trade_id_64_char_hex() -> None:
    h = stable_stock_trade_id("a", "b", "2024-01-01")
    assert len(h) == 64
    assert h == stable_stock_trade_id("a", "b", "2024-01-01")


def test_parse_house_ptr_xml_picks_transaction_blocks() -> None:
    xml = """<?xml version="1.0"?>
<Root>
  <Transaction>
    <Asset>Example Corp</Asset>
    <Ticker>EXMPL</Ticker>
    <Date>2024-06-15</Date>
    <Type>purchase</Type>
    <Amount>$1,001 - $15,000</Amount>
  </Transaction>
</Root>
"""
    rows = parse_house_financial_disclosure_xml(xml, default_source_url="https://example.com/f.xml")
    assert len(rows) == 1
    r = rows[0]
    assert "Example" in r["asset_name"] or r["asset_name"] == "Example Corp"
    assert r.get("ticker") in ("", "EXMPL")


def _fake_async_client(*_a, **_k):
    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return False

    return _C()


def test_stock_trades_house_xml_prefix_fetches() -> None:
    xml = """<Root><Transaction><Asset>ACME</Asset><Date>2025-01-10</Date><Type>sale</Type>
<Amount>1001-15000</Amount></Transaction></Root>"""
    mock_resp = MagicMock()
    mock_resp.text = xml

    async def _mock_retry(*_a, **_k):
        return mock_resp

    async def _go():
        with (
            patch("adapters.stock_trades.httpx.AsyncClient", _fake_async_client),
            patch("adapters.stock_trades.async_http_request_with_retry", new=_mock_retry),
        ):
            return await StockTradesAdapter().search("house_xml:https://disclosures.example.com/f.xml", "house")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) >= 1
    assert "ACME" in (r.results[0].title or "")


def test_stock_trades_senate_empty_without_network() -> None:
    """Senate path returns honest empty if eFTDS does not return JSON bodies."""
    mock_resp = MagicMock()
    mock_resp.text = ""

    async def _mock_retry(*_a, **_k):
        return mock_resp

    async def _go():
        with (
            patch("adapters.stock_trades.httpx.AsyncClient", _fake_async_client),
            patch("adapters.stock_trades.async_http_request_with_retry", new=_mock_retry),
        ):
            return await StockTradesAdapter().search("Nothere", "senate")

    r = asyncio.run(_go())
    assert r.found is True
    assert r.results == []
    assert r.empty_success
