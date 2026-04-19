#!/usr/bin/env python3
"""
Run the Open Case EthicalAlt mapper against real deep-research JSON files.

Usage (from repo root):
  python3 testing/ethicalalt_mapper/run_real_profile_smoke.py \\
    /path/to/target_deep.json

Or copy JSON into ``testing/ethicalalt_mapper/data/`` and:
  python3 testing/ethicalalt_mapper/run_real_profile_smoke.py

Does not change ``scripts/ethicalalt_to_open_case.py`` — reports only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
SCRIPTS = REPO_ROOT / "scripts"
for p in (SCRIPTS, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ethicalalt_to_open_case import (  # noqa: E402
    RECIPIENT_TYPE_UNKNOWN,
    build_ethicalalt_entity,
    extract_donations_for_open_case,
    generate_soft_bundle_test_data,
    generate_temporal_clustering_test_data,
)
from profile_adapter import flatten_ethicalalt_deep_profile  # noqa: E402

DEFAULT_EXTERNAL_GLOB = (
    "/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output/*_deep.json"
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_source_incidents(raw: dict) -> tuple[int, dict[str, int]]:
    per_cat: dict[str, int] = {}
    total = 0
    root = raw.get("incidents")
    if isinstance(root, list) and len(root) > 0:
        total = len(root)
        for inc in root:
            if isinstance(inc, dict):
                c = str(inc.get("category") or "?")
                per_cat[c] = per_cat.get(c, 0) + 1
        return total, per_cat
    if "per_category" in raw and isinstance(raw["per_category"], list):
        for bucket in raw["per_category"]:
            cat = str(bucket.get("category") or "?")
            n = len(bucket.get("incidents") or [])
            per_cat[cat] = per_cat.get(cat, 0) + n
            total += n
    elif "categories" in raw and isinstance(raw["categories"], dict):
        for cat, bucket in raw["categories"].items():
            if isinstance(bucket, dict):
                n = len(bucket.get("incidents") or [])
                per_cat[str(cat)] = n
                total += n
    else:
        total = len(raw.get("incidents") or [])
        per_cat["flat"] = total
    return total, per_cat


def test_real_profile(path: Path) -> dict:
    raw = _load_json(path)
    total_src, per_cat = _count_source_incidents(raw)
    flat = flatten_ethicalalt_deep_profile(raw)
    brand = flat["profile_id"]

    print(f"\n=== {path.name} ({brand}) ===")
    print("Categories (source incident counts):")
    for cat, n in sorted(per_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n}")

    entity = build_ethicalalt_entity(flat)
    donations = extract_donations_for_open_case(flat)

    print(f"\nMapper incidents: {len(entity.incidents)}")
    print(f"Donation fixtures (strict): {len(donations)}")

    with_norm_date = [i for i in entity.incidents if i.normalized_date]
    without_norm = len(entity.incidents) - len(with_norm_date)
    print(f"  With normalized date: {len(with_norm_date)}")
    print(f"  Without normalized date (or invalid): {without_norm}")

    lobbying = [i for i in entity.incidents if i.event_type == "lobbying_expenditure"]
    print(f"  Classified lobbying_expenditure: {len(lobbying)}")

    for d in donations[:5]:
        amt = d.amount
        amt_s = f"${amt:,.0f}" if amt is not None else "?"
        desc = (d.description[:72] + "…") if len(d.description) > 72 else d.description
        print(f"  {d.normalized_date} | {amt_s} | {d.event_type} | {desc}")

    resolved = [d for d in donations if d.recipient_name]
    print("\nRecipient extraction (donation fixtures only):")
    print(f"  With recipient_name: {len(resolved)} / {len(donations)}")

    sb = generate_soft_bundle_test_data(entity)
    tc = generate_temporal_clustering_test_data(entity)
    assert sb["count"] == len(donations)
    print(f"\nSmoke: soft_bundle rows={sb['count']}, timeline={len(tc['timeline'])}")

    return {
        "file": path.name,
        "brand": brand,
        "total_source_incidents": total_src,
        "mapper_incidents": len(entity.incidents),
        "donation_fixtures": len(donations),
        "incidents_missing_normalized_date": without_norm,
        "recipients_resolved": len(resolved),
        "lobbying_classified": len(lobbying),
        "categories": per_cat,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="EthicalAlt → Open Case mapper smoke test")
    ap.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="JSON files (default: testing/ethicalalt_mapper/data/*_deep.json or EthicalAlt repo)",
    )
    ap.add_argument(
        "--use-default-ethicalalt-dir",
        action="store_true",
        help=f"Load *_deep.json from {DEFAULT_EXTERNAL_GLOB!r}",
    )
    args = ap.parse_args()

    paths: list[Path] = []
    if args.paths:
        paths = [p.expanduser().resolve() for p in args.paths]
    elif args.use_default_ethicalalt_dir:
        paths = sorted(Path("/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output").glob("*_deep.json"))
    else:
        data_dir = Path(__file__).resolve().parent / "data"
        paths = sorted(data_dir.glob("*_deep.json"))

    if not paths:
        print(
            "No profile files found. Copy *_deep.json into "
            "testing/ethicalalt_mapper/data/ or pass paths / --use-default-ethicalalt-dir",
            file=sys.stderr,
        )
        return 1

    results: list[dict] = []
    for p in paths:
        if not p.is_file():
            print(f"Skip missing: {p}", file=sys.stderr)
            continue
        try:
            results.append(test_real_profile(p))
        except Exception as e:
            print(f"FAILED {p}: {e}", file=sys.stderr)
            raise

    print("\n=== SUMMARY ===")
    for r in results:
        print(
            f"{r['brand']}: source_incidents={r['total_source_incidents']} "
            f"donation_fixtures={r['donation_fixtures']} "
            f"no_norm_date={r['incidents_missing_normalized_date']} "
            f"recipients={r['recipients_resolved']} lobbying_tagged={r['lobbying_classified']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
