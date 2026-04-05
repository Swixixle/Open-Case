"""When to attach EthicalAlt proportionality data to signals and pattern alerts (public-official donor context)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Signal
from services.proportionality_client import (
    fetch_proportionality_packet,
    fetch_proportionality_packet_sync,
)

SIGNAL_CATEGORY_MAP = {
    "donor_cluster": "political",
    "lobbying_proximity": "political",
    "pac_proximity": "political",
    "revolving_door": "legal",
}

_PATTERN_ALERT_RULES_WITH_CONTEXT = frozenset(
    {"COMMITTEE_SWEEP_V1", "FINGERPRINT_BLOOM_V1"}
)


def _signal_breakdown_dict(s: Signal) -> dict[str, Any]:
    try:
        raw = json.loads(s.weight_breakdown or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_anticipatory_donor_cluster(s: Signal, bd: dict[str, Any]) -> bool:
    """Matches temporal classification used in investigate/report (skip proportionality for anticipatory)."""
    if bd.get("kind") != "donor_cluster":
        return False
    if s.exposure_state == "unresolved" or s.signal_type != "temporal_proximity":
        return False
    tc = (s.temporal_class or "").strip().lower()
    if tc == "anticipatory":
        return True
    if tc == "retrospective":
        return False
    return bool(s.days_between is not None and s.days_between >= 0)


def proportionality_category_and_amount(
    s: Signal, bd: dict[str, Any] | None = None
) -> tuple[str | None, float | None]:
    """
    Returns (api_category, amount) when Phase 1 triggers apply, else (None, None).
    """
    bd = bd if bd is not None else _signal_breakdown_dict(s)
    if bd.get("kind") != "donor_cluster":
        return None, None
    if s.dismissed or s.exposure_state == "unresolved":
        return None, None
    if _is_anticipatory_donor_cluster(s, bd):
        return None, None
    kind_key = bd.get("kind")
    category = SIGNAL_CATEGORY_MAP.get(str(kind_key) if kind_key is not None else "")
    if category is None:
        return None, None
    raw_amt = bd.get("total_amount")
    try:
        amt = float(raw_amt) if raw_amt is not None else 0.0
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        return None, None
    return category, amt


async def proportionality_packet_for_signal(s: Signal) -> dict[str, Any] | None:
    cat, amt = proportionality_category_and_amount(s)
    if not cat or amt is None:
        return None
    return await fetch_proportionality_packet(
        category=cat,
        violation_type="campaign contribution",
        charge_status=None,
        amount_involved=float(amt),
    )


def proportionality_packet_for_signal_sync(s: Signal) -> dict[str, Any] | None:
    cat, amt = proportionality_category_and_amount(s)
    if not cat or amt is None:
        return None
    return fetch_proportionality_packet_sync(
        category=cat,
        violation_type="campaign contribution",
        charge_status=None,
        amount_involved=float(amt),
    )


def signal_to_signing_dict(s: Signal) -> dict[str, Any]:
    """Subset of signal fields for case seal; include proportionality_packet only when fetched and non-empty."""
    bd = _signal_breakdown_dict(s)
    out: dict[str, Any] = {
        "id": str(s.id),
        "signal_identity_hash": s.signal_identity_hash or "",
        "signal_type": s.signal_type,
    }
    pkt = proportionality_packet_for_signal_sync(s)
    if pkt:
        out["proportionality_packet"] = pkt
    return out


def case_signals_for_signing(db: Session, case_file_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Signal)
        .where(Signal.case_file_id == case_file_id)
        .order_by(Signal.id)
    ).all()
    return [signal_to_signing_dict(s) for s in rows]


def attach_proportionality_to_pattern_alerts(
    db: Session, alerts: list[Any]
) -> None:
    """Populate PatternAlert.proportionality_context for sweep / bloom alerts (mutates alerts in place)."""
    for a in alerts:
        rid = getattr(a, "rule_id", None)
        if rid not in _PATTERN_ALERT_RULES_WITH_CONTEXT:
            continue
        refs: list[str] = list(getattr(a, "evidence_refs", []) or [])
        ctx: list[dict[str, Any]] = []
        for sid in refs:
            try:
                uid = uuid.UUID(str(sid))
            except ValueError:
                continue
            sig = db.get(Signal, uid)
            if not sig:
                continue
            bd = _signal_breakdown_dict(sig)
            cat, amt = proportionality_category_and_amount(sig, bd)
            if not cat or amt is None:
                continue
            packet = fetch_proportionality_packet_sync(
                category=cat,
                violation_type="campaign contribution",
                charge_status=None,
                amount_involved=float(amt),
            )
            if not packet:
                continue
            official = str(
                bd.get("official") or sig.actor_b or ""
            ).strip() or "Unknown official"
            donor = str(bd.get("donor") or sig.actor_a or "").strip() or "Unknown donor"
            ctx.append(
                {
                    "signal_id": str(sig.id),
                    "official": official,
                    "donor": donor,
                    "amount": float(amt),
                    "packet": packet,
                }
            )
        if ctx:
            a.proportionality_context = ctx
        else:
            a.proportionality_context = None
