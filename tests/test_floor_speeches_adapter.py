"""FloorSpeechesAdapter — Congress.gov Congressional Record issues."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.floor_speeches import FloorSpeechesAdapter


def test_floor_speeches_rejects_short_bioguide() -> None:
    r = asyncio.run(FloorSpeechesAdapter().search("x", "bioguide"))
    assert r.found is False


def test_floor_speeches_parses_results_issues() -> None:
    payload = {
        "Results": {
            "Issues": [
                {
                    "Congress": "119",
                    "Volume": "170",
                    "Issue": "5",
                    "PublishDate": "2026-01-10",
                    "Links": {
                        "FullRecord": {
                            "PDF": [
                                {
                                    "Url": "https://www.congress.gov/119/crec/2026/01/10/170/5/x.pdf"
                                }
                            ]
                        },
                        "House": {
                            "PDF": [
                                {
                                    "Url": "https://www.congress.gov/119/crec/2026/01/10/170/5/h.pdf"
                                }
                            ]
                        },
                    },
                }
            ],
        }
    }

    async def fake_get(_u, params=None, **_k):
        r = MagicMock()
        r.status_code = 200
        r.json = lambda: payload
        return r

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _go():
        with (
            patch(
                "adapters.floor_speeches.CredentialRegistry.get_credential",
                return_value="k",
            ),
            patch("adapters.floor_speeches.httpx.AsyncClient", return_value=mock_client),
            patch("adapters.floor_speeches.current_congress_number", return_value=119),
        ):
            return await FloorSpeechesAdapter().search("Y000064", "bioguide")

    r = asyncio.run(_go())
    assert r.found is True
    assert len(r.results) == 1
    assert r.results[0].entry_type == "floor_speech"
    assert "170" in (r.results[0].title or "")
    assert (r.results[0].raw_data or {}).get("full_text_url", "").endswith(".pdf")
