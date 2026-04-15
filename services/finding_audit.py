"""Persist audit events for findings (classification, render, disputes)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from models import FindingAuditLog


def log_finding_audit(
    db: Session,
    *,
    finding_id: uuid.UUID,
    event_type: str,
    detail: dict[str, Any],
) -> FindingAuditLog:
    row = FindingAuditLog(
        finding_id=finding_id,
        event_type=event_type[:64],
        detail_json=json.dumps(detail, sort_keys=True, default=str)[:50000],
    )
    db.add(row)
    db.flush()
    return row
