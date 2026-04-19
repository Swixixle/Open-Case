"""
Map EthicalAlt-style profile payloads into Open Case pattern-testing fixtures.

Parse → classify → extract → build entity → emit test-oriented dicts.
Conservative: ambiguous political-economic records are not coerced into donations.

Not for production ingestion — fixture / lab use only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# --- Event classification (Phase 1) -------------------------------------------

EVENT_DONATION = "donation"
EVENT_PAC_CONTRIBUTION = "pac_contribution"
EVENT_LOBBYING_EXPENDITURE = "lobbying_expenditure"
EVENT_POLITICAL_SPEND_OTHER = "political_spend_other"
EVENT_UNKNOWN_POLITICAL = "unknown_political"

# Keywords: lobbying must win over generic "contribution"
_LOBBYING_MARKERS = (
    "lobbying expenditure",
    "lobbying disclosure",
    "lobbying registr",
    "registered lobby",
    "lobbyist",
    "lobbying firm",
    "lobbying activity",
    "lobbying fee",
    "lobbying registrant",
    " lda ",
    " lda,",
    "influence legislation",
    "seeking to influence",
)
_PAC_MARKERS = (
    "political action committee",
    "pac contribution",
    "contribution to pac",
    "donate to pac",
    "donated to pac",
    "given to the pac",
)
_PAC_WORD = re.compile(r"\bpac\b", re.I)

_DONATION_MARKERS = (
    "campaign donation",
    "donated $",
    "donated to",
    "donation to",
    "donation of",
    "contribution to campaign",
    "campaign contribution",
    "contributed $",
    # Not bare "contributed to" — matches environmental "contributed to contamination" in EthicalAlt exports.
    "write check",
)
_DONATION_SINGLE = frozenset({"donation", "donated", "donate"})

_INDEPENDENT_OR_IE = (
    "independent expenditure",
    "super pac",
    "electioneering",
    "issue advocacy",
)


def classify_political_event_type(description: str) -> str:
    """
    Conservative keyword classifier. Does not call lobbying a donation.
    """
    if not description or not description.strip():
        return EVENT_UNKNOWN_POLITICAL
    d = f" {description.lower()} "

    for phrase in _LOBBYING_MARKERS:
        if phrase in d:
            return EVENT_LOBBYING_EXPENDITURE
    if "lobbying" in d or "lobbyist" in d:
        return EVENT_LOBBYING_EXPENDITURE

    for phrase in _INDEPENDENT_OR_IE:
        if phrase in d:
            return EVENT_POLITICAL_SPEND_OTHER

    has_pac = "political action committee" in d or _PAC_WORD.search(description)
    for phrase in _PAC_MARKERS:
        if phrase in d:
            return EVENT_PAC_CONTRIBUTION
    if has_pac and any(
        x in d for x in ("contribution", "contributed", "donate", "donated", "gave")
    ):
        return EVENT_PAC_CONTRIBUTION

    for phrase in _DONATION_MARKERS:
        if phrase in d:
            return EVENT_DONATION
    if any(w in d for w in _DONATION_SINGLE):
        if "lobby" in d:
            return EVENT_UNKNOWN_POLITICAL
        return EVENT_DONATION

    if "contribution" in d and "lobby" not in d and "pac" not in d:
        return EVENT_UNKNOWN_POLITICAL

    return EVENT_UNKNOWN_POLITICAL


# --- Recipient extraction (Phase 2) -------------------------------------------

RECIPIENT_TYPE_PAC = "pac"
RECIPIENT_TYPE_COMMITTEE = "committee"
RECIPIENT_TYPE_OFFICIAL = "official"
RECIPIENT_TYPE_ORGANIZATION = "organization"
RECIPIENT_TYPE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class RecipientExtraction:
    recipient_name: str | None
    recipient_type: str
    recipient_raw_text: str | None


_COMMITTEE_PREFIX = re.compile(
    r"(?:Friends of|Committee to (?:Re-)?elect|Campaign of)\s+([A-Z][A-Za-z0-9\s,\.&'\-]{2,100})",
    re.I,
)
_OFFICIAL = re.compile(
    r"\b(?:Sen\.|Senator|Rep\.|Representative)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
_TO_FOR_VIA = re.compile(
    r"\b(?:to|for|via)\s+(?:the\s+)?([A-Z][A-Za-z0-9\s,\.&'\-]{2,120}?)(?=\s+(?:in|on|for|during|to|\.|,|;|$))",
    re.I,
)
_PAC_NAME = re.compile(
    r"\b(?:PAC|committee)\s+[\"']([^\"']+)[\"']",
    re.I,
)
_ORG_SUFFIX = re.compile(
    r"\b([A-Z][A-Za-z0-9\s,\.&'\-]{2,80}?(?:\s+LLC|Inc\.|Corp\.|Association|Foundation|PAC))\b"
)


def extract_recipient(description: str) -> RecipientExtraction:
    """Regex-only; does not fabricate names."""
    raw = (description or "").strip()
    if not raw:
        return RecipientExtraction(None, RECIPIENT_TYPE_UNKNOWN, None)

    if m := _COMMITTEE_PREFIX.search(raw):
        name = _clean_name(m.group(1))
        if name:
            return RecipientExtraction(
                name, RECIPIENT_TYPE_COMMITTEE, m.group(0).strip()
            )

    if m := _OFFICIAL.search(raw):
        return RecipientExtraction(
            m.group(1).strip(), RECIPIENT_TYPE_OFFICIAL, m.group(0).strip()
        )

    if m := _PAC_NAME.search(raw):
        name = _clean_name(m.group(1))
        if name:
            return RecipientExtraction(name, RECIPIENT_TYPE_PAC, m.group(0).strip())

    if m := _TO_FOR_VIA.search(raw):
        name = _clean_name(m.group(1))
        if name and len(name) >= 3:
            rtype = (
                RECIPIENT_TYPE_PAC
                if "pac" in name.lower()
                else RECIPIENT_TYPE_ORGANIZATION
            )
            return RecipientExtraction(name, rtype, m.group(0).strip())

    if m := _ORG_SUFFIX.search(raw):
        name = _clean_name(m.group(1))
        if name:
            rtype = (
                RECIPIENT_TYPE_PAC if name.upper().endswith("PAC") else RECIPIENT_TYPE_ORGANIZATION
            )
            return RecipientExtraction(name, rtype, m.group(0).strip())

    return RecipientExtraction(None, RECIPIENT_TYPE_UNKNOWN, None)


def _clean_name(s: str) -> str:
    s = s.strip().strip(",.;")
    return " ".join(s.split()) if s else ""


# --- Date normalization (Phase 3) ---------------------------------------------

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
)


def normalize_date(raw: str | None) -> tuple[str | None, str | None]:
    """
    Return (iso_date YYYY-MM-DD or None, preserved raw string or None).
    Invalid or empty input → (None, raw or None).

    EthicalAlt often emits month-only (``YYYY-MM``) or year-only (``YYYY``) strings.
    Those normalize to the first day of the month or year respectively (precision limit).
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    # Month precision (common in EthicalAlt category exports)
    if re.fullmatch(r"\d{4}-\d{2}", s):
        try:
            dt = datetime.strptime(s, "%Y-%m")
            return dt.date().isoformat(), s
        except ValueError:
            pass
    # Year precision only — anchor to Jan 1 (documented limitation)
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        if 1900 <= y <= 2100:
            return f"{y:04d}-01-01", s
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s[:19] if "T" in s and len(s) > 10 else s, fmt)
            return dt.date().isoformat(), s
        except ValueError:
            continue
    # Try date-only prefix
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s[:10]).isoformat(), s
        except ValueError:
            pass
    return None, s


