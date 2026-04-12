"""BASELINE_ANOMALY temporal gate and election-cycle discount."""

from datetime import date

from engines.pattern_engine import (
    BASELINE_ANOMALY_CAMPAIGN_SEASON_DISCOUNT,
    _baseline_election_cycle_discount,
)


def test_baseline_election_cycle_discount_halves_when_midpoint_year_matches_even_cycle() -> (
    None
):
    assert (
        _baseline_election_cycle_discount(date(2014, 4, 15), [2012, 2014, 2016])
        == BASELINE_ANOMALY_CAMPAIGN_SEASON_DISCOUNT
    )


def test_baseline_election_cycle_discount_no_match_odd_year() -> None:
    assert _baseline_election_cycle_discount(date(2013, 6, 1), [2012, 2014]) == 1.0


def test_baseline_election_cycle_discount_no_match_year_not_in_cycles() -> None:
    assert _baseline_election_cycle_discount(date(2014, 6, 1), [2022, 2024]) == 1.0
