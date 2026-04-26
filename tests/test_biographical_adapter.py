"""BiographicalProfile adapter (Congress.gov v3 member)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.biographical import BiographicalAdapter, parse_member_data


def test_parse_member_data_maps_office_and_party() -> None:
    member = {
        "directOrderName": "Young, Todd",
        "birthYear": "1972",
        "state": "IN",
        "partyHistory": [{"partyName": "Republican", "startYear": 2011}],
        "terms": {
            "item": [
                {
                    "chamber": "Senate",
                    "stateCode": "IN",
                    "startYear": 2017,
                    "endYear": None,
                }
            ]
        },
        "officialWebsiteUrl": "https://www.young.senate.gov",
    }
    profile = parse_member_data(member, "Y000064")
    assert profile["bioguide_id"] == "Y000064"
    assert profile["full_name"] == "Young, Todd"
    assert profile["party"] == "Republican"
    assert "Senate" in (profile.get("current_office") or "")
    assert "IN" in (profile.get("current_office") or "")
    assert profile["official_website"] == "https://www.young.senate.gov"


def test_parse_member_empty_terms() -> None:
    profile = parse_member_data(
        {
            "directOrderName": "Doe, Jane",
        },
        "D000000",
    )
    assert profile["current_office"] is None
    assert profile["previous_offices"] is None


def test_adapter_search_parses_success() -> None:
    payload = {
        "member": {
            "directOrderName": "Young, Todd",
            "partyHistory": [{"partyName": "R", "startYear": 2011}],
            "terms": {
                "item": [
                    {
                        "chamber": "Senate",
                        "stateCode": "IN",
                        "startYear": 2017,
                    }
                ]
            },
        }
    }
    rmock = MagicMock()
    rmock.status_code = 200
    rmock.json = lambda: payload

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=rmock)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    async def _go():
        with (
            patch(
                "adapters.biographical.CredentialRegistry.get_credential",
                return_value="fake-key",
            ),
            patch("adapters.biographical.httpx.AsyncClient", return_value=mock_client),
        ):
            return await BiographicalAdapter().search("Y000064", "bioguide")

    resp = asyncio.run(_go())
    assert resp.found is True
    assert len(resp.results) == 1
    assert resp.results[0].entry_type == "biographical_profile"
    raw = resp.results[0].raw_data
    assert raw.get("bioguide_id") == "Y000064"
    assert raw.get("full_name")


def test_adapter_missing_key_credential() -> None:
    async def _go():
        with patch(
            "adapters.biographical.CredentialRegistry.get_credential",
            return_value="",
        ):
            return await BiographicalAdapter().search("Y000064", "bioguide")

    resp = asyncio.run(_go())
    assert resp.found is False
    assert (resp.error_kind or "") == "credential"
