from __future__ import annotations

import json
import uuid
from typing import Any

from engines.contract_anomaly import ContractAnomaly
from engines.contract_proximity import ContractProximitySignal
from engines.temporal_proximity import (
    DonorCluster,
    assert_cluster_direction_verified,
    build_cluster_copy_text,
)
from signals.dedup import make_signal_identity_hash


def compute_relevance_score(cluster: DonorCluster) -> float:
    """Jurisdiction + sponsorship concentration for donor cluster signals."""
    return float(cluster.relevance_score)


def evaluate_confirmation_status(signal: dict) -> dict:
    has_collision = bool(signal.get("has_collision", False))
    direction_verified = bool(signal.get("direction_verified", True))
    relevance = float(signal.get("relevance_score", 0.0) or 0.0)
    checks = {
        "identity_resolved": not has_collision,
        "direction_verified": direction_verified,
        "jurisdictional_match": relevance >= 0.5,
        "sponsorship_present": relevance >= 0.9,
    }
    confirmed = (
        checks["identity_resolved"]
        and checks["direction_verified"]
        and (checks["jurisdictional_match"] or checks["sponsorship_present"])
    )
    return {
        "confirmed": confirmed,
        "confirmation_checks": checks,
        "confirmation_basis": [k for k, v in checks.items() if v],
    }


def build_signals_from_proximity(
    donor_clusters: list[DonorCluster],
    case_file_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """One persisted signal per donor–official cluster."""
    signals: list[dict[str, Any]] = []
    case_s = str(case_file_id)
    for cluster in donor_clusters:
        description, proximity_summary_manual = build_cluster_copy_text(cluster)
        assert_cluster_direction_verified(cluster, description, proximity_summary_manual)

        identity = make_signal_identity_hash(
            case_s,
            "temporal_proximity",
            None,
            cluster.donor_key,
            f"cluster|{cluster.official_key}",
        )

        eid_set: list[uuid.UUID] = []
        seen: set[str] = set()
        for row in cluster.supporting_pairs:
            for key in ("financial_entry_id", "decision_entry_id"):
                raw_id = row.get(key)
                if not raw_id:
                    continue
                sid = str(raw_id)
                if sid in seen:
                    continue
                seen.add(sid)
                try:
                    eid_set.append(uuid.UUID(sid))
                except ValueError:
                    continue

        rel_score = float(cluster.relevance_score)
        breakdown = {
            "kind": "donor_cluster",
            "donor": cluster.donor_display,
            "official": cluster.official_display,
            "total_amount": cluster.total_amount,
            "donation_count": cluster.donation_count,
            "vote_count": cluster.vote_count,
            "pair_count": cluster.pair_count,
            "min_gap_days": cluster.min_gap_days,
            "median_gap_days": round(cluster.median_gap_days, 2),
            "exemplar_vote": cluster.exemplar_vote,
            "exemplar_gap": cluster.exemplar_gap,
            "exemplar_direction": cluster.exemplar_direction,
            "exemplar_position": cluster.exemplar_position,
            "proximity_score": cluster.proximity_score,
            "amount_multiplier": cluster.amount_multiplier,
            "committee_label": cluster.committee_label,
            "has_collision": cluster.has_collision,
            "has_jurisdictional_match": cluster.has_jurisdictional_match,
            "has_lda_filing": cluster.has_lda_filing,
            "has_sponsorship": any(
                bool(x.get("subject_is_sponsor")) for x in cluster.supporting_pairs
            ),
            "relevance_score": rel_score,
        }

        exposure_state = "unresolved" if cluster.has_collision else "internal"

        conf_eval = evaluate_confirmation_status(
            {
                "has_collision": cluster.has_collision,
                "direction_verified": True,
                "relevance_score": rel_score,
            }
        )

        signals.append(
            {
                "case_file_id": case_file_id,
                "signal_identity_hash": identity,
                "signal_type": "temporal_proximity",
                "weight": min(1.0, float(cluster.final_weight)),
                "description": description,
                "weight_breakdown": json.dumps(breakdown, separators=(",", ":"), default=str),
                "weight_explanation": (
                    f"Clustered donor signal: proximity tier from {cluster.min_gap_days}d "
                    f"tightest gap × amount tier on ${cluster.total_amount:,.0f} total."
                ),
                "exposure_state": exposure_state,
                "routing_log": "[]",
                "evidence_ids": eid_set,
                "actor_a": cluster.donor_display,
                "actor_b": cluster.official_display,
                "event_date_a": cluster.exemplar_financial_date,
                "event_date_b": cluster.exemplar_decision_date,
                "days_between": cluster.exemplar_gap,
                "amount": cluster.total_amount,
                "direction_verified": True,
                "temporal_class": cluster.temporal_class,
                "proximity_summary_override": proximity_summary_manual,
                "relevance_score": rel_score,
                "confirmed": bool(conf_eval["confirmed"]),
                "confirmation_checks": json.dumps(
                    conf_eval["confirmation_checks"], separators=(",", ":")
                ),
                "confirmation_basis": json.dumps(
                    conf_eval["confirmation_basis"], separators=(",", ":")
                ),
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
                "direction_verified": True,
                "temporal_class": None,
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
                "direction_verified": True,
                "temporal_class": None,
            }
        )
    return signals
