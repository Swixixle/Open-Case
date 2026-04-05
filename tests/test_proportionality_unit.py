"""Proportionality trigger rules (no HTTP when SKIP_EXTERNAL_PROPORTIONALITY is set)."""

from __future__ import annotations

import json
import uuid

from models import Signal
from services.proportionality import (
    SIGNAL_CATEGORY_MAP,
    proportionality_category_and_amount,
)


def _sig(**kwargs: object) -> Signal:
    defaults: dict[str, object] = {
        "case_file_id": uuid.uuid4(),
        "signal_identity_hash": "a" * 64,
        "signal_type": "temporal_proximity",
        "weight": 0.5,
        "description": "x",
        "evidence_ids": "[]",
        "exposure_state": "internal",
        "dismissed": False,
        "temporal_class": "retrospective",
        "days_between": -5,
    }
    defaults.update(kwargs)
    bd = defaults.pop("breakdown", None)
    s = Signal(**defaults)
    if bd is not None:
        s.weight_breakdown = json.dumps(bd)
    return s


def test_map_has_donor_cluster_political() -> None:
    assert SIGNAL_CATEGORY_MAP["donor_cluster"] == "political"


def test_eligible_donor_cluster_retrospective_with_amount() -> None:
    s = _sig(
        breakdown={
            "kind": "donor_cluster",
            "total_amount": 50000.0,
            "donor": "X",
            "official": "Y",
        },
    )
    cat, amt = proportionality_category_and_amount(s)
    assert cat == "political"
    assert amt == 50000.0


def test_skip_unresolved() -> None:
    s = _sig(
        exposure_state="unresolved",
        breakdown={"kind": "donor_cluster", "total_amount": 100.0},
    )
    assert proportionality_category_and_amount(s) == (None, None)


def test_skip_dismissed() -> None:
    s = _sig(
        dismissed=True,
        breakdown={"kind": "donor_cluster", "total_amount": 100.0},
    )
    assert proportionality_category_and_amount(s) == (None, None)


def test_skip_anticipatory() -> None:
    s = _sig(
        temporal_class="anticipatory",
        breakdown={"kind": "donor_cluster", "total_amount": 100.0},
    )
    assert proportionality_category_and_amount(s) == (None, None)


def test_skip_non_donor_cluster_kind() -> None:
    s = _sig(breakdown={"kind": "other", "total_amount": 100.0})
    assert proportionality_category_and_amount(s) == (None, None)


def test_skip_zero_amount() -> None:
    s = _sig(
        breakdown={"kind": "donor_cluster", "total_amount": 0},
    )
    assert proportionality_category_and_amount(s) == (None, None)
