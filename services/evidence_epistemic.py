"""Attach epistemic_level, review flags, and full finding policy to evidence rows."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from services.epistemic_classifier import classify_epistemic_level
from services.finding_policy import apply_finding_policy_to_entry


def apply_epistemic_metadata_to_entry(
    entry: Any,
    *,
    case_subject_type: str | None,
    case: Any | None = None,
    db: Any | None = None,
) -> None:
    level = classify_epistemic_level(
        source_url=getattr(entry, "source_url", "") or "",
        source_name=getattr(entry, "source_name", "") or "",
        body=getattr(entry, "body", "") or "",
        title=getattr(entry, "title", "") or "",
    )
    entry.epistemic_level = level
    case_obj = case
    if case_obj is None:
        case_obj = SimpleNamespace(subject_type=case_subject_type, jurisdiction="")
    apply_finding_policy_to_entry(entry, case_obj, db=db)
