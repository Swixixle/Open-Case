"""
Perplexity sonar-deep-research adapter for public officials (six category pipeline).

Mirrors the two-phase flow in adapters/perplexity_enrichment.py with category-scoped
queries, AdapterCache (48h), and narrative validation from services.enrichment_service.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from services.enrichment_service import validate_narrative
from utils.http_retry import http_request_with_retry

logger = logging.getLogger(__name__)

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
CACHE_ADAPTER = "senator_deep_research"
CACHE_TTL_HOURS = 48

SENATOR_CATEGORIES = [
    "ethics_and_investigations",
    "financial_disclosures",
    "donor_vs_vote_record",
    "public_statements_vs_votes",
    "revolving_door",
    "recent_news",
]

SENATOR_QUERIES = {
    "ethics_and_investigations": (
        '"{name}" ethics complaint OR investigation OR censure OR OCE referral '
        "OR misconduct site:ethics.senate.gov OR site:oce.house.gov OR site:propublica.org "
        "OR site:nytimes.com OR site:washingtonpost.com"
    ),
    "financial_disclosures": (
        '"{name}" senator financial disclosure stock holdings '
        "OR outside income OR conflict of interest OR blind trust "
        "site:efts.senate.gov OR site:opensecrets.org OR site:disclosures.house.gov"
    ),
    "donor_vs_vote_record": (
        '"{name}" senator campaign donor industry PAC contribution '
        "voted legislation benefit site:opensecrets.org OR site:fec.gov "
        "OR site:propublica.org OR site:followthemoney.org"
    ),
    "public_statements_vs_votes": (
        '"{name}" senator promised OR pledged OR said vs voted '
        "OR contradiction OR flip OR reversal "
        "site:votesmart.org OR site:congress.gov OR site:rollcall.com"
    ),
    "revolving_door": (
        '"{name}" senator staff lobbyist K Street revolving door '
        "OR former aide OR chief of staff lobbying firm "
        "site:opensecrets.org OR site:propublica.org OR site:politico.com"
    ),
    "recent_news": (
        '"{name}" senator investigation OR scrutiny OR criticism OR controversy '
        "2023 OR 2024 OR 2025 OR 2026 "
        "site:propublica.org OR site:nytimes.com OR site:washingtonpost.com "
        "OR site:politico.com OR site:thehill.com"
    ),
}

PHASE_1_SYSTEM = """You are a structured data extractor for a civic accountability tool.
Extract only factual statements directly supported by the provided text.
Rules:
- Every claim must reference at least one source from the provided documents
- If no source exists for a claim, do not include it
- No interpretation, no causation, no speculation
- Format: {"claim": "...", "date": "...", "amount": null, "source": "...", "type": "fact"}
- Return only a JSON array, no prose"""

PHASE_2_SYSTEM = """You receive a list of verified factual claims about a public official.
Write a brief neutral summary.
Banned language: corrupt, criminal, bribed, illegal, in exchange for, because of donations,
quid pro quo, scandal, led to, caused by.
Required language: "public records document", "coincides with", "has been alleged",
"records show", "according to [source]".
Always distinguish filed/dismissed/substantiated/unknown for any allegation.
Required disclaimer at end: "These findings document public records only. They do not
prove causation or wrongdoing. All findings are for further human review."
Return plain text only."""

SONAR_DEEP_RESEARCH_MODEL = "sonar-deep-research"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cache_query_string(bioguide_id: str, category: str) -> str:
    return f"{bioguide_id}:{category}"


def _extract_json_array(text: str) -> list[Any]:
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
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                data = json.loads(m.group(0))
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
        return []


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


def _call_perplexity(system_prompt: str, user_content: str, api_key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": SONAR_DEEP_RESEARCH_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    resp = http_request_with_retry(
        "POST",
        PERPLEXITY_API_URL,
        headers=headers,
        json=body,
        timeout=300.0,
    )
    data = resp.json()
    return data if isinstance(data, dict) else {}


def fetch_senator_deep_research_category(
    db: Session,
    bioguide_id: str,
    subject_name: str,
    category: str,
) -> dict[str, Any]:
    """
    Run one research category (cached48h). Failures return empty claims and log errors
    without raising.
    """
    bg = (bioguide_id or "").strip()
    cat = (category or "").strip()
    name = (subject_name or "").strip()
    if cat not in SENATOR_QUERIES:
        return {
            "claims": [],
            "narrative": "",
            "query_errors": [f"unknown category: {cat}"],
            "retrieved_at": _now_iso(),
            "narrative_validation_flags": [],
            "needs_human_review": False,
            "from_cache": False,
        }

    qkey = _cache_query_string(bg, cat)
    cached = get_cached_raw_json(db, CACHE_ADAPTER, qkey)
    if cached is not None and isinstance(cached, dict):
        out = dict(cached)
        out["from_cache"] = True
        return out

    api_key = (os.environ.get("PERPLEXITY_API_KEY") or "").strip()
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY missing; senator deep research category skipped")
        empty = {
            "claims": [],
            "narrative": "",
            "query_errors": ["PERPLEXITY_API_KEY not set"],
            "retrieved_at": _now_iso(),
            "narrative_validation_flags": [],
            "needs_human_review": False,
            "from_cache": False,
        }
        return empty

    if not name:
        return {
            "claims": [],
            "narrative": "",
            "query_errors": ["subject_name empty"],
            "retrieved_at": _now_iso(),
            "narrative_validation_flags": [],
            "needs_human_review": False,
            "from_cache": False,
        }

    template = SENATOR_QUERIES[cat]
    query = template.format(name=name)
    query_errors: list[str] = []
    phase_1_claims: list[dict[str, Any]] = []

    user_p1 = (
        f"Research query: {query}\n\n"
        f"Subject: {name} (bioguide {bg})\n"
        "Using only retrieved sources, output the JSON array of fact objects as specified."
    )
    try:
        data = _call_perplexity(PHASE_1_SYSTEM, user_p1, api_key)
    except Exception as e:
        logger.warning("Perplexity phase-1 failed category=%s: %s", cat, e)
        query_errors.append(f"phase1: {e!s}")
        data = {}

    raw_text = _assistant_text(data)
    arr = _extract_json_array(raw_text)
    for item in arr:
        if isinstance(item, dict) and str(item.get("claim", "")).strip():
            row = dict(item)
            row["_query"] = query
            row["_category"] = cat
            phase_1_claims.append(row)

    narrative = ""
    if phase_1_claims:
        user_p2 = (
            "Verified factual claims (JSON):\n"
            + json.dumps(phase_1_claims, ensure_ascii=False, default=str)
        )
        try:
            data2 = _call_perplexity(PHASE_2_SYSTEM, user_p2, api_key)
            narrative = _assistant_text(data2).strip()
        except Exception as e:
            logger.warning("Perplexity phase-2 failed category=%s: %s", cat, e)
            query_errors.append(f"phase2: {e!s}")

    _, banned_flags = validate_narrative(narrative)
    needs_human_review = bool(banned_flags)

    result: dict[str, Any] = {
        "claims": phase_1_claims,
        "narrative": narrative,
        "query_errors": query_errors,
        "retrieved_at": _now_iso(),
        "narrative_validation_flags": banned_flags,
        "needs_human_review": needs_human_review,
        "from_cache": False,
    }
    try:
        store_cached_raw_json(db, CACHE_ADAPTER, qkey, result, CACHE_TTL_HOURS)
    except Exception as e:
        logger.warning("could not cache senator deep research: %s", e)

    return result


def fetch_all_senator_deep_research(
    db: Session,
    bioguide_id: str,
    subject_name: str,
) -> dict[str, Any]:
    """All categories; failures are isolated per category."""
    categories: dict[str, Any] = {}
    all_flags: list[str] = []
    any_review = False
    for cat in SENATOR_CATEGORIES:
        block = fetch_senator_deep_research_category(db, bioguide_id, subject_name, cat)
        categories[cat] = block
        flags = block.get("narrative_validation_flags") or []
        if isinstance(flags, list):
            all_flags.extend(str(f) for f in flags if f)
        if block.get("needs_human_review"):
            any_review = True
    return {
        "categories": categories,
        "needs_human_review": any_review,
        "narrative_validation_flags": sorted(set(all_flags)),
    }
