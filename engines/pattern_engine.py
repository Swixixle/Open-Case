"""
Pattern Engine — cross-official donor pattern detection.

Rules are versioned and typed. PatternAlerts are read-side only.
They document what public records show across investigations.
They assert nothing about intent or causation.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from signals.dedup import _parse_evidence_id_list

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import (
    DonorFingerprint,
    EvidenceEntry,
    PatternAlertRecord,
    SenatorCommittee,
    Signal,
    SubjectProfile,
)

PATTERN_ENGINE_VERSION = "1.2"

# Rule IDs — increment when logic changes, never reuse
RULE_COMMITTEE_SWEEP = "COMMITTEE_SWEEP_V1"
RULE_FINGERPRINT_BLOOM = "FINGERPRINT_BLOOM_V1"
RULE_SOFT_BUNDLE = "SOFT_BUNDLE_V1"
RULE_SECTOR_CONVERGENCE = "SECTOR_CONVERGENCE_V1"
RULE_GEO_MISMATCH = "GEO_MISMATCH_V1"
RULE_DISBURSEMENT_LOOP = "DISBURSEMENT_LOOP_V1"
RULE_REVOLVING_DOOR = "REVOLVING_DOOR_V1"

COMMITTEE_SWEEP_MIN_OFFICIALS = 3
COMMITTEE_SWEEP_MAX_WINDOW_DAYS = 14
FINGERPRINT_BLOOM_MIN_CASES = 4
FINGERPRINT_BLOOM_MIN_RELEVANCE = 0.3

SOFT_BUNDLE_MIN_UNIQUE_DONORS = 3
SOFT_BUNDLE_MAX_SPAN_DAYS = 7
SOFT_BUNDLE_MIN_AGGREGATE = 1000.0

SECTOR_CONVERGENCE_MIN_DONORS = 3
SECTOR_CONVERGENCE_WINDOW_DAYS = 14
SECTOR_CONVERGENCE_MIN_AGGREGATE = 5000.0

GEO_MISMATCH_MIN_DONORS = 5
GEO_MISMATCH_WINDOW_DAYS = 14
GEO_MISMATCH_OUT_OF_STATE_THRESHOLD = 0.75
GEO_MISMATCH_MIN_AGGREGATE = 1000.0

# DC + org-style name → unknown (not out-of-state); see _geo_bucket
_GEO_DC_UNKNOWN_NAME_MARKERS = ("PAC", "COMMITTEE", "ASSOCIATION", "COUNCIL", "INSTITUTE")

DISBURSEMENT_LOOP_WINDOW_DAYS = 30
DISBURSEMENT_LOOP_MIN_AMOUNT = 5000.0

REVOLVING_DOOR_MIN_MATCHED_DONORS = 1
REVOLVING_DOOR_MIN_LDA_FILING_YEAR = 2024
REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN = 6

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "pharma": [
        "pharma",
        "pharmaceutical",
        "biotech",
        "drug",
        "medicine",
        "health insurance",
        "medical",
        "biologics",
        "clinical",
        "therapeutics",
        "life sciences",
    ],
    "finance": [
        "bank",
        "financial",
        "insurance",
        "investment",
        "capital",
        "credit union",
        "mortgage",
        "lending",
        "asset management",
        "securities",
        "wall street",
        "hedge fund",
        "private equity",
        "wealth management",
        "brokerage",
    ],
    "energy": [
        "oil",
        "gas",
        "petroleum",
        "pipeline",
        "coal",
        "mining",
        "refinery",
        "energy",
        "utilities",
        "electric",
        "power",
        "fracking",
        "fossil",
        "natural gas",
        "liquefied",
        "lng",
    ],
    "defense": [
        "defense",
        "aerospace",
        "military",
        "weapons",
        "contractor",
        "lockheed",
        "raytheon",
        "boeing",
        "northrop",
        "general dynamics",
        "navy",
        "army",
    ],
    "real_estate": [
        "real estate",
        "realty",
        "property",
        "housing",
        "construction",
        "builder",
        "developer",
        "mortgage",
        "home builder",
        "reit",
        "commercial property",
    ],
    "tech": [
        "technology",
        "software",
        "hardware",
        "telecom",
        "semiconductor",
        "data",
        "internet",
        "cyber",
        "cloud",
        "ai",
        "artificial intelligence",
        "chip",
    ],
    "agriculture": [
        "farm",
        "agriculture",
        "crop",
        "livestock",
        "grain",
        "soybean",
        "corn",
        "wheat",
        "dairy",
        "poultry",
        "beef",
        "pork",
        "cotton",
        "tobacco",
    ],
    "legal": [
        "law firm",
        "attorney",
        "legal",
        "counsel",
        "litigation",
        "lawyer",
    ],
}

VOTE_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "pharma": ["drug", "pharma", "health", "medicare", "medicaid", "prescription", "fda", "biotech"],
    "finance": [
        "bank",
        "financial",
        "tax",
        "credit",
        "lending",
        "securities",
        "minimum tax",
        "corporate",
    ],
    "energy": ["energy", "oil", "gas", "pipeline", "coal", "climate", "carbon", "emissions", "epa"],
    "defense": [
        "defense",
        "military",
        "armed forces",
        "pentagon",
        "national security",
        "weapons",
        "navy",
        "army",
    ],
    "real_estate": ["housing", "real estate", "mortgage", "zoning", "construction", "hud"],
    "tech": ["technology", "internet", "data", "privacy", "cyber", "semiconductor", "telecom"],
    "agriculture": ["farm", "agriculture", "food", "crop", "rural", "usda"],
    "legal": ["court", "judiciary", "legal", "attorney general", "doj"],
}

ISSUE_CODE_TO_SECTOR: dict[str, str] = {
    "TAX": "finance",
    "FIN": "finance",
    "BNK": "finance",
    "HCR": "pharma",
    "PHR": "pharma",
    "DEF": "defense",
    "ENV": "energy",
    "ENE": "energy",
    "AGR": "agriculture",
    "HOU": "real_estate",
    "TEC": "tech",
}

SENATOR_HOME_STATE: dict[str, str] = {
    "S001198": "AK",
    "C001095": "AR",
    "E000295": "IA",
    "W000779": "OR",
    "C000880": "ID",
    "G000386": "IA",
    "C000127": "WA",
    "B001236": "AR",
    "Y000064": "IN",
    "B001306": "IN",
}

FEC_FUNDRAISING_DEADLINES: list[tuple[int, int]] = [
    (3, 31),
    (6, 30),
    (9, 30),
    (12, 31),
]
DEADLINE_WINDOW_DAYS = 5

PATTERN_ALERT_DISCLAIMER = (
    "This alert documents donor appearance across public records. "
    "It does not assert coordination, intent, or quid pro quo."
)


@dataclass
class PatternAlert:
    rule_id: str
    pattern_version: str
    donor_entity: str
    matched_officials: list[str]
    matched_case_ids: list[str]
    committee: str
    window_days: int | None
    evidence_refs: list[str]
    fired_at: datetime
    disclaimer: str = PATTERN_ALERT_DISCLAIMER
    donation_window_start: date | None = None
    donation_window_end: date | None = None
    aggregate_amount: float | None = None
    cluster_size: int | None = None
    amount_diversification: float | None = None
    days_to_nearest_vote: int | None = None
    nearest_vote_id: str | None = None
    nearest_vote_date: str | None = None
    nearest_vote_description: str | None = None
    nearest_vote_result: str | None = None
    nearest_vote_question: str | None = None
    proximity_to_vote_score: float | None = None
    deadline_adjacent: bool = False
    deadline_discount: float = 1.0
    deadline_note: str | None = None
    suspicion_score: float | None = None
    sector: str | None = None
    sector_donor_count: int | None = None
    sector_aggregate: float | None = None
    sector_concentration: float | None = None
    sector_vote_match: bool | None = None
    senator_state: str | None = None
    out_of_state_ratio: float | None = None
    out_of_state_count: int | None = None
    in_state_count: int | None = None
    unknown_state_count: int | None = None
    top_donor_states: list[str] | None = None
    disbursing_committee: str | None = None
    recipient_committee: str | None = None
    disbursement_amount: float | None = None
    disbursement_date: str | None = None
    loop_confirmed: bool | None = None
    matched_donor: str | None = None
    matched_lda_registrant: str | None = None
    matched_issue_codes: list[str] | None = None
    revolving_door_vote_relevant: bool | None = None
    lda_filing_year: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _signal_breakdown_json(s: Signal) -> dict[str, Any]:
    try:
        raw = json.loads(s.weight_breakdown or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _donor_display_for_signal(s: Signal, normalized_fallback: str) -> str:
    bd = _signal_breakdown_json(s)
    d = bd.get("donor") or s.actor_a
    if d and str(d).strip():
        return str(d).strip()
    return normalized_fallback


def proximity_to_vote_score_from_days(days_to_nearest: int | None) -> float:
    """Maps calendar distance from bundle midpoint to nearest vote (tiers 0.1–1.0)."""
    if days_to_nearest is None:
        return 0.1
    if days_to_nearest <= 7:
        return 1.0
    if days_to_nearest <= 14:
        return 0.75
    if days_to_nearest <= 30:
        return 0.5
    if days_to_nearest <= 60:
        return 0.25
    return 0.1


def is_deadline_adjacent(window_end_date: date) -> bool:
    for month, day in FEC_FUNDRAISING_DEADLINES:
        deadline = date(window_end_date.year, month, day)
        if abs((window_end_date - deadline).days) <= DEADLINE_WINDOW_DAYS:
            return True
    return False


def classify_donor_sector(donor_name: str, employer: str = "", occupation: str = "") -> str | None:
    text = " ".join([donor_name, employer, occupation]).lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return sector
    return None


def vote_matches_sector(vote_description: str, sector: str) -> bool:
    if not vote_description or sector not in VOTE_SECTOR_KEYWORDS:
        return False
    text = vote_description.lower()
    return any(kw in text for kw in VOTE_SECTOR_KEYWORDS[sector])


def _vote_text_bundle(vd: str | None, vq: str | None, vr: str | None) -> str:
    return " ".join(p for p in (vd or "", vq or "", vr or "") if p).strip()


def _sectors_matching_vote_text(vote_blob: str) -> set[str]:
    if not vote_blob.strip():
        return set()
    return {s for s in VOTE_SECTOR_KEYWORDS if vote_matches_sector(vote_blob, s)}


def _normalize_match_token(s: str) -> str:
    t = (s or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _lda_substring_hit(needle: str, haystack: str) -> bool:
    """Contiguous substring only; short needles avoid keyword collisions."""
    if len(needle) < REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN:
        return False
    return bool(needle and haystack and needle in haystack)


def match_donor_to_lda(
    donor_normalized_name: str,
    employer: str,
    lda_entries: list[EvidenceEntry],
) -> list[dict[str, Any]]:
    dn = _normalize_match_token(donor_normalized_name)
    em = _normalize_match_token(employer)
    matches: list[dict[str, Any]] = []
    for ent in lda_entries:
        if ent.entry_type != "lobbying_filing":
            continue
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        fy: int | None = None
        try:
            fy = int(raw.get("filing_year")) if raw.get("filing_year") is not None else None
        except (TypeError, ValueError):
            fy = None
        if fy is None or fy < REVOLVING_DOOR_MIN_LDA_FILING_YEAR:
            continue
        rn = _normalize_match_token(str(raw.get("registrant_name") or ""))
        cn = _normalize_match_token(str(raw.get("client_name") or ""))
        lobbyists = raw.get("lobbyist_names") or []
        if not isinstance(lobbyists, list):
            lobbyists = []
        lnorm = [_normalize_match_token(str(x)) for x in lobbyists if x]
        hit = False
        if _lda_substring_hit(dn, rn) or _lda_substring_hit(dn, cn):
            hit = True
        if not hit and (_lda_substring_hit(em, rn) or _lda_substring_hit(em, cn)):
            hit = True
        if not hit and dn:
            for ln in lnorm:
                if _lda_substring_hit(dn, ln):
                    hit = True
                    break
        if not hit:
            continue
        codes_raw = raw.get("issue_codes") or []
        if not isinstance(codes_raw, list):
            codes_raw = []
        codes = [str(c).strip().upper() for c in codes_raw if c]
        matches.append(
            {
                "registrant_name": str(raw.get("registrant_name") or ""),
                "client_name": str(raw.get("client_name") or ""),
                "issue_codes": codes,
                "filing_year": fy,
                "filing_uuid": str(raw.get("filing_uuid") or ""),
            }
        )
    return matches


def _fec_fields_from_signal(db: Session, sig: Signal) -> tuple[str, str, str | None, str | None]:
    employer, occupation = "", ""
    c_state: str | None = None
    c_zip: str | None = None
    for sid in _parse_evidence_id_list(sig.evidence_ids):
        try:
            eid = uuid.UUID(str(sid))
        except ValueError:
            continue
        entry = db.get(EvidenceEntry, eid)
        if not entry or entry.entry_type != "financial_connection":
            continue
        if (entry.source_name or "") != "FEC" and (entry.adapter_name or "") != "FEC":
            continue
        try:
            raw = json.loads(entry.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        employer = str(raw.get("contributor_employer") or "")
        occupation = str(raw.get("contributor_occupation") or "")
        c_state = _nonempty_str(raw.get("contributor_state"))
        z = raw.get("contributor_zip") or raw.get("contributor_zip_code")
        c_zip = str(z).strip() if z else None
        break
    return employer, occupation, c_state, c_zip


def _geo_bucket(
    donor_display: str,
    contributor_state: str | None,
    home_state: str,
) -> str:
    st = (contributor_state or "").strip().upper()
    if not st:
        return "unknown"
    up = donor_display.upper()
    if st == "DC" and any(x in up for x in _GEO_DC_UNKNOWN_NAME_MARKERS):
        return "unknown"
    hs = home_state.strip().upper()
    if st == hs:
        return "in"
    return "out"


def _cluster_midpoint_date(d0: date, d1: date) -> date:
    mid_ord = (d0.toordinal() + d1.toordinal()) // 2
    return date.fromordinal(mid_ord)


def _nonempty_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _bill_dict(raw: dict[str, Any]) -> dict[str, Any]:
    b = raw.get("bill")
    return b if isinstance(b, dict) else {}


def _nearest_vote_description_from_raw(raw: dict[str, Any]) -> str | None:
    """Bill subject / measure text — not the motion question (that is nearest_vote_question)."""
    billd = _bill_dict(raw)
    s = _nonempty_str(billd.get("title"))
    if s:
        return s
    for key in ("measure_title",):
        t = _nonempty_str(raw.get(key)) or _nonempty_str(billd.get(key))
        if t:
            return t
    t = _nonempty_str(raw.get("title")) or _nonempty_str(billd.get("title"))
    if t:
        return t
    t = _nonempty_str(raw.get("description"))
    if t:
        return t
    bn = _nonempty_str(raw.get("bill_number") or billd.get("number"))
    cong = _nonempty_str(raw.get("congress"))
    if bn and cong:
        return f"{bn} ({cong}th Congress)"
    return bn


def _vote_details_from_evidence_id(db: Session, evidence_id: str | None) -> tuple[str | None, str | None, str | None]:
    if not evidence_id or not str(evidence_id).strip():
        return None, None, None
    try:
        eid = uuid.UUID(str(evidence_id).strip())
    except ValueError:
        return None, None, None
    entry = db.get(EvidenceEntry, eid)
    if entry is None:
        return None, None, None
    raw_text = (entry.raw_data_json or "").strip()
    if not raw_text:
        return None, None, None
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(raw, dict):
        return None, None, None
    desc = _nearest_vote_description_from_raw(raw)
    result = _nonempty_str(
        raw.get("result")
        or raw.get("vote_result")
        or raw.get("voteResult")
        or raw.get("vote_result_text")
    )
    question = _nonempty_str(raw.get("question")) or _nonempty_str(
        raw.get("voteQuestion") or raw.get("vote_question")
    )
    return desc, result, question


def _vote_evidence_by_case(db: Session) -> dict[uuid.UUID, list[tuple[uuid.UUID, date]]]:
    rows = db.execute(
        select(EvidenceEntry.id, EvidenceEntry.case_file_id, EvidenceEntry.date_of_event).where(
            EvidenceEntry.entry_type == "vote_record",
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all()
    m: dict[uuid.UUID, list[tuple[uuid.UUID, date]]] = {}
    for eid, cid, d in rows:
        if cid is None or d is None:
            continue
        m.setdefault(cid, []).append((eid, d))
    return m


def _nearest_vote_for_cases(
    db: Session,
    case_ids: set[uuid.UUID],
    midpoint: date,
    votes_by_case: dict[uuid.UUID, list[tuple[uuid.UUID, date]]],
) -> tuple[
    int | None,
    str | None,
    str | None,
    float,
    str | None,
    str | None,
    str | None,
]:
    best_days: int | None = None
    best_id: str | None = None
    best_date: str | None = None
    for cid in case_ids:
        for vid, vd in votes_by_case.get(cid, []):
            dist = abs((midpoint - vd).days)
            if best_days is None or dist < best_days:
                best_days = dist
                best_id = str(vid)
                best_date = vd.isoformat()
    prof = proximity_to_vote_score_from_days(best_days)
    vdesc, vres, vq = _vote_details_from_evidence_id(db, best_id)
    return best_days, best_id, best_date, prof, vdesc, vres, vq


def _donation_date_for_signal(s: Signal) -> date | None:
    raw = s.event_date_a
    if raw:
        try:
            return date.fromisoformat(str(raw).strip()[:10])
        except ValueError:
            pass
    bd = _signal_breakdown_json(s)
    rpt = bd.get("receipt_date")
    if rpt:
        try:
            return date.fromisoformat(str(rpt).strip()[:10])
        except ValueError:
            pass
    ex = bd.get("exemplar_financial_date")
    if ex:
        try:
            return date.fromisoformat(str(ex).strip()[:10])
        except ValueError:
            pass
    return None


@dataclass
class _SoftBundleRow:
    donor_key: str
    donor_display: str
    committee_key: str
    committee_display: str
    d: date
    amount: float
    case_file_id: uuid.UUID
    signal_id: uuid.UUID
    official_name: str


def _load_soft_bundle_rows(db: Session) -> list[_SoftBundleRow]:
    """Donor-cluster signals with committee labels + dated financials (fingerprint join)."""
    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    out: list[_SoftBundleRow] = []
    for fp, sig in rows:
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        cl = str(bd.get("committee_label") or "").strip()
        if not cl:
            continue
        d = _donation_date_for_signal(sig)
        if d is None:
            continue
        raw_amt = bd.get("total_amount")
        try:
            amt_f = float(raw_amt) if raw_amt is not None else float(sig.amount or 0.0)
        except (TypeError, ValueError):
            amt_f = float(sig.amount or 0.0)
        if amt_f <= 0:
            continue
        cid = (fp.canonical_id or "").strip().lower()
        leg = (fp.normalized_donor_key or "").strip().lower()
        dk = cid if cid else leg
        if not dk:
            continue
        ck = cl.lower()
        out.append(
            _SoftBundleRow(
                donor_key=dk,
                donor_display=_donor_display_for_signal(sig, dk),
                committee_key=ck,
                committee_display=cl,
                d=d,
                amount=amt_f,
                case_file_id=fp.case_file_id,
                signal_id=sig.id,
                official_name=(fp.official_name or sig.actor_b or "").strip() or "Unknown official",
            )
        )
    return out


@dataclass
class _EnrichedClusterRow:
    donor_key: str
    donor_display: str
    committee_key: str
    committee_display: str
    d: date
    amount: float
    case_file_id: uuid.UUID
    signal_id: uuid.UUID
    official_name: str
    bioguide_id: str | None
    employer: str
    occupation: str
    contributor_state: str | None
    contributor_zip: str | None
    has_lda_filing: bool


def _load_enriched_cluster_rows(db: Session) -> list[_EnrichedClusterRow]:
    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    case_cache: dict[uuid.UUID, str | None] = {}
    out: list[_EnrichedClusterRow] = []
    for fp, sig in rows:
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        cl = str(bd.get("committee_label") or "").strip()
        if not cl:
            continue
        d = _donation_date_for_signal(sig)
        if d is None:
            continue
        raw_amt = bd.get("total_amount")
        try:
            amt_f = float(raw_amt) if raw_amt is not None else float(sig.amount or 0.0)
        except (TypeError, ValueError):
            amt_f = float(sig.amount or 0.0)
        if amt_f <= 0:
            continue
        cid_key = (fp.canonical_id or "").strip().lower()
        leg = (fp.normalized_donor_key or "").strip().lower()
        dk = cid_key if cid_key else leg
        if not dk:
            continue
        emp, occ, st, zzip = _fec_fields_from_signal(db, sig)
        bg = _resolve_bioguide(db, fp, case_cache)
        has_lda = bool(bd.get("has_lda_filing"))
        out.append(
            _EnrichedClusterRow(
                donor_key=dk,
                donor_display=_donor_display_for_signal(sig, dk),
                committee_key=cl.lower(),
                committee_display=cl,
                d=d,
                amount=amt_f,
                case_file_id=fp.case_file_id,
                signal_id=sig.id,
                official_name=(fp.official_name or sig.actor_b or "").strip() or "Unknown official",
                bioguide_id=bg,
                employer=emp,
                occupation=occ,
                contributor_state=st,
                contributor_zip=zzip,
                has_lda_filing=has_lda,
            )
        )
    return out


def _detect_sector_convergence(db: Session, fired_at: datetime) -> list[PatternAlert]:
    votes_by_case = _vote_evidence_by_case(db)
    by_committee: dict[str, tuple[str, list[_EnrichedClusterRow]]] = {}
    for row in _load_enriched_cluster_rows(db):
        ck = row.committee_key
        if ck not in by_committee:
            by_committee[ck] = (row.committee_display, [])
        by_committee[ck][1].append(row)

    qualifying: list[
        tuple[
            frozenset[uuid.UUID],
            str,
            list[_EnrichedClusterRow],
            date,
            date,
            int,
            float,
            float,
            bool,
        ]
    ] = []
    for _disp, events in by_committee.values():
        if len(events) < SECTOR_CONVERGENCE_MIN_DONORS:
            continue
        dates_sorted = sorted({e.d for e in events})
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > SECTOR_CONVERGENCE_WINDOW_DAYS:
                    continue
                window = [e for e in events if d0 <= e.d <= d1]
                total_dk = {e.donor_key for e in window}
                if len(total_dk) < SECTOR_CONVERGENCE_MIN_DONORS:
                    continue
                for sector_key in SECTOR_KEYWORDS:
                    sector_rows = [
                        e
                        for e in window
                        if classify_donor_sector(e.donor_display, e.employer, e.occupation)
                        == sector_key
                    ]
                    sd = {e.donor_key for e in sector_rows}
                    if len(sd) < SECTOR_CONVERGENCE_MIN_DONORS:
                        continue
                    s_agg = sum(e.amount for e in sector_rows)
                    if s_agg < SECTOR_CONVERGENCE_MIN_AGGREGATE:
                        continue
                    conc = len(sd) / max(len(total_dk), 1)
                    fs = frozenset(e.signal_id for e in sector_rows)
                    case_uuids = {e.case_file_id for e in sector_rows}
                    midpoint = _cluster_midpoint_date(d0, d1)
                    _, _, _, _, vdesc, vres, vq = _nearest_vote_for_cases(
                        db, case_uuids, midpoint, votes_by_case
                    )
                    vblob = _vote_text_bundle(vdesc, vq, vres)
                    vm = vote_matches_sector(vblob, sector_key)
                    qualifying.append((fs, sector_key, sector_rows, d0, d1, len(sd), s_agg, conc, vm))

    by_key: dict[tuple[frozenset[uuid.UUID], str], tuple] = {}
    for tup in qualifying:
        fs0 = tup[0]
        sk = tup[1]
        k = (fs0, sk)
        if k not in by_key:
            by_key[k] = tup
    deduped = list(by_key.values())

    maximal_sets: set[frozenset[uuid.UUID]] = set()
    for tup in sorted(deduped, key=lambda t: -len(t[0])):
        fs = tup[0]
        if fs in maximal_sets:
            continue
        if any(fs < k for k in maximal_sets):
            continue
        subsumed = {k for k in maximal_sets if k < fs}
        maximal_sets -= subsumed
        maximal_sets.add(fs)

    alerts: list[PatternAlert] = []
    for tup in deduped:
        fs, sector_key, sector_rows, d0, d1, s_n, s_agg, conc, vm = tup
        if fs not in maximal_sets:
            continue
        case_uuids = {e.case_file_id for e in sector_rows}
        midpoint = _cluster_midpoint_date(d0, d1)
        d_days, v_id, v_date, prof, vdesc, vres, vq = _nearest_vote_for_cases(
            db, case_uuids, midpoint, votes_by_case
        )
        if is_deadline_adjacent(d1):
            dl_adj, dl_disc, dl_note = True, 0.6, (
                "Bundle window overlaps FEC quarterly deadline — reduced weight"
            )
        else:
            dl_adj, dl_disc, dl_note = False, 1.0, None
        mult = 1.5 if vm else 1.0
        suspicion = conc * float(prof) * dl_disc * mult
        donor_labels = sorted({e.donor_display for e in sector_rows})
        preview = ", ".join(donor_labels[:5])
        if len(donor_labels) > 5:
            preview = f"{preview}, +{len(donor_labels) - 5} more"
        alerts.append(
            PatternAlert(
                rule_id=RULE_SECTOR_CONVERGENCE,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Sector convergence — {sector_key} — {preview}",
                matched_officials=sorted({e.official_name for e in sector_rows}),
                matched_case_ids=sorted({str(e.case_file_id) for e in sector_rows}),
                committee=sector_rows[0].committee_display,
                window_days=int((d1 - d0).days),
                evidence_refs=sorted({str(e.signal_id) for e in sector_rows}),
                fired_at=fired_at,
                donation_window_start=d0,
                donation_window_end=d1,
                aggregate_amount=float(s_agg),
                cluster_size=int(s_n),
                days_to_nearest_vote=d_days,
                nearest_vote_id=v_id,
                nearest_vote_date=v_date,
                nearest_vote_description=vdesc,
                nearest_vote_result=vres,
                nearest_vote_question=vq,
                proximity_to_vote_score=prof,
                deadline_adjacent=dl_adj,
                deadline_discount=dl_disc,
                deadline_note=dl_note,
                suspicion_score=suspicion,
                sector=sector_key,
                sector_donor_count=int(s_n),
                sector_aggregate=float(s_agg),
                sector_concentration=float(conc),
                sector_vote_match=vm,
            )
        )
    return alerts


def _detect_geo_mismatch(db: Session, fired_at: datetime) -> list[PatternAlert]:
    votes_by_case = _vote_evidence_by_case(db)
    by_committee: dict[str, tuple[str, list[_EnrichedClusterRow]]] = {}
    for row in _load_enriched_cluster_rows(db):
        ck = row.committee_key
        if ck not in by_committee:
            by_committee[ck] = (row.committee_display, [])
        by_committee[ck][1].append(row)

    alerts: list[PatternAlert] = []
    seen_fs: set[frozenset[uuid.UUID]] = set()
    for _disp, events in by_committee.values():
        if len(events) < GEO_MISMATCH_MIN_DONORS:
            continue
        dates_sorted = sorted({e.d for e in events})
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > GEO_MISMATCH_WINDOW_DAYS:
                    continue
                window = [e for e in events if d0 <= e.d <= d1]
                if len({e.donor_key for e in window}) < GEO_MISMATCH_MIN_DONORS:
                    continue
                total_amt = sum(e.amount for e in window)
                if total_amt < GEO_MISMATCH_MIN_AGGREGATE:
                    continue
                bg = next((e.bioguide_id for e in window if e.bioguide_id), None)
                if not bg or bg not in SENATOR_HOME_STATE:
                    continue
                home = SENATOR_HOME_STATE[bg]
                in_n = out_n = unk_n = 0
                state_counts: dict[str, int] = {}
                for e in window:
                    b = _geo_bucket(e.donor_display, e.contributor_state, home)
                    if b == "in":
                        in_n += 1
                    elif b == "out":
                        out_n += 1
                        st = (e.contributor_state or "").strip().upper()
                        if st:
                            state_counts[st] = state_counts.get(st, 0) + 1
                    else:
                        unk_n += 1
                classified = in_n + out_n
                if classified < GEO_MISMATCH_MIN_DONORS:
                    continue
                ratio = out_n / classified if classified else 0.0
                if ratio < GEO_MISMATCH_OUT_OF_STATE_THRESHOLD:
                    continue
                fs = frozenset(e.signal_id for e in window)
                if fs in seen_fs:
                    continue
                seen_fs.add(fs)
                top_states = [s for s, _ in sorted(state_counts.items(), key=lambda x: -x[1])[:3]]
                case_uuids = {e.case_file_id for e in window}
                midpoint = _cluster_midpoint_date(d0, d1)
                d_days, v_id, v_date, prof, vdesc, vres, vq = _nearest_vote_for_cases(
                    db, case_uuids, midpoint, votes_by_case
                )
                if is_deadline_adjacent(d1):
                    dl_adj, dl_disc, dl_note = True, 0.6, (
                        "Bundle window overlaps FEC quarterly deadline — reduced weight"
                    )
                else:
                    dl_adj, dl_disc, dl_note = False, 1.0, None
                suspicion = ratio * float(prof) * dl_disc
                donor_labels = sorted({e.donor_display for e in window})
                preview = ", ".join(donor_labels[:5])
                if len(donor_labels) > 5:
                    preview = f"{preview}, +{len(donor_labels) - 5} more"
                alerts.append(
                    PatternAlert(
                        rule_id=RULE_GEO_MISMATCH,
                        pattern_version=PATTERN_ENGINE_VERSION,
                        donor_entity=f"Geographic mismatch — {preview}",
                        matched_officials=sorted({e.official_name for e in window}),
                        matched_case_ids=sorted({str(e.case_file_id) for e in window}),
                        committee=window[0].committee_display,
                        window_days=int((d1 - d0).days),
                        evidence_refs=sorted({str(e.signal_id) for e in window}),
                        fired_at=fired_at,
                        donation_window_start=d0,
                        donation_window_end=d1,
                        aggregate_amount=float(total_amt),
                        cluster_size=len({e.donor_key for e in window}),
                        days_to_nearest_vote=d_days,
                        nearest_vote_id=v_id,
                        nearest_vote_date=v_date,
                        nearest_vote_description=vdesc,
                        nearest_vote_result=vres,
                        nearest_vote_question=vq,
                        proximity_to_vote_score=prof,
                        deadline_adjacent=dl_adj,
                        deadline_discount=dl_disc,
                        deadline_note=dl_note,
                        suspicion_score=suspicion,
                        senator_state=home,
                        out_of_state_ratio=float(ratio),
                        out_of_state_count=out_n,
                        in_state_count=in_n,
                        unknown_state_count=unk_n,
                        top_donor_states=top_states,
                    )
                )
    return alerts


def _schedule_a_contributor_committee_ids_for_case(db: Session, case_id: uuid.UUID) -> set[str]:
    out: set[str] = set()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "financial_connection",
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        rcid = raw.get("contributor_committee_id") or raw.get("contributor_committee_fec_id")
        if rcid:
            out.add(str(rcid).strip().upper())
    return out


def _vote_dates_for_case(db: Session, case_id: uuid.UUID) -> list[date]:
    rows = db.execute(
        select(EvidenceEntry.date_of_event).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "vote_record",
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all()
    return [d for (d,) in rows if d is not None]


def _detect_disbursement_loop(db: Session, fired_at: datetime) -> list[PatternAlert]:
    disbursements = db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.entry_type == "fec_disbursement")
    ).all()
    alerts: list[PatternAlert] = []
    seen: set[tuple[str, str, str]] = set()
    for ent in disbursements:
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        try:
            amt = float(raw.get("disbursement_amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt < DISBURSEMENT_LOOP_MIN_AMOUNT:
            continue
        rec_id = raw.get("recipient_committee_id") or raw.get("recipient_committee_fec_id")
        if not rec_id:
            continue
        rec_id = str(rec_id).strip().upper()
        raw_dd = raw.get("disbursement_date") or ""
        dd = str(raw_dd).strip()[:10]
        try:
            d_event = date.fromisoformat(dd) if dd else None
        except ValueError:
            d_event = None
        if d_event is None:
            continue
        cid = ent.case_file_id
        vote_dates = _vote_dates_for_case(db, cid)
        if not any(abs((d_event - vd).days) <= DISBURSEMENT_LOOP_WINDOW_DAYS for vd in vote_dates):
            continue
        contrib_ids = _schedule_a_contributor_committee_ids_for_case(db, cid)
        loop_ok = rec_id in contrib_ids
        comm = raw.get("committee_id")
        if isinstance(comm, dict):
            disb_c = str(comm.get("committee_id") or "").strip().upper()
        else:
            disb_c = str(comm or raw.get("committee_id") or "").strip().upper()
        rec_name = str(raw.get("recipient_name") or rec_id)
        dedupe_k = (str(cid), rec_id, dd)
        if dedupe_k in seen:
            continue
        seen.add(dedupe_k)
        alerts.append(
            PatternAlert(
                rule_id=RULE_DISBURSEMENT_LOOP,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Disbursement to {rec_name}",
                matched_officials=[],
                matched_case_ids=[str(cid)],
                committee=disb_c or "",
                window_days=DISBURSEMENT_LOOP_WINDOW_DAYS,
                evidence_refs=[str(ent.id)],
                fired_at=fired_at,
                aggregate_amount=amt,
                disbursing_committee=disb_c or None,
                recipient_committee=rec_id,
                disbursement_amount=amt,
                disbursement_date=dd or None,
                loop_confirmed=loop_ok,
                suspicion_score=1.0 if loop_ok else 0.5,
            )
        )
    return alerts


def _detect_revolving_door(db: Session, fired_at: datetime) -> list[PatternAlert]:
    votes_by_case = _vote_evidence_by_case(db)
    lda_by_case: dict[uuid.UUID, list[EvidenceEntry]] = {}
    for ent in db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.entry_type == "lobbying_filing")
    ).all():
        lda_by_case.setdefault(ent.case_file_id, []).append(ent)

    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    alerts: list[PatternAlert] = []
    seen: set[tuple[str, str, str]] = set()
    for fp, sig in rows:
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        if not bool(bd.get("has_lda_filing")):
            continue
        case_id = fp.case_file_id
        lda_list = lda_by_case.get(case_id, [])
        if not lda_list:
            continue
        emp, occ, _, __ = _fec_fields_from_signal(db, sig)
        dk = (fp.canonical_id or "").strip().lower() or (fp.normalized_donor_key or "").strip().lower()
        display = _donor_display_for_signal(sig, dk)
        matches = match_donor_to_lda(display, emp, lda_list)
        if len(matches) < REVOLVING_DOOR_MIN_MATCHED_DONORS:
            continue
        d = _donation_date_for_signal(sig)
        if d is None:
            continue
        case_uuids = {case_id}
        midpoint = d
        _, _, _, _, vdesc, vres, vq = _nearest_vote_for_cases(
            db, case_uuids, midpoint, votes_by_case
        )
        vblob = _vote_text_bundle(vdesc, vq, vres)
        vote_sectors = _sectors_matching_vote_text(vblob)
        for m in matches:
            uid = m.get("filing_uuid") or ""
            reg = str(m.get("registrant_name") or "")
            sk = (str(case_id), str(sig.id), uid)
            if sk in seen:
                continue
            seen.add(sk)
            codes = m.get("issue_codes") or []
            if not isinstance(codes, list):
                codes = []
            lda_sectors: set[str] = set()
            for c in codes:
                s = ISSUE_CODE_TO_SECTOR.get(str(c).strip().upper())
                if s:
                    lda_sectors.add(s)
            rel = bool(lda_sectors and vote_sectors and (lda_sectors & vote_sectors))
            fy = m.get("filing_year")
            try:
                fy_i = int(fy) if fy is not None else None
            except (TypeError, ValueError):
                fy_i = None
            cl = str(bd.get("committee_label") or "")
            alerts.append(
                PatternAlert(
                    rule_id=RULE_REVOLVING_DOOR,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"Revolving door — {display}",
                    matched_officials=[(fp.official_name or sig.actor_b or "").strip() or "Unknown"],
                    matched_case_ids=[str(case_id)],
                    committee=cl,
                    window_days=None,
                    evidence_refs=[str(sig.id)],
                    fired_at=fired_at,
                    donation_window_start=d,
                    donation_window_end=d,
                    nearest_vote_description=vdesc,
                    nearest_vote_result=vres,
                    nearest_vote_question=vq,
                    matched_donor=display,
                    matched_lda_registrant=reg or str(m.get("client_name") or ""),
                    matched_issue_codes=[str(c) for c in codes],
                    revolving_door_vote_relevant=rel,
                    lda_filing_year=fy_i,
                    suspicion_score=1.0 if rel else 0.6,
                )
            )
    return alerts


def _detect_soft_bundles(db: Session, fired_at: datetime) -> list[PatternAlert]:
    votes_by_case = _vote_evidence_by_case(db)
    by_committee: dict[str, tuple[str, list[_SoftBundleRow]]] = {}
    for row in _load_soft_bundle_rows(db):
        disp = row.committee_display
        ck = row.committee_key
        if ck not in by_committee:
            by_committee[ck] = (disp, [])
        by_committee[ck][1].append(row)

    qualifying: list[
        tuple[frozenset[uuid.UUID], list[_SoftBundleRow], date, date, int, float, float | None]
    ] = []
    for _committee_display, events in by_committee.values():
        if len(events) < SOFT_BUNDLE_MIN_UNIQUE_DONORS:
            continue
        dates_sorted = sorted({e.d for e in events})
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > SOFT_BUNDLE_MAX_SPAN_DAYS:
                    continue
                window = [e for e in events if d0 <= e.d <= d1]
                donors = {e.donor_key for e in window}
                if len(donors) < SOFT_BUNDLE_MIN_UNIQUE_DONORS:
                    continue
                total = sum(e.amount for e in window)
                if total < SOFT_BUNDLE_MIN_AGGREGATE:
                    continue
                fs = frozenset(e.signal_id for e in window)
                per_donor: dict[str, float] = {}
                for e in window:
                    per_donor[e.donor_key] = per_donor.get(e.donor_key, 0.0) + e.amount
                div: float | None = None
                if total > 0 and per_donor:
                    hhi = sum((v / total) ** 2 for v in per_donor.values())
                    div = 1.0 - float(hhi)
                qualifying.append((fs, window, d0, d1, len(donors), total, div))

    by_sig_set: dict[frozenset[uuid.UUID], tuple] = {}
    for tup in qualifying:
        fs0 = tup[0]
        if fs0 not in by_sig_set:
            by_sig_set[fs0] = tup
    deduped = list(by_sig_set.values())

    maximal_sets: set[frozenset[uuid.UUID]] = set()
    for tup in sorted(deduped, key=lambda t: -len(t[0])):
        fs = tup[0]
        if fs in maximal_sets:
            continue
        if any(fs < k for k in maximal_sets):
            continue
        subsumed = {k for k in maximal_sets if k < fs}
        maximal_sets -= subsumed
        maximal_sets.add(fs)

    alerts: list[PatternAlert] = []
    for tup in deduped:
        fs, window, d0, d1, n_donors, total, div = tup
        if fs not in maximal_sets:
            continue
        sample_row = window[0]
        donor_labels = sorted({e.donor_display for e in window})
        preview = ", ".join(donor_labels[:5])
        if len(donor_labels) > 5:
            preview = f"{preview}, +{len(donor_labels) - 5} more"
        span_days = (d1 - d0).days
        case_uuids = {e.case_file_id for e in window}
        midpoint = _cluster_midpoint_date(d0, d1)
        d_days, v_id, v_date, prof, v_desc, v_res, v_q = _nearest_vote_for_cases(
            db, case_uuids, midpoint, votes_by_case
        )
        if is_deadline_adjacent(d1):
            dl_adj = True
            dl_discount = 0.6
            dl_note = "Bundle window overlaps FEC quarterly deadline — reduced weight"
        else:
            dl_adj = False
            dl_discount = 1.0
            dl_note = None
        div_f = float(div) if div is not None else 0.0
        size_factor = min(int(n_donors) / 10.0, 1.0)
        suspicion = div_f * prof * dl_discount * size_factor
        alerts.append(
            PatternAlert(
                rule_id=RULE_SOFT_BUNDLE,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Soft bundle — {n_donors} donors ({preview})",
                matched_officials=sorted({e.official_name for e in window}),
                matched_case_ids=sorted({str(e.case_file_id) for e in window}),
                committee=sample_row.committee_display,
                window_days=int(span_days),
                evidence_refs=sorted({str(e.signal_id) for e in window}),
                fired_at=fired_at,
                donation_window_start=d0,
                donation_window_end=d1,
                aggregate_amount=float(total),
                cluster_size=int(n_donors),
                amount_diversification=div,
                days_to_nearest_vote=d_days,
                nearest_vote_id=v_id,
                nearest_vote_date=v_date,
                nearest_vote_description=v_desc,
                nearest_vote_result=v_res,
                nearest_vote_question=v_q,
                proximity_to_vote_score=prof,
                deadline_adjacent=dl_adj,
                deadline_discount=dl_discount,
                deadline_note=dl_note,
                suspicion_score=suspicion,
            )
        )
    return alerts


def _committees_by_bioguide(db: Session) -> dict[str, set[str]]:
    rows = db.execute(
        select(SenatorCommittee.bioguide_id, SenatorCommittee.committee_name)
    ).all()
    m: dict[str, set[str]] = {}
    for bg, name in rows:
        if not bg or not name:
            continue
        m.setdefault(str(bg).strip(), set()).add(str(name).strip())
    return m


def _resolve_bioguide(
    db: Session,
    fp: DonorFingerprint,
    case_bg_cache: dict[uuid.UUID, str | None],
) -> str | None:
    if fp.bioguide_id and str(fp.bioguide_id).strip():
        return str(fp.bioguide_id).strip()
    cid = fp.case_file_id
    if cid not in case_bg_cache:
        bg = db.scalar(
            select(SubjectProfile.bioguide_id).where(SubjectProfile.case_file_id == cid)
        )
        case_bg_cache[cid] = str(bg).strip() if bg else None
    return case_bg_cache[cid]


@dataclass
class _Appearance:
    donor_key: str
    donor_display: str
    official_name: str
    case_file_id: uuid.UUID
    signal_id: uuid.UUID
    bioguide: str | None
    donation_date: date | None
    relevance_score: float


def _load_appearances(db: Session) -> list[_Appearance]:
    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    case_cache: dict[uuid.UUID, str | None] = {}
    out: list[_Appearance] = []
    for fp, sig in rows:
        cid = (fp.canonical_id or "").strip().lower()
        leg = (fp.normalized_donor_key or "").strip().lower()
        dk = cid if cid else leg
        if not dk:
            continue
        bg = _resolve_bioguide(db, fp, case_cache)
        out.append(
            _Appearance(
                donor_key=dk,
                donor_display=_donor_display_for_signal(sig, dk),
                official_name=(fp.official_name or sig.actor_b or "").strip() or "Unknown official",
                case_file_id=fp.case_file_id,
                signal_id=sig.id,
                bioguide=bg,
                donation_date=_donation_date_for_signal(sig),
                relevance_score=float(sig.relevance_score or 0.0),
            )
        )
    return out


def _detect_committee_sweep(
    appearances_by_donor: dict[str, list[_Appearance]],
    committees_map: dict[str, set[str]],
    fired_at: datetime,
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for donor_key, apps in appearances_by_donor.items():
        donor_label = apps[0].donor_display if apps else donor_key
        all_committees: set[str] = set()
        for a in apps:
            if a.bioguide and a.bioguide in committees_map:
                all_committees |= committees_map[a.bioguide]

        seen_pairs: set[tuple[str, str]] = set()
        for c in all_committees:
            officials_with_c: set[str] = set()
            for a in apps:
                if not a.bioguide:
                    continue
                if c in committees_map.get(a.bioguide, set()):
                    officials_with_c.add(a.official_name)
            if len(officials_with_c) < COMMITTEE_SWEEP_MIN_OFFICIALS:
                continue

            subset = [a for a in apps if a.official_name in officials_with_c]
            dated = [a for a in subset if a.donation_date is not None]
            distinct_off = {a.official_name for a in dated}
            if len(distinct_off) < COMMITTEE_SWEEP_MIN_OFFICIALS:
                continue
            dates = [a.donation_date for a in dated if a.donation_date]
            if not dates:
                continue
            dmin, dmax = min(dates), max(dates)
            span = (dmax - dmin).days
            if span > COMMITTEE_SWEEP_MAX_WINDOW_DAYS:
                continue

            dedupe_k = (donor_key, c)
            if dedupe_k in seen_pairs:
                continue
            seen_pairs.add(dedupe_k)

            case_ids = sorted({str(a.case_file_id) for a in subset})
            officials = sorted(officials_with_c)
            ev_ids = sorted({str(a.signal_id) for a in subset})
            alerts.append(
                PatternAlert(
                    rule_id=RULE_COMMITTEE_SWEEP,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=donor_label,
                    matched_officials=officials,
                    matched_case_ids=case_ids,
                    committee=c,
                    window_days=int(span),
                    evidence_refs=ev_ids,
                    fired_at=fired_at,
                    donation_window_start=dmin,
                    donation_window_end=dmax,
                )
            )
    return alerts


def _detect_fingerprint_bloom(
    appearances_by_donor: dict[str, list[_Appearance]],
    fired_at: datetime,
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for donor_key, apps in appearances_by_donor.items():
        hi = [a for a in apps if a.relevance_score >= FINGERPRINT_BLOOM_MIN_RELEVANCE]
        case_ids_set = {str(a.case_file_id) for a in hi}
        if len(case_ids_set) < FINGERPRINT_BLOOM_MIN_CASES:
            continue
        donor_label = apps[0].donor_display if apps else donor_key
        officials = sorted({a.official_name for a in hi})
        case_ids = sorted(case_ids_set)
        ev_ids = sorted({str(a.signal_id) for a in hi})
        alerts.append(
            PatternAlert(
                rule_id=RULE_FINGERPRINT_BLOOM,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=donor_label,
                matched_officials=officials,
                matched_case_ids=case_ids,
                committee="",
                window_days=None,
                evidence_refs=ev_ids,
                fired_at=fired_at,
                donation_window_start=None,
                donation_window_end=None,
            )
        )
    return alerts


def run_pattern_engine(db: Session) -> list[PatternAlert]:
    """
    Run all pattern rules against the current fingerprint table.
    Returns a list of PatternAlert objects. Never mutates case state.
    """
    fired_at = _utc_now()
    appearances = _load_appearances(db)
    by_donor: dict[str, list[_Appearance]] = {}
    for a in appearances:
        by_donor.setdefault(a.donor_key, []).append(a)

    committees_map = _committees_by_bioguide(db)
    alerts: list[PatternAlert] = []
    alerts.extend(_detect_committee_sweep(by_donor, committees_map, fired_at))
    alerts.extend(_detect_fingerprint_bloom(by_donor, fired_at))
    alerts.extend(_detect_soft_bundles(db, fired_at))
    alerts.extend(_detect_sector_convergence(db, fired_at))
    alerts.extend(_detect_geo_mismatch(db, fired_at))
    alerts.extend(_detect_disbursement_loop(db, fired_at))
    alerts.extend(_detect_revolving_door(db, fired_at))
    alerts.sort(key=lambda x: (x.donor_entity.lower(), x.rule_id, x.committee or ""))
    return alerts


def pattern_alert_to_payload(a: PatternAlert) -> dict[str, Any]:
    return {
        "rule_id": a.rule_id,
        "pattern_version": a.pattern_version,
        "donor_entity": a.donor_entity,
        "matched_officials": list(a.matched_officials),
        "matched_case_ids": list(a.matched_case_ids),
        "committee": a.committee or "",
        "window_days": a.window_days,
        "evidence_refs": list(a.evidence_refs),
        "fired_at": a.fired_at.isoformat(),
        "disclaimer": a.disclaimer,
        "donation_window_start": a.donation_window_start.isoformat()
        if a.donation_window_start
        else None,
        "donation_window_end": a.donation_window_end.isoformat()
        if a.donation_window_end
        else None,
        "aggregate_amount": a.aggregate_amount,
        "cluster_size": a.cluster_size,
        "amount_diversification": a.amount_diversification,
        "days_to_nearest_vote": a.days_to_nearest_vote,
        "nearest_vote_id": a.nearest_vote_id,
        "nearest_vote_date": a.nearest_vote_date,
        "nearest_vote_description": a.nearest_vote_description,
        "nearest_vote_result": a.nearest_vote_result,
        "nearest_vote_question": a.nearest_vote_question,
        "proximity_to_vote_score": a.proximity_to_vote_score,
        "deadline_adjacent": a.deadline_adjacent,
        "deadline_discount": a.deadline_discount,
        "deadline_note": a.deadline_note,
        "suspicion_score": a.suspicion_score,
        "sector": a.sector,
        "sector_donor_count": a.sector_donor_count,
        "sector_aggregate": a.sector_aggregate,
        "sector_concentration": a.sector_concentration,
        "sector_vote_match": a.sector_vote_match,
        "senator_state": a.senator_state,
        "out_of_state_ratio": a.out_of_state_ratio,
        "out_of_state_count": a.out_of_state_count,
        "in_state_count": a.in_state_count,
        "unknown_state_count": a.unknown_state_count,
        "top_donor_states": list(a.top_donor_states) if a.top_donor_states else None,
        "disbursing_committee": a.disbursing_committee,
        "recipient_committee": a.recipient_committee,
        "disbursement_amount": a.disbursement_amount,
        "disbursement_date": a.disbursement_date,
        "loop_confirmed": a.loop_confirmed,
        "matched_donor": a.matched_donor,
        "matched_lda_registrant": a.matched_lda_registrant,
        "matched_issue_codes": list(a.matched_issue_codes)
        if a.matched_issue_codes
        else None,
        "revolving_door_vote_relevant": a.revolving_door_vote_relevant,
        "lda_filing_year": a.lda_filing_year,
    }


def pattern_alerts_for_signing(alerts: list[PatternAlert]) -> list[dict[str, Any]]:
    return [pattern_alert_to_payload(a) for a in alerts]


def sync_pattern_alert_records(db: Session, alerts: list[PatternAlert]) -> None:
    """Replace persisted pattern alerts with the latest engine output (global snapshot)."""
    db.execute(delete(PatternAlertRecord))
    now = _utc_now()
    for a in alerts:
        db.add(
            PatternAlertRecord(
                rule_id=a.rule_id,
                pattern_version=a.pattern_version,
                donor_entity=a.donor_entity,
                matched_officials=json.dumps(a.matched_officials),
                matched_case_ids=json.dumps(a.matched_case_ids),
                committee=a.committee or None,
                window_days=a.window_days,
                evidence_refs=json.dumps(a.evidence_refs),
                disclaimer=a.disclaimer,
                fired_at=a.fired_at,
                created_at=now,
            )
        )


def pattern_alerts_for_case(case_id: uuid.UUID, alerts: list[PatternAlert]) -> list[dict[str, Any]]:
    """HTML report rows: only alerts that reference this case."""
    sid = str(case_id)
    return [
        pattern_alert_to_report_dict(a)
        for a in alerts
        if sid in a.matched_case_ids
    ]


def pattern_alert_to_report_dict(a: PatternAlert) -> dict[str, Any]:
    if a.rule_id == RULE_COMMITTEE_SWEEP:
        badge = "Multi-Senator Donor"
        rule_line = (
            f"Committee Sweep — appeared for {COMMITTEE_SWEEP_MIN_OFFICIALS}+ members of "
            f"{a.committee} within {a.window_days} day(s)"
        )
    elif a.rule_id == RULE_SOFT_BUNDLE:
        badge = "Soft Bundle"
        agg = float(a.aggregate_amount or 0.0)
        n = int(a.cluster_size or 0)
        rule_line = (
            f"Soft bundle — {n} distinct donors aggregated ${agg:,.0f} to {a.committee} "
            f"within {a.window_days} day(s)"
        )
    else:
        badge = "Cross-Case Donor"
        rule_line = (
            f"Fingerprint bloom — appeared in {FINGERPRINT_BLOOM_MIN_CASES}+ investigations "
            f"with relevance ≥ {FINGERPRINT_BLOOM_MIN_RELEVANCE}"
        )
    ws = a.donation_window_start.strftime("%b %d, %Y") if a.donation_window_start else None
    we = a.donation_window_end.strftime("%b %d, %Y") if a.donation_window_end else None
    window_phrase = ""
    if ws and we and a.window_days is not None:
        window_phrase = f"{ws}–{we} ({a.window_days} days)"
    elif a.window_days is not None:
        window_phrase = f"{a.window_days} days"
    return {
        "badge": badge,
        "rule_line": rule_line,
        "donor_entity": a.donor_entity,
        "matched_officials": a.matched_officials,
        "committee": a.committee or "",
        "window_days": a.window_days,
        "window_phrase": window_phrase,
        "disclaimer": a.disclaimer,
        "rule_id": a.rule_id,
    }


def filter_pattern_alerts(
    alerts: list[PatternAlert],
    *,
    donor: str | None = None,
    rule: str | None = None,
    case_id: uuid.UUID | None = None,
) -> list[PatternAlert]:
    out = alerts
    if case_id is not None:
        sid = str(case_id)
        out = [a for a in out if sid in a.matched_case_ids]
    if rule and rule.strip():
        out = [a for a in out if a.rule_id == rule.strip()]
    if donor and donor.strip():
        dnorm = donor.strip().lower()
        out = [
            a
            for a in out
            if dnorm in a.donor_entity.lower() or dnorm.replace(" ", "") in a.donor_entity.lower().replace(" ", "")
        ]
    return out
