"""Rules for auto-flagging findings that must not ship in public API by default."""

from __future__ import annotations

import re

from core.subject_taxonomy import subject_type_is_judicial
from services.epistemic_classifier import ALLEGED, CONTEXTUAL, REPORTED

_DEMOGRAPHIC = re.compile(
    r"\b(disparit|discriminat|bias|demographic|racial|gender|ethnic)\w*\b", re.I
)
_HARASSMENT = re.compile(
    r"\b(harass|misconduct|sexual\s+assault|abuse\s+of\s+office|bully)\w*\b", re.I
)
_CORRUPTION_CRIME = re.compile(
    r"\b(brib|corrupt|kickback|embezzl|fraud|felony|indict|convict|criminal)\w*\b",
    re.I,
)


def evidence_requires_human_review(
    *,
    epistemic_level: str,
    subject_type: str | None,
    title: str,
    body: str,
) -> bool:
    return evidence_requires_human_review_extended(
        epistemic_level=epistemic_level,
        subject_type=subject_type,
        title=title,
        body=body,
        source_type="",
        confidence="",
    )


def evidence_requires_human_review_extended(
    *,
    epistemic_level: str,
    subject_type: str | None,
    title: str,
    body: str,
    source_type: str,
    confidence: str,
) -> bool:
    blob = f"{title}\n{body}"
    st = (source_type or "").strip().lower()
    if epistemic_level == CONTEXTUAL:
        return True
    if st in ("forum", "social"):
        return True
    if epistemic_level == ALLEGED and subject_type_is_judicial(subject_type):
        return True
    if subject_type_is_judicial(subject_type) and epistemic_level == REPORTED:
        if _HARASSMENT.search(blob) or _DEMOGRAPHIC.search(blob):
            return True
    if _DEMOGRAPHIC.search(blob):
        return True
    if _HARASSMENT.search(blob):
        return True
    if _CORRUPTION_CRIME.search(blob):
        return True
    conf = (confidence or "").strip().lower()
    if conf == "unverified":
        return True
    return False


def pattern_alert_requires_human_review(
    *,
    epistemic_level: str,
    rule_id: str,
    rule_line: str,
    matched_case_subject_types: list[str | None],
) -> bool:
    blob = f"{rule_id}\n{rule_line}"
    if epistemic_level == CONTEXTUAL:
        return True
    if epistemic_level == ALLEGED and any(subject_type_is_judicial(t) for t in matched_case_subject_types):
        return True
    if _DEMOGRAPHIC.search(blob):
        return True
    if _HARASSMENT.search(blob):
        return True
    return False