# --- Amount parsing (Phase 4) -------------------------------------------------

_RANGE_OR_AMBIGUOUS = re.compile(
    r"(?:\b(?:between|from)\s+\$?"
    r"|\bup\s+to\s+\$?"
    r"|\$\s*[\d,.]+\s*[-–—]\s*\$?"
    r"|\$\s*[\d,.]+\s+to\s+\$?"
    r"|\band\s+\$)",
    re.I,
)
_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([kKmM])?", re.I)
_BARE_SUFFIX_RE = re.compile(r"\b([\d]+(?:\.\d+)?)\s*([kKmM])\b", re.I)


def _amount_from_groups(num: str, suf: str | None) -> float:
    base = float(num.replace(",", ""))
    mult = {"k": 1_000.0, "m": 1_000_000.0}.get((suf or "").lower(), 1.0)
    return base * mult


def parse_amount(text: str | None) -> float | None:
    """
    Extract a single monetary value when unambiguous.
    Returns None for ranges, multiple competing amounts, or junk.
    """
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    if _RANGE_OR_AMBIGUOUS.search(s):
        return None
    if re.search(r"\$\s*[\d,.]+\s*[-–—]\s*\$?\s*[\d,.]+", s):
        return None
    if s.lower().count(" and ") > 0 and s.count("$") > 1:
        return None

    values: list[float] = []
    for m in _DOLLAR_RE.finditer(s):
        try:
            values.append(_amount_from_groups(m.group(1), m.group(2)))
        except ValueError:
            continue
    if not values:
        for m in _BARE_SUFFIX_RE.finditer(s):
            try:
                values.append(_amount_from_groups(m.group(1), m.group(2)))
            except ValueError:
                continue

    if not values:
        return None
    if len(values) > 1:
        first, last = values[0], values[-1]
        if len(values) == 2 and abs(first - last) < 1e-6:
            return first
        return None
    return values[0]


