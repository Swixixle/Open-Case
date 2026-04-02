"""
API key issuance (unauthenticated).

POST /api/v1/auth/keys?handle=...
Generates open_case_<hex>, stores SHA-256 only, returns plaintext once.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import generate_raw_key, hash_key
from database import get_db
from models import Investigator

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/keys")
def generate_api_key(
    handle: str = Query(..., description="Investigator handle to generate key for"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    investigator = db.scalar(select(Investigator).where(Investigator.handle == handle))
    if not investigator:
        investigator = Investigator(handle=handle, public_key="")
        db.add(investigator)
        db.flush()

    raw_key = generate_raw_key()
    investigator.hashed_api_key = hash_key(raw_key)
    investigator.api_key_created_at = datetime.now(timezone.utc)

    db.commit()

    return {
        "handle": handle,
        "api_key": raw_key,
        "format": "open_case_[64 hex chars]",
        "warning": (
            "This key will not be shown again. Store it securely. "
            "Calling this endpoint again will revoke this key."
        ),
        "usage": f"Authorization: Bearer {raw_key}",
    }
