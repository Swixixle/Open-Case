from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


@dataclass
class ProximitySignal:
    actor_a: str
    actor_b: str
    financial_event: str
    decision_event: str
    financial_date: str
    decision_date: str
    days_between: int
    amount: float
    financial_entry_id: str
    decision_entry_id: str
    weight: float

    def to_description(self) -> str:
        direction = "before" if self.days_between > 0 else "after"
        days = abs(self.days_between)
        return (
            f"${self.amount:,.0f} financial connection involving {self.actor_a} "
            f"occurred {days} days {direction} a decision event involving {self.actor_b}. "
            f"Financial: {self.financial_event}. "
            f"Decision: {self.decision_event}."
        )

    def to_breakdown(self) -> dict[str, Any]:
        abs_days = abs(self.days_between)
        proximity_label = (
            "very high"
            if abs_days <= 14
            else "high"
            if abs_days <= 30
            else "medium"
            if abs_days <= 90
            else "lower"
        )
        amt = self.amount or 0.0
        amount_label = (
            "large"
            if amt >= 250000
            else "medium"
            if amt >= 50000
            else "small"
            if amt >= 10000
            else "minor"
        )
        proximity_score = (
            1.0 if abs_days <= 30 else 0.6 if abs_days <= 90 else 0.3
        )
        return {
            "days_between": self.days_between,
            "proximity_score": round(proximity_score, 2),
            "proximity_label": proximity_label,
            "amount": self.amount,
            "amount_label": amount_label,
            "base_weight": round(self.weight, 3),
            "final_weight": round(self.weight, 3),
            "components": [
                f"{abs_days}-day proximity ({proximity_label})",
                f"{amount_label} amount tier",
            ],
        }

    def to_explanation(self) -> str:
        direction = "before" if self.days_between >= 0 else "after"
        days = abs(self.days_between)
        amount_str = f"${self.amount:,.0f}" if self.amount else "an unspecified amount"
        prox = "high" if days <= 30 else "medium" if days <= 90 else "lower"
        return (
            f"Financial connection of {amount_str} occurred {days} days {direction} "
            f"a decision event. Proximity weight is {prox} based on {days}-day gap."
        )


FINANCIAL_ENTRY_TYPES = frozenset({"financial_connection", "disclosure"})
DECISION_ENTRY_TYPES = frozenset({"vote_record", "timeline_event"})


def _entry_event_dt(entry: Any) -> datetime | None:
    if not getattr(entry, "date_of_event", None):
        return None
    d = entry.date_of_event
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    try:
        raw = str(d).replace("Z", "+00:00")
        return datetime.fromisoformat(raw)
    except (ValueError, AttributeError):
        return None


def _actor_for(entry: Any, fallback: str) -> str:
    m = getattr(entry, "matched_name", None)
    if m and str(m).strip():
        return str(m).strip()
    return fallback


def _cooling_factor_for_event_age(latest_event: datetime, reference: datetime | None = None) -> float:
    ref = reference or datetime.now(timezone.utc)
    age_days = max(0, (ref - latest_event).days)
    # Recent events keep full weight; multi-year-old pairs taper without vanishing.
    if age_days <= 365:
        return 1.0
    if age_days <= 365 * 3:
        return max(0.65, 1.0 - (age_days - 365) / (365 * 8))
    return max(0.35, 0.65 - (age_days - 365 * 3) / (365 * 15))


def detect_proximity(
    evidence_entries: list[Any],
    max_days: int = 90,
) -> list[ProximitySignal]:
    financial_events: list[tuple[datetime, Any]] = []
    decision_events: list[tuple[datetime, Any]] = []

    for entry in evidence_entries:
        dt = _entry_event_dt(entry)
        if not dt:
            continue
        et = getattr(entry, "entry_type", "")
        if et in FINANCIAL_ENTRY_TYPES:
            financial_events.append((dt, entry))
        elif et in DECISION_ENTRY_TYPES:
            decision_events.append((dt, entry))

    if not financial_events or not decision_events:
        return []

    signals: list[ProximitySignal] = []
    for f_date, f_entry in financial_events:
        amt = getattr(f_entry, "amount", None) or 0.0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0.0
        for d_date, d_entry in decision_events:
            days_diff = (d_date - f_date).days
            if -30 <= days_diff <= max_days:
                weight = _calculate_weight(days_diff, amt)
                latest = max(f_date, d_date)
                weight *= _cooling_factor_for_event_age(latest)
                weight = round(min(1.0, weight), 3)
                if weight > 0.1:
                    fin_id = str(getattr(f_entry, "id", ""))
                    dec_id = str(getattr(d_entry, "id", ""))
                    fd = getattr(f_entry, "date_of_event", None)
                    dd = getattr(d_entry, "date_of_event", None)
                    fd_s = fd.isoformat() if hasattr(fd, "isoformat") else str(fd or "")
                    dd_s = dd.isoformat() if hasattr(dd, "isoformat") else str(dd or "")
                    signals.append(
                        ProximitySignal(
                            actor_a=_actor_for(f_entry, "Unknown party"),
                            actor_b=_actor_for(d_entry, "Unknown official"),
                            financial_event=str(getattr(f_entry, "title", "")),
                            decision_event=str(getattr(d_entry, "title", "")),
                            financial_date=fd_s,
                            decision_date=dd_s,
                            days_between=days_diff,
                            amount=amt,
                            financial_entry_id=fin_id,
                            decision_entry_id=dec_id,
                            weight=weight,
                        )
                    )

    signals = _apply_repeat_multiplier(signals)
    signals.sort(key=lambda s: s.weight, reverse=True)
    return signals


def _calculate_weight(days_between: int, amount: float) -> float:
    if days_between <= 30:
        proximity_score = 1.0
    elif days_between <= 90:
        proximity_score = 0.6
    else:
        proximity_score = 0.3

    if amount <= 0:
        amount_score = 0.1
    elif amount < 10000:
        amount_score = 0.2
    elif amount < 50000:
        amount_score = 0.4
    elif amount < 250000:
        amount_score = 0.6
    elif amount < 1000000:
        amount_score = 0.8
    else:
        amount_score = 1.0

    return round(proximity_score * 0.6 + amount_score * 0.4, 3)


def _apply_repeat_multiplier(signals: list[ProximitySignal]) -> list[ProximitySignal]:
    actor_counts = Counter(s.actor_a for s in signals)
    for signal in signals:
        count = actor_counts[signal.actor_a]
        if count >= 3:
            signal.weight = min(1.0, signal.weight * 1.5)
        elif count == 2:
            signal.weight = min(1.0, signal.weight * 1.25)
    return signals
