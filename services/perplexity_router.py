"""
Routed research calls: Perplexity (Sonar / Sonar Deep Research), Gemini, Claude.

Phase 1 (web-grounded extraction): route by topic — high-stakes / citation-critical
queries use Perplexity; lighter queries may try Gemini first with Perplexity fallback.

Phase 2 (narrative from in-context claims only): Claude first (no web search required),
then Perplexity Sonar, then Gemini.

Investigation cores (FEC, patterns, etc.) do not use this module.
"""

from __future__ import annotations

import logging
import os
import re
from enum import StrEnum
from typing import Any

import httpx

from services.llm_router import strip_json_fences
from utils.http_retry import http_request_with_retry

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MODEL_SONAR = "sonar"
MODEL_SONAR_DEEP_RESEARCH = "sonar-deep-research"

# Bump when routing logic changes materially (invalidates adapter caches that embed it).
ROUTER_CACHE_BUMP = "rr1"


class ResearchPhase1Kind(StrEnum):
    """Phase-1 extraction routing (JSON claims + optional Perplexity citations)."""

    perplexity_deep = "perplexity_deep"
    perplexity_sonar = "perplexity_sonar"
    gemini_first = "gemini_first"


_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+")


def classify_senator_deep_research_phase1(category: str) -> ResearchPhase1Kind:
    """
    Ethics / money-in-politics / investigation-heavy categories → Perplexity (deep or sonar).
    Routine disclosure-style category → Gemini first with fallback.
    """
    cat = (category or "").strip()
    if cat == "ethics_and_investigations":
        return ResearchPhase1Kind.perplexity_deep
    if cat == "financial_disclosures":
        return ResearchPhase1Kind.gemini_first
    if cat in (
        "donor_vs_vote_record",
        "public_statements_vs_votes",
        "revolving_door",
        "recent_news",
    ):
        return ResearchPhase1Kind.perplexity_sonar
    return ResearchPhase1Kind.perplexity_sonar


def classify_enrichment_phase1_template_index(template_index: int) -> ResearchPhase1Kind:
    """ENRICHMENT_QUERIES order in perplexity_enrichment.py."""
    if template_index == 0:
        return ResearchPhase1Kind.gemini_first
    if template_index == 1:
        return ResearchPhase1Kind.perplexity_deep
    return ResearchPhase1Kind.perplexity_sonar


def classify_staff_network_phase1() -> ResearchPhase1Kind:
    """
    Staff lists need source-backed names; default remains Perplexity Sonar.
    Set STAFF_NETWORK_TRY_GEMINI=1 to try Gemini first (biographic context only),
    then fall back to Sonar.
    """
    raw = (os.environ.get("STAFF_NETWORK_TRY_GEMINI") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        return ResearchPhase1Kind.gemini_first
    return ResearchPhase1Kind.perplexity_sonar


def _wrap_openai_style_assistant(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (content or "").strip(),
                }
            }
        ]
    }


def _assistant_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    ch0 = choices[0]
    if not isinstance(ch0, dict):
        return ""
    msg = ch0.get("message")
    if not isinstance(msg, dict):
        return ""
    return str(msg.get("content") or "")


def enrich_claims_with_inline_urls(claims: list[dict[str, Any]]) -> None:
    """When Perplexity citation lists are absent (e.g. Gemini), attach http(s) from text fields."""
    for c in claims:
        if not isinstance(c, dict):
            continue
        blob = f"{c.get('claim', '')} {c.get('source', '')}"
        found = _URL_RE.findall(blob)
        if not found:
            continue
        existing = c.get("sources")
        if not isinstance(existing, list):
            existing = []
        seen = {
            str(x).strip()
            for x in existing
            if isinstance(x, str)
            and (x.strip().startswith("http://") or x.strip().startswith("https://"))
        }
        out: list[str] = []
        for x in existing:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        for u in found:
            u = u.rstrip(").,;]")
            if u not in seen and (u.startswith("http://") or u.startswith("https://")):
                seen.add(u)
                out.append(u)
        if out:
            c["sources"] = out


def _gemini_model() -> str:
    return (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()


def _claude_model() -> str:
    return (os.environ.get("CLAUDE_MODEL") or "claude-sonnet-4-20250514").strip()


def _call_gemini_sync(system_prompt: str, user_content: str, *, timeout: float) -> str:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = _gemini_model()
    url = GEMINI_URL.format(model=model)
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096},
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, params={"key": key}, json=body)
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


