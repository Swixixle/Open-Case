from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, Field

from core.admin_gate import require_admin_http
from engines.entity_resolution import append_alias_entry, suggest_aliases_detail

router = APIRouter(prefix="/api/v1/entity-resolution", tags=["entity-resolution"])


@router.get("/suggest")
def entity_suggest(
    name_a: str = Query(..., min_length=1),
    name_b: str = Query(..., min_length=1),
) -> dict[str, Any]:
    return suggest_aliases_detail(name_a, name_b)


class AliasAppendBody(BaseModel):
    canonical_id: str = Field(..., min_length=1)
    canonical_name: str = Field(..., min_length=1)
    aliases: list[str] = Field(default_factory=list)
    provenance: str = "manual_review"
    added_by: str = Field(..., min_length=1)
    added_at: str = Field(..., min_length=1)


@router.post("/aliases")
def append_alias(
    body: AliasAppendBody,
    x_admin_secret: str | None = Header(None, alias="X-Admin-Secret"),
) -> dict[str, Any]:
    require_admin_http(x_admin_secret)
    entry = {
        "canonical_id": body.canonical_id.strip(),
        "canonical_name": body.canonical_name.strip(),
        "aliases": [str(a).strip() for a in body.aliases if str(a).strip()],
        "provenance": body.provenance.strip(),
        "added_by": body.added_by.strip(),
        "added_at": body.added_at.strip(),
    }
    append_alias_entry(entry)
    return {"ok": True, "canonical_id": entry["canonical_id"]}
