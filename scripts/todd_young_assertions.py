"""
Four-category diagnostics for the Todd Young gate (Phase 4 Step 1A).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models import EvidenceEntry, Signal


def run_assertions(case_id: uuid.UUID, db: Session) -> tuple[bool, dict[str, Any]]:
    """
    Run all assertions. Return (passed, diagnostic_dict).
    diagnostic_dict is always emitted to stdout for debugging.
    """
    diagnostics: dict[str, Any] = {}

    fec_evidence = db.scalar(
        select(func.count())
        .select_from(EvidenceEntry)
        .where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.source_name == "FEC",
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.is_absence.is_(False),
        )
    ) or 0

    vote_evidence = db.scalar(
        select(func.count())
        .select_from(EvidenceEntry)
        .where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "vote_record",
            EvidenceEntry.is_absence.is_(False),
        )
    ) or 0

    parse_stmt = (
        select(EvidenceEntry)
        .where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "gap_documented",
            or_(
                EvidenceEntry.title.ilike("%parse warning%"),
                EvidenceEntry.body.ilike("%parse warning%"),
                EvidenceEntry.body.ilike("%parse_warning%"),
            ),
        )
    )
    parse_warnings = db.scalars(parse_stmt).all()

    diagnostics["fec_evidence_count"] = fec_evidence
    diagnostics["vote_evidence_count"] = vote_evidence
    diagnostics["parse_warnings"] = [e.body[:200] for e in parse_warnings]

    cat1_pass = fec_evidence > 0 and vote_evidence > 0

    proximity_signals = db.scalars(
        select(Signal).where(
            Signal.case_file_id == case_id,
            Signal.signal_type == "temporal_proximity",
        )
    ).all()

    diagnostics["proximity_signal_count"] = len(proximity_signals)
    cat2_pass = len(proximity_signals) > 0

    cat3_pass = False
    if proximity_signals:
        top_signal = max(proximity_signals, key=lambda s: s.weight)
        try:
            raw_ids = json.loads(top_signal.evidence_ids or "[]")
        except json.JSONDecodeError:
            raw_ids = []
        uuids: list[uuid.UUID] = []
        for x in raw_ids:
            try:
                uuids.append(uuid.UUID(str(x)))
            except ValueError:
                continue
        signal_evidence = []
        if uuids:
            signal_evidence = db.scalars(
                select(EvidenceEntry).where(EvidenceEntry.id.in_(uuids))
            ).all()

        types_present = {e.entry_type for e in signal_evidence}
        sources_present = {e.source_name for e in signal_evidence}

        has_fec = any(
            e.source_name == "FEC" and e.entry_type == "financial_connection"
            for e in signal_evidence
        )
        has_vote = any(e.entry_type == "vote_record" for e in signal_evidence)

        diagnostics["top_signal_weight"] = top_signal.weight
        diagnostics["top_signal_evidence_types"] = list(types_present)
        diagnostics["top_signal_evidence_sources"] = list(sources_present)
        diagnostics["has_fec_in_signal"] = has_fec
        diagnostics["has_vote_in_signal"] = has_vote
        diagnostics["days_between"] = top_signal.days_between
        diagnostics["amount"] = top_signal.amount

        cat3_pass = has_fec and has_vote

    cat4_pass = False
    if proximity_signals:
        top = max(proximity_signals, key=lambda s: s.weight)
        has_summary = bool(top.proximity_summary or top.weight_explanation)
        explanation = top.weight_explanation or ""
        diagnostics["weight_explanation_preview"] = explanation[:300]
        diagnostics["proximity_summary"] = top.proximity_summary
        cat4_pass = top.weight > 0 and has_summary

    print("\n" + "=" * 60)
    print("TODD YOUNG TEST DIAGNOSTICS")
    print("=" * 60)
    print(json.dumps(diagnostics, indent=2, default=str))
    print("=" * 60)

    print(f"\nCategory 1 (Data path alive): {'PASS' if cat1_pass else 'FAIL'}")
    print(f"  FEC evidence: {fec_evidence}")
    print(f"  Vote evidence: {vote_evidence}")
    if parse_warnings:
        print(f"  WARNING: {len(parse_warnings)} parse-warning gap entries")

    print(f"\nCategory 2 (Signal exists): {'PASS' if cat2_pass else 'FAIL'}")
    print(f"  Proximity signals: {len(proximity_signals)}")

    print(f"\nCategory 3 (Evidence intersection): {'PASS' if cat3_pass else 'FAIL'}")
    if proximity_signals:
        print(f"  Has FEC in signal: {diagnostics.get('has_fec_in_signal')}")
        print(f"  Has vote in signal: {diagnostics.get('has_vote_in_signal')}")

    print(f"\nCategory 4 (Readable narrative): {'PASS' if cat4_pass else 'FAIL'}")

    all_pass = cat1_pass and cat2_pass and cat3_pass and cat4_pass
    return all_pass, diagnostics
