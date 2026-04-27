from __future__ import annotations

import asyncio
import html
import json
import os
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import nulls_last, select
from sqlalchemy.orm import Session

from auth import require_api_key, require_matching_handle
from core.admin_gate import admin_authorized
from database import get_db
from engines.entity_resolution import resolve
from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
from services.proportionality import proportionality_packet_for_signal_sync
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
    SubjectProfile,
)
from payloads import (
    METHODOLOGY_LEGAL_LIABILITY_NOTE,
    METHODOLOGY_NOTE_TEXT,
    METHODOLOGY_NOTE_VERSION,
    epistemic_distribution_from_entries,
)
from services.case_auto_ingest import case_needs_fec_refresh
from server.services.report_stream import (
    report_pattern_refresh_task,
    subscribe_pattern_events,
    unsubscribe_pattern_events,
)

router = APIRouter(prefix="/api/v1", tags=["reporting"])

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


def get_base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")


def _is_demo_case_file(case: CaseFile) -> bool:
    """Public-demo / batch-demo cases: relax signal visibility when client requests demo_internal_signals."""
    slug_l = (case.slug or "").strip().lower()
    if slug_l.startswith("open-case-public-demo-"):
        return True
    demo_handle = (os.getenv("OPEN_CASE_DEMO_INVESTIGATOR_HANDLE") or "demo_public").strip().lower()
    creator = (case.created_by or "").strip().lower()
    return bool(creator) and creator == demo_handle


def _visible_evidence_rows(
    entries: list[EvidenceEntry], include_unreviewed: bool
) -> list[EvidenceEntry]:
    if include_unreviewed:
        return entries
    return [e for e in entries if not getattr(e, "requires_human_review", False)]


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
        "epistemic_level": getattr(e, "epistemic_level", "REPORTED"),
        "requires_human_review": bool(getattr(e, "requires_human_review", False)),
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
        lda_href = f"https://lda.gov/api/v1/filings/?registrant_name={donor_enc}"

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
    db: Session,
    s: Signal,
    source_lines: list[dict[str, Any]],
    *,
    include_unreviewed: bool = False,
) -> dict[str, Any]:
    bd = _sig_breakdown(s)
    rel = float(getattr(s, "relevance_score", 0.0) or bd.get("relevance_score") or 0.0)
    wd = getattr(s, "weight_delta", None)
    xco = _parse_cross_case_officials(s)
    donor_label = str(bd.get("donor") or s.actor_a or "").strip()
    official_label = str(bd.get("official") or s.actor_b or "").strip()
    resolution_method_label = ""
    if bd.get("kind") == "donor_cluster" and donor_label:
        rm = resolve(donor_label).resolution_method
        resolution_method_label = {
            "exact": "[exact]",
            "alias_table": "[alias]",
            "unresolved": "[unresolved]",
        }.get(rm, "")
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
        "supporting_evidence": _supporting_evidence_summaries(
            db, s, include_unreviewed=include_unreviewed
        ),
        "epistemic_level": getattr(s, "epistemic_level", "REPORTED"),
        "requires_human_review": bool(getattr(s, "requires_human_review", False)),
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
        "resolution_method_label": resolution_method_label,
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
        "proportionality_packet": proportionality_packet_for_signal_sync(s),
    }


def _supporting_evidence_summaries(
    db: Session, signal: Signal, *, include_unreviewed: bool = False
) -> list[dict[str, Any]]:
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
        if not include_unreviewed and getattr(e, "requires_human_review", False):
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


_REPORT_SECTION_KEYS = frozenset(
    {"identity", "bench_record", "money", "politics", "conduct", "signals"}
)


