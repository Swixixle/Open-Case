"""Epistemic finding policy: classification, display labels, render framing, public eligibility."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from services.epistemic_classifier import (
    ALLEGED,
    CONTEXTUAL,
    DISPUTED,
    LEVEL_RANK,
    REPORTED,
    VERIFIED,
)

SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "court_document",
        "news",
        "agency_record",
        "disciplinary",
        "campaign_disclosure",
        "forum",
        "social",
        "archived_page",
        "complaint",
        "bar_evaluation",
        "watchdog_report",
        "financial_disclosure",
        "other",
    }
)

CLAIM_STATUSES: frozenset[str] = frozenset(
    {"active", "superseded", "dismissed", "adjudicated"}
)
REVIEW_STATUSES: frozenset[str] = frozenset(
    {"pending", "approved", "rejected", "needs_correction"}
)

CLASSIFICATION_RULES: dict[str, tuple[str, ...]] = {
    VERIFIED: (
        "adjudicated_record",
        "disciplinary_order",
        "final_judgment",
        "financial_disclosure_official",
        "government_report_final",
        "court_order",
        "regulatory_settlement",
        "conviction",
    ),
    REPORTED: (
        "named_source_journalism",
        "official_statement",
        "press_release",
    ),
    ALLEGED: (
        "court_filing",
        "complaint",
        "affidavit",
        "sworn_declaration",
        "lawsuit",
        "public_grievance",
        "motion_to_recuse",
    ),
    DISPUTED: (
        "rebuttal_on_record",
        "dismissal_with_finding",
        "contrary_adjudication",
    ),
    CONTEXTUAL: (
        "forum_post",
        "social_media",
        "anonymous_source",
        "rumor_cluster",
        "weakly_sourced_commentary",
    ),
}

_BASIS_TO_LEVEL: dict[str, str] = {}
for _lvl, _bases in CLASSIFICATION_RULES.items():
    for _b in _bases:
        _BASIS_TO_LEVEL[_b] = _lvl

_CORRUPTION_CRIME = re.compile(
    r"\b(brib|corrupt|kickback|embezzl|fraud|felony|indict|convict|criminal)\w*\b",
    re.I,
)
_SEXUAL_RACISM = re.compile(
    r"\b(sexual|harass|rape|assault|racis|bigot|slur)\w*\b",
    re.I,
)


def valid_http_url(url: str | None) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    try:
        p = urlparse(u)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def infer_source_type(
    *,
    source_url: str,
    source_name: str,
    entry_type: str,
    adapter_name: str | None,
) -> str:
    u = (source_url or "").lower()
    sn = (source_name or "").lower()
    ad = (adapter_name or "").lower()
    et = (entry_type or "").lower()
    if "fec.gov" in u or ad == "fec" or et == "fec_disbursement":
        return "campaign_disclosure"
    if "courtlistener.com" in u or "pacer" in u or et in ("court_record", "court_docket_reference", "court_opinion_summary"):
        return "court_document"
    if "fjc.gov" in u:
        return "agency_record"
    if "sec.gov" in u or "edgar" in u:
        return "agency_record"
    if "congress.gov" in u or "senate.gov" in u:
        return "agency_record"
    if "web.archive.org" in u or "archive.org" in u:
        return "archived_page"
    if any(x in u for x in ("twitter.com", "x.com", "facebook.com", "instagram.com")):
        return "social"
    if "reddit.com" in u or "forum" in u:
        return "forum"
    if any(x in sn for x in ("tribune", "times", "post", "news", "herald", "ap news", "reuters")):
        return "news"
    if "disclosure" in et or "financial_disclosure" in et:
        return "financial_disclosure"
    return "other"


def infer_classification_basis(
    *,
    source_type: str,
    entry_type: str,
    title: str,
    body: str,
) -> str:
    blob = f"{title}\n{body}".lower()
    et = (entry_type or "").lower()
    if et in ("court_opinion_summary", "judicial_index") and "opinion" in blob:
        return "court_order"
    if et == "financial_disclosure_index" or source_type == "financial_disclosure":
        return "financial_disclosure_official"
    if "recuse" in blob or "recusal" in blob:
        return "motion_to_recuse"
    if "complaint" in blob or et == "gap_documented":
        return "complaint"
    if source_type == "news":
        return "named_source_journalism"
    if source_type == "forum":
        return "forum_post"
    if source_type == "social":
        return "social_media"
    if source_type == "court_document":
        return "court_filing"
    if source_type == "campaign_disclosure":
        return "financial_disclosure_official"
    return "official_statement" if source_type == "agency_record" else "weakly_sourced_commentary"


def epistemic_level_for_basis(basis: str) -> str:
    return _BASIS_TO_LEVEL.get(basis, REPORTED)


def merge_epistemic_levels(*levels: str) -> str:
    """Pick the most conservative (weakest) level — do not over-claim."""
    clean = [(L or "").strip().upper() for L in levels if (L or "").strip()]
    clean = [L for L in clean if L in LEVEL_RANK]
    if not clean:
        return REPORTED
    return min(clean, key=lambda L: LEVEL_RANK[L])


def compute_display_label(epistemic_level: str) -> str:
    e = (epistemic_level or "").strip().upper()
    if e == VERIFIED:
        return "VERIFIED RECORD"
    if e == REPORTED:
        return "REPORTED"
    if e == ALLEGED:
        return "ALLEGED — not adjudicated"
    if e == DISPUTED:
        return "DISPUTED"
    if e == CONTEXTUAL:
        return "CONTEXTUAL — unverified public record"
    return "REPORTED"


def build_rendered_claim_text(
    *,
    epistemic_level: str,
    claim_text: str,
    source_publisher: str,
    source_type: str,
    document_type_label: str | None = None,
    dispute_publisher: str | None = None,
) -> str:
    """Frame claims as statements about the source, not bare facts (non-VERIFIED)."""
    claim = (claim_text or "").strip()
    pub = (source_publisher or "the source").strip() or "the source"
    doc = (document_type_label or source_type.replace("_", " ") or "record").strip()
    e = (epistemic_level or "").strip().upper()
    if not claim:
        return ""
    if e == VERIFIED:
        return claim
    if e == REPORTED:
        return f"{pub} reported that {claim}"
    if e == ALLEGED:
        return f"A {doc} alleged that {claim}"
    if e == DISPUTED:
        reb = (dispute_publisher or "another on-record source").strip()
        return f"{pub} alleged that {claim} — {reb} disputes this finding"
    if e == CONTEXTUAL:
        return f"{pub} posted that {claim} — this claim is unverified"
    return f"{pub} indicated that {claim}"


def hash_source_excerpt(excerpt: str | None, body: str | None) -> str:
    raw = (excerpt or body or "").encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


def effective_receipt_id(entry: Any) -> str:
    r = (getattr(entry, "receipt_id", None) or "").strip()
    if r:
        return r
    sh = (getattr(entry, "signed_hash", None) or "").strip()
    return sh[:512] if sh else ""


def compute_is_publicly_renderable(entry: Any, *, admin_contextual_override: bool = False) -> bool:
    """Derived flag per product spec (stored on row for filtering)."""
    if not valid_http_url(getattr(entry, "source_url", None)):
        return False
    claim = (getattr(entry, "claim_text", None) or "").strip() or (
        getattr(entry, "body", None) or ""
    ).strip()
    excerpt = (getattr(entry, "source_excerpt", None) or "").strip()
    if not claim and not excerpt:
        return False
    dl = (getattr(entry, "display_label", None) or "").strip()
    if not dl:
        dl = compute_display_label(getattr(entry, "epistemic_level", None) or REPORTED)
    if not dl.strip():
        return False
    if not effective_receipt_id(entry):
        return False
    el = (getattr(entry, "epistemic_level", None) or "").strip().upper()
    if el == CONTEXTUAL and not admin_contextual_override:
        return False
    rs = (getattr(entry, "review_status", None) or "pending").strip().lower()
    if rs == "rejected":
        return False
    if rs not in ("pending", "approved"):
        return False
    return True


def apply_finding_policy_to_entry(
    entry: Any, case: Any | None, db: Any | None = None
) -> None:
    """Populate classification, display, hashes, receipt linkage; refresh review flags."""
    from services.human_review import evidence_requires_human_review_extended

    url = getattr(entry, "source_url", "") or ""
    st = infer_source_type(
        source_url=url,
        source_name=getattr(entry, "source_name", "") or "",
        entry_type=getattr(entry, "entry_type", "") or "",
        adapter_name=getattr(entry, "adapter_name", None),
    )
    entry.source_type = st
    if not (getattr(entry, "source_title", None) or "").strip():
        entry.source_title = (getattr(entry, "title", "") or "")[:1024]
    if not (getattr(entry, "source_publisher", None) or "").strip():
        entry.source_publisher = (getattr(entry, "source_name", "") or "")[:512]
    if getattr(entry, "source_date", None) is None and getattr(entry, "date_of_event", None):
        entry.source_date = entry.date_of_event
    if getattr(entry, "date_discovered", None) is None:
        entry.date_discovered = datetime.now(timezone.utc)
    if not (getattr(entry, "claim_text", None) or "").strip():
        entry.claim_text = (getattr(entry, "body", "") or "")[:200000]
    if not (getattr(entry, "claim_summary", None) or "").strip() and getattr(entry, "title", None):
        entry.claim_summary = (entry.title or "")[:2048]

    basis = infer_classification_basis(
        source_type=st,
        entry_type=getattr(entry, "entry_type", "") or "",
        title=getattr(entry, "title", "") or "",
        body=getattr(entry, "body", "") or "",
    )
    entry.classification_basis = basis[:64]
    domain_level = (getattr(entry, "epistemic_level", None) or REPORTED).strip().upper()
    if domain_level not in LEVEL_RANK:
        domain_level = REPORTED
    basis_level = epistemic_level_for_basis(basis)
    if int(getattr(entry, "contradiction_count", 0) or 0) > 0:
        entry.epistemic_level = DISPUTED
    else:
        entry.epistemic_level = merge_epistemic_levels(domain_level, basis_level)

    entry.display_label = compute_display_label(entry.epistemic_level)[:256]
    if case is not None:
        entry.jurisdiction = (getattr(case, "jurisdiction", None) or entry.jurisdiction or "")[:512]
    if db is not None:
        sid = subject_profile_id_for_case(entry.case_file_id, db)
        if sid:
            entry.subject_id = sid
    ex = (getattr(entry, "source_excerpt", None) or "").strip()
    entry.source_hash = hash_source_excerpt(ex or None, getattr(entry, "body", None))[:128]

    if getattr(entry, "linked_entities_json", None) in (None, "", "null"):
        entry.linked_entities_json = "[]"

    entry.requires_human_review = evidence_requires_human_review_extended(
        epistemic_level=entry.epistemic_level,
        subject_type=getattr(case, "subject_type", None) if case else None,
        title=getattr(entry, "title", "") or "",
        body=getattr(entry, "body", "") or "",
        source_type=st,
        confidence=getattr(entry, "confidence", None) or "",
    )

    entry.is_publicly_renderable = compute_is_publicly_renderable(entry)


def finalize_finding_after_sign(entry: Any, case: Any | None) -> None:
    """After cryptographic sign: receipt id + render eligibility."""
    sh = (getattr(entry, "signed_hash", None) or "").strip()
    if sh:
        entry.receipt_id = sh[:512]
    else:
        entry.receipt_id = f"finding:{entry.id}"
    entry.display_label = compute_display_label(entry.epistemic_level)[:256]
    entry.is_publicly_renderable = compute_is_publicly_renderable(entry)


def subject_profile_id_for_case(case_id: Any, db: Any) -> Any | None:
    from sqlalchemy import select

    from models import SubjectProfile

    return db.scalar(select(SubjectProfile.id).where(SubjectProfile.case_file_id == case_id))
