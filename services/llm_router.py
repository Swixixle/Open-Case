"""
Routed LLM calls for optional narrative assist (story angles).

Investigation core (pattern engine, FEC, votes, entity resolution) is deterministic;
this module is only for tasks that intentionally invoke a generative model.
"""

from __future__ import annotations

import json
import logging
import os
import re
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

_COMPLEX_RULE_IDS = frozenset(
    {
        "SOFT_BUNDLE_V1",
        "SOFT_BUNDLE_V2",
        "ALIGNMENT_ANOMALY_V1",
        "AMENDMENT_TELL_V1",
        "SECTOR_CONVERGENCE_V1",
        "COMMITTEE_SWEEP_V1",
    }
)


class TaskTier(StrEnum):
    simple = "simple"
    medium = "medium"
    complex = "complex"


class LLMConfigurationError(RuntimeError):
    """No provider credentials available for the requested tier."""


def strip_json_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _alert_numeric_scores(alert: dict[str, Any]) -> list[float]:
    out: list[float] = []
    for key in ("suspicion_score", "proximity_to_vote_score"):
        v = alert.get(key)
        if isinstance(v, (int, float)):
            out.append(float(v))
    extra = alert.get("payload_extra")
    if isinstance(extra, dict):
        fw = extra.get("final_weight")
        if isinstance(fw, (int, float)):
            out.append(float(fw))
    return out


def classify_story_angles_tier(dossier: dict[str, Any]) -> TaskTier:
    """
    Heuristic tier for story-angle generation from dossier shape.
    Mirrors product intent: heavy vote–money patterns → stronger model.
    """
    alerts = dossier.get("pattern_alerts") or []
    if not isinstance(alerts, list):
        alerts = []

    dr = dossier.get("deep_research") or {}
    cats = {}
    if isinstance(dr, dict):
        cats = dr.get("categories") or {}
    try:
        cat_len = len(json.dumps(cats, default=str))
    except (TypeError, ValueError):
        cat_len = 0

    n_alerts = len(alerts)
    max_score = 0.0
    complex_rule = False
    for a in alerts:
        if not isinstance(a, dict):
            continue
        rid = str(a.get("rule_id") or "")
        if rid in _COMPLEX_RULE_IDS:
            complex_rule = True
        for s in _alert_numeric_scores(a):
            max_score = max(max_score, s)

    if complex_rule or max_score >= 0.85 or n_alerts >= 5 or cat_len > 6000:
        return TaskTier.complex
    if n_alerts <= 1 and cat_len < 2000 and max_score < 0.5:
        return TaskTier.simple
    return TaskTier.medium


def build_story_angles_prompt(dossier: dict[str, Any]) -> str:
    sub = dossier.get("subject") or {}
    if not isinstance(sub, dict):
        sub = {}
    name = sub.get("name") or "Official"
    state = sub.get("state") or ""
    cats = {}
    dr = dossier.get("deep_research") or {}
    if isinstance(dr, dict):
        raw_cats = dr.get("categories")
        if isinstance(raw_cats, dict):
            cats = raw_cats

    gaps = dossier.get("gap_analysis") or []
    alerts = dossier.get("pattern_alerts") or []
    dark_money = dossier.get("dark_money") or []
    ethics_travel = dossier.get("ethics_travel") or []
    committee_witnesses = dossier.get("committee_witnesses") or []

    def _clip(obj: Any, n: int) -> str:
        try:
            s = json.dumps(obj, indent=2, default=str)
        except (TypeError, ValueError):
            s = str(obj)
        return s[:n]

    return f"""You are an investigative journalism assistant.

Given this official dossier data, generate 3-5 specific newsworthy story angles.

Official: {name} ({state})

Deep research findings:
{_clip(cats, 3000)}

Gap analysis:
{_clip(gaps, 1000)}

Pattern alerts:
{_clip(alerts, 1000)}

Dark money connections:
{_clip(dark_money, 500)}

Ethics and travel:
{_clip(ethics_travel, 500)}

Committee witness overlaps:
{_clip(committee_witnesses, 500)}

Return ONLY a JSON array, no prose, no markdown:
[{{
  "headline": "Short punchy headline",
  "angle": "2-3 sentence story description with specific facts from the dossier",
  "why_now": "One sentence on timeliness",
  "source_types": ["FEC", "LDA", "Ethics filing"]
}}]

Rules:
- Use only facts present in the dossier data
- No causal language — say "coincides with" not "because of"
- No accusations — document patterns only
- If data is sparse, say so and suggest what reporting would reveal
- Always note findings require independent verification"""


def _gemini_model() -> str:
    return (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()


def _claude_model() -> str:
    return (os.environ.get("CLAUDE_MODEL") or "claude-sonnet-4-20250514").strip()


async def _call_gemini(user_prompt: str) -> str:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise LLMConfigurationError("GEMINI_API_KEY is not set")
    model = _gemini_model()
    url = GEMINI_URL.format(model=model)
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, params={"key": key}, json=body)
        r.raise_for_status()
        data = r.json()
    parts = (
        (((data.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
    )
    texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
    out = "".join(texts).strip()
    if not out:
        raise ValueError("Gemini returned empty text")
    return out


async def _call_claude(user_prompt: str) -> str:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise LLMConfigurationError("ANTHROPIC_API_KEY is not set")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    payload = {
        "model": _claude_model(),
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(ANTHROPIC_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    blocks = data.get("content") or []
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
    return "".join(texts).strip()


def _parse_angles_json(text: str) -> list[dict[str, Any]]:
    cleaned = strip_json_fences(text)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("model output is not a JSON array")
    return [x for x in parsed if isinstance(x, dict)]


async def generate_story_angles(user_prompt: str, tier: TaskTier) -> list[dict[str, Any]]:
    """
    Tier routing:
    - simple: Gemini, then Claude if Gemini fails or JSON invalid
    - medium: Gemini first; Claude on failure or invalid JSON
    - complex: Claude first; Gemini fallback if Claude fails (availability)
    """
    async def _try_gemini() -> list[dict[str, Any]]:
        raw = await _call_gemini(user_prompt)
        return _parse_angles_json(raw)

    async def _try_claude() -> list[dict[str, Any]]:
        raw = await _call_claude(user_prompt)
        return _parse_angles_json(raw)

    if tier == TaskTier.complex:
        try:
            return await _try_claude()
        except Exception as e:
            logger.warning("Claude story-angles failed, trying Gemini: %s", e)
            try:
                return await _try_gemini()
            except Exception as e2:
                raise e from e2

    # simple + medium: prefer cheaper model first, then Claude
    last_err: Exception | None = None
    try:
        return await _try_gemini()
    except Exception as e:
        logger.warning("Gemini story-angles failed, falling back to Claude: %s", e)
        last_err = e
    try:
        return await _try_claude()
    except Exception as e:
        if last_err:
            raise e from last_err
        raise
