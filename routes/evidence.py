from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from database import get_db
from models import CaseContributor, CaseFile, EvidenceEntry, Investigator
from payloads import (
    apply_case_file_signature,
    evidence_semantic_dict,
    sign_evidence_entry,
    verify_case_file_seal,
)
from scoring import add_credibility
from services.evidence_epistemic import apply_epistemic_metadata_to_entry
from services.finding_audit import log_finding_audit
from services.finding_policy import (
    build_rendered_claim_text,
    finalize_finding_after_sign,
    infer_source_type,
    valid_http_url,
)
from services.epistemic_classifier import CONTEXTUAL

ENTRY_TYPES = frozenset(
    {
        "financial_connection",
        "fec_disbursement",
        "lobbying_filing",
        "vote_record",
        "property_record",
        "court_record",
        "disclosure",
        "timeline_event",
        "gap_documented",
        "photo_tap",
        "foia_response",
        "investigator_note",
        "handoff_note",
    }
)
CONFIDENCE = frozenset({"confirmed", "probable", "unverified"})


class EvidenceCreate(BaseModel):
    entry_type: str
    title: str
    body: str
    source_url: str = ""
    source_name: str = ""
    date_of_event: str | None = None  # ISO date YYYY-MM-DD
    entered_by: str
    confidence: str
    is_absence: bool = False
    flagged_for_review: bool = False


def _parse_event_date(s: str | None):
    if not s:
        return None
    from datetime import date as date_cls

    return date_cls.fromisoformat(s[:10])


def _evidence_passes_public_surface(
    e: EvidenceEntry, *, admin_journalist_surface: bool
) -> bool:
    from services.finding_policy import compute_is_publicly_renderable

    if admin_journalist_surface:
        return True
    if getattr(e, "requires_human_review", False):
        return False
    if (getattr(e, "epistemic_level", None) or "").strip().upper() == CONTEXTUAL:
        return False
    return compute_is_publicly_renderable(e)


def _apply_evidence_query_filters(
    entries: list[EvidenceEntry],
    *,
    epistemic_level: str | None = None,
    review_status: str | None = None,
    source_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    court: str | None = None,
    case_number: str | None = None,
    has_direct_source: bool | None = None,
    is_publicly_renderable: bool | None = None,
) -> list[EvidenceEntry]:
    out = list(entries)
    if epistemic_level:
        want = epistemic_level.strip().upper()
        out = [e for e in out if (e.epistemic_level or "").strip().upper() == want]
    if review_status:
        rs = review_status.strip().lower()
        out = [e for e in out if (getattr(e, "review_status", "pending") or "").strip().lower() == rs]
    if source_type:
        st = source_type.strip().lower()
        out = [e for e in out if (getattr(e, "source_type", "") or "").strip().lower() == st]
    if court:
        c = court.strip().lower()
        out = [e for e in out if c in (getattr(e, "court", None) or "").lower()]
    if case_number:
        cn = case_number.strip().lower()
        out = [e for e in out if cn in (getattr(e, "case_number", None) or "").lower()]
    if has_direct_source is True:
        out = [e for e in out if valid_http_url(getattr(e, "source_url", None))]
    if is_publicly_renderable is True:
        from services.finding_policy import compute_is_publicly_renderable

        out = [e for e in out if compute_is_publicly_renderable(e)]
    if date_from or date_to:
        from datetime import date as date_cls

        df = None
        dt = None
        try:
            if date_from:
                df = date_cls.fromisoformat(date_from[:10])
            if date_to:
                dt = date_cls.fromisoformat(date_to[:10])
        except ValueError:
            df, dt = None, None

        def _in_range(ev: EvidenceEntry) -> bool:
            sd = getattr(ev, "source_date", None) or ev.date_of_event
            if sd is None:
                return False
            if df and sd < df:
                return False
            if dt and sd > dt:
                return False
            return True

        if df or dt:
            out = [e for e in out if _in_range(e)]
    return out