def _build_report_sections(
    db: Session,
    case_id: uuid.UUID,
    case: CaseFile,
    all_evidence: list[EvidenceEntry],
    financial: list[EvidenceEntry],
    votes: list[EvidenceEntry],
    signal_rows: list[dict[str, Any]],
    pattern_rows: list[dict[str, Any]],
    include_unreviewed: bool,
) -> dict[str, Any]:
    prof = db.scalar(select(SubjectProfile).where(SubjectProfile.case_file_id == case_id))
    ev_vis = _visible_evidence_rows(all_evidence, include_unreviewed)
    fin_vis = _visible_evidence_rows(financial, include_unreviewed)
    politics_types = frozenset(
        {
            "vote_record",
            "bill_sponsorship",
            "committee_assignment",
            "floor_speech",
            "lobbying_filing",
            "timeline_event",
            "regulatory_comment",
        }
    )
    politics = [
        _evidence_dict(e)
        for e in ev_vis
        if e.entry_type in politics_types and not e.is_absence
    ]
    conduct_types = frozenset({"fec_violation", "ethics_issue"})
    conduct = [
        _evidence_dict(e)
        for e in ev_vis
        if e.entry_type in conduct_types
        or e.flagged_for_review
        or "discipline" in (e.entry_type or "").lower()
        or "complaint" in ((e.title or "") + (e.body or "")).lower()
    ]
    bench_types = frozenset({"hearing_witness", "court_opinion", "judicial_disclosure"})
    bench = [
        _evidence_dict(e)
        for e in ev_vis
        if e.entry_type in bench_types or "opinion" in (e.entry_type or "").lower()
    ]
    sig_vis = [
        r
        for r in signal_rows
        if include_unreviewed or not r.get("requires_human_review")
    ]
    identity = {
        "profile": None,
        "summary": case.summary,
        "case_government_level": getattr(case, "government_level", None),
        "case_branch": getattr(case, "branch", None),
        "pilot_cohort": getattr(case, "pilot_cohort", None),
        "summary_epistemic_level": getattr(case, "summary_epistemic_level", "REPORTED"),
    }
    if prof:
        identity["profile"] = {
            "subject_name": prof.subject_name,
            "subject_type": prof.subject_type,
            "bioguide_id": prof.bioguide_id,
            "government_level": prof.government_level,
            "branch": prof.branch,
            "historical_depth": prof.historical_depth,
            "state": prof.state,
            "district": prof.district,
            "office": prof.office,
        }
    signals_tab = {
        "signals": sig_vis,
        "pattern_alerts": pattern_rows,
        "epistemic_distribution": epistemic_distribution_from_entries(
            [e for e in ev_vis if not e.is_absence]
        ),
    }
    return {
        "identity": [identity],
        "bench_record": bench,
        "money": [_evidence_dict(e) for e in fin_vis],
        "politics": politics,
        "conduct": conduct,
        "signals": [signals_tab],
    }