# --- Severity (Phase 5) -------------------------------------------------------

CRITICAL_SEVERITY_TERMS = frozenset(
    {
        "indictment",
        "indicted",
        "conviction",
        "convicted",
        "bribery",
        "bribe",
        "criminal charge",
        "guilty plea",
        "wire fraud",
        "prison sentence",
    }
)
HIGH_SEVERITY_TERMS = frozenset(
    {
        "sec investigation",
        "sec enforcement",
        "doj",
        "department of justice",
        "subpoena",
        "felony",
        "misdemeanor charge",
        "fraud",
        "racketeering",
    }
)
HIGH_AMOUNT_THRESHOLD = 500_000.0
MEDIUM_SEVERITY_TERMS = frozenset(
    {
        "settlement",
        "fine",
        "penalty",
        "violation",
        "lawsuit",
        "complaint filed",
        "investigation",
        "enforcement",
    }
)


def classify_severity(description: str, amount: float | None = None) -> str:
    """
    Explicit keyword buckets + amount hint. Conservative defaults to medium/low.
    """
    d = (description or "").lower()
    if any(t in d for t in CRITICAL_SEVERITY_TERMS):
        return "critical"
    if any(t in d for t in HIGH_SEVERITY_TERMS):
        return "high"
    if amount is not None and amount >= HIGH_AMOUNT_THRESHOLD:
        if any(t in d for t in MEDIUM_SEVERITY_TERMS):
            return "high"
    if any(t in d for t in MEDIUM_SEVERITY_TERMS):
        return "medium"
    return "low"


# --- Models (Phase 6) ---------------------------------------------------------


@dataclass
class OpenCaseDonation:
    """Donation-like transfers only (not lobbying spend)."""

    amount: float | None
    normalized_date: str | None
    raw_date: str | None
    recipient_name: str | None
    recipient_type: str
    recipient_raw_text: str | None
    event_type: str
    description: str
    source_incident_id: str | None = None


@dataclass
class OpenCaseIncident:
    """All incidents from profile (including lobbying) for timelines / mixed tests."""

    description: str
    event_type: str
    severity: str
    normalized_date: str | None
    raw_date: str | None
    amount: float | None
    recipient_name: str | None
    recipient_type: str
    recipient_raw_text: str | None
    included_in_donation_fixtures: bool
    incident_id: str = ""


@dataclass
class EthicalAltEntity:
    """Fixture-oriented view of a profile."""

    profile_id: str
    name: str
    incidents: list[OpenCaseIncident] = field(default_factory=list)
    donations: list[OpenCaseDonation] = field(default_factory=list)


