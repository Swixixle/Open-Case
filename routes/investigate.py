from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from adapters.base import BaseAdapter
from adapters.cache import get_cached_response, response_from_cache_dict, store_cached_response
from adapters.congress_votes import CongressVotesAdapter
from adapters.dedup import is_duplicate, make_evidence_hash
from adapters.fec import FECAdapter
from adapters.indiana_cf import IndianaCFAdapter
from adapters.indy_gis import IndyGISAdapter
from adapters.marion_assessor import MarionCountyAssessorAdapter
from adapters.usa_spending import USASpendingAdapter
from database import get_db
from engines.contract_anomaly import detect_contract_anomalies
from engines.contract_proximity import detect_contract_proximity
from engines.signal_scorer import (
    build_signals_from_anomalies,
    build_signals_from_contract_proximity,
    build_signals_from_proximity,
)
from engines.temporal_proximity import detect_proximity
from models import (
    CaseContributor,
    CaseFile,
    EvidenceEntry,
    Investigator,
    Signal,
    SignalAuditLog,
    SourceCheckLog,
    SubjectProfile,
)
from signals.dedup import upsert_signal
from payloads import apply_case_file_signature, sign_evidence_entry
from scoring import add_credibility

router = APIRouter(prefix="/api/v1", tags=["investigate"])


class InvestigateRequest(BaseModel):
    subject_name: str
    investigator_handle: str
    address: str | None = None
    bioguide_id: str | None = None
    proximity_days: int = Field(
        90,
        ge=1,
        le=1095,
        description="Max days after a financial event a vote may still pair (default 90).",
    )
    fec_committee_id: str | None = Field(
        None,
        description="Optional FEC committee ID (e.g. C00459255) for schedule_a by committee.",
    )


class ConfirmSignalBody(BaseModel):
    investigator_handle: str


class DismissSignalBody(BaseModel):
    investigator_handle: str
    reason: str = Field(..., min_length=1)


def _parse_event_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(str(s)[:10])


def _ensure_investigator(db: Session, handle: str) -> None:
    row = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if row:
        return
    db.add(Investigator(handle=handle, public_key=""))
    db.flush()


def _bump_contributor(db: Session, case_id: uuid.UUID, handle: str) -> None:
    cc = db.scalar(
        select(CaseContributor).where(
            CaseContributor.case_file_id == case_id,
            CaseContributor.investigator_handle == handle,
        )
    )
    now = datetime.now(timezone.utc)
    if cc:
        cc.last_active_at = now
    else:
        db.add(
            CaseContributor(
                case_file_id=case_id,
                investigator_handle=handle,
                role="field",
                last_active_at=now,
            )
        )


def _sync_subject_profile(
    db: Session,
    case: CaseFile,
    request: InvestigateRequest,
    investigator: str,
) -> SubjectProfile | None:
    prof = db.scalar(
        select(SubjectProfile).where(SubjectProfile.case_file_id == case.id)
    )
    if prof is None:
        prof = SubjectProfile(
            case_file_id=case.id,
            subject_name=case.subject_name,
            subject_type=case.subject_type,
        )
        db.add(prof)
        db.flush()
    if request.bioguide_id:
        prof.bioguide_id = request.bioguide_id
        prof.updated_by = investigator
    return prof


def _add_source_log(
    db: Session,
    case_id: uuid.UUID,
    source_name: str,
    query: str,
    result_count: int,
    investigator: str,
    result_hash: str,
    tracker: list[str] | None = None,
) -> None:
    db.add(
        SourceCheckLog(
            case_file_id=case_id,
            source_name=source_name,
            query_string=query,
            result_count=result_count,
            checked_by=investigator,
            result_hash=result_hash or "",
        )
    )
    if tracker is not None:
        tracker.append(source_name)


def _ingest_parse_warning_note(
    db: Session,
    case_id: uuid.UUID,
    investigator: str,
    adapter: BaseAdapter,
    query: str,
    warning: str,
    created: list[EvidenceEntry],
) -> None:
    eh = make_evidence_hash(
        case_id,
        adapter.source_name,
        "",
        None,
        None,
        f"__parse_warning__:{query}:{warning[:240]}",
    )
    if is_duplicate(db, case_id, eh):
        return
    entry = EvidenceEntry(
        case_file_id=case_id,
        entry_type="gap_documented",
        title=f"{adapter.source_name}: Source parse warning",
        body=warning,
        source_name=adapter.source_name,
        adapter_name=adapter.source_name,
        entered_by=investigator,
        confidence="confirmed",
        is_absence=True,
        flagged_for_review=False,
        evidence_hash=eh,
    )
    db.add(entry)
    db.flush()
    sign_evidence_entry(entry)
    created.append(entry)


