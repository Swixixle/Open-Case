from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import logging

from core.datetime_utils import coerce_utc

logger = logging.getLogger(__name__)


def new_contract_pairing_stats() -> dict[str, Any]:
    return {
        "candidate_pairs_examined": 0,
        "pairs_emitted": 0,
        "pairs_skipped_missing_datetime": 0,
        "pairs_skipped_window": 0,
        "pairs_skipped_direction": 0,
        "pairs_skipped_other": 0,
        "sample_skips": [],
    }


def _append_contract_sample_skip(
    stats: dict[str, Any],
    skip_reason: str,
    raw_donation_val: Any,
    raw_contract_val: Any,
) -> None:
    """Uses same keys as temporal pairing_diagnostics (donation / vote = contract leg)."""
    samples: list[dict[str, str]] = stats["sample_skips"]
    if len(samples) >= 5:
        return
    samples.append(
        {
            "skip_reason": skip_reason,
            "donation_raw": repr(raw_donation_val),
            "donation_type": type(raw_donation_val).__name__,
            "vote_raw": repr(raw_contract_val),
            "vote_type": type(raw_contract_val).__name__,
        }
    )


@dataclass
class ContractProximitySignal:
    donor_label: str
    contractor_label: str
    donation_title: str
    contract_title: str
    donation_date: str
    contract_date: str
    days_between: int
    donation_amount: float
    donation_entry_id: str
    contract_entry_id: str
    weight: float

    def to_description(self) -> str:
        direction = "before" if self.days_between > 0 else "after"
        days = abs(self.days_between)
        amt = self.donation_amount or 0.0
        return (
            f"${amt:,.0f} FEC-reported contribution involving {self.donor_label} "
            f"occurred {days} days {direction} a USASpending award involving "
            f"{self.contractor_label}. "
            f"Donation: {self.donation_title}. Award: {self.contract_title}."
        )

    def to_breakdown(self) -> dict[str, Any]:
        abs_days = abs(self.days_between)
        return {
            "signal_subtype": "contract_proximity",
            "days_between": self.days_between,
            "donation_amount": self.donation_amount,
            "base_weight": round(self.weight, 3),
            "final_weight": round(min(1.0, self.weight), 3),
            "components": [
                f"{abs_days}-day gap (contribution vs contract)",
                "secondary signal (no vote record required)",
            ],
        }

    def to_explanation(self) -> str:
        days = abs(self.days_between)
        return (
            f"Contribution and federal award timing are {days} days apart — "
            "secondary contract proximity (useful when vote records are absent)."
        )


def _entry_event_dt(entry: Any) -> datetime | None:
    raw = getattr(entry, "date_of_event", None)
    if raw is None:
        return None
    if isinstance(raw, (datetime, date, str)):
        return coerce_utc(raw)
    return coerce_utc(str(raw))


def _label(entry: Any, fallback: str) -> str:
    m = getattr(entry, "matched_name", None)
    if m and str(m).strip():
        return str(m).strip()
    return fallback


def _contract_weight(days_between: int, amount: float) -> float:
    abs_d = abs(days_between)
    if abs_d <= 30:
        prox = 0.55
    elif abs_d <= 90:
        prox = 0.4
    elif abs_d <= 180:
        prox = 0.28
    else:
        prox = 0.15
    if amount <= 0:
        amt = 0.05
    elif amount < 5000:
        amt = 0.12
    elif amount < 25000:
        amt = 0.22
    elif amount < 100000:
        amt = 0.32
    else:
        amt = 0.42
    return round(min(0.55, prox * 0.65 + amt * 0.35), 3)


