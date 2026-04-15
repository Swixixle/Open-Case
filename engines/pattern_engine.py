"""
Pattern Engine — cross-official donor pattern detection.

Rules are versioned and typed. PatternAlerts are read-side only.
They document what public records show across investigations.
They assert nothing about intent or causation.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from signals.dedup import _parse_evidence_id_list

from adapters.fec import classify_donor_type
from adapters.indianapolis_procurement import normalize_vendor_name
from engines.entity_resolution import canonicalize, resolve
from utils.local_entity_matching import (
    MATCH_RELATED_ENTITY,
    _load_curated_aliases,
    _local_match_type,
    local_jurisdiction_alias_key,
    local_match_eligible_for_loop_and_timing,
)
from engines.political_calendar import get_calendar_discount
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from models import (
    CaseFile,
    DonorFingerprint,
    EvidenceEntry,
    PatternAlertRecord,
    SenatorCommittee,
    Signal,
    SubjectProfile,
)

PATTERN_ENGINE_VERSION = "2.7"

logger = logging.getLogger(__name__)

# Rule IDs — increment when logic changes, never reuse
RULE_COMMITTEE_SWEEP = "COMMITTEE_SWEEP_V1"
RULE_FINGERPRINT_BLOOM = "FINGERPRINT_BLOOM_V1"
RULE_SOFT_BUNDLE = "SOFT_BUNDLE_V1"
RULE_SOFT_BUNDLE_V2 = "SOFT_BUNDLE_V2"
RULE_SECTOR_CONVERGENCE = "SECTOR_CONVERGENCE_V1"
RULE_GEO_MISMATCH = "GEO_MISMATCH_V1"
RULE_DISBURSEMENT_LOOP = "DISBURSEMENT_LOOP_V1"
RULE_JOINT_FUNDRAISING = "JOINT_FUNDRAISING_V1"
RULE_BASELINE_ANOMALY = "BASELINE_ANOMALY_V1"
RULE_ALIGNMENT_ANOMALY = "ALIGNMENT_ANOMALY_V1"
RULE_AMENDMENT_TELL = "AMENDMENT_TELL_V1"
RULE_HEARING_TESTIMONY = "HEARING_TESTIMONY_V1"
RULE_REVOLVING_DOOR = "REVOLVING_DOOR_V1"
RULE_LOCAL_CONTRACTOR_DONOR_LOOP = "LOCAL_CONTRACTOR_DONOR_LOOP_V1"
RULE_LOCAL_CONTRACT_DONATION_TIMING = "LOCAL_CONTRACT_DONATION_TIMING_V1"
RULE_LOCAL_VENDOR_CONCENTRATION = "LOCAL_VENDOR_CONCENTRATION_V1"
RULE_LOCAL_RELATED_ENTITY_DONOR = "LOCAL_RELATED_ENTITY_DONOR_V1"
RULE_LEGISLATIVE_RELATED_ENTITY_DONOR = "LEGISLATIVE_RELATED_ENTITY_DONOR_V1"

# Enumerated rule ids (diagnostics, QA, client allowlists).
PATTERN_RULE_IDS: frozenset[str] = frozenset(
    {
        RULE_COMMITTEE_SWEEP,
        RULE_FINGERPRINT_BLOOM,
        RULE_SOFT_BUNDLE,
        RULE_SOFT_BUNDLE_V2,
        RULE_SECTOR_CONVERGENCE,
        RULE_GEO_MISMATCH,
        RULE_DISBURSEMENT_LOOP,
        RULE_JOINT_FUNDRAISING,
        RULE_BASELINE_ANOMALY,
        RULE_ALIGNMENT_ANOMALY,
        RULE_AMENDMENT_TELL,
        RULE_HEARING_TESTIMONY,
        RULE_REVOLVING_DOOR,
        RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
        RULE_LOCAL_CONTRACT_DONATION_TIMING,
        RULE_LOCAL_VENDOR_CONCENTRATION,
        RULE_LOCAL_RELATED_ENTITY_DONOR,
        RULE_LEGISLATIVE_RELATED_ENTITY_DONOR,
    }
)

RULE_FAMILIES: dict[str, dict[str, Any]] = {
    "related_entity_influence": {
        "members": [
            RULE_LOCAL_RELATED_ENTITY_DONOR,
            RULE_LEGISLATIVE_RELATED_ENTITY_DONOR,
        ],
    },
}

LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS = 180

_VOICE_VOTE_PHRASES = (
    "voice vote",
    "viva voce",
    "without objection",
)

# Populated each run by _detect_local_related_entity_donor (read-only telemetry for POC / tests).
LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS: dict[str, int] = {
    "skipped_missing_contract_event_type": 0,
}

LOCAL_TIMING_WINDOW_DAYS = 180
LOCAL_CONCENTRATION_TOP_N = 5
LOCAL_CONCENTRATION_MIN_OVERLAP = 2

# Future: non-legislative subject profiles (appointed boards, prosecutors, law enforcement,
# utilities, port authorities, gaming commissions, etc.). These rules are implemented for
# the FEC/Congress pipeline today but apply with minor adaptation:
#   - SOFT_BUNDLE_V1 / SOFT_BUNDLE_V2 — donor-to-decision proximity for any elected official.
#   - COMMITTEE_SWEEP_V1 — map committee/jurisdiction to regulatory or agency oversight body.
#   - REVOLVING_DOOR_V1 — employment and interest transitions (chiefs, prosecutors, zoning).
#   - SECTOR_CONVERGENCE_V1 — sector clustering for utilities, ports, gaming, licensing boards.
# No engine logic changes yet — taxonomy/registry only.

COMMITTEE_SWEEP_MIN_OFFICIALS = 3
COMMITTEE_SWEEP_MAX_WINDOW_DAYS = 14
FINGERPRINT_BLOOM_MIN_CASES = 4
FINGERPRINT_BLOOM_MIN_RELEVANCE = 0.3

SOFT_BUNDLE_MIN_UNIQUE_DONORS = 3
SOFT_BUNDLE_MAX_SPAN_DAYS = 7
SOFT_BUNDLE_MIN_AGGREGATE = 1000.0

SOFT_BUNDLE_V2_MIN_DONORS = 3
SOFT_BUNDLE_V2_WINDOW_DAYS = 7
SOFT_BUNDLE_V2_MIN_AGGREGATE = 1000.0
SOFT_BUNDLE_V2_SECTOR_THRESHOLD = 0.60
SOFT_BUNDLE_V2_BASELINE_MULTIPLIER = 3.0
SOFT_BUNDLE_V2_HEARING_WINDOW_DAYS = 14
SOFT_BUNDLE_V2_INDIVIDUAL_WEIGHT_BONUS = 0.15
SOFT_BUNDLE_V2_SECTOR_WEIGHT_BONUS = 0.10
SOFT_BUNDLE_V2_HEARING_WEIGHT_BONUS = 0.20
SOFT_BUNDLE_V2_ORG_DOMINATED_PENALTY = -0.10

_HEARING_V2_ENTRY_TYPES = frozenset({"hearing_witness"})

SECTOR_CONVERGENCE_MIN_DONORS = 3
SECTOR_CONVERGENCE_WINDOW_DAYS = 14
SECTOR_CONVERGENCE_MIN_AGGREGATE = 5000.0

GEO_MISMATCH_MIN_DONORS = 5
GEO_MISMATCH_WINDOW_DAYS = 14
GEO_MISMATCH_OUT_OF_STATE_THRESHOLD = 0.75
GEO_MISMATCH_MIN_AGGREGATE = 1000.0
GEO_MISMATCH_MAX_ALERTS_PER_COMMITTEE = 3

# DC + org-style name → unknown (not out-of-state); see _geo_bucket
_GEO_DC_UNKNOWN_NAME_MARKERS = ("PAC", "COMMITTEE", "ASSOCIATION", "COUNCIL", "INSTITUTE")

# GEO mismatch ratio uses individual donors only; org markers = structural non-person entities.
_GEO_ORG_DONOR_MARKERS: frozenset[str] = frozenset(
    {
        "PAC",
        "COMMITTEE",
        "CORPORATION",
        "CORP",
        "INC",
        "LLC",
        "LLP",
        "ASSOCIATION",
        "ASSOC",
        "COUNCIL",
        "INSTITUTE",
        "FUND",
        "GROUP",
        "FOUNDATION",
        "TRUST",
        "BANK",
        "UNION",
        "COALITION",
        "ALLIANCE",
        "NETWORK",
        "SOCIETY",
        "FEDERATION",
        "BUREAU",
    }
)

DISBURSEMENT_LOOP_WINDOW_DAYS = 30
DISBURSEMENT_LOOP_MIN_AMOUNT = 5000.0

AMENDMENT_TELL_WINDOW_DAYS = 90
AMENDMENT_TELL_MIN_SIGNAL_WEIGHT = 0.3
_AMENDMENT_WEAKENING_KEYWORDS = (
    "exempt",
    "delay",
    "reduce",
    "limit",
    "repeal",
    "strike",
    "waive",
)

BASELINE_ANOMALY_MIN_MULTIPLIER = 6.0
BASELINE_ANOMALY_CALENDAR_ADJACENT_MIN_MULTIPLIER = 10.0
BASELINE_ANOMALY_MIN_AGGREGATE = 5000.0
BASELINE_ANOMALY_MIN_DATAPOINTS = 20
BASELINE_ANOMALY_MAX_ALERTS_PER_CASE = 5
# Spikes decades from the nearest stored vote are not comparable to legislative context.
BASELINE_ANOMALY_MAX_DAYS_TO_NEAREST_VOTE = 180
# Expected fundraising spikes in FEC election-cycle years (even years in cycles_included).
BASELINE_ANOMALY_CAMPAIGN_SEASON_DISCOUNT = 0.5

ALIGNMENT_ANOMALY_MIN_VOTES = 5
ALIGNMENT_ANOMALY_DEVIATION_THRESHOLD = 1.5
ALIGNMENT_LDA_ACTIVE_DAYS = 90
CHAMBER_BASELINE_MIN_SENATORS = 3

HEARING_TESTIMONY_WINDOW_DAYS = 180

REVOLVING_DOOR_MIN_MATCHED_DONORS = 1
REVOLVING_DOOR_MIN_LDA_FILING_YEAR = 2024
REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN = 6
REVOLVING_DOOR_MIN_EMPLOYER_SUBSTRING_LEN = 8

REVOLVING_DOOR_DONOR_BLOCKLIST = frozenset(
    {
        "actblue",
        "winred",
        "ngp van",
        "anedot",
        "revv",
        "republican national committee",
        "democratic national committee",
        "democratic senatorial campaign committee",
        "national republican senatorial committee",
    }
)

_REVOLVING_DOOR_EMPLOYER_BLOCKLIST_RAW = (
    "self",
    "self-employed",
    "retired",
    "none",
    "n/a",
    "na",
    "various",
    "homemaker",
    "not employed",
    "unemployed",
    "student",
)

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
    "FDA": "pharma",
    "DEF": "defense",
    "ARM": "defense",
    "ENV": "energy",
    "ENE": "energy",
    "OIL": "energy",
    "AGR": "agriculture",
    "FOO": "agriculture",
    "HOU": "real_estate",
    "MOR": "real_estate",
    "TEC": "tech",
    "INT": "tech",
    "DAT": "tech",
}

# Longer role phrases first so "assistant secretary of defense" wins over "secretary of defense".
NOMINATION_ROLE_TO_SECTOR: dict[str, str] = {
    "assistant secretary of defense": "defense",
    "under secretary of defense": "defense",
    "administrator of the environmental protection agency": "energy",
    "commissioner of food and drugs": "pharma",
    "comptroller of the currency": "finance",
    "secretary of defense": "defense",
    "secretary of energy": "energy",
    "secretary of the treasury": "finance",
    "secretary of health": "pharma",
    "secretary of agriculture": "agriculture",
    "secretary of housing": "real_estate",
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

CURRENT_CONGRESS_FOR_VOTE_CONTEXT = 119


def _home_state_from_db(db: Session, bioguide_id: str | None) -> str | None:
    """Resolve senator home state for calendar / geo rules beyond the static map."""
    if not bioguide_id:
        return None
    u = bioguide_id.strip().upper()
    if u in SENATOR_HOME_STATE:
        return SENATOR_HOME_STATE[u]
    st = db.scalar(select(SubjectProfile.state).where(SubjectProfile.bioguide_id == u))
    if st and len(str(st).strip()) >= 2:
        return str(st).strip().upper()[:2]
    jur = db.scalar(
        select(CaseFile.jurisdiction)
        .join(SubjectProfile, SubjectProfile.case_file_id == CaseFile.id)
        .where(SubjectProfile.bioguide_id == u)
        .limit(1)
    )
    if jur and len(str(jur).strip()) == 2:
        j = str(jur).strip().upper()
        if j.isalpha():
            return j[:2]
    return None

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
    calendar_event_type: str | None = None
    calendar_event_name: str | None = None
    vote_context_available: bool | None = None
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
    individual_donor_count: int | None = None
    org_donor_count: int | None = None
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
    lda_match_count: int | None = None
    diagnostics_json: str | None = None
    payload_extra: dict[str, Any] | None = None
    proportionality_context: list[dict[str, Any]] | None = None
    epistemic_level: str = "REPORTED"
    requires_human_review: bool = False


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


def _vote_raw_congress_number(raw: dict[str, Any]) -> int | None:
    c = raw.get("congress")
    if c is None:
        bill = raw.get("bill")
        if isinstance(bill, dict):
            c = bill.get("congress")
    if c is None:
        return None
    if isinstance(c, bool):
        return None
    if isinstance(c, int):
        return c
    if isinstance(c, float):
        if c.is_integer():
            return int(c)
        return None
    s = str(c).strip()
    if s.isdigit():
        return int(s)
    # "119th Congress", "One Hundred Nineteenth Congress"
    m = re.search(r"\b(11[89]|12\d)\b", s)
    if m:
        return int(m.group(1))
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 3:
        return int(digits[:3])
    return int(digits) if digits else None


def _subject_bioguides_by_case(db: Session) -> dict[uuid.UUID, str | None]:
    m: dict[uuid.UUID, str | None] = {}
    for prof in db.scalars(select(SubjectProfile)).all():
        bg = (prof.bioguide_id or "").strip().upper() or None
        m[prof.case_file_id] = bg
    return m


def _vote_record_bioguide_raw(raw: dict[str, Any]) -> str:
    mv = raw.get("memberVote")
    if isinstance(mv, dict):
        v = mv.get("bioguideId") or mv.get("bioguide_id")
        if v:
            return str(v).strip().upper()
    for k in ("bioguide_id", "member_bioguide_id"):
        v = raw.get(k)
        if v:
            return str(v).strip().upper()
    return ""


def _vote_bioguide_aligns_with_subject(
    raw: dict[str, Any], case_subject_bioguide: str | None
) -> bool:
    """Reject votes explicitly tagged for another member (production data hygiene)."""
    subj = (case_subject_bioguide or "").strip().upper() or None
    vote_bg = _vote_record_bioguide_raw(raw)
    if vote_bg and subj and vote_bg != subj:
        return False
    return True


def _vote_qualifies_for_ghost_gate(
    raw: dict[str, Any],
    case_subject_bioguide: str | None,
) -> bool:
    """Strict: must be current-congress vote evidence tied to this subject when bioguides exist."""
    subj = (case_subject_bioguide or "").strip().upper() or None
    vote_bg = _vote_record_bioguide_raw(raw)
    if subj:
        # Production hygiene (Shaheen-class): votes with no member bioguide in raw JSON
        # must not open the vote-context gate — otherwise another senator's roll calls
        # attached to the case could still fire BASELINE_ANOMALY / alignment, etc.
        if not vote_bg or vote_bg != subj:
            return False
    else:
        if not _vote_bioguide_aligns_with_subject(raw, case_subject_bioguide):
            return False
    if _vote_raw_congress_number(raw) != CURRENT_CONGRESS_FOR_VOTE_CONTEXT:
        return False
    return True


def _vote_qualifies_for_nearest_vote_map(
    raw: dict[str, Any],
    case_subject_bioguide: str | None,
) -> bool:
    """
    Loose for temporal proximity: allow legacy rows with no congress field; exclude
    wrong-congress and wrong-bioguide rows.
    """
    if not _vote_bioguide_aligns_with_subject(raw, case_subject_bioguide):
        return False
    cn = _vote_raw_congress_number(raw)
    if cn is None:
        return True
    return cn == CURRENT_CONGRESS_FOR_VOTE_CONTEXT


def _vote_question_blob_lower(raw: dict[str, Any]) -> str:
    return " ".join(
        str(raw.get(k) or "")
        for k in ("question", "voteQuestion", "vote_question", "description")
    ).lower()


def vote_qualifies(raw: dict[str, Any], case_subject_bioguide: str | None) -> bool:
    """
    Episode gate for auditable vote proximity: recorded vote context (not voice vote),
    current congress when known, member bioguide present and aligned to the case subject.
    """
    if not isinstance(raw, dict):
        return False
    qlow = _vote_question_blob_lower(raw)
    if any(p in qlow for p in _VOICE_VOTE_PHRASES):
        return False
    vt = str(
        raw.get("vote_type") or raw.get("voteType") or raw.get("method") or ""
    ).strip().lower()
    if vt and "voice" in vt and "roll" not in vt:
        return False
    subj = (case_subject_bioguide or "").strip().upper() or None
    vote_bg = _vote_record_bioguide_raw(raw)
    if subj:
        if not vote_bg or vote_bg != subj:
            return False
    else:
        if not _vote_bioguide_aligns_with_subject(raw, case_subject_bioguide):
            return False
    cn = _vote_raw_congress_number(raw)
    if cn is not None and cn != CURRENT_CONGRESS_FOR_VOTE_CONTEXT:
        return False
    return True


def _count_skipped_votes_for_cases(
    db: Session,
    case_ids: set[uuid.UUID],
    prof_by_case: dict[uuid.UUID, str | None],
) -> int:
    """Votes present on case that fail ``vote_qualifies`` (audit only; does not change selection)."""
    if not case_ids:
        return 0
    n = 0
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id.in_(case_ids),
            EvidenceEntry.entry_type.in_(("vote_record", "amendment_vote")),
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        subj = prof_by_case.get(ent.case_file_id)
        if not vote_qualifies(raw, subj):
            n += 1
    return n


def _nearest_vote_within_days_for_case(
    db: Session,
    case_id: uuid.UUID,
    reference_day: date,
    prof_by_case: dict[uuid.UUID, str | None],
    max_days: int,
) -> tuple[str | None, str | None, int | None]:
    """Nearest vote evidence that passes ``vote_qualifies`` within ``max_days``."""
    best_id: str | None = None
    best_day: date | None = None
    best_dist: int | None = None
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type.in_(("vote_record", "amendment_vote")),
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all():
        d = ent.date_of_event
        if d is None:
            continue
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        subj = prof_by_case.get(case_id)
        if not vote_qualifies(raw, subj):
            continue
        dist = abs((reference_day - d).days)
        if dist > max_days:
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_id = str(ent.id)
            best_day = d
    if best_id is None or best_day is None or best_dist is None:
        return None, None, None
    return best_id, best_day.isoformat(), best_dist


def _legislative_related_entity_amount_score(amount: float | None) -> float:
    d = max(0.0, float(amount or 0))
    nd = min(1.0, math.log10(d + 1.0) / 6.0)
    return max(0.35, min(0.75, 0.35 + 0.40 * nd))


def _federal_indirect_match_label(relationship_type: str | None) -> str:
    rt = (relationship_type or "").strip().lower()
    mapping = {
        "pac_of_donor": "PAC affiliated with donor",
        "affiliate": "Corporate affiliate of donor",
        "subsidiary": "Subsidiary of donor",
        "parent": "Parent company of donor",
        "trade_name": "Trade name of donor",
    }
    return mapping.get(rt, "Related entity of donor")


def _fec_financial_evidence_rows(db: Session, case_id: uuid.UUID) -> list[EvidenceEntry]:
    return list(
        db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "financial_connection",
                EvidenceEntry.is_absence.is_(False),
                or_(
                    EvidenceEntry.source_name == "FEC",
                    EvidenceEntry.adapter_name == "FEC",
                ),
            )
        ).all()
    )


def _fec_contributor_display(raw: dict[str, Any]) -> str:
    for k in (
        "contributor_name",
        "contributor_name_text",
        "contributor_display",
    ):
        v = raw.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _case_ids_with_current_congress_votes(db: Session) -> set[uuid.UUID]:
    prof_by_case = _subject_bioguides_by_case(db)
    out: set[uuid.UUID] = set()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.entry_type.in_(("vote_record", "amendment_vote"))
        )
    ).all():
        subj = prof_by_case.get(ent.case_file_id)
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        if not _vote_qualifies_for_ghost_gate(raw, subj):
            continue
        out.add(ent.case_file_id)
    return out


def _all_cases_have_vote_context(
    matched_case_ids: list[str], cases_with_votes: set[uuid.UUID]
) -> bool:
    for sid in matched_case_ids:
        try:
            cid = uuid.UUID(sid)
        except ValueError:
            return False
        if cid not in cases_with_votes:
            return False
    return True


def _senate_committee_calendar_context(
    db: Session, bioguide_id: str | None
) -> tuple[list[str], list[str]]:
    bg = (bioguide_id or "").strip().upper()
    if not bg:
        return [], []
    rows = list(
        db.scalars(select(SenatorCommittee).where(SenatorCommittee.bioguide_id == bg)).all()
    )
    codes: list[str] = []
    chairs: list[str] = []
    for r in rows:
        c = (r.committee_code or "").strip().upper()
        if not c:
            continue
        codes.append(c)
        nm = (r.committee_name or "").lower()
        if "chair" in nm:
            chairs.append(c)
    return codes, chairs


def _calendar_for_window(
    db: Session,
    d0: date,
    d1: date,
    state_code: str | None,
    bioguide_id: str | None = None,
) -> tuple[bool, float, str | None, str | None, str | None]:
    comm, chair = _senate_committee_calendar_context(db, bioguide_id)
    disc, et, en = get_calendar_discount(
        db,
        d0,
        d1,
        state_code,
        committee_codes=comm if comm else None,
        chair_committee_codes=chair if chair else None,
    )
    adjacent = disc < 1.0
    note = f"{en} ({et})" if en and et else None
    return adjacent, float(disc), note, et, en


def _senator_state_for_calendar(
    bg: str | None,
    profile_state: str | None,
    db: Session | None = None,
) -> str | None:
    st = (profile_state or "").strip().upper()[:2]
    if len(st) == 2:
        return st
    b = (bg or "").strip()
    if b in SENATOR_HOME_STATE:
        return SENATOR_HOME_STATE[b]
    if db is not None and b:
        got = _home_state_from_db(db, b)
        if got:
            return got
    return None


def classify_donor_sector(donor_name: str, employer: str = "", occupation: str = "") -> str | None:
    text = " ".join([donor_name, employer, occupation]).lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return sector
    return None


def occupation_to_sector(occupation: str) -> str:
    """Map free-text FEC occupation to a SECTOR_KEYWORDS bucket; no match → other."""
    occ = (occupation or "").strip().lower()
    if not occ:
        return "other"
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in occ:
                return sector
    return "other"


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


def _nomination_vote_sector(vote_description: str) -> str | None:
    text = (vote_description or "").lower()
    if not text.strip():
        return None
    for role, sector in sorted(
        NOMINATION_ROLE_TO_SECTOR.items(), key=lambda x: -len(x[0])
    ):
        if role in text:
            return sector
    return None


def _revolving_door_vote_relevant(
    vdesc: str | None,
    vq: str | None,
    vres: str | None,
    lda_sectors: set[str],
) -> bool:
    if not lda_sectors:
        return False
    vblob = _vote_text_bundle(vdesc, vq, vres)
    vote_sectors = _sectors_matching_vote_text(vblob)
    if vote_sectors & lda_sectors:
        return True
    if (vq or "").strip().lower() == "on the nomination":
        nom_sec = _nomination_vote_sector(vdesc or "")
        if nom_sec and nom_sec in lda_sectors:
            return True
    return False


def _normalize_match_token(s: str) -> str:
    t = (s or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


REVOLVING_DOOR_EMPLOYER_BLOCKLIST = frozenset(
    _normalize_match_token(p) for p in _REVOLVING_DOOR_EMPLOYER_BLOCKLIST_RAW
)


def _revolving_door_donor_blocked(display_name: str) -> bool:
    """Pass-through / party infra donors — exclude from revolving-door matching."""
    dn = _normalize_match_token(display_name)
    if not dn:
        return False
    if dn in REVOLVING_DOOR_DONOR_BLOCKLIST:
        return True
    return any(phrase in dn for phrase in REVOLVING_DOOR_DONOR_BLOCKLIST)


def _revolving_door_employer_blocked(normalized_employer: str) -> bool:
    """Generic / vacant employer strings — do not use for employer→LDA substring match."""
    if not (normalized_employer or "").strip():
        return True
    if normalized_employer in REVOLVING_DOOR_EMPLOYER_BLOCKLIST:
        return True
    for phrase in REVOLVING_DOOR_EMPLOYER_BLOCKLIST:
        if " " in phrase:
            if phrase in normalized_employer:
                return True
        elif phrase in normalized_employer.split():
            return True
    return False


def _lda_substring_hit(needle: str, haystack: str) -> bool:
    """Contiguous substring only; short needles avoid keyword collisions."""
    if len(needle) < REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN:
        return False
    return bool(needle and haystack and needle in haystack)


def _lda_employer_substring_hit(needle: str, haystack: str) -> bool:
    """Employer-only matches use a longer needle to reduce generic-term overlap."""
    if len(needle) < REVOLVING_DOOR_MIN_EMPLOYER_SUBSTRING_LEN:
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
        if (
            not hit
            and em
            and not _revolving_door_employer_blocked(em)
            and (
                _lda_employer_substring_hit(em, rn) or _lda_employer_substring_hit(em, cn)
            )
        ):
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


def _fec_fields_from_signal(
    db: Session, sig: Signal
) -> tuple[str, str, str | None, str | None, str]:
    employer, occupation = "", ""
    c_state: str | None = None
    c_zip: str | None = None
    donor_type = "individual"
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
        col_dt = getattr(entry, "donor_type", None)
        if col_dt and str(col_dt).strip():
            donor_type = str(col_dt).strip()
        else:
            raw_dt = _nonempty_str(raw.get("donor_type"))
            if raw_dt:
                donor_type = raw_dt
            else:
                committee = raw.get("committee") if isinstance(raw.get("committee"), dict) else {}
                ct_raw = committee.get("committee_type") if isinstance(committee, dict) else None
                donor_type = classify_donor_type(
                    str(raw.get("entity_type") or ""),
                    str(ct_raw) if ct_raw is not None else None,
                )
        break
    return employer, occupation, c_state, c_zip, donor_type


def _is_individual_donor(donor_name: str) -> bool:
    """False if name resembles an org / committee / PAC — excluded from GEO in/out ratio."""
    up = (donor_name or "").upper()
    for marker in _GEO_ORG_DONOR_MARKERS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(marker)}(?![A-Z0-9])", up):
            return False
    return True


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
    prof_by_case = _subject_bioguides_by_case(db)
    rows = db.execute(
        select(
            EvidenceEntry.id,
            EvidenceEntry.case_file_id,
            EvidenceEntry.date_of_event,
            EvidenceEntry.raw_data_json,
        ).where(
            EvidenceEntry.entry_type.in_(("vote_record", "amendment_vote")),
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all()
    m: dict[uuid.UUID, list[tuple[uuid.UUID, date]]] = {}
    for eid, cid, d, raw_json in rows:
        if cid is None or d is None:
            continue
        try:
            raw = json.loads(raw_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        subj = prof_by_case.get(cid)
        if not _vote_qualifies_for_nearest_vote_map(raw, subj):
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


def _baseline_election_cycle_discount(midpoint: date, cycles_included: list[int]) -> float:
    """
    Campaign-season discount: if spike midpoint calendar year falls in an even
    FEC cycle year present for this committee, treat spike as expected fundraising.
    """
    election_years = [y for y in cycles_included if y % 2 == 0]
    if election_years and midpoint.year in election_years:
        return BASELINE_ANOMALY_CAMPAIGN_SEASON_DISCOUNT
    return 1.0


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
    bioguide_id: str | None = None
    donor_type: str = "individual"
    employer: str = ""
    occupation: str = ""


def _load_soft_bundle_rows(db: Session) -> list[_SoftBundleRow]:
    """Donor-cluster signals with committee labels + dated financials (fingerprint join)."""
    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    case_cache: dict[uuid.UUID, str | None] = {}
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
        emp, occ, _, __, dnt = _fec_fields_from_signal(db, sig)
        bg = _resolve_bioguide(db, fp, case_cache)
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
                bioguide_id=bg,
                donor_type=dnt,
                employer=emp,
                occupation=occ,
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


def _geo_donation_ranges_overlap(a0: date, a1: date, b0: date, b1: date) -> bool:
    """Inclusive calendar overlap (same committee alerts to merge)."""
    return a0 <= b1 and b0 <= a1


def _geo_build_alert_if_qualified(
    db: Session,
    votes_by_case: dict[uuid.UUID, list[tuple[uuid.UUID, date]]],
    fired_at: datetime,
    window: list[_EnrichedClusterRow],
    d0: date,
    d1: date,
) -> PatternAlert | None:
    individual_keys = {e.donor_key for e in window if _is_individual_donor(e.donor_display)}
    org_keys = {e.donor_key for e in window if not _is_individual_donor(e.donor_display)}
    if len(individual_keys) < GEO_MISMATCH_MIN_DONORS:
        return None
    total_amt = sum(e.amount for e in window if _is_individual_donor(e.donor_display))
    if total_amt < GEO_MISMATCH_MIN_AGGREGATE:
        return None
    bg = next((e.bioguide_id for e in window if e.bioguide_id), None)
    home = _home_state_from_db(db, bg)
    if not home:
        return None
    in_n = out_n = unk_n = 0
    state_counts: dict[str, int] = {}
    for e in window:
        if not _is_individual_donor(e.donor_display):
            continue
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
        return None
    ratio = out_n / classified if classified else 0.0
    if ratio < GEO_MISMATCH_OUT_OF_STATE_THRESHOLD:
        return None
    top_states = [s for s, _ in sorted(state_counts.items(), key=lambda x: -x[1])[:3]]
    case_uuids = {e.case_file_id for e in window}
    midpoint = _cluster_midpoint_date(d0, d1)
    d_days, v_id, v_date, prof, vdesc, vres, vq = _nearest_vote_for_cases(
        db, case_uuids, midpoint, votes_by_case
    )
    dl_adj, dl_disc, dl_note, cet, cen = _calendar_for_window(db, d0, d1, home, bg)
    suspicion = ratio * float(prof) * dl_disc
    donor_labels = sorted({e.donor_display for e in window})
    preview = ", ".join(donor_labels[:5])
    if len(donor_labels) > 5:
        preview = f"{preview}, +{len(donor_labels) - 5} more"
    return PatternAlert(
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
        individual_donor_count=len(individual_keys),
        org_donor_count=len(org_keys),
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
        calendar_event_type=cet,
        calendar_event_name=cen,
        suspicion_score=suspicion,
        senator_state=home,
        out_of_state_ratio=float(ratio),
        out_of_state_count=out_n,
        in_state_count=in_n,
        unknown_state_count=unk_n,
        top_donor_states=top_states,
    )


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
        emp, occ, st, zzip, _dnt_unused = _fec_fields_from_signal(db, sig)
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
        bg_sec = next((e.bioguide_id for e in sector_rows if e.bioguide_id), None)
        st_cal = _senator_state_for_calendar(bg_sec, None, db)
        dl_adj, dl_disc, dl_note, cet, cen = _calendar_for_window(
            db, d0, d1, st_cal, bg_sec
        )
        mult = 1.5 if vm else 1.0
        suspicion = conc * float(prof) * dl_disc * mult
        donor_labels = sorted({e.donor_display for e in sector_rows})
        preview = ", ".join(donor_labels[:5])
        if len(donor_labels) > 5:
            preview = f"{preview}, +{len(donor_labels) - 5} more"
        ev_sec = sorted({str(e.signal_id) for e in sector_rows})
        prof_sec = _subject_bioguides_by_case(db)
        skipped_sec = _count_skipped_votes_for_cases(db, case_uuids, prof_sec)
        alerts.append(
            PatternAlert(
                rule_id=RULE_SECTOR_CONVERGENCE,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Sector convergence — {sector_key} — {preview}",
                matched_officials=sorted({e.official_name for e in sector_rows}),
                matched_case_ids=sorted({str(e.case_file_id) for e in sector_rows}),
                committee=sector_rows[0].committee_display,
                window_days=int((d1 - d0).days),
                evidence_refs=ev_sec,
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
                calendar_event_type=cet,
                calendar_event_name=cen,
                suspicion_score=suspicion,
                sector=sector_key,
                sector_donor_count=int(s_n),
                sector_aggregate=float(s_agg),
                sector_concentration=float(conc),
                sector_vote_match=vm,
                payload_extra={
                    "match_type": "direct",
                    "epistemic_basis": "fec_sector_donor_cluster_vote_text",
                    "evidence_refs": ev_sec,
                    "score_components": {
                        "sector_concentration": conc,
                        "proximity_to_vote": prof,
                        "calendar_discount": dl_disc,
                        "vote_text_sector_multiplier": mult,
                    },
                    "skipped_votes": skipped_sec,
                },
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
    for _committee_key, (_disp, events) in by_committee.items():
        if len(events) < GEO_MISMATCH_MIN_DONORS:
            continue
        dates_sorted = sorted({e.d for e in events})
        raw_alerts: list[PatternAlert] = []
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > GEO_MISMATCH_WINDOW_DAYS:
                    continue
                window = [e for e in events if d0 <= e.d <= d1]
                ind_keys = {e.donor_key for e in window if _is_individual_donor(e.donor_display)}
                if len(ind_keys) < GEO_MISMATCH_MIN_DONORS:
                    continue
                ind_amt = sum(e.amount for e in window if _is_individual_donor(e.donor_display))
                if ind_amt < GEO_MISMATCH_MIN_AGGREGATE:
                    continue
                bg = next((e.bioguide_id for e in window if e.bioguide_id), None)
                home = _home_state_from_db(db, bg)
                if not home:
                    continue
                in_n = out_n = unk_n = 0
                for e in window:
                    if not _is_individual_donor(e.donor_display):
                        continue
                    b = _geo_bucket(e.donor_display, e.contributor_state, home)
                    if b == "in":
                        in_n += 1
                    elif b == "out":
                        out_n += 1
                    else:
                        unk_n += 1
                classified = in_n + out_n
                if classified < GEO_MISMATCH_MIN_DONORS:
                    continue
                ratio = out_n / classified if classified else 0.0
                if ratio < GEO_MISMATCH_OUT_OF_STATE_THRESHOLD:
                    continue
                built = _geo_build_alert_if_qualified(
                    db, votes_by_case, fired_at, window, d0, d1
                )
                if built is not None:
                    raw_alerts.append(built)

        if not raw_alerts:
            continue

        n_raw = len(raw_alerts)
        g_parent = list(range(n_raw))

        def _g_find(x: int) -> int:
            while g_parent[x] != x:
                g_parent[x] = g_parent[g_parent[x]]
                x = g_parent[x]
            return x

        def _g_union(ai: int, bi: int) -> None:
            ra, rb = _g_find(ai), _g_find(bi)
            if ra != rb:
                g_parent[rb] = ra

        for i in range(n_raw):
            for j in range(i + 1, n_raw):
                ai, aj = raw_alerts[i], raw_alerts[j]
                if (ai.out_of_state_ratio or 0.0) < GEO_MISMATCH_OUT_OF_STATE_THRESHOLD:
                    continue
                if (aj.out_of_state_ratio or 0.0) < GEO_MISMATCH_OUT_OF_STATE_THRESHOLD:
                    continue
                s_i, e_i = ai.donation_window_start, ai.donation_window_end
                s_j, e_j = aj.donation_window_start, aj.donation_window_end
                if s_i is None or e_i is None or s_j is None or e_j is None:
                    continue
                if not _geo_donation_ranges_overlap(s_i, e_i, s_j, e_j):
                    continue
                _g_union(i, j)

        comp_indices: dict[int, list[int]] = defaultdict(list)
        for i in range(n_raw):
            comp_indices[_g_find(i)].append(i)

        committee_merged: list[PatternAlert] = []
        for _root, idxs in comp_indices.items():
            group = [raw_alerts[i] for i in idxs]
            d0m = min(a.donation_window_start for a in group if a.donation_window_start)
            d1m = max(a.donation_window_end for a in group if a.donation_window_end)
            rep = max(
                group,
                key=lambda a: (
                    (a.individual_donor_count or 0),
                    (a.suspicion_score or 0.0),
                ),
            )
            ev_union: set[str] = set()
            for a in group:
                ev_union.update(a.evidence_refs)
            merged_window = [e for e in events if d0m <= e.d <= d1m]
            rebuilt = _geo_build_alert_if_qualified(
                db, votes_by_case, fired_at, merged_window, d0m, d1m
            )
            if rebuilt is None:
                merged_alert = replace(
                    rep,
                    donation_window_start=d0m,
                    donation_window_end=d1m,
                    window_days=int((d1m - d0m).days),
                    evidence_refs=sorted(ev_union),
                )
            else:
                merged_alert = replace(rebuilt, evidence_refs=sorted(ev_union))
                if (rep.individual_donor_count or 0) > (rebuilt.individual_donor_count or 0):
                    merged_alert = replace(merged_alert, donor_entity=rep.donor_entity)
            committee_merged.append(merged_alert)

        committee_merged.sort(key=lambda a: -(a.suspicion_score or 0.0))
        alerts.extend(committee_merged[:GEO_MISMATCH_MAX_ALERTS_PER_COMMITTEE])
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


def _schedule_b_recipient_committee_id(raw: dict[str, Any]) -> str | None:
    r = raw.get("recipient_committee_id") or raw.get("recipient_committee_fec_id")
    if r:
        s = str(r).strip().upper()
        return s if s else None
    rc = raw.get("recipient_committee")
    if isinstance(rc, dict):
        cid = rc.get("committee_id") or rc.get("fec_id")
        if cid:
            s = str(cid).strip().upper()
            return s if s else None
    return None


def _schedule_b_spender_committee_id(raw: dict[str, Any]) -> str | None:
    comm = raw.get("committee_id")
    if isinstance(comm, dict):
        s = str(comm.get("committee_id") or "").strip().upper()
        return s if s else None
    if raw.get("committee_id"):
        s = str(raw.get("committee_id")).strip().upper()
        return s if s else None
    return None


def _detect_disbursement_loop(db: Session, fired_at: datetime) -> list[PatternAlert]:
    disbursements = db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.entry_type == "fec_disbursement")
    ).all()
    sa_by_case: dict[uuid.UUID, int] = defaultdict(int)
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.entry_type == "financial_connection",
            or_(EvidenceEntry.source_name == "FEC", EvidenceEntry.adapter_name == "FEC"),
        )
    ).all():
        sa_by_case[ent.case_file_id] += 1
    dis_by_case: dict[uuid.UUID, int] = defaultdict(int)
    for ent in disbursements:
        dis_by_case[ent.case_file_id] += 1
    for cid, n in sorted(dis_by_case.items(), key=lambda x: str(x[0])):
        logger.info(
            "[DISBURSEMENT_LOOP] case=%s disbursement_entries=%s schedule_a_entries=%s",
            cid,
            n,
            sa_by_case.get(cid, 0),
        )

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
        rec_id = _schedule_b_recipient_committee_id(raw)
        if not rec_id:
            continue
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
        disb_c = _schedule_b_spender_committee_id(raw) or ""
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


def _case_ids_for_bioguide(db: Session, bioguide_id: str) -> list[uuid.UUID]:
    rows = db.execute(
        select(SubjectProfile.case_file_id).where(
            SubjectProfile.bioguide_id == bioguide_id.strip()
        )
    ).all()
    return [r[0] for r in rows if r[0] is not None]


def _fec_receipt_date_amount_pairs_for_bioguide(
    db: Session, bioguide_id: str
) -> list[tuple[date, float]]:
    pairs: list[tuple[date, float]] = []
    for cid in _case_ids_for_bioguide(db, bioguide_id):
        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == cid,
                or_(
                    EvidenceEntry.entry_type == "financial_connection",
                    EvidenceEntry.entry_type == "fec_historical",
                    EvidenceEntry.entry_type == "fec_jfc_donor",
                ),
            )
        ).all():
            if ent.entry_type == "financial_connection":
                if (ent.source_name or "") != "FEC" and (ent.adapter_name or "") != "FEC":
                    continue
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            raw_d = raw.get("contribution_receipt_date") or ""
            ds = str(raw_d).strip()[:10]
            try:
                d = date.fromisoformat(ds) if ds else None
            except ValueError:
                d = None
            if d is None:
                continue
            try:
                amt = float(raw.get("contribution_receipt_amount") or ent.amount or 0)
            except (TypeError, ValueError):
                amt = float(ent.amount or 0)
            if amt > 0:
                pairs.append((d, amt))
    return pairs


def _fec_receipt_evidence_ids_for_case_window(
    db: Session, case_id: uuid.UUID, d0: date, d1: date
) -> list[str]:
    """Evidence entry IDs on this case whose receipt date falls in [d0, d1] (FEC-style rows)."""
    ids: set[str] = set()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            or_(
                EvidenceEntry.entry_type == "financial_connection",
                EvidenceEntry.entry_type == "fec_historical",
                EvidenceEntry.entry_type == "fec_jfc_donor",
            ),
        )
    ).all():
        if ent.entry_type == "financial_connection":
            if (ent.source_name or "") != "FEC" and (ent.adapter_name or "") != "FEC":
                continue
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        raw_d = raw.get("contribution_receipt_date") or ""
        ds = str(raw_d).strip()[:10]
        try:
            d = date.fromisoformat(ds) if ds else None
        except ValueError:
            d = ent.date_of_event
        if d is None:
            continue
        if not (d0 <= d <= d1):
            continue
        try:
            amt = float(raw.get("contribution_receipt_amount") or ent.amount or 0)
        except (TypeError, ValueError):
            amt = float(ent.amount or 0)
        if amt > 0:
            ids.add(str(ent.id))
    return sorted(ids)


def _fec_cycles_present_for_bioguide(db: Session, bioguide_id: str) -> list[int]:
    cycles: set[int] = set()
    for cid in _case_ids_for_bioguide(db, bioguide_id):
        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == cid,
                or_(
                    EvidenceEntry.entry_type == "fec_historical",
                    EvidenceEntry.entry_type == "financial_connection",
                ),
            )
        ).all():
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            cy = raw.get("fec_cycle") or raw.get("two_year_transaction_period")
            if cy is not None:
                try:
                    cycles.add(int(cy))
                except (TypeError, ValueError):
                    pass
        cy2 = date.today().year
        cycles.add(cy2 if cy2 % 2 == 0 else cy2 + 1)
    return sorted(cycles)


def _principal_committee_id_for_case(db: Session, case_id: uuid.UUID) -> str | None:
    counts: dict[str, int] = defaultdict(int)
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
        comm = raw.get("committee") or {}
        if isinstance(comm, dict):
            cidv = str(comm.get("committee_id") or "").strip().upper()
            if cidv:
                counts[cidv] += 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _detect_joint_fundraising(db: Session, fired_at: datetime) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    seen: set[tuple[str, str, str]] = set()
    for prof in db.scalars(select(SubjectProfile)).all():
        cid = prof.case_file_id
        case_row = db.get(CaseFile, cid)
        subject = (case_row.subject_name if case_row else "Official").strip() or "Official"
        principal = _principal_committee_id_for_case(db, cid)
        if not principal:
            continue
        jfc_donors: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == cid,
                EvidenceEntry.entry_type == "fec_jfc_donor",
            )
        ).all():
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            jfc_id = str(raw.get("jfc_committee_id") or "").strip().upper()
            name = str(raw.get("contributor_name") or raw.get("matched_name") or "")
            try:
                amt = float(raw.get("contribution_receipt_amount") or ent.amount or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if jfc_id and name:
                jfc_donors[jfc_id].append((name, amt))

        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == cid,
                EvidenceEntry.entry_type == "fec_disbursement",
            )
        ).all():
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            rec = _schedule_b_recipient_committee_id(raw)
            sp = _schedule_b_spender_committee_id(raw)
            if not rec or not sp or rec != principal or sp == principal:
                continue
            try:
                amt = float(raw.get("disbursement_amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt < DISBURSEMENT_LOOP_MIN_AMOUNT:
                continue
            raw_dd = raw.get("disbursement_date") or ""
            dd = str(raw_dd).strip()[:10]
            dedupe_k = (str(cid), sp, dd)
            if dedupe_k in seen:
                continue
            seen.add(dedupe_k)
            upstream = jfc_donors.get(sp, [])
            upstream_sorted = sorted(upstream, key=lambda x: -x[1])[:5]
            alerts.append(
                PatternAlert(
                    rule_id=RULE_JOINT_FUNDRAISING,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"JFC upstream — committee {sp}",
                    matched_officials=[subject],
                    matched_case_ids=[str(cid)],
                    committee=sp,
                    window_days=None,
                    evidence_refs=[str(ent.id)],
                    fired_at=fired_at,
                    aggregate_amount=amt,
                    disbursement_amount=amt,
                    disbursement_date=dd or None,
                    suspicion_score=min(1.0, 0.4 + 0.1 * min(len(upstream), 15)),
                    payload_extra={
                        "jfc_name": str(raw.get("recipient_name") or sp),
                        "jfc_committee_id": sp,
                        "disbursement_amount": amt,
                        "disbursement_date": dd,
                        "upstream_donor_count": len({u[0] for u in upstream}),
                        "upstream_donors": [x[0] for x in upstream_sorted],
                    },
                )
            )
    return alerts


def _detect_baseline_anomaly(
    db: Session, fired_at: datetime, cases_with_votes: set[uuid.UUID]
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    votes_by_case = _vote_evidence_by_case(db)
    for prof in db.scalars(select(SubjectProfile)).all():
        bg = (prof.bioguide_id or "").strip()
        cid = prof.case_file_id
        case_row = db.get(CaseFile, cid)
        subject_lbl = (case_row.subject_name if case_row else "Official").strip() or "Official"
        if not bg:
            continue
        if cid not in cases_with_votes:
            continue
        pairs = _fec_receipt_date_amount_pairs_for_bioguide(db, bg)
        if len(pairs) < BASELINE_ANOMALY_MIN_DATAPOINTS:
            continue
        median = _median_seven_day_intake_for_bioguide(db, bg)
        if median is None or median <= 0:
            continue
        dates_sorted = sorted({d for d, _ in pairs})
        seen_win: set[tuple[date, date]] = set()
        case_alerts: list[PatternAlert] = []
        principal = _principal_committee_id_for_case(db, cid) or ""
        cycles_included = _fec_cycles_present_for_bioguide(db, bg)
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > 7:
                    continue
                if (d0, d1) in seen_win:
                    continue
                tot = sum(amt for d, amt in pairs if d0 <= d <= d1)
                if tot < BASELINE_ANOMALY_MIN_AGGREGATE:
                    continue
                mult = tot / median
                state_cal = _senator_state_for_calendar(bg, prof.state, db)
                dl_adj, dl_discount, dl_note, cet, cen = _calendar_for_window(
                    db, d0, d1, state_cal, bg
                )
                min_mult = (
                    BASELINE_ANOMALY_CALENDAR_ADJACENT_MIN_MULTIPLIER
                    if dl_adj
                    else BASELINE_ANOMALY_MIN_MULTIPLIER
                )
                if mult < min_mult:
                    continue
                midpoint = _cluster_midpoint_date(d0, d1)
                d_days, v_id, v_date, v_prof, v_desc, v_res, v_q = _nearest_vote_for_cases(
                    db, {cid}, midpoint, votes_by_case
                )
                if d_days is None or d_days > BASELINE_ANOMALY_MAX_DAYS_TO_NEAREST_VOTE:
                    continue
                seen_win.add((d0, d1))
                base_suspicion = min(1.0, mult / 8.0)
                suspicion = base_suspicion * dl_discount if dl_adj else base_suspicion
                election_cycle_discount = _baseline_election_cycle_discount(
                    midpoint, cycles_included
                )
                suspicion = suspicion * election_cycle_discount
                ev_ids = _fec_receipt_evidence_ids_for_case_window(db, cid, d0, d1)
                pe = {
                    "window_aggregate": float(tot),
                    "senator_median_7day": float(median),
                    "baseline_multiplier": float(mult),
                    "baseline_datapoints": len(pairs),
                    "cycles_included": cycles_included,
                    "base_suspicion_before_deadline": float(base_suspicion),
                    "election_cycle_discount": float(election_cycle_discount),
                    "max_days_to_nearest_vote": BASELINE_ANOMALY_MAX_DAYS_TO_NEAREST_VOTE,
                }
                case_alerts.append(
                    PatternAlert(
                        rule_id=RULE_BASELINE_ANOMALY,
                        pattern_version=PATTERN_ENGINE_VERSION,
                        donor_entity=f"Baseline spike — {mult:.1f}× median 7-day intake",
                        matched_officials=[subject_lbl],
                        matched_case_ids=[str(cid)],
                        committee=principal,
                        window_days=int((d1 - d0).days),
                        evidence_refs=ev_ids,
                        fired_at=fired_at,
                        donation_window_start=d0,
                        donation_window_end=d1,
                        aggregate_amount=float(tot),
                        suspicion_score=suspicion,
                        deadline_adjacent=dl_adj,
                        deadline_discount=dl_discount,
                        deadline_note=dl_note,
                        days_to_nearest_vote=d_days,
                        nearest_vote_id=v_id,
                        nearest_vote_date=v_date,
                        nearest_vote_description=v_desc,
                        nearest_vote_result=v_res,
                        nearest_vote_question=v_q,
                        proximity_to_vote_score=v_prof,
                        payload_extra=pe,
                        calendar_event_type=cet,
                        calendar_event_name=cen,
                        vote_context_available=True,
                    )
                )
        case_alerts.sort(key=lambda a: -(a.suspicion_score or 0.0))
        alerts.extend(case_alerts[:BASELINE_ANOMALY_MAX_ALERTS_PER_CASE])
    return alerts


def _lda_active_for_sector_on_date(
    db: Session, case_id: uuid.UUID, sector: str, vote_day: date
) -> bool:
    window_start = vote_day - timedelta(days=ALIGNMENT_LDA_ACTIVE_DAYS)
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "lobbying_filing",
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        codes = raw.get("issue_codes") or []
        if not isinstance(codes, list):
            continue
        sectors: set[str] = set()
        for c in codes:
            sec = ISSUE_CODE_TO_SECTOR.get(str(c).strip().upper())
            if sec:
                sectors.add(sec)
        if sector not in sectors:
            continue
        fy = raw.get("filing_year")
        try:
            y = int(fy) if fy is not None else None
        except (TypeError, ValueError):
            y = None
        if y is None:
            continue
        if date(y, 1, 1) <= vote_day <= date(y, 12, 31):
            return True
    return False


def _senator_vote_position_from_record(raw: dict[str, Any]) -> str | None:
    for k in (
        "member_vote",
        "vote_position",
        "position",
        "cast_code_name",
        "result_of_vote_position",
    ):
        v = raw.get(k)
        if v and str(v).strip():
            return str(v).strip().upper()
    return None


def _compute_case_sector_alignment_rates(
    db: Session, case_id: uuid.UUID
) -> dict[str, dict[str, Any]]:
    votes = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "vote_record",
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all()
    buckets: dict[str, list[str]] = defaultdict(list)
    for ent in votes:
        d = ent.date_of_event
        if d is None:
            continue
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        pos = _senator_vote_position_from_record(raw)
        if not pos:
            continue
        yn = "yea" if pos in ("YEA", "YES", "AYE") else ("nay" if pos in ("NAY", "NO") else "")
        if yn not in ("yea", "nay"):
            continue
        for sector in SECTOR_KEYWORDS:
            if _lda_active_for_sector_on_date(db, case_id, sector, d):
                buckets[sector].append(yn)
    out: dict[str, dict[str, Any]] = {}
    for sector, vals in buckets.items():
        yea = sum(1 for x in vals if x == "yea")
        out[sector] = {
            "alignment_rate": yea / len(vals) if vals else 0.0,
            "vote_count": len(vals),
            "lda_active_votes": len(vals),
        }
    return out


def _alignment_vote_evidence_refs_for_sector(
    db: Session, case_id: uuid.UUID, sector: str
) -> list[str]:
    """Vote evidence entry IDs that contributed to sector alignment buckets (epistemic trace)."""
    votes = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "vote_record",
            EvidenceEntry.date_of_event.isnot(None),
        )
    ).all()
    ids: list[str] = []
    for ent in votes:
        d = ent.date_of_event
        if d is None:
            continue
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        pos = _senator_vote_position_from_record(raw)
        if not pos:
            continue
        yn = "yea" if pos in ("YEA", "YES", "AYE") else ("nay" if pos in ("NAY", "NO") else "")
        if yn not in ("yea", "nay"):
            continue
        if _lda_active_for_sector_on_date(db, case_id, sector, d):
            ids.append(str(ent.id))
    return ids


def _chamber_sector_baseline(db: Session, sector: str) -> dict[str, Any] | None:
    rates: list[float] = []
    seen_bg: set[str] = set()
    for prof in db.scalars(select(SubjectProfile)).all():
        bg = (prof.bioguide_id or "").strip()
        if not bg or bg in seen_bg:
            continue
        seen_bg.add(bg)
        ar = _compute_case_sector_alignment_rates(db, prof.case_file_id)
        if sector not in ar:
            continue
        if ar[sector]["vote_count"] < ALIGNMENT_ANOMALY_MIN_VOTES:
            continue
        rates.append(float(ar[sector]["alignment_rate"]))
    if len(rates) < CHAMBER_BASELINE_MIN_SENATORS:
        return None
    mean = sum(rates) / len(rates)
    var = sum((x - mean) ** 2 for x in rates) / max(len(rates) - 1, 1)
    std = var**0.5
    return {"mean": mean, "std_dev": std if std > 1e-9 else 1e-9, "n": len(rates)}


def _detect_alignment_anomaly(
    db: Session, fired_at: datetime, cases_with_votes: set[uuid.UUID]
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for prof in db.scalars(select(SubjectProfile)).all():
        bg = (prof.bioguide_id or "").strip()
        cid = prof.case_file_id
        if not bg:
            continue
        if cid not in cases_with_votes:
            continue
        ar = _compute_case_sector_alignment_rates(db, cid)
        for sector, data in ar.items():
            if int(data["vote_count"]) < ALIGNMENT_ANOMALY_MIN_VOTES:
                continue
            base = _chamber_sector_baseline(db, sector)
            if base is None:
                continue
            rate = float(data["alignment_rate"])
            z = (rate - base["mean"]) / base["std_dev"]
            if z < ALIGNMENT_ANOMALY_DEVIATION_THRESHOLD:
                continue
            case_row = db.get(CaseFile, cid)
            subj = (case_row.subject_name if case_row else "Official").strip() or "Official"
            vrefs = _alignment_vote_evidence_refs_for_sector(db, cid, sector)
            prof_by = _subject_bioguides_by_case(db)
            skip_n = _count_skipped_votes_for_cases(db, {cid}, prof_by)
            alerts.append(
                PatternAlert(
                    rule_id=RULE_ALIGNMENT_ANOMALY,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"Alignment anomaly — {sector} ({rate:.0%} vs chamber ~{base['mean']:.0%})",
                    matched_officials=[subj],
                    matched_case_ids=[str(cid)],
                    committee="",
                    window_days=None,
                    evidence_refs=list(vrefs),
                    fired_at=fired_at,
                    sector=sector,
                    suspicion_score=min(1.0, z / 3.0),
                    vote_context_available=True,
                    payload_extra={
                        "match_type": "direct",
                        "epistemic_basis": "vote_record_sector_lda_temporal_overlap",
                        "evidence_refs": list(vrefs),
                        "score_components": {
                            "alignment_rate": rate,
                            "z_score": z,
                            "chamber_mean_rate": base["mean"],
                            "chamber_std_dev": base["std_dev"],
                        },
                        "skipped_votes": skip_n,
                        "senator_alignment_rate": rate,
                        "chamber_mean_rate": base["mean"],
                        "chamber_std_dev": base["std_dev"],
                        "z_score": z,
                        "sector_vote_count": int(data["vote_count"]),
                        "sector_active_lda_count": int(data["lda_active_votes"]),
                    },
                )
            )
    return alerts


def _amendment_text_is_weakening(description: str) -> bool:
    low = (description or "").lower()
    return any(kw in low for kw in _AMENDMENT_WEAKENING_KEYWORDS)


def _final_passage_nay_for_bill(
    db: Session, case_id: uuid.UUID, bill_number: str | None
) -> tuple[bool, str | None]:
    if not bill_number:
        return False, None
    bn = str(bill_number).strip().upper()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "vote_record",
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        bill_blob = str(raw.get("bill_number") or raw.get("measure_number") or "")
        if bn and bn not in bill_blob.upper():
            title = str(raw.get("question") or raw.get("vote_question") or "")
            if bn not in title.upper() and "PASSAGE" not in title.upper():
                continue
        title_u = str(raw.get("question") or raw.get("vote_question") or "").upper()
        if "PASSAGE" not in title_u and "ON PASSAGE" not in title_u:
            continue
        pos = _senator_vote_position_from_record(raw)
        if pos in ("NAY", "NO"):
            return True, pos
    return False, None


def _amendment_donor_alignment(
    db: Session, case_id: uuid.UUID, amendment_description: str
) -> bool:
    blob = (amendment_description or "").lower()
    for ent in db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_id,
            EvidenceEntry.entry_type == "lobbying_filing",
        )
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        codes = raw.get("issue_codes") or []
        if not isinstance(codes, list):
            continue
        for c in codes:
            sec = ISSUE_CODE_TO_SECTOR.get(str(c).strip().upper())
            if not sec:
                continue
            if sec == "pharma" and any(
                x in blob for x in ("drug", "pharma", "health", "fda")
            ):
                return True
            if sec == "finance" and any(
                x in blob for x in ("tax", "bank", "financial", "credit")
            ):
                return True
            if sec == "defense" and "defense" in blob:
                return True
            if sec == "energy" and any(x in blob for x in ("energy", "oil", "gas", "climate")):
                return True
    return False


def _detect_amendment_tell(
    db: Session, fired_at: datetime, cases_with_votes: set[uuid.UUID]
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    rows = db.execute(
        select(DonorFingerprint, Signal).join(Signal, DonorFingerprint.signal_id == Signal.id)
    ).all()
    for fp, sig in rows:
        if float(sig.weight or 0) < AMENDMENT_TELL_MIN_SIGNAL_WEIGHT:
            continue
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        case_id = fp.case_file_id
        if case_id not in cases_with_votes:
            continue
        donation_day = _donation_date_for_signal(sig)
        if donation_day is None:
            continue

        for ent in db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == case_id,
                EvidenceEntry.entry_type == "amendment_vote",
                EvidenceEntry.date_of_event.isnot(None),
            )
        ).all():
            avd = ent.date_of_event
            if avd is None:
                continue
            if abs((donation_day - avd).days) > AMENDMENT_TELL_WINDOW_DAYS:
                continue
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            desc = str(raw.get("amendment_description") or raw.get("description") or "")
            pos = str(raw.get("vote_position") or raw.get("position") or "").upper()
            if not _amendment_text_is_weakening(desc):
                continue
            if pos not in ("YEA", "YES", "Y"):
                continue
            bill = raw.get("bill_number") or raw.get("measure_number")
            inc, final_pos = _final_passage_nay_for_bill(db, case_id, str(bill) if bill else None)
            if not inc:
                continue
            donor_align = _amendment_donor_alignment(db, case_id, desc)
            prof = proximity_to_vote_score_from_days(
                abs((donation_day - avd).days)
            )
            bg_am = (fp.bioguide_id or "").strip() or None
            st_cal = _senator_state_for_calendar(bg_am, None, db)
            d0c, d1c = min(donation_day, avd), max(donation_day, avd)
            dl_adj, dl_disc, dl_note, cet, cen = _calendar_for_window(
                db, d0c, d1c, st_cal, bg_am
            )
            suspicion = (
                float(prof)
                * dl_disc
                * (1.5 if inc else 1.0)
                * (1.1 if donor_align else 1.0)
            )
            display = _donor_display_for_signal(
                sig,
                (fp.canonical_id or fp.normalized_donor_key or "").strip(),
            )
            ev_am = sorted({str(sig.id), str(ent.id)})
            prof_am = _subject_bioguides_by_case(db)
            skipped_am = _count_skipped_votes_for_cases(db, {case_id}, prof_am)
            alerts.append(
                PatternAlert(
                    rule_id=RULE_AMENDMENT_TELL,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"Amendment tell — {display}",
                    matched_officials=[(fp.official_name or sig.actor_b or "").strip() or "Official"],
                    matched_case_ids=[str(case_id)],
                    committee=str(bd.get("committee_label") or ""),
                    window_days=AMENDMENT_TELL_WINDOW_DAYS,
                    evidence_refs=ev_am,
                    fired_at=fired_at,
                    aggregate_amount=float(sig.amount or 0.0),
                    suspicion_score=min(1.0, suspicion),
                    deadline_adjacent=dl_adj,
                    deadline_discount=dl_disc,
                    deadline_note=dl_note,
                    calendar_event_type=cet,
                    calendar_event_name=cen,
                    vote_context_available=True,
                    payload_extra={
                        "match_type": "direct",
                        "epistemic_basis": "fec_donor_cluster_amendment_vote_temporal",
                        "evidence_refs": ev_am,
                        "score_components": {
                            "proximity_to_vote": float(prof),
                            "calendar_discount": dl_disc,
                            "inconsistent_record_multiplier": 1.5 if inc else 1.0,
                            "donor_alignment_multiplier": 1.1 if donor_align else 1.0,
                        },
                        "skipped_votes": skipped_am,
                        "amendment_number": str(raw.get("amendment_number") or ""),
                        "amendment_description": desc[:500],
                        "amendment_vote_position": pos,
                        "final_passage_vote_position": final_pos or "",
                        "inconsistent_record": inc,
                        "donor_alignment": donor_align,
                    },
                )
            )
    return alerts


def _normalize_substring_match(a: str, b: str, min_len: int = 6) -> bool:
    aa = re.sub(r"[^a-z0-9]+", "", (a or "").lower())
    bb = re.sub(r"[^a-z0-9]+", "", (b or "").lower())
    if len(aa) < min_len or len(bb) < min_len:
        return False
    return aa in bb or bb in aa


def _match_witness_to_donor_signals(
    witness_name: str,
    witness_org: str,
    signals: list[Signal],
    db: Session,
) -> list[Signal]:
    matched: list[Signal] = []
    for sig in signals:
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        dlab = str(bd.get("donor") or sig.actor_a or "")
        emp, occ, _, __, ___ = _fec_fields_from_signal(db, sig)
        blob = " ".join([dlab, emp, occ])
        if _normalize_substring_match(witness_name, blob, REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN):
            matched.append(sig)
            continue
        if witness_org and _normalize_substring_match(
            witness_org, blob, REVOLVING_DOOR_MIN_NAME_SUBSTRING_LEN
        ):
            matched.append(sig)
    return matched


def _detect_hearing_testimony(db: Session, fired_at: datetime) -> list[PatternAlert]:
    from core.credentials import CredentialRegistry

    try:
        key = CredentialRegistry.get_credential("govinfo")
    except Exception:
        key = None
    if not key:
        logger.info("HEARING_TESTIMONY_V1 skipped — GOVINFO_API_KEY not configured")
        return []

    alerts: list[PatternAlert] = []
    for ent in db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.entry_type == "hearing_witness")
    ).all():
        try:
            raw = json.loads(ent.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        case_id = ent.case_file_id
        matched_name = str(raw.get("matched_name") or "")
        hearing_title = str(raw.get("hearing_title") or ent.title or "")
        hid = str(raw.get("package_id") or "")
        date_s = str(
            raw.get("date_issued") or raw.get("dateIssued") or ent.date_of_event or ""
        )[:10]
        try:
            hday = date.fromisoformat(date_s) if date_s else None
        except ValueError:
            hday = ent.date_of_event
        if hday is None:
            continue
        sigs = db.scalars(
            select(Signal).where(Signal.case_file_id == case_id)
        ).all()
        hits = _match_witness_to_donor_signals(
            matched_name, "", list(sigs), db
        )
        for sig in hits:
            dd = _donation_date_for_signal(sig)
            if dd is None:
                continue
            gap = abs((dd - hday).days)
            if gap > HEARING_TESTIMONY_WINDOW_DAYS:
                continue
            display = _donor_display_for_signal(sig, "")
            amt = float(sig.amount or 0.0)
            votes_by_case = _vote_evidence_by_case(db)
            midpoint = _cluster_midpoint_date(min(dd, hday), max(dd, hday))
            d_days, v_id, v_date, _prof, v_desc, v_res, v_q = _nearest_vote_for_cases(
                db, {case_id}, midpoint, votes_by_case
            )
            payload_ex: dict[str, Any] = {
                "witness_name": matched_name,
                "witness_organization": "",
                "hearing_date": hday.isoformat(),
                "hearing_title": hearing_title,
                "matched_donor": display,
                "donation_amount": amt,
                "donation_date": dd.isoformat(),
                "days_between_testimony_and_donation": gap,
            }
            ref_set = {str(sig.id), str(ent.id)}
            if v_id:
                ref_set.add(str(v_id))
            refs = sorted(ref_set)
            alerts.append(
                PatternAlert(
                    rule_id=RULE_HEARING_TESTIMONY,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"Hearing testimony chain — {display}",
                    matched_officials=[],
                    matched_case_ids=[str(case_id)],
                    committee=hid,
                    window_days=HEARING_TESTIMONY_WINDOW_DAYS,
                    evidence_refs=refs,
                    fired_at=fired_at,
                    aggregate_amount=amt,
                    days_to_nearest_vote=d_days,
                    nearest_vote_id=v_id,
                    nearest_vote_date=v_date,
                    nearest_vote_description=v_desc,
                    nearest_vote_result=v_res,
                    nearest_vote_question=v_q,
                    suspicion_score=min(1.0, 0.35 + gap / 400.0),
                    matched_donor=display,
                    payload_extra=payload_ex,
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
    grouped: dict[tuple[uuid.UUID, str], list[tuple[DonorFingerprint, Signal]]] = defaultdict(
        list
    )
    for fp, sig in rows:
        bd = _signal_breakdown_json(sig)
        if str(bd.get("kind") or "") != "donor_cluster":
            continue
        if not bool(bd.get("has_lda_filing")):
            continue
        dk = (fp.canonical_id or "").strip().lower() or (fp.normalized_donor_key or "").strip().lower()
        if not dk:
            continue
        grouped[(fp.case_file_id, dk)].append((fp, sig))

    alerts: list[PatternAlert] = []
    for (case_id, dk), pairs in grouped.items():
        lda_list = lda_by_case.get(case_id, [])
        if not lda_list:
            continue
        pairs.sort(key=lambda t: str(t[1].id))
        fp0, sig0 = pairs[0]
        display = _donor_display_for_signal(sig0, dk)
        if _revolving_door_donor_blocked(display):
            continue
        emp, _, _, __, ___ = _fec_fields_from_signal(db, sig0)
        matches = match_donor_to_lda(display, emp, lda_list)
        if len(matches) < REVOLVING_DOOR_MIN_MATCHED_DONORS:
            continue
        dates = [
            d
            for _, s in pairs
            if (d := _donation_date_for_signal(s)) is not None
        ]
        if not dates:
            continue
        d_start, d_end = min(dates), max(dates)
        case_uuids = {case_id}
        midpoint = _cluster_midpoint_date(d_start, d_end)
        _, _, _, _, vdesc, vres, vq = _nearest_vote_for_cases(
            db, case_uuids, midpoint, votes_by_case
        )
        by_reg: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for m in matches:
            reg = str(m.get("registrant_name") or "").strip() or str(
                m.get("client_name") or ""
            ).strip()
            if reg:
                by_reg[reg].append(m)
        lda_match_count = len(by_reg)
        evidence_ids = sorted({str(s.id) for _, s in pairs})
        lda_hit_ids: set[str] = set()
        for ent in lda_list:
            if len(match_donor_to_lda(display, emp, [ent])) > 0:
                lda_hit_ids.add(str(ent.id))
        evidence_ids_full = sorted(set(evidence_ids) | lda_hit_ids)
        officials = sorted(
            {
                (fp.official_name or sig.actor_b or "").strip() or "Unknown"
                for fp, sig in pairs
            }
        )
        bd = _signal_breakdown_json(sig0)
        cl = str(bd.get("committee_label") or "")
        prof_rd = _subject_bioguides_by_case(db)
        skipped_rd = _count_skipped_votes_for_cases(db, {case_id}, prof_rd)
        for reg, reg_matches in sorted(by_reg.items()):
            codes_set: set[str] = set()
            fy_best: int | None = None
            for m in reg_matches:
                cr = m.get("issue_codes") or []
                if isinstance(cr, list):
                    for c in cr:
                        codes_set.add(str(c).strip().upper())
                fy = m.get("filing_year")
                try:
                    fy_i = int(fy) if fy is not None else None
                except (TypeError, ValueError):
                    fy_i = None
                if fy_i is not None and (fy_best is None or fy_i > fy_best):
                    fy_best = fy_i
            codes = sorted(codes_set)
            lda_sectors: set[str] = set()
            for c in codes:
                s_sec = ISSUE_CODE_TO_SECTOR.get(str(c).strip().upper())
                if s_sec:
                    lda_sectors.add(s_sec)
            rel = _revolving_door_vote_relevant(vdesc, vq, vres, lda_sectors)
            base_score = 1.0 if rel else 0.6
            alerts.append(
                PatternAlert(
                    rule_id=RULE_REVOLVING_DOOR,
                    pattern_version=PATTERN_ENGINE_VERSION,
                    donor_entity=f"Revolving door — {display}",
                    matched_officials=officials,
                    matched_case_ids=[str(case_id)],
                    committee=cl,
                    window_days=None,
                    evidence_refs=evidence_ids,
                    fired_at=fired_at,
                    donation_window_start=d_start,
                    donation_window_end=d_end,
                    nearest_vote_description=vdesc,
                    nearest_vote_result=vres,
                    nearest_vote_question=vq,
                    matched_donor=display,
                    matched_lda_registrant=reg,
                    matched_issue_codes=codes,
                    revolving_door_vote_relevant=rel,
                    lda_filing_year=fy_best,
                    lda_match_count=lda_match_count,
                    suspicion_score=base_score,
                    payload_extra={
                        "match_type": "direct",
                        "epistemic_basis": "fec_donor_signals_lda_substring_overlap",
                        "evidence_refs": evidence_ids_full,
                        "score_components": {
                            "vote_relevance_gate": base_score,
                            "lda_registrant_distinct_count": lda_match_count,
                            "issue_code_count": len(codes),
                        },
                        "skipped_votes": skipped_rd,
                    },
                )
            )
    return alerts


def _detect_soft_bundles(
    db: Session, fired_at: datetime, cases_with_votes: set[uuid.UUID]
) -> list[PatternAlert]:
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
        case_uids = {e.case_file_id for e in window}
        if not case_uids.issubset(cases_with_votes):
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
        st_cal = _senator_state_for_calendar(sample_row.bioguide_id, None, db)
        dl_adj, dl_discount, dl_note, cet, cen = _calendar_for_window(
            db, d0, d1, st_cal, sample_row.bioguide_id
        )
        div_f = float(div) if div is not None else 0.0
        size_factor = min(int(n_donors) / 10.0, 1.0)
        suspicion = div_f * prof * dl_discount * size_factor
        ev_ids = sorted({str(e.signal_id) for e in window})
        prof_by_sb = _subject_bioguides_by_case(db)
        skipped_v = _count_skipped_votes_for_cases(db, case_uuids, prof_by_sb)
        alerts.append(
            PatternAlert(
                rule_id=RULE_SOFT_BUNDLE,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Soft bundle — {n_donors} donors ({preview})",
                matched_officials=sorted({e.official_name for e in window}),
                matched_case_ids=sorted({str(e.case_file_id) for e in window}),
                committee=sample_row.committee_display,
                window_days=int(span_days),
                evidence_refs=ev_ids,
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
                calendar_event_type=cet,
                calendar_event_name=cen,
                vote_context_available=True,
                suspicion_score=suspicion,
                payload_extra={
                    "match_type": "direct",
                    "epistemic_basis": "fec_donor_cluster_signals",
                    "evidence_refs": ev_ids,
                    "score_components": {
                        "amount_diversification": div_f,
                        "proximity_to_vote": prof,
                        "calendar_discount": dl_discount,
                        "cluster_size_factor": size_factor,
                    },
                    "skipped_votes": skipped_v,
                },
            )
        )
    return alerts


def _median_seven_day_intake_for_bioguide(db: Session, bioguide_id: str) -> float | None:
    """Median rolling 7-day FEC receipt totals (multi-cycle baseline), not soft-bundle heuristics."""
    events = _fec_receipt_date_amount_pairs_for_bioguide(db, bioguide_id)
    if len(events) < BASELINE_ANOMALY_MIN_DATAPOINTS:
        return None
    dates_sorted = sorted({d for d, _ in events})
    totals: list[float] = []
    for d0 in dates_sorted:
        d_end = d0 + timedelta(days=6)
        tot = sum(amt for d, amt in events if d0 <= d <= d_end)
        totals.append(tot)
    if len(totals) < 3:
        return None
    totals.sort()
    n = len(totals)
    mid = n // 2
    if n % 2:
        return float(totals[mid])
    return float(totals[mid - 1] + totals[mid]) / 2.0


def _hearing_within_days_of_midpoint(
    db: Session,
    case_ids: set[uuid.UUID],
    midpoint: date,
    half_span: int,
) -> bool:
    lo = midpoint - timedelta(days=half_span)
    hi = midpoint + timedelta(days=half_span)
    for cid in case_ids:
        hit = db.execute(
            select(EvidenceEntry.id).where(
                EvidenceEntry.case_file_id == cid,
                EvidenceEntry.entry_type.in_(_HEARING_V2_ENTRY_TYPES),
                EvidenceEntry.date_of_event.isnot(None),
                EvidenceEntry.date_of_event >= lo,
                EvidenceEntry.date_of_event <= hi,
            ).limit(1)
        ).first()
        if hit:
            return True
    return False


def _detect_soft_bundle_v2(
    db: Session, fired_at: datetime, cases_with_votes: set[uuid.UUID]
) -> list[PatternAlert]:
    votes_by_case = _vote_evidence_by_case(db)
    by_committee: dict[str, tuple[str, list[_SoftBundleRow]]] = {}
    for row in _load_soft_bundle_rows(db):
        ck = row.committee_key
        if ck not in by_committee:
            by_committee[ck] = (row.committee_display, [])
        by_committee[ck][1].append(row)

    median_cache: dict[str, float | None] = {}

    qualifying: list[
        tuple[frozenset[uuid.UUID], list[_SoftBundleRow], date, date, int, float]
    ] = []
    for _committee_display, events in by_committee.values():
        if len(events) < SOFT_BUNDLE_V2_MIN_DONORS:
            continue
        dates_sorted = sorted({e.d for e in events})
        for d0 in dates_sorted:
            for d1 in dates_sorted:
                if (d1 - d0).days > SOFT_BUNDLE_V2_WINDOW_DAYS:
                    continue
                window = [e for e in events if d0 <= e.d <= d1]
                donors = {e.donor_key for e in window}
                if len(donors) < SOFT_BUNDLE_V2_MIN_DONORS:
                    continue
                total = sum(e.amount for e in window)
                if total < SOFT_BUNDLE_V2_MIN_AGGREGATE:
                    continue
                fs = frozenset(e.signal_id for e in window)
                qualifying.append((fs, window, d0, d1, len(donors), total))

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
        fs, window, d0, d1, n_donors, total = tup
        if fs not in maximal_sets:
            continue
        case_uids_v2 = {e.case_file_id for e in window}
        if not case_uids_v2.issubset(cases_with_votes):
            continue
        donor_labels = sorted({e.donor_display for e in window})
        preview = ", ".join(donor_labels[:5])
        if len(donor_labels) > 5:
            preview = f"{preview}, +{len(donor_labels) - 5} more"
        sample_row = window[0]
        span_days = (d1 - d0).days
        case_uuids = {e.case_file_id for e in window}
        midpoint = _cluster_midpoint_date(d0, d1)

        donor_keys = {e.donor_key for e in window}
        n_total = len(donor_keys)
        n_indiv = len({e.donor_key for e in window if e.donor_type == "individual"})
        individual_fraction = n_indiv / n_total if n_total else 0.0

        occ_by_dk: dict[str, str] = {}
        for e in window:
            if e.donor_key not in occ_by_dk:
                occ_by_dk[e.donor_key] = e.occupation or ""
        sector_by_dk = {dk: occupation_to_sector(occ) for dk, occ in occ_by_dk.items()}
        sec_counts = Counter(sector_by_dk.values())
        sector_similarity = 0.0
        if sector_by_dk:
            top_ct = sec_counts.most_common(1)[0][1]
            sector_similarity = top_ct / len(sector_by_dk)

        bg = next((e.bioguide_id for e in window if e.bioguide_id), None)
        baseline_ratio: float | None = None
        median_intake: float | None = None
        if bg:
            if bg not in median_cache:
                median_cache[bg] = _median_seven_day_intake_for_bioguide(db, bg)
            median_intake = median_cache[bg]
            if median_intake and median_intake > 0:
                baseline_ratio = float(total) / float(median_intake)

        hearing_nearby = _hearing_within_days_of_midpoint(
            db, case_uuids, midpoint, SOFT_BUNDLE_V2_HEARING_WINDOW_DAYS
        )

        base_weight = min(1.0, float(total) / 50000.0)
        adjustments: list[dict[str, Any]] = []
        w = base_weight
        if individual_fraction >= 0.7:
            w += SOFT_BUNDLE_V2_INDIVIDUAL_WEIGHT_BONUS
            adjustments.append(
                {"component": "individual_bonus", "delta": SOFT_BUNDLE_V2_INDIVIDUAL_WEIGHT_BONUS}
            )
        if individual_fraction <= 0.3:
            w += SOFT_BUNDLE_V2_ORG_DOMINATED_PENALTY
            adjustments.append(
                {"component": "org_dominated_penalty", "delta": SOFT_BUNDLE_V2_ORG_DOMINATED_PENALTY}
            )
        if sector_similarity >= SOFT_BUNDLE_V2_SECTOR_THRESHOLD:
            w += SOFT_BUNDLE_V2_SECTOR_WEIGHT_BONUS
            adjustments.append(
                {"component": "sector_bonus", "delta": SOFT_BUNDLE_V2_SECTOR_WEIGHT_BONUS}
            )
        if hearing_nearby:
            w += SOFT_BUNDLE_V2_HEARING_WEIGHT_BONUS
            adjustments.append(
                {"component": "hearing_proximity_bonus", "delta": SOFT_BUNDLE_V2_HEARING_WEIGHT_BONUS}
            )
        if baseline_ratio is not None and baseline_ratio >= SOFT_BUNDLE_V2_BASELINE_MULTIPLIER:
            w += 0.10
            adjustments.append({"component": "baseline_spike_bonus", "delta": 0.10})

        final_weight = max(0.0, min(1.0, w))

        d_days, v_id, v_date, prof, v_desc, v_res, v_q = _nearest_vote_for_cases(
            db, case_uuids, midpoint, votes_by_case
        )
        st_cal = _senator_state_for_calendar(bg, None, db)
        dl_adj, dl_discount, dl_note, cet, cen = _calendar_for_window(db, d0, d1, st_cal, bg)

        plurality = sec_counts.most_common(1)[0][0] if sec_counts else None
        diagnostics: dict[str, Any] = {
            "rule_id": RULE_SOFT_BUNDLE_V2,
            "individual_fraction": individual_fraction,
            "sector_similarity": sector_similarity,
            "plurality_sector": plurality,
            "baseline_ratio": baseline_ratio,
            "median_seven_day_intake": median_intake,
            "hearing_nearby": hearing_nearby,
            "base_weight": base_weight,
            "adjustments": adjustments,
            "final_weight": final_weight,
            "aggregate_amount": float(total),
            "donor_count": n_donors,
            "window_start": d0.isoformat(),
            "window_end": d1.isoformat(),
            "bioguide_id": bg,
        }
        ev_ids_v2 = sorted({str(e.signal_id) for e in window})
        prof_v2 = _subject_bioguides_by_case(db)
        skipped_v2 = _count_skipped_votes_for_cases(db, case_uuids, prof_v2)
        pe_v2: dict[str, Any] = {
            "match_type": "direct",
            "epistemic_basis": "fec_donor_cluster_signals_v2",
            "evidence_refs": ev_ids_v2,
            "score_components": {
                "final_weight": final_weight,
                "base_weight": base_weight,
                "adjustments": adjustments,
                "proximity_to_vote": prof,
                "calendar_discount": dl_discount,
            },
            "skipped_votes": skipped_v2,
        }

        alerts.append(
            PatternAlert(
                rule_id=RULE_SOFT_BUNDLE_V2,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=f"Soft bundle V2 — {n_donors} donors ({preview})",
                matched_officials=sorted({e.official_name for e in window}),
                matched_case_ids=sorted({str(e.case_file_id) for e in window}),
                committee=sample_row.committee_display,
                window_days=int(span_days),
                evidence_refs=ev_ids_v2,
                fired_at=fired_at,
                donation_window_start=d0,
                donation_window_end=d1,
                aggregate_amount=float(total),
                cluster_size=int(n_donors),
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
                calendar_event_type=cet,
                calendar_event_name=cen,
                vote_context_available=True,
                suspicion_score=final_weight,
                diagnostics_json=json.dumps(diagnostics, separators=(",", ":"), default=str),
                payload_extra=pe_v2,
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


_LOCAL_PROCUREMENT_ADAPTERS: frozenset[str] = frozenset(
    {"INDY_PROCUREMENT", "INDY_GATEWAY_CONTRACT_DOC"}
)


def _local_procurement_vendor_variants(entry: EvidenceEntry) -> list[str]:
    """Vendor labels for LOCAL_* rules: procurement rows only; prefers vendor_canonical."""
    if entry.is_absence or (entry.entry_type or "") == "gap_documented":
        return []
    if (entry.adapter_name or "") not in _LOCAL_PROCUREMENT_ADAPTERS:
        return []
    if (entry.entry_type or "") != "government_record":
        return []
    raw: dict[str, Any] = {}
    try:
        raw = json.loads(entry.raw_data_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = (s or "").strip()
        if len(t) < 3:
            return
        tl = t.lower()
        if tl in seen:
            return
        seen.add(tl)
        out.append(t)

    add(str(raw.get("vendor_canonical") or ""))
    add(str(raw.get("vendor_name_raw") or ""))
    add(entry.matched_name or "")
    return out


def _idis_donor_label(entry: EvidenceEntry) -> str:
    if entry.is_absence or (entry.entry_type or "") == "gap_documented":
        return ""
    if (entry.adapter_name or "") != "IDIS":
        return ""
    if (entry.entry_type or "") != "financial_connection":
        return ""
    raw: dict[str, Any] = {}
    try:
        raw = json.loads(entry.raw_data_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    return str(raw.get("contributor_name") or "").strip()


def _local_names_equivalent(name_a: str, name_b: str, db: Session) -> bool:
    a = (name_a or "").strip()
    b = (name_b or "").strip()
    if not a or not b:
        return False
    ca, cb = canonicalize(a), canonicalize(b)
    if ca and cb and ca == cb:
        return True
    ra, rb = resolve(a, db), resolve(b, db)
    return bool(ra.canonical_id) and ra.canonical_id == rb.canonical_id


_LOCAL_TIMING_ELIGIBLE_EVENTS = frozenset({"award"})
_LOCAL_LOOP_ELIGIBLE_EVENTS = frozenset({"award", "supply_purchase", "final_acceptance"})
_LOCAL_LOOP_BLOCKED_EVENTS = frozenset({"amendment", "change_order", "closeout"})


def _local_procurement_raw_dict(entry: EvidenceEntry) -> dict[str, Any]:
    try:
        raw = json.loads(entry.raw_data_json or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _local_contract_event_type(entry: EvidenceEntry) -> str | None:
    raw = _local_procurement_raw_dict(entry)
    v = raw.get("contract_event_type")
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip().lower()


def _idis_donor_raw_dict(entry: EvidenceEntry) -> dict[str, Any]:
    try:
        raw = json.loads(entry.raw_data_json or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _local_loop_score(contract_amt: float | None, donation_amt: float | None) -> float:
    c = max(0.0, float(contract_amt or 0))
    d = max(0.0, float(donation_amt or 0))
    nc = min(1.0, math.log10(c + 1.0) / 8.0)
    nd = min(1.0, math.log10(d + 1.0) / 6.0)
    return max(0.05, min(1.0, 0.12 + 0.38 * nc + 0.50 * nd))


_LOCAL_RELATED_ENTITY_ELIGIBLE_EVENTS = frozenset({"award", "supply_purchase"})


def _local_related_entity_score(contract_amt: float | None, donation_amt: float | None) -> float:
    """Separate band from direct loop: documented link but weaker epistemics."""
    c = max(0.0, float(contract_amt or 0))
    d = max(0.0, float(donation_amt or 0))
    nc = min(1.0, math.log10(c + 1.0) / 8.0)
    nd = min(1.0, math.log10(d + 1.0) / 6.0)
    return max(0.35, min(0.72, 0.35 + 0.20 * nc + 0.17 * nd))


def _local_indirect_match_label(relationship_type: str | None) -> str:
    rt = (relationship_type or "").strip().lower()
    mapping = {
        "pac_of_vendor": "PAC affiliated with vendor",
        "affiliate": "Corporate affiliate of vendor",
        "subsidiary": "Subsidiary of vendor",
        "parent": "Parent company of vendor",
    }
    return mapping.get(rt, "Related entity of vendor")


def _load_local_case_evidence(
    db: Session,
) -> dict[uuid.UUID, tuple[CaseFile, list[EvidenceEntry]]]:
    out: dict[uuid.UUID, tuple[CaseFile, list[EvidenceEntry]]] = {}
    for case in db.scalars(select(CaseFile)).all():
        if (case.government_level or "").strip().lower() != "local":
            continue
        entries = db.scalars(
            select(EvidenceEntry).where(
                EvidenceEntry.case_file_id == case.id,
                EvidenceEntry.is_absence.is_(False),
                or_(
                    EvidenceEntry.adapter_name == "INDY_PROCUREMENT",
                    EvidenceEntry.adapter_name == "INDY_GATEWAY_CONTRACT_DOC",
                    EvidenceEntry.adapter_name == "IDIS",
                ),
            )
        ).all()
        if entries:
            out[case.id] = (case, list(entries))
    return out


def _detect_local_contractor_donor_loop(
    db: Session, fired_at: datetime
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for case_id, (case, entries) in _load_local_case_evidence(db).items():
        jkey = local_jurisdiction_alias_key(case.jurisdiction or "")
        vendor_rows: list[tuple[EvidenceEntry, list[str]]] = []
        idis_rows: list[EvidenceEntry] = []
        for e in entries:
            if (e.adapter_name or "") in _LOCAL_PROCUREMENT_ADAPTERS:
                variants = _local_procurement_vendor_variants(e)
                if variants:
                    vendor_rows.append((e, variants))
            elif (e.adapter_name or "") == "IDIS":
                idis_rows.append(e)

        seen_pairs: set[tuple[str, str]] = set()
        official = (case.subject_name or "").strip() or "Unknown official"
        jurisdiction = (case.jurisdiction or "").strip()

        for vend_ent, vlabels in vendor_rows:
            cet = _local_contract_event_type(vend_ent)
            if cet in _LOCAL_LOOP_BLOCKED_EVENTS:
                continue
            if cet is not None and cet not in _LOCAL_LOOP_ELIGIBLE_EVENTS:
                continue
            vend_raw = _local_procurement_raw_dict(vend_ent)
            v_name_raw = str(vend_raw.get("vendor_name_raw") or "").strip()
            v_canon = str(vend_raw.get("vendor_canonical") or "").strip()
            event_warn: str | None = None
            if cet is None:
                event_warn = "contract_event_type missing on procurement row"
            elif cet != "award":
                event_warn = (
                    f"contract_event_type={cet} is not a primary award; "
                    "interpret loop context cautiously"
                )

            for idis_ent in idis_rows:
                dlabel = _idis_donor_label(idis_ent)
                if not dlabel:
                    continue
                d_raw = _idis_donor_raw_dict(idis_ent)
                d_name_raw = str(d_raw.get("contributor_name_raw") or dlabel).strip()
                d_canon = str(
                    d_raw.get("contributor_canonical")
                    or normalize_vendor_name(dlabel)
                    or canonicalize(dlabel)
                ).strip()
                for vl in vlabels:
                    ok, mtype, rtype, rnote = local_match_eligible_for_loop_and_timing(
                        vl, dlabel, jkey, db
                    )
                    if not ok:
                        continue
                    pair_key = (str(vend_ent.id), str(idis_ent.id))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    c_amt = vend_ent.amount
                    d_amt = idis_ent.amount
                    score = _local_loop_score(c_amt, d_amt)
                    agg: float | None = None
                    if c_amt is not None or d_amt is not None:
                        agg = float(c_amt or 0) + float(d_amt or 0)
                    alerts.append(
                        PatternAlert(
                            rule_id=RULE_LOCAL_CONTRACTOR_DONOR_LOOP,
                            pattern_version=PATTERN_ENGINE_VERSION,
                            donor_entity=(
                                f"Local contractor/donor match: {dlabel} / {vl}"
                            ),
                            matched_officials=[official],
                            matched_case_ids=[str(case_id)],
                            committee=jurisdiction,
                            window_days=None,
                            evidence_refs=sorted([str(vend_ent.id), str(idis_ent.id)]),
                            fired_at=fired_at,
                            suspicion_score=score,
                            aggregate_amount=agg,
                            payload_extra={
                                "vendor_label": vl,
                                "donor_label": dlabel,
                                "match_type": mtype,
                                "relationship_type": rtype,
                                "relationship_source_note": rnote,
                                "vendor_canonical": v_canon or None,
                                "donor_canonical": d_canon or None,
                                "contract_event_type": cet,
                                "vendor_name_raw": v_name_raw or None,
                                "donor_name_raw": d_name_raw or None,
                                "contract_amount": c_amt,
                                "donation_amount": d_amt,
                                "event_type_used": cet,
                                "event_type_warning": event_warn,
                            },
                        )
                    )
                    break
    return alerts


def _detect_local_related_entity_donor(
    db: Session, fired_at: datetime
) -> list[PatternAlert]:
    """Curated related-entity rows only (affiliate / PAC / parent / subsidiary); not direct or alias."""
    LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS["skipped_missing_contract_event_type"] = 0
    alerts: list[PatternAlert] = []
    for case_id, (case, entries) in _load_local_case_evidence(db).items():
        jkey = local_jurisdiction_alias_key(case.jurisdiction or "")
        vendor_rows: list[tuple[EvidenceEntry, list[str]]] = []
        idis_rows: list[EvidenceEntry] = []
        for e in entries:
            if (e.adapter_name or "") in _LOCAL_PROCUREMENT_ADAPTERS:
                variants = _local_procurement_vendor_variants(e)
                if variants:
                    vendor_rows.append((e, variants))
            elif (e.adapter_name or "") == "IDIS":
                idis_rows.append(e)

        seen_pairs: set[tuple[str, str]] = set()
        official = (case.subject_name or "").strip() or "Unknown official"
        jurisdiction = (case.jurisdiction or "").strip()

        for vend_ent, vlabels in vendor_rows:
            cet = _local_contract_event_type(vend_ent)
            for idis_ent in idis_rows:
                dlabel = _idis_donor_label(idis_ent)
                if not dlabel:
                    continue
                matched_vl: str | None = None
                mtype: str | None = None
                rtype: str | None = None
                rnote: str | None = None
                for vl in vlabels:
                    mt, rt, rn = _local_match_type(vl, dlabel, jkey, db)
                    if mt == MATCH_RELATED_ENTITY:
                        matched_vl = vl
                        mtype, rtype, rnote = mt, rt, rn
                        break
                if not matched_vl or mtype != MATCH_RELATED_ENTITY:
                    continue
                if cet is None:
                    LOCAL_RELATED_ENTITY_DONOR_DIAGNOSTICS[
                        "skipped_missing_contract_event_type"
                    ] += 1
                    continue
                if cet not in _LOCAL_RELATED_ENTITY_ELIGIBLE_EVENTS:
                    continue
                pair_key = (str(vend_ent.id), str(idis_ent.id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                vend_raw = _local_procurement_raw_dict(vend_ent)
                v_name_raw = str(vend_raw.get("vendor_name_raw") or "").strip()
                v_canon = str(vend_raw.get("vendor_canonical") or "").strip()
                d_raw = _idis_donor_raw_dict(idis_ent)
                d_name_raw = str(d_raw.get("contributor_name_raw") or dlabel).strip()
                d_canon = str(
                    d_raw.get("contributor_canonical")
                    or normalize_vendor_name(dlabel)
                    or canonicalize(dlabel)
                ).strip()
                c_amt = vend_ent.amount
                d_amt = idis_ent.amount
                indirect = _local_indirect_match_label(rtype)
                score = _local_related_entity_score(c_amt, d_amt)
                agg: float | None = None
                if c_amt is not None or d_amt is not None:
                    agg = float(c_amt or 0) + float(d_amt or 0)
                amt_s = (
                    f"${float(c_amt):,.2f}"
                    if c_amt is not None
                    else "amount n/a"
                )
                dt_s = (
                    vend_ent.date_of_event.isoformat()
                    if vend_ent.date_of_event
                    else "date n/a"
                )
                donor_entity = (
                    f"Related entity donor — {d_name_raw or dlabel} ({indirect}) linked to "
                    f"vendor {v_name_raw or matched_vl}; {cet} {amt_s} on {dt_s}. "
                    f"Documented related-entity tie — not a direct same-entity donation."
                )
                alerts.append(
                    PatternAlert(
                        rule_id=RULE_LOCAL_RELATED_ENTITY_DONOR,
                        pattern_version=PATTERN_ENGINE_VERSION,
                        donor_entity=donor_entity,
                        matched_officials=[official],
                        matched_case_ids=[str(case_id)],
                        committee=jurisdiction,
                        window_days=None,
                        evidence_refs=sorted([str(vend_ent.id), str(idis_ent.id)]),
                        fired_at=fired_at,
                        suspicion_score=score,
                        aggregate_amount=agg,
                        payload_extra={
                            "vendor_label": matched_vl,
                            "donor_label": dlabel,
                            "match_type": mtype,
                            "relationship_type": rtype,
                            "relationship_source_note": (rnote or "") or "",
                            "vendor_canonical": v_canon or "",
                            "donor_canonical": d_canon or "",
                            "vendor_name_raw": v_name_raw or "",
                            "donor_name_raw": d_name_raw or "",
                            "contract_event_type": cet,
                            "contract_amount": float(c_amt or 0.0),
                            "donation_amount": float(d_amt or 0.0),
                            "indirect_match_label": indirect,
                        },
                    )
                )
    return alerts


def _detect_local_contract_donation_timing(
    db: Session, fired_at: datetime
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for case_id, (case, entries) in _load_local_case_evidence(db).items():
        jkey = local_jurisdiction_alias_key(case.jurisdiction or "")
        vendor_rows: list[tuple[EvidenceEntry, list[str]]] = []
        idis_rows: list[EvidenceEntry] = []
        for e in entries:
            if (e.adapter_name or "") in _LOCAL_PROCUREMENT_ADAPTERS:
                variants = _local_procurement_vendor_variants(e)
                if variants:
                    vendor_rows.append((e, variants))
            elif (e.adapter_name or "") == "IDIS":
                idis_rows.append(e)

        seen_pairs: set[tuple[str, str]] = set()
        official = (case.subject_name or "").strip() or "Unknown official"
        jurisdiction = (case.jurisdiction or "").strip()

        for vend_ent, vlabels in vendor_rows:
            cet = _local_contract_event_type(vend_ent)
            if cet not in _LOCAL_TIMING_ELIGIBLE_EVENTS:
                continue
            award = vend_ent.date_of_event
            if award is None:
                continue
            vend_raw = _local_procurement_raw_dict(vend_ent)
            v_name_raw = str(vend_raw.get("vendor_name_raw") or "").strip()
            v_canon = str(vend_raw.get("vendor_canonical") or "").strip()

            for idis_ent in idis_rows:
                dlabel = _idis_donor_label(idis_ent)
                if not dlabel:
                    continue
                don_date = idis_ent.date_of_event
                if don_date is None:
                    continue
                delta = (don_date - award).days
                if abs(delta) > LOCAL_TIMING_WINDOW_DAYS:
                    continue
                d_raw = _idis_donor_raw_dict(idis_ent)
                d_name_raw = str(d_raw.get("contributor_name_raw") or dlabel).strip()
                d_canon = str(
                    d_raw.get("contributor_canonical")
                    or normalize_vendor_name(dlabel)
                    or canonicalize(dlabel)
                ).strip()
                for vl in vlabels:
                    ok, mtype, rtype, rnote = local_match_eligible_for_loop_and_timing(
                        vl, dlabel, jkey, db
                    )
                    if not ok:
                        continue
                    pair_key = (str(vend_ent.id), str(idis_ent.id))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    direction = "pre_award" if don_date < award else "post_award"
                    c_amt = vend_ent.amount
                    d_amt = idis_ent.amount
                    base = _local_loop_score(c_amt, d_amt)
                    score = min(1.0, base + 0.22)
                    agg: float | None = None
                    if c_amt is not None or d_amt is not None:
                        agg = float(c_amt or 0) + float(d_amt or 0)
                    alerts.append(
                        PatternAlert(
                            rule_id=RULE_LOCAL_CONTRACT_DONATION_TIMING,
                            pattern_version=PATTERN_ENGINE_VERSION,
                            donor_entity=(
                                f"Local contract/donation timing: {dlabel} / {vl} ({direction})"
                            ),
                            matched_officials=[official],
                            matched_case_ids=[str(case_id)],
                            committee=jurisdiction,
                            window_days=LOCAL_TIMING_WINDOW_DAYS,
                            evidence_refs=sorted([str(vend_ent.id), str(idis_ent.id)]),
                            fired_at=fired_at,
                            suspicion_score=score,
                            aggregate_amount=agg,
                            payload_extra={
                                "vendor_label": vl,
                                "donor_label": dlabel,
                                "match_type": mtype,
                                "relationship_type": rtype,
                                "relationship_source_note": rnote,
                                "vendor_canonical": v_canon or None,
                                "donor_canonical": d_canon or None,
                                "contract_event_type": cet,
                                "vendor_name_raw": v_name_raw or None,
                                "donor_name_raw": d_name_raw or None,
                                "contract_amount": c_amt,
                                "donation_amount": d_amt,
                                "award_date": award.isoformat(),
                                "donation_date": don_date.isoformat(),
                                "timing_direction": direction,
                                "days_donation_minus_award": delta,
                                "contract_award_date": award.isoformat(),
                            },
                        )
                    )
                    break
    return alerts


def _detect_local_vendor_concentration(
    db: Session, fired_at: datetime
) -> list[PatternAlert]:
    alerts: list[PatternAlert] = []
    for case_id, (case, entries) in _load_local_case_evidence(db).items():
        jkey = local_jurisdiction_alias_key(case.jurisdiction or "")
        vendor_totals: dict[str, tuple[float, str]] = {}
        donor_totals: dict[str, tuple[float, str]] = {}
        procurement_award_total = 0.0
        procurement_other_event_total = 0.0

        for e in entries:
            if (e.adapter_name or "") in _LOCAL_PROCUREMENT_ADAPTERS:
                variants = _local_procurement_vendor_variants(e)
                if not variants:
                    continue
                raw = _local_procurement_raw_dict(e)
                label = str(raw.get("vendor_canonical") or "").strip() or variants[0]
                key = resolve(label, db).canonical_id
                amt = float(e.amount or 0)
                prev = vendor_totals.get(key)
                if prev is None:
                    vendor_totals[key] = (amt, label)
                else:
                    vendor_totals[key] = (prev[0] + amt, prev[1])
                cet = _local_contract_event_type(e)
                if cet == "award":
                    procurement_award_total += amt
                else:
                    procurement_other_event_total += amt
            elif (e.adapter_name or "") == "IDIS":
                dl = _idis_donor_label(e)
                if not dl:
                    continue
                key = resolve(dl, db).canonical_id
                amt = float(e.amount or 0)
                prev = donor_totals.get(key)
                if prev is None:
                    donor_totals[key] = (amt, dl)
                else:
                    donor_totals[key] = (prev[0] + amt, prev[1])

        if len(vendor_totals) < 1 or len(donor_totals) < 1:
            continue

        top_v = sorted(vendor_totals.items(), key=lambda x: -x[1][0])[
            :LOCAL_CONCENTRATION_TOP_N
        ]
        top_d = sorted(donor_totals.items(), key=lambda x: -x[1][0])[
            :LOCAL_CONCENTRATION_TOP_N
        ]
        vkeys = {k for k, _ in top_v}
        dkeys = {k for k, _ in top_d}
        overlap_keys = vkeys & dkeys
        if len(overlap_keys) < LOCAL_CONCENTRATION_MIN_OVERLAP:
            continue

        overlap_n = len(overlap_keys)
        score = max(0.35, min(1.0, 0.30 + 0.14 * float(overlap_n)))

        evidence_ids: set[str] = set()
        overlap_entities: list[dict[str, Any]] = []
        for ok in sorted(overlap_keys):
            vtot, vlabel = vendor_totals[ok]
            dtot, dlabel = donor_totals[ok]
            mt, rt, rn = _local_match_type(vlabel, dlabel, jkey, db)
            overlap_entities.append(
                {
                    "entity_key": ok,
                    "match_type": mt,
                    "relationship_type": rt,
                    "relationship_source_note": rn,
                    "vendor_label": vlabel,
                    "vendor_total": vtot,
                    "donor_label": dlabel,
                    "donor_total": dtot,
                }
            )

        for e in entries:
            if (e.adapter_name or "") in _LOCAL_PROCUREMENT_ADAPTERS:
                variants = _local_procurement_vendor_variants(e)
                if not variants:
                    continue
                raw = _local_procurement_raw_dict(e)
                lab = str(raw.get("vendor_canonical") or "").strip() or variants[0]
                if resolve(lab, db).canonical_id in overlap_keys:
                    evidence_ids.add(str(e.id))
            elif (e.adapter_name or "") == "IDIS":
                dl = _idis_donor_label(e)
                if dl and resolve(dl, db).canonical_id in overlap_keys:
                    evidence_ids.add(str(e.id))

        official = (case.subject_name or "").strip() or "Unknown official"
        jurisdiction = (case.jurisdiction or "").strip()

        alerts.append(
            PatternAlert(
                rule_id=RULE_LOCAL_VENDOR_CONCENTRATION,
                pattern_version=PATTERN_ENGINE_VERSION,
                donor_entity=(
                    f"Local top-vendor / top-donor overlap ({overlap_n} entities)"
                ),
                matched_officials=[official],
                matched_case_ids=[str(case_id)],
                committee=jurisdiction,
                window_days=None,
                evidence_refs=sorted(evidence_ids),
                fired_at=fired_at,
                suspicion_score=score,
                cluster_size=overlap_n,
                payload_extra={
                    "overlap_count": overlap_n,
                    "overlapping_entities": overlap_entities,
                    "procurement_total_award_amount": procurement_award_total,
                    "procurement_total_other_event_types_amount": procurement_other_event_total,
                    "top_vendors": [
                        {"entity_key": k, "label": vendor_totals[k][1], "total": vendor_totals[k][0]}
                        for k, _ in top_v
                    ],
                    "top_donors": [
                        {"entity_key": k, "label": donor_totals[k][1], "total": donor_totals[k][0]}
                        for k, _ in top_d
                    ],
                },
            )
        )
    return alerts


_LEGISLATIVE_RELATED_ENTITY_ROW_TYPES = frozenset(
    {"affiliate", "pac_of_donor", "subsidiary", "parent", "trade_name"}
)


def _detect_legislative_related_entity_donor(
    db: Session, fired_at: datetime
) -> list[PatternAlert]:
    """Federal legislative only: curated related-entity PAC/affiliate vs known donor canonical."""
    alerts: list[PatternAlert] = []
    prof_by = _subject_bioguides_by_case(db)
    fed_related_rows = [
        r
        for r in _load_curated_aliases("federal")
        if str(r.get("relationship_type") or "").strip().lower() in _LEGISLATIVE_RELATED_ENTITY_ROW_TYPES
    ]
    if not fed_related_rows:
        return alerts

    lda_by_case: dict[uuid.UUID, list[EvidenceEntry]] = {}
    for ent in db.scalars(
        select(EvidenceEntry).where(EvidenceEntry.entry_type == "lobbying_filing")
    ).all():
        lda_by_case.setdefault(ent.case_file_id, []).append(ent)

    seen: set[tuple[str, str, str, str]] = set()

    for case in db.scalars(select(CaseFile)).all():
        gl = (case.government_level or "federal").strip().lower()
        br = (case.branch or "legislative").strip().lower()
        if gl != "federal" or br != "legislative":
            continue
        cid = case.id
        official = (case.subject_name or "").strip() or "Unknown official"

        for ent in _fec_financial_evidence_rows(db, cid):
            try:
                raw = json.loads(ent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            contrib = _fec_contributor_display(raw)
            if not contrib:
                continue
            don_date = ent.date_of_event
            if don_date is None:
                continue
            try:
                amt = float(ent.amount or raw.get("contribution_receipt_amount") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0

            for row in fed_related_rows:
                canon = str(row.get("canonical_key") or "").strip()
                if not canon:
                    continue
                rt_row = str(row.get("relationship_type") or "").strip().lower()
                mt, rt, note = _local_match_type(canon, contrib, "federal", db)
                if mt != MATCH_RELATED_ENTITY or rt != rt_row:
                    continue
                vid, vday, dd = _nearest_vote_within_days_for_case(
                    db, cid, don_date, prof_by, LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS
                )
                if not vid or vday is None or dd is None:
                    continue
                key = (str(cid), str(ent.id), "fec", canon)
                if key in seen:
                    continue
                seen.add(key)
                label = _federal_indirect_match_label(rt)
                score = _legislative_related_entity_amount_score(amt)
                note_s = str(note or row.get("source_note") or "").strip()
                alerts.append(
                    PatternAlert(
                        rule_id=RULE_LEGISLATIVE_RELATED_ENTITY_DONOR,
                        pattern_version=PATTERN_ENGINE_VERSION,
                        donor_entity=(
                            f"Legislative related-entity donor — {contrib} ({label}) vs {canon}"
                        ),
                        matched_officials=[official],
                        matched_case_ids=[str(cid)],
                        committee=str(row.get("alias") or "")[:512],
                        window_days=LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS,
                        evidence_refs=sorted({str(ent.id), vid}),
                        fired_at=fired_at,
                        aggregate_amount=float(amt),
                        suspicion_score=score,
                        nearest_vote_id=vid,
                        nearest_vote_date=vday,
                        vote_context_available=True,
                        payload_extra={
                            "match_type": "related_entity",
                            "relationship_type": rt,
                            "relationship_source_note": note_s,
                            "donor_canonical": canon,
                            "related_entity_canonical": str(row.get("alias") or ""),
                            "donor_name_raw": canon,
                            "related_entity_name_raw": contrib,
                            "contribution_amount": float(amt),
                            "contribution_date": don_date.isoformat(),
                            "adjacent_vote_id": vid,
                            "adjacent_vote_date": vday,
                            "days_contribution_to_vote": int(dd),
                            "indirect_match_label": label,
                            "epistemic_basis": "fec_schedule_a_contribution",
                            "evidence_refs": sorted({str(ent.id), vid}),
                            "score_components": {
                                "base": 0.35,
                                "amount_log_scaled": float(score),
                            },
                        },
                    )
                )
                break

        for lent in lda_by_case.get(cid, []):
            try:
                lraw = json.loads(lent.raw_data_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(lraw, dict):
                continue
            reg = str(lraw.get("registrant_name") or "").strip()
            if not reg:
                continue
            lda_day = lent.date_of_event
            if lda_day is None:
                fy = lraw.get("filing_year")
                try:
                    y = int(fy) if fy is not None else None
                except (TypeError, ValueError):
                    y = None
                if y is None:
                    continue
                lda_day = date(y, 6, 15)

            for row in fed_related_rows:
                canon = str(row.get("canonical_key") or "").strip()
                if not canon:
                    continue
                rt_row = str(row.get("relationship_type") or "").strip().lower()
                mt, rt, note = _local_match_type(canon, reg, "federal", db)
                if mt != MATCH_RELATED_ENTITY or rt != rt_row:
                    continue
                vid, vday, dd = _nearest_vote_within_days_for_case(
                    db, cid, lda_day, prof_by, LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS
                )
                if not vid or vday is None or dd is None:
                    continue
                key = (str(cid), str(lent.id), "lda", canon)
                if key in seen:
                    continue
                seen.add(key)
                label = _federal_indirect_match_label(rt)
                score = _legislative_related_entity_amount_score(0.0)
                note_s = str(note or row.get("source_note") or "").strip()
                alerts.append(
                    PatternAlert(
                        rule_id=RULE_LEGISLATIVE_RELATED_ENTITY_DONOR,
                        pattern_version=PATTERN_ENGINE_VERSION,
                        donor_entity=(
                            f"Legislative related-entity LDA — {reg} ({label}) vs {canon}"
                        ),
                        matched_officials=[official],
                        matched_case_ids=[str(cid)],
                        committee=str(row.get("alias") or "")[:512],
                        window_days=LEGISLATIVE_RELATED_ENTITY_WINDOW_DAYS,
                        evidence_refs=sorted({str(lent.id), vid}),
                        fired_at=fired_at,
                        aggregate_amount=0.0,
                        suspicion_score=score,
                        nearest_vote_id=vid,
                        nearest_vote_date=vday,
                        vote_context_available=True,
                        payload_extra={
                            "match_type": "related_entity",
                            "relationship_type": rt,
                            "relationship_source_note": note_s,
                            "donor_canonical": canon,
                            "related_entity_canonical": str(row.get("alias") or ""),
                            "donor_name_raw": canon,
                            "related_entity_name_raw": reg,
                            "contribution_amount": 0.0,
                            "contribution_date": lda_day.isoformat(),
                            "adjacent_vote_id": vid,
                            "adjacent_vote_date": vday,
                            "days_contribution_to_vote": int(dd),
                            "indirect_match_label": label,
                            "epistemic_basis": "lda_lobbying_filing_registrant",
                            "evidence_refs": sorted({str(lent.id), vid}),
                            "score_components": {
                                "base": 0.35,
                                "amount_log_scaled": float(score),
                            },
                        },
                    )
                )
                break

    return alerts


def run_pattern_engine(db: Session) -> list[PatternAlert]:
    """
    Run all pattern rules against the current fingerprint table.
    Returns a list of PatternAlert objects. Never mutates case state.
    """
    fired_at = _utc_now()
    cases_with_votes = _case_ids_with_current_congress_votes(db)
    appearances = _load_appearances(db)
    by_donor: dict[str, list[_Appearance]] = {}
    for a in appearances:
        by_donor.setdefault(a.donor_key, []).append(a)

    committees_map = _committees_by_bioguide(db)
    alerts: list[PatternAlert] = []
    alerts.extend(_detect_committee_sweep(by_donor, committees_map, fired_at))
    alerts.extend(_detect_fingerprint_bloom(by_donor, fired_at))
    alerts.extend(_detect_soft_bundles(db, fired_at, cases_with_votes))
    alerts.extend(_detect_soft_bundle_v2(db, fired_at, cases_with_votes))
    alerts.extend(_detect_sector_convergence(db, fired_at))
    alerts.extend(_detect_geo_mismatch(db, fired_at))
    alerts.extend(_detect_disbursement_loop(db, fired_at))
    alerts.extend(_detect_joint_fundraising(db, fired_at))
    alerts.extend(_detect_baseline_anomaly(db, fired_at, cases_with_votes))
    alerts.extend(_detect_alignment_anomaly(db, fired_at, cases_with_votes))
    alerts.extend(_detect_amendment_tell(db, fired_at, cases_with_votes))
    alerts.extend(_detect_hearing_testimony(db, fired_at))
    alerts.extend(_detect_revolving_door(db, fired_at))
    alerts.extend(_detect_legislative_related_entity_donor(db, fired_at))
    alerts.extend(_detect_local_contractor_donor_loop(db, fired_at))
    alerts.extend(_detect_local_related_entity_donor(db, fired_at))
    alerts.extend(_detect_local_contract_donation_timing(db, fired_at))
    alerts.extend(_detect_local_vendor_concentration(db, fired_at))
    for a in alerts:
        if a.vote_context_available is None:
            a.vote_context_available = _all_cases_have_vote_context(
                a.matched_case_ids, cases_with_votes
            )
    alerts.sort(key=lambda x: (x.donor_entity.lower(), x.rule_id, x.committee or ""))
    from engines.pattern_alert_epistemic import enrich_pattern_alerts_epistemic_metadata

    enrich_pattern_alerts_epistemic_metadata(db, alerts)
    from services.proportionality import attach_proportionality_to_pattern_alerts

    attach_proportionality_to_pattern_alerts(db, alerts)
    return alerts


def pattern_alert_to_payload(a: PatternAlert) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
        "calendar_explained": a.deadline_adjacent,
        "calendar_event_type": a.calendar_event_type,
        "calendar_event_name": a.calendar_event_name,
        "calendar_discount_factor": a.deadline_discount,
        "vote_context_available": a.vote_context_available,
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
        "individual_donor_count": a.individual_donor_count,
        "org_donor_count": a.org_donor_count,
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
        "lda_match_count": a.lda_match_count,
        "diagnostics": json.loads(a.diagnostics_json)
        if a.diagnostics_json
        else None,
    }
    if a.payload_extra:
        payload["payload_extra"] = a.payload_extra
    if a.proportionality_context:
        payload["proportionality_context"] = list(a.proportionality_context)
    payload["epistemic_level"] = getattr(a, "epistemic_level", "REPORTED")
    payload["requires_human_review"] = bool(getattr(a, "requires_human_review", False))
    return payload


def pattern_alerts_for_signing(alerts: list[PatternAlert]) -> list[dict[str, Any]]:
    return [pattern_alert_to_payload(a) for a in alerts]


def sync_pattern_alert_records(db: Session, alerts: list[PatternAlert]) -> None:
    """Replace persisted pattern alerts with the latest engine output (global snapshot)."""
    db.execute(delete(PatternAlertRecord))
    now = _utc_now()
    for a in alerts:
        diag_obj: dict[str, Any] | None = None
        if a.diagnostics_json:
            try:
                parsed = json.loads(a.diagnostics_json)
                diag_obj = parsed if isinstance(parsed, dict) else {"diagnostics": parsed}
            except json.JSONDecodeError:
                diag_obj = {}
        if a.payload_extra:
            if diag_obj is None:
                diag_obj = {}
            diag_obj = {**diag_obj, "payload_extra": a.payload_extra}
        if a.proportionality_context:
            if diag_obj is None:
                diag_obj = {}
            diag_obj["proportionality_context"] = a.proportionality_context
        if diag_obj is not None:
            diag_store = json.dumps(diag_obj, separators=(",", ":"), default=str)
        else:
            diag_store = a.diagnostics_json
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
                diagnostics_json=diag_store,
                epistemic_level=getattr(a, "epistemic_level", "REPORTED"),
                requires_human_review=bool(getattr(a, "requires_human_review", False)),
            )
        )


def pattern_alerts_for_case(
    case_id: uuid.UUID,
    alerts: list[PatternAlert],
    *,
    include_unreviewed: bool = False,
) -> list[dict[str, Any]]:
    """HTML report rows: only alerts that reference this case."""
    sid = str(case_id)
    rows = [
        pattern_alert_to_report_dict(a)
        for a in alerts
        if sid in a.matched_case_ids
    ]
    if not include_unreviewed:
        rows = [r for r in rows if not r.get("requires_human_review")]
    return rows


def _pattern_alert_report_score(a: PatternAlert) -> float | None:
    """Unified 0..1 score for API report rows; None if no usable signal."""
    for attr in ("proximity_to_vote_score", "suspicion_score"):
        raw = getattr(a, attr, None)
        if raw is None:
            continue
        try:
            f = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isnan(f):
            continue
        return max(0.0, min(1.0, f))
    return None


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
    elif a.rule_id == RULE_FINGERPRINT_BLOOM:
        badge = "Cross-Case Donor"
        rule_line = (
            f"Fingerprint bloom — appeared in {FINGERPRINT_BLOOM_MIN_CASES}+ investigations "
            f"with relevance ≥ {FINGERPRINT_BLOOM_MIN_RELEVANCE}"
        )
    elif a.rule_id == RULE_LOCAL_CONTRACTOR_DONOR_LOOP:
        pe = a.payload_extra or {}
        badge = "Local contractor / donor"
        rule_line = (
            f"Direct or curated-alias overlap: vendor {pe.get('vendor_name_raw') or pe.get('vendor_label') or ''} "
            f"and donor {pe.get('donor_name_raw') or pe.get('donor_label') or ''}"
        )
    elif a.rule_id == RULE_LOCAL_CONTRACT_DONATION_TIMING:
        pe = a.payload_extra or {}
        badge = "Local contract / donation timing"
        rule_line = (
            f"Donation near award window: {pe.get('donor_name_raw') or pe.get('donor_label') or ''} "
            f"vs vendor {pe.get('vendor_name_raw') or pe.get('vendor_label') or ''} "
            f"({pe.get('timing_direction') or 'timing'})"
        )
    elif a.rule_id == RULE_LOCAL_VENDOR_CONCENTRATION:
        pe = a.payload_extra or {}
        n = int(pe.get("overlap_count") or 0)
        badge = "Local vendor concentration"
        rule_line = (
            f"Top vendor and top donor totals overlap on {n} entities "
            f"(procurement vs IDIS)"
        )
    elif a.rule_id == RULE_LOCAL_RELATED_ENTITY_DONOR:
        pe = a.payload_extra or {}
        d = pe.get("donor_name_raw") or pe.get("donor_label") or ""
        v = pe.get("vendor_name_raw") or pe.get("vendor_label") or ""
        rel = pe.get("indirect_match_label") or "documented related entity"
        badge = "Related entity donor"
        rule_line = (
            f"Related entity donor — {d} ({rel}) linked to vendor {v}; "
            f"not a direct same-entity donation."
        )
    elif a.rule_id == RULE_LEGISLATIVE_RELATED_ENTITY_DONOR:
        pe = a.payload_extra or {}
        rel = pe.get("indirect_match_label") or "related entity"
        rawn = pe.get("related_entity_name_raw") or ""
        canon = pe.get("donor_canonical") or ""
        badge = "Legislative related entity"
        rule_line = (
            f"Related-entity contribution or LDA filer — {rawn} ({rel}) "
            f"documented vs donor-of-record {canon}"
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
    el = getattr(a, "epistemic_level", "REPORTED")
    ep_note = ""
    if el == "VERIFIED":
        ep_note = " Epistemic basis: verified official records."
    elif el == "ALLEGED":
        ep_note = " Epistemic basis: alleged / filed claims (not adjudicated)."
    elif el == "CONTEXTUAL":
        ep_note = " Epistemic basis: contextual / unverified discourse."
    row: dict[str, Any] = {
        "badge": badge,
        "rule_line": rule_line + ep_note,
        "donor_entity": a.donor_entity,
        "matched_officials": a.matched_officials,
        "committee": a.committee or "",
        "window_days": a.window_days,
        "window_phrase": window_phrase,
        "disclaimer": a.disclaimer,
        "rule_id": a.rule_id,
        "epistemic_level": el,
        "requires_human_review": bool(getattr(a, "requires_human_review", False)),
        "score": _pattern_alert_report_score(a),
    }
    if a.proportionality_context:
        row["proportionality_context"] = list(a.proportionality_context)
    return row


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
