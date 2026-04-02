from __future__ import annotations

import html
import json
import os
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import nulls_last, select
from sqlalchemy.orm import Session

from auth import require_api_key, require_matching_handle
from database import get_db
from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
from engines.signal_scorer import evidence_tier_from_checks
from models import (
    CaseContributor,
    CaseFile,
    CaseSnapshot,
    EvidenceEntry,
    Investigator,
    Signal,
    SignalAuditLog,
    SourceCheckLog,
)
from payloads import METHODOLOGY_NOTE_TEXT

router = APIRouter(prefix="/api/v1", tags=["reporting"])

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def get_base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


class ExposeSignalRequest(BaseModel):
    investigator_handle: str
    note: str | None = None


def _evidence_dict(e: EvidenceEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "type": e.entry_type,
        "title": e.title,
        "body": e.body,
        "source_name": e.source_name,
        "source_url": e.source_url,
        "date_of_event": e.date_of_event.isoformat() if e.date_of_event else None,
        "confidence": e.confidence,
        "flagged": e.flagged_for_review,
        "entered_by": e.entered_by,
        "entered_at": e.entered_at.isoformat() if e.entered_at else None,
        "amount": e.amount,
    }


def _sig_breakdown(s: Signal) -> dict[str, Any]:
    try:
        return json.loads(s.weight_breakdown or "{}")
    except json.JSONDecodeError:
        return {}


def _parse_signal_evidence_ids(s: Signal) -> list[str]:
    try:
        data = json.loads(s.evidence_ids or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _parse_cross_case_officials(signal: Signal) -> list[str]:
    raw = getattr(signal, "cross_case_officials", None)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x) for x in parsed]


def _confirmation_checks_dict(s: Signal) -> dict[str, Any]:
    try:
        c = json.loads(s.confirmation_checks or "{}")
        return c if isinstance(c, dict) else {}
    except json.JSONDecodeError:
        return {}


def _report_row_is_anticipatory(s: Signal, bd: dict[str, Any]) -> bool:
    tc = (s.temporal_class or "").strip().lower()
    if tc == "anticipatory":
        return True
    if tc == "retrospective":
        return False
    if (bd.get("exemplar_direction") or "").strip().lower() == "after":
        return False
    if (bd.get("exemplar_direction") or "").strip().lower() == "before":
        return True
    return (s.days_between is not None and s.days_between >= 0)


def _chronology_sentence(s: Signal, bd: dict[str, Any], official: str) -> str:
    gap_v = bd.get("min_gap_days")
    if gap_v is None:
        gap = abs(int(s.days_between or 0))
    else:
        try:
            gap = abs(int(gap_v))
        except (TypeError, ValueError):
            gap = abs(int(s.days_between or 0))
    pos = str(bd.get("exemplar_position") or "a recorded vote").strip()
    exdir = str(bd.get("exemplar_direction") or "").strip().lower()
    off = official or "the official"
    if exdir == "same_day":
        return f"Same calendar day as {off} cast vote ({pos})."
    if _report_row_is_anticipatory(s, bd):
        return f"{gap} days before {off} voted {pos}."
    return f"{gap} days after {off} voted {pos}."



def _fec_committee_id_from_raw(raw: dict[str, Any]) -> str | None:
    c = raw.get("committee")
    if isinstance(c, dict):
        cid = c.get("committee_id")
        if cid:
            return str(cid).strip().upper()
    return None


def _enrichment_channel_notes(lines: list[dict[str, Any]]) -> dict[str, str | None]:
    out: dict[str, str | None] = {"regulations": None, "govinfo": None}
    for row in lines:
        name = (row.get("display_name") or "").lower()
        line = (row.get("line") or "").lower()
        if "regulations.gov" in name or "regulations" in name:
            if "credential" in line or "not configured" in line:
                out["regulations"] = "Not checked — Regulations.gov unavailable."
        if "govinfo" in name:
            if "credential" in line or "not configured" in line:
                out["govinfo"] = "Not checked — GovInfo unavailable."
    return out


