"""
Adapt EthicalAlt deep-research JSON into the flat shape expected by
``scripts.ethicalalt_to_open_case.build_ethicalalt_entity`` (``incidents`` list).

Supports:
  - Monolithic ``*_deep.json`` with ``per_category`` (list of {category, incidents}).
  - Legacy ``categories`` dict mapping category name -> {incidents: [...]}.

Does not modify core mapper logic — structural normalization only.
"""
from __future__ import annotations

from typing import Any


def flatten_ethicalalt_deep_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Return ``{profile_id, name, incidents}`` for the Open Case mapper.
    Each incident gains ``ethicalalt_category`` and a stable ``id`` when missing.
    """
    profile_id = str(
        raw.get("brand_slug")
        or raw.get("slug")
        or raw.get("profile_id")
        or raw.get("id")
        or "unknown"
    )
    name = str(
        raw.get("companyName")
        or raw.get("name")
        or raw.get("entity_name")
        or profile_id
    )

    flat: list[dict[str, Any]] = []

    # Full exports usually include a merged ``incidents`` list (all categories).
    # ``per_category`` may be a partial sample — prefer root ``incidents`` when present.
    root_incidents = raw.get("incidents")
    if isinstance(root_incidents, list) and len(root_incidents) > 0:
        for idx, inc in enumerate(root_incidents):
            if not isinstance(inc, dict):
                continue
            merged = {**inc}
            cat = str(merged.get("category") or "uncategorized")
            merged.setdefault("id", f"{cat}-{idx}")
            merged["ethicalalt_category"] = cat
            flat.append(merged)
        return {
            "profile_id": profile_id,
            "name": name,
            "incidents": flat,
        }

    if "per_category" in raw and isinstance(raw["per_category"], list):
        for bucket in raw["per_category"]:
            cat = str(bucket.get("category") or "uncategorized")
            for idx, inc in enumerate(bucket.get("incidents") or []):
                if not isinstance(inc, dict):
                    continue
                merged = {**inc}
                merged.setdefault("id", f"{cat}-{idx}")
                merged["ethicalalt_category"] = cat
                flat.append(merged)
    elif "categories" in raw and isinstance(raw["categories"], dict):
        for cat, bucket in raw["categories"].items():
            if not isinstance(bucket, dict):
                continue
            for idx, inc in enumerate(bucket.get("incidents") or []):
                if not isinstance(inc, dict):
                    continue
                merged = {**inc}
                merged.setdefault("id", f"{cat}-{idx}")
                merged["ethicalalt_category"] = str(cat)
                flat.append(merged)
    else:
        # Already flat or unknown shape — pass through incidents only
        for idx, inc in enumerate(raw.get("incidents") or []):
            if isinstance(inc, dict):
                merged = {**inc}
                merged.setdefault("id", f"inc-{idx}")
                flat.append(merged)

    return {
        "profile_id": profile_id,
        "name": name,
        "incidents": flat,
    }