def _ingest_adapter_results(
    db: Session,
    case_id: uuid.UUID,
    investigator: str,
    adapter: BaseAdapter,
    response: Any,
    query: str,
    created: list[EvidenceEntry],
    tracker: list[str] | None = None,
) -> None:
    if not response.found:
        _add_source_log(db, case_id, adapter.source_name, query, 0, investigator, "", tracker)
        return

    rh = getattr(response, "result_hash", "") or ""
    _add_source_log(
        db,
        case_id,
        adapter.source_name,
        query,
        len(response.results),
        investigator,
        rh,
        tracker,
    )

    if not response.results:
        note = response.error or response.parse_warning or "No matching records found."
        eh = make_evidence_hash(
            case_id,
            adapter.source_name,
            "",
            None,
            None,
            f"__gap__:{query}",
        )
        if is_duplicate(db, case_id, eh):
            return
        entry = EvidenceEntry(
            case_file_id=case_id,
            entry_type="gap_documented",
            title=f"{adapter.source_name}: No records found",
            body=(
                f"Searched {adapter.source_name} for '{query}'. {note} "
                f"Documented absence."
            ),
            source_name=adapter.source_name,
            adapter_name=adapter.source_name,
            entered_by=investigator,
            confidence="confirmed",
            is_absence=True,
            flagged_for_review=False,
            evidence_hash=eh,
        )
        db.add(entry)
        db.flush()
        sign_evidence_entry(entry)
        created.append(entry)
        return

    for result in response.results:
        confidence = result.confidence
        flagged = False
        if result.collision_count > 1:
            confidence = "unverified"
            flagged = True
        try:
            d = _parse_event_date(result.date_of_event)
        except ValueError:
            d = None

        de = str(result.date_of_event) if result.date_of_event else None
        eh = make_evidence_hash(
            case_id,
            result.source_name,
            result.source_url or "",
            de,
            result.amount,
            result.matched_name,
        )
        if is_duplicate(db, case_id, eh):
            continue

        entry = EvidenceEntry(
            case_file_id=case_id,
            entry_type=result.entry_type,
            title=result.title,
            body=result.body,
            source_url=result.source_url,
            source_name=result.source_name,
            adapter_name=adapter.source_name,
            date_of_event=d,
            entered_by=investigator,
            confidence=confidence,
            is_absence=getattr(result, "is_absence", False),
            flagged_for_review=flagged,
            amount=result.amount,
            matched_name=result.matched_name,
            raw_data_json=json.dumps(result.raw_data, sort_keys=True, default=str),
            evidence_hash=eh,
        )
        db.add(entry)
        db.flush()
        sign_evidence_entry(entry)
        created.append(entry)

    if getattr(response, "parse_warning", None):
        _ingest_parse_warning_note(
            db,
            case_id,
            investigator,
            adapter,
            query,
            str(response.parse_warning),
            created,
        )


