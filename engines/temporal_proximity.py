from __future__ import annotations

import json
import logging
import re
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from core.datetime_utils import coerce_utc, coerce_utc_from_date_only
from engines.relevance import compute_relevance_score

logger = logging.getLogger(__name__)


@dataclass
class RawProximityPair:
    """One donation × one vote pairing (internal only)."""

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
    financial_flagged: bool
    financial_jurisdictional_match: bool = False
    subject_is_sponsor: bool = False
    subject_is_cosponsor: bool = False


@dataclass
class DonorCluster:
    """
    One signal per donor–official relationship: aggregated pairings with an exemplar vote.
    """

    donor_key: str
    donor_display: str
    official_key: str
    official_display: str
    total_amount: float
    donation_count: int
    vote_count: int
    pair_count: int
    min_gap_days: int
    median_gap_days: float
    exemplar_vote: str
    exemplar_gap: int
    exemplar_direction: str
    exemplar_position: str
    temporal_class: str
    committee_label: str
    final_weight: float
    proximity_score: float
    amount_multiplier: float
    has_collision: bool
    has_jurisdictional_match: bool = False
    has_lda_filing: bool = False
    has_regulatory_comment: bool = False
    has_hearing_appearance: bool = False
    regulatory_comment_confidence: str | None = None
    hearing_match_confidence: str | None = None
    relevance_score: float = 0.0
    exemplar_financial_date: str = ""
    exemplar_decision_date: str = ""
    supporting_pairs: list[dict[str, Any]] = field(default_factory=list)
    witness_evidence_ids: list[uuid.UUID] = field(default_factory=list)


FINANCIAL_ENTRY_TYPES = frozenset({"financial_connection", "disclosure"})
DECISION_ENTRY_TYPES = frozenset({"vote_record", "timeline_event"})

_DECISION_BILL_RE = re.compile(
    r"(?i)\b("
    r"PN\s*#?\s*\d+|"
    r"H\.?\s*J\.?\s*R\.?\s*E\.?\s*S\.?\s*\d+|"
    r"S\.?\s*J\.?\s*R\.?\s*E\.?\s*S\.?\s*\d+|"
    r"H\.?\s*R\.?\s*\d+|"
    r"S\.?\s*\d+"
    r")\b"
)
_VOTE_POSITION_RE = re.compile(r"Vote:\s*(\S+)", re.I)
_CONGRESS_IN_TITLE_RE = re.compile(r"\((\d+)(?:st|nd|rd|th)\s*Congress\)", re.I)


def _financial_entity_key(actor_a: str) -> str:
    return (actor_a or "").strip().lower()


def _official_entity_key(actor_b: str) -> str:
    return (actor_b or "").strip().lower()


def _decision_vote_key(decision_event: str) -> str:
    text = decision_event or ""
    m = _DECISION_BILL_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip().upper()
    return text.strip().lower()[:240]


def _vote_position_from_title(title: str) -> str:
    m = _VOTE_POSITION_RE.search(title or "")
    return m.group(1) if m else "—"


def _exemplar_vote_label(decision_event: str) -> str:
    bill = _decision_vote_key(decision_event)
    m = _CONGRESS_IN_TITLE_RE.search(decision_event or "")
    if m:
        return f"{bill} ({m.group(1)}th Congress)"
    return bill


def _sponsor_flags_from_vote_entry(entry: Any) -> tuple[bool, bool]:
    raw = getattr(entry, "raw_data_json", "") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False, False
    if not isinstance(data, dict):
        return False, False
    return bool(data.get("subject_is_sponsor")), bool(data.get("subject_is_cosponsor"))


def _financial_jurisdictional_from_entry(entry: Any) -> bool:
    v = getattr(entry, "jurisdictional_match", None)
    if v is not None:
        return bool(v)
    return False


def _donor_has_lda(donor_key: str, evidence_entries: list[Any]) -> bool:
    dk = (donor_key or "").strip().lower()
    for e in evidence_entries:
        if getattr(e, "entry_type", "") != "lobbying_filing":
            continue
        raw = getattr(e, "raw_data_json", "") or "{}"
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if str(j.get("donor_key", "")).strip().lower() == dk:
            return True
    return False


