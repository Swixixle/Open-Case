"""CommitteeAssignmentsAdapter — Congress.gov member committee assignments."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.committee_assignments import (
    CommitteeAssignmentsAdapter,
    public_congress_gov_url_from_api,
)


def test_public_congress_gov_url_maps_api_v3() -> None:
    u = "https://api.congress.gov/v3/committee/senate/ssju"
    assert "www.congress.gov/committee/senate/ssju" in public_congress_gov_url_from_api(
        u
    )


def test_committee_assignments_needs_valid_bioguide() -> None:
    r = asyncio.run(CommitteeAssignmentsAdapter().search("x", "current"))
    assert r.found is False


def test_committee_assignments_no_key() -> None:
    with patch(
        "adapters.committee_assignments.CredentialRegistry.get_credential",
        return_value=None,
    ):
        r = asyncio.run(CommitteeAssignmentsAdapter().search("Y000064", "current"))
    assert r.error_kind == "credential"


def test_committee_from_member_json() -> None:
    data = {
        "member": {
            "bioguideId": "Y000064",
            "committeeAssignments": [
                {
                    "congress": 119,
                    "chamber": "Senate",
                    "systemCode": "SSFI",
                    "name": "Committee on Finance",
                    "committeeType": "Standing",
                    "url": "https://api.congress.gov/v3/committee/senate/ssfi",
                }
            ],
        }
    }
    member_ok = MagicMock()
    member_ok.status_code = 200
    member_ok.json = lambda: data
    sub_empty = MagicMock()
    sub_empty.status_code = 404

    async def fake_get(url: str, **_kwargs):
        if "/committee-assignments" in url:
            return sub_empty
        return member_ok

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _go():
        with (
            patch(
                "adapters.committee_assignments.CredentialRegistry.get_credential",
                return_value="k",
            ),
            patch("adapters.committee_assignments.httpx.AsyncClient", return_value=mock_client),
            patch(
                "adapters.committee_assignments.current_congress_number",
                return_value=119,
            ),
        ):
            return await CommitteeAssignmentsAdapter().search("Y000064", "all")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) >= 1
    assert r.results[0].entry_type == "committee_assignment"
    assert r.results[0].raw_data.get("committee_code") == "SSFI"
    assert "Committee" in (r.results[0].title or "")
