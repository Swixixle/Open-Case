"""Background Perplexity enrichment: persist signed receipts; errors are logged only."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from adapters.perplexity_enrichment import fetch_perplexity_enrichment
from database import SessionLocal
from models import CaseFile, EnrichmentReceipt, SubjectProfile
from services.enrichment_signing import claims_to_findings, sign_enrichment_receipt

logger = logging.getLogger(__name__)

BANNED_PHRASES = [
    "corrupt",
    "criminal",
    "bribed",
    "illegal activity",
    "in exchange for",
    "because of donations",
    "quid pro quo",
    "in return for",
    "led to",
    "caused by",
]


def validate_narrative(text: str) -> tuple[str, list[str]]:
    """Returns cleaned text and list of flagged phrases found."""
    flags: list[str] = []
    lower = (text or "").lower()
    for phrase in BANNED_PHRASES:
        if phrase.lower() in lower:
            flags.append(phrase)
    return text, flags


def _urls_from_stored_findings(findings_val: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(findings_val, dict):
        items = findings_val.get("items")
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                for u in it.get("sources") or []:
                    s = str(u).strip()
                    if s:
                        out.add(s)
            return out
    if isinstance(findings_val, list):
        for it in findings_val:
            if not isinstance(it, dict):
                continue
            u = str(it.get("source_url") or "").strip()
            if u:
                out.add(u)
            for u2 in it.get("sources") or []:
                s = str(u2).strip()
                if s:
                    out.add(s)
    return out


def _count_new_sources(
    findings_items: list[dict[str, Any]],
    prior_urls: set[str],
) -> int:
    n = 0
    seen: set[str] = set()
    for it in findings_items:
        if not isinstance(it, dict):
            continue
        for u in it.get("sources") or []:
            s = str(u).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            if s not in prior_urls:
                n += 1
    return n


def run_enrichment(case_id: str) -> None:
    """Run enrichment in a fresh DB session. Swallows all exceptions."""
    try:
        _run_enrichment_impl(case_id)
    except Exception:
        logger.exception("enrichment failed for case_id=%s", case_id)


def _run_enrichment_impl(case_id: str) -> None:
    cid = uuid.UUID(str(case_id))
    db = SessionLocal()
    try:
        case = db.scalar(select(CaseFile).where(CaseFile.id == cid))
        if not case:
            logger.warning("enrichment skipped: case not found %s", case_id)
            return

        prof = db.scalar(select(SubjectProfile).where(SubjectProfile.case_file_id == cid))
        subject_name = (case.subject_name or "").strip() or (
            (prof.subject_name or "").strip() if prof else ""
        )
        bioguide = (prof.bioguide_id if prof else None) or None
        bioguide = (str(bioguide).strip() or None) if bioguide else None

        bundle = fetch_perplexity_enrichment(
            subject_name,
            bioguide_id=bioguide,
        )
        phase_claims = bundle.get("phase_1_claims") or []
        if not isinstance(phase_claims, list):
            phase_claims = []
        narrative = str(bundle.get("narrative") or "")
        _, banned_flags = validate_narrative(narrative)
        if banned_flags:
            logger.warning(
                "Enrichment narrative contained banned phrases (human review required): %s",
                banned_flags,
            )

        items = claims_to_findings([c for c in phase_claims if isinstance(c, dict)])
        needs_human_review = (
            any(bool(i.get("needs_human_review")) for i in items) or bool(banned_flags)
        )

        prior = db.scalar(
            select(EnrichmentReceipt)
            .where(EnrichmentReceipt.case_file_id == cid)
            .order_by(EnrichmentReceipt.queried_at.desc())
            .limit(1)
        )
        prior_urls = _urls_from_stored_findings(
            prior.findings if prior and prior.findings is not None else None
        )
        new_count = _count_new_sources(items, prior_urls)
        is_delta = new_count > 0

        queried_at = datetime.now(timezone.utc)
        signed = sign_enrichment_receipt(
            subject_name,
            bioguide,
            queried_at,
            items,
            new_count,
            narrative=narrative,
            needs_human_review=needs_human_review,
        )

        stored_findings: dict[str, Any] = {
            "items": items,
            "narrative": narrative,
            "needs_human_review": needs_human_review,
            "narrative_validation_flags": banned_flags,
            "query_errors": bundle.get("query_errors") or [],
            "retrieved_at": bundle.get("retrieved_at"),
        }

        row = EnrichmentReceipt(
            case_file_id=cid,
            subject_name=subject_name or case.subject_name,
            bioguide_id=bioguide,
            queried_at=queried_at,
            findings=stored_findings,
            new_findings_count=new_count,
            is_delta=is_delta,
            signed_receipt=signed,
        )
        db.add(row)
        case.last_enriched_at = queried_at
        db.add(case)
        db.commit()

        logger.info(
            "enrichment stored case_id=%s items=%s new_sources=%s delta=%s needs_review=%s",
            case_id,
            len(items),
            new_count,
            is_delta,
            needs_human_review,
        )
    finally:
        db.close()


def enqueue_stale_enrichment(
    db: Session | None = None,
    *,
    max_cases: int = 20,
    stale_after_hours: int = 23,
) -> None:
    """
    Used by the scheduler: refresh cases with no recent enrichment.
    Opens its own session if `db` is None.
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    assert db is not None
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)
        q = (
            select(CaseFile)
            .where(
                or_(
                    CaseFile.last_enriched_at.is_(None),
                    CaseFile.last_enriched_at < cutoff,
                )
            )
            .order_by(CaseFile.last_enriched_at.asc().nulls_first())
            .limit(max_cases)
        )
        cases = list(db.scalars(q).all())
        for c in cases:
            run_enrichment(str(c.id))
    finally:
        if owns_session:
            db.close()
