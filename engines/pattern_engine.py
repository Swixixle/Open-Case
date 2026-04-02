"""
Pattern Engine — cross-official donor pattern detection.

Rules are versioned and typed. PatternAlerts are read-side only.
They document what public records show across investigations.
They assert nothing about intent or causation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import DonorFingerprint, PatternAlertRecord, SenatorCommittee, Signal, SubjectProfile

PATTERN_ENGINE_VERSION = "1.0"

# Rule IDs — increment when logic changes, never reuse
RULE_COMMITTEE_SWEEP = "COMMITTEE_SWEEP_V1"
RULE_FINGERPRINT_BLOOM = "FINGERPRINT_BLOOM_V1"

COMMITTEE_SWEEP_MIN_OFFICIALS = 3
COMMITTEE_SWEEP_MAX_WINDOW_DAYS = 14
FINGERPRINT_BLOOM_MIN_CASES = 4
FINGERPRINT_BLOOM_MIN_RELEVANCE = 0.3

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


def _donation_date_for_signal(s: Signal) -> date | None:
    raw = s.event_date_a
    if raw:
        try:
            return date.fromisoformat(str(raw).strip()[:10])
        except ValueError:
            pass
    bd = _signal_breakdown_json(s)
    ex = bd.get("exemplar_financial_date")
    if ex:
        try:
            return date.fromisoformat(str(ex).strip()[:10])
        except ValueError:
            pass
    return None


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
        dk = (fp.normalized_donor_key or "").strip().lower()
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
