"""Registry-driven research adapter ordering for a subject profile."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.subject_taxonomy import HISTORICAL_DEPTHS
from models import SubjectProfile

logger = logging.getLogger(__name__)

# Implemented: FJC bulk judges CSV (adapters.fjc_biographical) — https://www.fjc.gov/history/judges
# Planned adapter: free_law_project_judge_db (Free Law Project / CourtListener judge DB).
# REST API: https://www.courtlistener.com/api/rest/v4/people/ — judge biographical data
# (same CourtListener stack; people endpoint is the judge-specific corpus). Large-scale
# coverage (16k+ judges, investments, outside-income sources); used in major outlet
# conflict-of-interest reporting. Basic API access is no-cost with registration.

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "subject_type_sources.json"
_LOADED: dict[str, Any] | None = None


def load_subject_type_sources() -> dict[str, Any]:
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    raw = _REGISTRY_PATH.read_text(encoding="utf-8")
    _LOADED = json.loads(raw)
    return _LOADED


def _normalize_entry(row: Any) -> dict[str, str]:
    if isinstance(row, str):
        return {"id": row.strip(), "status": "planned"}
    if isinstance(row, dict):
        i = str(row.get("id") or row.get("name") or "").strip()
        st = str(row.get("status") or "planned").strip()
        return {"id": i, "status": st}
    return {"id": "", "status": "planned"}


def _bucket_lists(cfg: dict[str, Any], key: str) -> list[dict[str, str]]:
    raw = cfg.get(key) or []
    if not isinstance(raw, list):
        return []
    return [_normalize_entry(x) for x in raw if _normalize_entry(x)["id"]]


class ResearchProfile:
    def __init__(self, subject_profile: SubjectProfile):
        self.subject = subject_profile
        self.source_registry = load_subject_type_sources()

    def _config_for_type(self) -> dict[str, Any]:
        st = (self.subject.subject_type or "").strip()
        reg = self.source_registry
        if st in reg:
            return reg[st]
        logger.warning(
            "subject_type %r not in source registry; using public_official entry",
            st,
        )
        return reg.get("public_official", {})

    def get_adapters(self) -> list[str]:
        """Ordered adapter ids for this subject type (registry-driven)."""
        cfg = self._config_for_type()
        out: list[str] = []
        seen: set[str] = set()
        for key in ("primary", "secondary", "judicial", "local", "historical"):
            for row in _bucket_lists(cfg, key):
                i = row["id"]
                if not i or i in seen:
                    continue
                seen.add(i)
                out.append(i)
        return out

    def get_historical_depth(self) -> str:
        d = (self.subject.historical_depth or "career").strip()
        if d not in HISTORICAL_DEPTHS:
            return "career"
        return d

    def full_depth_extra_source_ids(self) -> list[str]:
        """Additional sources attempted when historical_depth is full (best-effort)."""
        if self.get_historical_depth() != "full":
            return []
        return [
            "education_records",
            "bar_admission_records",
            "pre_career_employment",
            "personal_criminal_civil_record",
            "local_news_archive_deep",
        ]