def _collect_report_payload(
    case_id: uuid.UUID,
    db: Session,
    bump_view: bool,
    *,
    include_unreviewed: bool = False,
    section: str | None = None,
    demo_include_internal_signals: bool = False,
) -> dict[str, Any]:
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

    _money_entry_types = frozenset(
        {"financial_connection", "stock_trade", "financial_disclosure"}
    )
    financial = [e for e in all_evidence if e.entry_type in _money_entry_types]
    votes = [e for e in all_evidence if e.entry_type == "vote_record"]
    gaps = [e for e in all_evidence if e.is_absence]
    timeline_all = [e for e in all_evidence if not e.is_absence]
    timeline = _visible_evidence_rows(timeline_all, include_unreviewed)
    financial_pub = _visible_evidence_rows(financial, include_unreviewed)
    votes_pub = _visible_evidence_rows(votes, include_unreviewed)

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
        _signal_to_report_row(
            db,
            s,
            source_lines,
            include_unreviewed=include_unreviewed or demo_include_internal_signals,
        )
        for s in signals
        if not s.dismissed
    ]
    relax_signal_filter = include_unreviewed or demo_include_internal_signals
    if not relax_signal_filter:
        signal_rows_active = [
            r for r in signal_rows_active if not r.get("requires_human_review")
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

    pal = run_pattern_engine(db)
    pattern_rows = pattern_alerts_for_case(
        case_id, pal, include_unreviewed=include_unreviewed
    )
    sections = _build_report_sections(
        db,
        case_id,
        case,
        all_evidence,
        financial,
        votes,
        signal_rows_active,
        pattern_rows,
        include_unreviewed or demo_include_internal_signals,
    )
    epistemic_distribution = epistemic_distribution_from_entries(
        [e for e in _visible_evidence_rows(all_evidence, include_unreviewed) if not e.is_absence]
    )

    payload = {
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
        "methodology_note_version": METHODOLOGY_NOTE_VERSION,
        "legal_liability_note": METHODOLOGY_LEGAL_LIABILITY_NOTE,
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
        "financial_connections": [_evidence_dict(e) for e in financial_pub],
        "vote_records": [_evidence_dict(e) for e in votes_pub],
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
        "pattern_alerts": pattern_rows,
        "sections": sections,
        "epistemic_distribution": epistemic_distribution,
    }
    if section and section in _REPORT_SECTION_KEYS:
        filtered_sec = {k: [] for k in _REPORT_SECTION_KEYS}
        filtered_sec[section] = sections.get(section, [])
        payload["sections"] = filtered_sec
        payload["section_filter"] = section
    return payload


def _enforce_report_query_params(
    section: str | None,
    include_unreviewed: bool,
    x_admin_secret: str | None,
) -> None:
    if section and section not in _REPORT_SECTION_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"section must be one of {sorted(_REPORT_SECTION_KEYS)}",
        )
    if include_unreviewed and not admin_authorized(x_admin_secret):
        raise HTTPException(
            status_code=403,
            detail="include_unreviewed requires a valid X-Admin-Secret",
        )


def _attach_pattern_refresh_meta(
    report: dict[str, Any], case_id: uuid.UUID, pending: bool
) -> None:
    report["pattern_alerts_refresh_pending"] = pending
    if pending:
        report["pattern_alerts_stream"] = {
            "protocol": "sse",
            "path": f"/api/v1/cases/{case_id}/report/pattern-events",
        }


