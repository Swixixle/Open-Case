"""Fixture adapter: EthicalAlt-style profile → Open Case test payloads."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ethicalalt_to_open_case import (  # noqa: E402
    EVENT_DONATION,
    EVENT_LOBBYING_EXPENDITURE,
    EVENT_PAC_CONTRIBUTION,
    EVENT_UNKNOWN_POLITICAL,
    RECIPIENT_TYPE_COMMITTEE,
    RECIPIENT_TYPE_OFFICIAL,
    RECIPIENT_TYPE_UNKNOWN,
    build_ethicalalt_entity,
    classify_political_event_type,
    classify_severity,
    extract_donations_for_open_case,
    extract_recipient,
    generate_soft_bundle_test_data,
    generate_temporal_clustering_test_data,
    is_political_donation_context,
    normalize_date,
    parse_amount,
)


class TestParseAmount:
    def test_comma_dollar(self) -> None:
        assert parse_amount("Paid $50,000 in Q1") == 50_000.0

    def test_million_suffix(self) -> None:
        assert parse_amount("Fee $1.2M disclosed") == 1.2e6

    def test_lowercase_k(self) -> None:
        assert parse_amount("donation 25k total") == 25_000.0

    def test_range_returns_none(self) -> None:
        assert parse_amount("between $2M and $4M") is None
        assert parse_amount("up to $5M possible") is None
        assert parse_amount("$1M-$3M range") is None

    def test_no_money(self) -> None:
        assert parse_amount("No figures here") is None
        assert parse_amount("") is None

    def test_two_distinct_amounts_none(self) -> None:
        assert parse_amount("Donated $5000 in 2020 and $6000 in 2021") is None


class TestClassifyPoliticalEventType:
    def test_donation(self) -> None:
        assert (
            classify_political_event_type("Campaign donation of $500 to Smith")
            == EVENT_DONATION
        )

    def test_pac(self) -> None:
        assert (
            classify_political_event_type("PAC contribution of $1000 to Blue PAC")
            == EVENT_PAC_CONTRIBUTION
        )

    def test_lobbying_not_donation(self) -> None:
        assert (
            classify_political_event_type(
                "Lobbying expenditure $50,000 to influence bill HR 1"
            )
            == EVENT_LOBBYING_EXPENDITURE
        )

    def test_ambiguous(self) -> None:
        assert (
            classify_political_event_type("Political activity reported in Q3")
            == EVENT_UNKNOWN_POLITICAL
        )

    def test_contributed_to_pollution_not_donation(self) -> None:
        # Real EthicalAlt phrasing: "operations contributed to … contamination"
        assert (
            classify_political_event_type(
                "Operations contributed to nitrate groundwater contamination in Oregon."
            )
            == EVENT_UNKNOWN_POLITICAL
        )

    def test_charitable_donated_not_strict_donation(self) -> None:
        # EthicalAlt Nestlé-style recall: product donated to charity — not campaign finance
        text = (
            "Nestlé USA recalled Hot Pockets donated to a charitable organization "
            "in Missouri due to misbranding."
        )
        assert not is_political_donation_context(text)
        assert classify_political_event_type(text) == EVENT_UNKNOWN_POLITICAL

    def test_friends_of_senate_is_political(self) -> None:
        assert classify_political_event_type(
            "Donated $25,000 to Friends of Lee for Senate"
        ) == EVENT_DONATION


class TestExtractRecipient:
    def test_committee(self) -> None:
        r = extract_recipient("Donation to Friends of Jane Smith for Senate")
        assert r.recipient_type == RECIPIENT_TYPE_COMMITTEE
        assert r.recipient_name and "Jane" in r.recipient_name

    def test_official(self) -> None:
        r = extract_recipient("Contribution to Sen. Pat Roberts committee")
        assert r.recipient_type == RECIPIENT_TYPE_OFFICIAL
        assert r.recipient_name == "Pat Roberts"

    def test_unresolved(self) -> None:
        r = extract_recipient("Miscellaneous political payment")
        assert r.recipient_type == RECIPIENT_TYPE_UNKNOWN
        assert r.recipient_name is None


class TestClassifySeverity:
    def test_critical_keyword(self) -> None:
        assert classify_severity("Federal indictment unsealed") == "critical"

    def test_medium_settlement(self) -> None:
        assert classify_severity("Reached settlement with regulator", 10_000.0) == "medium"

    def test_high_large_settlement(self) -> None:
        assert classify_severity("Settlement announced", 600_000.0) == "high"


class TestNormalizeDate:
    def test_iso(self) -> None:
        n, raw = normalize_date("2023-01-15")
        assert n == "2023-01-15"
        assert raw == "2023-01-15"

    def test_us_slash(self) -> None:
        n, raw = normalize_date("03/15/2023")
        assert n == "2023-03-15"

    def test_invalid(self) -> None:
        n, raw = normalize_date("not-a-date")
        assert n is None
        assert raw == "not-a-date"

    def test_month_precision_ethicalalt(self) -> None:
        n, raw = normalize_date("2025-09")
        assert n == "2025-09-01"
        assert raw == "2025-09"

    def test_year_precision_ethicalalt(self) -> None:
        n, raw = normalize_date("2018")
        assert n == "2018-01-01"
        assert raw == "2018"


class TestExtractionPipeline:
    def test_lobbying_not_in_donation_fixtures(self) -> None:
        profile = {
            "profile_id": "p1",
            "name": "Acme Corp",
            "incidents": [
                {
                    "id": "a",
                    "description": "Lobbying expenditure $40,000 reported",
                    "date": "2022-06-01",
                },
            ],
        }
        ent = build_ethicalalt_entity(profile)
        assert ent.donations == []
        assert ent.incidents[0].event_type == EVENT_LOBBYING_EXPENDITURE
        assert not ent.incidents[0].included_in_donation_fixtures

    def test_donation_included(self) -> None:
        profile = {
            "profile_id": "p2",
            "name": "Beta LLC",
            "incidents": [
                {
                    "id": "b",
                    "description": "Campaign donation $2,000 to Friends of Lee",
                    "date": "2021-04-10",
                },
            ],
        }
        d = extract_donations_for_open_case(profile)
        assert len(d) == 1
        assert d[0].event_type == EVENT_DONATION
        assert d[0].normalized_date == "2021-04-10"

    def test_invalid_date_skips_donation_fixture(self) -> None:
        profile = {
            "profile_id": "p3",
            "name": "Gamma",
            "incidents": [
                {
                    "id": "c",
                    "description": "Donated $1,000 to campaign",
                    "date": "bogus",
                },
            ],
        }
        ent = build_ethicalalt_entity(profile)
        assert ent.donations == []
        assert ent.incidents[0].normalized_date is None


class TestGenerators:
    def test_soft_bundle_shape(self) -> None:
        profile = {
            "profile_id": "x",
            "name": "Co",
            "incidents": [
                {
                    "description": "Donated $100 to campaign fund",
                    "date": "2020-01-02",
                },
            ],
        }
        ent = build_ethicalalt_entity(profile)
        out = generate_soft_bundle_test_data(ent)
        assert out["profile_id"] == "x"
        assert out["count"] == 1
        assert out["donation_rows"][0]["date"] == "2020-01-02"

    def test_temporal_sorted_and_span(self) -> None:
        profile = {
            "profile_id": "y",
            "name": "Co",
            "incidents": [
                {"description": "Donation $1", "date": "2020-12-01"},
                {"description": "Lobbying fee", "date": "2020-01-01"},
            ],
        }
        ent = build_ethicalalt_entity(profile)
        tc = generate_temporal_clustering_test_data(ent)
        dates = [r["normalized_date"] for r in tc["timeline"]]
        assert dates == sorted(d for d in dates if d)
        assert tc["timeline_span"]["start"] == "2020-01-01"
        assert tc["timeline_span"]["end"] == "2020-12-01"
