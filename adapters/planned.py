"""Stub adapters for sources not yet implemented — log and no-op."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PLANNED_STUB_SOURCE_NAMES: frozenset[str] = frozenset(
    {
        "courtlistener",
        "fjc_biographical",
        "ejudiciary_disclosure",
        "pacer",
        "local_campaign_finance",
        "city_contracts",
        "local_news",
        "bar_complaints",
        "followthemoney",
        "courtlistener_opinions",
        "fjc_reversal_stats",
        "ejudiciary_ptrs",
        "opensecrets",
        "news_archive",
        "news_search",
        "state_ethics",
        "state_contracts",
        "state_votes",
        "court_records",
        "state_disclosure",
        "campaign_finance_filings",
        "business_registry",
        "district_contracts",
        "election_records",
        "property_records",
        "civil_settlements",
        "doj_pattern_practice",
        "education_records",
        "bar_admission_records",
        "pre_career_employment",
        "personal_criminal_civil_record",
        "local_news_archive_deep",
        "prior_employment",
        "prior_candidacies",
        "prior_practice",
        "prior_law_enforcement",
    }
)


def log_planned_adapter(adapter_id: str, *, case_id: Any | None = None, detail: str = "") -> None:
    msg = f"adapter planned, not yet implemented ({adapter_id})"
    if case_id is not None:
        msg = f"{msg} case_id={case_id}"
    if detail:
        msg = f"{msg} {detail}"
    logger.info(msg)
