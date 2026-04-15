"""CourtListener judicial adapter — HTTP mocked; no live API key required."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from adapters.courtlistener import (
    CourtListenerAdapter,
    courtlistener_court_ids_from_jurisdiction,
    split_judge_name,
)


def test_split_judge_name_strips_suffix() -> None:
    assert split_judge_name("James R. Sweeney II") == ("James", "Sweeney")


def test_courtlistener_court_ids_southern_indiana() -> None:
    assert courtlistener_court_ids_from_jurisdiction(
        "U.S. District Court, Southern District of Indiana"
    ) == ["insd"]


def _ok_response(data: dict, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=data)
    m.text = ""
    m.raise_for_status = MagicMock()
    return m


def _run(adapter: CourtListenerAdapter, q: str):
    return asyncio.run(adapter.search(q, "judge"))


def test_courtlistener_profile_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("COURTLISTENER_API_KEY", raising=False)

    person = {
        "id": 42,
        "slug": "jane-smith",
        "name_first": "Jane",
        "name_last": "Smith",
        "fjc_id": 12345,
    }
    positions = {
        "results": [
            {
                "court": {"id": "insd"},
                "position_type": "jud",
                "job_title": "",
                "date_start": "2021-01-01",
            }
        ]
    }
    disclosures = {"results": []}

    async def fake_get(url: str, **kwargs):
        u = str(url)
        if "/people/" in u:
            return _ok_response({"results": [person]})
        if "/positions/" in u and "person=42" in u:
            return _ok_response(positions)
        if "/financial-disclosures/" in u:
            return _ok_response(disclosures)
        return _ok_response({"results": []})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=fake_get)

    ad = CourtListenerAdapter()
    ad.court_ids = ["insd"]

    with patch("adapters.courtlistener.httpx.AsyncClient", return_value=mock_client):
        resp = _run(ad, "Jane R. Smith|insd")

    assert resp.found is True
    assert len(resp.results) >= 1
    assert resp.results[0].entry_type == "judicial_index"
    assert "insd" in resp.results[0].body
    assert resp.credential_mode == "credential_unavailable"
    assert resp.parse_warning and "COURTLISTENER_API_KEY" in resp.parse_warning


def test_courtlistener_opinions_with_api_key(monkeypatch) -> None:
    monkeypatch.setenv("COURTLISTENER_API_KEY", "test-token")

    person = {
        "id": 7,
        "slug": "judge-seven",
        "name_first": "Pat",
        "name_last": "Judge",
        "fjc_id": 1,
    }
    positions = {
        "results": [
            {
                "court": {"id": "insd"},
                "position_type": "jud",
                "job_title": "",
                "date_start": "2019-01-01",
            }
        ]
    }
    disclosures = {"results": []}
    opinions_page = {
        "results": [
            {
                "case_name": "Alpha v. Beta",
                "date_filed": "2022-06-15",
                "absolute_url": "https://www.courtlistener.com/opinion/1/",
                "cluster": {"citation_count": 50},
            },
            {
                "case_name": "Gamma v. Delta",
                "date_filed": "2021-01-10",
                "absolute_url": "https://www.courtlistener.com/opinion/2/",
                "cluster": {"citation_count": 3},
            },
        ],
        "next": None,
    }
    dockets_page = {
        "results": [
            {"case_name": "In re sanctions motion", "docket_number": "1:20-cv-1"},
            {"case_name": "Smith recusal motion", "docket_number": "1:21-cv-9"},
        ],
        "next": None,
    }

    async def fake_get(url: str, **kwargs):
        u = str(url)
        if "/people/" in u:
            return _ok_response({"results": [person]})
        if "/positions/" in u and "person=7" in u:
            return _ok_response(positions)
        if "/financial-disclosures/" in u:
            return _ok_response(disclosures)
        if "/opinions/" in u:
            return _ok_response(opinions_page)
        if "/dockets/" in u:
            return _ok_response(dockets_page)
        return _ok_response({"results": [], "next": None})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=fake_get)

    ad = CourtListenerAdapter()
    ad.court_ids = ["insd"]

    with patch("adapters.courtlistener.httpx.AsyncClient", return_value=mock_client):
        resp = _run(ad, "Pat Judge|insd")

    assert resp.found is True
    types = {r.entry_type for r in resp.results}
    assert "court_opinion_summary" in types
    recusal = [r for r in resp.results if "recusal" in r.title.lower()]
    sanction = [r for r in resp.results if "sanctions" in r.title.lower()]
    assert len(recusal) == 1
    assert len(sanction) == 1
    assert resp.credential_mode == "ok"


def test_courtlistener_http_error_is_network_failure(monkeypatch) -> None:
    monkeypatch.delenv("COURTLISTENER_API_KEY", raising=False)

    async def boom(url: str, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=boom)

    ad = CourtListenerAdapter()
    ad.court_ids = ["insd"]
    with patch("adapters.courtlistener.httpx.AsyncClient", return_value=mock_client):
        resp = _run(ad, "Jane Smith|insd")

    assert resp.found is False
    assert resp.error_kind == "network"
