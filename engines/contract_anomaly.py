from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class ContractAnomaly:
    anomaly_type: str
    description: str
    evidence_entry_id: str
    amount: float
    weight: float

    def to_breakdown(self) -> dict[str, Any]:
        return {
            "anomaly_type": self.anomaly_type,
            "amount": self.amount,
            "base_weight": round(self.weight, 3),
            "final_weight": round(min(1.0, self.weight), 3),
            "components": [
                self.anomaly_type.replace("_", " "),
            ],
        }

    def to_explanation(self) -> str:
        lab = self.anomaly_type.replace("_", " ").title()
        return (
            f"{lab} signal (weight {self.weight:.2f}): {self.description}"
        )


PROCUREMENT_THRESHOLDS = [25_000, 150_000, 750_000, 10_000_000]


def _raw_dict(entry: Any) -> dict[str, Any]:
    raw_json = getattr(entry, "raw_data_json", None) or ""
    if not raw_json:
        return {}
    try:
        data = json.loads(raw_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def detect_contract_anomalies(evidence_entries: list[Any]) -> list[ContractAnomaly]:
    contract_entries = [
        e
        for e in evidence_entries
        if getattr(e, "entry_type", None) == "financial_connection"
        and getattr(e, "source_name", None) == "USASpending"
    ]

    if not contract_entries:
        return []

    anomalies: list[ContractAnomaly] = []

    for entry in contract_entries:
        try:
            amount = float(getattr(entry, "amount", None) or 0)
        except (TypeError, ValueError):
            amount = 0.0
        raw = _raw_dict(entry)
        num_offers = raw.get("Number of Offers Received")
        try:
            n_off = int(num_offers) if num_offers is not None else None
        except (TypeError, ValueError):
            n_off = None

        if n_off is not None and n_off <= 1:
            anomalies.append(
                ContractAnomaly(
                    anomaly_type="no_bid",
                    description=(
                        f"Contract of ${amount:,.0f} awarded with "
                        f"{n_off} offer(s) received — "
                        f"no competitive bidding documented."
                    ),
                    evidence_entry_id=str(getattr(entry, "id", "")),
                    amount=amount,
                    weight=0.6,
                )
            )

        for threshold in PROCUREMENT_THRESHOLDS:
            if threshold * 0.92 <= amount < threshold:
                anomalies.append(
                    ContractAnomaly(
                        anomaly_type="threshold_avoidance",
                        description=(
                            f"Contract value ${amount:,.0f} falls just below "
                            f"the ${threshold:,.0f} oversight threshold "
                            f"({((threshold - amount) / threshold * 100):.1f}% below)."
                        ),
                        evidence_entry_id=str(getattr(entry, "id", "")),
                        amount=amount,
                        weight=0.5,
                    )
                )
                break

    vendor_totals: dict[str, list[Any]] = defaultdict(list)
    for entry in contract_entries:
        name = (
            getattr(entry, "matched_name", None)
            or getattr(entry, "title", None)
            or "unknown"
        )
        vendor_totals[str(name)].append(entry)

    for vendor, entries in vendor_totals.items():
        if len(entries) >= 3:
            try:
                total = sum(float(getattr(e, "amount", None) or 0) for e in entries)
            except (TypeError, ValueError):
                total = 0.0
            anomalies.append(
                ContractAnomaly(
                    anomaly_type="repeat_vendor",
                    description=(
                        f"{vendor} received {len(entries)} separate contract awards "
                        f"totaling ${total:,.0f}. Repeated vendor concentration warrants review."
                    ),
                    evidence_entry_id=str(getattr(entries[0], "id", "")),
                    amount=total,
                    weight=0.4 + min(0.3, len(entries) * 0.05),
                )
            )

    anomalies.sort(key=lambda a: a.weight, reverse=True)
    return anomalies
