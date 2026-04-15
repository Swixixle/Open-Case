"""Unit tests for IDIS bulk row subject matching (committee/candidate only)."""

from __future__ import annotations

from adapters.indiana_campaign_finance import _row_matches_subject_campaign


def test_idis_row_matches_hogsett_committee() -> None:
    row = {
        "Committee": "Hogsett for Indianapolis",
        "CandidateName": "",
        "Name": "Some Donor",
    }
    assert _row_matches_subject_campaign("Joe Hogsett", row) is True


def test_idis_row_rejects_donor_only_hogsett() -> None:
    row = {
        "Committee": "Indiana Democratic State Central Committee",
        "CandidateName": "",
        "Name": "WILL HOGSETT",
    }
    assert _row_matches_subject_campaign("Joe Hogsett", row) is False