@router.post("/cases/{case_id}/investigate")
async def run_investigation(
    case_id: uuid.UUID,
    request: InvestigateRequest,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    require_matching_handle(auth_inv, request.investigator_handle)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    created_entries: list[EvidenceEntry] = []
    errors: list[str] = []
    source_check_tracker: list[str] = []
    cache_hits: list[str] = []
    signal_payloads: list[dict[str, Any]] = []

    try:
        _ensure_investigator(db, request.investigator_handle)
        _bump_contributor(db, case_id, request.investigator_handle)
        prof: SubjectProfile | None = None
        if case.subject_type == "public_official":
            prof = _sync_subject_profile(db, case, request, request.investigator_handle)

        # Reinvestigation: replace evidence so adapter fixes (e.g. matched_name) apply;
        # drop existing proximity signals tied to stale entry ids.
        db.execute(delete(Signal).where(Signal.case_file_id == case_id))
        db.execute(delete(EvidenceEntry).where(EvidenceEntry.case_file_id == case_id))
        db.flush()

        await _run_investigation_adapters(
            case=case,
            case_id=case_id,
            request=request,
            db=db,
            prof=prof,
            created_entries=created_entries,
            errors=errors,
            source_check_tracker=source_check_tracker,
            cache_hits=cache_hits,
        )

        substantive = list(
            db.scalars(
                select(EvidenceEntry).where(
                    EvidenceEntry.case_file_id == case_id,
                    EvidenceEntry.is_absence.is_(False),
                )
            ).all()
        )

        proximity_signals = detect_proximity(
            substantive, max_days=request.proximity_days
        )
        contract_prox = detect_contract_proximity(substantive)
        contract_anomalies = detect_contract_anomalies(substantive)

        all_signal_dicts = (
            build_signals_from_proximity(proximity_signals, case_id)
            + build_signals_from_contract_proximity(contract_prox, case_id)
            + build_signals_from_anomalies(contract_anomalies, case_id)
        )

        stored_by_id: dict[uuid.UUID, Signal] = {}
        for sig_dict in all_signal_dicts:
            s = upsert_signal(
                db,
                sig_dict,
                performed_by=request.investigator_handle,
            )
            stored_by_id[s.id] = s
        stored_signals = sorted(
            stored_by_id.values(), key=lambda s: s.weight, reverse=True
        )

        def _bd(s: Signal) -> dict[str, Any]:
            try:
                return json.loads(s.weight_breakdown or "{}")
            except json.JSONDecodeError:
                return {}

        signal_payloads = [
            {
                "id": str(s.id),
                "type": s.signal_type,
                "weight": s.weight,
                "weight_explanation": s.weight_explanation,
                "weight_breakdown": _bd(s),
                "description": s.description,
                "days_between": s.days_between,
                "amount": s.amount,
                "confirmed": s.confirmed,
                "dismissed": s.dismissed,
                "exposure_state": s.exposure_state,
                "proximity_summary": s.proximity_summary,
                "repeat_count": s.repeat_count,
                "is_featured": (s.weight or 0) >= 0.5,
            }
            for s in stored_signals
        ]

        case_refresh = db.scalar(
            select(CaseFile)
            .options(selectinload(CaseFile.evidence_entries))
            .where(CaseFile.id == case_id)
        )
        if case_refresh:
            apply_case_file_signature(
                case_refresh, list(case_refresh.evidence_entries)
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "case_id": str(case_id),
        "subject_searched": request.subject_name,
        "address_searched": request.address,
        "sources_checked": len(source_check_tracker),
        "cache_hits": cache_hits,
        "evidence_entries_created": len(created_entries),
        "signals_detected": len(signal_payloads),
        "errors": errors,
        "signals": signal_payloads,
        "collision_warnings": [
            {
                "entry_id": str(e.id),
                "title": e.title,
                "source": e.source_name,
                "note": "Multiple entities matched this name — human confirmation required",
                "action": f"PATCH /api/v1/evidence/{e.id}/disambiguate",
            }
            for e in created_entries
            if e.flagged_for_review and not e.is_absence
        ],
    }


async def _run_investigation_adapters(
    *,
    case: CaseFile,
    case_id: uuid.UUID,
    request: InvestigateRequest,
    db: Session,
    prof: SubjectProfile | None,
    created_entries: list[EvidenceEntry],
    errors: list[str],
    source_check_tracker: list[str],
    cache_hits: list[str],
) -> None:
    async def get_adapter_response(
        adapter: BaseAdapter, q: str, qt: str = "person"
    ) -> tuple[Any, bool]:
        cached = get_cached_response(db, adapter.source_name, q)
        if cached is not None:
            resp = response_from_cache_dict(cached)
            resp.result_hash = (cached.get("result_hash") or "") + "_cached"
            return resp, True
        resp = await adapter.search(q, qt)
        store_cached_response(db, adapter.source_name, q, resp)
        return resp, False

    async def run_cached(adapter: BaseAdapter, q: str, qt: str = "person") -> None:
        try:
            response, from_cache = await get_adapter_response(adapter, q, qt)
        except Exception as e:
            errors.append(f"{adapter.source_name}: {e!s}")
            _add_source_log(
                db,
                case_id,
                adapter.source_name,
                q,
                0,
                request.investigator_handle,
                "",
                source_check_tracker,
            )
            return
        if from_cache:
            cache_hits.append(adapter.source_name)
        if not response.found:
            errors.append(f"{adapter.source_name}: {response.error or 'request failed'}")
        _ingest_adapter_results(
            db,
            case_id,
            request.investigator_handle,
            adapter,
            response,
            q,
            created_entries,
            source_check_tracker,
        )

    if request.address:
        indy = IndyGISAdapter()
        try:
            indy_resp, indy_cached = await get_adapter_response(
                indy, request.address, "address"
            )
        except Exception as e:
            errors.append(f"{indy.source_name}: {e!s}")
            _add_source_log(
                db,
                case_id,
                indy.source_name,
                request.address,
                0,
                request.investigator_handle,
                "",
                source_check_tracker,
            )
        else:
            if indy_cached:
                cache_hits.append(indy.source_name)
            if not indy_resp.found:
                errors.append(f"{indy.source_name}: {indy_resp.error or 'request failed'}")
            _ingest_adapter_results(
                db,
                case_id,
                request.investigator_handle,
                indy,
                indy_resp,
                request.address,
                created_entries,
                source_check_tracker,
            )
            pins: list[str] = []
            if indy_resp.found and indy_resp.results:
                for r in indy_resp.results:
                    pin = (r.raw_data or {}).get("PARCEL_NUM")
                    if pin:
                        pins.append(str(pin))
            for pin in list(dict.fromkeys(pins))[:3]:
                await run_cached(MarionCountyAssessorAdapter(), pin, "pin")

    if request.fec_committee_id:
        cid = request.fec_committee_id.strip().upper()
        await run_cached(FECAdapter(), cid, "committee")
    else:
        await run_cached(FECAdapter(), request.subject_name, "person")
    await run_cached(USASpendingAdapter(), request.subject_name, "entity")
    await run_cached(IndianaCFAdapter(), request.subject_name, "person")

    if case.subject_type == "public_official":
        bg = request.bioguide_id or (prof.bioguide_id if prof else None)
        cv = CongressVotesAdapter()
        if bg:
            await run_cached(cv, bg, "bioguide_id")
        else:
            await run_cached(cv, request.subject_name, "person")


@router.get("/cases/{case_id}/signals")
def get_signals(case_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    signals = db.scalars(
        select(Signal)
        .where(Signal.case_file_id == case_id)
        .order_by(Signal.weight.desc())
    ).all()

    def _bd(s: Signal) -> dict[str, Any]:
        try:
            return json.loads(s.weight_breakdown or "{}")
        except json.JSONDecodeError:
            return {}

    return {
        "case_id": str(case_id),
        "signal_count": len(signals),
        "signals": [
            {
                "id": str(s.id),
                "type": s.signal_type,
                "weight": s.weight,
                "description": s.description,
                "weight_explanation": s.weight_explanation,
                "weight_breakdown": _bd(s),
                "days_between": s.days_between,
                "amount": s.amount,
                "confirmed": s.confirmed,
                "dismissed": s.dismissed,
                "exposure_state": s.exposure_state,
                "proximity_summary": s.proximity_summary,
                "repeat_count": s.repeat_count,
                "signal_identity_hash": s.signal_identity_hash,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "is_featured": (s.weight or 0) >= 0.5,
            }
            for s in signals
        ],
    }


@router.patch("/signals/{signal_id}/confirm")
def confirm_signal(
    signal_id: uuid.UUID,
    body: ConfirmSignalBody,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    require_matching_handle(auth_inv, body.investigator_handle)
    signal = db.scalar(select(Signal).where(Signal.id == signal_id))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.confirmed = True
    signal.confirmed_by = body.investigator_handle
    db.add(
        SignalAuditLog(
            signal_id=signal.id,
            action="confirmed",
            performed_by=body.investigator_handle,
            old_weight=None,
            new_weight=signal.weight,
            note="investigator confirmed signal",
        )
    )
    add_credibility(db, body.investigator_handle, 2, "confirmed signal")
    db.commit()
    return {"signal_id": str(signal_id), "confirmed": True}


@router.patch("/signals/{signal_id}/dismiss")
def dismiss_signal(
    signal_id: uuid.UUID,
    body: DismissSignalBody,
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    require_matching_handle(auth_inv, body.investigator_handle)
    signal = db.scalar(select(Signal).where(Signal.id == signal_id))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    signal.dismissed = True
    signal.dismissed_by = body.investigator_handle
    signal.dismissed_reason = body.reason
    db.add(
        SignalAuditLog(
            signal_id=signal.id,
            action="dismissed",
            performed_by=body.investigator_handle,
            old_weight=signal.weight,
            new_weight=signal.weight,
            note=body.reason[:500],
        )
    )
    db.commit()
    return {"signal_id": str(signal_id), "dismissed": True}