def _dedupe_pairs_by_donor_and_vote(
    pairs: list[RawProximityPair],
) -> list[RawProximityPair]:
    best: dict[tuple[str, str], RawProximityPair] = {}
    for p in pairs:
        key = (_financial_entity_key(p.actor_a), _decision_vote_key(p.decision_event))
        cur = best.get(key)
        if cur is None or abs(p.days_between) < abs(cur.days_between):
            best[key] = p
        elif cur is not None and abs(p.days_between) == abs(cur.days_between):
            if p.amount > cur.amount:
                best[key] = p
    return list(best.values())


def _proximity_score_from_abs_gap(abs_days: int) -> float:
    if abs_days <= 7:
        return 1.00
    if abs_days <= 14:
        return 0.85
    if abs_days <= 30:
        return 0.70
    if abs_days <= 60:
        return 0.50
    if abs_days <= 90:
        return 0.35
    if abs_days <= 180:
        return 0.20
    return 0.10


def _amount_multiplier_from_total(total_amount: float) -> float:
    if total_amount >= 50000:
        return 1.0
    if total_amount >= 10000:
        return 0.85
    if total_amount >= 2500:
        return 0.70
    if total_amount >= 500:
        return 0.55
    return 0.40


def _entry_event_dt(entry: Any) -> datetime | None:
    if not getattr(entry, "date_of_event", None):
        return None
    d = entry.date_of_event
    if isinstance(d, datetime):
        return coerce_utc(d)
    if isinstance(d, date):
        return coerce_utc_from_date_only(d)
    if isinstance(d, str):
        return coerce_utc(d)
    return coerce_utc(str(d))


def _actor_for(entry: Any, fallback: str) -> str:
    m = getattr(entry, "matched_name", None)
    if m and str(m).strip():
        return str(m).strip()
    return fallback


def _collect_raw_pairs(
    evidence_entries: list[Any],
    max_days: int,
) -> list[RawProximityPair]:
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

    pairs: list[RawProximityPair] = []
    for f_date, f_entry in financial_events:
        amt = getattr(f_entry, "amount", None) or 0.0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            amt = 0.0
        flagged = bool(getattr(f_entry, "flagged_for_review", False))
        for d_date, d_entry in decision_events:
            f_utc = coerce_utc(f_date)
            d_utc = coerce_utc(d_date)
            if f_utc is None or d_utc is None:
                logger.warning(
                    "Skipping proximity pair: could not coerce event datetimes to UTC."
                )
                continue
            days_diff = (d_utc - f_utc).days
            if -30 <= days_diff <= max_days:
                fin_id = str(getattr(f_entry, "id", ""))
                dec_id = str(getattr(d_entry, "id", ""))
                fd = getattr(f_entry, "date_of_event", None)
                dd = getattr(d_entry, "date_of_event", None)
                fd_s = fd.isoformat() if hasattr(fd, "isoformat") else str(fd or "")
                dd_s = dd.isoformat() if hasattr(dd, "isoformat") else str(dd or "")
                sp, csp = _sponsor_flags_from_vote_entry(d_entry)
                pairs.append(
                    RawProximityPair(
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
                        financial_flagged=flagged,
                        financial_jurisdictional_match=_financial_jurisdictional_from_entry(
                            f_entry
                        ),
                        subject_is_sponsor=sp,
                        subject_is_cosponsor=csp,
                    )
                )
    return _dedupe_pairs_by_donor_and_vote(pairs)


def _select_exemplar_pair(rel_pairs: list[RawProximityPair]) -> RawProximityPair:
    def exemplar_score(p: RawProximityPair) -> float:
        proximity = _proximity_score_from_abs_gap(abs(p.days_between))
        relevance_bonus = (
            0.3 if p.subject_is_sponsor else (0.15 if p.subject_is_cosponsor else 0.0)
        )
        return proximity + relevance_bonus

    return max(rel_pairs, key=exemplar_score)


