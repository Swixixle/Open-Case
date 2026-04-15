"""Fuzzy name scoring for subject search — last-name and substring first, overlap last."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def _normalize_name(s: str) -> str:
    s = re.sub(r"[,]+", " ", (s or "").strip())
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return " ".join(s.split())


def subject_name_match_score(query: str, display_name: str) -> float:
    """
    0..1 match quality. Priority:
    1) Last-name prefix / strong last-name similarity (single-token queries)
    2) Full normalized name substring
    3) Tiny tiebreaker from character similarity + token overlap (only if (1) or (2) scored)

    Unrelated names (no last-name signal, no substring) return 0 so callers can drop them.
    """
    q = _normalize_name(query)
    n = _normalize_name(display_name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if n.startswith(q):
        return min(1.0, 0.87 + 0.13 * (len(q) / max(len(n), 1)))

    parts = n.split()
    last = parts[-1] if parts else n
    q_parts = q.split()
    q_one = len(q_parts) == 1

    tier_last = 0.0
    # (1) Last name: query is a prefix of surname (e.g. sander → Sanders, gras → Grassley)
    if len(q) >= 2 and len(last) >= 2 and last.startswith(q):
        tier_last = max(tier_last, 0.91 + 0.09 * min(1.0, len(q) / len(last)))
    # Single-token query vs last name: fuzzy (coton→Cotton, warrn→Warren, cruz→Cruz)
    if q_one and len(last) >= 2:
        r_ql = SequenceMatcher(None, q, last).ratio()
        if r_ql >= 0.86:
            tier_last = max(tier_last, 0.84 + 0.15 * r_ql)
        elif r_ql >= 0.62:
            tier_last = max(tier_last, 0.48 + 0.38 * r_ql)

    # Multi-token query: first + last prefix hints (e.g. "bernie sand" → Bernie Sanders)
    if len(q_parts) >= 2 and len(parts) >= 2:
        if parts[0].startswith(q_parts[0]) and last.startswith(q_parts[-1][: max(2, len(q_parts[-1]))]):
            tier_last = max(tier_last, 0.89)

    tier_sub = 0.0
    # (2) Contiguous substring of full name (second priority)
    if len(q) >= 3 and q in n:
        tier_sub = 0.76 + 0.22 * min(1.0, len(q) / max(len(n), 1))

    base = max(tier_last, tier_sub)
    if base <= 0.0:
        return 0.0

    # (3) Tiebreaker only — token overlap and full-string ratio are weak signals
    qs = set(q_parts)
    ns = set(parts)
    inter = len(qs & ns)
    union = len(qs | ns) or 1
    jacc = inter / union
    seq = SequenceMatcher(None, q, n).ratio()
    tie = 0.04 * seq + 0.03 * jacc

    return min(1.0, base + tie)
