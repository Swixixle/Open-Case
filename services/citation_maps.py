"""
Category-scoped citation URL lists for senator deep research (Perplexity and similar).

Emits {index, url} rows in stable 1-based order for the *current research block only*.
Does not invent URLs from bracket numbers alone — callers must supply ordered URLs from upstream.
"""

from __future__ import annotations

import re
from typing import Any

_BRACKET_RE = re.compile(r"\[(\d+)\]")


def _urls_from_list(block: object) -> list[str]:
    """URLs in API list order; duplicate URLs kept so indices stay aligned with the model."""
    out: list[str] = []
    if not isinstance(block, list) or not block:
        return out
    for item in block:
        if isinstance(item, str):
            u = item.strip()
        elif isinstance(item, dict):
            raw = item.get("url") or item.get("href") or item.get("link")
            u = str(raw or "").strip()
        else:
            continue
        if u.startswith("http://") or u.startswith("https://"):
            out.append(u)
    return out


def ordered_urls_from_perplexity_response(data: dict[str, Any]) -> list[str]:
    """
    Best-effort ordered URL list from a Perplexity-style chat/completions JSON body.
    Uses the first non-empty citation/search list found (priority order) so we do not
    concatenate multiple API sections in a way that could mis-order indices.
    """
    candidates: list[object] = [
        data.get("citations"),
        data.get("search_results"),
    ]
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            candidates.append(ch0.get("citations"))
            candidates.append(ch0.get("search_results"))
            msg = ch0.get("message")
            if isinstance(msg, dict):
                candidates.append(msg.get("citations"))
                candidates.append(msg.get("search_results"))

    for block in candidates:
        urls = _urls_from_list(block)
        if urls:
            return urls
    return []


def references_payload_from_ordered_urls(urls: list[str]) -> list[dict[str, Any]]:
    """1-based indices matching bracket order in model output for this block."""
    out: list[dict[str, Any]] = []
    for i, u in enumerate(urls):
        u = (u or "").strip()
        if not u.startswith("http://") and not u.startswith("https://"):
            continue
        out.append({"index": i + 1, "url": u})
    return out


def _index_to_url_from_references(references: list[dict[str, Any]]) -> dict[int, str]:
    m: dict[int, str] = {}
    for item in references:
        if not isinstance(item, dict):
            continue
        u = str(item.get("url") or "").strip()
        if not u.startswith("http://") and not u.startswith("https://"):
            continue
        raw = item.get("index")
        if raw is None:
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n < 1:
            continue
        if n not in m:
            m[n] = u
    return m


def enrich_claim_sources_from_references(
    claims: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> None:
    """
    Append resolvable http URLs to claim[\"sources\"] for [n] markers in claim[\"source\"].
    Does not modify claim text, claim[\"source\"] string, or bracket numbering.
    """
    if not references:
        return
    idx_map = _index_to_url_from_references(references)
    if not idx_map:
        return

    for c in claims:
        if not isinstance(c, dict):
            continue
        src = str(c.get("source") or "").strip()
        if not src or "[" not in src:
            continue
        ids: list[int] = []
        for m in _BRACKET_RE.finditer(src):
            try:
                ids.append(int(m.group(1)))
            except ValueError:
                continue
        if not ids:
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
        for n in ids:
            u = idx_map.get(n)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        if out:
            c["sources"] = out
