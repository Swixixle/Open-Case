"""
Four-category diagnostics for the Todd Young gate (Phase 4/5).
Category 3 uses type sets for forward compatibility (entry_type name drift).
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models import EvidenceEntry, Signal

# Forward-compatible with normalized entry_type names (Phase 5).
FINANCIAL_TYPES = frozenset({"financial_connection"})
DECISION_TYPES = frozenset({"vote_record", "decision_event", "congressional_vote"})


def run_assertions(
    case_id: uuid.UUID, db: Session
) -> tuple[bool, dict[str, Any], dict[int, str]]:
    """
    Run all assertions. Return (passed, diagnostic_dict, category_results).

    category_results maps 1..4 to \"PASS\" or \"FAIL\" for closure artifacts.
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

        signal_evidence: list[EvidenceEntry] = []
        if uuids:
            signal_evidence = db.scalars(
                select(EvidenceEntry).where(EvidenceEntry.id.in_(uuids))
            ).all()

        print("\n--- Category 3 Evidence Debug ---")
        print(f"Signal ID: {top_signal.id}")
        print(f"Evidence IDs: {raw_ids}")
        print(f"Evidence entries found in DB: {len(signal_evidence)}")
        for e in signal_evidence:
            tid = str(e.id)[:8]
            tit = (e.title or "")[:60]
            print(
                f"  entry_id={tid}…  source_name={e.source_name!r}  "
                f"entry_type={e.entry_type!r}  title={tit!r}"
            )

        by_adapter: dict[str, list[EvidenceEntry]] = defaultdict(list)
        for e in signal_evidence:
            key = (e.adapter_name or e.source_name or "(unknown)").strip() or "(unknown)"
            by_adapter[key].append(e)
        print("\n--- Category 3 by adapter/source (failure triage) ---")
        for ad_key in sorted(by_adapter.keys()):
            rows = by_adapter[ad_key]
            types = sorted({r.entry_type for r in rows})
            print(f"  [{ad_key}] n={len(rows)}  entry_types={types}")

        print("\n--- Category 3 Assertion ---")
        print(
            "REQUIRE: at least one entry_type in FINANCIAL_TYPES "
            "AND at least one entry_type in DECISION_TYPES"
        )
        print(f"FINANCIAL_TYPES = {sorted(FINANCIAL_TYPES)}")
        print(f"DECISION_TYPES = {sorted(DECISION_TYPES)}")

        has_financial = any(e.entry_type in FINANCIAL_TYPES for e in signal_evidence)
        has_decision = any(e.entry_type in DECISION_TYPES for e in signal_evidence)

        print(f"Has financial entry: {has_financial}")
        print(f"Has decision entry: {has_decision}")
        print("-------------------------------\n")

        types_present = {e.entry_type for e in signal_evidence}
        sources_present = {e.source_name for e in signal_evidence}

        diagnostics["top_signal_weight"] = top_signal.weight
        diagnostics["top_signal_evidence_types"] = list(types_present)
        diagnostics["top_signal_evidence_sources"] = list(sources_present)
        diagnostics["has_financial_type_in_signal"] = has_financial
        diagnostics["has_decision_type_in_signal"] = has_decision
        diagnostics["days_between"] = top_signal.days_between
        diagnostics["amount"] = top_signal.amount

        cat3_pass = has_financial and has_decision

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
        print(
            f"  Has financial-type in signal: {diagnostics.get('has_financial_type_in_signal')}"
        )
        print(
            f"  Has decision-type in signal: {diagnostics.get('has_decision_type_in_signal')}"
        )

    print(f"\nCategory 4 (Readable narrative): {'PASS' if cat4_pass else 'FAIL'}")

    all_pass = cat1_pass and cat2_pass and cat3_pass and cat4_pass
    if all_pass:
        print("\nRESULT: PASS")

    category_results: dict[int, str] = {
        1: "PASS" if cat1_pass else "FAIL",
        2: "PASS" if cat2_pass else "FAIL",
        3: "PASS" if cat3_pass else "FAIL",
        4: "PASS" if cat4_pass else "FAIL",
    }

    return all_pass, diagnostics, category_results
