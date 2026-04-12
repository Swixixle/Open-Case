"""
Perplexity Sonar adapter — two-phase enrichment (cold facts → neutral narrative).

Uses PERPLEXITY_API_KEY; missing key logs a warning and returns an empty structure.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"

ENRICHMENT_QUERIES = [
    '"{name}" financial disclosure {year}',
    '"{name}" ethics investigation OR lawsuit OR indictment',
    '"{name}" board seat OR business interest OR spouse OR family',
    '"{name}" news site:reuters.com OR site:apnews.com OR site:propublica.org',
]

PHASE_1_SYSTEM = """You are a structured data extractor for a civic accountability tool.
You receive documents about a public official. Extract only factual statements that are
directly supported by the provided text.

Rules:
- Every claim must reference at least one source from the provided documents
- If no source exists for a claim, do not include it
- No interpretation, no causation, no speculation
- Format: {"claim": "...", "date": "...", "amount": null, "source": "...", "type": "fact"}
- Return only a JSON array, no prose"""

PHASE_2_SYSTEM = """You receive a list of verified factual claims about a public official.
Write a brief neutral summary.

Banned language: corrupt, criminal, bribed, illegal, in exchange for, because of donations,
quid pro quo, scandal.

Required language: "public records document", "coincides with", "has been alleged",
"records show", "according to [source]".

Required structure:
- Fact: state what records show
- Allegation vs outcome: always distinguish filed/dismissed/substantiated/unknown
- Disclaimer: "These patterns do not prove causation or wrongdoing; they document
  public records for further human review."

Never connect a donation to a vote unless: same issue domain AND within 180 days.
If uncertain, write "records do not establish a connection."
Return plain text only."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_json_array(text: str) -> list[Any]:
    """Parse JSON array from model output; tolerate markdown fences."""
    t = (text or "").strip()
    if not t:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if fence:
        t = fence.group(1).strip()
    try:
        data = json.loads(t)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        # Try first [...] block
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
        return []


def _call_sonar(
    system_prompt: str,
    user_content: str,
    api_key: str,
    *,
    timeout: float = 120.0,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "search_recency_filter": "month",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(PERPLEXITY_API_URL, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


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


def fetch_perplexity_enrichment(
    subject_name: str,
    bioguide_id: str | None = None,
) -> dict[str, Any]:
    """
    Two-phase enrichment: Phase 1 extracts JSON claims per query; Phase 2 narrative.

    Returns:
      {
        "findings": list[dict],  # structured claims (normalized upstream in service)
        "narrative": str,
        "phase_1_claims": list[dict],
        "retrieved_at": str,
        "query_errors": list[str],
      }
    """
    _ = bioguide_id
    api_key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    if not api_key:
        logger.warning(
            "PERPLEXITY_API_KEY is not set; skipping Perplexity enrichment "
            "(investigate and other routes continue normally)."
        )
        return {
            "findings": [],
            "narrative": "",
            "phase_1_claims": [],
            "retrieved_at": _now_iso(),
            "query_errors": [],
        }

    name = (subject_name or "").strip()
    if not name:
        return {
            "findings": [],
            "narrative": "",
            "phase_1_claims": [],
            "retrieved_at": _now_iso(),
            "query_errors": [],
        }

    year = datetime.now(timezone.utc).year
    retrieved_at = _now_iso()
    phase_1_claims: list[dict[str, Any]] = []
    query_errors: list[str] = []

    for template in ENRICHMENT_QUERIES:
        query = template.format(name=name, year=year)
        user_p1 = (
            f"Research query: {query}\n\n"
            f"Subject: {name}\n"
            "Using only retrieved sources, output the JSON array of fact objects as specified."
        )
        try:
            data = _call_sonar(PHASE_1_SYSTEM, user_p1, api_key)
        except Exception as e:
            logger.warning("Perplexity phase-1 failed for query=%r: %s", query, e)
            query_errors.append(f"{query}: {e!s}")
            continue

        raw_text = _assistant_text(data)
        arr = _extract_json_array(raw_text)
        for item in arr:
            if isinstance(item, dict) and str(item.get("claim", "")).strip():
                item = dict(item)
                item["_query"] = query
                phase_1_claims.append(item)

    narrative = ""
    if phase_1_claims:
        user_p2 = (
            "Verified factual claims (JSON):\n"
            + json.dumps(phase_1_claims, ensure_ascii=False, default=str)
        )
        try:
            data2 = _call_sonar(PHASE_2_SYSTEM, user_p2, api_key)
            narrative = _assistant_text(data2).strip()
        except Exception as e:
            logger.warning("Perplexity phase-2 failed: %s", e)
            query_errors.append(f"phase2: {e!s}")

    return {
        "findings": [],
        "narrative": narrative,
        "phase_1_claims": phase_1_claims,
        "retrieved_at": retrieved_at,
        "query_errors": query_errors,
    }
