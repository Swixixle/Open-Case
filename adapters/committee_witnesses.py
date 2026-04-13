"""
Committee hearing witnesses vs FEC / LDA cross-reference (GovInfo CHRG).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.cache import get_cached_raw_json, store_cached_raw_json
from adapters.govinfo_hearings import current_congress_number, list_committee_hearing_witness_records
from adapters.lda import fetch_lda_filings
from adapters.staff_network import _entities_overlap, _fec_donor_strings_for_case
from models import EvidenceEntry, SenatorCommittee

logger = logging.getLogger(__name__)

CACHE_ADAPTER = "committee_witnesses"
CACHE_TTL_HOURS = 7 * 24

WITNESS_DISCLAIMER = (
    "Witness lists are derived from public hearing records. Employer overlap with donors "
    "or lobbyists is co-appearance in public data only."
)


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def affiliation_from_granule(granule_title: str, witness_guess: str) -> str:
    t = (granule_title or "").strip()
    if " of " in t.lower():
        return t.split(" of ", 1)[-1].strip()
    if "," in t:
        return t.split(",")[-1].strip()
    return (witness_guess or "").strip()


def _fec_donation_for_affiliation(db: Session, case_file_id: UUID, affiliation: str) -> float:
    if len((affiliation or "").strip()) < 3:
        return 0.0
    total = 0.0
    rows = db.scalars(
        select(EvidenceEntry).where(
            EvidenceEntry.case_file_id == case_file_id,
            EvidenceEntry.entry_type == "financial_connection",
            EvidenceEntry.source_name == "FEC",
        )
    ).all()
    for e in rows:
        try:
            raw = json.loads(e.raw_data_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        for key in ("contributor_name", "contributor_employer", "contributor_organization"):
            label = str(raw.get(key) or "").strip()
            if label and _entities_overlap(affiliation, label):
                try:
                    total += float(raw.get("contribution_receipt_amount") or e.amount or 0.0)
                except (TypeError, ValueError):
                    total += float(e.amount or 0.0)
                break
    return total


async def fetch_committee_witnesses(
    db: Session,
    bioguide_id: str,
    committees: list[SenatorCommittee],
    case_file_id: UUID,
) -> list[dict[str, Any]]:
    bg = (bioguide_id or "").strip()
    cached = get_cached_raw_json(db, CACHE_ADAPTER, bg)
    if isinstance(cached, dict) and isinstance(cached.get("witnesses"), list):
        return list(cached["witnesses"])

    api_key = (os.environ.get("GOVINFO_API_KEY") or "").strip()
    codes = [str(c.committee_code).strip().upper() for c in committees if c.committee_code]
    codes = [c for c in codes if c]
    if not api_key or not codes:
        try:
            store_cached_raw_json(
                db, CACHE_ADAPTER, bg, {"witnesses": []}, CACHE_TTL_HOURS
            )
        except Exception as e:
            logger.warning("committee_witnesses cache store failed: %s", e)
        return []

    congress = current_congress_number()
    records = await list_committee_hearing_witness_records(
        codes,
        congress,
        api_key,
        max_packages_per_code=6,
        max_granules_per_package=25,
    )
    fec_entities = _fec_donor_strings_for_case(db, case_file_id)
    committee_by_code = {str(c.committee_code).upper(): c.committee_name for c in committees}

    lda_cache: dict[str, bool] = {}

    out: list[dict[str, Any]] = []
    for rec in records[:80]:
        if not isinstance(rec, dict):
            continue
        code = str(rec.get("committee_code") or "").upper()
        comm_name = committee_by_code.get(code, code)
        title = str(rec.get("hearing_granule_title") or "")
        witness_name = str(rec.get("matched_name") or "")
        aff = affiliation_from_granule(title, witness_name)
        aff_key = _norm_key(aff)
        fec_donor_match = any(_entities_overlap(aff, fe) for fe in fec_entities) if aff else False
        lda_match = False
        if aff_key:
            if aff_key not in lda_cache:
                try:
                    lda_cache[aff_key] = bool(await fetch_lda_filings(aff, aff))
                except Exception as e:
                    logger.warning("[committee_witnesses] LDA failed for %r: %s", aff, e)
                    lda_cache[aff_key] = False
            lda_match = lda_cache.get(aff_key, False)
        donation_amount = _fec_donation_for_affiliation(db, case_file_id, aff) if aff else 0.0
        date_issued = str(rec.get("date_issued") or "")[:10]
        hearing_title = str(rec.get("hearing_title") or "")
        source_url = str(rec.get("source_url") or "")
        out.append(
            {
                "hearing_date": date_issued,
                "hearing_title": hearing_title,
                "witness_name": witness_name,
                "witness_affiliation": aff,
                "fec_donor_match": fec_donor_match,
                "lda_match": lda_match,
                "donation_amount": float(donation_amount),
                "committee": comm_name,
                "source_url": source_url,
                "disclaimer": WITNESS_DISCLAIMER,
            }
        )

    try:
        store_cached_raw_json(db, CACHE_ADAPTER, bg, {"witnesses": out}, CACHE_TTL_HOURS)
    except Exception as e:
        logger.warning("committee_witnesses cache store failed: %s", e)
    return out
