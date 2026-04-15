"""
Merge near-duplicate dossier deep-research claims before packaging (per category).

Uses token-frequency cosine similarity over normalized claim text. When similarity
exceeds the threshold, sources are merged into the first-seen claim and the duplicate
row is dropped.

Entity normalization uses ``engines.entity_resolution.canonicalize`` for optional
``_entity_canonical`` hints on merged rows (audit / UI alignment with donor resolution).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from engines.entity_resolution import canonicalize

_BANNED_FIRST = frozenset({
    "THE", "THIS", "ACCORDING", "SENATOR", "REPRESENTATIVE", "CONGRESS", "SENATE",
    "HOUSE", "UNITED", "AMERICAN", "NATIONAL", "FEDERAL", "STATE", "DEPARTMENT",
    "COMMITTEE", "STAFF", "FORMER", "CHIEF", "DURING", "AFTER", "BEFORE", "WHILE",
    "WHEN", "FOLLOWING", "PUBLIC", "RECORDS", "REPORT", "REPORTS", "OPENSECRETS",
    "FEC",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def extract_primary_entity(claim_text: str) -> str:
    """Heuristic aligned with ``client/src/lib/claimEntityGroups.js``."""
    t = (claim_text or "").strip()
    if not t:
        return "Other details"
    qm = re.match(r'^"([^"]{3,120})"', t)
    if qm:
        return qm.group(1).strip()[:120]
    best = None
    best_score = -1
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", t):
        phrase = m.group(1)
        parts = phrase.split()
        if parts[0].upper() in _BANNED_FIRST:
            continue
        if any(len(p) <= 1 for p in parts):
            continue
        score = 500 - m.start() + len(phrase) * 3
        if score > best_score:
            best_score = score
            best = phrase
    return best or "Other details"


def _claim_text(c: dict[str, Any]) -> str:
    return str(c.get("claim") or c.get("text") or "").strip()


def _sources_list(c: dict[str, Any]) -> list[str]:
    out: list[str] = []
    s = c.get("source")
    if s:
        out.append(str(s).strip())
    extra = c.get("sources")
    if isinstance(extra, list):
        for x in extra:
            if x:
                out.append(str(x).strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for u in out:
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def tf_cosine_similarity(a: str, b: str) -> float:
    """Cosine similarity of term-frequency vectors (lowercase tokens)."""
    ta = [x.lower() for x in _TOKEN_RE.findall(a or "")]
    tb = [x.lower() for x in _TOKEN_RE.findall(b or "")]
    if not ta or not tb:
        return 0.0
    ca, cb = Counter(ta), Counter(tb)
    vocab = set(ca) | set(cb)
    dot = sum(ca.get(v, 0) * cb.get(v, 0) for v in vocab)
    na = sum(c * c for c in ca.values()) ** 0.5
    nb = sum(c * c for c in cb.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _merge_into(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    combined = _sources_list(target) + _sources_list(incoming)
    seen: set[str] = set()
    urls: list[str] = []
    for u in combined:
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    if urls:
        target["sources"] = urls
        if not target.get("source"):
            target["source"] = urls[0]
    ent = extract_primary_entity(_claim_text(target))
    if ent and ent != "Other details":
        target["_entity_canonical"] = canonicalize(ent)


def dedupe_merge_claims(
    claims: list[Any],
    *,
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Preserve order: for each claim, merge into an earlier claim if similarity >= threshold.
    """
    merged: list[dict[str, Any]] = []
    for raw in claims:
        if not isinstance(raw, dict):
            continue
        text = _claim_text(raw)
        if not text:
            continue
        c = dict(raw)
        placed = False
        for i, bucket in enumerate(merged):
            sim = tf_cosine_similarity(text, _claim_text(bucket))
            if sim >= threshold:
                _merge_into(bucket, c)
                placed = True
                break
        if not placed:
            ent = extract_primary_entity(text)
            if ent and ent != "Other details":
                c["_entity_canonical"] = canonicalize(ent)
            merged.append(c)
    return merged
