"""AI narrative synthesis for investigation summaries."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from auth import require_api_key
from database import get_db
from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
from models import CaseFile, CaseNarrative, EvidenceEntry, Investigator, Signal
from services.perplexity_router import run_phase2_narrative
from signing import pack_signed_hash, sign_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cases", tags=["narrative"])


def _as_utc_for_timedelta(dt: datetime) -> datetime:
    """SQLite may return timezone-naive datetimes; normalize before mixing with aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

NARRATIVE_SYSTEM_PROMPT = """You are a careful analyst of U.S. public campaign-finance, lobbying, and roll-call data for Open Case. \
Follow the "receipts, not verdicts" philosophy. Output clear prose only (no JSON, no markdown code fences)."""


def _generate_id() -> str:
    return hashlib.sha256(
        f"narrative_{uuid.uuid4().hex}_{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:64]


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:64]


def _build_narrative_prompt(
    case_file: CaseFile,
    evidence: list[EvidenceEntry],
    signals: list[Signal],
    pattern_rows: list[dict[str, Any]],
) -> str:
    subject_info = {
        "name": case_file.subject_name,
        "subject_type": case_file.subject_type,
        "jurisdiction": case_file.jurisdiction,
        "government_level": case_file.government_level,
        "branch": case_file.branch,
    }

    alert_data = []
    for row in pattern_rows[:12]:
        alert_data.append(
            {
                "rule_id": row.get("rule_id"),
                "summary": row.get("rule_line"),
                "donor_entity": row.get("donor_entity"),
                "committee": row.get("committee"),
                "window_phrase": row.get("window_phrase"),
                "score": row.get("score"),
            }
        )

    signal_data = []
    for signal in signals[:25]:
        signal_data.append(
            {
                "type": signal.signal_type,
                "actor_a": signal.actor_a,
                "actor_b": signal.actor_b,
                "weight": signal.weight,
                "description": (signal.description or "")[:4000],
                "days_between": signal.days_between,
                "amount": signal.amount,
            }
        )

    evidence_data = []
    for entry in evidence[:40]:
        evidence_data.append(
            {
                "type": entry.entry_type,
                "title": entry.title,
                "source": entry.source_name,
                "date": entry.date_of_event.isoformat() if entry.date_of_event else None,
                "amount": entry.amount,
                "confidence": entry.confidence,
            }
        )

    return f"""You are analyzing a public official's record. Write a 3-5 paragraph investigative summary that:
- Highlights temporal patterns between donations and votes (when signals support it)
- Notes unusual donation or lobbying timing reflected in pattern rules below
- Presents facts neutrally without asserting corruption
- Uses phrases like "records show," "public filings indicate," "proximity analysis detected"
- Cites specific dates, amounts, and legislative context where present in the data

SUBJECT INFORMATION:
{json.dumps(subject_info, indent=2)}

PATTERN ALERTS ({len(pattern_rows)} total, sample for this case):
{json.dumps(alert_data, indent=2)}

SIGNALS ({len(signals)} total, sample by weight):
{json.dumps(signal_data, indent=2)}

EVIDENCE ENTRIES ({len(evidence)} total, sample):
{json.dumps(evidence_data, indent=2)}

Write a factual, journalistic summary. Do not conclude corruption or wrongdoing: describe what the records show."""


def _fallback_narrative(
    case_file: CaseFile,
    evidence_count: int,
    signal_count: int,
    alert_count: int,
) -> str:
    narrative = f"""Investigation Summary for {case_file.subject_name}

This case file contains {evidence_count} evidence entries, {signal_count} signals, and {alert_count} pattern alerts.

"""
    if alert_count > 0:
        narrative += f"""Pattern analysis has detected {alert_count} potential concern(s) requiring review. These patterns represent temporal or financial features in public records, not allegations of wrongdoing.

"""
    narrative += """All findings are based on publicly available records. The system documents proximity and timing; it does not render verdicts on intent or legality.

Review the detailed timeline and evidence tabs for source documentation."""
    return narrative


