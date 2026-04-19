"""Flatten EthicalAlt deep JSON for the Open Case mapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing" / "ethicalalt_mapper"))

from profile_adapter import (  # noqa: E402
    flatten_ethicalalt_deep_profile,
    profile_from_brand_directory,
)


def test_flatten_per_category() -> None:
    raw = {
        "slug": "acme",
        "companyName": "Acme Corp",
        "per_category": [
            {
                "category": "environmental",
                "incidents": [{"description": "Fine $1", "date": "2020-01-01"}],
            },
            {
                "category": "labor_and_wage",
                "incidents": [{"description": "Wage claim", "date": "2021-06-15"}],
            },
        ],
    }
    out = flatten_ethicalalt_deep_profile(raw)
    assert out["profile_id"] == "acme"
    assert out["name"] == "Acme Corp"
    assert len(out["incidents"]) == 2
    assert out["incidents"][0]["ethicalalt_category"] == "environmental"
    assert out["incidents"][0]["id"] == "environmental-0"


def test_flatten_prefers_root_incidents() -> None:
    raw = {
        "slug": "t",
        "companyName": "T",
        "per_category": [
            {"category": "environmental", "incidents": [{"description": "a", "date": "2020-01-01"}]}
        ],
        "incidents": [
            {"description": "labor", "date": "2021-01-01", "category": "labor_and_wage"},
            {"description": "env", "date": "2022-01-01", "category": "environmental"},
        ],
    }
    out = flatten_ethicalalt_deep_profile(raw)
    assert len(out["incidents"]) == 2
    assert {i["ethicalalt_category"] for i in out["incidents"]} == {
        "labor_and_wage",
        "environmental",
    }


def test_profile_from_brand_directory_rounds(tmp_path: Path) -> None:
    d = tmp_path / "brand"
    d.mkdir()
    payload = {
        "slug": "demo",
        "category": "labor_and_wage",
        "rounds": [
            {
                "round": 1,
                "incidents_raw": [
                    {"description": "Fine", "date": "2020-01-01", "category": "labor_and_wage"}
                ],
            }
        ],
    }
    (d / "labor_and_wage.json").write_text(json.dumps(payload), encoding="utf-8")
    out = profile_from_brand_directory(d)
    assert out["profile_id"] == "demo"
    assert len(out["incidents"]) == 1
    assert out["incidents"][0]["ethicalalt_category"] == "labor_and_wage"


def test_flatten_categories_dict() -> None:
    raw = {
        "brand_slug": "x",
        "name": "X",
        "categories": {
            "political": {"incidents": [{"description": "Donation $500", "date": "2019-01-01"}]}
        },
    }
    out = flatten_ethicalalt_deep_profile(raw)
    assert len(out["incidents"]) == 1
    assert out["incidents"][0]["ethicalalt_category"] == "political"
