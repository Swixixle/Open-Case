"""
Authentication dependency for Open Case write routes.

GET routes stay public. POST/PATCH on write paths require
`Authorization: Bearer open_case_<64 hex>`.

Keys are stored as SHA-256 hex (64 chars); plaintext is shown once at issuance.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Investigator


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_raw_key() -> str:
    return f"open_case_{secrets.token_hex(32)}"


def require_matching_handle(investigator: Investigator, handle: str) -> None:
    """Reject spoofed body handles (must match authenticated investigator)."""
    if investigator.handle != handle:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="investigator_handle must match the authenticated API key holder",
        )


def require_api_key(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Investigator:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be: Bearer open_case_...",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_key = authorization.removeprefix("Bearer ").strip()

    if not raw_key.startswith("open_case_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid key format. Expected: open_case_[64 hex chars]",
            headers={"WWW-Authenticate": "Bearer"},
        )

    hashed = hash_key(raw_key)
    row = db.scalar(
        select(Investigator).where(Investigator.hashed_api_key == hashed)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return row
