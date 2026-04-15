"""FEC Schedule A HTTP errors must not look like clean empty runs."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.congress_votes import CongressVotesAdapter
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.usa_spending import USASpendingAdapter
from tests.test_fec_congress_adapter_fixtures import (
    _congress_vote_response,
    _fec_financial_response,
    _stub_empty,
)


def _mock_schedule_response(status_code: int, json_body: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = json_body
    mock_response.text = str(json_body)
    req = MagicMock()
    req.url = "https://api.open.fec.gov/v1/schedules/schedule_a/?api_key=***"
    mock_response.request = req
    return mock_response


def _run_fec(adapter: FECAdapter, query: str, query_type: str = "committee"):
    return asyncio.run(adapter.search(query, query_type))


@pytest.fixture
def mock_fec_invalid_key_response():
    """OpenFEC-style JSON when the API key is rejected (HTTP 200 with error object)."""
    return _mock_schedule_response(
        200,
        {
            "error": {
                "code": "API_KEY_INVALID",
                "message": "Invalid api_key",
            }
        },
    )


def test_fec_api_error_payload_returns_failure_not_clean(mock_fec_invalid_key_response):
    """
    When FEC API returns {"error": {"code": "API_KEY_INVALID", ...}},
    the adapter must return a credential failure, not clean + zero rows.
    """
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_fec_invalid_key_response)

    with patch("adapters.fec.httpx.AsyncClient", return_value=mock_client):
        response = _run_fec(FECAdapter(), "C00459255", "committee")

    assert response.error is not None
    assert response.error_kind == "credential"
    assert "API_KEY_INVALID" in response.error
    assert "credential_mode=" in response.error
    assert len(response.results) == 0
    assert response.found is False


def test_fec_schedule_a_422_retries_without_two_year_transaction_period(monkeypatch):
    """Some committees reject an explicit cycle; adapter drops two_year and retries once."""
    monkeypatch.setenv("FEC_API_KEY", "DEMO_KEY")
    ok_body = {
        "results": [
            {
                "contribution_receipt_amount": 100,
                "contribution_receipt_date": "2024-06-01",
                "committee": {"name": "Friends of Example", "committee_type": "H"},
                "contributor_name": "DONOR PAC",
                "entity_type": "ORG",
            }
        ],
        "pagination": {},
    }
    resp422 = _mock_schedule_response(422, {"errors": ["Invalid parameter"]})
    resp200 = _mock_schedule_response(200, ok_body)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=[resp422, resp200])

    with patch("adapters.fec.httpx.AsyncClient", return_value=mock_client):
        response = asyncio.run(
            FECAdapter().search("C00459255", "committee", two_year_transaction_period=2024)
        )

    assert response.found is True
    assert len(response.results) == 1
    assert mock_client.get.call_count == 2


def test_fec_schedule_a_committee_422_after_retry_returns_gap_documented(monkeypatch):
    """Persistent HTTP 422 (e.g. some principal committees) becomes gap_documented, not an error."""
    monkeypatch.setenv("FEC_API_KEY", "DEMO_KEY")
    resp422 = _mock_schedule_response(422, {"errors": ["Invalid parameter"]})
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=[resp422, resp422])

    with patch("adapters.fec.httpx.AsyncClient", return_value=mock_client):
        response = asyncio.run(
            FECAdapter().search("C00459255", "committee", two_year_transaction_period=2024)
        )

    assert response.found is True
    assert response.error is None
    assert len(response.results) == 1
    row = response.results[0]
    assert row.entry_type == "gap_documented"
    assert row.raw_data.get("gap_reason") == "fec_schedule_a_http_422_committee"
    assert mock_client.get.call_count == 2


def test_fec_bad_env_key_surfaces_as_credential_failure(
    monkeypatch, mock_fec_invalid_key_response
):
    """
    If FEC_API_KEY env var is set to an invalid value,
    the adapter must not silently return clean + zero rows.
    """
    monkeypatch.setenv("FEC_API_KEY", "INVALID_KEY_VALUE_XYZ")
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_fec_invalid_key_response)

    with patch("adapters.fec.httpx.AsyncClient", return_value=mock_client):
        response = _run_fec(FECAdapter(), "C00459255", "committee")

    assert response.error_kind == "credential"
    assert response.error is not None
    assert "env" in response.error


def test_investigate_debug_query_param_gates_diagnostics(
    client, seeded_public_official_case
) -> None:
    """Phase 9A: pairing_diagnostics / source_row_counts only when ?debug=true."""
    case_id = seeded_public_official_case["case_id"]
    handle = seeded_public_official_case["handle"]
    api_key = seeded_public_official_case["api_key"]

    async def fec_search(query: str, query_type: str = "person", **_kw):
        assert query_type == "committee"
        assert query == "C00459255"
        return _fec_financial_response(query)

    patches = (
        patch(
            "routes.investigate.resolve_principal_committee_id_for_official",
            new_callable=AsyncMock,
            return_value="C00459255",
        ),
        patch.object(FECAdapter, "search", new_callable=AsyncMock, side_effect=fec_search),
        patch.object(
            USASpendingAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("USASpending"),
        ),
        patch.object(
            IndianaCFAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_stub_empty("Indiana Campaign Finance"),
        ),
        patch.object(
            CongressVotesAdapter,
            "search",
            new_callable=AsyncMock,
            return_value=_congress_vote_response(),
        ),
    )
    payload = {
        "subject_name": "Todd Young",
        "investigator_handle": handle,
        "bioguide_id": "Y000064",
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        r_clean = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json=payload,
            headers=headers,
        )
    assert r_clean.status_code == 200, r_clean.text
    data_clean = r_clean.json()
    assert "source_row_counts" not in data_clean
    assert "pairing_diagnostics" not in data_clean
    assert data_clean.get("signals")
    assert data_clean["signals"][0].get("entity_name")

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        r_dbg = client.post(
            f"/api/v1/cases/{case_id}/investigate?debug=true",
            json=payload,
            headers=headers,
        )
    assert r_dbg.status_code == 200, r_dbg.text
    data_dbg = r_dbg.json()
    assert "source_row_counts" in data_dbg
    assert "pairing_diagnostics" in data_dbg