def case_to_response(
    case: CaseFile,
    *,
    include_unreviewed: bool = False,
    admin_authorized: bool = False,
    epistemic_level: str | None = None,
    review_status: str | None = None,
    source_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    court: str | None = None,
    case_number: str | None = None,
    has_direct_source: bool | None = None,
    is_publicly_renderable: bool | None = None,
) -> dict[str, Any]:
    entries = sorted(case.evidence_entries, key=lambda e: str(e.id))
    admin_surface = bool(include_unreviewed and admin_authorized)
    entries = [e for e in entries if _evidence_passes_public_surface(e, admin_journalist_surface=admin_surface)]
    entries = _apply_evidence_query_filters(
        entries,
        epistemic_level=epistemic_level,
        review_status=review_status,
        source_type=source_type,
        date_from=date_from,
        date_to=date_to,
        court=court,
        case_number=case_number,
        has_direct_source=has_direct_source,
        is_publicly_renderable=is_publicly_renderable,
    )
    return {
        "id": str(case.id),
        "slug": case.slug,
        "title": case.title,
        "subject_name": case.subject_name,
        "subject_type": case.subject_type,
        "jurisdiction": case.jurisdiction,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "created_by": case.created_by,
        "summary": case.summary,
        "pickup_note": case.pickup_note or "",
        "signed_hash": case.signed_hash,
        "last_signed_at": case.last_signed_at.isoformat() if case.last_signed_at else None,
        "view_count": case.view_count,
        "is_public": case.is_public,
        "evidence_entries": [evidence_to_response(e) for e in entries],
    }


def evidence_to_response(e: EvidenceEntry) -> dict[str, Any]:
    st = getattr(e, "source_type", None) or infer_source_type(
        source_url=e.source_url or "",
        source_name=e.source_name or "",
        entry_type=e.entry_type or "",
        adapter_name=getattr(e, "adapter_name", None),
    )
    claim = (getattr(e, "claim_text", None) or e.body or "").strip()
    pub = (getattr(e, "source_publisher", None) or e.source_name or "").strip()
    rendered = build_rendered_claim_text(
        epistemic_level=getattr(e, "epistemic_level", "REPORTED") or "REPORTED",
        claim_text=claim,
        source_publisher=pub,
        source_type=st,
        document_type_label=(e.entry_type or "").replace("_", " "),
    )
    return {
        "finding_id": str(e.id),
        "id": str(e.id),
        "subject_id": str(e.subject_id) if getattr(e, "subject_id", None) else None,
        "case_file_id": str(e.case_file_id),
        "entry_type": e.entry_type,
        "title": e.title,
        "body": e.body,
        "source_url": e.source_url,
        "source_type": st,
        "source_title": getattr(e, "source_title", None) or e.title,
        "source_publisher": getattr(e, "source_publisher", None) or e.source_name,
        "source_date": e.source_date.isoformat()
        if getattr(e, "source_date", None)
        else (e.date_of_event.isoformat() if e.date_of_event else None),
        "date_discovered": e.date_discovered.isoformat()
        if getattr(e, "date_discovered", None)
        else None,
        "claim_text": getattr(e, "claim_text", None) or e.body,
        "claim_summary": getattr(e, "claim_summary", None) or "",
        "claim_status": getattr(e, "claim_status", "active"),
        "source_name": e.source_name,
        "date_of_event": e.date_of_event.isoformat() if e.date_of_event else None,
        "entered_at": e.entered_at.isoformat() if e.entered_at else None,
        "entered_by": e.entered_by,
        "signed_hash": e.signed_hash,
        "confidence": e.confidence,
        "is_absence": e.is_absence,
        "flagged_for_review": e.flagged_for_review,
        "amount": e.amount,
        "matched_name": e.matched_name,
        "epistemic_level": getattr(e, "epistemic_level", "REPORTED"),
        "requires_human_review": bool(getattr(e, "requires_human_review", False)),
        "review_status": getattr(e, "review_status", "pending"),
        "review_notes": getattr(e, "review_notes", "") or "",
        "is_publicly_renderable": bool(getattr(e, "is_publicly_renderable", False)),
        "display_label": getattr(e, "display_label", "") or "",
        "source_excerpt": getattr(e, "source_excerpt", "") or "",
        "source_hash": getattr(e, "source_hash", "") or "",
        "linked_entities": getattr(e, "linked_entities_json", None) or "[]",
        "jurisdiction": getattr(e, "jurisdiction", "") or "",
        "case_number": getattr(e, "case_number", None),
        "court": getattr(e, "court", None),
        "ingest_method": getattr(e, "ingest_method", None),
        "receipt_id": getattr(e, "receipt_id", "") or "",
        "classification_basis": getattr(e, "classification_basis", "") or "",
        "corroboration_count": int(getattr(e, "corroboration_count", 0) or 0),
        "contradiction_count": int(getattr(e, "contradiction_count", 0) or 0),
        "rendered_claim_text": rendered,
    }


