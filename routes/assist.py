"""
Optional LLM assist endpoints (narrative only).

Core investigations, pattern detection, and entity resolution do not use these models.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_api_key
from models import Investigator
from services.llm_router import (
    LLMConfigurationError,
    build_story_angles_prompt,
    classify_story_angles_tier,
    generate_story_angles,
)

router = APIRouter(prefix="/api/v1/assist", tags=["assist"])


class StoryAnglesBody(BaseModel):
    dossier: dict[str, Any] = Field(default_factory=dict)


@router.post("/story-angles")
async def post_story_angles(
    body: StoryAnglesBody,
    _inv: Investigator = Depends(require_api_key),
) -> dict[str, Any]:
    tier = classify_story_angles_tier(body.dossier)
    prompt = build_story_angles_prompt(body.dossier)
    try:
        angles = await generate_story_angles(prompt, tier)
    except LLMConfigurationError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Story angle generation failed: {e!s}"[:2000],
        ) from e
    return {"tier": tier.value, "angles": angles}
