from __future__ import annotations

from fastapi import APIRouter

from core.credentials import CredentialRegistry

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/credentials")
def list_credential_status() -> dict:
    """Adapter credential presence (never returns secret values)."""
    return {"credentials": CredentialRegistry.get_all_statuses()}