@router.get("/cases/{case_id}/report/pattern-events")
async def case_report_pattern_events(case_id: uuid.UUID) -> StreamingResponse:
    """SSE: receive updated `pattern_alerts` after background FEC ingest completes."""

    async def event_gen():
        queue = await subscribe_pattern_events(case_id)
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=75.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    continue
                yield f"data: {json.dumps(msg, default=str)}\n\n"
                if msg.get("type") in ("pattern_alerts", "error"):
                    break
        finally:
            await unsubscribe_pattern_events(case_id, queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/methodology")
def get_methodology() -> dict[str, Any]:
    return {
        "methodology_note_version": METHODOLOGY_NOTE_VERSION,
        "methodology_note": METHODOLOGY_NOTE_TEXT,
        "legal_liability_note": METHODOLOGY_LEGAL_LIABILITY_NOTE,
    }


@router.get("/cases")
def list_cases_api(
    db: Session = Depends(get_db),
    government_level: str | None = Query(None),
    branch: str | None = Query(None),
    subject_type: str | None = Query(None),
    pilot: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    q = select(CaseFile).order_by(CaseFile.created_at.desc()).limit(limit)
    if subject_type:
        q = q.where(CaseFile.subject_type == subject_type.strip())
    if pilot:
        q = q.where(CaseFile.pilot_cohort == pilot.strip())
    join_profile = government_level is not None or branch is not None
    if join_profile:
        q = q.join(SubjectProfile, SubjectProfile.case_file_id == CaseFile.id)
        if government_level:
            q = q.where(SubjectProfile.government_level == government_level.strip())
        if branch:
            q = q.where(SubjectProfile.branch == branch.strip())
        q = q.distinct()
    rows = db.scalars(q).all()
    return {
        "count": len(rows),
        "cases": [
            {
                "id": str(c.id),
                "slug": c.slug,
                "title": c.title,
                "subject_name": c.subject_name,
                "subject_type": c.subject_type,
                "jurisdiction": c.jurisdiction,
                "status": c.status,
                "government_level": getattr(c, "government_level", None),
                "branch": getattr(c, "branch", None),
                "pilot_cohort": getattr(c, "pilot_cohort", None),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ],
    }


@router.get("/cases/lookup-by-bioguide/{bioguide_id}")
def lookup_case_by_bioguide(
    bioguide_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Resolve the newest CaseFile for a Congress bioguide_id (investigate pipeline / demo cases).
    Used when the UI opens /official/{bioguide} but only a case report exists (no senator dossier).
    """
    bg = (bioguide_id or "").strip().upper()
    if not bg or len(bg) > 12:
        raise HTTPException(status_code=400, detail="Invalid bioguide_id")
    case = db.scalar(
        select(CaseFile)
        .join(SubjectProfile, SubjectProfile.case_file_id == CaseFile.id)
        .where(SubjectProfile.bioguide_id == bg)
        .order_by(CaseFile.created_at.desc())
        .limit(1)
    )
    if not case:
        raise HTTPException(status_code=404, detail="No case for bioguide_id")
    return {
        "case_id": str(case.id),
        "slug": case.slug,
        "subject_name": case.subject_name,
        "subject_type": case.subject_type,
    }


@router.get("/cases/{case_id}/report")
async def get_case_report(
    case_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    section: str | None = Query(None),
    include_unreviewed: bool = Query(False),
    demo_internal_signals: bool = Query(
        False,
        description="For demo/batch cases only: include quarantined human-review signal rows.",
    ),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> dict[str, Any]:
    _enforce_report_query_params(section, include_unreviewed, x_admin_secret)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    demo_relaxed = bool(demo_internal_signals) and _is_demo_case_file(case)
    pending = case_needs_fec_refresh(db, case_id, case)
    if pending:
        background_tasks.add_task(report_pattern_refresh_task, case_id)
    report = _collect_report_payload(
        case_id,
        db,
        bump_view=True,
        include_unreviewed=include_unreviewed,
        section=section,
        demo_include_internal_signals=demo_relaxed,
    )
    _attach_pattern_refresh_meta(report, case_id, pending)
    return report


@router.get("/cases/{case_id}/report/view", response_class=HTMLResponse)
async def get_case_report_html(
    request: Request,
    case_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    section: str | None = Query(None),
    include_unreviewed: bool = Query(False),
    demo_internal_signals: bool = Query(False),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> Any:
    _enforce_report_query_params(section, include_unreviewed, x_admin_secret)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    demo_relaxed = bool(demo_internal_signals) and _is_demo_case_file(case)
    pending = case_needs_fec_refresh(db, case_id, case)
    if pending:
        background_tasks.add_task(report_pattern_refresh_task, case_id)
    report = _collect_report_payload(
        case_id,
        db,
        bump_view=True,
        include_unreviewed=include_unreviewed,
        section=section,
        demo_include_internal_signals=demo_relaxed,
    )
    _attach_pattern_refresh_meta(report, case_id, pending)
    return _TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {"request": request, "report": report},
    )


@router.get("/cases/{case_id}/report/card", response_class=HTMLResponse)
async def get_case_receipt_card(
    request: Request,
    case_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    section: str | None = Query(None),
    include_unreviewed: bool = Query(False),
    demo_internal_signals: bool = Query(False),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> HTMLResponse:
    _enforce_report_query_params(section, include_unreviewed, x_admin_secret)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    demo_relaxed = bool(demo_internal_signals) and _is_demo_case_file(case)
    pending = case_needs_fec_refresh(db, case_id, case)
    if pending:
        background_tasks.add_task(report_pattern_refresh_task, case_id)
    report = _collect_report_payload(
        case_id,
        db,
        bump_view=False,
        include_unreviewed=include_unreviewed,
        section=section,
        demo_include_internal_signals=demo_relaxed,
    )
    _attach_pattern_refresh_meta(report, case_id, pending)
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
