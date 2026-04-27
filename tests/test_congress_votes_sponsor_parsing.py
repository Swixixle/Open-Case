"""Sponsor / cosponsor bioguide extraction for Congress.gov bill payloads."""

from __future__ import annotations

from adapters.congress_votes import (
    _bioguides_from_bill_endpoint,
    _bioguides_sponsors_from_full_bill_payload,
)


def test_sponsors_from_full_bill_uses_bill_sponsors() -> None:
    payload = {
        "bill": {
            "sponsors": [
                {"bioguideId": "A000001"},
            ],
        }
    }
    assert _bioguides_sponsors_from_full_bill_payload(payload) == {"A000001"}


def test_sponsors_from_full_bill_wrapped_sponsor() -> None:
    """Some responses nest items under ``sponsor`` (single or list)."""
    payload = {
        "bill": {
            "sponsors": {
                "sponsor": [
                    {"bioguideId": "B000002"},
                ],
            }
        }
    }
    assert _bioguides_sponsors_from_full_bill_payload(payload) == {"B000002"}


def test_cosponsor_endpoint_uses_top_level_cosponsors() -> None:
    payload = {
        "cosponsors": [
            {"bioguideId": "C000003"},
        ],
    }
    assert _bioguides_from_bill_endpoint(payload) == {"C000003"}


def test_sponsors_missing_returns_empty() -> None:
    assert _bioguides_sponsors_from_full_bill_payload({}) == set()
    assert _bioguides_sponsors_from_full_bill_payload({"bill": {}}) == set()
