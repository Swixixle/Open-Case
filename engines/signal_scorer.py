from __future__ import annotations

import json
import uuid
from typing import Any

from engines.contract_anomaly import ContractAnomaly
from engines.contract_proximity import ContractProximitySignal
from engines.temporal_proximity import ProximitySignal
from signals.dedup import make_signal_identity_hash


def build_signals_from_proximity(
    proximity_signals: list[ProximitySignal],
    case_file_id: uuid.UUID,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    case_s = str(case_file_id)
    for ps in proximity_signals:
        breakdown = ps.to_breakdown()
        fin_uid = uuid.UUID(ps.financial_entry_id)
        dec_uid = uuid.UUID(ps.decision_entry_id)
        identity = make_signal_identity_hash(
            case_s,
            "temporal_proximity",
            fin_uid,
            ps.actor_a,
            ps.decision_entry_id,
        )
        signals.append(
            {
                "case_file_id": case_file_id,
                "signal_identity_hash": identity,
                "signal_type": "temporal_proximity",
                "weight": min(1.0, float(ps.weight)),
                "description": ps.to_description(),
                "weight_breakdown": json.dumps(breakdown, separators=(",", ":")),
                "weight_explanation": ps.to_explanation(),
                "exposure_state": "internal",
                "routing_log": "[]",
                "evidence_ids": [fin_uid, dec_uid],
                "actor_a": ps.actor_a,
                "actor_b": ps.actor_b,
                "event_date_a": ps.financial_date,
                "event_date_b": ps.decision_date,
                "days_between": ps.days_between,
                "amount": ps.amount,
            }
        )
    return signals


def build_signals_from_contract_proximity(
    proximity_signals: list[ContractProximitySignal],
    case_file_id: uuid.UUID,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    case_s = str(case_file_id)
    for cp in proximity_signals:
        breakdown = cp.to_breakdown()
        d_uid = uuid.UUID(cp.donation_entry_id)
        identity = make_signal_identity_hash(
            case_s,
            "contract_proximity",
            d_uid,
            cp.donor_label,
            cp.contract_entry_id,
            contractor_name=cp.contractor_label,
        )
        signals.append(
            {
                "case_file_id": case_file_id,
                "signal_identity_hash": identity,
                "signal_type": "contract_proximity",
                "weight": min(0.55, float(cp.weight)),
                "description": cp.to_description(),
                "weight_breakdown": json.dumps(breakdown, separators=(",", ":")),
                "weight_explanation": cp.to_explanation(),
                "exposure_state": "internal",
                "routing_log": "[]",
                "evidence_ids": [d_uid, uuid.UUID(cp.contract_entry_id)],
                "actor_a": cp.donor_label,
                "actor_b": cp.contractor_label,
                "event_date_a": cp.donation_date,
                "event_date_b": cp.contract_date,
                "days_between": cp.days_between,
                "amount": cp.donation_amount,
            }
        )
    return signals


def build_signals_from_anomalies(
    anomalies: list[ContractAnomaly],
    case_file_id: uuid.UUID,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    case_s = str(case_file_id)
    for ca in anomalies:
        breakdown = ca.to_breakdown()
        eid = uuid.UUID(str(ca.evidence_entry_id))
        identity = make_signal_identity_hash(
            case_s,
            "contract_anomaly",
            eid,
            None,
            None,
            anomaly_subtype=str(ca.anomaly_type),
        )
        signals.append(
            {
                "case_file_id": case_file_id,
                "signal_identity_hash": identity,
                "signal_type": "contract_anomaly",
                "weight": min(1.0, float(ca.weight)),
                "description": ca.description,
                "weight_breakdown": json.dumps(breakdown, separators=(",", ":")),
                "weight_explanation": ca.to_explanation(),
                "exposure_state": "internal",
                "routing_log": "[]",
                "evidence_ids": [eid],
                "actor_a": None,
                "actor_b": None,
                "event_date_a": None,
                "event_date_b": None,
                "days_between": None,
                "amount": ca.amount,
            }
        )
    return signals
