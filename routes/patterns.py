from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from engines.pattern_engine import (
    PATTERN_ENGINE_VERSION,
    RULE_SOFT_BUNDLE_V2,
    filter_pattern_alerts,
    pattern_alert_to_payload,
    run_pattern_engine,
)

router = APIRouter(prefix="/api/v1", tags=["patterns"])


@router.get("/patterns/diagnostics")
def get_pattern_diagnostics(
    case_id: uuid.UUID = Query(..., description="Alerts for this case UUID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """SOFT_BUNDLE_V2 weight components without opening raw receipts."""
    alerts = run_pattern_engine(db)
    v2 = filter_pattern_alerts(alerts, case_id=case_id, rule=RULE_SOFT_BUNDLE_V2)
    return {
        "case_id": str(case_id),
        "alerts": [pattern_alert_to_payload(a) for a in v2],
        "total": len(v2),
        "pattern_engine_version": PATTERN_ENGINE_VERSION,
    }


@router.get("/patterns")
def get_patterns(
    donor: str | None = Query(None, description="Filter by donor name (substring match)"),
    rule: str | None = Query(None, description="Filter by rule_id e.g. COMMITTEE_SWEEP_V1"),
    case_id: uuid.UUID | None = Query(None, description="Alerts involving this case UUID"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run_at = datetime.now(timezone.utc)
    alerts = run_pattern_engine(db)
    alerts = filter_pattern_alerts(alerts, donor=donor, rule=rule, case_id=case_id)
    return {
        "alerts": [pattern_alert_to_payload(a) for a in alerts],
        "total": len(alerts),
        "pattern_engine_version": PATTERN_ENGINE_VERSION,
        "run_at": run_at.isoformat(),
    }