def detect_contract_proximity(
    evidence_entries: list[Any],
) -> tuple[list[ContractProximitySignal], dict[str, Any]]:
    pairing_stats = new_contract_pairing_stats()
    donations = [
        e
        for e in evidence_entries
        if getattr(e, "entry_type", "") == "financial_connection"
        and getattr(e, "source_name", "") == "FEC"
    ]
    contracts = [
        e
        for e in evidence_entries
        if getattr(e, "entry_type", "") == "financial_connection"
        and getattr(e, "source_name", "") == "USASpending"
    ]
    if not donations:
        pairing_stats["pairs_skipped_other"] += 1
        _append_contract_sample_skip(
            pairing_stats,
            "no_fec_donation_entries",
            0,
            len(contracts),
        )
    if not contracts:
        pairing_stats["pairs_skipped_other"] += 1
        _append_contract_sample_skip(
            pairing_stats,
            "no_usaspending_contract_entries",
            len(donations),
            0,
        )
    if not donations or not contracts:
        return [], pairing_stats

    _skip_log_count = 0
    _MAX_SKIP_LOGS = 10

    out: list[ContractProximitySignal] = []
    for d_ent in donations:
        d_dt = _entry_event_dt(d_ent)
        if not d_dt:
            pairing_stats["pairs_skipped_other"] += 1
            _append_contract_sample_skip(
                pairing_stats,
                "donation_entry_no_datetime",
                getattr(d_ent, "date_of_event", None),
                None,
            )
            continue
        try:
            d_amt = float(getattr(d_ent, "amount", None) or 0.0)
        except (TypeError, ValueError):
            d_amt = 0.0
        for c_ent in contracts:
            c_dt = _entry_event_dt(c_ent)
            if not c_dt:
                pairing_stats["pairs_skipped_other"] += 1
                _append_contract_sample_skip(
                    pairing_stats,
                    "contract_entry_no_datetime",
                    getattr(d_ent, "date_of_event", None),
                    getattr(c_ent, "date_of_event", None),
                )
                continue
            pairing_stats["candidate_pairs_examined"] += 1
            d_utc = coerce_utc(d_dt)
            c_utc = coerce_utc(c_dt)
            raw_donation = getattr(d_ent, "date_of_event", None)
            raw_contract = getattr(c_ent, "date_of_event", None)
            if d_utc is None or c_utc is None:
                pairing_stats["pairs_skipped_missing_datetime"] += 1
                _append_contract_sample_skip(
                    pairing_stats,
                    "missing_datetime",
                    raw_donation,
                    raw_contract,
                )
                if _skip_log_count < _MAX_SKIP_LOGS:
                    logger.warning(
                        "CONTRACT PROXIMITY SKIP | "
                        f"donation_raw={repr(raw_donation)} type={type(raw_donation).__name__} | "
                        f"contract_raw={repr(raw_contract)} type={type(raw_contract).__name__}"
                    )
                    _skip_log_count += 1
                continue
            days_diff = (c_utc - d_utc).days
            if days_diff < -45:
                pairing_stats["pairs_skipped_direction"] += 1
                _append_contract_sample_skip(
                    pairing_stats,
                    "outside_direction_grace",
                    raw_donation,
                    raw_contract,
                )
                continue
            if days_diff > 270:
                pairing_stats["pairs_skipped_window"] += 1
                _append_contract_sample_skip(
                    pairing_stats,
                    "outside_contract_window",
                    raw_donation,
                    raw_contract,
                )
                continue
            w = _contract_weight(days_diff, d_amt)
            if w < 0.12:
                pairing_stats["pairs_skipped_other"] += 1
                _append_contract_sample_skip(
                    pairing_stats,
                    "contract_weight_below_floor",
                    raw_donation,
                    raw_contract,
                )
                continue
            dd = getattr(d_ent, "date_of_event", None)
            cd = getattr(c_ent, "date_of_event", None)
            dd_s = dd.isoformat() if hasattr(dd, "isoformat") else str(dd or "")
            cd_s = cd.isoformat() if hasattr(cd, "isoformat") else str(cd or "")
            pairing_stats["pairs_emitted"] += 1
            out.append(
                ContractProximitySignal(
                    donor_label=_label(d_ent, "Contributor"),
                    contractor_label=_label(c_ent, "Recipient"),
                    donation_title=str(getattr(d_ent, "title", "")),
                    contract_title=str(getattr(c_ent, "title", "")),
                    donation_date=dd_s,
                    contract_date=cd_s,
                    days_between=days_diff,
                    donation_amount=d_amt,
                    donation_entry_id=str(getattr(d_ent, "id", "")),
                    contract_entry_id=str(getattr(c_ent, "id", "")),
                    weight=w,
                )
            )
    out.sort(key=lambda s: s.weight, reverse=True)
    return out, pairing_stats
