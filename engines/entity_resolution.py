"""
Entity resolution for donor fingerprint normalization.

Layer 1: Deterministic canonicalization
Layer 2: Alias table lookup (human-reviewed)
Layer 3: Fuzzy suggestion queue (suggest only, never auto-merge)

Identity must be auditable. Fuzzy matches never self-authorize.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Legal noise tokens stripped before comparison
NOISE_TOKENS = frozenset({
    "PAC", "POLITICAL", "ACTION", "COMMITTEE", "CMTE",
    "INC", "LLC", "CORP", "CORPORATION", "LTD", "LP",
    "CO", "COMPANY", "ASSOCIATION", "ASSN", "GROUP",
    "HOLDINGS", "PARTNERS", "FUND", "TRUST",
})

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ALIASES_PATH = _ROOT / "data" / "entity_aliases.json"


def _aliases_path(override: Path | None) -> Path:
    return override if override is not None else _DEFAULT_ALIASES_PATH


def canonicalize(name: str) -> str:
    """
    Deterministic normalization. Idempotent.
    1. Uppercase
    2. Strip punctuation except hyphens between words
    3. Collapse whitespace
    4. Remove trailing/leading noise tokens
    5. Collapse remaining noise tokens only when they appear as standalone words
    """
    if not name or not str(name).strip():
        return ""
    s = str(name).strip().upper()
    s = s.replace("&", " AND ")
    # Hyphens act as word boundaries; other listed punctuation becomes space
    for ch in '.,()"\'':
        s = s.replace(ch, " ")
    # Keep hyphen as separator: split words joined by hyphen
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    parts = [p for p in s.split(" ") if p]
    parts = [p for p in parts if p not in NOISE_TOKENS]
    return " ".join(parts)


def slug(name: str) -> str:
    """URL-safe slug from normalized / canonical text."""
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


@dataclass(frozen=True)
class ResolvedEntity:
    raw_name: str
    canonical_name: str
    canonical_id: str  # slug, e.g. "morgan-stanley"
    resolution_method: str  # "exact" | "alias_table" | "unresolved"
    normalized_name: str  # after Layer 1


def _load_aliases_doc(path: Path) -> dict:
    if not path.is_file():
        return {"aliases": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"aliases": []}
    return raw if isinstance(raw, dict) else {"aliases": []}


@lru_cache(maxsize=2)
def _cached_aliases_tuple(path_str: str, mtime: float) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    path = Path(path_str)
    doc = _load_aliases_doc(path)
    rows = doc.get("aliases") if isinstance(doc.get("aliases"), list) else []
    out: list[tuple[str, str, str, tuple[str, ...]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("canonical_id") or "").strip()
        cname = str(row.get("canonical_name") or "").strip()
        aliases = row.get("aliases")
        alist: tuple[str, ...] = tuple(
            str(a).strip() for a in aliases if isinstance(aliases, list) and str(a).strip()
        )
        if cid and cname:
            out.append((cid, cname, canonicalize(cname), tuple(canonicalize(a) for a in alist)))
    return tuple(out)


def _aliases_rows(path: Path) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    return _cached_aliases_tuple(str(path.resolve()), mtime)


def resolve(name: str, db: object | None = None, *, aliases_path: Path | None = None) -> ResolvedEntity:
    """
    Resolve a raw donor name. ``db`` is reserved for future use.

    Order:
    1. Canonicalize; match alias table canonical_name
    2. Canonicalize; match alias strings
    3. Unresolved: canonical_id = slug(canonical_name)
    """
    raw = str(name or "").strip()
    normalized = canonicalize(raw)
    path = _aliases_path(aliases_path)
    if not normalized:
        sid = slug(raw) if raw else "unknown"
        return ResolvedEntity(
            raw_name=raw,
            canonical_name="UNKNOWN",
            canonical_id=sid or "unknown",
            resolution_method="unresolved",
            normalized_name="",
        )

    for cid, cname, cnorm, alias_norms in _aliases_rows(path):
        if normalized == cnorm:
            return ResolvedEntity(
                raw_name=raw,
                canonical_name=cname,
                canonical_id=cid,
                resolution_method="exact",
                normalized_name=normalized,
            )
    for cid, cname, _cnorm, alias_norms in _aliases_rows(path):
        if normalized in alias_norms:
            return ResolvedEntity(
                raw_name=raw,
                canonical_name=cname,
                canonical_id=cid,
                resolution_method="alias_table",
                normalized_name=normalized,
            )

    sid = slug(normalized)
    return ResolvedEntity(
        raw_name=raw,
        canonical_name=normalized,
        canonical_id=sid,
        resolution_method="unresolved",
        normalized_name=normalized,
    )


def _non_noise_tokens(name: str) -> set[str]:
    norm = canonicalize(name)
    return {t for t in norm.split(" ") if t}


def suggest_aliases(name_a: str, name_b: str) -> float:
    """
    Jaccard similarity on non-noise tokens. Returns 0.0 when no shared
    non-noise token has length >= 4 (no eligible suggestion).
    """
    ta = _non_noise_tokens(name_a)
    tb = _non_noise_tokens(name_b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not any(len(t) >= 4 for t in inter):
        return 0.0
    union = ta | tb
    if not union:
        return 0.0
    return len(inter) / len(union)


def suggest_aliases_detail(
    name_a: str, name_b: str
) -> dict[str, str | float | list[str]]:
    """Rich suggestion payload for API (human review only)."""
    ca = canonicalize(name_a)
    cb = canonicalize(name_b)
    ta = _non_noise_tokens(name_a)
    tb = _non_noise_tokens(name_b)
    inter = sorted(ta & tb)
    score = suggest_aliases(name_a, name_b)
    if score > 0.85:
        sug = "likely_same_entity"
    elif score > 0.4:
        sug = "possible_same_entity"
    else:
        sug = "weak_or_unrelated"
    return {
        "name_a": name_a,
        "name_b": name_b,
        "canonical_a": ca,
        "canonical_b": cb,
        "jaccard_score": round(score, 4),
        "shared_tokens": inter,
        "suggestion": sug,
        "action_required": "human_review_before_alias_merge",
    }


def append_alias_entry(entry: dict, *, aliases_path: Path | None = None) -> None:
    """Append one alias object to the JSON file (atomic write)."""
    path = _aliases_path(aliases_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = _load_aliases_doc(path)
    rows = doc.get("aliases")
    if not isinstance(rows, list):
        rows = []
    rows.append(entry)
    doc["aliases"] = rows
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        _cached_aliases_tuple.cache_clear()
    except Exception:
        pass