def _external_source_links_for_signal(
    db: Session,
    s: Signal,
    donor_display: str,
    source_lines: list[dict[str, Any]],
    bd: dict[str, Any],
) -> dict[str, Any]:
    notes = _enrichment_channel_notes(source_lines)
    if bd.get("has_regulatory_comment"):
        notes["regulations"] = None
    if bd.get("has_hearing_appearance"):
        notes["govinfo"] = None

    fec_cid: str | None = None
    vote_url: str | None = None
    for eid in _parse_signal_evidence_ids(s):
        try:
            uid = uuid.UUID(str(eid))
        except ValueError:
            continue
        ent = db.get(EvidenceEntry, uid)
        if not ent or ent.case_file_id != s.case_file_id:
            continue
        if ent.entry_type == "financial_connection" and (
            (ent.source_name or "").upper() == "FEC"
        ):
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                raw = {}
            if isinstance(raw, dict):
                fec_cid = fec_cid or _fec_committee_id_from_raw(raw)
        if ent.entry_type == "vote_record" and ent.source_url:
            u = ent.source_url.strip()
            if "senate.gov" in u and "roll_call" in u:
                vote_url = vote_url or u

    donor_enc = urllib.parse.quote((donor_display or "").strip() or "donor")
    fec_href: str | None = None
    if (donor_display or "").strip():
        if fec_cid:
            fec_href = f"https://www.fec.gov/data/receipts/?committee_id={fec_cid}&contributor_name={donor_enc}"
        else:
            fec_href = f"https://www.fec.gov/data/receipts/?contributor_name={donor_enc}"

    has_lda = bool(bd.get("has_lda_filing"))
    lda_href: str | None = None
    if has_lda and (donor_display or "").strip():
        lda_href = f"https://lda.senate.gov/api/v1/filings/?registrant_name={donor_enc}"

    return {
        "fec_href": fec_href,
        "vote_href": vote_url,
        "lda_href": lda_href,
        "regulations_note": notes["regulations"],
        "govinfo_note": notes["govinfo"],
    }


def _receipt_crypto_block(case: CaseFile) -> dict[str, Any]:
    """Display-only crypto metadata for HTML report (fingerprint previews, not full secrets)."""
    block: dict[str, Any] = {
        "case_id": str(case.id),
        "signed_at": case.last_signed_at.isoformat() if case.last_signed_at else None,
        "algorithm": "Ed25519",
        "content_hash_preview": "",
        "signature_preview": "",
        "public_key_preview": "",
        "has_material": False,
        "seal_schema_version": "",
        "pattern_alerts_sealed": None,
        "pattern_alerts_count": None,
        "has_embedded_payload": False,
    }
    packed = (case.signed_hash or "").strip()
    if not packed:
        return block
    try:
        data = json.loads(packed)
        ch = str(data.get("content_hash") or "")
        sig = str(data.get("signature") or "")
        if ch:
            block["content_hash_preview"] = ch[:16] + ("…" if len(ch) > 16 else "")
        if sig:
            block["signature_preview"] = sig[:16] + ("…" if len(sig) > 16 else "")
        block["has_material"] = bool(ch or sig)
        pub = os.environ.get("OPEN_CASE_PUBLIC_KEY", "").strip()
        if pub:
            block["public_key_preview"] = pub[:16] + ("…" if len(pub) > 16 else "")
        pl = data.get("payload")
        if isinstance(pl, dict):
            block["has_embedded_payload"] = True
            block["seal_schema_version"] = str(pl.get("schema_version") or "")
            pals = pl.get("pattern_alerts")
            if isinstance(pals, list):
                block["pattern_alerts_sealed"] = pals
                block["pattern_alerts_count"] = len(pals)
            else:
                block["pattern_alerts_sealed"] = []
                block["pattern_alerts_count"] = 0
    except json.JSONDecodeError:
        block["content_hash_preview"] = packed[:24] + ("…" if len(packed) > 24 else packed)
        block["has_material"] = bool(packed)
    return block