def _cluster_from_pairs(
    rel_pairs: list[RawProximityPair],
    committee_label: str,
    evidence_entries: list[Any],
) -> DonorCluster | None:
    if not rel_pairs:
        return None

    donor_key = _financial_entity_key(rel_pairs[0].actor_a)
    official_key = _official_entity_key(rel_pairs[0].actor_b)
    donor_display = rel_pairs[0].actor_a.strip() or donor_key
    official_display = rel_pairs[0].actor_b.strip() or official_key

    fin_amounts: dict[str, float] = {}
    dec_ids: set[str] = set()
    for p in rel_pairs:
        if p.financial_entry_id not in fin_amounts:
            fin_amounts[p.financial_entry_id] = float(p.amount)
        dec_ids.add(p.decision_entry_id)

    total_amount = float(sum(fin_amounts.values()))
    donation_count = len(fin_amounts)
    vote_count = len(dec_ids)
    pair_count = len(rel_pairs)
    has_collision = any(p.financial_flagged for p in rel_pairs)
    has_jurisdictional_match = any(p.financial_jurisdictional_match for p in rel_pairs)
    has_lda_filing = _donor_has_lda(donor_key, evidence_entries)

    gaps = [p.days_between for p in rel_pairs]
    median_gap = float(statistics.median(gaps)) if gaps else 0.0

    exemplar = _select_exemplar_pair(rel_pairs)
    eg = exemplar.days_between
    abs_eg = abs(eg)
    exemplar_vote = _exemplar_vote_label(exemplar.decision_event)
    exemplar_position = _vote_position_from_title(exemplar.decision_event)

    if eg > 0:
        exemplar_direction = "before"
        temporal_class = "anticipatory"
    elif eg < 0:
        exemplar_direction = "after"
        temporal_class = "retrospective"
    else:
        exemplar_direction = "same_day"
        temporal_class = "anticipatory"

    prox = _proximity_score_from_abs_gap(abs_eg)
    mult = _amount_multiplier_from_total(total_amount)
    relevance_score = compute_relevance_score(
        has_jurisdictional_match=has_jurisdictional_match,
        subject_is_sponsor_any=any(p.subject_is_sponsor for p in rel_pairs),
        subject_is_cosponsor_any=any(p.subject_is_cosponsor for p in rel_pairs),
        has_lda_filing=has_lda_filing,
        has_regulatory_comment=False,
        has_hearing_appearance=False,
    )
    final_weight = round(
        min(1.0, prox * mult * (1.0 + relevance_score * 0.5)),
        4,
    )

    supporting = [
        {
            "financial_entry_id": p.financial_entry_id,
            "decision_entry_id": p.decision_entry_id,
            "days_between": p.days_between,
            "amount": p.amount,
            "financial_flagged": p.financial_flagged,
            "decision_event": p.decision_event,
            "jurisdictional_match": p.financial_jurisdictional_match,
            "subject_is_sponsor": p.subject_is_sponsor,
            "subject_is_cosponsor": p.subject_is_cosponsor,
        }
        for p in rel_pairs
    ]

    return DonorCluster(
        donor_key=donor_key,
        donor_display=donor_display,
        official_key=official_key,
        official_display=official_display,
        total_amount=total_amount,
        donation_count=donation_count,
        vote_count=vote_count,
        pair_count=pair_count,
        min_gap_days=abs_eg,
        median_gap_days=median_gap,
        exemplar_vote=exemplar_vote,
        exemplar_gap=eg,
        exemplar_direction=exemplar_direction,
        exemplar_position=exemplar_position,
        temporal_class=temporal_class,
        committee_label=committee_label or "the recipient committee",
        final_weight=final_weight,
        proximity_score=prox,
        amount_multiplier=mult,
        has_collision=has_collision,
        has_jurisdictional_match=has_jurisdictional_match,
        has_lda_filing=has_lda_filing,
        has_regulatory_comment=False,
        has_hearing_appearance=False,
        regulatory_comment_confidence=None,
        hearing_match_confidence=None,
        relevance_score=relevance_score,
        exemplar_financial_date=exemplar.financial_date,
        exemplar_decision_date=exemplar.decision_date,
        supporting_pairs=supporting,
    )