def _model_used_from_trail(trail: list[str]) -> str:
    if not trail:
        return "fallback-template"
    first = trail[0]
    if first == "claude":
        return (os.environ.get("CLAUDE_MODEL") or "claude-3-sonnet-20240229").strip()
    if first == "perplexity_sonar":
        return "perplexity-sonar"
    if first == "gemini":
        return (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    return first


@router.post("/{case_id}/synthesize-narrative")
def synthesize_narrative(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    _auth: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    """Generate an AI narrative summary for a case (Claude -> Perplexity Sonar -> Gemini, then template)."""

    case_file = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case_file:
        raise HTTPException(status_code=404, detail="Case not found")

    existing = db.scalar(
        select(CaseNarrative)
        .where(CaseNarrative.case_file_id == case_id)
        .order_by(desc(CaseNarrative.generated_at))
        .limit(1)
    )

    if existing:
        existing_dt = _as_utc_for_timedelta(existing.generated_at)
        age_hours = (datetime.now(timezone.utc) - existing_dt).total_seconds() / 3600
        if age_hours < 1:
            return {
                "case_id": str(case_id),
                "narrative": existing.narrative_text,
                "model_used": existing.model_used,
                "generated_at": existing.generated_at.isoformat(),
                "cached": True,
            }

    evidence = list(
        db.scalars(
            select(EvidenceEntry)
            .where(EvidenceEntry.case_file_id == case_id)
            .order_by(desc(EvidenceEntry.entered_at))
        ).all()
    )

    signals = list(
        db.scalars(
            select(Signal)
            .where(Signal.case_file_id == case_id)
            .order_by(desc(Signal.weight))
        ).all()
    )

    pal = run_pattern_engine(db)
    pattern_rows = pattern_alerts_for_case(case_id, pal, include_unreviewed=True)

    user_prompt = _build_narrative_prompt(case_file, evidence, signals, pattern_rows)
    prompt_hash = _hash_prompt(user_prompt)

    perplexity_key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    narrative_text, trail = run_phase2_narrative(
        NARRATIVE_SYSTEM_PROMPT,
        user_prompt,
        perplexity_api_key=perplexity_key,
        timeout=180.0,
    )
    if narrative_text:
        model_used = _model_used_from_trail(trail)
    else:
        narrative_text = _fallback_narrative(
            case_file, len(evidence), len(signals), len(pattern_rows)
        )
        model_used = "fallback-template"
        trail = []
        logger.info("Narrative fallback template for case %s", case_id)

    now = datetime.now(timezone.utc)
    sem = {
        "kind": "case_narrative",
        "case_id": str(case_id),
        "narrative_sha256": hashlib.sha256(narrative_text.encode("utf-8")).hexdigest(),
        "model_used": model_used,
        "prompt_hash": prompt_hash,
        "generated_at": now.isoformat(),
    }
    sp = sign_payload(sem)
    signature_stored = pack_signed_hash(
        str(sp.get("content_hash", "")),
        str(sp.get("signature", "")),
        payload=sem,
    )

    narrative_id = _generate_id()
    narrative_record = CaseNarrative(
        id=narrative_id,
        case_file_id=case_id,
        narrative_text=narrative_text,
        model_used=model_used,
        generated_at=now,
        signature=signature_stored,
        prompt_hash=prompt_hash,
        token_count=None,
    )

    db.add(narrative_record)
    db.commit()
    db.refresh(narrative_record)

    return {
        "case_id": str(case_id),
        "narrative_id": narrative_id,
        "narrative": narrative_text,
        "model_used": model_used,
        "model_trail": trail,
        "generated_at": narrative_record.generated_at.isoformat(),
        "signature": signature_stored[:200] + "…" if len(signature_stored) > 200 else signature_stored,
        "cached": False,
    }


@router.get("/{case_id}/narrative")
def get_narrative(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get the most recent stored narrative for a case."""

    narrative = db.scalar(
        select(CaseNarrative)
        .where(CaseNarrative.case_file_id == case_id)
        .order_by(desc(CaseNarrative.generated_at))
        .limit(1)
    )

    if not narrative:
        raise HTTPException(
            status_code=404,
            detail="No narrative found. Generate one with POST /api/v1/cases/{id}/synthesize-narrative",
        )

    sig = narrative.signature
    return {
        "case_id": str(case_id),
        "narrative_id": narrative.id,
        "narrative": narrative.narrative_text,
        "model_used": narrative.model_used,
        "generated_at": narrative.generated_at.isoformat(),
        "signature": sig[:200] + "…" if len(sig) > 200 else sig,
        "token_count": narrative.token_count,
    }
