"""EthicsComplaintsAdapter — OCE public reports list HTML."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.ethics_complaints import EthicsComplaintsAdapter

_OCE_LIST_HTML = """
<!DOCTYPE html>
<html><body>
<div class="view-content">
  <div class="views-row evo-views-row">
    <div class="views-field-title">
      <a href="/reports/occ-report-regarding-mr-doe-2023">OCC Report Regarding Rep. John Doe (2023)</a>
    </div>
    <div class="views-field-field-evo-article-type">
      <a href="/reports/investigation">OCE Investigations and Reports</a>
    </div>
    <time datetime="2023-10-12T00:00:00Z">10/12/2023</time>
    <div class="views-field-body">
      <div class="field-content">
        Dismissal recommendation transmitted to the House Ethics Committee.
        <a href="/files/static/public-report-2023-77.pdf">Public Report (PDF)</a>
      </div>
    </div>
  </div>
  <div class="views-row evo-views-row">
    <div class="views-field-title">
      <a href="/reports/quarter-2021-q1">OCE 2021 First Quarter Report</a>
    </div>
    <div class="views-field-field-evo-article-type">
      <a href="/reports">Quarterly Reports</a>
    </div>
    <time datetime="2021-04-15T00:00:00Z">04/15/2021</time>
    <div class="views-field-body">
      <div class="field-content">Aggregate activity summary.</div>
    </div>
  </div>
</div>
</body></html>
"""


def test_ethics_rejects_short_name() -> None:
    r = asyncio.run(EthicsComplaintsAdapter().search("x", "name"))
    assert r.found is False


def test_ethics_parses_matching_row() -> None:
    async def fake_get(_u, **_k):
        r = MagicMock()
        r.status_code = 200
        r.text = _OCE_LIST_HTML
        return r

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _go():
        with patch("adapters.ethics_complaints.httpx.AsyncClient", return_value=mock_client):
            return await EthicsComplaintsAdapter().search("Doe", "name")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) == 1
    row = r.results[0]
    assert row.entry_type == "ethics_issue"
    assert "Doe" in (row.title or "")
    assert row.source_url and row.source_url.endswith(".pdf")
    raw = row.raw_data or {}
    assert raw.get("source_body") == "OCE"
    assert raw.get("chamber") == "House"
    assert raw.get("source_url", "").endswith(".pdf")
