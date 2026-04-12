"""
Amendment vote fingerprint: Congress.gov amendment votes vs FEC donor sector profiles.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from adapters.congress_votes import fetch_amendment_votes_for_member
from core.credentials import CredentialRegistry
from engines.pattern_engine import _sectors_matching_vote_text, classify_donor_sector

logger = logging.getLogger(__name__)

CONGRESS_AMENDMENTS_URL = (
    "https://api.congress.gov/v3/amendment?congress={congress}&limit=250&offset={offset}&api_key={key}"
)
CONGRESS_AMENDMENT_VOTES_URL = (
    "https://api.congress.gov/v3/amendment/{congress}/{amendmentType}/{amendmentNumber}/actions?api_key={key}"
)

CACHE_ADAPTER = "senator_amendment_fingerprint"
CACHE_TTL_HOURS = 7 * 24

AMENDMENT_FINGERPRINT_DISCLAIMER = (
    "Amendment vote alignment with donor sectors is a documented pattern from "
    "public congressional records. Alignment does not establish intent, causation, "
    "or wrongdoing. Senators may oppose donors for unrelated policy reasons."
)

ENFORCEMENT_STRIP_RE = re.compile(
    r"(penalt(y|ies)|civil\s+penalt|civil\s+enforcement|enforcement\s+mechanism|"
    r"reduce\s+(the\s+)?(fine|penalt)|weaken(ing)?\s+(enforcement|penalt)|"
    r"limit\s+enforcement|strike\s+.*?enforcement|weakened\s+penalt)",
    re.I,
)

CONGRESSES_ANALYZED = (118, 119, 120)


def top_donor_sectors_for_case(db: Session, case_file_id: UUID, *, limit: int = 5) -> list[str]:
    from models import EvidenceEntry

    rows = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_file_id,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    ).all()
    by_donor: defaultdict[str, float] = defaultdict(float)
    for e in rows:
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        donor = str(raw.get("contributor_name") or "").strip()
        if not donor:
            continue
        amt = float(e.amount or raw.get("contribution_receipt_amount") or 0)
        by_donor[donor] += amt
    ranked = sorted(by_donor.items(), key=lambda x: -x[1])[:25]
    sectors: list[str] = []
    for donor, _ in ranked:
        s = classify_donor_sector(donor, "", "")
        if s and s not in sectors:
            sectors.append(s)
        if len(sectors) >= limit:
            break
    return sectors


def _vote_position(record: dict[str, Any]) -> str:
    p = str(record.get("vote_position") or record.get("position") or "").strip()
    pl = p.lower()
    if pl in ("yea", "yay", "aye") or p.upper() == "YEA":
        return "Yea"
    if pl in ("nay", "no") or p.upper() == "NAY":
        return "Nay"
    return "Not Voting"


def analyze_amendment_votes(
    votes: list[dict[str, Any]],
    top_donor_sectors: list[str],
) -> dict[str, Any]:
    top_set = set(top_donor_sectors)
    donor_aligned_votes = 0
    donor_opposed_votes = 0
    enforcement_stripping_count = 0
    notable: list[dict[str, Any]] = []
    align_sector_hits: Counter[str] = Counter()

    for v in votes:
        if not isinstance(v, dict):
            continue
        desc = str(v.get("amendment_description") or v.get("description") or "")
        bill = str(v.get("bill_number") or "")
        blob = f"{desc} {bill}"
        vote_sectors = _sectors_matching_vote_text(blob)
        donor_sector_match = bool(vote_sectors & top_set) if top_set else False
        enforcement_relevant = bool(ENFORCEMENT_STRIP_RE.search(blob))
        pos = _vote_position(v)

        issue_areas = sorted(vote_sectors)

        if enforcement_relevant and pos == "Yea":
            enforcement_stripping_count += 1

        if donor_sector_match:
            if pos == "Yea":
                donor_aligned_votes += 1
                for s in vote_sectors & top_set:
                    align_sector_hits[s] += 1
            elif pos == "Nay":
                donor_opposed_votes += 1

        if donor_sector_match or enforcement_relevant:
            if len(notable) < 75:
                notable.append(
                    {
                        "amendment_id": str(
                            v.get("amendment_number") or v.get("amendmentNumber") or ""
                        ),
                        "description": desc[:2000],
                        "vote": pos,
                        "issue_area": ", ".join(issue_areas) if issue_areas else "other",
                        "donor_sector_match": donor_sector_match,
                        "enforcement_relevant": enforcement_relevant,
                        "bill_title": bill or str(v.get("bill_title") or ""),
                        "vote_date": str(v.get("vote_date") or "")[:10],
                        "source_url": str(v.get("source_url") or ""),
                    }
                )

    total = len([v for v in votes if isinstance(v, dict)])
    alignment_rate = float(donor_aligned_votes) / float(max(1, total))

    top_aligned_sectors = [s for s, _ in align_sector_hits.most_common(5)]

    return {
        "total_amendment_votes": total,
        "donor_aligned_votes": donor_aligned_votes,
        "donor_opposed_votes": donor_opposed_votes,
        "alignment_rate": round(alignment_rate, 4),
        "enforcement_stripping_count": enforcement_stripping_count,
        "top_aligned_sectors": top_aligned_sectors,
        "notable_amendments": notable,
    }


async def fetch_amendment_fingerprint(db: Session, bioguide_id: str, case_file_id: UUID) -> dict[str, Any]:
    """
    Aggregate amendment votes for congresses 118–120 (via member votes endpoint),
    cross-reference with top FEC donor sectors for the case.
    """
    bg = (bioguide_id or "").strip()
    cache_key = bg
    cached = get_cached_raw_json(db, CACHE_ADAPTER, cache_key)
    if cached is not None and isinstance(cached, dict) and "total_amendment_votes" in cached:
        return cached

    key = None
    try:
        key = CredentialRegistry.get_credential("congress")
    except Exception:
        key = None
    key = (key or "").strip() or None

    all_votes: list[dict[str, Any]] = []
    if key:
        for c in CONGRESSES_ANALYZED:
            try:
                part = await fetch_amendment_votes_for_member(bg, congress=c, api_key=key)
                all_votes.extend(part)
            except Exception as e:
                logger.warning("amendment votes congress=%s: %s", c, e)
    else:
        logger.warning("CONGRESS_API_KEY missing; amendment fingerprint empty")

    sectors = top_donor_sectors_for_case(db, case_file_id)
    analyzed = analyze_amendment_votes(all_votes, sectors)
    out: dict[str, Any] = {
        "bioguide_id": bg,
        "congresses_analyzed": list(CONGRESSES_ANALYZED),
        "disclaimer": AMENDMENT_FINGERPRINT_DISCLAIMER,
        **analyzed,
    }
    try:
        store_cached_raw_json(db, CACHE_ADAPTER, cache_key, out, CACHE_TTL_HOURS)
    except Exception as e:
        logger.warning("amendment fingerprint cache failed: %s", e)
    return out