def case_detail_response(
    db: Session,
    case: CaseFile,
    *,
    include_unreviewed: bool = False,
    admin_authorized: bool = False,
    epistemic_level: str | None = None,
    review_status: str | None = None,
    source_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    court: str | None = None,
    case_number: str | None = None,
    has_direct_source: bool | None = None,
    is_publicly_renderable: bool | None = None,
) -> dict[str, Any]:
    """Re-load evidence for fresh list after commits."""
    c = db.scalar(
        select(CaseFile)
        .options(selectinload(CaseFile.evidence_entries))
        .where(CaseFile.id == case.id)
    )
    if not c:
        raise HTTPException(404, detail="case not found")
    out = case_to_response(
        c,
        include_unreviewed=include_unreviewed,
        admin_authorized=admin_authorized,
        epistemic_level=epistemic_level,
        review_status=review_status,
        source_type=source_type,
        date_from=date_from,
        date_to=date_to,
        court=court,
        case_number=case_number,
        has_direct_source=has_direct_source,
        is_publicly_renderable=is_publicly_renderable,
    )
    out["signature_check"] = verify_case_file_seal(c, list(c.evidence_entries), db)
    return out


def attach_evidence_routes(router: APIRouter) -> None:
    @router.post("/{case_id}/evidence")
    def add_evidence(
        case_id: uuid.UUID,
        body: EvidenceCreate,
        db: Session = Depends(get_db),
        auth_inv: Investigator = Depends(require_api_key),
    ):
        require_matching_handle(auth_inv, body.entered_by)
        if body.entry_type not in ENTRY_TYPES:
            raise HTTPException(400, detail=f"entry_type must be one of {sorted(ENTRY_TYPES)}")
        if body.confidence not in CONFIDENCE:
            raise HTTPException(400, detail=f"confidence must be one of {sorted(CONFIDENCE)}")

        case = db.scalar(
            select(CaseFile)
            .options(selectinload(CaseFile.evidence_entries))
            .where(CaseFile.id == case_id)
        )
        if not case:
            raise HTTPException(404, detail="case not found")

        try:
            d = _parse_event_date(body.date_of_event)
        except ValueError:
            raise HTTPException(400, detail="date_of_event must be ISO YYYY-MM-DD") from None

        if not body.is_absence and not valid_http_url(body.source_url):
            raise HTTPException(
                400,
                detail="source_url must be a valid http(s) URL for sourced findings (or set is_absence).",
            )

        inv = db.scalar(select(Investigator).where(Investigator.handle == body.entered_by))
        if not inv:
            inv = Investigator(handle=body.entered_by, public_key="")
            db.add(inv)
            db.flush()
        inv.entries_contributed = (inv.entries_contributed or 0) + 1

        cc = db.scalar(
            select(CaseContributor).where(
                CaseContributor.case_file_id == case.id,
                CaseContributor.investigator_handle == body.entered_by,
            )
        )
        if cc:
            cc.entry_count = (cc.entry_count or 0) + 1
            cc.last_active_at = datetime.now(timezone.utc)
        else:
            db.add(
                CaseContributor(
                    case_file_id=case.id,
                    investigator_handle=body.entered_by,
                    role="field",
                    entry_count=1,
                )
            )

        entry = EvidenceEntry(
            case_file_id=case.id,
            entry_type=body.entry_type,
            title=body.title,
            body=body.body,
            source_url=body.source_url,
            source_name=body.source_name,
            date_of_event=d,
            entered_by=body.entered_by,
            confidence=body.confidence,
            is_absence=body.is_absence,
            flagged_for_review=body.flagged_for_review,
        )
        db.add(entry)
        db.flush()
        apply_epistemic_metadata_to_entry(
            entry, case_subject_type=case.subject_type, case=case, db=db
        )
        sign_evidence_entry(entry)
        finalize_finding_after_sign(entry, case)
        log_finding_audit(
            db,
            finding_id=entry.id,
            event_type="manual_evidence_create",
            detail={"entered_by": body.entered_by, "entry_type": body.entry_type},
        )
        log_finding_audit(
            db,
            finding_id=entry.id,
            event_type="render_decision",
            detail={
                "is_publicly_renderable": bool(entry.is_publicly_renderable),
                "epistemic_level": entry.epistemic_level,
                "review_status": getattr(entry, "review_status", "pending"),
                "requires_human_review": bool(getattr(entry, "requires_human_review", False)),
            },
        )

        all_entries = db.scalars(
            select(EvidenceEntry).where(EvidenceEntry.case_file_id == case.id)
        ).all()
        apply_case_file_signature(case, list(all_entries), db=db)

        add_credibility(db, body.entered_by, 1, "added evidence")
        db.commit()
        return case_detail_response(db, case)
