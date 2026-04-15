"""FJC judges CSV adapter — parse, match, cache, evidence shape (no live download in unit tests)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adapters.fjc_biographical import (
    FJCBiographicalAdapter,
    court_match_score,
    ensure_judges_csv,
    find_best_judge_matches,
    iter_csv_rows,
    name_match_score,
    row_to_adapter_result,
)

MINIMAL_CSV = '''"nid","jid","Last Name","First Name","Middle Name","Suffix","Birth Year","Court Name (1)","Court Type (1)","Appointment Title (1)","Appointing President (1)","Party of Appointing President (1)","Commission Date (1)","Termination (1)","Termination Date (1)","Court Name (2)","School (1)","Degree (1)","Degree Year (1)","School (2)","Degree (2)","Degree Year (2)","Professional Career","Other Federal Judicial Service (1)"
"1393931","3419","Abrams","Ronnie"," "," ","1968","U.S. District Court for the Southern District of New York","U.S. District Court","Judge","Barack Obama","Democratic","2012-03-23","","","","","Cornell University","B.A.","1990","Yale Law School","J.D.","1993","Law clerk; Private practice; Assistant U.S. attorney",""
"999","999","Smith","John","Q","Jr.","1970","U.S. District Court for the Southern District of Indiana","U.S. District Court","Judge","Test President","Democratic","2020-01-15","Retirement","2024-01-01","","Indiana University","B.A.","1992","Notre Dame Law School","J.D.","1995","Private practice","U.S. Magistrate Judge, 2010-2019"
"1000","1000","Smith","John","A","","1971","U.S. District Court for the Northern District of Illinois","U.S. District Court","Judge","Other President","Republican","2021-06-01","","","","","U of I","B.S.","1993","","","","Attorney"
'''


def test_iter_csv_rows_parses_header_and_rows() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    assert len(rows) == 3
    assert rows[0]["Last Name"] == "Abrams"
    assert rows[0]["First Name"] == "Ronnie"
    assert "Southern District of New York" in (rows[0].get("Court Name (1)") or "")


def test_name_match_fuzzy_middle_and_suffix() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    smith = next(r for r in rows if r["nid"] == "999")
    assert name_match_score("John Q. Smith Jr.", smith) > 0.85
    assert name_match_score("John Smith", smith) > 0.82
    ab = next(r for r in rows if r["nid"] == "1393931")
    assert name_match_score("Ronnie Abrams", ab) > 0.88


def test_court_match_score_uses_hints() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    ab = next(r for r in rows if r["nid"] == "1393931")
    assert court_match_score(ab, ["southern district of new york"]) >= 0.5
    assert court_match_score(ab, ["southern district of indiana"]) < 0.3


def test_find_best_judge_matches_with_jurisdiction_hints() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    matches, top = find_best_judge_matches(
        rows,
        "Ronnie Abrams",
        ["southern district of new york"],
    )
    assert top > 0
    assert len(matches) >= 1
    assert matches[0]["nid"] == "1393931"


def test_find_best_collision_two_john_smith_without_court_hints() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    john_rows = [r for r in rows if r["Last Name"] == "Smith" and r["First Name"] == "John"]
    assert len(john_rows) == 2
    matches, _ = find_best_judge_matches(rows, "John Smith", [])
    assert len(matches) >= 2


def test_row_to_adapter_result_shape() -> None:
    rows = iter_csv_rows(MINIMAL_CSV)
    ab = next(r for r in rows if r["nid"] == "1393931")
    res = row_to_adapter_result(ab, collision_count=1)
    assert res.source_name == "FJC"
    assert "fjc.gov" in res.source_url
    assert res.entry_type == "court_record"
    assert "Ronnie" in res.title and "Abrams" in res.title
    assert "Barack Obama" in res.body
    assert "Cornell" in res.body and "Yale" in res.body
    assert res.raw_data.get("epistemic_level") == "VERIFIED"
    assert res.date_of_event == "2012-03-23"


def test_ensure_judges_csv_cache_skips_redownload_when_fresh(tmp_path: Path) -> None:
    p = tmp_path / "judges.csv"
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return MINIMAL_CSV.encode("utf-8")

    fixed = datetime(2026, 1, 10, tzinfo=timezone.utc)
    ensure_judges_csv(p, now=fixed, fetch=fetch)
    os.utime(p, (fixed.timestamp(), fixed.timestamp()))
    ensure_judges_csv(p, now=fixed, fetch=fetch)
    assert len(calls) == 1
    assert p.read_text(encoding="utf-8").startswith('"nid"')


def test_ensure_judges_csv_refreshes_after_max_age(tmp_path: Path) -> None:
    p = tmp_path / "judges.csv"
    calls: list[int] = []

    def fetch(_url: str) -> bytes:
        calls.append(1)
        return MINIMAL_CSV.encode("utf-8")

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ensure_judges_csv(p, now=t0, fetch=fetch)
    os.utime(p, (t0.timestamp(), t0.timestamp()))
    ensure_judges_csv(p, now=datetime(2026, 1, 20, tzinfo=timezone.utc), fetch=fetch)
    assert len(calls) == 2


def test_fjc_adapter_search_uses_injected_csv(tmp_path: Path) -> None:
    p = tmp_path / "judges.csv"
    p.write_text(MINIMAL_CSV, encoding="utf-8")
    fixed_ts = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    os.utime(p, (fixed_ts, fixed_ts))

    ad = FJCBiographicalAdapter()
    ad.csv_path = p
    ad.jurisdiction_text = "Southern District of New York"
    ad.court_ids = []
    ad._now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    ad._fetch = lambda _u: b""  # should not run — file fresh

    resp = asyncio.run(ad.search("Ronnie Abrams|nocourt|NY", "person"))
    assert resp.found
    assert len(resp.results) == 1
    assert "Abrams" in resp.results[0].body
    assert resp.results[0].raw_data.get("fjc_nid") == "1393931"


def test_fjc_adapter_empty_when_no_match(tmp_path: Path) -> None:
    p = tmp_path / "judges.csv"
    p.write_text(MINIMAL_CSV, encoding="utf-8")
    fixed_ts = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    os.utime(p, (fixed_ts, fixed_ts))
    ad = FJCBiographicalAdapter()
    ad.csv_path = p
    ad.jurisdiction_text = ""
    ad._now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    ad._fetch = lambda _u: b""

    resp = asyncio.run(ad.search("ZZZ Nonexistent Judge", "person"))
    assert resp.found
    assert resp.empty_success
    assert not resp.results
