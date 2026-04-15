#!/usr/bin/env python3
"""
Replay SOFT_BUNDLE_V1 scores for verified senators with vs without legislative calendar spans.

Uses OPEN_CASE_DISABLE_LEGISLATIVE_CALENDAR_SPANS=1 for baseline (FEC/election calendar
still applies). Flags |current - baseline| > 0.05 as potential calibration drift.

Requires DATABASE_URL and populated cases for each bioguide (production or staging DB).
Reference anchor: Tom Cotton SOFT_BUNDLE_V1 ~0.921 (spot-check after run).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# server/scripts/ → repository root
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import SessionLocal
from engines.pattern_engine import RULE_SOFT_BUNDLE, filter_pattern_alerts, run_pattern_engine
from models import SubjectProfile

# Verified corpus from batch_senator_dossiers / production QA.
VERIFY = [
    {"name": "Dan Sullivan", "bioguide_id": "S001198"},
    {"name": "Tom Cotton", "bioguide_id": "C001095"},
    {"name": "Joni Ernst", "bioguide_id": "E000295"},
    {"name": "Ron Wyden", "bioguide_id": "W000779"},
    {"name": "Mike Crapo", "bioguide_id": "C000880"},
    {"name": "Chuck Grassley", "bioguide_id": "G000386"},
    {"name": "Maria Cantwell", "bioguide_id": "C000127"},
]

DRIFT_THRESHOLD = 0.05


def _max_soft_bundle_v1(db: Session, case_id) -> float | None:
    alerts = run_pattern_engine(db)
    soft = filter_pattern_alerts(alerts, case_id=case_id, rule=RULE_SOFT_BUNDLE)
    if not soft:
        return None
    return max(float(a.suspicion_score or 0.0) for a in soft)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drift-threshold",
        type=float,
        default=DRIFT_THRESHOLD,
        help=f"Flag abs(delta) above this (default {DRIFT_THRESHOLD})",
    )
    args = parser.parse_args()
    thresh: float = args.drift_threshold

    db = SessionLocal()
    try:
        rows_out: list[tuple[str, str, float | None, float | None, float | None]] = []
        for row in VERIFY:
            bid = row["bioguide_id"]
            sp = db.scalar(
                select(SubjectProfile).where(SubjectProfile.bioguide_id == bid).limit(1)
            )
            if sp is None:
                print(f"missing SubjectProfile bioguide_id={bid} ({row['name']})")
                continue
            case_id = sp.case_file_id

            os.environ["OPEN_CASE_DISABLE_LEGISLATIVE_CALENDAR_SPANS"] = "1"
            baseline = _max_soft_bundle_v1(db, case_id)
            os.environ.pop("OPEN_CASE_DISABLE_LEGISLATIVE_CALENDAR_SPANS", None)
            current = _max_soft_bundle_v1(db, case_id)

            delta = (
                None
                if baseline is None or current is None
                else abs(current - baseline)
            )
            rows_out.append((row["name"], bid, baseline, current, delta))
            flag = (
                f" DRIFT>{thresh}"
                if delta is not None and delta > thresh
                else ""
            )
            print(
                f"{row['name']:16} {bid}  baseline={baseline}  current={current}  "
                f"|d|={delta}{flag}"
            )

        cotton = next((r for r in rows_out if r[1] == "C001095"), None)
        if cotton:
            _, _, b, c, _ = cotton
            print(
                f"\nAnchor check (Cotton): baseline={b} current={c} "
                f"(expect current ~0.921 if production data matches prior calibration)."
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
