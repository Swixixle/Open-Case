"""Shared FEC/Congress adapter stubs and committee-resolution investigate path."""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from adapters.base import AdapterResponse, AdapterResult
from adapters.congress_votes import CongressVotesAdapter
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.usa_spending import USASpendingAdapter


def _stub_empty(source_name: str) -> AdapterResponse:
    return AdapterResponse(
        source_name=source_name,
        query="q",
        results=[],
        found=True,
        empty_success=True,
        parse_warning="stub",
        credential_mode="ok",
    )


def _fec_financial_response(committee_id: str) -> AdapterResponse:
    return AdapterResponse(
        source_name="FEC",
        query=committee_id,
        results=[
            AdapterResult(
                source_name="FEC",
                source_url="https://www.fec.gov/data/receipts/",
                entry_type="financial_connection",
                title="FEC Donation: $500 to Friends of Example",
                body="ACME PAC donated.",
                date_of_event="2025-06-15",
                amount=500.0,
                matched_name="ACME PAC",
                raw_data={
                    "contribution_receipt_date": "2025-06-15",
                    "committee": {"name": "FRIENDS OF EXAMPLE"},
                },
            )
        ],
        found=True,
        credential_mode="ok",
    )


def _congress_vote_response() -> AdapterResponse:
    return AdapterResponse(
        source_name=CongressVotesAdapter.source_name,
        query="Y000064",
        results=[
            AdapterResult(
                source_name=CongressVotesAdapter.source_name,
                source_url="https://www.senate.gov/",
                entry_type="vote_record",
                title="Vote: Yea on S. 1 (119th Congress)",
                body="Vote body",
                date_of_event="2025-06-20",
                matched_name="Todd Young",
                raw_data={"subject_is_sponsor": False},
            )
        ],
        found=True,
        credential_mode="ok",
    )


def test_fec_produces_financial_evidence_without_committee_id(
    client, seeded_public_official_case
) -> None:
    """Principal committee resolution + schedule_a by committee yields financial rows."""
    case_id = seeded_public_official_case["case_id"]
    handle = seeded_public_official_case["handle"]
    api_key = seeded_public_official_case["api_key"]

    async def fec_search(query: str, query_type: str = "person", **_kw: Any):
        assert query_type == "committee"
        assert query == "C00459255"
        return _fec_financial_response(query)

    with (
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
    ):
        response = client.post(
            f"/api/v1/cases/{case_id}/investigate?debug=true",
            json={
                "subject_name": "Todd Young",
                "investigator_handle": handle,
                "bioguide_id": "Y000064",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source_row_counts"]["fec_raw_results"] > 0
    assert data["source_row_counts"]["fec_evidence_written"] > 0
    assert data["pairing_diagnostics"]["temporal"]["financial_entries_seen"] > 0
    sigs = data.get("signals") or []
    assert sigs and sigs[0].get("entity_name")
    assert sigs[0]["entity_name"] != "Signal"


def test_congress_produces_votes_for_known_bioguide(client, seeded_public_official_case) -> None:
    """Two successive investigates both see nonzero congress_raw_results (no stale empty cache)."""
    case_id = seeded_public_official_case["case_id"]
    handle = seeded_public_official_case["handle"]
    api_key = seeded_public_official_case["api_key"]

    async def fec_search(_query: str, query_type: str = "person", **_kw: Any):
        return _fec_financial_response("C00459255")

    payload = {
        "subject_name": "Todd Young",
        "investigator_handle": handle,
        "bioguide_id": "Y000064",
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    for _ in range(2):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "routes.investigate.resolve_principal_committee_id_for_official",
                    new_callable=AsyncMock,
                    return_value="C00459255",
                )
            )
            stack.enter_context(
                patch.object(
                    FECAdapter, "search", new_callable=AsyncMock, side_effect=fec_search
                )
            )
            stack.enter_context(
                patch.object(
                    USASpendingAdapter,
                    "search",
                    new_callable=AsyncMock,
                    return_value=_stub_empty("USASpending"),
                )
            )
            stack.enter_context(
                patch.object(
                    IndianaCFAdapter,
                    "search",
                    new_callable=AsyncMock,
                    return_value=_stub_empty("Indiana Campaign Finance"),
                )
            )
            stack.enter_context(
                patch.object(
                    CongressVotesAdapter,
                    "search",
                    new_callable=AsyncMock,
                    return_value=_congress_vote_response(),
                )
            )
            response = client.post(
                f"/api/v1/cases/{case_id}/investigate?debug=true",
                json=payload,
                headers=headers,
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["source_row_counts"]["congress_raw_results"] > 0


def test_http_error_not_cached_for_congress(client, seeded_public_official_case) -> None:
    """Failed Congress fetches must not populate AdapterCache (retry next run)."""
    case_id = seeded_public_official_case["case_id"]
    handle = seeded_public_official_case["handle"]
    api_key = seeded_public_official_case["api_key"]

    async def fec_search(_query: str, query_type: str = "person", **_kw: Any):
        return _fec_financial_response("C00459255")

    with (
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
            side_effect=httpx.ConnectTimeout("timeout"),
        ),
    ):
        r1 = client.post(
            f"/api/v1/cases/{case_id}/investigate",
            json={
                "subject_name": "Todd Young",
                "investigator_handle": handle,
                "bioguide_id": "Y000064",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert r1.status_code == 422

    with (
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
    ):
        r2 = client.post(
            f"/api/v1/cases/{case_id}/investigate?debug=true",
            json={
                "subject_name": "Todd Young",
                "investigator_handle": handle,
                "bioguide_id": "Y000064",
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert r2.status_code == 200
    assert r2.json()["source_row_counts"]["congress_raw_results"] > 0
