"""Auto-classify epistemic level from source URL, name, and text cues."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

VERIFIED = "VERIFIED"
REPORTED = "REPORTED"
ALLEGED = "ALLEGED"
DISPUTED = "DISPUTED"
CONTEXTUAL = "CONTEXTUAL"

ALL_LEVELS: tuple[str, ...] = (VERIFIED, REPORTED, ALLEGED, DISPUTED, CONTEXTUAL)

# Strongest (most authoritative) first — aggregate uses weakest among sources.
LEVEL_RANK: dict[str, int] = {
    CONTEXTUAL: 1,
    ALLEGED: 2,
    DISPUTED: 3,
    REPORTED: 4,
    VERIFIED: 5,
}

SOURCE_RULES: dict[str, tuple[str, ...]] = {
    VERIFIED: (
        "fec.gov",
        "sec.gov",
        "doj.gov",
        "justice.gov",
        "epa.gov",
        "dol.gov",
        "ftc.gov",
        "pacer",
        "court_document",
        "congress.gov",
        "senate.gov",
        "fjc.gov",
        "regulations.gov",
        "ejudiciary",
        "courtlistener.com",
        "gis.indy.gov",
        "data.indy.gov",
    ),
    REPORTED: (
        "nytimes.com",
        "propublica.org",
        "reuters.com",
        "apnews.com",
        "wsj.com",
        "washingtonpost.com",
        "theguardian.com",
        "bloomberg.com",
        "themarkup.org",
        "themarshallproject.org",
        "indianapolis.com",
        "chicagotribune.com",
        "indystar.com",
    ),
    ALLEGED: (
        "complaint",
        "allegation",
        "filed_against",
        "plaintiff_claim",
    ),
    CONTEXTUAL: (
        "wikipedia.org",
        "twitter.com",
        "facebook.com",
        "reddit.com",
    ),
}

DISPUTED_HINTS: tuple[str, ...] = (
    "denies",
    "denial",
    "counterclaim",
    "counter-evidence",
    "counter evidence",
    "disputed",
)


def _host_and_blob(url: str, source_name: str, body: str) -> tuple[str, str]:
    u = (url or "").strip().lower()
    host = ""
    if u.startswith("http"):
        try:
            host = (urlparse(u).hostname or "").lower()
        except ValueError:
            host = ""
    blob = " ".join(
        [
            u,
            (source_name or "").lower(),
            (body or "").lower(),
        ]
    )
    return host, blob


def classify_epistemic_level(
    *,
    source_url: str = "",
    source_name: str = "",
    body: str = "",
    title: str = "",
) -> str:
    """Return one of ALL_LEVELS; defaults to REPORTED."""
    host, blob = _host_and_blob(source_url, source_name, f"{title}\n{body}")

    for hint in DISPUTED_HINTS:
        if hint in blob:
            return DISPUTED

    for frag in SOURCE_RULES[ALLEGED]:
        if frag in blob or frag in host:
            return ALLEGED

    for domain in SOURCE_RULES[VERIFIED]:
        if domain in host or domain in blob:
            return VERIFIED

    for domain in SOURCE_RULES[CONTEXTUAL]:
        if domain in host or domain in blob:
            return CONTEXTUAL

    for domain in SOURCE_RULES[REPORTED]:
        if domain in host or domain in blob:
            return REPORTED

    return REPORTED


def aggregate_epistemic_levels(levels: list[str]) -> str:
    """Pick the weakest (lowest rank) level — conservative when mixing sources."""
    if not levels:
        return REPORTED
    cleaned = [x for x in levels if x in LEVEL_RANK]
    if not cleaned:
        return REPORTED
    return min(cleaned, key=lambda x: LEVEL_RANK[x])


def apply_epistemic_to_evidence_dict(entry: Any) -> tuple[str, str]:
    """Return (epistemic_level, source_key) for logging."""
    level = classify_epistemic_level(
        source_url=getattr(entry, "source_url", "") or "",
        source_name=getattr(entry, "source_name", "") or "",
        body=getattr(entry, "body", "") or "",
        title=getattr(entry, "title", "") or "",
    )
    u = (getattr(entry, "source_url", "") or "").strip()
    return level, u