def _source_status_lines(case: CaseFile) -> list[dict[str, Any]]:
    """Last investigate adapter statuses for journalist-facing disclosure."""
    raw = getattr(case, "last_source_statuses", None) or ""
    if not str(raw).strip():
        return []
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("display_name") or row.get("adapter") or "Source")
        st = str(row.get("status") or "").strip().lower()
        detail = str(row.get("detail") or "").strip()
        if st == "clean":
            line = detail if detail else "Completed successfully"
        elif st == "credential_unavailable":
            line = "Not checked — credential not configured"
            if detail:
                line = f"{line}. {detail}"
        elif st in ("network_failure", "processing_failure", "credential_failure"):
            line = detail if detail else st.replace("_", " ")
        elif st == "rate_limited":
            line = "Temporarily unavailable"
            if detail:
                line = f"{line}: {detail}"
        elif st == "fallback":
            line = detail if detail else "Used rate-limited or fallback API path"
        elif st == "cached":
            line = detail if detail else "Served from adapter cache"
        else:
            line = detail if detail else (st or "unknown")
        out.append({"display_name": name, "status": st, "line": line})
    return out


def _signal_to_report_row(
    db: Session, s: Signal, source_lines: list[dict[str, Any]]
) -> dict[str, Any]:
    bd = _sig_breakdown(s)
    rel = float(getattr(s, "relevance_score", 0.0) or bd.get("relevance_score") or 0.0)
    wd = getattr(s, "weight_delta", None)
    xco = _parse_cross_case_officials(s)
    donor_label = str(bd.get("donor") or s.actor_a or "").strip()
    official_label = str(bd.get("official") or s.actor_b or "").strip()
    conf_checks = _confirmation_checks_dict(s)
    evidence_tier = evidence_tier_from_checks(conf_checks)
    is_anti = _report_row_is_anticipatory(s, bd)
    chron = _chronology_sentence(s, bd, official_label) if bd.get("kind") == "donor_cluster" else (s.description or "")
    links = _external_source_links_for_signal(db, s, donor_label, source_lines, bd)
    return {
        "id": str(s.id),
        "entity_name": donor_label or "Unknown donor",
        "official_name": official_label,
        "type": s.signal_type,
        "weight": s.weight,
        "description": s.description,
        "explanation": s.weight_explanation,
        "breakdown": bd,
        "proximity_summary": s.proximity_summary,
        "exposure_state": s.exposure_state,
        "repeat_count": s.repeat_count,
        "evidence_ids": _parse_signal_evidence_ids(s),
        "supporting_evidence": _supporting_evidence_summaries(db, s),
        "parse_warning": s.parse_warning,
        "confirmed": s.confirmed,
        "dismissed": s.dismissed,
        "dismissed_reason": s.dismissed_reason,
        "days_between": s.days_between,
        "amount": s.amount,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "is_featured": (s.weight or 0) >= 0.5,
        "relevance_score": rel,
        "is_donor_cluster": bd.get("kind") == "donor_cluster",
        "donor_display": donor_label,
        "official_display": official_label,
        "total_amount": float(bd.get("total_amount") or s.amount or 0.0),
        "min_gap_days": bd.get("min_gap_days"),
        "exemplar_vote": str(bd.get("exemplar_vote") or ""),
        "exemplar_direction": str(bd.get("exemplar_direction") or ""),
        "has_jurisdictional_match": bool(bd.get("has_jurisdictional_match")),
        "has_lda_filing": bool(bd.get("has_lda_filing")),
        "has_regulatory_comment": bool(bd.get("has_regulatory_comment")),
        "regulatory_comment_confidence": bd.get("regulatory_comment_confidence"),
        "has_hearing_appearance": bool(bd.get("has_hearing_appearance")),
        "has_sponsorship": bool(bd.get("has_sponsorship")),
        "cross_case_appearances": int(getattr(s, "cross_case_appearances", 0) or 0),
        "cross_case_officials": xco,
        "weight_delta": float(wd) if wd is not None else None,
        "new_top_signal": bool(getattr(s, "new_top_signal", False)),
        "first_appearance": bool(getattr(s, "first_appearance", False)),
        "evidence_tier": evidence_tier,
        "is_anticipatory": is_anti,
        "chronology_line": chron,
        "source_links": links,
        "temporal_class": (s.temporal_class or "").strip(),
    }


