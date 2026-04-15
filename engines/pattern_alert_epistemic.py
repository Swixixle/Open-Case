"""Derive epistemic_level and human-review flags for pattern alerts."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from models import CaseFile, EvidenceEntry
from services.epistemic_classifier import REPORTED, aggregate_epistemic_levels
from services.human_review import pattern_alert_requires_human_review

if TYPE_CHECKING:
    from engines.pattern_engine import PatternAlert
    from sqlalchemy.orm import Session


def enrich_pattern_alerts_epistemic_metadata(db: "Session", alerts: list["PatternAlert"]) -> None:
    for a in alerts:
        levels: list[str] = []
        for ref in a.evidence_refs:
            try:
                uid = uuid.UUID(str(ref).strip())
            except ValueError:
                continue
            ent = db.get(EvidenceEntry, uid)
            if ent and (ent.epistemic_level or "").strip():
                levels.append(str(ent.epistemic_level).strip())
        a.epistemic_level = aggregate_epistemic_levels(levels) if levels else REPORTED

        stypes: list[str | None] = []
        for cid in a.matched_case_ids:
            try:
                c_uuid = uuid.UUID(str(cid).strip())
            except ValueError:
                continue
            cf = db.get(CaseFile, c_uuid)
            if cf:
                stypes.append(cf.subject_type)

        a.requires_human_review = pattern_alert_requires_human_review(
            epistemic_level=a.epistemic_level,
            rule_id=a.rule_id,
            rule_line=f"{a.rule_id} {a.donor_entity}",
            matched_case_subject_types=stypes,
        )
