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


def evidence_tier_from_checks(conf_checks: dict[str, Any] | None) -> str:
    """Journalist-facing tier from enrichment indicator count (not the internal confirmed flag)."""
    count = 0
    if conf_checks and isinstance(conf_checks, dict):
        count = int(conf_checks.get("relevance_indicator_count") or 0)
    if count >= 2:
        return "Multi-source"
    if count == 1:
        return "Corroborated"
    return "Documented"


def compute_relevance_score(cluster: DonorCluster) -> float:
    """Jurisdiction + sponsorship concentration for donor cluster signals."""
    return float(cluster.relevance_score)


def evaluate_confirmation_status(signal_data: dict) -> dict:
    checks = {
        "identity_resolved": not signal_data.get("has_collision", False),
        "direction_verified": signal_data.get("direction_verified", False),
        "jurisdictional_match": float(signal_data.get("relevance_score", 0) or 0) >= 0.5,
        "sponsorship_present": signal_data.get("subject_is_sponsor", False)
        or signal_data.get("subject_is_cosponsor", False),
        "lda_filing": signal_data.get("has_lda_filing", False),
        "regulatory_comment": signal_data.get("has_regulatory_comment", False),
        "hearing_appearance": signal_data.get("has_hearing_appearance", False),
    }

    relevance_indicators = [
        checks["jurisdictional_match"],
        checks["sponsorship_present"],
        checks["lda_filing"],
        checks["regulatory_comment"],
        checks["hearing_appearance"],
    ]
    relevance_count = sum(1 for x in relevance_indicators if x)

    confirmed = (
        checks["identity_resolved"]
        and checks["direction_verified"]
        and relevance_count >= 2
    )

    return {
        "confirmed": confirmed,
        "confirmation_checks": checks,
        "confirmation_basis": [k for k, v in checks.items() if v],
        "relevance_indicator_count": relevance_count,
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
        for wid in cluster.witness_evidence_ids:
            ws = str(wid)
            if ws not in seen:
                seen.add(ws)
                eid_set.append(wid)

        rel_score = float(cluster.relevance_score)
        rec_norm = (cluster.receipt_date or "").strip()
        if not rec_norm and cluster.exemplar_financial_date:
            rec_norm = str(cluster.exemplar_financial_date).strip()[:10]
        ex_fin = (cluster.exemplar_financial_date or "").strip()
        if not ex_fin and rec_norm:
            ex_fin = rec_norm
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
            "exemplar_financial_date": ex_fin,
            "receipt_date": rec_norm,
            "proximity_score": cluster.proximity_score,
            "amount_multiplier": cluster.amount_multiplier,
            "committee_label": cluster.committee_label,
            "has_collision": cluster.has_collision,
            "has_jurisdictional_match": cluster.has_jurisdictional_match,
            "has_lda_filing": cluster.has_lda_filing,
            "has_regulatory_comment": cluster.has_regulatory_comment,
            "regulatory_comment_confidence": cluster.regulatory_comment_confidence,
            "has_hearing_appearance": cluster.has_hearing_appearance,
            "hearing_match_confidence": cluster.hearing_match_confidence,
            "has_sponsorship": any(
                bool(x.get("subject_is_sponsor")) for x in cluster.supporting_pairs
            ),
            "relevance_score": rel_score,
        }

        exposure_state = "unresolved" if cluster.has_collision else "internal"

        sponsor_any = any(
            bool(x.get("subject_is_sponsor")) for x in cluster.supporting_pairs
        )
        cosponsor_any = any(
            bool(x.get("subject_is_cosponsor")) for x in cluster.supporting_pairs
        )
        conf_eval = evaluate_confirmation_status(
            {
                "has_collision": cluster.has_collision,
                "direction_verified": True,
                "relevance_score": rel_score,
                "subject_is_sponsor": sponsor_any,
                "subject_is_cosponsor": cosponsor_any,
                "has_lda_filing": cluster.has_lda_filing,
                "has_regulatory_comment": cluster.has_regulatory_comment,
                "has_hearing_appearance": cluster.has_hearing_appearance,
            }
        )
        checks_payload = {
            **conf_eval["confirmation_checks"],
            "relevance_indicator_count": conf_eval["relevance_indicator_count"],
        }

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
                "confirmation_checks": json.dumps(checks_payload, separators=(",", ":")),
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
