#!/usr/bin/env python3
"""
Test EthicalAlt → Open Case mapper on real deep research profiles.

Uses:
  - scripts/ethicalalt_to_open_case.py
  - testing/ethicalalt_mapper/profile_adapter.py

Accepts:
  - Monolithic ``*_deep.json`` (root ``incidents`` or ``per_category``).
  - Brand directories with per-category JSON (``rounds[].incidents_raw``).

Run from repo root:
  python3 testing/ethicalalt_validation/validate_real_profiles.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAPPER_DIR = REPO_ROOT / "testing" / "ethicalalt_mapper"
SCRIPTS = REPO_ROOT / "scripts"
for p in (SCRIPTS, MAPPER_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ethicalalt_to_open_case import (  # noqa: E402
    EVENT_LOBBYING_EXPENDITURE,
    build_ethicalalt_entity,
    extract_donations_for_open_case,
)
from profile_adapter import (  # noqa: E402
    flatten_ethicalalt_deep_profile,
    profile_from_brand_directory,
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


def _political_raw_incidents(flat_incidents: list[dict]) -> list[dict]:
    return [
        x
        for x in flat_incidents
        if isinstance(x, dict)
        and str(x.get("ethicalalt_category") or x.get("category") or "").lower()
        == "political"
    ]


def run_analysis(raw: dict, *, brand: str, source_label: str) -> dict:
    total_source, source_stats = _count_source_incidents(raw)
    flat = flatten_ethicalalt_deep_profile(raw)
    entity = build_ethicalalt_entity(flat)
    donations = extract_donations_for_open_case(flat)

    print(f"\n{'=' * 50}")
    print(f"TESTING: {brand.upper()}  ({source_label})")
    print(f"{'=' * 50}")

    print("\nSOURCE (by category):")
    for cat, n in sorted(source_stats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {n} incidents")
    print(f"  Total source incidents: {total_source}")

    print("\nDONATION EXTRACTION (strict fixtures):")
    print(f"  Donation fixtures produced: {len(donations)}")
    if donations:
        print("  Sample donations:")
        for i, d in enumerate(donations[:3], 1):
            amt = d.amount
            amt_s = f"${amt:,.0f}" if amt is not None else "(no amount in description)"
            rec = d.recipient_name or "(unresolved)"
            desc = d.description[:60] + ("..." if len(d.description) > 60 else "")
            print(f"    {i}. {d.normalized_date}: {amt_s}")
            print(f"       recipient: {rec}")
            print(f"       {desc}")
    else:
        print(
            "  (None — expected for labor/environmental-only text; mapper needs "
            "campaign/PAC/donation language.)"
        )

    valid_incidents = [i for i in entity.incidents if i.normalized_date]
    dropped = len(entity.incidents) - len(valid_incidents)
    drop_pct = (dropped / max(len(entity.incidents), 1)) * 100
    date_coverage_rate = len(valid_incidents) / max(len(entity.incidents), 1)
    donation_fixture_rate = len(donations) / max(len(entity.incidents), 1)

    print("\nINCIDENT PROCESSING (mapper output):")
    print(f"  Mapper incidents: {len(entity.incidents)}")
    print(f"  With normalized date: {len(valid_incidents)}")
    print(f"  Missing/invalid normalized date: {dropped}")
    if entity.incidents:
        print(f"  Drop rate (no ISO date): {drop_pct:.1f}%")
    print(f"  date_coverage_rate: {date_coverage_rate:.3f}")
    print(f"  donation_fixture_rate: {donation_fixture_rate:.3f}")

    resolved = [
        d for d in donations if d.recipient_name and d.recipient_type != "unknown"
    ]
    unresolved = len(donations) - len(resolved)
    recipient_resolution_rate = (
        100.0 * len(resolved) / max(len(donations), 1) if donations else 0.0
    )

    print("\nRECIPIENT RESOLUTION (donation fixtures only):")
    print(f"  Total donation fixtures: {len(donations)}")
    print(f"  Recipients resolved (non-unknown type): {len(resolved)}")
    print(f"  Unresolved / unknown: {unresolved}")
    if donations:
        print(f"  recipient_resolution_rate: {recipient_resolution_rate:.1f}%")

    political_raw = _political_raw_incidents(flat.get("incidents") or [])
    lobbying_raw: list[dict] = []
    donation_like_raw: list[dict] = []
    for inc in political_raw:
        desc = str(inc.get("description") or "").lower()
        if "lobby" in desc:
            lobbying_raw.append(inc)
        elif any(w in desc for w in ("donation", "contribution", "pac")):
            donation_like_raw.append(inc)

    lobbying_classified = sum(
        1 for i in entity.incidents if i.event_type == EVENT_LOBBYING_EXPENDITURE
    )

    print("\nLOBBYING / POLITICAL (heuristic on source + mapper):")
    print(f"  Source incidents in category 'political': {len(political_raw)}")
    print(f"  Source rows with lobby* in description: {len(lobbying_raw)}")
    print(f"  Source rows with donation/contribution/pac in text: {len(donation_like_raw)}")
    print(f"  Mapper classified lobbying_expenditure: {lobbying_classified}")

    return {
        "brand": brand,
        "file": source_label,
        "total_source_incidents": total_source,
        "donation_fixtures": len(donations),
        "mapper_incidents": len(entity.incidents),
        "incidents_dropped": dropped,
        "drop_rate_percent": drop_pct,
        "date_coverage_rate": date_coverage_rate,
        "donation_fixture_rate": donation_fixture_rate,
        "recipients_resolved": len(resolved),
        "recipient_resolution_rate": recipient_resolution_rate,
        "resolution_rate_percent": recipient_resolution_rate,
        "lobbying_classified": lobbying_classified,
        "source_political_count": len(political_raw),
        "category_breakdown": source_stats,
    }


def analyze_profile_file(profile_path: Path) -> dict:
    raw = _load_json(profile_path)
    brand = str(raw.get("brand_slug") or raw.get("slug") or profile_path.stem)
    return run_analysis(raw, brand=brand, source_label=profile_path.name)


def analyze_brand_dir(brand_dir: Path) -> dict:
    raw = profile_from_brand_directory(brand_dir)
    brand = str(raw.get("profile_id") or brand_dir.name)
    return run_analysis(raw, brand=brand, source_label=f"{brand_dir.name}/")


def _discover() -> list[tuple[str, Path]]:
    """Return (kind, path) where kind is ``file`` or ``brand_dir``."""
    items: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    cwd = Path.cwd()
    for pattern in ("*_deep.json", "*deep*.json"):
        for p in cwd.glob(pattern):
            p = p.resolve()
            if p.is_file() and p not in seen:
                seen.add(p)
                items.append(("file", p))

    bases = [
        Path("/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output"),
        REPO_ROOT / "testing" / "ethicalalt_mapper" / "data",
        cwd / "profiles",
    ]
    for base in bases:
        if not base.is_dir():
            continue
        for p in base.glob("*.json"):
            p = p.resolve()
            if p.is_file() and p not in seen:
                seen.add(p)
                items.append(("file", p))

    eth = Path("/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output")
    if eth.is_dir():
        for sub in sorted(eth.iterdir()):
            if not sub.is_dir():
                continue
            jfs = [x for x in sub.glob("*.json") if x.is_file()]
            if not jfs:
                continue
            key = sub.resolve()
            if key not in seen:
                seen.add(key)
                items.append(("brand_dir", key))

    return items


def main() -> int:
    print("EthicalAlt → Open Case mapper validation")
    print("Conservative mapper — metrics only")
    print("=" * 60)

    discovered = _discover()
    if not discovered:
        print("No profiles found.")
        print("\nTry:")
        print("  - Copy *_deep.json into testing/ethicalalt_mapper/data/")
        print(
            f"  - Or ensure {Path('/Users/alexmaksimovich/ETHICAL_ALTERNATIVES/server/deep_research_output')} exists"
        )
        return 1

    print(f"Found {len(discovered)} profile(s):")
    for kind, p in discovered:
        print(f"  - [{kind}] {p}")

    results: list[dict] = []
    for kind, path in discovered:
        try:
            if kind == "file":
                results.append(analyze_profile_file(path))
            else:
                results.append(analyze_brand_dir(path))
        except Exception as e:
            print(f"FAILED {path}: {e}")

    if not results:
        return 1

    print(f"\n{'=' * 80}")
    print("VALIDATION SUMMARY")
    print(f"{'=' * 80}")
    hdr = (
        f"{'Brand':<12} {'Src':<5} {'Don':<4} {'date%':<7} {'don%':<7} "
        f"{'Drop':<5} {'Lobby':<5}"
    )
    print(hdr)
    print("-" * 80)
    for r in results:
        print(
            f"{r['brand'][:11]:<12} "
            f"{r['total_source_incidents']:<5} "
            f"{r['donation_fixtures']:<4} "
            f"{r['date_coverage_rate']:<7.2f} "
            f"{r['donation_fixture_rate']:<7.2f} "
            f"{r['incidents_dropped']:<5} "
            f"{r['lobbying_classified']:<5}"
        )

    total_source = sum(x["total_source_incidents"] for x in results)
    total_donations = sum(x["donation_fixtures"] for x in results)
    total_dropped = sum(x["incidents_dropped"] for x in results)
    avg_drop = sum(x["drop_rate_percent"] for x in results) / len(results)
    avg_cov = sum(x["date_coverage_rate"] for x in results) / len(results)

    print(f"\nOVERALL:")
    print(f"  Profiles: {len(results)}")
    print(f"  Total source incidents: {total_source}")
    print(f"  Total donation fixtures: {total_donations}")
    print(f"  Total mapper rows without normalized date: {total_dropped}")
    print(f"  Average no-date rate: {avg_drop:.1f}%")
    print(f"  Average date_coverage_rate: {avg_cov:.3f}")

    print("\nQUALITY (heuristic):")
    if avg_drop < 20:
        print("  OK: Low rate of missing normalized dates")
    elif avg_drop < 40:
        print("  NOTE: Moderate missing-date rate — review raw date strings")
    else:
        print("  WARN: High missing-date rate — consider more date formats (mapper)")

    if total_donations > 0:
        print("  OK: At least one strict donation fixture")
    else:
        print(
            "  NOTE: No donation fixtures — often expected unless profiles include "
            "campaign/PAC/donation language."
        )

    print("\nRECOMMENDATIONS:")
    if avg_drop > 30:
        print("  - Inspect sample raw `date` fields that fail normalize_date()")
    if total_donations == 0 and total_source > 50:
        print("  - Try companies with known FEC/political coverage in EthicalAlt")
    print("  - See testing/ethicalalt_validation/real_profile_findings.md")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
