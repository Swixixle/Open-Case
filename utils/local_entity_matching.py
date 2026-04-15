"""
Curated entity relationships for procurement ↔ donor (local) and federal legislative
related-entity matching (non-fuzzy). Only explicit JSON rows qualify; no string similarity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engines.entity_resolution import canonicalize, resolve

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_ALIASES_PATH = _ROOT / "data" / "reference" / "local_entity_aliases.json"
DEFAULT_FEDERAL_ALIASES_PATH = _ROOT / "data" / "reference" / "federal_entity_aliases.json"

MATCH_DIRECT = "direct"
MATCH_ALIAS = "alias"
MATCH_RELATED_ENTITY = "related_entity"
MATCH_NONE = "none"

_REL_TYPES_ALIAS = frozenset({"alias"})
_REL_TYPES_RELATED = frozenset(
    {
        "affiliate",
        "pac_of_vendor",
        "pac_of_donor",
        "subsidiary",
        "parent",
        "trade_name",
    }
)


def _resolved_local_aliases_path(aliases_path: Path | None = None) -> Path:
    if aliases_path is not None:
        return aliases_path
    env = os.environ.get("OPEN_CASE_LOCAL_ENTITY_ALIASES")
    if env:
        return Path(env)
    return DEFAULT_LOCAL_ALIASES_PATH


def _resolved_federal_aliases_path(aliases_path: Path | None = None) -> Path:
    if aliases_path is not None:
        return aliases_path
    env = os.environ.get("OPEN_CASE_FEDERAL_ENTITY_ALIASES")
    if env:
        return Path(env)
    return DEFAULT_FEDERAL_ALIASES_PATH


def aliases_path_for_jurisdiction(
    jurisdiction: str,
    *,
    aliases_path: Path | None = None,
) -> Path:
    """Which JSON file backs curated rows for this jurisdiction slug."""
    if aliases_path is not None:
        return aliases_path
    j = (jurisdiction or "").strip().lower()
    if j == "federal":
        return _resolved_federal_aliases_path()
    return _resolved_local_aliases_path()


def _load_curated_aliases(
    jurisdiction: str,
    *,
    aliases_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = aliases_path_for_jurisdiction(jurisdiction, aliases_path=aliases_path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("aliases")
    if not isinstance(rows, list):
        return []
    j = (jurisdiction or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("active") is False:
            continue
        jr = str(row.get("jurisdiction") or "").strip().lower()
        if jr != j:
            continue
        out.append(row)
    return out


def _load_local_aliases(
    jurisdiction: str,
    *,
    aliases_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Backward-compatible name: loads rows for a jurisdiction slug from the appropriate file."""
    return _load_curated_aliases(jurisdiction, aliases_path=aliases_path)


def local_jurisdiction_alias_key(case_jurisdiction: str) -> str:
    """Map CaseFile.jurisdiction to alias file jurisdiction slug."""
    j = (case_jurisdiction or "").strip().lower()
    if "indianapolis" in j:
        return "indianapolis"
    if "testville" in j:
        return "testville"
    return j[:120] or "unknown"


def _lookup_curated_relationship(
    left_label: str,
    right_label: str,
    jurisdiction: str,
    *,
    aliases_path: Path | None = None,
) -> dict[str, Any] | None:
    vl = canonicalize(left_label)
    dr = canonicalize(right_label)
    if not vl or not dr:
        return None
    for row in _load_curated_aliases(jurisdiction, aliases_path=aliases_path):
        ck = canonicalize(str(row.get("canonical_key") or ""))
        al = canonicalize(str(row.get("alias") or ""))
        if not ck or not al:
            continue
        if (vl == ck and dr == al) or (vl == al and dr == ck):
            return row
    return None


def _lookup_local_relationship(
    left_label: str,
    right_label: str,
    jurisdiction: str,
    *,
    aliases_path: Path | None = None,
) -> dict[str, Any] | None:
    return _lookup_curated_relationship(
        left_label, right_label, jurisdiction, aliases_path=aliases_path
    )


def _local_match_type(
    vendor_label: str,
    donor_label: str,
    jurisdiction: str,
    db: Session,
    *,
    aliases_path: Path | None = None,
) -> tuple[str, str | None, str | None]:
    """
    Returns (match_type, relationship_type, relationship_source_note).
    match_type: direct | alias | related_entity | none

    ``jurisdiction`` is a slug: ``federal``, ``indianapolis``, ``testville``, etc.
    Loads ``federal_entity_aliases.json`` when jurisdiction is ``federal``,
    otherwise ``local_entity_aliases.json`` (filtered by row jurisdiction).
    """
    a = (vendor_label or "").strip()
    b = (donor_label or "").strip()
    if not a or not b:
        return MATCH_NONE, None, None
    ca, cb = canonicalize(a), canonicalize(b)
    if ca and cb and ca == cb:
        return MATCH_DIRECT, None, None
    ra, rb = resolve(a, db), resolve(b, db)
    if bool(ra.canonical_id) and ra.canonical_id == rb.canonical_id:
        return MATCH_DIRECT, None, None

    row = _lookup_curated_relationship(a, b, jurisdiction, aliases_path=aliases_path)
    if row is None:
        return MATCH_NONE, None, None
    rt = str(row.get("relationship_type") or "").strip().lower()
    note = str(row.get("source_note") or row.get("public_explanation") or "").strip() or None
    if rt in _REL_TYPES_ALIAS:
        return MATCH_ALIAS, rt, note
    if rt in _REL_TYPES_RELATED:
        return MATCH_RELATED_ENTITY, rt, note
    return MATCH_NONE, None, None


def local_match_eligible_for_loop_and_timing(
    vendor_label: str,
    donor_label: str,
    jurisdiction: str,
    db: Session,
    *,
    aliases_path: Path | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """
    Loop and timing rules accept direct and curated alias only (not affiliate/PAC/etc.).
    Returns (eligible, match_type, relationship_type, relationship_source_note).
    """
    mt, rt, note = _local_match_type(
        vendor_label, donor_label, jurisdiction, db, aliases_path=aliases_path
    )
    if mt in (MATCH_DIRECT, MATCH_ALIAS):
        return True, mt, rt, note
    return False, mt, rt, note
