"""
Plain-English gap analysis from structured FEC + vote data (journalist-ready).

Does not assert causation; sentences follow fixed templates only.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engines.pattern_engine import (
    ISSUE_CODE_TO_SECTOR,
    _compute_case_sector_alignment_rates,
    _senator_vote_position_from_record,
)
from models import CaseFile, EvidenceEntry, Signal
from signals.dedup import _parse_evidence_id_list

TEMPLATES = {
    "donation_vote_proximity": (
        "Public records show {donor_entity} contributed ${donation_amount:,.0f} "
        "to {senator_name}'s campaign on {donation_date}. "
        "{days_between} days later, {senator_name} voted {vote_result} on "
        '"{vote_description}" ({vote_date}).'
    ),
    "sector_pattern": (
        "FEC records document {senator_name} receiving ${total_amount:,.0f} "
        "from {sector} interests across {cycle_count} election cycles. "
        "During this period, {senator_name} voted {vote_alignment} on "
        "{vote_count} relevant {sector} measures."
    ),
    "high_volume_donor": (
        "Public records show {donor_entity} contributed ${total_amount:,.0f} "
        "to {senator_name} across {transaction_count} transactions. "
        "{donor_entity} has registered lobbying activity on {issue_areas}."
    ),
}

GAP_ANALYSIS_DISCLAIMER = (
    "This gap analysis documents public records only. "
    "Timelines do not prove causation or wrongdoing. "
    "All sentences are for human review and verification."
)


def _gap_confidence(days_between: int) -> str:
    if days_between <= 30:
        return "high"
    if days_between > 90:
        return "medium"
    return "medium"


def _vote_entry_details(entry: EvidenceEntry) -> tuple[str, str]:
    raw: dict[str, Any] = {}
    try:
        raw = json.loads(entry.raw_data_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    pos = _senator_vote_position_from_record(raw) or "RECORDED"
    desc = (
        str(raw.get("question") or raw.get("vote_question") or entry.title or "vote")
        .strip()
        or "vote"
    )
    return pos, desc


def _fec_urls_from_evidence_ids(db: Session, eid_strings: list[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for sid in eid_strings:
        try:
            eid = uuid.UUID(str(sid).strip())
        except ValueError:
            continue
        ent = db.get(EvidenceEntry, eid)
        if ent is None:
            continue
        if ent.entry_type not in (
            "financial_connection",
            "fec_historical",
            "fec_jfc_donor",
        ):
            continue
        u = (ent.source_url or "").strip()
        if u and "fec.gov" in u.lower() and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _first_fec_amount_and_url(
    db: Session, eid_strings: list[str]
) -> tuple[float, str | None]:
    for sid in eid_strings:
        try:
            eid = uuid.UUID(str(sid).strip())
        except ValueError:
            continue
        ent = db.get(EvidenceEntry, eid)
        if ent is None:
            continue
        if ent.entry_type not in ("financial_connection", "fec_historical"):
            continue
        amt = float(ent.amount or 0)
        try:
            raw = json.loads(ent.raw_data_json or "{}")
            if isinstance(raw, dict) and raw.get("contribution_receipt_amount") is not None:
                amt = float(raw.get("contribution_receipt_amount") or amt)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        if amt > 0:
            return amt, (ent.source_url or "").strip() or None
    return 0.0, None


def _find_vote_entry_for_signal(db: Session, eid_strings: list[str]) -> EvidenceEntry | None:
    for sid in eid_strings:
        try:
            eid = uuid.UUID(str(sid).strip())
        except ValueError:
            continue
        ent = db.get(EvidenceEntry, eid)
        if ent and ent.entry_type == "vote_record":
            return ent
    return None


def _case_fec_totals_and_cycles(db: Session, case_id: uuid.UUID) -> tuple[float, int]:
    total = 0.0
    cycles: set[int] = set()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type.in_(("financial_connection", "fec_historical")),
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        if isinstance(raw, dict):
            for key in ("fec_cycle", "two_year_transaction_period"):
                if raw.get(key) is not None:
                    try:
                        cycles.add(int(raw[key]))
                    except (TypeError, ValueError):
                        pass
        amt = float(ent.amount or 0)
        try:
            if isinstance(raw, dict) and raw.get("contribution_receipt_amount") is not None:
                amt = float(raw.get("contribution_receipt_amount") or amt)
        except (TypeError, ValueError):
            pass
        total += max(0.0, amt)
    return total, max(1, len(cycles))


def _sector_display(sector: str) -> str:
    return sector.replace("_", " ")


def _vote_alignment_label(rate: float, vote_count: int) -> str:
    if vote_count <= 0:
        return "on recorded positions"
    if rate >= 0.65:
        return "predominantly YEA"
    if rate <= 0.35:
        return "predominantly NAY"
    return "with mixed YEA and NAY"


def _high_volume_donor_gaps(
    db: Session, case_id: uuid.UUID, senator_name: str
) -> list[dict[str, Any]]:
    by_donor: dict[str, list[tuple[float, EvidenceEntry]]] = defaultdict(list)
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type.in_(("financial_connection", "fec_historical")),
        )
    ).all():
        donor = ""
        raw: dict[str, Any] = {}
        try:
            parsed = json.loads(ent.raw_data_json or "{}")
            if isinstance(parsed, dict):
                raw = parsed
                donor = str(raw.get("contributor_name") or raw.get("donor_name") or "").strip()
        except json.JSONDecodeError:
            pass
        if not donor and ent.matched_name:
            donor = str(ent.matched_name).strip()
        if not donor:
            continue
        amt = float(ent.amount or 0)
        try:
            if raw.get("contribution_receipt_amount") is not None:
                amt = float(raw.get("contribution_receipt_amount") or amt)
        except (TypeError, ValueError):
            pass
        by_donor[donor].append((max(0.0, amt), ent))

    lda_issues: list[str] = []
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "lobbying_filing",
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        codes = raw.get("issue_codes") or []
        if isinstance(codes, list):
            for c in codes[:12]:
                sec = ISSUE_CODE_TO_SECTOR.get(str(c).strip().upper())
                if sec:
                    lda_issues.append(sec)
    issue_areas = ", ".join(sorted(set(lda_issues))) if lda_issues else "listed issue codes"

    out: list[dict[str, Any]] = []
    for donor, rows in by_donor.items():
        if len(rows) < 5:
            continue
        total = sum(a for a, _ in rows)
        if total < 5000:
            continue
        fec_urls = []
        seen_u: set[str] = set()
        for _, e in rows:
            u = (e.source_url or "").strip()
            if u and "fec.gov" in u.lower() and u not in seen_u:
                seen_u.add(u)
                fec_urls.append(u)
        if not fec_urls:
            continue
        sentence = TEMPLATES["high_volume_donor"].format(
            donor_entity=donor,
            total_amount=total,
            senator_name=senator_name,
            transaction_count=len(rows),
            issue_areas=issue_areas,
        )
        out.append(
            {
                "sentence": sentence,
                "type": "high_volume_donor",
                "confidence": "medium",
                "donation_amount": total,
                "donation_date": "",
                "donor_entity": donor,
                "vote_date": "",
                "vote_description": "",
                "vote_result": "",
                "days_between": 0,
                "sources": fec_urls[:5],
                "needs_human_review": total > 50_000,
            }
        )
    return out


def generate_gap_sentences(case_id: str, db: Session) -> list[dict[str, Any]]:
    """
    For a given senator case, generate plain-English gap analysis sentences
    combining FEC donor data with vote records.
    """
    try:
        cid = uuid.UUID(str(case_id).strip())
    except ValueError:
        return []

    case = db.get(CaseFile, cid)
    if not case:
        return []

    senator_name = (case.subject_name or "the official").strip() or "the official"
    gaps: list[dict[str, Any]] = []

    signals = db.scalars(
        select(Signal).where(
            Signal.case_file_id == cid,
            Signal.signal_type == "temporal_proximity",
            Signal.exposure_state != "unresolved",
        )
    ).all()

    for sig in signals:
        try:
            bd = json.loads(sig.weight_breakdown or "{}")
        except json.JSONDecodeError:
            bd = {}
        if not isinstance(bd, dict) or bd.get("kind") != "donor_cluster":
            continue

        eids = _parse_evidence_id_list(sig.evidence_ids)
        days_raw = sig.days_between
        if days_raw is None:
            continue
        days_between = abs(int(days_raw))
        if days_between > 180:
            continue

        fec_urls = _fec_urls_from_evidence_ids(db, eids)
        if not fec_urls:
            continue

        donation_amount, _ = _first_fec_amount_and_url(db, eids)
        if donation_amount <= 0:
            donation_amount = float(sig.amount or bd.get("total_amount") or 0)

        vote_ent = _find_vote_entry_for_signal(db, eids)
        if vote_ent is None or vote_ent.date_of_event is None:
            continue
        vote_result, vote_description = _vote_entry_details(vote_ent)

        donor_entity = str(bd.get("donor") or sig.actor_a or "a contributor").strip()
        donation_date = str(
            bd.get("receipt_date") or bd.get("exemplar_financial_date") or sig.event_date_a or ""
        ).strip()[:10]
        if not donation_date and sig.event_date_a:
            donation_date = str(sig.event_date_a).strip()[:10]
        vote_date = vote_ent.date_of_event.isoformat()[:10]

        sentence = TEMPLATES["donation_vote_proximity"].format(
            donor_entity=donor_entity,
            donation_amount=donation_amount,
            senator_name=senator_name,
            donation_date=donation_date,
            days_between=days_between,
            vote_result=vote_result,
            vote_description=vote_description.replace('"', "'"),
            vote_date=vote_date,
        )
        conf = _gap_confidence(days_between)
        needs_hr = donation_amount > 50_000

        gaps.append(
            {
                "sentence": sentence,
                "type": "donation_vote_proximity",
                "confidence": conf,
                "donation_amount": donation_amount,
                "donation_date": donation_date,
                "donor_entity": donor_entity,
                "vote_date": vote_date,
                "vote_description": vote_description,
                "vote_result": vote_result,
                "days_between": days_between,
                "sources": fec_urls,
                "needs_human_review": needs_hr,
            }
        )

    align = _compute_case_sector_alignment_rates(db, cid)
    total_amt, cycle_count = _case_fec_totals_and_cycles(db, cid)
    best_sector: str | None = None
    best_info: dict[str, Any] | None = None
    for sector, info in align.items():
        if info.get("vote_count", 0) < 2:
            continue
        if best_info is None or int(info.get("vote_count", 0)) > int(
            best_info.get("vote_count", 0)
        ):
            best_sector = sector
            best_info = info

    if best_sector and best_info and total_amt > 0:
        rate = float(best_info.get("alignment_rate", 0.0))
        vc = int(best_info.get("vote_count", 0))
        vote_alignment = _vote_alignment_label(rate, vc)
        sector_label = _sector_display(best_sector)
        sentence = TEMPLATES["sector_pattern"].format(
            senator_name=senator_name,
            total_amount=total_amt,
            sector=sector_label,
            cycle_count=cycle_count,
            vote_alignment=vote_alignment,
            vote_count=vc,
        )
        fec_urls_sec: list[str] = []
        seen_s: set[str] = set()
        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == cid,
                EvidenceEntry.entry_type.in_(("financial_connection", "fec_historical")),
            )
        ).all():
            u = (ent.source_url or "").strip()
            if u and "fec.gov" in u.lower() and u not in seen_s:
                seen_s.add(u)
                fec_urls_sec.append(u)
        if fec_urls_sec:
            gaps.append(
                {
                    "sentence": sentence,
                    "type": "sector_pattern",
                    "confidence": "medium",
                    "donation_amount": total_amt,
                    "donation_date": "",
                    "donor_entity": "",
                    "vote_date": "",
                    "vote_description": f"{sector_label}-related measures",
                    "vote_result": vote_alignment,
                    "days_between": 0,
                    "sources": fec_urls_sec[:10],
                    "needs_human_review": total_amt > 50_000,
                }
            )

    gaps.extend(_high_volume_donor_gaps(db, cid, senator_name))

    return gaps
