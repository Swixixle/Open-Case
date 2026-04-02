from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Signal, SignalAuditLog


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, uuid.UUID):
        return str(v)
    return str(v).strip().lower()


def make_signal_identity_hash(
    case_id: str,
    signal_type: str,
    evidence_id: uuid.UUID | None,
    donor_name: str | None,
    vote_id: str | None,
    contractor_name: str | None = None,
    anomaly_subtype: str | None = None,
) -> str:
    parts = [
        _norm(case_id),
        _norm(signal_type),
        _norm(evidence_id),
        _norm(donor_name),
        _norm(vote_id),
        _norm(contractor_name),
        _norm(anomaly_subtype),
    ]
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_evidence_id_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _build_proximity_summary(sig: Signal) -> str | None:
    if sig.signal_type == "temporal_proximity":
        days = sig.days_between
        if days is None:
            return "Timing correlates with related official action; exact gap unavailable."
        if days == 0:
            return "Occurred same day as related official action."
        if days == 1:
            return "Occurred next day after related official action."
        return f"Occurred {days} days after related official action."
    if sig.signal_type == "contract_proximity":
        days = sig.days_between
        if days is None:
            return "Campaign contribution timing correlates with a federal award to a related entity."
        ad = abs(days)
        return (
            f"A reported contribution and federal award are separated by {ad} days "
            "(secondary proximity signal — no vote linkage required)."
        )
    return None


def upsert_signal(
    db: Session,
    signal_dict: dict[str, Any],
    performed_by: str | None = None,
) -> Signal:
    case_id = signal_dict["case_file_id"]
    identity = signal_dict.get("signal_identity_hash")
    if not identity:
        raise ValueError("signal_dict must include signal_identity_hash")

    stmt = (
        select(Signal)
        .where(Signal.case_file_id == case_id)
        .where(Signal.signal_identity_hash == identity)
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    existing = db.execute(stmt).scalar_one_or_none()
    new_weight = float(signal_dict.get("weight", 0.0))

    if existing is None:
        row: dict[str, Any] = {k: v for k, v in signal_dict.items() if k != "id"}
        row.setdefault("repeat_count", 1)
        eids = row.get("evidence_ids")
        if isinstance(eids, list):
            row["evidence_ids"] = json.dumps([str(x) for x in eids], separators=(",", ":"))
        elif eids is None:
            row["evidence_ids"] = "[]"
        sig = Signal(**row)
        sig.proximity_summary = _build_proximity_summary(sig)
        db.add(sig)
        db.flush()
        db.add(
            SignalAuditLog(
                signal_id=sig.id,
                action="created",
                performed_by=performed_by,
                old_weight=None,
                new_weight=new_weight,
                note="initial",
            )
        )
        return sig

    old_w = float(existing.weight)
    repeat = (existing.repeat_count or 1) + 1
    existing.repeat_count = repeat

    merged_ids = _parse_evidence_id_list(existing.evidence_ids)
    eid_list = signal_dict.get("evidence_ids")
    if isinstance(eid_list, list):
        for x in eid_list:
            s = str(x)
            if s not in merged_ids:
                merged_ids.append(s)
    elif isinstance(eid_list, str):
        try:
            for x in json.loads(eid_list):
                s = str(x)
                if s not in merged_ids:
                    merged_ids.append(s)
        except json.JSONDecodeError:
            pass
    existing.evidence_ids = json.dumps(merged_ids)

    if new_weight > old_w:
        existing.weight = new_weight
        if signal_dict.get("weight_breakdown") is not None:
            existing.weight_breakdown = signal_dict["weight_breakdown"]
        if signal_dict.get("weight_explanation") is not None:
            existing.weight_explanation = signal_dict["weight_explanation"]
        if signal_dict.get("description") is not None:
            existing.description = signal_dict["description"]
        if signal_dict.get("days_between") is not None:
            existing.days_between = signal_dict["days_between"]
        if signal_dict.get("event_date_a") is not None:
            existing.event_date_a = signal_dict["event_date_a"]
        if signal_dict.get("event_date_b") is not None:
            existing.event_date_b = signal_dict["event_date_b"]
        if signal_dict.get("actor_a") is not None:
            existing.actor_a = signal_dict["actor_a"]
        if signal_dict.get("actor_b") is not None:
            existing.actor_b = signal_dict["actor_b"]
        db.add(
            SignalAuditLog(
                signal_id=existing.id,
                action="weight_updated",
                performed_by=performed_by,
                old_weight=old_w,
                new_weight=new_weight,
                note=f"repeat {repeat}",
            )
        )
    elif new_weight < old_w:
        db.add(
            SignalAuditLog(
                signal_id=existing.id,
                action="weight_unchanged_lower",
                performed_by=performed_by,
                old_weight=old_w,
                new_weight=new_weight,
                note=f"repeat {repeat}; kept {old_w}",
            )
        )
    else:
        db.add(
            SignalAuditLog(
                signal_id=existing.id,
                action="repeat_observed",
                performed_by=performed_by,
                old_weight=old_w,
                new_weight=old_w,
                note=f"repeat {repeat}",
            )
        )

    na = signal_dict.get("amount")
    if na is not None:
        oa = existing.amount
        if oa is None or float(na) > float(oa):
            existing.amount = float(na)

    existing.proximity_summary = _build_proximity_summary(existing)
    return existing
