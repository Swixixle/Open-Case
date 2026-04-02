from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from core.credentials import CredentialRegistry

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class RegisterCredentialBody(BaseModel):
    adapter_name: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


@router.get("/credentials")
def list_credential_status() -> dict:
    """Adapter credential presence (never returns secret values)."""
    return {"credentials": CredentialRegistry.get_all_statuses()}


@router.post("/credentials/register")
def register_credential_file(
    body: RegisterCredentialBody,
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> dict:
    """
    Write adapter API key to CREDENTIAL_DATA_DIR (default /data/.credentials/).
    Requires X-Admin-Secret matching ADMIN_SECRET.
    """
    expected = os.getenv("ADMIN_SECRET", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoint not configured.",
        )
    if not x_admin_secret or x_admin_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid admin secret.")

    name = body.adapter_name.strip()
    if name not in CredentialRegistry.ADAPTERS:
        raise HTTPException(status_code=400, detail=f"Unknown adapter: {name}")
    if name in ("open_case_signing", "lda"):
        raise HTTPException(
            status_code=400,
            detail="This adapter cannot be registered via this endpoint.",
        )
    try:
        path = CredentialRegistry.write_credential_file(name, body.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "adapter": name, "path": str(path)}
