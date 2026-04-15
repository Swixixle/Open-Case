"""
Post-investigation verification logging for public-official runs (FEC depth, votes, patterns).

Structured logs support production checks (e.g. Sanders Schedule A depth, Crapo auto-ingest).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
from models import EvidenceEntry

logger = logging.getLogger(__name__)


def log_post_investigate_evidence_snapshot(
    db: Session,
    *,
    case_id: UUID,
    bioguide_id: str | None,
    subject_name: str,
) -> None:
    """After a successful investigate commit: counts for QA without failing the request."""
    fc = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceEntry)
            .where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "financial_connection",
                EvidenceEntry.source_name == "FEC",
            )
        )
        or 0
    )
    hist = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceEntry)
            .where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "fec_historical",
            )
        )
        or 0
    )
    votes = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceEntry)
            .where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "vote_record",
            )
        )
        or 0
    )
    amendments = int(
        db.scalar(
            select(func.count())
            .select_from(EvidenceEntry)
            .where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "amendment_vote",
            )
        )
        or 0
    )
    try:
        alerts = run_pattern_engine(db)
        case_alerts = pattern_alerts_for_case(case_id, alerts)
        rule_ids = sorted({a.get("rule_id", "") for a in case_alerts if a.get("rule_id")})
    except Exception:
        logger.exception(
            "post_investigate_verify pattern_engine failed case_id=%s", case_id
        )
        case_alerts = []
        rule_ids = []

    logger.info(
        "post_investigate_verify case_id=%s bioguide_id=%s subject=%r "
        "fec_financial_connection_rows=%s fec_historical_rows=%s vote_record_rows=%s "
        "amendment_vote_rows=%s pattern_alerts_for_case=%s pattern_rule_ids=%s",
        case_id,
        (bioguide_id or "").strip() or None,
        (subject_name or "").strip()[:200],
        fc,
        hist,
        votes,
        amendments,
        len(case_alerts),
        rule_ids,
    )
    if fc == 0 and hist == 0:
        logger.warning(
            "post_investigate_verify: zero FEC financial rows for case_id=%s bioguide_id=%s "
            "(Schedule A may be empty, blocked, or wrong committee — check source_statuses)",
            case_id,
            (bioguide_id or "").strip() or None,
        )
    if not case_alerts:
        logger.warning(
            "post_investigate_verify: zero pattern alerts for case_id=%s bioguide_id=%s",
            case_id,
            (bioguide_id or "").strip() or None,
        )
