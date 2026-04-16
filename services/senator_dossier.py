"""
Orchestrate senator dossier: deep research, staff network, gap analysis, pattern alerts, signing.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.amendment_fingerprint import fetch_amendment_fingerprint
from adapters.committee_witnesses import fetch_committee_witnesses
from adapters.dark_money import fetch_dark_money
from adapters.ethics_travel import fetch_ethics_travel
from adapters.senate_committees import get_or_refresh_senator_committees
from adapters.senator_deep_research import fetch_all_senator_deep_research
from adapters.staff_network import fetch_staff_network
from adapters.stock_act_trades import fetch_stock_act_trades_all_years
from adapters.stock_trade_proximity import fetch_stock_trade_proximity_all_years
from engines.pattern_engine import pattern_alert_to_payload, run_pattern_engine
from models import CaseFile, SenatorDossier, SubjectProfile
from services.case_auto_ingest import maybe_auto_ingest_case
from services.gap_analysis import generate_gap_sentences
from signing import pack_signed_hash, sign_payload

logger = logging.getLogger(__name__)

DOSSIER_DISCLAIMER = (
    "These findings document public records only. They do not prove causation or "
    "wrongdoing. All findings are for further human review."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _base_url() -> str:
    return (os.environ.get("BASE_URL") or "http://localhost:8000").rstrip("/")


def _urls_for_dossier(dossier_id: uuid.UUID) -> tuple[str, str, str]:
    base = _base_url()
    pdf_url = f"{base}/api/v1/dossiers/{dossier_id}/pdf"
    public_url = f"{base}/api/v1/dossiers/{dossier_id}/public"
    verify_url = public_url
    return pdf_url, public_url, verify_url


async def build_senator_dossier(bioguide_id: str, db: Session) -> dict[str, Any]:
    """
    Full pipeline for the latest `building` SenatorDossier row for this bioguide:
    deep research (6 categories), staff network, gap analysis, pattern alerts,
    signed dossier payload (includes content_hash, signature, public_key).
    """
    bg = (bioguide_id or "").strip()
    row = db.scalar(
        select(SenatorDossier)
        .where(SenatorDossier.bioguide_id == bg, SenatorDossier.status == "building")
        .order_by(SenatorDossier.created_at.desc())
    )
    if row is None:
        raise ValueError(f"No in-progress dossier for bioguide_id={bg!r}")

    prof = db.scalar(select(SubjectProfile).where(SubjectProfile.bioguide_id == bg))
    if prof is None:
        raise ValueError(f"No subject profile for bioguide_id={bg!r}")
    case = db.get(CaseFile, prof.case_file_id)
    if case is None:
        raise ValueError("Case file missing for subject profile")

    await maybe_auto_ingest_case(db, case.id, background_tasks=None)

    senator_name = (case.subject_name or prof.subject_name or "").strip()
    generated_at = _now_iso()

    deep = fetch_all_senator_deep_research(db, bg, senator_name)

    staff_bundle = await fetch_staff_network(db, bg, case.id)
    staff_list = staff_bundle.get("staff") or []
    subject_meta = dict(staff_bundle.get("subject_meta") or {})

    sen_rows = await get_or_refresh_senator_committees(db, bg)
    committee_names = [r.committee_name for r in sen_rows]
    if committee_names and not subject_meta.get("committees"):
        subject_meta["committees"] = committee_names

    if not subject_meta.get("name"):
        subject_meta["name"] = senator_name
    subject_meta["bioguide_id"] = bg
    subject_meta.setdefault("state", "")
    subject_meta.setdefault("party", "")
    subject_meta.setdefault("committees", committee_names)
    subject_meta.setdefault("years_in_office", 0)

    gap_list = generate_gap_sentences(str(case.id), db)

    case_key = str(case.id)
    pattern_alerts = [
        pattern_alert_to_payload(a)
        for a in run_pattern_engine(db)
        if case_key in list(a.matched_case_ids or [])
    ]

    stock_trade_proximity = await fetch_stock_trade_proximity_all_years(
        db, bg, senator_name, committee_names
    )
    amendment_fingerprint = await fetch_amendment_fingerprint(db, bg, case.id)

    state_for_dm = str(subject_meta.get("state") or "").strip()

    stock_act_trades: list[dict[str, Any]] = []
    try:
        stock_act_trades = await fetch_stock_act_trades_all_years(
            db, bg, senator_name, committee_names, case.id
        )
    except Exception:
        logger.exception("stock_act_trades adapter failed bioguide_id=%s", bg)

    dark_money: list[dict[str, Any]] = []
    try:
        dark_money = await fetch_dark_money(db, bg, case.id, state_for_dm)
    except Exception:
        logger.exception("dark_money adapter failed bioguide_id=%s", bg)

    ethics_travel: list[dict[str, Any]] = []
    try:
        ethics_travel = await fetch_ethics_travel(db, bg, senator_name, case.id)
    except Exception:
        logger.exception("ethics_travel adapter failed bioguide_id=%s", bg)

    committee_witnesses: list[dict[str, Any]] = []
    try:
        committee_witnesses = await fetch_committee_witnesses(db, bg, sen_rows, case.id)
    except Exception:
        logger.exception("committee_witnesses adapter failed bioguide_id=%s", bg)

    pdf_url, public_url, verify_url = _urls_for_dossier(row.id)

    body: dict[str, Any] = {
        "schema_version": "2.0",
        "dossier_id": str(row.id),
        "version": row.version,
        "previous_version_id": str(row.previous_version_id)
        if row.previous_version_id
        else None,
        "subject": subject_meta,
        "generated_at": generated_at,
        "completed_at": generated_at,
        # Category blocks may include scoped "references" / "source_citations" from
        # senator_deep_research (Perplexity). No top-level dossier "references" aggregate:
        # indices are meaningful per category only to avoid cross-category mis-mapping.
        "deep_research": {
            "categories": deep.get("categories") or {},
            "needs_human_review": bool(deep.get("needs_human_review")),
            "narrative_validation_flags": deep.get("narrative_validation_flags") or [],
        },
        "staff_network": staff_list,
        "gap_analysis": gap_list,
        "pattern_alerts": pattern_alerts,
        "stock_trade_proximity": stock_trade_proximity,
        "stock_act_trades": stock_act_trades,
        "dark_money": dark_money,
        "ethics_travel": ethics_travel,
        "committee_witnesses": committee_witnesses,
        "amendment_fingerprint": amendment_fingerprint,
        "share_token": row.share_token,
        "disclaimer": DOSSIER_DISCLAIMER,
        "pdf_url": pdf_url,
        "public_url": public_url,
        "verify_url": verify_url,
    }

    signed = sign_payload(body)
    packed = pack_signed_hash(signed["content_hash"], signed["signature"], body)

    row.dossier_json = json.dumps(signed, separators=(",", ":"), default=str)
    row.signature = packed
    row.senator_name = senator_name[:256]
    row.status = "completed"
    row.completed_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()

    return signed


async def _run_senator_dossier_job_async(dossier_id: uuid.UUID) -> None:
    from database import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(SenatorDossier, dossier_id)
        if not row:
            logger.warning("senator dossier job: row missing %s", dossier_id)
            return
        await build_senator_dossier(row.bioguide_id, db)
    except Exception:
        logger.exception("senator dossier build failed dossier_id=%s", dossier_id)
        try:
            row = db.get(SenatorDossier, dossier_id)
            if row:
                row.status = "failed"
                db.add(row)
                db.commit()
        except Exception:
            logger.exception(
                "senator dossier failed-status update error dossier_id=%s", dossier_id
            )
            db.rollback()
    finally:
        db.close()


def run_senator_dossier_job(dossier_id: uuid.UUID) -> None:
    """Sync wrapper for FastAPI BackgroundTasks."""
    import asyncio

    asyncio.run(_run_senator_dossier_job_async(dossier_id))
