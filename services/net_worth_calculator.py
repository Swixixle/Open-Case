"""Estimate net worth from structured financial_disclosures rows (range-based)."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import CaseFile, FinancialDisclosure

# OpenSecrets-style text ranges; keys must match stored ``value_range`` when present
VALUE_RANGES: dict[str, tuple[int, int]] = {
    "$1,001 - $15,000": (1_001, 15_000),
    "$15,001 - $50,000": (15_001, 50_000),
    "$50,001 - $100,000": (50_001, 100_000),
    "$100,001 - $250,000": (100_001, 250_000),
    "$250,001 - $500,000": (250_001, 500_000),
    "$500,001 - $1,000,000": (500_001, 1_000_000),
    "$1,000,001 - $5,000,000": (1_000_001, 5_000_000),
    "$5,000,001 - $25,000,000": (5_000_001, 25_000_000),
    "$25,000,001 - $50,000,000": (25_000_001, 50_000_000),
    "Over $50,000,000": (50_000_001, 100_000_000),
}

_ASSETISH = re.compile(
    r"asset|stock|property|estate|fund|bond|reit|ptr|income|business",
    re.IGNORECASE,
)


def _income_to_int(raw: str | None) -> int | None:
    if not raw:
        return None
    s = str(raw).replace(",", "")
    m = re.search(r"-?\$?\s*([0-9.]+)", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if v < 0:
        return None
    return int(round(v))


def calculate_net_worth(
    db: Session, case_id: uuid.UUID
) -> tuple[int, int, date] | None:
    """
    Sum range minima/maxima for the latest ``filing_year`` where a row has a
    known ``value_range`` or a parseable ``income_amount``, and a plausible category.
    """
    latest = db.scalar(
        select(func.max(FinancialDisclosure.filing_year)).where(
            FinancialDisclosure.case_file_id == case_id
        )
    )
    if latest is None:
        return None
    rows = (
        db.scalars(
            select(FinancialDisclosure).where(
                FinancialDisclosure.case_file_id == case_id,
                FinancialDisclosure.filing_year == latest,
            )
        )
        .all()
    )
    if not rows:
        return None
    total_min = 0
    total_max = 0
    any_use = False
    for a in rows:
        cat = str(a.category or "")
        vr = (a.value_range or "").strip() if a.value_range else None
        inc = _income_to_int(a.income_amount)
        include = bool(vr and vr in VALUE_RANGES)
        if not include and inc is not None and _ASSETISH.search(
            f"{cat} {a.description}"
        ):
            include = True
        if not include:
            continue
        if vr and vr in VALUE_RANGES:
            lo, hi = VALUE_RANGES[vr]
            total_min += lo
            total_max += hi
            any_use = True
        elif inc is not None:
            total_min += inc
            total_max += inc
            any_use = True
    if not any_use:
        return None
    calc_date = datetime.now(timezone.utc).date()
    return (total_min, total_max, calc_date)


def update_case_file_net_worth(db: Session, case_id: uuid.UUID) -> bool:
    """Write ``CaseFile`` net worth fields from disclosure sums."""
    out = calculate_net_worth(db, case_id)
    if not out:
        return False
    min_v, max_v, calc_date = out
    case = db.get(CaseFile, case_id)
    if not case:
        return False
    case.estimated_net_worth_min = min_v
    case.estimated_net_worth_max = max_v
    case.net_worth_calculation_date = calc_date
    db.add(case)
    return True
