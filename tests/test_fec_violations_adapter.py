"""FECViolationsAdapter — OpenFEC legal search (MUR, AF, ADR)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.fec_violations import FECViolationsAdapter, _source_url


def test_source_url_relative_path() -> None:
    assert _source_url("/legal/matter-under-review/1/").startswith("https://www.fec.gov")


def test_fec_violations_empty_query() -> None:
    r = asyncio.run(FECViolationsAdapter().search("", "committee"))
    assert r.found is False


def test_fec_violations_murs_admin_adrs_parsed() -> None:
    mur = {
        "no": "1",
        "open_date": "2020-01-01T00:00:00",
        "subjects": [{"subject": "Prohibited contributions"}],
        "respondents": ["Comm A"],
        "url": "/legal/matter-under-review/1/",
    }
    admin = {
        "no": "99",
        "name": "Late filing",
        "final_determination_amount": 500,
    }
    adr = {
        "no": "2",
        "name": "ADR matter",
    }
    async def fake_get(_url, params=None, **_k):
        t = (params or {}).get("type", "")
        resp = MagicMock()
        resp.status_code = 200
        if t == "murs":
            resp.json = lambda: {"murs": [mur]}
        elif t == "admin_fines":
            resp.json = lambda: {"admin_fines": [admin]}
        elif t == "adrs":
            resp.json = lambda: {"adrs": [adr]}
        else:
            resp.json = lambda: {}
        return resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _go():
        with (
            patch(
                "adapters.fec_violations.CredentialRegistry.get_credential",
                return_value="k",
            ),
            patch("adapters.fec_violations.httpx.AsyncClient", return_value=mock_client),
            patch(
                "adapters.fec_violations._committee_display_name",
                AsyncMock(return_value="Test Committee"),
            ),
        ):
            return await FECViolationsAdapter().search("C00123456", "committee")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) == 3
    et = {x.entry_type for x in r.results}
    assert et == {"fec_violation"}
    titles = {x.title for x in r.results}
    assert any("MUR" in t for t in titles)
    raw0 = r.results[0].raw_data or {}
    assert json.loads(raw0.get("respondent_names") or "[]")
