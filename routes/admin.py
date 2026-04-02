"""
Development / ops helpers. Unauthenticated for now.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from adapters.cache import flush_adapter_cache
from database import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/clear-cache")
def clear_adapter_cache(db: Session = Depends(get_db)) -> dict[str, str | int]:
    """Delete all rows in adapter_cache (forces fresh API calls on next investigate)."""
    deleted = flush_adapter_cache(db, None)
    db.commit()
    return {
        "deleted": deleted,
        "message": "adapter_cache table cleared",
    }
