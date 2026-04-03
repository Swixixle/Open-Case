"""FEC Schedule B adapter — disbursement rows as fec_disbursement evidence."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from adapters.fec import FECAdapter


def test_schedule_b_fetched() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "disbursement_amount": 5400.0,
                "disbursement_date": "2024-06-15",
                "recipient_name": "Joint Victory Fund",
                "recipient_committee_id": "C00888888",
                "committee_id": "C00777777",
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("adapters.fec.httpx.AsyncClient", return_value=mock_client),
        patch("adapters.fec.CredentialRegistry.get_credential", return_value="DEMO_KEY"),
    ):
        out = asyncio.run(FECAdapter().search_schedule_b("C00777777"))

    assert out.found is True
    assert len(out.results) == 1
    assert out.results[0].entry_type == "fec_disbursement"
    raw = out.results[0].raw_data or {}
    assert raw.get("recipient_committee_id") == "C00888888"
    assert float(raw.get("disbursement_amount") or 0) == 5400.0
