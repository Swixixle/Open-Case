"""FinancialDisclosuresAdapter (House XML + Senate guidance)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from adapters.financial_disclosures import (
    FinancialDisclosuresAdapter,
    parse_house_disclosure_line_items,
)


def _fake_async_client(*_a, **_k):
    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return False

    return _C()


def test_parse_house_reuses_ptr_parser() -> None:
    xml = """<Root><Transaction><Asset>ACME</Asset><Date>2025-01-10</Date><Type>sale</Type>
<Amount>1001-15000</Amount></Transaction></Root>"""
    rows = parse_house_disclosure_line_items(
        xml, default_url="https://example.com/x.xml", default_year=2025
    )
    assert len(rows) >= 1
    assert rows[0].get("category") == "PTR"


def test_senate_path_guidance() -> None:
    a = FinancialDisclosuresAdapter()
    r = asyncio.run(a.search("", "senate"))
    assert r.found
    assert "efdsearch" in (r.parse_warning or "").lower()


def test_house_auto_delegates_to_house() -> None:
    xml = """<Root><Transaction><Asset>ZZZ</Asset><Date>2024-01-01</Date><Type>sale</Type>
<Amount>1001-15000</Amount></Transaction></Root>"""
    mock_resp = MagicMock()
    mock_resp.text = xml

    async def _one(*_a, **_k):
        return mock_resp

    with (
        patch("adapters.financial_disclosures.httpx.AsyncClient", _fake_async_client),
        patch(
            "adapters.financial_disclosures.async_http_request_with_retry", new=_one
        ),
    ):
        r = asyncio.run(
            FinancialDisclosuresAdapter().search(
                "house_xml:https://h.example.com/a.xml", "auto"
            )
        )
    assert r.found
    assert len(r.results) >= 1