def _incident_from_raw(
    raw: dict[str, Any], idx: int
) -> OpenCaseIncident:
    desc = str(raw.get("description") or raw.get("text") or "")
    raw_date = raw.get("date") or raw.get("occurred_on") or raw.get("occurred")
    if raw_date is not None and not isinstance(raw_date, str):
        raw_date = str(raw_date)
    norm, _preserved = normalize_date(raw_date)
    event_type = classify_political_event_type(desc)
    amt = parse_amount(desc)
    sev = classify_severity(desc, amt)
    rec = extract_recipient(desc)
    donation_ok = event_type in (EVENT_DONATION, EVENT_PAC_CONTRIBUTION)
    if not norm and raw_date:
        # Required date missing for strict donation fixtures — still keep incident
        donation_ok = False
    iid = str(raw.get("id") or f"inc-{idx}")
    return OpenCaseIncident(
        description=desc,
        event_type=event_type,
        severity=sev,
        normalized_date=norm,
        raw_date=raw_date if isinstance(raw_date, str) else None,
        amount=amt,
        recipient_name=rec.recipient_name,
        recipient_type=rec.recipient_type,
        recipient_raw_text=rec.recipient_raw_text,
        included_in_donation_fixtures=donation_ok,
        incident_id=iid,
    )


def build_ethicalalt_entity(profile: dict[str, Any]) -> EthicalAltEntity:
    """Build entity from EthicalAlt-like dict (incidents list)."""
    pid = str(profile.get("profile_id") or profile.get("id") or "unknown")
    name = str(profile.get("name") or profile.get("entity_name") or "Unknown")
    raw_incidents: Iterable[dict[str, Any]] = profile.get("incidents") or []
    incidents: list[OpenCaseIncident] = []
    donations: list[OpenCaseDonation] = []
    for idx, r in enumerate(raw_incidents):
        inc = _incident_from_raw(r, idx)
        incidents.append(inc)
        if inc.included_in_donation_fixtures and inc.event_type in (
            EVENT_DONATION,
            EVENT_PAC_CONTRIBUTION,
        ):
            donations.append(
                OpenCaseDonation(
                    amount=inc.amount,
                    normalized_date=inc.normalized_date,
                    raw_date=inc.raw_date,
                    recipient_name=inc.recipient_name,
                    recipient_type=inc.recipient_type,
                    recipient_raw_text=inc.recipient_raw_text,
                    event_type=inc.event_type,
                    description=inc.description,
                    source_incident_id=inc.incident_id,
                )
            )
    return EthicalAltEntity(profile_id=pid, name=name, incidents=incidents, donations=donations)


def extract_donations_for_open_case(profile: dict[str, Any]) -> list[OpenCaseDonation]:
    """Strict donation / PAC contribution fixtures only."""
    return list(build_ethicalalt_entity(profile).donations)


# --- Generators (Phase 8) -----------------------------------------------------


def generate_soft_bundle_test_data(entity: EthicalAltEntity) -> dict[str, Any]:
    """Minimal dict for SOFT_BUNDLE-style lab fixtures."""
    rows: list[dict[str, Any]] = []
    for d in entity.donations:
        rows.append(
            {
                "donor_label": entity.name,
                "amount": d.amount,
                "date": d.normalized_date,
                "raw_date": d.raw_date,
                "recipient": d.recipient_name,
                "recipient_type": d.recipient_type,
                "event_type": d.event_type,
                "description": d.description,
            }
        )
    return {
        "profile_id": entity.profile_id,
        "donation_rows": rows,
        "count": len(rows),
    }


def generate_temporal_clustering_test_data(entity: EthicalAltEntity) -> dict[str, Any]:
    """Sorted incidents with normalized dates for temporal tests."""
    dated: list[tuple[str | None, OpenCaseIncident]] = [
        (i.normalized_date, i) for i in entity.incidents
    ]
    dated.sort(key=lambda x: (x[0] is None, x[0] or ""))
    timeline: list[dict[str, Any]] = []
    span_start: str | None = None
    span_end: str | None = None
    for nd, inc in dated:
        if nd:
            if span_start is None or nd < span_start:
                span_start = nd
            if span_end is None or nd > span_end:
                span_end = nd
        timeline.append(
            {
                "incident_id": inc.incident_id,
                "normalized_date": inc.normalized_date,
                "raw_date": inc.raw_date,
                "event_type": inc.event_type,
                "severity": inc.severity,
                "amount": inc.amount,
                "included_as_donation_fixture": inc.included_in_donation_fixtures,
            }
        )
    return {
        "profile_id": entity.profile_id,
        "timeline": timeline,
        "timeline_span": {"start": span_start, "end": span_end},
    }


def parse_ethicalalt_profile(profile: dict[str, Any]) -> EthicalAltEntity:
    """Alias for build_ethicalalt_entity."""
    return build_ethicalalt_entity(profile)
