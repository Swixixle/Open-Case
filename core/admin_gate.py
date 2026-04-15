"""Shared admin-secret check for optional unreviewed / sensitive payloads."""

from __future__ import annotations

import os

from fastapi import HTTPException


def admin_authorized(x_admin_secret: str | None) -> bool:
    expected = os.getenv("ADMIN_SECRET", "").strip()
    return bool(expected and x_admin_secret and x_admin_secret == expected)


def require_admin_http(x_admin_secret: str | None) -> None:
    """
    Enforce X-Admin-Secret for privileged HTTP routes (same rules as
    POST /api/v1/system/credentials/register).
    """
    expected = os.getenv("ADMIN_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin endpoint not configured.")
    if not admin_authorized(x_admin_secret):
        raise HTTPException(status_code=403, detail="Invalid admin secret.")
