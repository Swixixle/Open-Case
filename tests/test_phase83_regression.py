"""Phase 8.3 — temporal proximity emits at least one cluster for a known-good window."""
from __future__ import annotations

import uuid
from datetime import date

from engines.temporal_proximity import detect_proximity


class _E:
    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def test_temporal_proximity_emits_pair_for_known_good_window() -> None:
    """
    One donation (2025-12-13) and one vote (2025-12-18) — gap 5 days, window 90.
    Runs detect_proximity end-to-end (pairing + clustering), not datetime arithmetic alone.
    """
    donation = _E(
        entry_type="financial_connection",
        date_of_event=date(2025, 12, 13),
        amount=21336.0,
        title="Contribution",
        matched_name="MASS MUTUAL",
        flagged_for_review=False,
        id=uuid.uuid4(),
    )
    vote = _E(
        entry_type="vote_record",
        date_of_event=date(2025, 12, 18),
        title="Vote: Yea on PN12 (119th Congress)",
        matched_name="Todd Young",
        id=uuid.uuid4(),
    )
    clusters, pairing_stats = detect_proximity(
        [donation, vote],
        max_days=90,
        committee_label="Test committee",
    )
    assert len(clusters) == 1, (
        f"Expected 1 cluster for 5-day gap within 90-day window, got {len(clusters)}"
    )
    assert pairing_stats["pairs_emitted"] >= 1
    assert pairing_stats["candidate_pairs_examined"] == 1