def _supporting_evidence_summaries(db: Session, signal: Signal) -> list[dict[str, Any]]:
    raw_ids = _parse_signal_evidence_ids(signal)
    if not raw_ids:
        return []
    uuids: list[uuid.UUID] = []
    for x in raw_ids:
        try:
            uuids.append(uuid.UUID(str(x)))
        except ValueError:
            continue
    if not uuids:
        return []
    rows = db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.id.in_(uuids))
    ).all()
    by_id = {str(e.id): e for e in rows}
    ordered: list[dict[str, Any]] = []
    for u in uuids:
        e = by_id.get(str(u))
        if not e:
            continue
        display_source = e.source_name
        if e.entry_type == "lobbying_filing":
            display_source = "Senate LDA Lobbying Filing"
        elif e.entry_type == "regulatory_comment":
            display_source = "Regulations.gov Comment"
        elif e.entry_type == "hearing_witness":
            display_source = "GovInfo — Hearing Witness"
        elif e.entry_type == "hearing_absence":
            display_source = "GovInfo — Hearing Search (no match)"
        ordered.append(
            {
                "id": str(e.id),
                "title": e.title or "",
                "source_name": display_source,
                "date_of_event": e.date_of_event.isoformat() if e.date_of_event else None,
                "amount": e.amount,
                "entry_type": e.entry_type,
                "source_url": e.source_url or "",
            }
        )
    return ordered


def _collect_report_payload(case_id: uuid.UUID, db: Session, bump_view: bool) -> dict[str, Any]:
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if bump_view:
        case.view_count = (case.view_count or 0) + 1
        db.commit()
        db.refresh(case)

    all_evidence = db.scalars(
        select(EvidenceEntry)
        .where(EvidenceEntry.case_file_id == case_id)
        .order_by(nulls_last(EvidenceEntry.date_of_event.asc()))
    ).all()

    signals = db.scalars(
        select(Signal)
        .where(Signal.case_file_id == case_id)
        .order_by(Signal.weight.desc())
    ).all()

    source_checks = db.scalars(
        select(SourceCheckLog)
        .where(SourceCheckLog.case_file_id == case_id)
        .order_by(SourceCheckLog.checked_at.desc())
    ).all()

    contributors = db.scalars(
        select(CaseContributor).where(CaseContributor.case_file_id == case_id)
    ).all()

    snapshots = db.scalars(
        select(CaseSnapshot)
        .where(CaseSnapshot.case_file_id == case_id)
        .order_by(CaseSnapshot.snapshot_number.desc())
    ).all()

    financial = [e for e in all_evidence if e.entry_type == "financial_connection"]
    votes = [e for e in all_evidence if e.entry_type == "vote_record"]
    gaps = [e for e in all_evidence if e.is_absence]
    timeline = [e for e in all_evidence if not e.is_absence]

    display_recipient = case.subject_name
    for s in signals:
        if s.signal_type != "temporal_proximity":
            continue
        sbd = _sig_breakdown(s)
        if sbd.get("kind") == "donor_cluster":
            cl = (sbd.get("committee_label") or "").strip()
            if cl:
                display_recipient = cl
                break

    source_lines = _source_status_lines(case)
    signal_rows_active = [
        _signal_to_report_row(db, s, source_lines) for s in signals if not s.dismissed
    ]
    qualified_leads = [
        r
        for r in signal_rows_active
        if r["confirmed"] or r["relevance_score"] >= 0.5
    ]
    qualified_leads.sort(
        key=lambda r: (-int(r["is_anticipatory"]), -float(r["weight"] or 0))
    )
    top_leads = qualified_leads[:5]
    top_leads_anticipatory = [r for r in top_leads if r["is_anticipatory"]]
    top_leads_retrospective = [r for r in top_leads if not r["is_anticipatory"]]

    signals_anticipatory = [r for r in signal_rows_active if r["is_anticipatory"]]
    signals_retrospective = [r for r in signal_rows_active if not r["is_anticipatory"]]
    signals_anticipatory.sort(key=lambda r: -float(r["weight"] or 0))
    signals_retrospective.sort(key=lambda r: -float(r["weight"] or 0))

    return {
        "case_id": str(case_id),
        "case_number": str(case.id).replace("-", "").upper()[:8],
        "title": case.title,
        "subject": case.subject_name,
        "display_recipient": display_recipient,
        "subject_type": case.subject_type,
        "jurisdiction": case.jurisdiction,
        "status": case.status,
        "opened_by": case.created_by,
        "opened_at": case.created_at.isoformat() if case.created_at else None,
        "pickup_note": case.pickup_note,
        "summary": case.summary,
        "signals": signal_rows_active,
        "top_leads": top_leads,
        "top_leads_anticipatory": top_leads_anticipatory,
        "top_leads_retrospective": top_leads_retrospective,
        "signals_anticipatory": signals_anticipatory,
        "signals_retrospective": signals_retrospective,
        "methodology_note": METHODOLOGY_NOTE_TEXT,
        "dismissed_signals": [
            {
                "id": str(s.id),
                "description": s.description,
                "dismissed_by": s.dismissed_by,
                "dismissed_reason": s.dismissed_reason,
            }
            for s in signals
            if s.dismissed
        ],
        "timeline": [_evidence_dict(e) for e in timeline],
        "financial_connections": [_evidence_dict(e) for e in financial],
        "vote_records": [_evidence_dict(e) for e in votes],
        "gaps_documented": [
            {
                "source": e.source_name,
                "body": e.body,
                "checked_at": e.entered_at.isoformat() if e.entered_at else None,
            }
            for e in gaps
        ],
        "sources_checked": [
            {
                "source": sc.source_name,
                "query": sc.query_string,
                "result_count": sc.result_count,
                "checked_at": sc.checked_at.isoformat() if sc.checked_at else None,
                "checked_by": sc.checked_by,
            }
            for sc in source_checks
        ],
        "contributors": [
            {
                "handle": c.investigator_handle,
                "role": c.role,
                "joined_at": c.joined_at.isoformat() if c.joined_at else None,
                "entry_count": c.entry_count,
            }
            for c in contributors
        ],
        "snapshots": [
            {
                "id": str(s.id),
                "number": s.snapshot_number,
                "taken_at": s.taken_at.isoformat() if s.taken_at else None,
                "taken_by": s.taken_by,
                "entry_count": s.entry_count,
                "share_url": s.share_url,
                "label": s.label,
            }
            for s in snapshots
        ],
        "totals": {
            "evidence_entries": len(all_evidence),
            "financial_connections": len(financial),
            "vote_records": len(votes),
            "gaps_documented": len(gaps),
            "signals_detected": len(signals),
            "signals_confirmed": sum(1 for s in signals if s.confirmed),
            "signals_dismissed": sum(1 for s in signals if s.dismissed),
            "top_leads_count": len(top_leads),
            "sources_checked": len(source_checks),
            "contributors": len(contributors),
        },
        "receipt_crypto": _receipt_crypto_block(case),
        "source_status_lines": _source_status_lines(case),
        "pattern_alerts": pattern_alerts_for_case(case_id, run_pattern_engine(db)),
    }


