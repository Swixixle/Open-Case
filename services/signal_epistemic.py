"""Derive epistemic metadata on signals from linked evidence."""

from __future__ import annotations

import json
import uuid
from sqlalchemy.orm import Session

from models import EvidenceEntry, Signal
from services.epistemic_classifier import REPORTED, aggregate_epistemic_levels
from services.human_review import evidence_requires_human_review


def refresh_signal_epistemic_from_evidence(
    sig: Signal, db: Session, *, case_subject_type: str | None
) -> None:
    raw = sig.evidence_ids or "[]"
    try:
        ids = json.loads(raw)
    except json.JSONDecodeError:
        ids = []
    if not isinstance(ids, list):
        ids = []
    levels: list[str] = []
    for eid in ids:
        try:
            uid = uuid.UUID(str(eid))
        except ValueError:
            continue
        ent = db.get(EvidenceEntry, uid)
        if ent and (ent.epistemic_level or "").strip():
            levels.append(str(ent.epistemic_level).strip())
    sig.epistemic_level = aggregate_epistemic_levels(levels) if levels else REPORTED
    blob = f"{sig.description or ''}\n{sig.weight_explanation or ''}"
    sig.requires_human_review = evidence_requires_human_review(
        epistemic_level=sig.epistemic_level,
        subject_type=case_subject_type,
        title=sig.description or "",
        body=blob,
    )
    db.add(sig)
