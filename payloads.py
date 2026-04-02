"""Deterministic JSON-shaped dicts for JCS signing (case + evidence)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from models import CaseFile, EvidenceEntry


def _dt_iso(v: Any) -> str | None:
    """UTC `YYYY-MM-DDTHH:MM:SSZ` so SQLite round-trips match what was signed."""
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        v = v.astimezone(timezone.utc)
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def case_semantic_dict(c: CaseFile) -> dict[str, Any]:
    """Fields that define the case record; excludes counters, signing metadata."""
    base: dict[str, Any] = {
        "id": str(c.id),
        "slug": c.slug,
        "title": c.title,
        "subject_name": c.subject_name,
        "subject_type": c.subject_type,
        "jurisdiction": c.jurisdiction,
        "status": c.status,
        "created_at": _dt_iso(c.created_at),
        "created_by": c.created_by,
        "summary": c.summary,
        "pickup_note": c.pickup_note or "",
        "is_public": c.is_public,
    }
    lss = getattr(c, "last_source_statuses", None)
    if lss:
        base["last_source_statuses"] = lss
    return base


def evidence_semantic_dict(e: EvidenceEntry) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(e.id),
        "case_file_id": str(e.case_file_id),
        "entry_type": e.entry_type,
        "title": e.title,
        "body": e.body,
        "source_url": e.source_url or "",
        "source_name": e.source_name or "",
        "date_of_event": e.date_of_event.isoformat() if e.date_of_event else None,
        "entered_at": _dt_iso(e.entered_at),
        "entered_by": e.entered_by,
        "confidence": e.confidence,
        "is_absence": e.is_absence,
        "flagged_for_review": e.flagged_for_review,
    }
    if e.amount is not None:
        d["amount"] = e.amount
    if e.matched_name:
        d["matched_name"] = e.matched_name
    if getattr(e, "evidence_hash", None):
        d["evidence_hash"] = e.evidence_hash
    if getattr(e, "disambiguation_note", None):
        d["disambiguation_note"] = e.disambiguation_note
    if getattr(e, "disambiguation_by", None):
        d["disambiguation_by"] = e.disambiguation_by
    if getattr(e, "disambiguation_at", None) and e.disambiguation_at:
        d["disambiguation_at"] = _dt_iso(e.disambiguation_at)
    if getattr(e, "jurisdictional_match", None) is not None:
        d["jurisdictional_match"] = bool(e.jurisdictional_match)
    mc = getattr(e, "matched_committees", None)
    if mc:
        d["matched_committees"] = mc
    return d


def sign_evidence_entry(entry: EvidenceEntry) -> None:
    from signing import pack_signed_hash, sign_payload

    sem = evidence_semantic_dict(entry)
    signed = sign_payload(sem)
    entry.signed_hash = pack_signed_hash(signed["content_hash"], signed["signature"])


def full_case_signing_payload(case: CaseFile, entries: list[EvidenceEntry]) -> dict[str, Any]:
    ordered = sorted(entries, key=lambda x: str(x.id))
    return {
        "schema_version": "open-case-full-1",
        "case": case_semantic_dict(case),
        "evidence": [evidence_semantic_dict(x) for x in ordered],
    }


def apply_case_file_signature(case: CaseFile, entries: list[EvidenceEntry]) -> None:
    """Update case.signed_hash / last_signed_at from full case + evidence canonical payload."""
    from signing import pack_signed_hash, sign_payload

    payload = full_case_signing_payload(case, entries)
    signed = sign_payload(payload)
    case.signed_hash = pack_signed_hash(signed["content_hash"], signed["signature"])
    case.last_signed_at = datetime.now(timezone.utc)
