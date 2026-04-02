from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from auth import require_api_key, require_matching_handle
from adapters.base import AdapterResponse, BaseAdapter
from adapters.cache import get_cached_response, response_from_cache_dict, store_cached_response
from adapters.congress_votes import CongressVotesAdapter
from adapters.dedup import is_duplicate, make_evidence_hash
from adapters.fec import FECAdapter
from adapters.govinfo_hearings import current_congress_number, search_hearing_witnesses
from adapters.lda import fetch_lda_filings
from adapters.regulations import fetch_docket_comments
from adapters.indiana_cf import IndianaCFAdapter
from adapters.indy_gis import IndyGISAdapter
from adapters.marion_assessor import MarionCountyAssessorAdapter
from adapters.usa_spending import USASpendingAdapter
from database import get_db
from engines.contract_anomaly import detect_contract_anomalies
from engines.contract_proximity import detect_contract_proximity, new_contract_pairing_stats
from engines.signal_scorer import (
    build_signals_from_anomalies,
    build_signals_from_contract_proximity,
    build_signals_from_proximity,
)
from engines.temporal_proximity import (
    DonorCluster,
    detect_proximity,
    new_temporal_pairing_stats,
    refresh_cluster_scoring,
)
from models import (
    CaseContributor,
    CaseFile,
    DonorFingerprint,
    EvidenceEntry,
    InvestigationRun,
    Investigator,
    Signal,
    SignalAuditLog,
    SourceCheckLog,
    SubjectProfile,
)
from signals.dedup import upsert_signal
from payloads import apply_case_file_signature, sign_evidence_entry
from adapters.senate_committees import get_or_refresh_senator_committees
from data.industry_jurisdiction_map import (
    get_chrg_codes_for_committees,
    get_jurisdictions_for_donor,
    jurisdiction_label_matches_committee,
)
from core.credentials import CredentialRegistry
from core.datetime_utils import coerce_utc
from scoring import add_credibility

router = APIRouter(prefix="/api/v1", tags=["investigate"])

# Top donor-cluster signals in POST investigate body (resolved only; see signals_unresolved).
INVESTIGATE_SIGNALS_RESPONSE_LIMIT = 10
MAX_LDA_DONORS_PER_RUN = 25
MAX_WITNESS_CLUSTERS_PER_RUN = 20


def _temporal_core_required_adapters(case: CaseFile) -> list[str]:
    """FEC plus Congress when the subject is a public official (Senate vote path)."""
    req = ["fec"]
    if case.subject_type == "public_official":
        req.append("congress")
    return req


def _failed_required_core_adapters(
    case: CaseFile, source_statuses: list[dict[str, Any]]
) -> list[str]:
    required = _temporal_core_required_adapters(case)
    by_key = {s["adapter"]: s["status"] for s in source_statuses}
    return [
        name
        for name in required
        if by_key.get(name) in ("network_failure", "processing_failure")
    ]


