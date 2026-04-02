"""Temporal donor-cluster pipeline: direction text must match gap sign."""
from __future__ import annotations

import uuid
from datetime import date

from engines.signal_scorer import build_signals_from_proximity
from engines.temporal_proximity import (
    assert_cluster_direction_verified,
    build_cluster_copy_text,
    detect_proximity,
    verify_cluster_direction_text,
)


class _E:
    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def test_detect_proximity_builds_cluster_and_direction_verified() -> None:
    fid = uuid.uuid4()
    vid = uuid.uuid4()
    fin = _E(
        entry_type="financial_connection",
        date_of_event=date(2025, 1, 1),
        amount=5000.0,
        title="Contribution",
        matched_name="ACME CORP",
        flagged_for_review=False,
        id=fid,
    )
    vote = _E(
        entry_type="vote_record",
        date_of_event=date(2025, 1, 10),
        title="Vote: Yea on S. 5 (119th Congress)",
        matched_name="Senator Smith",
        id=vid,
    )
    clusters, _stats = detect_proximity(
        [fin, vote], max_days=90, committee_label="Friends of Smith"
    )
    assert len(clusters) == 1
    c = clusters[0]
    assert c.exemplar_gap > 0
    assert c.temporal_class == "anticipatory"

    desc, summary = build_cluster_copy_text(c)
    assert verify_cluster_direction_text(c, desc, summary) is True
    assert_cluster_direction_verified(c, desc, summary)


def test_donation_after_vote_is_retrospective() -> None:
    fid = uuid.uuid4()
    vid = uuid.uuid4()
    fin = _E(
        entry_type="financial_connection",
        date_of_event=date(2025, 2, 15),
        amount=1000.0,
        title="Contribution",
        matched_name="Beta LLC",
        flagged_for_review=False,
        id=fid,
    )
    vote = _E(
        entry_type="vote_record",
        date_of_event=date(2025, 2, 1),
        title="Vote: Nay on H.R. 99 (119th Congress)",
        matched_name="Senator Smith",
        id=vid,
    )
    clusters, _stats = detect_proximity(
        [fin, vote], max_days=90, committee_label="Committee"
    )
    assert len(clusters) == 1
    c = clusters[0]
    assert c.exemplar_gap < 0
    assert c.temporal_class == "retrospective"
    desc, summary = build_cluster_copy_text(c)
    assert verify_cluster_direction_text(c, desc, summary) is True


def test_all_built_signals_have_direction_verified_true() -> None:
    fid = uuid.uuid4()
    vid = uuid.uuid4()
    fin = _E(
        entry_type="financial_connection",
        date_of_event=date(2025, 3, 1),
        amount=800.0,
        title="Contribution",
        matched_name="Gamma PAC",
        flagged_for_review=False,
        id=fid,
    )
    vote = _E(
        entry_type="vote_record",
        date_of_event=date(2025, 3, 5),
        title="Vote: Present on PN 12 (119th Congress)",
        matched_name="Senator Jones",
        id=vid,
    )
    clusters, _stats = detect_proximity(
        [fin, vote], max_days=90, committee_label="Campaign"
    )
    sigs = build_signals_from_proximity(clusters, uuid.uuid4())
    assert len(sigs) == 1
    assert sigs[0]["direction_verified"] is True
