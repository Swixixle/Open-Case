"""FEC principal committee resolution (name + bioguide q fallback)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.fec import (
    _principal_committee_id_from_candidate_list,
    resolve_principal_committee_id_for_official,
)


def test_principal_committee_id_from_candidate_list_principal_designation() -> None:
    data = {
        "results": [
            {
                "state": "IN",
                "last_file_date": "2024-01-10",
                "candidate_inactive": False,
                "principal_committees": [
                    {
                        "committee_id": "C00459255",
                        "designation": "P",
                        "designation_full": "Principal campaign committee",
                    },
                ],
            }
        ]
    }
    assert (
        _principal_committee_id_from_candidate_list(data, state_filter="IN")
        == "C00459255"
    )


def test_resolve_principal_committee_falls_back_to_bioguide_q() -> None:
    """When name+office search returns no principal, try ``q=bioguide``."""
    empty_page = {"results": [], "pagination": {"count": 0}}
    bioguide_page = {
        "results": [
            {
                "state": "IN",
                "last_file_date": "2024-03-01",
                "candidate_inactive": False,
                "principal_committees": [
                    {
                        "committee_id": "C00459255",
                        "designation": "P",
                        "designation_full": "Principal campaign committee",
                    },
                ],
            }
        ],
        "pagination": {"count": 1},
    }

    async def fake_get(_url, params=None, **_k):
        r = MagicMock()
        r.request.url = "https://api.open.fec.gov/v1/candidates/search/?masked"
        if params and params.get("q") == "Y000064":
            r.status_code = 200
            r.text = ""
            r.json = lambda: bioguide_page
            return r
        r.status_code = 200
        r.text = ""
        r.json = lambda: empty_page
        return r

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    async def _run():
        with (
            patch(
                "adapters.fec.CredentialRegistry.get_credential",
                return_value="DEMO_KEY",
            ),
            patch("adapters.fec.httpx.AsyncClient", return_value=mock_client),
        ):
            return await resolve_principal_committee_id_for_official(
                "Todd Young", "IN", bioguide_id="Y000064"
            )

    out = asyncio.run(_run())
    assert out == "C00459255"