def _call_claude_sync(system_prompt: str, user_content: str, *, max_tokens: int, timeout: float) -> str:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    payload: dict[str, Any] = {
        "model": _claude_model(),
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(ANTHROPIC_URL, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    blocks = data.get("content") or []
    texts = [b.get("text", "") for b in blocks if isinstance(b, dict)]
    return "".join(texts).strip()


def _call_perplexity_sync(
    model: str,
    system_prompt: str,
    user_content: str,
    api_key: str,
    *,
    timeout: float,
    search_recency_filter: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if search_recency_filter and model == MODEL_SONAR:
        body["search_recency_filter"] = search_recency_filter
    resp = http_request_with_retry(
        "POST",
        PERPLEXITY_API_URL,
        headers=headers,
        json=body,
        timeout=timeout,
    )
    data = resp.json()
    return data if isinstance(data, dict) else {}


def run_phase1_extraction(
    system_prompt: str,
    user_content: str,
    *,
    classification: ResearchPhase1Kind,
    perplexity_api_key: str,
    timeout: float = 300.0,
) -> tuple[dict[str, Any], list[str]]:
    """
    Returns (Perplexity-shaped response dict, ordered list of provider attempts for logs).
    """
    trail: list[str] = []
    pkey = (perplexity_api_key or "").strip()
    if classification != ResearchPhase1Kind.gemini_first and not pkey:
        raise RuntimeError("PERPLEXITY_API_KEY is not set")

    def _deep() -> dict[str, Any]:
        if not pkey:
            raise RuntimeError("PERPLEXITY_API_KEY is not set")
        trail.append("perplexity_deep")
        return _call_perplexity_sync(
            MODEL_SONAR_DEEP_RESEARCH,
            system_prompt,
            user_content,
            pkey,
            timeout=timeout,
            search_recency_filter=None,
        )

    def _sonar() -> dict[str, Any]:
        if not pkey:
            raise RuntimeError("PERPLEXITY_API_KEY is not set")
        trail.append("perplexity_sonar")
        return _call_perplexity_sync(
            MODEL_SONAR,
            system_prompt,
            user_content,
            pkey,
            timeout=min(timeout, 180.0),
            search_recency_filter="month",
        )

    if classification == ResearchPhase1Kind.perplexity_deep:
        return _deep(), trail
    if classification == ResearchPhase1Kind.perplexity_sonar:
        return _sonar(), trail

    # gemini_first — try Gemini, then Sonar, then Deep Research (when key present)
    try:
        text = _call_gemini_sync(system_prompt, user_content, timeout=min(timeout, 120.0))
        trail.append("gemini")
        return _wrap_openai_style_assistant(text), trail
    except Exception as e:
        logger.warning("Gemini phase-1 failed; falling back to Perplexity: %s", e)
    if not pkey:
        raise RuntimeError(
            "Gemini phase-1 failed and PERPLEXITY_API_KEY is not set for web-backed fallback"
        ) from None
    try:
        return _sonar(), trail
    except Exception as e:
        logger.warning("Perplexity Sonar phase-1 failed; falling back to Deep Research: %s", e)
    return _deep(), trail


def run_phase2_narrative(
    system_prompt: str,
    user_content: str,
    *,
    perplexity_api_key: str,
    timeout: float = 120.0,
) -> tuple[str, list[str]]:
    """
    Synthesis from claims already in the user message — prefer Claude (no web search),
    then Perplexity Sonar, then Gemini.
    """
    trail: list[str] = []
    pkey = (perplexity_api_key or "").strip()

    try:
        text = _call_claude_sync(
            system_prompt,
            user_content,
            max_tokens=1200,
            timeout=min(timeout, 180.0),
        )
        trail.append("claude")
        return strip_json_fences(text).strip(), trail
    except Exception as e:
        logger.warning("Claude phase-2 narrative failed: %s", e)

    if pkey:
        try:
            data = _call_perplexity_sync(
                MODEL_SONAR,
                system_prompt,
                user_content,
                pkey,
                timeout=timeout,
                search_recency_filter=None,
            )
            trail.append("perplexity_sonar")
            return _assistant_text(data).strip(), trail
        except Exception as e:
            logger.warning("Perplexity Sonar phase-2 failed: %s", e)

    try:
        text = _call_gemini_sync(system_prompt, user_content, timeout=min(timeout, 120.0))
        trail.append("gemini")
        return strip_json_fences(text).strip(), trail
    except Exception as e:
        logger.warning("Gemini phase-2 failed: %s", e)
    return "", trail
