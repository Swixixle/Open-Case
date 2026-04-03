#!/usr/bin/env python3
"""
Backfill weight_breakdown.receipt_date (and exemplar_financial_date when empty)
on temporal_proximity donor_cluster signals using FEC contribution_receipt_date
from linked evidence rows.

  PYTHONPATH=. python scripts/backfill_signal_receipt_dates.py
  PYTHONPATH=. python scripts/backfill_signal_receipt_dates.py --force

Requires DATABASE_URL (or default sqlite ./open_case.db).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import SessionLocal  # noqa: E402
from engines.signal_receipt_backfill import backfill_all_temporal_signals  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill signal receipt_date from FEC evidence")
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite non-empty receipt_date in weight_breakdown",
    )
    args = p.parse_args()
    db = SessionLocal()
    try:
        n = backfill_all_temporal_signals(db, force=args.force)
        db.commit()
        print(f"backfill_signal_receipt_dates: updated {n} signal(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