def _required_sources_ready_and_missing(
    case: CaseFile, source_statuses: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    missing = _failed_required_core_adapters(case, source_statuses)
    return (len(missing) == 0, missing)


def _connected_org_for_cluster(
    db: Session, case_id: uuid.UUID, cluster: DonorCluster
) -> str:
    for row in cluster.supporting_pairs:
        fid = row.get("financial_entry_id")
        if not fid:
            continue
        try:
            uid = uuid.UUID(str(fid))
        except ValueError:
            continue
        e = db.get(EvidenceEntry, uid)
        if not e or e.case_file_id != case_id:
            continue
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        org = str(raw.get("contributor_employer") or "").strip()
        if org:
            return org
    return ""


async def _ingest_regulations_and_hearings_for_clusters(
    db: Session,
    case_id: uuid.UUID,
    donor_clusters: list[DonorCluster],
    created_entries: list[EvidenceEntry],
    investigator: str,
    committee_names: list[str],
    source_statuses: list[dict[str, Any]],
) -> None:
    reg_key = CredentialRegistry.get_credential("regulations")
    gov_key = CredentialRegistry.get_credential("govinfo")
    chrg_codes = get_chrg_codes_for_committees(committee_names)
    congress = current_congress_number()

    if not reg_key:
        source_statuses.append(
            {
                "adapter": "regulations",
                "display_name": "Regulations.gov",
                "status": "credential_unavailable",
                "detail": "REGULATIONS_GOV_API_KEY not set and no file credential",
            }
        )
    if not gov_key:
        source_statuses.append(
            {
                "adapter": "govinfo",
                "display_name": "GovInfo",
                "status": "credential_unavailable",
                "detail": "GOVINFO_API_KEY not set and no file credential",
            }
        )

    n_witness = 0
    for cluster in donor_clusters:
        if float(cluster.relevance_score) <= 0.3:
            continue
        if n_witness >= MAX_WITNESS_CLUSTERS_PER_RUN:
            break
        n_witness += 1
        org = _connected_org_for_cluster(db, case_id, cluster)
        donor = cluster.donor_display

        if reg_key:
            try:
                matches = await fetch_docket_comments(
                    donor, org, committee_names, reg_key
                )
            except Exception:
                matches = []
            if matches:
                cluster.has_regulatory_comment = True
                if any(m.get("match_confidence") == "confirmed" for m in matches):
                    cluster.regulatory_comment_confidence = "confirmed"
                else:
                    cluster.regulatory_comment_confidence = "probable"
                for m in matches[:8]:
                    cid = str(m.get("comment_id") or "")
                    if not cid:
                        continue
                    eh = make_evidence_hash(
                        case_id,
                        "Regulations.gov",
                        cid,
                        None,
                        None,
                        cluster.donor_key,
                    )
                    if is_duplicate(db, case_id, eh):
                        continue
                    body = json.dumps(m, sort_keys=True, default=str)
                    entry = EvidenceEntry(
                        case_file_id=case_id,
                        entry_type="regulatory_comment",
                        title=(
                            f"Regulations.gov comment "
                            f"({m.get('match_confidence')}): "
                            f"{m.get('docket_title') or cid}"
                        ),
                        body=str(m.get("docket_title") or body)[:8000],
                        source_url=f"https://www.regulations.gov/comment/{cid}",
                        source_name="Regulations.gov",
                        adapter_name="Regulations.gov",
                        entered_by=investigator,
                        confidence=str(m.get("match_confidence") or "probable"),
                        raw_data_json=body,
                        evidence_hash=eh,
                    )
                    db.add(entry)
                    db.flush()
                    sign_evidence_entry(entry)
                    created_entries.append(entry)
                    cluster.witness_evidence_ids.append(entry.id)

        if gov_key and chrg_codes:
            try:
                gres = await search_hearing_witnesses(
                    donor, org, chrg_codes, congress, gov_key
                )
            except Exception:
                gres = {"hits": [], "searched": False, "matched": False}
            if gres.get("searched"):
                if gres.get("matched") and gres.get("hits"):
                    hit = gres["hits"][0]
                    cluster.has_hearing_appearance = True
                    cluster.hearing_match_confidence = str(
                        hit.get("match_confidence") or "probable"
                    )
                    hid = str(hit.get("package_id") or "")
                    eh2 = make_evidence_hash(
                        case_id,
                        "GovInfo",
                        hid,
                        None,
                        None,
                        cluster.donor_key,
                    )
                    if not is_duplicate(db, case_id, eh2):
                        raw_h = json.dumps(hit, sort_keys=True, default=str)
                        hent = EvidenceEntry(
                            case_file_id=case_id,
                            entry_type="hearing_witness",
                            title=f"Hearing witness match: {hit.get('hearing_title') or hid}",
                            body=raw_h[:8000],
                            source_url=str(hit.get("source_url") or ""),
                            source_name="GovInfo (CHRG)",
                            adapter_name="GovInfo",
                            entered_by=investigator,
                            confidence=str(hit.get("match_confidence") or "probable"),
                            raw_data_json=raw_h,
                            evidence_hash=eh2,
                        )
                        db.add(hent)
                        db.flush()
                        sign_evidence_entry(hent)
                        created_entries.append(hent)
                        cluster.witness_evidence_ids.append(hent.id)
                else:
                    abs_key = "|".join(
                        [
                            cluster.donor_key,
                            ",".join(sorted(chrg_codes)),
                            str(congress),
                        ]
                    )
                    eh3 = make_evidence_hash(
                        case_id,
                        "GovInfo",
                        f"hearing_absence|{abs_key}",
                        None,
                        None,
                        cluster.donor_key,
                    )
                    if not is_duplicate(db, case_id, eh3):
                        absence_payload = {
                            "searched": True,
                            "match": False,
                            "donor_key": cluster.donor_key,
                            "committee_codes": chrg_codes,
                            "congress": congress,
                        }
                        aent = EvidenceEntry(
                            case_file_id=case_id,
                            entry_type="hearing_absence",
                            title="GovInfo hearing search — no witness name match",
                            body=json.dumps(absence_payload, sort_keys=True),
                            source_url="https://api.govinfo.gov/",
                            source_name="GovInfo (CHRG)",
                            adapter_name="GovInfo",
                            entered_by=investigator,
                            confidence="absence",
                            raw_data_json=json.dumps(absence_payload, sort_keys=True),
                            evidence_hash=eh3,
                        )
                        db.add(aent)
                        db.flush()
                        sign_evidence_entry(aent)
                        created_entries.append(aent)
                        cluster.witness_evidence_ids.append(aent.id)

        refresh_cluster_scoring(cluster)

    if reg_key:
        source_statuses.append(
            {
                "adapter": "regulations",
                "display_name": "Regulations.gov",
                "status": "clean",
                "detail": None,
            }
        )
    if gov_key:
        source_statuses.append(
            {
                "adapter": "govinfo",
                "display_name": "GovInfo",
                "status": "clean",
                "detail": None,
            }
        )


def _adapter_registry_key(adapter: BaseAdapter) -> str:
    if isinstance(adapter, FECAdapter):
        return "fec"
    if isinstance(adapter, CongressVotesAdapter):
        return "congress"
    if isinstance(adapter, IndyGISAdapter):
        return "indy_gis"
    if isinstance(adapter, MarionCountyAssessorAdapter):
        return "marion_assessor"
    if isinstance(adapter, USASpendingAdapter):
        return "usaspending"
    if isinstance(adapter, IndianaCFAdapter):
        return "indiana_cf"
    return "other"


def _append_source_status(
    bucket: list[dict[str, Any]],
    registry_key: str,
    response: AdapterResponse,
    from_cache: bool,
) -> None:
    if from_cache:
        bucket.append(
            {
                "adapter": registry_key,
                "display_name": response.source_name,
                "status": "cached",
                "detail": None,
            }
        )
        return
    if response.empty_success:
        bucket.append(
            {
                "adapter": registry_key,
                "display_name": response.source_name,
                "status": "clean",
                "detail": response.parse_warning
                or response.error
                or "0 records returned",
            }
        )
        return
    if response.error:
        kind = (response.error_kind or "processing").lower()
        st = "network_failure" if kind == "network" else "processing_failure"
        bucket.append(
            {
                "adapter": registry_key,
                "display_name": response.source_name,
                "status": st,
                "detail": response.error,
            }
        )
        return
    mode = response.credential_mode
    if mode == "fallback":
        st = "fallback"
    elif mode == "credential_unavailable":
        st = "credential_unavailable"
    elif mode == "ok":
        st = "clean"
    else:
        st = "clean"
    bucket.append(
        {
            "adapter": registry_key,
            "display_name": response.source_name,
            "status": st,
            "detail": response.error,
        }
    )


def _donor_key_from_signal_row(s: Signal) -> str:
    bd = _signal_breakdown_local(s)
    if bd.get("donor"):
        return str(bd["donor"]).strip().lower()
    return (s.actor_a or "").strip().lower()


async def _ingest_lda_for_unique_donors(
    db: Session,
    case_id: uuid.UUID,
    created_entries: list[EvidenceEntry],
    investigator: str,
    source_statuses: list[dict[str, Any]],
) -> None:
    seen_pairs: set[tuple[str, str]] = set()
    for e in created_entries:
        if e.entry_type != "financial_connection" or e.source_name != "FEC":
            continue
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        donor = str(raw.get("contributor_name") or "").strip()
        org = str(raw.get("contributor_employer") or "").strip()
        if not donor:
            continue
        key = (donor.lower(), org.lower())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        if len(seen_pairs) > MAX_LDA_DONORS_PER_RUN:
            break
        try:
            filings = await fetch_lda_filings(donor, org)
        except Exception:
            continue
        donor_key_norm = donor.strip().lower()
        for f in filings:
            uid = f.get("filing_uuid")
            if not uid:
                continue
            rd = {
                **f,
                "donor_key": donor_key_norm,
                "lda_detail_url": f"https://lda.senate.gov/filings/{uid}/",
            }
            eh = make_evidence_hash(
                case_id,
                "Senate LDA",
                str(uid),
                None,
                None,
                donor_key_norm,
            )
            if is_duplicate(db, case_id, eh):
                continue
            fn = str(f.get("filing_year") or "")
            rn = str(f.get("registrant_name") or "")
            cn = str(f.get("client_name") or "")
            entry = EvidenceEntry(
                case_file_id=case_id,
                entry_type="lobbying_filing",
                title=f"Senate LDA Lobbying Filing ({fn}): {rn or cn or uid}",
                body=(
                    f"LDA filing {uid}. Registrant: {rn}. Client: {cn}. "
                    f"Linked research donor key: {donor_key_norm}."
                ),
                source_url=f"https://lda.senate.gov/filings/{uid}/",
                source_name="Senate LDA",
                adapter_name="Senate LDA",
                entered_by=investigator,
                confidence="confirmed",
                is_absence=False,
                flagged_for_review=False,
                raw_data_json=json.dumps(rd, sort_keys=True, default=str),
                evidence_hash=eh,
            )
            db.add(entry)
            db.flush()
            sign_evidence_entry(entry)
            created_entries.append(entry)
    source_statuses.append(
        {
            "adapter": "lda",
            "display_name": "Senate LDA (Lobbying Disclosure)",
            "status": "clean",
            "detail": "Public API; no key required",
        }
    )


def _apply_cross_case_baseline_and_fingerprints(
    db: Session,
    case_id: uuid.UUID,
    stored_signals: list[Signal],
    prev_top: list[dict[str, Any]],
    bioguide_id: str | None,
) -> None:
    prev_by_donor = {
        str(r.get("donor_key") or "").strip().lower(): r
        for r in prev_top
        if isinstance(r, dict) and r.get("donor_key")
    }
    prev_top_keys = [
        str(r.get("donor_key") or "").strip().lower()
        for r in prev_top[:10]
        if isinstance(r, dict) and r.get("donor_key")
    ]
    temporal_resolved = [
        s
        for s in stored_signals
        if s.signal_type == "temporal_proximity" and s.exposure_state != "unresolved"
    ]
    temporal_sorted = sorted(
        temporal_resolved, key=lambda s: float(s.weight or 0.0), reverse=True
    )

    for i, s in enumerate(temporal_sorted):
        dk = _donor_key_from_signal_row(s)
        if not dk:
            continue
        cc_count = (
            db.scalar(
                select(func.count(func.distinct(DonorFingerprint.case_file_id)))
                .select_from(DonorFingerprint)
                .where(
                    DonorFingerprint.normalized_donor_key == dk,
                    DonorFingerprint.case_file_id != case_id,
                )
            )
            or 0
        )
        other_off = db.scalars(
            select(DonorFingerprint.official_name)
            .where(
                DonorFingerprint.normalized_donor_key == dk,
                DonorFingerprint.case_file_id != case_id,
            )
            .distinct()
        ).all()
        s.cross_case_appearances = int(cc_count)
        s.cross_case_officials = json.dumps(
            [str(x) for x in other_off if x], separators=(",", ":")
        )
        prev = prev_by_donor.get(dk)
        if prev is not None and prev.get("weight") is not None:
            s.weight_delta = float(s.weight or 0.0) - float(prev["weight"])
        else:
            s.weight_delta = None
        s.first_appearance = prev is None
        s.new_top_signal = (i < 10) and (dk not in prev_top_keys)
        db.add(s)

    for s in temporal_sorted[:10]:
        dk = _donor_key_from_signal_row(s)
        if not dk:
            continue
        db.add(
            DonorFingerprint(
                normalized_donor_key=dk,
                case_file_id=case_id,
                signal_id=s.id,
                weight=float(s.weight or 0.0),
                official_name=(s.actor_b or "").strip() or "Unknown official",
                bioguide_id=(bioguide_id or "").strip() or None,
            )
        )
    db.flush()


def _signal_breakdown_local(s: Signal) -> dict[str, Any]:
    try:
        return json.loads(s.weight_breakdown or "{}")
    except json.JSONDecodeError:
        return {}


def _signal_to_response_dict(s: Signal) -> dict[str, Any]:
    bd = _signal_breakdown_local(s)
    featured = (s.weight or 0) >= 0.5 and s.exposure_state != "unresolved"
    conf_checks: dict[str, Any] = {}
    if getattr(s, "confirmation_checks", None):
        try:
            conf_checks = json.loads(s.confirmation_checks)
        except json.JSONDecodeError:
            conf_checks = {}
    conf_basis: list[Any] = []
    if getattr(s, "confirmation_basis", None):
        try:
            raw_basis = json.loads(s.confirmation_basis)
            conf_basis = raw_basis if isinstance(raw_basis, list) else []
        except json.JSONDecodeError:
            conf_basis = []
    rel_sc = float(getattr(s, "relevance_score", 0.0) or bd.get("relevance_score") or 0.0)
    out: dict[str, Any] = {
        "id": str(s.id),
        "weight": s.weight,
        "weight_explanation": s.weight_explanation,
        "weight_breakdown": bd,
        "description": s.description,
        "days_between": s.days_between,
        "amount": s.amount,
        "confirmed": s.confirmed,
        "dismissed": s.dismissed,
        "exposure_state": s.exposure_state,
        "proximity_summary": s.proximity_summary,
        "repeat_count": s.repeat_count,
        "direction_verified": bool(getattr(s, "direction_verified", True)),
        "signal_identity_hash": s.signal_identity_hash,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "is_featured": featured,
        "relevance_score": rel_sc,
        "confirmation_checks": conf_checks,
        "confirmation_basis": conf_basis,
        "jurisdictional_match": bool(conf_checks.get("jurisdictional_match"))
        if conf_checks
        else bool(bd.get("has_jurisdictional_match")),
    }
    if bd.get("kind") == "donor_cluster":
        out.update(
            {
                "type": "donor_cluster",
                "signal_type": s.temporal_class,
                "donor": bd.get("donor"),
                "official": bd.get("official"),
                "total_amount": bd.get("total_amount"),
                "donation_count": bd.get("donation_count"),
                "vote_count": bd.get("vote_count"),
                "pair_count": bd.get("pair_count"),
                "min_gap_days": bd.get("min_gap_days"),
                "median_gap_days": bd.get("median_gap_days"),
                "exemplar_vote": bd.get("exemplar_vote"),
                "exemplar_direction": bd.get("exemplar_direction"),
                "has_lda_filing": bool(bd.get("has_lda_filing")),
                "has_regulatory_comment": bool(bd.get("has_regulatory_comment")),
                "has_hearing_appearance": bool(bd.get("has_hearing_appearance")),
            }
        )
    xco: list[str] = []
    if getattr(s, "cross_case_officials", None):
        try:
            raw_x = json.loads(s.cross_case_officials)
            xco = raw_x if isinstance(raw_x, list) else []
        except json.JSONDecodeError:
            xco = []
    out["cross_case_appearances"] = int(getattr(s, "cross_case_appearances", 0) or 0)
    out["cross_case_officials"] = xco
    wd = getattr(s, "weight_delta", None)
    out["weight_delta"] = float(wd) if wd is not None else None
    out["new_top_signal"] = bool(getattr(s, "new_top_signal", False))
    out["first_appearance"] = bool(getattr(s, "first_appearance", False))

    if bd.get("kind") != "donor_cluster":
        out["type"] = s.signal_type
        out["signal_type"] = s.signal_type
    return out


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
    coerced = coerce_utc(str(s).strip())
    if coerced is None:
        return None
    return coerced.date()


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


def _fec_committee_legal_name(created_entries: list[EvidenceEntry]) -> str | None:
    for e in created_entries:
        if e.entry_type != "financial_connection" or e.source_name != "FEC":
            continue
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        c = raw.get("committee")
        if isinstance(c, dict) and c.get("name"):
            return str(c["name"]).strip()
    return None


async def _enrich_fec_evidence_jurisdiction(
    db: Session,
    bioguide_id: str | None,
    created_entries: list[EvidenceEntry],
) -> None:
    if not bioguide_id:
        return
    sen_rows = await get_or_refresh_senator_committees(db, bioguide_id.strip())
    for entry in created_entries:
        if entry.entry_type != "financial_connection" or entry.source_name != "FEC":
            continue
        try:
            raw = json.loads(entry.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        donor = raw.get("contributor_name") or ""
        org = raw.get("contributor_employer") or raw.get("contributor_organization") or ""
        jurs = get_jurisdictions_for_donor(str(donor), str(org))
        matched: list[str] = []
        for j in jurs:
            for sr in sen_rows:
                if jurisdiction_label_matches_committee(
                    j, sr.committee_name, sr.committee_code
                ):
                    if j not in matched:
                        matched.append(j)
        entry.jurisdictional_match = bool(matched)
        entry.matched_committees = json.dumps(matched)
        db.add(entry)
        sign_evidence_entry(entry)


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


@router.post("/cases/{case_id}/investigate", response_model=None)
async def run_investigation(
    case_id: uuid.UUID,
    request: InvestigateRequest,
    include_unresolved: bool = Query(
        False,
        description="If true, include signals_unresolved_detail for quarantined clusters.",
    ),
    db: Session = Depends(get_db),
    auth_inv: Investigator = Depends(require_api_key),
) -> dict[str, Any] | JSONResponse:
    require_matching_handle(auth_inv, request.investigator_handle)
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    prior_signal_count = (
        db.scalar(
            select(func.count()).select_from(Signal).where(Signal.case_file_id == case_id)
        )
        or 0
    )

    created_entries: list[EvidenceEntry] = []
    errors: list[str] = []
    source_check_tracker: list[str] = []
    cache_hits: list[str] = []
    source_statuses: list[dict[str, Any]] = []
    unresolved_payload: list[dict[str, Any]] | None = None

    prev_run_row = db.scalar(
        select(InvestigationRun)
        .where(InvestigationRun.case_file_id == case_id)
        .order_by(InvestigationRun.run_at.desc())
    )
    prev_top: list[dict[str, Any]] = []
    if prev_run_row:
        try:
            raw_prev = json.loads(prev_run_row.top_donors or "[]")
            prev_top = raw_prev if isinstance(raw_prev, list) else []
        except json.JSONDecodeError:
            prev_top = []

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
            source_statuses=source_statuses,
        )

        failed_required = _failed_required_core_adapters(case, source_statuses)
        if failed_required:
            db.rollback()
            detail_msg = (
                f"Required adapters failed: {failed_required}. "
                "Evidence not promoted. Prior case state preserved."
            )
            rs_ready, rs_missing = _required_sources_ready_and_missing(
                case, source_statuses
            )
            return JSONResponse(
                status_code=422,
                content={
                    "detail": detail_msg,
                    "case_id": str(case_id),
                    "subject_searched": request.subject_name,
                    "address_searched": request.address,
                    "sources_checked": len(source_check_tracker),
                    "cache_hits": cache_hits,
                    "evidence_entries_created": 0,
                    "signals_detected": 0,
                    "signals_unresolved": 0,
                    "errors": errors,
                    "signals": [],
                    "collision_warnings": [],
                    "source_statuses": source_statuses,
                    "pairing_diagnostics": {
                        "temporal": new_temporal_pairing_stats(),
                        "contract": new_contract_pairing_stats(),
                    },
                    "required_sources_ready": rs_ready,
                    "required_sources_missing": rs_missing,
                },
            )

        bg_for_intel = request.bioguide_id or (prof.bioguide_id if prof else None)
        await _ingest_lda_for_unique_donors(
            db,
            case_id,
            created_entries,
            request.investigator_handle,
            source_statuses,
        )
        await _enrich_fec_evidence_jurisdiction(db, bg_for_intel, created_entries)

        substantive = list(
            db.scalars(
                select(EvidenceEntry).where(
                    EvidenceEntry.case_file_id == case_id,
                    EvidenceEntry.is_absence.is_(False),
                )
            ).all()
        )

        fec_committee = _fec_committee_legal_name(created_entries)
        committee_label = (fec_committee or "").strip()
        if not committee_label:
            committee_label = (request.fec_committee_id or "").strip()
        if not committee_label:
            committee_label = (case.subject_name or "").strip()
        committee_names_witness: list[str] = []
        if bg_for_intel:
            sen_for_witness = await get_or_refresh_senator_committees(db, bg_for_intel)
            committee_names_witness = [r.committee_name for r in sen_for_witness]

        donor_clusters, temporal_pairing_stats = detect_proximity(
            substantive,
            max_days=request.proximity_days,
            committee_label=committee_label,
        )
        await _ingest_regulations_and_hearings_for_clusters(
            db,
            case_id,
            donor_clusters,
            created_entries,
            request.investigator_handle,
            committee_names_witness,
            source_statuses,
        )
        contract_prox, contract_pairing_stats = detect_contract_proximity(substantive)
        contract_anomalies = detect_contract_anomalies(substantive)

        all_signal_dicts = (
            build_signals_from_proximity(donor_clusters, case_id)
            + build_signals_from_contract_proximity(contract_prox, case_id)
            + build_signals_from_anomalies(contract_anomalies, case_id)
        )

        for sig_dict in all_signal_dicts:
            if not sig_dict.get("direction_verified", True):
                raise ValueError("Signal failed direction_verified gate — not persisting.")

        stored_by_id: dict[uuid.UUID, Signal] = {}
        for sig_dict in all_signal_dicts:
            s = upsert_signal(
                db,
                sig_dict,
                performed_by=request.investigator_handle,
            )
            stored_by_id[s.id] = s
        stored_signals = sorted(
            stored_by_id.values(), key=lambda s: s.weight or 0.0, reverse=True
        )

        _apply_cross_case_baseline_and_fingerprints(
            db,
            case_id,
            stored_signals,
            prev_top,
            bg_for_intel,
        )

        resolved_signals = [
            s for s in stored_signals if s.exposure_state != "unresolved"
        ]
        unresolved_signals = [
            s for s in stored_signals if s.exposure_state == "unresolved"
        ]
        signals_detected_total = len(resolved_signals)
        signals_unresolved_total = len(unresolved_signals)

        signal_payloads_response = [
            _signal_to_response_dict(s)
            for s in resolved_signals[:INVESTIGATE_SIGNALS_RESPONSE_LIMIT]
        ]
        if include_unresolved and unresolved_signals:
            unresolved_payload = [
                _signal_to_response_dict(s)
                for s in sorted(
                    unresolved_signals, key=lambda x: x.weight or 0.0, reverse=True
                )[:25]
            ]

        temporal_for_run = [
            s
            for s in stored_signals
            if s.signal_type == "temporal_proximity" and s.exposure_state != "unresolved"
        ]
        temporal_sorted_run = sorted(
            temporal_for_run, key=lambda s: float(s.weight or 0.0), reverse=True
        )
        top_donor_payload: list[dict[str, Any]] = []
        for s in temporal_sorted_run[:10]:
            bd_r = _signal_breakdown_local(s)
            top_donor_payload.append(
                {
                    "donor_key": _donor_key_from_signal_row(s),
                    "weight": float(s.weight or 0.0),
                    "min_gap_days": bd_r.get("min_gap_days"),
                    "total_amount": bd_r.get("total_amount"),
                }
            )
        db.add(
            InvestigationRun(
                case_file_id=case_id,
                signals_detected=signals_detected_total,
                top_donors=json.dumps(top_donor_payload, separators=(",", ":"), default=str),
            )
        )

        case_refresh = db.scalar(
            select(CaseFile)
            .options(selectinload(CaseFile.evidence_entries))
            .where(CaseFile.id == case_id)
        )
        if case_refresh:
            case_refresh.last_source_statuses = json.dumps(
                source_statuses, separators=(",", ":"), default=str
            )
            apply_case_file_signature(
                case_refresh, list(case_refresh.evidence_entries)
            )

        new_signal_count = len(stored_signals)
        if new_signal_count == 0 and prior_signal_count > 0:
            db.rollback()
            raise HTTPException(
                status_code=422,
                detail=(
                    "Run produced zero signals. Prior signals preserved. "
                    "Investigate the enrichment pipeline before re-running."
                ),
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Investigation run failed: {e!s}. Prior signals preserved.",
        ) from e

    required_sources_ready, required_sources_missing = (
        _required_sources_ready_and_missing(case, source_statuses)
    )

    return {
        "case_id": str(case_id),
        "subject_searched": request.subject_name,
        "address_searched": request.address,
        "sources_checked": len(source_check_tracker),
        "cache_hits": cache_hits,
        "evidence_entries_created": len(created_entries),
        "signals_detected": signals_detected_total,
        "signals_unresolved": signals_unresolved_total,
        # TODO: remove after 8.3 verified
        "pairing_diagnostics": {
            "temporal": temporal_pairing_stats,
            "contract": contract_pairing_stats,
        },
        "required_sources_ready": required_sources_ready,
        "required_sources_missing": required_sources_missing,
        "errors": errors,
        "signals": signal_payloads_response,
        **(
            {"signals_unresolved_detail": unresolved_payload}
            if unresolved_payload is not None
            else {}
        ),
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
        "source_statuses": source_statuses,
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
    source_statuses: list[dict[str, Any]],
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
        rk = _adapter_registry_key(adapter)
        try:
            response, from_cache = await get_adapter_response(adapter, q, qt)
        except (httpx.HTTPError, httpx.RequestError) as e:
            errors.append(f"{adapter.source_name}: network failure — {e!s}")
            source_statuses.append(
                {
                    "adapter": rk,
                    "display_name": adapter.source_name,
                    "status": "network_failure",
                    "detail": str(e),
                }
            )
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
        except Exception as e:
            errors.append(f"{adapter.source_name}: processing failure — {e!s}")
            source_statuses.append(
                {
                    "adapter": rk,
                    "display_name": adapter.source_name,
                    "status": "processing_failure",
                    "detail": str(e),
                }
            )
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
        _append_source_status(source_statuses, rk, response, from_cache)
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
            source_statuses.append(
                {
                    "adapter": "indy_gis",
                    "display_name": indy.source_name,
                    "status": "credential_unavailable",
                    "detail": str(e),
                }
            )
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
            _append_source_status(source_statuses, "indy_gis", indy_resp, indy_cached)
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
def get_signals(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500, description="Max signals to return (by weight desc)"),
    include_unresolved: bool = Query(
        False,
        description="If false (default), quarantined (unresolved) clusters are omitted from the list.",
    ),
) -> dict[str, Any]:
    case = db.scalar(select(CaseFile).where(CaseFile.id == case_id))
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    total_ct = (
        db.scalar(
            select(func.count())
            .select_from(Signal)
            .where(Signal.case_file_id == case_id)
        )
        or 0
    )
    unresolved_ct = (
        db.scalar(
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.case_file_id == case_id,
                Signal.exposure_state == "unresolved",
            )
        )
        or 0
    )
    resolved_ct = int(total_ct) - int(unresolved_ct)

    base_filter = Signal.case_file_id == case_id
    if not include_unresolved:
        stmt = (
            select(Signal)
            .where(base_filter, Signal.exposure_state != "unresolved")
            .order_by(Signal.weight.desc())
            .limit(limit)
        )
    else:
        stmt = (
            select(Signal)
            .where(base_filter)
            .order_by(Signal.weight.desc())
            .limit(limit)
        )
    signals = db.scalars(stmt).all()

    return {
        "case_id": str(case_id),
        "signal_count": int(total_ct),
        "signals_resolved_count": resolved_ct,
        "signals_unresolved_count": int(unresolved_ct),
        "limit": limit,
        "include_unresolved": include_unresolved,
        "signals": [_signal_to_response_dict(s) for s in signals],
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
