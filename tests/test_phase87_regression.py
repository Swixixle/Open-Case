"""Phase 8.7 — FEC API error payloads must not masquerade as clean empty runs."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.fec import FECAdapter


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
