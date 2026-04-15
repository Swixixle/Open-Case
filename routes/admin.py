"""Development / ops helpers — require X-Admin-Secret (ADMIN_SECRET env)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from adapters.cache import flush_adapter_cache
from core.admin_gate import require_admin_http
from database import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/clear-cache")
def clear_adapter_cache(
    db: Session = Depends(get_db),
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> dict[str, str | int]:
    """Delete all rows in adapter_cache (forces fresh API calls on next investigate)."""
    require_admin_http(x_admin_secret)
    deleted = flush_adapter_cache(db, None)
    db.commit()
    return {
        "deleted": deleted,
        "message": "adapter_cache table cleared",
    }