def refresh_cluster_scoring(cluster: DonorCluster) -> None:
    """Recompute relevance and final_weight after witness-room enrichment."""
    cluster.relevance_score = compute_relevance_score(
        has_jurisdictional_match=cluster.has_jurisdictional_match,
        subject_is_sponsor_any=any(
            bool(x.get("subject_is_sponsor")) for x in cluster.supporting_pairs
        ),
        subject_is_cosponsor_any=any(
            bool(x.get("subject_is_cosponsor")) for x in cluster.supporting_pairs
        ),
        has_lda_filing=cluster.has_lda_filing,
        has_regulatory_comment=cluster.has_regulatory_comment,
        has_hearing_appearance=cluster.has_hearing_appearance,
    )
    cluster.final_weight = round(
        min(
            1.0,
            cluster.proximity_score
            * cluster.amount_multiplier
            * (1.0 + cluster.relevance_score * 0.5),
        ),
        4,
    )


def build_cluster_copy_text(cluster: DonorCluster) -> tuple[str, str]:
    """Long description and proximity_summary; mandatory before/after language."""
    donor = cluster.donor_display
    official = cluster.official_display
    committee = cluster.committee_label
    total = cluster.total_amount
    pos = cluster.exemplar_position
    bill = cluster.exemplar_vote
    d = cluster.exemplar_gap
    absd = abs(d)
    amt_s = f"{total:,.2f}"

    if d > 0:
        line1 = (
            f"{donor} donated ${amt_s} to {committee} {absd} days before "
            f"{official} voted {pos} on {bill}."
        )
        summary = f"Donation occurred {absd} days before the vote"
    elif d < 0:
        line1 = (
            f"{donor} donated ${amt_s} to {committee} {absd} days after "
            f"{official} voted {pos} on {bill}."
        )
        summary = f"Donation occurred {absd} days after the vote"
    else:
        line1 = (
            f"{donor} donated ${amt_s} to {committee} on the same calendar day "
            f"{official} voted {pos} on {bill}."
        )
        summary = "Donation occurred the same day as the vote"

    tail = (
        f" The tightest timing among {cluster.pair_count} pairings in this window "
        f"involves {cluster.vote_count} distinct votes (median gap "
        f"{cluster.median_gap_days:.1f} days)."
    )
    description = line1 + tail
    return description, summary


def verify_cluster_direction_text(
    cluster: DonorCluster, description: str, proximity_summary: str
) -> bool:
    """True if narrative matches days_between sign (non-negotiable)."""
    d = cluster.exemplar_gap
    desc_l = description.lower()
    sum_l = proximity_summary.lower()
    if d > 0:
        if "before" not in desc_l:
            return False
        if "after" in sum_l and "before" not in sum_l:
            return False
        return "before" in sum_l
    if d < 0:
        if "after" not in desc_l:
            return False
        return "after" in sum_l
    return "same" in sum_l or "same day" in sum_l


def assert_cluster_direction_verified(
    cluster: DonorCluster, description: str, proximity_summary: str
) -> None:
    if not verify_cluster_direction_text(cluster, description, proximity_summary):
        raise ValueError(
            f"direction_verified failed for cluster donor={cluster.donor_key!r} "
            f"exemplar_gap={cluster.exemplar_gap}: text does not match timing math."
        )


def detect_proximity(
    evidence_entries: list[Any],
    max_days: int = 90,
    committee_label: str = "",
) -> list[DonorCluster]:
    """
    Evidence → raw pairings → donor–official clusters → one row per relationship.
    """
    raw = _collect_raw_pairs(evidence_entries, max_days)
    if not raw:
        return []

    by_rel: dict[tuple[str, str], list[RawProximityPair]] = {}
    for p in raw:
        key = (_financial_entity_key(p.actor_a), _official_entity_key(p.actor_b))
        by_rel.setdefault(key, []).append(p)

    clusters: list[DonorCluster] = []
    for rel_pairs in by_rel.values():
        c = _cluster_from_pairs(rel_pairs, committee_label, evidence_entries)
        if c is not None and c.final_weight > 0.01:
            clusters.append(c)

    clusters.sort(key=lambda x: x.final_weight, reverse=True)
    return clusters
