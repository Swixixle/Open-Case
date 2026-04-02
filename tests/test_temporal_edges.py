"""Phase 9A — temporal pairing invariants (defensibility suite)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from engines.temporal_proximity import detect_proximity


def _fin(
    *,
    d_event: date | datetime,
    amount: float = 10_000.0,
    name: str = "Shared Donor PAC",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        entry_type="financial_connection",
        date_of_event=d_event,
        amount=amount,
        matched_name=name,
        flagged_for_review=False,
        raw_data_json="{}",
        title="Donation",
        jurisdictional_match=False,
    )


def _vote(
    *,
    d_event: date | datetime,
    official: str = "Senator X",
    title: str = "Vote: Yea on S. 100 (119th Congress)",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        entry_type="vote_record",
        date_of_event=d_event,
        matched_name=official,
        raw_data_json='{"subject_is_sponsor": false}',
        title=title,
    )


def test_donation_after_vote_is_not_anticipatory() -> None:
    """Donation following a vote must be retrospective, not anticipatory."""
    vote_d = date(2025, 6, 1)
    donation_d = date(2025, 6, 20)
    entries = [_fin(d_event=donation_d), _vote(d_event=vote_d)]
    clusters, stats = detect_proximity(entries, max_days=90, committee_label="")
    assert stats["pairs_emitted"] >= 1
    assert len(clusters) >= 1
    assert all(c.temporal_class != "anticipatory" for c in clusters)
    assert clusters[0].temporal_class == "retrospective"


def test_donation_exactly_at_proximity_boundary_pairs() -> None:
    """Gap == proximity_days (vote after donation) must pair."""
    donation_d = date(2025, 1, 1)
    vote_d = date(2025, 4, 1)
    assert (vote_d - donation_d).days == 90
    entries = [_fin(d_event=donation_d), _vote(d_event=vote_d)]
    clusters, stats = detect_proximity(entries, max_days=90, committee_label="")
    assert stats["pairs_skipped_window"] == 0
    assert stats["pairs_emitted"] >= 1
    assert len(clusters) >= 1


def test_donation_beyond_proximity_boundary_does_not_pair() -> None:
    """Gap == proximity_days + 1 must not pair."""
    donation_d = date(2025, 1, 1)
    vote_d = date(2025, 4, 2)
    assert (vote_d - donation_d).days == 91
    entries = [_fin(d_event=donation_d), _vote(d_event=vote_d)]
    clusters, stats = detect_proximity(entries, max_days=90, committee_label="")
    assert stats["pairs_emitted"] == 0
    assert len(clusters) == 0


def test_same_day_donation_and_vote_pairs() -> None:
    """Gap == 0 is a valid high-weight (same-day) pairing."""
    d = date(2025, 7, 4)
    entries = [_fin(d_event=d), _vote(d_event=d)]
    clusters, stats = detect_proximity(entries, max_days=90, committee_label="")
    assert stats["pairs_emitted"] >= 1
    assert len(clusters) == 1
    assert clusters[0].exemplar_gap == 0
    assert clusters[0].temporal_class == "anticipatory"


def test_orm_date_vs_datetime_does_not_break_pairing() -> None:
    """date-only financial vs timezone-aware vote must pair on calendar semantics."""
    donation = date(2025, 3, 10)
    vote_dt = datetime(2025, 3, 20, 15, 30, tzinfo=timezone.utc)
    entries = [_fin(d_event=donation), _vote(d_event=vote_dt)]
    clusters, stats = detect_proximity(entries, max_days=90, committee_label="")
    assert stats["pairs_skipped_missing_datetime"] == 0
    assert stats["pairs_emitted"] >= 1
    assert len(clusters) == 1
    assert clusters[0].min_gap_days == 10