@router.get("/cases/{case_id}/report")
def get_case_report(case_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _collect_report_payload(case_id, db, bump_view=True)


@router.get("/cases/{case_id}/report/view", response_class=HTMLResponse)
def get_case_report_html(
    request: Request,
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Any:
    report = _collect_report_payload(case_id, db, bump_view=True)
    return _TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {"request": request, "report": report},
    )


@router.get("/cases/{case_id}/report/card", response_class=HTMLResponse)
def get_case_receipt_card(
    request: Request,
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    report = _collect_report_payload(case_id, db, bump_view=False)
    released = [
        s
        for s in report["signals"]
        if s.get("exposure_state") == "released" and s.get("confirmed")
    ]
    released.sort(key=lambda s: float(s.get("weight") or 0), reverse=True)
    top = released[0] if released else None
    base_url = get_base_url()
    verify_href = f"{base_url}/api/v1/cases/{case_id}/report/view"
    card_url = html.escape(f"{base_url}/api/v1/cases/{case_id}/report/card")
    subject = report.get("subject") or report.get("title") or "Open Case"
    title = html.escape(str(subject))
    og_title = html.escape(f"OPEN CASE: {subject}")
    if top:
        raw_headline = str(top.get("description") or "Signal released")
        headline = html.escape(raw_headline)
        sub = html.escape(
            str(top.get("proximity_summary") or top.get("explanation") or "")
        )
        og_desc = html.escape(raw_headline[:200])
    else:
        headline = html.escape("No released & confirmed signals yet")
        sub = html.escape(
            "Expose and confirm a signal to generate the receipt card."
        )
        og_desc = headline
    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta property="og:url" content="{card_url}" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="{og_title}" />
  <meta property="og:description" content="{og_desc}" />
  <meta name="twitter:card" content="summary_large_image" />
  <title>Receipt — {title}</title>
  <style>
    body {{ margin: 0; background: #0d0d0f; color: #f2f2f2; font-family: system-ui, sans-serif; }}
    .card {{
      max-width: 520px; margin: 2rem auto; padding: 1.25rem 1.5rem;
      border: 3px solid #c9182e; border-radius: 4px; background: #12121a;
      box-shadow: 0 12px 40px rgba(0,0,0,0.45);
    }}
    .kicker {{ color: #ff6b6b; font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase; }}
    h1 {{ font-size: 1.35rem; line-height: 1.25; margin: 0.5rem 0 0.75rem; font-weight: 700; }}
    p.meta {{ color: #b8b8c2; font-size: 0.95rem; line-height: 1.45; margin: 0 0 1rem; }}
    a.verify {{
      display: inline-block; color: #7ec8ff; font-weight: 600; text-decoration: none;
      border-bottom: 1px solid #7ec8ff;
    }}
    a.verify:hover {{ color: #fff; border-color: #fff; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="kicker">Open Case receipt</div>
    <h1>{headline}</h1>
    <p class="meta">{sub}</p>
    <a class="verify" href="{verify_href}">Verification link (full report)</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html_doc)


@router.patch("/signals/{signal_id}/expose")
def expose_signal(
    signal_id: uuid.UUID,
    body: ExposeSignalRequest,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    require_matching_handle(auth_inv, body.investigator_handle)
    signal = db.scalar(select(Signal).where(Signal.id == signal_id))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if not signal.confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "UNCONFIRMED_SIGNAL",
                "message": (
                    "Signal must be confirmed before it can be exposed. "
                    "Use PATCH /api/v1/signals/{id}/confirm first. "
                    "A receipt should only contain verified findings."
                ),
                "signal_id": str(signal_id),
                "current_state": {
                    "confirmed": False,
                    "exposure_state": signal.exposure_state,
                },
            },
        )
    if signal.dismissed:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "DISMISSED_SIGNAL",
                "message": "Dismissed signals cannot be exposed.",
                "signal_id": str(signal_id),
            },
        )
    prev = signal.exposure_state
    signal.exposure_state = "released"
    db.add(
        SignalAuditLog(
            signal_id=signal.id,
            action="exposed",
            performed_by=body.investigator_handle,
            old_weight=None,
            new_weight=signal.weight,
            note=(body.note or f"exposure {prev} -> released"),
        )
    )
    db.commit()
    db.refresh(signal)
    return {"signal_id": str(signal_id), "exposure_state": signal.exposure_state}


@router.get("/signals/{signal_id}/history")
def get_signal_history(
    signal_id: uuid.UUID, db: Session = Depends(get_db)
) -> dict[str, Any]:
    signal = db.scalar(select(Signal).where(Signal.id == signal_id))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    history = db.scalars(
        select(SignalAuditLog)
        .where(SignalAuditLog.signal_id == signal_id)
        .order_by(SignalAuditLog.performed_at.asc())
    ).all()
    return {
        "signal_id": str(signal_id),
        "current_weight": signal.weight,
        "current_state": {
            "confirmed": signal.confirmed,
            "dismissed": signal.dismissed,
            "exposure_state": signal.exposure_state,
            "repeat_count": signal.repeat_count,
        },
        "history": [
            {
                "action": h.action,
                "performed_by": h.performed_by,
                "performed_at": h.performed_at.isoformat() if h.performed_at else None,
                "old_weight": h.old_weight,
                "new_weight": h.new_weight,
                "note": h.note,
            }
            for h in history
        ],
    }


@router.get("/cases/{case_id}/report/view", response_class=HTMLResponse)
def investigation_report_view(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> HTMLResponse:
    """
    Human-readable HTML summary of the case — top donor-cluster signals (resolved only).
    """
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    rows = db.scalars(
        select(Signal)
        .where(
            Signal.case_file_id == case_id,
            Signal.exposure_state != "unresolved",
            Signal.signal_type == "temporal_proximity",
        )
        .order_by(Signal.weight.desc())
    ).all()

    clusters: list[tuple[Signal, dict[str, Any]]] = []
    for s in rows:
        try:
            bd = json.loads(s.weight_breakdown or "{}")
        except json.JSONDecodeError:
            bd = {}
        if bd.get("kind") == "donor_cluster":
            clusters.append((s, bd))
    clusters = clusters[:10]

    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    handle = html.escape(case.created_by or "unknown")
    header_recipient = case.subject_name or "Subject"
    for _s, bd0 in clusters:
        cl = (bd0.get("committee_label") or "").strip()
        if cl:
            header_recipient = cl
            break
    subject = html.escape(header_recipient)
    title = html.escape(case.title or "Investigation")
    sig_short = html.escape((case.signed_hash or "")[:48])

    cards_html: list[str] = []
    for s, bd in clusters:
        donor = html.escape(str(bd.get("donor") or s.actor_a or ""))
        official = html.escape(str(bd.get("official") or s.actor_b or ""))
        total_amt = float(bd.get("total_amount") or s.amount or 0.0)
        min_gap = bd.get("min_gap_days", "—")
        ex_vote = html.escape(str(bd.get("exemplar_vote") or ""))
        direction = html.escape(str(bd.get("exemplar_direction") or ""))
        w = float(s.weight or 0.0)
        bar_w = min(100, int(w * 100))
        temporal = html.escape(str(s.temporal_class or ""))
        cards_html.append(
            f'<section style="border:1px solid #ccc;margin:1em 0;padding:1em;max-width:50em;">'
            f"<h3>{donor}</h3>"
            f"<p><strong>Official:</strong> {official} &nbsp;|&nbsp; "
            f"<strong>Type:</strong> {temporal}</p>"
            f"<p><strong>Total amount (window):</strong> ${total_amt:,.2f}</p>"
            f"<p><strong>Tightest gap:</strong> {html.escape(str(min_gap))} days &nbsp;|&nbsp; "
            f"<strong>Exemplar vote:</strong> {ex_vote} &nbsp;|&nbsp; "
            f"<strong>Direction:</strong> {direction}</p>"
            f'<p><strong>Weight:</strong> {w:.3f}</p>'
            f'<div style="background:#eee;height:12px;width:100%;max-width:400px;">'
            f'<div style="background:#264653;height:12px;width:{bar_w}%;"></div></div>'
            f"<p style=\"font-size:0.9em;color:#444;\">{html.escape((s.description or '')[:500])}</p>"
            f"</section>"
        )

    body = "".join(cards_html) or "<p>No donor-cluster signals yet (run investigate).</p>"

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Open Case — {subject}</title></head>
<body style="font-family:Georgia,serif;max-width:720px;margin:2em auto;line-height:1.45;">
<h1>Open Case Investigation: {subject}</h1>
<p><strong>Case:</strong> {title} &nbsp;|&nbsp; <strong>Generated:</strong> {html.escape(gen_at)}</p>
<p><strong>Investigator handle (created_by):</strong> {handle}</p>
<h2>Top donor clusters (temporal proximity)</h2>
{body}
<hr/>
<p style="font-size:0.85em;color:#333;">
All findings drawn from public FEC and Congressional records. Signals represent temporal proximity,
not confirmed causation.
<strong>Signed (truncated):</strong> <code>{sig_short}</code>
</p>
</body></html>"""
    return HTMLResponse(content=page)


@router.get("/investigators/{handle}/score")
def get_investigator_score(handle: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    investigator = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if not investigator:
        raise HTTPException(status_code=404, detail="Investigator not found")
    return {
        "handle": handle,
        "credibility_score": investigator.credibility_score,
        "cases_opened": investigator.cases_opened,
        "entries_contributed": investigator.entries_contributed,
    }
