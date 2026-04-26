"""BillSponsorshipAdapter — Congress.gov sponsored/cosponsored lists."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.bill_sponsorship import BillSponsorshipAdapter, public_congress_gov_bill_url


def test_public_congress_gov_bill_url_house_and_senate() -> None:
    assert "house-bill/1" in public_congress_gov_bill_url(118, "hr", "1")
    assert "senate-bill/2" in public_congress_gov_bill_url(118, "s", "2")


def test_bill_sponsorship_requires_bioguide_shape() -> None:
    r = asyncio.run(BillSponsorshipAdapter().search("bad", "both"))
    assert r.found is False
    assert r.error_kind == "processing"


def test_bill_sponsorship_missing_credential() -> None:
    with patch(
        "adapters.bill_sponsorship.CredentialRegistry.get_credential", return_value=None
    ):
        r = asyncio.run(BillSponsorshipAdapter().search("Y000064", "both"))
    assert r.error_kind == "credential"


def test_bill_sponsorship_parses_sponsored_and_cosponsored() -> None:
    bill = {
        "congress": 118,
        "number": "7024",
        "type": "HR",
        "title": "Example Act",
        "introducedDate": "2024-01-15",
        "policyArea": {"name": "Commerce"},
        "latestAction": {"text": "Introduced in House"},
    }

    async def fake_get(url: str, **_kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "cosponsored-legislation" in url:
            resp.json = lambda: {
                "cosponsoredLegislation": [],
                "pagination": {},
            }
        else:
            resp.json = lambda: {
                "sponsoredLegislation": [bill],
                "pagination": {},
            }
        return resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _go():
        with (
            patch(
                "adapters.bill_sponsorship.CredentialRegistry.get_credential",
                return_value="test-key",
            ),
            patch("adapters.bill_sponsorship.httpx.AsyncClient", return_value=mock_client),
        ):
            return await BillSponsorshipAdapter().search("Y000064", "both")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) == 1
    assert r.results[0].entry_type == "bill_sponsorship"
    assert "H.R" in (r.results[0].raw_data or {}).get("bill_number", "")
    assert (r.results[0].raw_data or {}).get("role") == "sponsor"
    assert "Sponsored" in (r.results[0].title or "")
